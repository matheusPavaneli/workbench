"""``wb review`` -- the computed half of a review.

What moved, where the bar is higher, and what changed without a test. The
judgement stays with the reviewer; this removes the part that is the same
every time and easy to get wrong by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import artifacts, gitctx, profile as profile_lib, review as review_lib
from ..errors import EXIT_AUDIT, UsageError

ACTIONS = ["context", "gates"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("review", help="computed facts about the current diff")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    context = actions.add_parser("context", help="changed files, critical zones, gates, untested sources")
    context.add_argument("--staged", action="store_true")
    context.add_argument("--json", action="store_true")

    gates = actions.add_parser("gates", help="settle the gates a script can settle, as findings")
    gates.add_argument("--staged", action="store_true")
    gates.add_argument("--key", help="a ticket key, so the bug-fix regression gate knows the ticket type")
    gates.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb review needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"context": _context, "gates": _gates}[args.action](args)


def _gates(args: argparse.Namespace) -> int:
    root = gitctx.repo_root(Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])

    changed = gitctx.changed_files(root, staged=args.staged)
    added = gitctx.added_lines(root, staged=args.staged)
    findings = review_lib.gate_findings(added) + review_lib.dependency_findings(added)

    ticket_type = ""
    if args.key:
        try:
            ticket_type = str(artifacts.read_json(artifacts.validate_key(args.key), "triage.json").get("type", ""))
        except Exception:  # noqa: BLE001 - a missing triage must not fail the review
            ticket_type = ""
    missing_regression = review_lib.regression_test_missing(ticket_type, changed)

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [f.to_dict() for f in findings],
                    "regression_test_missing": missing_regression,
                    "ticket_type": ticket_type,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return EXIT_AUDIT if findings or missing_regression else 0

    if not findings and not missing_regression:
        print("pass  no mechanically checkable gate was violated")
        print("      the remaining gates are judgement; wb review context lists them")
        return 0

    for finding in findings:
        print(f"{finding.file}:{finding.line}  {finding.severity}  {finding.detail}", file=sys.stderr)
        print(f"    gate: {finding.gate}", file=sys.stderr)
        print(f"    {finding.quote}", file=sys.stderr)
    if missing_regression:
        print(f"diff  high  a {ticket_type} fix with no test file in the diff", file=sys.stderr)
        print("    gate: every bug fix lands with a regression test that fails without it", file=sys.stderr)
    return EXIT_AUDIT


def _context(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    root = gitctx.repo_root(cwd)
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])

    changed = gitctx.changed_files(root, staged=args.staged)
    if not changed:
        print("no changes" + (" staged" if args.staged else ""))
        return 0

    detected = profile_lib.resolve(root)
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
