"""GitLab Issues provider (REST v4).

Works against gitlab.com and self-managed instances alike: ``base_url`` is the
instance root and the API lives under ``/api/v4``.

**Keys are issue IIDs**, plain numbers. An IID is unique within a project, and
the project is fixed by the context (``group/subgroup/project``) or by the
checkout's remote on the same host -- so one number maps back to exactly one
issue. The global issue id would also be unique, but nobody sees it: every
GitLab URL, reference and UI shows the IID, and a key the user cannot read off
the screen is a key they will get wrong.

What GitLab has that GitHub lacks, and this file uses:

- **Typed issue links.** ``blocks`` and ``is_blocked_by`` map to the canonical
  pair and ``relates_to`` to ``relates``, so a blocker is reported as one.
- **An issue type**, for incidents; everything else still comes from labels,
  including scoped ones (``type::bug``).
- **System notes**, which are the issue's history. They are excluded from the
  comments and read as the history instead, at no extra request.

What it lacks: a field-limited read. Revalidation reads the whole issue, but it
still saves the links request that a refetch costs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from .. import gitctx, http, schema
from ..errors import AuthError, ConfigError, UsageError
from ..schema import Comment, Link, Task
from ..text import normalise
from .base import Identity, Provider

API = "/api/v4"
PAGE_SIZE = 100
MAX_PAGES = 3

_LINK_TYPES = {"blocks": schema.BLOCKS, "is_blocked_by": schema.BLOCKED_BY, "relates_to": schema.RELATES}

_REMOTE = (
    re.compile(r"^(?:ssh://)?git@([^:/]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$"),
    re.compile(r"^https?://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+?)(?:\.git)?/?$"),
)


class GitlabProvider(Provider):
    name = "gitlab"

    # ---- identity and transport -----------------------------------------

    @property
    def host(self) -> str:
        return (urllib.parse.urlparse(self.context.base_url).hostname or "").lower()

    @property
    def project(self) -> str:
        """``group/subgroup/project``, from the context or from the remote."""
        configured = (self.context.project or "").strip("/")
        if configured:
            if "/" not in configured:
                raise ConfigError(
                    f"context {self.context.name!r}: project must be the full path, got {configured!r}",
                    fix=['e.g. "project": "acme/platform/widgets"'],
                )
            return configured

        remote = gitctx.origin(Path.cwd())
        path = _remote_path(remote.url) if remote else None
        if remote is None or path is None:
            raise ConfigError(
                "no project to read issues from",
                fix=[
                    "run inside a checkout with a GitLab origin remote",
                    'or set "project": "group/project" in the context file',
                ],
            )
        if remote.host != self.host:
            raise ConfigError(
                f"origin points at {remote.host}, but the context's base_url is {self.host}",
                fix=[f"for a self-managed instance set base_url to https://{remote.host}",
                     'or set "project" explicitly'],
            )
        return path

    @property
    def _project_id(self) -> str:
        return urllib.parse.quote(self.project, safe="")

    def _build_auth(self, token: str) -> str:
        return f"Bearer {token}"

    @property
    def auth(self) -> str:
        """A configured token wins; otherwise borrow the one ``glab`` stored."""
        if self._auth is not None:
            return self._auth
        if self.context.auth.get("pat_env") or self.context.auth.get("pat_keychain"):
            self._auth = super().auth
            return self._auth
        self._auth = self._build_auth(_glab_token(self.host))
        return self._auth

    def get(self, path: str, **query) -> object:
        return http.request("GET", f"{self.context.base_url}{API}{path}", auth=self.auth, query=query or None)

    def probe(self) -> Identity:
        data = self.require_dict(self.get("/user"), "/user")
        return Identity(account=str(data.get("username") or "unknown"), detail=f"{self.host}/{self.project}")

    # ---- reading --------------------------------------------------------

    def list_tasks(self, limit: int) -> list[dict]:
        data = self.get(
            f"/projects/{self._project_id}/issues",
            scope="assigned_to_me",
            state="opened",
            order_by="updated_at",
            sort="desc",
            per_page=min(limit, PAGE_SIZE),
        )
        rows = []
        for issue in data if isinstance(data, list) else []:
            if isinstance(issue, dict):
                rows.append(
                    {
                        "key": str(issue.get("iid", "")),
                        "status": str(issue.get("state", "")),
                        "title": schema.make_title(issue.get("title")),
                        "updated": str(issue.get("updated_at", ""))[:10],
                    }
                )
        return rows[:limit]

    def fetch_task(self, key: str) -> Task:
        issue = self.require_dict(self._issue(key), f"issue {key}")
        unmapped: list[str] = []
        labels = [str(label) for label in issue.get("labels") or [] if isinstance(label, str)]
        return Task(
            key=str(issue.get("iid", key)),
            title=schema.make_title(issue.get("title")),
            status=str(issue.get("state", "")),
            type=_type_from(str(issue.get("issue_type") or ""), labels, unmapped),
            provider=self.name,
            url=str(issue.get("web_url", "")),
            assignee=_person(issue.get("assignee")),
            updated=str(issue.get("updated_at", "")),
            desc=normalise(str(issue.get("description") or "")),
            linked=self._links(key, issue, unmapped),
            unmapped=unmapped,
        )

    def _links(self, key: str, issue: dict, unmapped: list[str]) -> list[Link]:
        links: list[Link] = []
        epic = issue.get("epic")
        if isinstance(epic, dict) and epic.get("iid"):
            links.append(
                Link(key=f"&{epic['iid']}", type=schema.PARENT, status="", title=schema.make_title(epic.get("title")))
            )

        data = self.get(f"/projects/{self._project_id}/issues/{key}/links")
        own = self.project.lower()
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not item.get("iid"):
                continue
            kind = str(item.get("link_type") or "")
            mapped = _LINK_TYPES.get(kind)
            if mapped is None:
                note = f"issue link type: {kind or '?'}"
                if note not in unmapped:
                    unmapped.append(note)
                mapped = schema.OTHER
            links.append(
                Link(
                    key=_link_key(item, own),
                    type=mapped,
                    status=str(item.get("state", "")),
                    title=schema.make_title(item.get("title")),
                )
            )
        return links

    def _notes(self, key: str) -> list[dict]:
        collected: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.get(
                f"/projects/{self._project_id}/issues/{key}/notes",
                sort="desc",
                order_by="created_at",
                per_page=PAGE_SIZE,
                page=page,
            )
            batch = [note for note in data if isinstance(note, dict)] if isinstance(data, list) else []
            collected.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        # Sorted here as well: the order is asked for, and then not trusted.
        collected.sort(key=lambda note: str(note.get("created_at", "")), reverse=True)
        return collected

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        wanted = schema.MAX_COMMENTS_ALL if limit is None else limit
        comments = [note for note in self._notes(_check_key(key)) if not note.get("system")]
        return len(comments), [
            schema.make_comment(
                author=_person(note.get("author")) or "unknown",
                when=str(note.get("created_at", ""))[:10],
                raw=normalise(str(note.get("body") or "")),
            )
            for note in comments[:wanted]
        ]

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        """No batch endpoint; one request per same-project IID, capped."""
        result: dict[str, str] = {}
        for key in keys[: schema.LINKED_MAX]:
            if not key.isdigit():
                continue  # another project's issue, or an epic: not ours to fetch
            try:
                issue = self.require_dict(self._issue(key), f"issue {key}")
            except (ConfigError, UsageError, AuthError):
                raise
            except Exception:  # noqa: BLE001 - a missing link must not fail the triage
                continue
            result[key.upper()] = normalise(str(issue.get("description") or ""))
        return result

    def fetch_history(self, key: str, limit: int) -> list[str]:
        lines = []
        for note in self._notes(_check_key(key)):
            if not note.get("system"):
                continue
            who = _person(note.get("author")) or "unknown"
            body = " ".join(str(note.get("body") or "").split())[:120]
            lines.append(f"{str(note.get('created_at', ''))[:10]} {who}: {body}")
        return lines[:limit]

    def fetch_updated(self, key: str) -> str:
        return str(self.require_dict(self._issue(key), f"issue {key}").get("updated_at", ""))

    def _issue(self, key: str) -> object:
        return self.get(f"/projects/{self._project_id}/issues/{_check_key(key)}")


def _check_key(key: str) -> str:
    cleaned = str(key).strip().lstrip("#")
    if not cleaned.isdigit():
        raise UsageError(
            f"GitLab issues are keyed by their number in the project, got {key!r}",
            fix=["use the issue number GitLab shows, e.g. wb task get 42"],
        )
    return cleaned


def _remote_path(url: str) -> str | None:
    for pattern in _REMOTE:
        match = pattern.match(url.strip())
        if match:
            path = match.group(2).strip("/")
            return path if "/" in path else None
    return None


def _link_key(item: dict, own_project: str) -> str:
    """The IID for this project's issues; ``group/project#IID`` for another's."""
    iid = str(item.get("iid"))
    references = item.get("references") if isinstance(item.get("references"), dict) else {}
    full = str((references or {}).get("full") or "")
    project = full.rsplit("#", 1)[0].lower() if "#" in full else own_project
    return iid if project == own_project else full


def _glab_token(host: str) -> str:
    from .. import redact

    fix = [
        "install glab and run: glab auth login",
        "or add a token: wb ctx add <name> --provider gitlab --pat-env GITLAB_TOKEN",
    ]
    if shutil.which("glab") is None:
        raise AuthError("no GitLab credential: glab is not on PATH and the context names none", fix=fix)
    try:
        completed = subprocess.run(
            ["glab", "config", "get", "token", "--host", host],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthError(f"could not read a token from glab: {exc}", fix=fix) from exc

    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise AuthError(f"glab has no token for {host}", fix=fix)
    redact.register(token)
    return token


def _person(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return str(value.get("name") or value.get("username") or "") or None


def _type_from(issue_type: str, labels: list[str], unmapped: list[str]) -> str:
    """``issue_type`` only distinguishes incidents; labels carry the rest."""
    if issue_type.lower() == "incident":
        return "incident"
    lowered = {label.lower().rsplit("::", 1)[-1].strip() for label in labels}
    for label, kind in (
        ("bug", "bug"),
        ("defect", "bug"),
        ("feature", "feature"),
        ("enhancement", "feature"),
        ("chore", "chore"),
        ("maintenance", "chore"),
        ("documentation", "chore"),
        ("support", "support"),
        ("question", "support"),
    ):
        if label in lowered:
            return kind
    if labels:
        unmapped.append(f"labels: {', '.join(sorted(labels)[:5])}")
    return "issue"
