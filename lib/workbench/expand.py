"""Expansion handles: a map of what exists, without the contents.

``_expand`` lists what *could* be fetched for a task. The agent picks one and
passes it straight back to ``wb task get --expand``. Handles are opaque: they
are generated here from what the payload actually contains, and validated here
on the way back in. A handle that was never offered is rejected with the list
of the ones that were -- which is what stops an invented ``comments:page2``
from silently returning nothing.
"""

from __future__ import annotations

from .errors import UsageError
from .schema import DEEP_LINK_TYPES, Task

DESC_FULL = "desc:full"
COMMENTS_ALL = "comments:all"
HISTORY = "history"


def offer(task: Task, *, history: bool = True) -> list[str]:
    """Build the handle list for a task, from what is genuinely there.

    ``history`` is a provider capability, not a property of the task: a
    file-backed backlog keeps no changelog, and offering a handle that can only
    ever return an empty list costs a round trip to learn nothing.
    """
    handles: list[str] = []
    if task.desc_truncated:
        handles.append(DESC_FULL)
    if task.comments_total > len(task.comments):
        handles.append(COMMENTS_ALL)
    for link in task.linked:
        if link.type in DEEP_LINK_TYPES and not link.desc:
            handles.append(f"linked:{link.key}:full")
    if history:
        handles.append(HISTORY)
    return handles


def validate(requested: list[str], offered: list[str]) -> list[str]:
    unknown = [handle for handle in requested if handle not in offered]
    if unknown:
        raise UsageError(
            f"unknown expand handle(s): {', '.join(unknown)}",
            fix=[
                f"valid handles for this task: {', '.join(offered)}" if offered
                else "this task offers no expand handles",
                "handles come from the _expand list of a previous 'wb task get'; do not compose them",
            ],
        )
    return requested


def linked_key(handle: str) -> str | None:
    """Extract the ticket key from a ``linked:<KEY>:full`` handle."""
    parts = handle.split(":")
    if len(parts) == 3 and parts[0] == "linked" and parts[2] == "full":
        return parts[1]
    return None
