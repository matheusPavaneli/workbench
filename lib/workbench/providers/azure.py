"""Azure DevOps provider (REST API 7.1).

Quirks this file exists to absorb:

- Fields are namespaced strings (``System.Title``, ``System.State``), not a
  nested object like Jira's.
- Links are ``relations`` whose target is a URL, not a key, so the id has to be
  parsed out of it. Relations also carry attachments and hyperlinks, which are
  not links between work items at all.
- Link direction is encoded in the rel suffix: ``-Forward`` names the *target*
  ("Child", "Successor", "Duplicate"), ``-Reverse`` names it from the other end.
- WIQL returns ids only, so listing work items always costs a second call.
- Descriptions are HTML, and bugs put their body in ``ReproSteps`` instead.
"""

from __future__ import annotations

import re
import urllib.parse

from .. import http, schema
from ..errors import ConfigError
from ..schema import Comment, Link, Task
from ..text import html_to_text, normalise
from .base import Identity, Provider

API_VERSION = "7.1"
COMMENTS_API_VERSION = "7.1-preview.4"

FIELD_ID = "System.Id"
FIELD_TITLE = "System.Title"
FIELD_STATE = "System.State"
FIELD_TYPE = "System.WorkItemType"
FIELD_ASSIGNED = "System.AssignedTo"
FIELD_CHANGED = "System.ChangedDate"
FIELD_DESCRIPTION = "System.Description"
FIELD_REPRO = "Microsoft.VSTS.TCM.ReproSteps"

LIST_FIELDS = [FIELD_TITLE, FIELD_STATE, FIELD_CHANGED]
LINK_FIELDS = [FIELD_TITLE, FIELD_STATE]

# Comment bodies are markdown unless `format` says html; `renderedText` is the
# HTML rendering. Stripping tags off markdown mangles anything with angle
# brackets in it, so the format field decides.
def _comment_text(item: dict) -> str:
    if str(item.get("format", "")).lower() == "html":
        return normalise(html_to_text(item.get("renderedText") or item.get("text")))
    return normalise(str(item.get("text") or ""))


# A "-Forward" relation names the target from this item's point of view:
# Child, Successor, Duplicate. "-Reverse" names it from the other end.
_LINK_MAP = {
    "system.linktypes.hierarchy-forward": schema.CHILD,
    "system.linktypes.hierarchy-reverse": schema.PARENT,
    "system.linktypes.dependency-forward": schema.BLOCKS,
    "system.linktypes.dependency-reverse": schema.BLOCKED_BY,
    "system.linktypes.duplicate-forward": schema.DUPLICATED_BY,
    "system.linktypes.duplicate-reverse": schema.DUPLICATES,
    "system.linktypes.related": schema.RELATES,
    # Cross-organization links. Same semantics, different reference names.
    "system.linktypes.remote.dependency-forward": schema.BLOCKS,
    "system.linktypes.remote.dependency-reverse": schema.BLOCKED_BY,
    "system.linktypes.remote.related": schema.RELATES,
    # Process-defined pairs that are common enough to name rather than leave
    # in _unmapped, but which are not dependencies.
    "microsoft.vsts.common.testedby-forward": schema.RELATES,
    "microsoft.vsts.common.testedby-reverse": schema.RELATES,
    "microsoft.vsts.common.affects-forward": schema.RELATES,
    "microsoft.vsts.common.affects-reverse": schema.RELATES,
}

# Relations that are not links between work items. Skipping them is correct,
# not a mapping failure, so they never reach _unmapped.
_NON_WORK_ITEM_RELS = ("attachedfile", "hyperlink", "artifactlink", "wiki")

_CLOSED_STATES = ("Closed", "Removed", "Done", "Resolved")

_ID_IN_URL = re.compile(r"/workItems/(\d+)\s*$", re.IGNORECASE)


class AzureProvider(Provider):
    name = "azure"

    def _build_auth(self, token: str) -> str:
        # A PAT authenticates as an empty username with the token as password.
        return http.basic_auth("", token)

    @property
    def _project_path(self) -> str:
        return urllib.parse.quote(self.context.project, safe="")

    def probe(self) -> Identity:
        data = self.get(f"/_apis/projects/{self._project_path}", **{"api-version": API_VERSION})
        if not isinstance(data, dict) or "name" not in data:
            raise ConfigError(
                f"project {self.context.project!r} not found",
                fix=[
                    "check base_url is https://dev.azure.com/<org> and project is the project name",
                    "check the PAT has Work Items (read) scope for that organization",
                ],
            )
        return Identity(account=str(data["name"]), detail=f"{self.context.base_url} project {data['name']}")

    # ---- reading --------------------------------------------------------

    def list_tasks(self, limit: int) -> list[dict]:
        states = ", ".join(f"'{state}'" for state in _CLOSED_STATES)
        query = (
            f"SELECT [{FIELD_ID}] FROM WorkItems "
            f"WHERE [{FIELD_ASSIGNED}] = @Me "
            f"AND [System.TeamProject] = @project "
            f"AND [{FIELD_STATE}] NOT IN ({states}) "
            f"ORDER BY [{FIELD_CHANGED}] DESC"
        )
        result = self.require_dict(
            self.post(
                f"/{self._project_path}/_apis/wit/wiql",
                {"query": query},
                **{"api-version": API_VERSION, "$top": limit},
            ),
            "wiql",
        )
        ids = [str(item.get("id")) for item in result.get("workItems", []) if item.get("id") is not None]
        if not ids:
            return []

        items = self._batch(ids[:limit], LIST_FIELDS)
        rows = []
        for item in items:
            fields = item.get("fields") or {}
            rows.append(
                {
                    "key": str(item.get("id", "")),
                    "status": str(fields.get(FIELD_STATE, "")),
                    "title": schema.make_title(fields.get(FIELD_TITLE)),
                    "updated": str(fields.get(FIELD_CHANGED, ""))[:10],
                }
            )
        return rows

    def fetch_task(self, key: str) -> Task:
        data = self.require_dict(
            self.get(f"/_apis/wit/workitems/{key}", **{"$expand": "relations", "api-version": API_VERSION}),
            f"work item {key}",
        )
        fields = data.get("fields") or {}
        unmapped: list[str] = []
        body = fields.get(FIELD_DESCRIPTION) or fields.get(FIELD_REPRO) or ""

        return Task(
            key=str(data.get("id", key)),
            title=schema.make_title(fields.get(FIELD_TITLE)),
            status=str(fields.get(FIELD_STATE, "")),
            type=str(fields.get(FIELD_TYPE, "")),
            provider=self.name,
            url=f"{self.context.base_url}/{self._project_path}/_workitems/edit/{data.get('id', key)}",
            assignee=_display_name(fields.get(FIELD_ASSIGNED)),
            updated=str(fields.get(FIELD_CHANGED, "")),
            desc=normalise(html_to_text(body)),
            linked=self._links(data.get("relations") or [], unmapped),
            unmapped=unmapped,
        )

    def _links(self, relations: list, unmapped: list[str]) -> list[Link]:
        """Relations give ids only; titles and states need one extra call."""
        pending: list[tuple[str, str]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            rel = str(relation.get("rel", "")).lower()
            if any(rel.startswith(prefix) for prefix in _NON_WORK_ITEM_RELS):
                continue

            match = _ID_IN_URL.search(str(relation.get("url", "")))
            if not match:
                continue

            canonical = _LINK_MAP.get(rel)
            if canonical is None:
                canonical = schema.OTHER
                unmapped.append(f"relation type: {rel or 'unnamed'}")
            pending.append((match.group(1), canonical))

        if not pending:
            return []

        details = {
            str(item.get("id")): (item.get("fields") or {})
            for item in self._batch([item_id for item_id, _ in pending[: schema.LINKED_MAX]], LINK_FIELDS)
        }

        links = []
        for item_id, canonical in pending:
            fields = details.get(item_id, {})
            links.append(
                Link(
                    key=item_id,
                    type=canonical,
                    status=str(fields.get(FIELD_STATE, "")),
                    title=schema.make_title(fields.get(FIELD_TITLE)),
                    url=f"{self.context.base_url}/{self._project_path}/_workitems/edit/{item_id}",
                )
            )
        return links

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        """Newest first. ``limit=None`` follows continuation tokens to the ceiling."""
        wanted = schema.MAX_COMMENTS_ALL if limit is None else limit
        comments: list[Comment] = []
        total = 0
        continuation: str | None = None

        while len(comments) < wanted:
            query = {
                "api-version": COMMENTS_API_VERSION,
                "order": "desc",
                "$top": min(schema.COMMENT_PAGE, wanted - len(comments)),
            }
            if continuation:
                query["continuationToken"] = continuation

            data = self.require_dict(
                self.get(f"/{self._project_path}/_apis/wit/workItems/{key}/comments", **query),
                f"comments for {key}",
            )
            batch = [item for item in data.get("comments", []) if isinstance(item, dict)]
            total = int(data.get("totalCount", len(batch)))
            comments.extend(
                schema.make_comment(
                    author=_display_name(item.get("createdBy")) or "unknown",
                    when=str(item.get("createdDate", ""))[:10],
                    raw=_comment_text(item),
                )
                for item in batch
            )
            continuation = data.get("continuationToken")
            if not batch or not continuation:
                break

        return total, comments

    def fetch_updated(self, key: str) -> str:
        data = self.require_dict(
            self.get(f"/_apis/wit/workitems/{key}", fields=FIELD_CHANGED, **{"api-version": API_VERSION}),
            f"work item {key}",
        )
        return str((data.get("fields") or {}).get(FIELD_CHANGED, ""))

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        items = self._batch(keys, [FIELD_DESCRIPTION, FIELD_REPRO])
        result = {}
        for item in items:
            fields = item.get("fields") or {}
            body = fields.get(FIELD_DESCRIPTION) or fields.get(FIELD_REPRO) or ""
            result[str(item.get("id", "")).upper()] = normalise(html_to_text(body))
        return result

    def fetch_history(self, key: str, limit: int) -> list[str]:
        data = self.require_dict(
            self.get(f"/_apis/wit/workItems/{key}/updates", **{"api-version": API_VERSION, "$top": 200}),
            f"updates for {key}",
        )
        lines: list[str] = []
        for update in reversed(data.get("value", [])):
            if not isinstance(update, dict):
                continue
            who = _display_name(update.get("revisedBy")) or "unknown"
            when = str(update.get("revisedDate", ""))[:10]
            for name, change in (update.get("fields") or {}).items():
                if name in {"System.Rev", "System.ChangedDate", "System.AuthorizedDate", "System.Watermark"}:
                    continue
                short = name.split(".")[-1]
                lines.append(f"{when} {who}: {short} {change.get('oldValue') or '-'} -> {change.get('newValue') or '-'}")
                if len(lines) >= limit:
                    return lines
        return lines

    def _batch(self, ids: list[str], fields: list[str]) -> list[dict]:
        """Fetch several work items in one call. ``fields`` and ``$expand`` are
        mutually exclusive in this API, so callers ask for fields explicitly."""
        if not ids:
            return []
        data = self.require_dict(
            self.get(
                "/_apis/wit/workitems",
                ids=",".join(ids),
                fields=",".join(fields),
                **{"api-version": API_VERSION, "errorPolicy": "Omit"},
            ),
            "work item batch",
        )
        return [item for item in data.get("value", []) if isinstance(item, dict)]


def _display_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return str(value.get("displayName") or value.get("uniqueName") or "") or None
