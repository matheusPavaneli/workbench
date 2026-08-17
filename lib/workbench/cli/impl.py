"""``wb impl`` -- keep an implementation inside the plan it was audited against.

Two checks, both refusing to run on a plan that has not passed its audit. A
plan whose citations were never verified is not a plan to implement from, and
letting the next step start anyway is how an unverified claim reaches a PR.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import artifacts, gitctx, profile as profile_lib, verify as verify_lib
from ..errors import EXIT_AUDIT, UsageError, WbError

ACTIONS = ["check", "verify"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("impl", help="implementation guardrails")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    check = actions.add_parser("check", help="compare the working tree against the plan's file list")
    check.add_argument("key")
    check.add_argument("--staged", action="store_true", help="compare the staged diff instead")

    verify = actions.add_parser("verify", help="run the plan's verify[] commands and record the output")
    verify.add_argument("key")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb impl needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"check": _check, "verify": _verify}[args.action](args)


def _check(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = _audited_plan(key)
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()

    planned = {str(item.get("path", "")).replace("\\", "/") for item in doc.get("files") or []}
    planned.discard("")
    changed = set(gitctx.changed_files(root, staged=args.staged))

    unplanned = sorted(changed - planned)
    untouched = sorted(planned - changed)

    for path in sorted(changed & planned):
        print(f"  ok        {path}")
    for path in untouched:
        print(f"  pending   {path}  (planned, not changed yet)")

    if not unplanned:
        print(f"\nin plan: {len(changed & planned)} of {len(planned)} planned file(s) changed, nothing outside")
        return 0

    sys.stdout.flush()  # keep the file list above the failure that explains it
    print(f"\nDEVIATION  {len(unplanned)} file(s) changed that the plan does not list:", file=sys.stderr)
    for path in unplanned:
        print(f"  {path}", file=sys.stderr)

    zones = profile_lib.critical_zones(unplanned)
    for zone, paths in sorted(zones.items()):
        print(f"  critical zone {zone}: {', '.join(paths)}", file=sys.stderr)

    print(
        "\nStop. Either revert these, or say why the plan was wrong and update sdd.json,\n"
        "then re-run: wb sdd audit " + key,
        file=sys.stderr,
    )
    return EXIT_AUDIT


def _verify(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = _audited_plan(key)
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()

    commands = verify_lib.require_commands(doc.get("verify"))
    for command in commands:
        print(f"running: {command}", flush=True)

    evidence = verify_lib.run(key, commands, root)
    artifacts.write_json(key, "evidence.json", evidence.to_dict())
    path = artifacts.write_text(key, "evidence.md", verify_lib.render(evidence))

    for result in evidence.results:
        status = "pass" if result.ok else f"FAIL exit {result.exit_code}"
        print(f"  {status:<14} {result.command}  ({result.duration_ms} ms)")
    sys.stdout.flush()
    for command, reason in evidence.refused:
        print(f"  refused        {command}\n                 {reason}", file=sys.stderr)

    print(f"\nwrote {path}")
    if evidence.passed:
        return 0

    print("verification failed: fix the code, not the evidence", file=sys.stderr)
    return EXIT_AUDIT


def _audited_plan(key: str) -> dict:
    """Load the plan, refusing unless its audit passed."""
    doc = artifacts.read_json(key, "sdd.json")
    try:
        report = artifacts.read_json(key, "audit.json")
    except WbError:
        raise UsageError(
            f"{key} has no audit result",
            fix=[f"run: wb sdd audit {key}"],
        ) from None

    if report.get("verdict") != "pass":
        raise UsageError(
            f"the audit for {key} did not pass",
            fix=[
                f"see .workflow/{key}/audit.json",
                "fix the plan and re-run: wb sdd audit " + key,
            ],
        )
    return doc
