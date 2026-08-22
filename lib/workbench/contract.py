"""The machine-readable surface, versioned so something else can depend on it.

Exit codes have been a contract since the beginning. The `--json` payloads were
not: they were whatever dict the command happened to build, so a field could be
renamed by an edit that looked local and every consumer would find out at
runtime. A skill in this package can be fixed in the same commit; a skill in
somebody else's cannot.

So each payload carries a `schema` number and a recorded set of top-level keys.
Adding a key is compatible and free. Removing or renaming one is not, and the
test that guards this fails until the version is raised deliberately -- which is
the point: the number changing is a decision, and it should cost a moment's
thought rather than happening by accident.
"""

from __future__ import annotations

import json
from typing import Any

# Command -> the version of the payload it emits. Raise one when a key is
# removed or renamed; adding a key needs no change.
VERSIONS: dict[str, int] = {
    "next": 1,
    "status": 1,
    "status.list": 1,
    "status.stats": 1,
    "route": 1,
    "repo.profile": 1,
    "repo.gates": 1,
    "flow.show": 1,
    "sdd.audit": 1,
    "commit.convention": 1,
    "review.context": 1,
    "review.gates": 1,
    "pr.context": 1,
    "surface": 1,
}

# The keys a consumer may rely on being present. Recorded rather than described,
# and asserted against the real output, so this cannot drift from the truth.
KEYS: dict[str, set[str]] = {
    "next": {"schema", "key", "origin", "stage", "state", "reason", "command", "skill"},
    "route": {"schema", "key", "tier", "reason", "steps"},
    "repo.profile": {
        "schema", "preset", "detected", "confidence", "confirmed",
        "alternatives", "signals", "conventions", "gates",
    },
    "repo.gates": {"schema", "preset", "by_preset", "gates"},
    "flow.show": {"schema", "strategy", "source", "validation", "branch_pattern", "protected", "detected"},
}


def emit(command: str, payload: dict) -> str:
    """The payload as a consumer sees it: stamped, ordered, ready to print."""
    if command not in VERSIONS:
        raise KeyError(f"{command!r} has no declared payload version; add it to contract.VERSIONS")
    stamped = {"schema": VERSIONS[command], **payload}
    return json.dumps(stamped, indent=2, ensure_ascii=False)


def stamp(command: str, payload: Any) -> Any:
    """As ``emit``, without serialising -- for a caller that nests the result."""
    if isinstance(payload, dict):
        return {"schema": VERSIONS.get(command, 1), **payload}
    return payload
