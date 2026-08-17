"""Local backlog provider. No tracker, no network, no credential.

The point of this provider is reach. Everything downstream of ``triage.json``
-- planning, the citation audit, the scope guard, verification, the commit
convention -- is tracker-agnostic and works on any repo. Requiring a Jira site
to get at it meant a side project, a fresh clone or a Saturday afternoon could
not use nine tenths of the tooling.

A task is one JSON file under ``.workflow/tasks/``. It is deliberately a normal
file in the repo: committable when a team wants a shared backlog, ignorable
when one person just wants somewhere to put a title before planning it.

Nothing here invents fields the other providers have. A local task has no
changelog, so ``history`` is not offered rather than faked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .. import artifacts, gitctx, schema
from ..errors import NotFoundError, UsageError
from ..schema import Comment, Link, Task
from ..text import normalise
from .base import Identity, Provider

TASKS_DIR = "tasks"
KEY_PREFIX = "WB"

# The types the rest of the toolchain already branches on: write-handover is
# required for bug and support work, and the commit convention maps type to a
# subject prefix. Keeping the set closed means those branches cannot miss.
TYPES = ["feature", "bug", "chore", "support", "spike"]

OPEN = "open"
DONE = "done"
STATUSES = [OPEN, "in-progress", "blocked", DONE]

_KEY_IN_TEXT = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")


def tasks_dir(cwd: Path | None = None) -> Path:
    return artifacts.root(cwd) / TASKS_DIR


def task_path(key: str, cwd: Path | None = None) -> Path:
    return tasks_dir(cwd) / f"{artifacts.validate_key(key)}.json"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_key(cwd: Path | None = None) -> str:
    """The lowest unused ``WB-<n>``.

    Reusing a freed number would point two different ``.workflow/<KEY>/``
    directories at one task, so the counter only ever moves forward: it is the
    highest number seen plus one, not the count of files.
    """
    highest = 0
    for path in tasks_dir(cwd).glob(f"{KEY_PREFIX}-*.json"):
        try:
            highest = max(highest, int(path.stem.split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"{KEY_PREFIX}-{highest + 1}"


def create(
    title: str,
    *,
    kind: str = "feature",
    desc: str = "",
    key: str | None = None,
    links: list[str] | None = None,
    cwd: Path | None = None,
) -> dict:
    if kind not in TYPES:
        from ..errors import unknown_choice

        raise unknown_choice("type", kind, TYPES)

    title = " ".join(title.split())
    if not title:
        raise UsageError("a task needs a title", fix=['wb task new "what needs doing"'])

    key = artifacts.validate_key(key) if key else next_key(cwd)
    path = task_path(key, cwd)
    if path.exists():
        raise UsageError(f"task {key} already exists", fix=[f"edit {path}, or omit --key for the next free one"])

    stamp = now()
    data = {
        "key": key,
        "title": title,
        "type": kind,
        "status": OPEN,
        "desc": desc,
        "created": stamp,
        "updated": stamp,
        "comments": [],
        "linked": [{"key": k, "type": schema.RELATES} for k in (links or [])],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


class LocalProvider(Provider):
    name = "local"
    has_history = False  # a JSON file has no changelog; see fetch_history

    # ---- no transport ---------------------------------------------------

    @property
    def auth(self) -> str:
        raise NotFoundError(
            "the local provider makes no requests",
            fix=["this is a bug: something tried to authenticate a file-backed backlog"],
        )

    def probe(self) -> Identity:
        directory = tasks_dir()
        count = len(list(directory.glob("*.json"))) if directory.is_dir() else 0
        who = gitctx.identity(Path.cwd()).get("email") or "this checkout"
        return Identity(account=who, detail=f"{count} task(s) in {directory}")

    def _load_task(self, key: str, *, use_cache: bool) -> Task:
        # A file read is already cheaper than the cache write it would trigger,
        # and a stale local task would be a file the user just edited by hand.
        return self.fetch_task(key)

    # ---- reading --------------------------------------------------------

    def list_tasks(self, limit: int) -> list[dict]:
        rows = []
        for data in self._all():
            if str(data.get("status", OPEN)) == DONE:
                continue
            rows.append(
                {
                    "key": str(data.get("key", "")),
                    "status": str(data.get("status", OPEN)),
                    "title": schema.make_title(data.get("title")),
                    "updated": str(data.get("updated", ""))[:10],
                }
            )
        rows.sort(key=lambda row: row["updated"], reverse=True)
        return rows[:limit]

    def fetch_task(self, key: str) -> Task:
        data = self._read(key)
        unmapped: list[str] = []
        return Task(
            key=str(data.get("key", key)),
            title=schema.make_title(data.get("title")),
            status=str(data.get("status", OPEN)),
            type=str(data.get("type", "feature")),
            provider=self.name,
            url=task_path(key).as_uri() if task_path(key).exists() else "",
            assignee=self.context.git.get("email"),
            updated=str(data.get("updated", "")),
            desc=normalise(str(data.get("desc", ""))),
            linked=self._links(data, unmapped),
            unmapped=unmapped,
        )

    def _links(self, data: dict, unmapped: list[str]) -> list[Link]:
        links: list[Link] = []
        seen: set[str] = set()

        for item in data.get("linked") or []:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            key = str(item["key"]).upper()
            link_type = str(item.get("type", schema.RELATES))
            if link_type not in schema.LINK_TYPES:
                unmapped.append(f"link type: {link_type}")
                link_type = schema.OTHER
            seen.add(key)
            links.append(self._link(key, link_type))

        # Keys mentioned in the body are how people actually cross-reference a
        # file-backed backlog. They are "relates" and never more: an untyped
        # mention is not evidence that something blocks.
        for key in _KEY_IN_TEXT.findall(str(data.get("desc", ""))):
            if key.upper() not in seen and key.upper() != str(data.get("key", "")).upper():
                seen.add(key.upper())
                links.append(self._link(key.upper(), schema.RELATES))

        return links

    def _link(self, key: str, link_type: str) -> Link:
        path = tasks_dir() / f"{key}.json"
        if not path.is_file():
            # A reference to something this backlog does not hold. Say so
            # rather than dropping it -- a dangling link is a finding.
            return Link(key=key, type=link_type, status="unknown", title="(not in this backlog)")
        data = self._parse(path)
        return Link(
            key=key,
            type=link_type,
            status=str(data.get("status", OPEN)),
            title=schema.make_title(data.get("title")),
            url=path.as_uri(),
        )

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        raw = [c for c in self._read(key).get("comments") or [] if isinstance(c, dict)]
        raw.reverse()  # newest first, matching every other provider
        wanted = schema.MAX_COMMENTS_ALL if limit is None else limit
        return len(raw), [
            schema.make_comment(
                author=str(item.get("author", "")) or "unknown",
                when=str(item.get("when", ""))[:10],
                raw=normalise(str(item.get("text", ""))),
            )
            for item in raw[:wanted]
        ]

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in keys:
            path = tasks_dir() / f"{key.upper()}.json"
            if path.is_file():
                result[key.upper()] = normalise(str(self._parse(path).get("desc", "")))
        return result

    def fetch_history(self, key: str, limit: int) -> list[str]:
        """A file-backed task keeps no changelog, and inventing one would be a lie."""
        return []

    def fetch_updated(self, key: str) -> str:
        return str(self._read(key).get("updated", ""))

    # ---- files ----------------------------------------------------------

    def _all(self) -> list[dict]:
        directory = tasks_dir()
        if not directory.is_dir():
            return []
        return [self._parse(path) for path in sorted(directory.glob("*.json"))]

    def _read(self, key: str) -> dict:
        path = task_path(key)
        if not path.is_file():
            known = [p.stem for p in sorted(tasks_dir().glob("*.json"))][:10]
            raise NotFoundError(
                f"no local task {key}",
                fix=[
                    f"tasks here: {', '.join(known)}" if known else "this backlog is empty",
                    'create one: wb task new "title" --type bug',
                ],
            )
        return self._parse(path)

    def _parse(self, path: Path) -> dict:
        from ..errors import ConfigError

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}", fix=["fix the syntax, or delete the file"]) from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path} must contain a JSON object", fix=['expected {"key": ..., "title": ...}'])
        return data
