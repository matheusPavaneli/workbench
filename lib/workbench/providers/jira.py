"""Jira Cloud provider (REST API v3).

Quirks this file exists to absorb:

- Descriptions and comments are ADF documents, not strings.
- ``/rest/api/3/search`` is retired; the current endpoint is
  ``/rest/api/3/search/jql`` and it is a POST with a token cursor.
- Link direction is implicit: an ``issuelinks`` entry carries either an
  ``inwardIssue`` or an ``outwardIssue``, and which one decides whether this
  issue blocks or is blocked.
- ``parent`` and ``subtasks`` are separate fields, not links, so hierarchy has
  to be folded into the link list by hand.
"""

from __future__ import annotations

from .. import schema
from ..errors import ConfigError
from ..schema import Comment, Link, Task
from .. import fields as fields_lib
from ..text import adf_to_text, normalise
from .base import Identity, Provider

ISSUE_FIELDS = [
    "summary",
    "status",
    "issuetype",
    "assignee",
    "updated",
    "description",
    "issuelinks",
    "parent",
    "subtasks",
]

LIST_FIELDS = ["summary", "status", "updated"]

# (link type name, direction) -> canonical type. Direction is "outward" when the
# payload carries outwardIssue, "inward" when it carries inwardIssue.
_LINK_MAP = {
    ("blocks", "outward"): schema.BLOCKS,
    ("blocks", "inward"): schema.BLOCKED_BY,
    ("duplicate", "outward"): schema.DUPLICATES,
    ("duplicate", "inward"): schema.DUPLICATED_BY,
    ("cloners", "outward"): schema.RELATES,
    ("cloners", "inward"): schema.RELATES,
    ("relates", "outward"): schema.RELATES,
    ("relates", "inward"): schema.RELATES,
}


class JiraProvider(Provider):
    name = "jira"

    def _build_auth(self, token: str) -> str:
        from .. import http

        email = self.context.auth.get("email")
        if not email:
            raise ConfigError(
                f"context {self.context.name!r} needs auth.email for Jira",
                fix=[
                    'add "email" to the auth block -- Jira Cloud authenticates as email:api_token',
                    "the API token itself stays in the environment variable named by pat_env",
                ],
            )
        return http.basic_auth(email, token)

    def probe(self) -> Identity:
        data = self.require_dict(self.get("/rest/api/3/myself"), "/rest/api/3/myself")
        return Identity(
            account=str(data.get("displayName") or data.get("emailAddress") or "unknown"),
            detail=f"{self.context.base_url} project {self.context.project}",
        )

    # ---- reading --------------------------------------------------------

    def list_tasks(self, limit: int) -> list[dict]:
        clauses = ["assignee = currentUser()", "statusCategory != Done"]
        if self.context.project:
            clauses.insert(0, f'project = "{_escape(self.context.project)}"')
        jql = " AND ".join(clauses) + " ORDER BY updated DESC"

        data = self.require_dict(
            self.post("/rest/api/3/search/jql", {"jql": jql, "fields": LIST_FIELDS, "maxResults": limit}),
            "search",
        )
        rows = []
        for issue in data.get("issues", []):
            fields = issue.get("fields") or {}
            rows.append(
                {
                    "key": issue.get("key", ""),
                    "status": _name(fields.get("status")),
                    "title": schema.make_title(fields.get("summary")),
                    "updated": str(fields.get("updated", ""))[:10],
                }
            )
        return rows

    def fetch_task(self, key: str) -> Task:
        # A mapped custom field has to be asked for by name: Jira returns only
        # the fields in the query, so a field_map nobody adds here reads as an
        # empty field rather than as a mapping that did not work.
        wanted = ISSUE_FIELDS + [name for name in self.field_map if name not in ISSUE_FIELDS]
        data = self.require_dict(
            self.get(f"/rest/api/3/issue/{key}", fields=",".join(wanted)), f"issue {key}"
        )
        fields = data.get("fields") or {}
        unmapped: list[str] = []

        return Task(
            key=str(data.get("key", key)),
            title=schema.make_title(fields.get("summary")),
            status=_name(fields.get("status")),
            type=_name(fields.get("issuetype")),
            provider=self.name,
            url=f"{self.context.base_url}/browse/{data.get('key', key)}",
            assignee=_display_name(fields.get("assignee")),
            updated=str(fields.get("updated", "")),
            desc=normalise(adf_to_text(fields.get("description"))),
            linked=self._links(fields, unmapped),
            unmapped=unmapped,
            extra=fields_lib.mapped(fields, self.field_map),
        )

    def scan_fields(self, key: str) -> dict[str, str]:
        """Every field this tenant carries that nothing here reads.

        Asks for everything, once, on request only -- `fields=*all` is a much
        larger response than the normal path wants, which is exactly why the
        normal path does not use it.
        """
        data = self.require_dict(self.get(f"/rest/api/3/issue/{key}", fields="*all"), f"issue {key}")
        return fields_lib.unread(data.get("fields") or {}, set(ISSUE_FIELDS), self.field_map)

    def _links(self, fields: dict, unmapped: list[str]) -> list[Link]:
        links: list[Link] = []

        parent = fields.get("parent")
        if isinstance(parent, dict):
            links.append(self._link_from_issue(parent, schema.PARENT))

        for subtask in fields.get("subtasks") or []:
            if isinstance(subtask, dict):
                links.append(self._link_from_issue(subtask, schema.CHILD))

        for item in fields.get("issuelinks") or []:
            if not isinstance(item, dict):
                continue
            type_name = str((item.get("type") or {}).get("name", "")).lower()
            if "outwardIssue" in item:
                direction, issue = "outward", item["outwardIssue"]
            elif "inwardIssue" in item:
                direction, issue = "inward", item["inwardIssue"]
            else:
                continue

            canonical = _LINK_MAP.get((type_name, direction))
            if canonical is None:
                canonical = schema.OTHER
                unmapped.append(f"issuelink type: {type_name or 'unnamed'}")
            links.append(self._link_from_issue(issue, canonical))

        return links

    def _link_from_issue(self, issue: dict, link_type: str) -> Link:
        fields = issue.get("fields") or {}
        key = str(issue.get("key", ""))
        return Link(
            key=key,
            type=link_type,
            status=_name(fields.get("status")),
            title=schema.make_title(fields.get("summary")),
            url=f"{self.context.base_url}/browse/{key}" if key else "",
        )

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        """Newest first. ``limit=None`` pages up to the hard ceiling.

        The provider's own default page size is not "all" -- relying on it
        returns one page while reporting the full total, which reads as
        complete and is not.
        """
        wanted = schema.MAX_COMMENTS_ALL if limit is None else limit
        comments: list[Comment] = []
        total = 0
        start_at = 0

        while len(comments) < wanted:
            page_size = min(schema.COMMENT_PAGE, wanted - len(comments))
            data = self.require_dict(
                self.get(
                    f"/rest/api/3/issue/{key}/comment",
                    orderBy="-created",
                    maxResults=page_size,
                    startAt=start_at,
                ),
                f"comments for {key}",
            )
            batch = [item for item in data.get("comments", []) if isinstance(item, dict)]
            total = int(data.get("total", len(batch)))
            comments.extend(
                schema.make_comment(
                    author=_display_name(item.get("author")) or "unknown",
                    when=str(item.get("created", ""))[:10],
                    raw=normalise(adf_to_text(item.get("body"))),
                )
                for item in batch
            )
            start_at += len(batch)
            if not batch or start_at >= total:
                break

        return total, comments

    def fetch_updated(self, key: str) -> str:
        data = self.require_dict(self.get(f"/rest/api/3/issue/{key}", fields="updated"), f"issue {key}")
        return str((data.get("fields") or {}).get("updated", ""))

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        quoted = ", ".join(f'"{_escape(k)}"' for k in keys)
        data = self.require_dict(
            self.post(
                "/rest/api/3/search/jql",
                {"jql": f"key in ({quoted})", "fields": ["description"], "maxResults": len(keys)},
            ),
            "search",
        )
        result: dict[str, str] = {}
        for issue in data.get("issues", []):
            fields = issue.get("fields") or {}
            result[str(issue.get("key", "")).upper()] = normalise(adf_to_text(fields.get("description")))
        return result

    def fetch_history(self, key: str, limit: int) -> list[str]:
        data = self.require_dict(
            self.get(f"/rest/api/3/issue/{key}", expand="changelog", fields="summary"), f"issue {key}"
        )
        histories = ((data.get("changelog") or {}).get("histories") or [])[-limit:]
        lines = []
        for entry in reversed(histories):
            who = _display_name(entry.get("author")) or "unknown"
            when = str(entry.get("created", ""))[:10]
            for item in entry.get("items") or []:
                field = item.get("field", "?")
                lines.append(f"{when} {who}: {field} {item.get('fromString') or '-'} -> {item.get('toString') or '-'}")
        return lines[:limit]


def _name(value: object) -> str:
    return str(value.get("name", "")) if isinstance(value, dict) else ""


def _display_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return str(value.get("displayName") or value.get("emailAddress") or "") or None


def _escape(value: str) -> str:
    """Neutralise quotes and backslashes before they reach a JQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
