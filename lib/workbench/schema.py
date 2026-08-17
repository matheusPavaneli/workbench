"""The internal schema. One shape, whatever the tracker.

Normalisation lives here and in the providers, in code, because if it lives in
the model's judgement then every session normalises differently and field names
that sound plausible but do not exist start appearing in plans.

Two rules hold everywhere:

- **Never invent.** A field a provider does not have is omitted, and what we
  could not map is listed in ``_unmapped`` so it is visible rather than lost.
- **Never unbounded.** Every string and every list has a cap, and exceeding it
  is recorded in ``_truncated`` rather than silently swallowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .text import truncate

SCHEMA_VERSION = 1

DESC_CHARS = 800
COMMENT_CHARS = 300
COMMENTS_RECENT = 5
# Paging for an explicit comments:all. The ceiling exists because "all" on a
# five-year-old ticket is not a useful answer, and an unbounded one is worse.
COMMENT_PAGE = 100
MAX_COMMENTS_ALL = 200
TITLE_CHARS = 160
LINKED_MAX = 15
# Hard ceiling on one serialised task. Reached only by pathological tickets;
# degradation below is deterministic so two runs never differ.
TOTAL_BYTES = 3072

# Canonical link types. Providers map onto this set and nothing else -- a
# consumer must never have to know whether it is reading Jira or Azure.
BLOCKS = "blocks"
BLOCKED_BY = "blocked_by"
DUPLICATES = "duplicates"
DUPLICATED_BY = "duplicated_by"
PARENT = "parent"
CHILD = "child"
RELATES = "relates"
OTHER = "other"

LINK_TYPES = [BLOCKS, BLOCKED_BY, DUPLICATES, DUPLICATED_BY, PARENT, CHILD, RELATES, OTHER]

# Only these justify reading a linked item's full body. "relates" never does:
# it is the type people attach when they are not sure, and following it in
# depth is how a triage turns into a crawl of the whole board.
DEEP_LINK_TYPES = frozenset({BLOCKS, BLOCKED_BY, PARENT, CHILD})


@dataclass
class Link:
    key: str
    type: str
    status: str
    title: str
    url: str = ""
    desc: str = ""  # populated only at depth 2, and only for DEEP_LINK_TYPES


@dataclass
class Comment:
    author: str
    when: str
    text: str
    truncated: bool = False


@dataclass
class Task:
    key: str
    title: str
    status: str
    type: str
    provider: str
    url: str = ""
    assignee: str | None = None
    updated: str = ""
    desc: str = ""
    desc_chars: int = 0
    desc_truncated: bool = False
    comments_total: int = 0
    comments: list[Comment] = field(default_factory=list)
    linked: list[Link] = field(default_factory=list)
    linked_total: int = 0
    unmapped: list[str] = field(default_factory=list)

    def to_dict(self, expand_handles: list[str], truncations: list[str] | None = None) -> dict:
        truncations = list(truncations or [])
        payload = {
            "schema": SCHEMA_VERSION,
            "provider": self.provider,
            "key": self.key,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "assignee": self.assignee,
            "updated": self.updated,
            "url": self.url,
            "desc": self.desc,
            "desc_chars": self.desc_chars,
            "comments": {
                "total": self.comments_total,
                "recent": [_comment_dict(c) for c in self.comments],
            },
            "linked": [_link_dict(link) for link in self.linked],
            "linked_total": self.linked_total,
            "_expand": expand_handles,
        }
        if self.desc_truncated:
            truncations.append("desc")
        if self.linked_total > len(self.linked):
            truncations.append(f"linked ({self.linked_total - len(self.linked)} more)")
        if self.comments_total > len(self.comments):
            truncations.append(f"comments ({self.comments_total - len(self.comments)} more)")
        if self.unmapped:
            payload["_unmapped"] = sorted(set(self.unmapped))
        if truncations:
            payload["_truncated"] = truncations
        return payload


def _link_dict(link: Link) -> dict:
    # No url: it is derivable from the key and the root url, and repeating it
    # for every link is roughly a sixth of a typical payload for no new fact.
    data = {"key": link.key, "type": link.type, "status": link.status, "title": link.title}
    if link.desc:
        data["desc"] = link.desc
    return data


def _comment_dict(comment: Comment) -> dict:
    data = {"author": comment.author, "when": comment.when, "text": comment.text}
    if comment.truncated:
        data["truncated"] = True
    return data


def make_title(raw: str | None) -> str:
    title, _ = truncate(" ".join((raw or "").split()), TITLE_CHARS)
    return title


def make_desc(raw: str) -> tuple[str, int, bool]:
    """Return (capped text, original length, whether it was cut)."""
    total = len(raw)
    capped, was_cut = truncate(raw, DESC_CHARS)
    return capped, total, was_cut


def make_comment(author: str, when: str, raw: str) -> Comment:
    text, was_cut = truncate(" ".join(raw.split()), COMMENT_CHARS)
    return Comment(author=author or "unknown", when=when or "", text=text, truncated=was_cut)


def fit(payload: dict) -> dict:
    """Bring a payload under ``TOTAL_BYTES``, shedding the least useful first.

    Order is fixed so the same ticket always degrades the same way: comment
    bodies, then whole comments, then linked-item descriptions, then links.
    """
    if _size(payload) <= TOTAL_BYTES:
        return payload

    shed = payload.setdefault("_truncated", [])

    for comment in payload["comments"]["recent"]:
        comment["text"], _ = truncate(comment["text"], 120)
        comment["truncated"] = True
    if _size(payload) <= TOTAL_BYTES:
        shed.append("comment bodies shortened to fit")
        return payload

    while payload["comments"]["recent"] and _size(payload) > TOTAL_BYTES:
        payload["comments"]["recent"].pop()
    if _size(payload) <= TOTAL_BYTES:
        shed.append("comments dropped to fit")
        return payload

    for link in payload["linked"]:
        link.pop("desc", None)
    if _size(payload) <= TOTAL_BYTES:
        shed.append("linked descriptions dropped to fit")
        return payload

    while len(payload["linked"]) > 1 and _size(payload) > TOTAL_BYTES:
        payload["linked"].pop()
    shed.append("linked items dropped to fit")
    return payload


def _size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
