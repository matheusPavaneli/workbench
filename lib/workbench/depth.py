"""Depth policy. Explicit, enforced in code, identical every run.

    depth 0  the task alone
    depth 1  linked items as one line each: key, type, status, title
    depth 2  full body, but only for blocks / blocked_by / parent / child
    never    "relates" in depth, at any level

Depth 2 fetches one hop, never recursively: a board is a cyclic graph and a
recursive read of it is unbounded work for information nobody asked for. The
visited set and node cap below hold regardless, because a ticket can link the
same key twice, link itself, and appear again through its parent.
"""

from __future__ import annotations

from .errors import UsageError
from .schema import DEEP_LINK_TYPES, Link

MAX_DEPTH = 2
MAX_NODES = 25


def validate(depth: int) -> int:
    if depth < 0 or depth > MAX_DEPTH:
        raise UsageError(
            f"depth {depth} is out of range",
            fix=[
                "0 = the task only",
                "1 = linked items, one line each",
                "2 = full body of blocking and hierarchy links only",
            ],
        )
    return depth


def selection(links: list[Link], depth: int, root_key: str) -> list[Link]:
    """Which links get their body fetched at this depth."""
    if depth < 2:
        return []

    visited = {root_key.upper()}
    chosen: list[Link] = []
    for link in links:
        key = link.key.upper()
        if key in visited:
            continue
        visited.add(key)
        if link.type not in DEEP_LINK_TYPES:
            continue
        if len(chosen) >= MAX_NODES:
            break
        chosen.append(link)
    return chosen
