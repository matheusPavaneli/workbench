"""Linear provider.

Linear is the tracker many small product teams use instead of Jira. It has one
endpoint, a GraphQL API at ``api.linear.app/graphql``, and a personal API key
sent as the ``Authorization`` header as-is -- no ``Bearer`` prefix, which is
reserved for OAuth tokens.

What maps cleanly, and what does not:

- **Keys** are ``TEAM-123`` identifiers, which ``issue(id:)`` accepts directly,
  so a key never needs translating to a UUID first.
- **Typed relations** exist: ``blocks``, ``duplicate`` and ``related``, stored
  once on the issue that created them. The issue's ``relations`` are its own
  side and ``inverseRelations`` the other's, so direction is read from which
  list a relation came back in. ``similar`` (an AI suggestion, not a decision)
  and any type added later land in ``other`` and ``_unmapped``.
- **No issue type.** Labels are the only signal, as on GitHub.
- **A field-limited read** does exist: ``issue { updatedAt }`` is one small
  request, so the cache is revalidated rather than skipped.
- **Rate limits** come back as a GraphQL error coded ``RATELIMITED``. That is
  surfaced as one clear error; the shared transport's bounded retry covers
  only 429/5xx, so there is no retry storm against a limit that resets hourly.
"""

from __future__ import annotations

import re

from .. import schema
from ..errors import NotFoundError, ProviderError, UsageError
from ..schema import Comment, Link, Task
from ..text import normalise
from .base import Identity, Provider

ENDPOINT = "/graphql"
PAGE_SIZE = 50
MAX_PAGES = 4
LINKS_MAX = 50

# TEAM-123. Team keys are letters and digits, starting with a letter.
_KEY = re.compile(r"^[A-Z][A-Z0-9]{0,9}-\d+$")

RATE_LIMITED = "RATELIMITED"

_ISSUE_FIELDS = f"""
  identifier title description url updatedAt
  state {{ name type }}
  assignee {{ name displayName }}
  labels(first: 20) {{ nodes {{ name }} }}
  parent {{ identifier title state {{ name }} }}
  children(first: {LINKS_MAX}) {{ nodes {{ identifier title state {{ name }} }} }}
  relations(first: {LINKS_MAX}) {{ nodes {{ type relatedIssue {{ identifier title state {{ name }} }} }} }}
  inverseRelations(first: {LINKS_MAX}) {{ nodes {{ type issue {{ identifier title state {{ name }} }} }} }}
"""

ISSUE = f"query Issue($id: String!) {{ issue(id: $id) {{ {_ISSUE_FIELDS} }} }}"
UPDATED = "query IssueUpdated($id: String!) { issue(id: $id) { updatedAt } }"
COMMENTS = (
    "query IssueComments($id: String!, $first: Int!, $after: String) { issue(id: $id) { "
    "comments(first: $first, after: $after) { nodes { body createdAt user { name displayName } } "
    "pageInfo { hasNextPage endCursor } } } }"
)
HISTORY = (
    "query IssueHistory($id: String!, $first: Int!) { issue(id: $id) { history(first: $first) { nodes { "
    "createdAt actor { name } fromState { name } toState { name } fromTitle toTitle "
    "fromAssignee { name } toAssignee { name } addedLabels { name } removedLabels { name } } } } }"
)
VIEWER = "query Viewer { viewer { name email } organization { urlKey } }"
LIST = (
    "query AssignedIssues($first: Int!, $filter: IssueFilter) { viewer { assignedIssues("
    "first: $first, orderBy: updatedAt, filter: $filter) { nodes { identifier title updatedAt state { name } } } } }"
)

# Forward relations: this issue is the subject. Inverse: the other issue is.
_FORWARD = {"blocks": schema.BLOCKS, "duplicate": schema.DUPLICATES, "related": schema.RELATES}
_INVERSE = {"blocks": schema.BLOCKED_BY, "duplicate": schema.DUPLICATED_BY, "related": schema.RELATES}


class LinearProvider(Provider):
    name = "linear"

    def _build_auth(self, token: str) -> str:
        # A personal API key goes in as-is; "Bearer" is for OAuth tokens only.
        return token

    # ---- transport -------------------------------------------------------

    def _query(self, operation: str, query: str, variables: dict | None = None) -> dict:
        """One GraphQL call. ``data``, or a typed error -- never a partial guess."""
        response = self._post(operation, query, variables)
        if not isinstance(response, dict):
            raise ProviderError(f"Linear returned no usable data for {operation}")
        errors = [item for item in response.get("errors") or [] if isinstance(item, dict)]
        if errors:
            _raise_for(errors, operation)
        data = response.get("data")
        if not isinstance(data, dict):
            raise ProviderError(f"Linear returned no data for {operation}")
        return data

    def _post(self, operation: str, query: str, variables: dict | None) -> object:
        body = {"operationName": operation, "query": query, "variables": variables or {}}
        try:
            return self.post(ENDPOINT, body)
        except ProviderError as exc:
            # Linear answers a rate limit with a 400 carrying the code.
            if RATE_LIMITED in exc.message:
                raise _rate_limited() from exc
            raise

    def _issue(self, operation: str, query: str, key: str, **variables) -> dict:
        data = self._query(operation, query, {"id": _check_key(key), **variables})
        issue = data.get("issue")
        if not isinstance(issue, dict):
            raise NotFoundError(f"Linear issue {key} not found")
        return issue

    def probe(self) -> Identity:
        data = self._query("Viewer", VIEWER)
        viewer = _obj(data.get("viewer"))
        org = _obj(data.get("organization"))
        return Identity(
            account=str(viewer.get("name") or viewer.get("email") or "unknown"),
            detail=f"linear.app/{org.get('urlKey') or '?'}",
        )

    # ---- reading ---------------------------------------------------------

    def list_tasks(self, limit: int) -> list[dict]:
        issue_filter: dict = {"state": {"type": {"nin": ["completed", "canceled"]}}}
        team = (self.context.project or "").strip()
        if team:
            issue_filter["team"] = {"key": {"eq": team.upper()}}
        data = self._query("AssignedIssues", LIST, {"first": min(limit, PAGE_SIZE), "filter": issue_filter})
        viewer = _obj(data.get("viewer"))
        rows = []
        for node in _nodes(viewer.get("assignedIssues")):
            rows.append(
                {
                    "key": str(node.get("identifier", "")),
                    "status": _state(node),
                    "title": schema.make_title(node.get("title")),
                    "updated": str(node.get("updatedAt", ""))[:10],
                }
            )
        return rows[:limit]

    def fetch_task(self, key: str) -> Task:
        issue = self._issue("Issue", ISSUE, key)
        unmapped: list[str] = []
        labels = [str(node.get("name", "")) for node in _nodes(issue.get("labels"))]
        assignee = _obj(issue.get("assignee"))
        return Task(
            key=str(issue.get("identifier") or key),
            title=schema.make_title(issue.get("title")),
            status=_state(issue),
            type=_type_from(labels, unmapped),
            provider=self.name,
            url=str(issue.get("url", "")),
            assignee=str(assignee.get("displayName") or assignee.get("name") or "") or None,
            updated=str(issue.get("updatedAt", "")),
            desc=normalise(str(issue.get("description") or "")),
            linked=_links(issue, unmapped),
            unmapped=unmapped,
        )

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        wanted = schema.MAX_COMMENTS_ALL if limit is None else limit
        collected: list[dict] = []
        after = None
        for _ in range(MAX_PAGES):
            issue = self._issue("IssueComments", COMMENTS, key, first=PAGE_SIZE, after=after)
            connection = _obj(issue.get("comments"))
            collected.extend(_nodes(connection))
            page = _obj(connection.get("pageInfo"))
            if not page.get("hasNextPage") or not page.get("endCursor"):
                break
            after = str(page["endCursor"])

        # The connection's order is not documented as chronological; sort
        # rather than trust it, newest first like every other provider.
        collected.sort(key=lambda item: str(item.get("createdAt", "")), reverse=True)
        return len(collected), [
            schema.make_comment(
                author=_person(item.get("user")) or "unknown",
                when=str(item.get("createdAt", ""))[:10],
                raw=normalise(str(item.get("body") or "")),
            )
            for item in collected[:wanted]
        ]

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        """One request for all of them: an alias per issue, capped."""
        wanted = [key.upper() for key in keys[: schema.LINKED_MAX] if _KEY.match(key.upper())]
        if not wanted:
            return {}
        params = ", ".join(f"$k{index}: String!" for index in range(len(wanted)))
        fields = " ".join(
            f"i{index}: issue(id: $k{index}) {{ identifier description }}" for index in range(len(wanted))
        )
        query = f"query IssueDescriptions({params}) {{ {fields} }}"
        variables = {f"k{index}": key for index, key in enumerate(wanted)}
        response = self._post("IssueDescriptions", query, variables)
        errors = response.get("errors") if isinstance(response, dict) else None
        if any(isinstance(item, dict) and (item.get("extensions") or {}).get("code") == RATE_LIMITED
               for item in errors or []):
            raise _rate_limited()
        # A link to an issue this key cannot see comes back as an error beside
        # the others' data. The rest are still worth having.
        data = response.get("data") if isinstance(response, dict) else None
        result: dict[str, str] = {}
        for node in (data or {}).values():
            if isinstance(node, dict) and node.get("identifier"):
                result[str(node["identifier"]).upper()] = normalise(str(node.get("description") or ""))
        return result

    def fetch_history(self, key: str, limit: int) -> list[str]:
        issue = self._issue("IssueHistory", HISTORY, key, first=min(max(limit, 1) * 3, PAGE_SIZE))
        lines = []
        for entry in _nodes(issue.get("history")):
            detail = _history_detail(entry)
            if not detail:
                continue
            who = _person(entry.get("actor")) or "unknown"
            lines.append(f"{str(entry.get('createdAt', ''))[:10]} {who}: {detail}")
        return lines[:limit]

    def fetch_updated(self, key: str) -> str:
        return str(self._issue("IssueUpdated", UPDATED, key).get("updatedAt", ""))


def _check_key(key: str) -> str:
    cleaned = str(key).strip().upper()
    if not _KEY.match(cleaned):
        raise UsageError(
            f"Linear issues are keyed TEAM-123, got {key!r}",
            fix=["use the identifier Linear shows, e.g. wb task get ENG-42"],
        )
    return cleaned


def _rate_limited() -> ProviderError:
    return ProviderError(
        "Linear rate limit reached (RATELIMITED)",
        fix=["wait for the limit to reset (Linear's limits are per hour), then retry",
             "nothing was retried automatically, so no further requests were spent"],
    )


def _raise_for(errors: list[dict], operation: str) -> None:
    codes = {str((item.get("extensions") or {}).get("code") or "") for item in errors}
    messages = "; ".join(str(item.get("message") or "")[:200] for item in errors[:3])
    if RATE_LIMITED in codes:
        raise _rate_limited()
    if "AUTHENTICATION_ERROR" in codes:
        from ..errors import AuthError

        raise AuthError(f"Linear rejected the API key: {messages}", fix=["check the key in the context: wb ctx test"])
    if any("not found" in str(item.get("message", "")).lower() for item in errors) or "ENTITY_NOT_FOUND" in codes:
        raise NotFoundError(f"Linear {operation}: {messages}")
    raise ProviderError(f"Linear {operation} failed: {messages}")


def _obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _nodes(connection: object) -> list[dict]:
    if not isinstance(connection, dict):
        return []
    return [node for node in connection.get("nodes") or [] if isinstance(node, dict)]


def _state(issue: dict) -> str:
    state = issue.get("state")
    return str(state.get("name") or "") if isinstance(state, dict) else ""


def _person(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return str(value.get("displayName") or value.get("name") or "") or None


def _link(issue: object, kind: str) -> Link | None:
    if not isinstance(issue, dict) or not issue.get("identifier"):
        return None
    return Link(
        key=str(issue["identifier"]),
        type=kind,
        status=_state(issue),
        title=schema.make_title(issue.get("title")),
    )


def _links(issue: dict, unmapped: list[str]) -> list[Link]:
    links: list[Link] = []
    seen: set[tuple[str, str]] = set()

    def add(link: Link | None) -> None:
        if link is not None and (link.key, link.type) not in seen:
            seen.add((link.key, link.type))
            links.append(link)

    add(_link(issue.get("parent"), schema.PARENT))
    for child in _nodes(issue.get("children")):
        add(_link(child, schema.CHILD))

    for side, mapping, field in (
        ("relations", _FORWARD, "relatedIssue"),
        ("inverseRelations", _INVERSE, "issue"),
    ):
        for relation in _nodes(issue.get(side)):
            kind = str(relation.get("type") or "")
            mapped = mapping.get(kind)
            if mapped is None:
                note = f"relation type: {kind or '?'}"
                if note not in unmapped:
                    unmapped.append(note)
                mapped = schema.OTHER
            add(_link(relation.get(field), mapped))
    return links


def _type_from(labels: list[str], unmapped: list[str]) -> str:
    """Linear has no issue type, so the label set is the only signal there is."""
    lowered = {label.lower() for label in labels}
    for label, kind in (
        ("bug", "bug"),
        ("defect", "bug"),
        ("feature", "feature"),
        ("improvement", "feature"),
        ("chore", "chore"),
        ("tech debt", "chore"),
        ("support", "support"),
        ("question", "support"),
    ):
        if label in lowered:
            return kind
    if labels:
        unmapped.append(f"labels: {', '.join(sorted(labels)[:5])}")
    return "issue"


def _history_detail(entry: dict) -> str:
    def name(field: str) -> str:
        value = entry.get(field)
        return str(value.get("name") or "") if isinstance(value, dict) else ""

    if entry.get("toState"):
        return f"state {name('fromState') or '?'} -> {name('toState')}"
    if entry.get("toTitle") is not None and entry.get("fromTitle") is not None:
        return "title"
    if "toAssignee" in entry and (entry.get("toAssignee") or entry.get("fromAssignee")):
        return f"assignee {name('fromAssignee') or 'none'} -> {name('toAssignee') or 'none'}"
    added = [str(item.get("name", "")) for item in entry.get("addedLabels") or [] if isinstance(item, dict)]
    removed = [str(item.get("name", "")) for item in entry.get("removedLabels") or [] if isinstance(item, dict)]
    if added or removed:
        return " ".join([*(f"+{label}" for label in added), *(f"-{label}" for label in removed)])
    return ""

