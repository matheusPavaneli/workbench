"""GitHub Issues provider.

Chosen for reach rather than depth. A Jira context costs a site URL, a project
key, an API token and an email before it answers anything; a GitHub context
costs nothing on a machine that already has ``gh`` logged in, which covers most
open-source and personal work.

Three things GitHub does not have, and this file refuses to fake:

- **Typed links.** There is no "blocks"/"duplicates" relation on the REST API.
  Body references (``#123``, ``owner/repo#123``) are mapped to ``relates`` and
  nothing stronger, because an untyped mention is not evidence of a blocker.
  Real hierarchy comes from the sub-issues fields when the payload carries them.
- **A field-limited read.** Every issue read returns the whole issue, so cache
  revalidation would cost exactly what a refetch costs. Caching is therefore
  skipped rather than pretended.
- **Newest-first comments.** They come back oldest-first with no total, so the
  provider pages and reverses, capped like every other provider.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .. import gitctx, http, schema
from ..errors import AuthError, ConfigError, UsageError
from ..schema import Comment, Link, Task
from ..text import normalise
from .base import Identity, Provider

API_VERSION = "2022-11-28"
ACCEPT = "application/vnd.github+json"
PAGE_SIZE = 100
MAX_PAGES = 5

# "#123" and "owner/repo#123". Fenced code and inline code are stripped first,
# so a snippet containing a colour literal does not become a link.
_REF = re.compile(r"(?:(?P<slug>[\w.-]+/[\w.-]+))?#(?P<num>\d+)\b")
_CODE = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)


class GithubProvider(Provider):
    name = "github"

    # ---- identity and transport -----------------------------------------

    @property
    def repo(self) -> str:
        """``owner/repo``, from the context or from the checkout's remote."""
        configured = (self.context.project or "").strip("/")
        if configured:
            if configured.count("/") != 1:
                raise ConfigError(
                    f"context {self.context.name!r}: project must be owner/repo, got {configured!r}",
                    fix=['e.g. "project": "matheusPavaneli/workbench"'],
                )
            return configured

        remote = gitctx.origin(Path.cwd())
        if remote is None or not remote.slug:
            raise ConfigError(
                "no repo to read issues from",
                fix=[
                    "run inside a checkout with a GitHub origin remote",
                    'or set "project": "owner/repo" in the context file',
                ],
            )
        if "github" not in remote.host:
            raise ConfigError(
                f"origin points at {remote.host}, not GitHub",
                fix=['set "project": "owner/repo" explicitly if this is a GitHub mirror'],
            )
        return remote.slug

    def _build_auth(self, token: str) -> str:
        return f"Bearer {token}"

    @property
    def auth(self) -> str:
        """A configured token wins; otherwise borrow the one ``gh`` already has.

        The fallback is the whole reason this provider is cheap to adopt. It is
        a fallback and not the default because a context that names its
        credential keeps working on a machine with no ``gh`` installed.
        """
        if self._auth is not None:
            return self._auth
        if self.context.auth.get("pat_env") or self.context.auth.get("pat_keychain"):
            self._auth = super().auth
            return self._auth
        self._auth = self._build_auth(_gh_token())
        return self._auth

    def get(self, path: str, **query) -> object:
        return http.request(
            "GET",
            f"{self.context.base_url}{path}",
            auth=self.auth,
            query=query or None,
            headers={"Accept": ACCEPT, "X-GitHub-Api-Version": API_VERSION},
        )

    def probe(self) -> Identity:
        data = self.require_dict(self.get("/user"), "/user")
        return Identity(
            account=str(data.get("login") or "unknown"),
            detail=f"github.com/{self.repo}",
        )

    def _load_task(self, key: str, *, use_cache: bool) -> Task:
        # See the module docstring: revalidation is not cheaper than refetching
        # here, so a cache would add a write and save nothing.
        return self.fetch_task(key)

    # ---- reading --------------------------------------------------------

    def list_tasks(self, limit: int) -> list[dict]:
        data = self.get(
            f"/repos/{self.repo}/issues",
            assignee="@me",
            state="open",
            sort="updated",
            direction="desc",
            per_page=min(limit, PAGE_SIZE),
        )
        if not isinstance(data, list):
            return []
        rows = []
        for issue in data:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue  # the issues endpoint returns PRs too; they are not work items
            rows.append(
                {
                    "key": str(issue.get("number", "")),
                    "status": _status(issue),
                    "title": schema.make_title(issue.get("title")),
                    "updated": str(issue.get("updated_at", ""))[:10],
                }
            )
        return rows[:limit]

    def fetch_task(self, key: str) -> Task:
        issue = self.require_dict(self._issue(key), f"issue {key}")
        unmapped: list[str] = []
        body = normalise(str(issue.get("body") or ""))

        labels = [str(l.get("name", "")) for l in issue.get("labels") or [] if isinstance(l, dict)]

        return Task(
            key=str(issue.get("number", key)),
            title=schema.make_title(issue.get("title")),
            status=_status(issue),
            type=_type_from(labels, unmapped),
            provider=self.name,
            url=str(issue.get("html_url", "")),
            assignee=_login(issue.get("assignee")),
            updated=str(issue.get("updated_at", "")),
            desc=body,
            linked=self._links(issue, body),
            unmapped=unmapped,
        )

    def _links(self, issue: dict, body: str) -> list[Link]:
        links: list[Link] = []
        seen: set[str] = set()
        number = str(issue.get("number", ""))

        parent = issue.get("parent") if isinstance(issue.get("parent"), dict) else None
        if parent and parent.get("number"):
            seen.add(str(parent["number"]))
            links.append(
                Link(
                    key=str(parent["number"]),
                    type=schema.PARENT,
                    status=_status(parent),
                    title=schema.make_title(parent.get("title")),
                )
            )

        summary = issue.get("sub_issues_summary")
        if isinstance(summary, dict) and summary.get("total"):
            # The count is in the issue payload; the children are a separate
            # request. Report the count rather than spend a call on titles.
            links.append(
                Link(
                    key=f"{number}/sub-issues",
                    type=schema.CHILD,
                    status=f"{summary.get('completed', 0)}/{summary['total']} done",
                    title="sub-issues",
                )
            )

        for match in _REF.finditer(_CODE.sub(" ", body)):
            ref = match.group("num")
            slug = match.group("slug")
            key = f"{slug}#{ref}" if slug else ref
            if key in seen or ref == number:
                continue
            seen.add(key)
            links.append(Link(key=key, type=schema.RELATES, status="", title="(referenced in the body)"))

        return links

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        wanted = schema.MAX_COMMENTS_ALL if limit is None else limit
        collected: list[dict] = []

        for page in range(1, MAX_PAGES + 1):
            data = self.get(f"/repos/{self.repo}/issues/{key}/comments", per_page=PAGE_SIZE, page=page)
            batch = [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
            collected.extend(batch)
            if len(batch) < PAGE_SIZE:
                break

        collected.reverse()  # the API returns oldest first; every consumer wants newest
        return len(collected), [
            schema.make_comment(
                author=_login(item.get("user")) or "unknown",
                when=str(item.get("created_at", ""))[:10],
                raw=normalise(str(item.get("body") or "")),
            )
            for item in collected[:wanted]
        ]

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        """No batch endpoint exists, so this is one request per key, capped."""
        result: dict[str, str] = {}
        for key in keys[: schema.LINKED_MAX]:
            if "#" in key or not key.isdigit():
                continue  # a cross-repo reference is not ours to fetch
            try:
                issue = self.require_dict(self._issue(key), f"issue {key}")
            except (ConfigError, UsageError):
                raise
            except Exception:  # noqa: BLE001 - a missing link must not fail the triage
                continue
            result[key.upper()] = normalise(str(issue.get("body") or ""))
        return result

    def fetch_history(self, key: str, limit: int) -> list[str]:
        data = self.get(f"/repos/{self.repo}/issues/{key}/timeline", per_page=PAGE_SIZE)
        if not isinstance(data, list):
            return []
        lines = []
        for event in reversed(data):
            if not isinstance(event, dict):
                continue
            name = str(event.get("event", ""))
            if name in {"commented", "subscribed", "mentioned"}:
                continue  # comments have their own channel; subscriptions are noise
            who = _login(event.get("actor")) or "unknown"
            when = str(event.get("created_at", ""))[:10]
            lines.append(f"{when} {who}: {name}{_event_detail(event)}")
        return lines[:limit]

    def fetch_updated(self, key: str) -> str:
        return str(self.require_dict(self._issue(key), f"issue {key}").get("updated_at", ""))

    def _issue(self, key: str) -> object:
        if not str(key).isdigit():
            raise UsageError(
                f"GitHub issues are numbered, got {key!r}",
                fix=["use the issue number, e.g. wb task get 42"],
            )
        return self.get(f"/repos/{self.repo}/issues/{key}")


def _gh_token() -> str:
    from .. import redact

    if shutil.which("gh") is None:
        raise AuthError(
            "no GitHub credential: gh is not on PATH and the context names none",
            fix=[
                "install the GitHub CLI and run: gh auth login",
                "or add a token: wb ctx add <name> --provider github --pat-env GITHUB_TOKEN",
            ],
        )
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthError(f"could not read a token from gh: {exc}", fix=["run: gh auth login"]) from exc

    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise AuthError(
            "gh has no token for this host",
            fix=["run: gh auth login", "or add a token: wb ctx add <name> --provider github --pat-env GITHUB_TOKEN"],
        )
    redact.register(token)
    return token


def _status(issue: dict) -> str:
    """``state_reason`` distinguishes a fix from a triage decision; state alone does not."""
    state = str(issue.get("state", ""))
    reason = str(issue.get("state_reason") or "")
    if state == "closed" and reason and reason != "completed":
        return f"closed ({reason.replace('_', ' ')})"
    return state


def _type_from(labels: list[str], unmapped: list[str]) -> str:
    """GitHub has no issue type, so the label set is the only signal there is."""
    lowered = {l.lower() for l in labels}
    for label, kind in (
        ("bug", "bug"),
        ("defect", "bug"),
        ("enhancement", "feature"),
        ("feature", "feature"),
        ("chore", "chore"),
        ("documentation", "chore"),
        ("support", "support"),
        ("question", "support"),
    ):
        if label in lowered:
            return kind
    if labels:
        unmapped.append(f"labels: {', '.join(sorted(labels)[:5])}")
    return "issue"


def _login(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return str(value.get("login") or "") or None


def _event_detail(event: dict) -> str:
    label = event.get("label")
    if isinstance(label, dict) and label.get("name"):
        return f" {label['name']}"
    if event.get("rename"):
        return " title"
    return ""
