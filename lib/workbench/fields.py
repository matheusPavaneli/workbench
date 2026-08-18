"""Fields a tenant carries that the normalised schema has no slot for.

Two halves of one problem. A Jira instance with a `customfield_10042` holding
acceptance criteria is not unusual -- it is the norm -- and today that field is
simply absent from the normalised task, with nothing saying so. Silence is the
worst possible handling: a wrong mapping gets noticed and corrected, a missing
one produces a plan written without the acceptance criteria and nobody knows.

So: **report** what is being dropped (`wb ctx test --deep`), and let the repo
**map** what it cares about (`field_map` in `.workflow/config.json`). The tool
never guesses which custom field means what -- guessing is how a field named
"Impact" ends up read as the description on the one tenant where it is a number.

Destinations are a closed set, for the same reason the CLI surface is closed: an
open one is an invitation to invent a slot no consumer reads.
"""

from __future__ import annotations

from typing import Any

# Where a mapped field may land. Each is read by something downstream, which is
# the entire test for whether a destination should exist.
DESTINATIONS = (
    "acceptance_criteria",  # plan-change reads it as the definition of done
    "steps_to_reproduce",   # trace-incident and write-handover both use it
    "impact",               # frame-product and the handover's plain summary
    "component",            # routing and the critical-zone hint
    "environment",          # which deployment the report came from
)

# Never worth reporting as "dropped": either read already, or structural noise
# every payload carries.
BORING = frozenset(
    {
        "summary", "status", "issuetype", "assignee", "updated", "description",
        "issuelinks", "parent", "subtasks", "comment", "created", "creator",
        "reporter", "project", "watches", "votes", "worklog", "progress",
        "workratio", "self", "id", "key", "expand", "attachment", "timetracking",
        "aggregateprogress", "statuscategorychangedate", "lastViewed",
    }
)

MAX_SAMPLE = 80


def mapped(payload: dict, field_map: dict[str, str]) -> dict[str, str]:
    """Values pulled out by an explicit mapping, keyed by destination.

    Unknown destinations are dropped rather than passed through: a typo in the
    config should lose the field loudly at validation time, not invent a key
    that no skill reads.
    """
    found: dict[str, str] = {}
    for source, destination in (field_map or {}).items():
        if destination not in DESTINATIONS:
            continue
        value = _flatten(payload.get(source))
        if value:
            found[destination] = value
    return found


def unread(payload: dict, read: set[str], field_map: dict[str, str] | None = None) -> dict[str, str]:
    """Fields present and carrying content that nothing in this tool reads.

    Returns a sample of each, capped, so the output is a decision aid rather
    than a dump of the payload it is describing.
    """
    accounted = set(read) | BORING | set(field_map or {})
    reported: dict[str, str] = {}

    for name, value in (payload or {}).items():
        if name in accounted:
            continue
        sample = _flatten(value)
        if not sample:
            continue  # An empty custom field is not a finding; every tenant has hundreds.
        reported[name] = sample[:MAX_SAMPLE] + ("…" if len(sample) > MAX_SAMPLE else "")

    return reported


def validate(field_map: Any) -> list[str]:
    """Problems with a configured mapping, as lines. Empty means usable."""
    if field_map in (None, {}):
        return []
    if not isinstance(field_map, dict):
        return ['field_map must be an object of "<source field>": "<destination>" pairs']

    problems = []
    for source, destination in field_map.items():
        if not isinstance(source, str) or not source.strip():
            problems.append(f"field_map has an empty source name: {source!r}")
        if destination not in DESTINATIONS:
            problems.append(
                f"field_map sends {source!r} to {destination!r}, which nothing reads; "
                f"destinations: {', '.join(DESTINATIONS)}"
            )
    return problems


def _flatten(value: Any) -> str:
    """A field's content as one string, whatever container the tracker used.

    Trackers wrap the same value half a dozen ways -- a bare string, `{"value":
    …}` for a select, a list of those for a multi-select, and Atlassian document
    format for anything rich. A reader that handles only the first sees an empty
    field on most tenants.
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_flatten(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "text"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        if value.get("type") == "doc" or "content" in value:
            from .text import adf_to_text

            return adf_to_text(value).strip()
    return ""
