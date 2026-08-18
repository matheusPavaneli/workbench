"""``wb route`` -- the shortest sequence that still clears the floor.

`sdd.tier` already refuses to let a plan argue its way to a lower bar. This is
the same reasoning one level up, applied to the *flow* rather than the document:
a one-line fix does not need ten skills, and pretending otherwise is how a tool
gets routed around. The cost of ceremony is not the minutes; it is that somebody
does the small change outside the process entirely, and then the process only
ever sees the changes nobody minded doing carefully.

So the short route is **named and computed**, not improvised. It is the same
gates, the same audit, the same commit convention -- with the two sections whose
value comes from size left out, exactly as the LIGHT tier already leaves them
out of the document.

Nothing here is a new skill: ten descriptions are already the always-on cost of
this plugin, and an eleventh to say "do less" would be the joke telling itself.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import artifacts, gitctx, profile as profile_lib, sdd as sdd_lib, status as status_lib
from ..errors import UsageError

ACTIONS: list[str] = []

# The full route, in order. Each step names the skill that does it and the
# command that proves it happened.
FULL = [
    ("triage", "triage-task", "wb task get {key}"),
    ("frame", "frame-product", ""),
    ("plan", "plan-change", "wb sdd audit {key}"),
    ("implement", "implement-change", "wb impl check {key}"),
    ("verify", "implement-change", "wb impl verify {key}"),
    ("handover", "write-handover", "wb sdd handover {key}"),
    ("commit", "write-commit", "wb commit check --file <path> --key {key}"),
    ("pr", "draft-pr", "wb pr context {key}"),
]

# What a change of one or two files, in no critical zone, on a ticket nobody
# has to explain to QA, actually needs.
SHORT = {"triage", "plan", "implement", "verify", "commit"}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("route", help="the steps this change actually needs, in order")
    parser.add_argument("key", nargs="?", help="a ticket key; omit to use the one this checkout is on")
    parser.add_argument("--files", nargs="*", default=None, help="paths the change will touch, if no plan exists yet")
    parser.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()
    key = _key(args)

    doc = _plan(key, root)
    paths = list(args.files or []) or [str(item.get("path", "")) for item in (doc or {}).get("files") or []]
    kind = _kind(key, root)

    tier, reason = _tier(doc, paths, kind)
    steps = [step for step in FULL if tier == sdd_lib.STANDARD or step[0] in SHORT]

    # A handover is owed by the ticket type, never by the size of the diff.
    if kind.lower() in sdd_lib.HANDOVER_TYPES or key.startswith("incident-"):
        steps = [step for step in FULL if step in steps or step[0] == "handover"]

    if args.json:
        print(json.dumps(
            {
                "schema": 1,
                "key": key,
                "tier": tier,
                "reason": reason,
                "steps": [{"step": name, "skill": skill, "check": check.format(key=key)} for name, skill, check in steps],
            },
            indent=2,
        ))
        return 0

    print(f"{key}  {tier} route  ({reason})")
    for index, (name, skill, check) in enumerate(steps, start=1):
        line = f"  {index}. {name:<10} {skill}"
        print(f"{line:<44}{check.format(key=key)}".rstrip())

    if tier == sdd_lib.LIGHT:
        print(f"\nwaived by the light tier: {', '.join(sdd_lib.LIGHT_WAIVES)}")
        print("the floor is not waived: citations, the file list, verify and rollback still apply")
    return 0


def _key(args: argparse.Namespace) -> str:
    if args.key:
        return artifacts.validate_key(args.key)
    picked = status_lib.pick()
    if picked is None:
        raise UsageError(
            "no ticket named and none in flight",
            fix=["name one: wb route ABC-123", "or start one: wb task list"],
        )
    return picked[0].key


def _plan(key: str, root: Path) -> dict | None:
    path = artifacts.ticket_dir(key, root) / "sdd.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _kind(key: str, root: Path) -> str:
    path = artifacts.ticket_dir(key, root) / "triage.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("type") or "")
    except (OSError, ValueError):
        return ""


def _tier(doc: dict | None, paths: list[str], kind: str) -> tuple[str, str]:
    """The plan's own tier where one exists; otherwise the same rules applied
    to whatever is known -- so the route can be asked for *before* planning,
    which is the only moment the answer changes anything."""
    if doc:
        return sdd_lib.tier(doc)

    if not paths:
        return sdd_lib.STANDARD, "no plan and no files named yet; assuming the full route"
    if len(paths) > sdd_lib.LIGHT_MAX_FILES:
        return sdd_lib.STANDARD, f"{len(paths)} files (light is up to {sdd_lib.LIGHT_MAX_FILES})"

    zones = profile_lib.critical_zones(paths)
    if zones:
        return sdd_lib.STANDARD, f"touches {', '.join(sorted(zones))}"
    if kind.lower() in sdd_lib.HANDOVER_TYPES:
        return sdd_lib.STANDARD, f"a {kind} ticket owes a handover"
    return sdd_lib.LIGHT, f"{len(paths)} file(s), no critical zone"
