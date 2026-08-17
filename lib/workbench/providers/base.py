"""Provider contract and shared orchestration.

A provider owns every tracker-specific detail: URLs, query languages, field
names, pagination, payload shapes. Nothing above this layer knows what JQL or
WIQL is -- that is the whole point.

What a provider must *not* own is the policy: depth limits, expansion handling,
caps and degradation are orchestrated here, once, so the two trackers cannot
drift apart in behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import artifacts, depth as depth_policy, expand, http, schema, secrets
from ..contexts import Context
from ..errors import NotFoundError, WbError
from ..schema import Comment, Link, Task


@dataclass(frozen=True)
class Identity:
    """Who the configured credential authenticates as."""

    account: str
    detail: str


class Provider:
    name = "base"

    # Whether the tracker keeps a changelog worth offering as an expand handle.
    has_history = True

    def __init__(self, context: Context) -> None:
        self.context = context
        self._auth: str | None = None

    # ---- authentication -------------------------------------------------

    @property
    def auth(self) -> str:
        if self._auth is None:
            self._auth = self._build_auth(secrets.resolve(self.context.auth, self.context.name))
        return self._auth

    def _build_auth(self, token: str) -> str:
        raise NotImplementedError

    def probe(self) -> Identity:
        """One cheap authenticated call, to prove the context works."""
        raise NotImplementedError

    # ---- provider surface (implemented per tracker) ----------------------

    def list_tasks(self, limit: int) -> list[dict]:
        """Assigned, still-open tasks. key/status/title/updated and nothing else."""
        raise NotImplementedError

    def fetch_task(self, key: str) -> Task:
        """One task, normalised, with the full description and all links."""
        raise NotImplementedError

    def fetch_comments(self, key: str, limit: int | None) -> tuple[int, list[Comment]]:
        """(total, newest-first comments). ``limit=None`` means all of them."""
        raise NotImplementedError

    def fetch_descriptions(self, keys: list[str]) -> dict[str, str]:
        """Plain-text descriptions for several keys, in as few calls as possible."""
        raise NotImplementedError

    def fetch_history(self, key: str, limit: int) -> list[str]:
        """Recent field changes, one short line each."""
        raise NotImplementedError

    def fetch_updated(self, key: str) -> str:
        """The task's last-modified stamp, in one field-limited request.

        Used to revalidate the cache. Cheap enough that paying it beats serving
        a ticket that gained a comment four minutes ago without saying so.
        """
        raise NotImplementedError

    # ---- shared orchestration -------------------------------------------

    def get_task(self, key: str, depth: int, requested: list[str], *, use_cache: bool = True) -> dict:
        depth = depth_policy.validate(depth)
        task = self._load_task(key, use_cache=use_cache)

        total, comments = self.fetch_comments(task.key, schema.COMMENTS_RECENT)
        task.comments_total = total
        task.comments = comments

        task.linked_total = len(task.linked)
        if depth == 0:
            task.linked = []
        else:
            task.linked = task.linked[: schema.LINKED_MAX]

        full_desc = task.desc
        task.desc, task.desc_chars, task.desc_truncated = schema.make_desc(full_desc)

        offered = expand.offer(task, history=self.has_history)
        expand.validate(requested, offered)

        extra: dict[str, object] = {}
        notes: list[str] = []

        if expand.DESC_FULL in requested:
            task.desc, task.desc_truncated = full_desc, False
            task.desc_chars = len(full_desc)

        if expand.COMMENTS_ALL in requested:
            total, comments = self.fetch_comments(task.key, None)
            task.comments_total, task.comments = total, comments

        if expand.HISTORY in requested:
            extra["history"] = self.fetch_history(task.key, limit=10)

        wanted_bodies = [k for k in (expand.linked_key(h) for h in requested) if k]
        for link in depth_policy.selection(task.linked, depth, task.key):
            wanted_bodies.append(link.key)

        if wanted_bodies:
            # A batch call may return more than was asked for. Only the links we
            # actually selected get a body, or the depth policy leaks: "relates"
            # would pick one up for free just by sharing a response.
            allowed = {k.upper() for k in wanted_bodies}
            descriptions = self.fetch_descriptions(sorted(allowed))
            for link in task.linked:
                if link.key.upper() not in allowed:
                    continue
                body = descriptions.get(link.key.upper())
                if body:
                    link.desc, _, cut = schema.make_desc(body)
                    if cut:
                        notes.append(f"linked {link.key} desc")

        payload = task.to_dict(expand.offer(task, history=self.has_history), truncations=notes)
        payload.update(extra)
        return schema.fit(payload)

    def _load_task(self, key: str, *, use_cache: bool) -> Task:
        name = f"{self.name}-task.json"
        cached = artifacts.cache_get(key, name) if use_cache else None

        if cached is not None and self._still_current(key, cached):
            return _task_from_cache(cached)

        task = self.fetch_task(key)
        artifacts.cache_put(key, name, _task_to_cache(task))
        return task

    def _still_current(self, key: str, cached: dict) -> bool:
        """A cache entry is only good while the ticket has not moved.

        Time alone is not a proof of freshness: a ticket that gained a decisive
        comment two minutes ago would be served silently stale, and silently is
        the part that matters.
        """
        stamp = cached.get("updated")
        if not stamp:
            return False
        try:
            return self.fetch_updated(key) == stamp
        except WbError:
            return False  # If we cannot confirm, refetch rather than assume.

    # ---- HTTP helpers ---------------------------------------------------

    def get(self, path: str, **query) -> object:
        return http.request("GET", f"{self.context.base_url}{path}", auth=self.auth, query=query or None)

    def post(self, path: str, body: object, **query) -> object:
        return http.request(
            "POST", f"{self.context.base_url}{path}", auth=self.auth, body=body, query=query or None
        )

    def require_dict(self, data: object, what: str) -> dict:
        if not isinstance(data, dict):
            raise NotFoundError(f"{what} returned no usable data")
        return data


def _task_to_cache(task: Task) -> dict:
    return {
        "key": task.key,
        "title": task.title,
        "status": task.status,
        "type": task.type,
        "provider": task.provider,
        "url": task.url,
        "assignee": task.assignee,
        "updated": task.updated,
        "desc": task.desc,
        "linked": [
            {"key": l.key, "type": l.type, "status": l.status, "title": l.title, "url": l.url}
            for l in task.linked
        ],
        "unmapped": task.unmapped,
    }


def _task_from_cache(data: dict) -> Task:
    return Task(
        key=data["key"],
        title=data["title"],
        status=data["status"],
        type=data["type"],
        provider=data["provider"],
        url=data.get("url", ""),
        assignee=data.get("assignee"),
        updated=data.get("updated", ""),
        desc=data.get("desc", ""),
        linked=[Link(**item) for item in data.get("linked", [])],
        unmapped=list(data.get("unmapped", [])),
    )
