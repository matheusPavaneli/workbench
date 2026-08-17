"""``wb review`` -- the computed half of a review.

What moved, where the bar is higher, and what changed without a test. The
judgement stays with the reviewer; this removes the part that is the same
every time and easy to get wrong by hand.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import gitctx, profile as profile_lib, review as review_lib
from ..errors import UsageError

ACTIONS = ["context"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("review", help="computed facts about the current diff")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    context = actions.add_parser("context", help="changed files, critical zones, gates, untested sources")
    context.add_argument("--staged", action="store_true")
    context.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb review needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return _context(args)


def _context(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    root = gitctx.repo_root(cwd)
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])

    changed = gitctx.changed_files(root, staged=args.staged)
    if not changed:
        print("no changes" + (" staged" if args.staged else ""))
        return 0

    detected = profile_lib.detect(root)
    zones = profile_lib.critical_zones(changed)
    missing_tests = review_lib.untested(changed)

    if args.json:
        print(
            json.dumps(
                {
                    "preset": detected.preset,
                    "changed": changed,
                    "zones": zones,
                    "untested": missing_tests,
                    "gates": detected.gates(),
                },
                indent=2,
            )
        )
        return 0

    print(f"preset    {detected.preset}")
    print(f"changed   {len(changed)} file(s)")
    for path in changed:
        marker = "T" if review_lib.is_test(path) else " "
        print(f"  {marker} {path}")

    if zones:
        print("\ncritical zones touched -- the bar rises here regardless of preset:")
        for zone, paths in sorted(zones.items()):
            print(f"  {zone}: {', '.join(sorted(set(paths)))}")

    if missing_tests:
        print("\nsource changed with no test changed alongside (heuristic, verify each):")
        for path in missing_tests:
            print(f"  {path}")

    print("\ngates:")
    for gate in detected.gates():
        print(f"  - {gate}")
    return 0
