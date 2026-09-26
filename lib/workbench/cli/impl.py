"""``wb impl`` -- keep an implementation inside the plan it was audited against.

Two checks, both refusing to run on a plan that has not passed its audit. A
plan whose citations were never verified is not a plan to implement from, and
letting the next step start anyway is how an unverified claim reaches a PR.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .. import artifacts, audit as audit_lib, gitctx, profile as profile_lib, scope as scope_lib, sdd as sdd_lib
from .. import companions as companions_lib, verify as verify_lib
from .. import events, status as status_lib
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
    verify.add_argument(
        "--approve",
        action="append",
        default=[],
        metavar="ENTRY",
        help="approve one command, or 'env NAME=value', on this machine; repeatable, exact text",
    )
    verify.add_argument(
        "--regression",
        action="store_true",
        help="also run each regression test without the fix (must fail) and with it (must pass)",
    )


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb impl needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"check": _check, "verify": _verify}[args.action](args)


def _check(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = _audited_plan(key)
    root = gitctx.checkout()

    planned = {str(item.get("path", "")).replace("\\", "/") for item in doc.get("files") or []}
    planned.discard("")
    changed = set(gitctx.changed_files(root, staged=args.staged))

    # A file another audited plan lists is accounted for -- elsewhere, but
    # accounted for. Without this, a second ticket in the same checkout read as
    # scope creep on the first, which is the ordinary state of a working day.
    claimed = scope_lib.claims(key)
    # A file tied to a planned one -- its test, its lockfile -- follows it
    # through. companions.reason is the same call the edit hook makes.
    globs = companions_lib.generated_globs(root)
    ties = {path: companions_lib.reason(path, planned, globs) for path in changed - planned}
    companions = {path: tie for path, tie in ties.items() if tie}
    elsewhere = sorted((changed - planned - set(companions)) & set(claimed))
    unplanned = sorted(changed - planned - set(companions) - set(claimed))
    untouched = sorted(planned - changed)
    overlap = sorted(planned & set(claimed))

    for path in sorted(changed & planned):
        print(f"  ok        {path}")
    for path in untouched:
        print(f"  pending   {path}  (planned, not changed yet)")
    for path in sorted(companions):
        print(f"  companion {path}  ({companions[path]})")
    for path in elsewhere:
        print(f"  other     {path}  (claimed by {', '.join(claimed[path])})")
    for path in overlap:
        # Two plans editing one file is worth knowing before either lands.
        print(f"  overlap   {path}  (also planned by {', '.join(claimed[path])})")

    oversize = _oversize(key, doc, root, sorted(planned), staged=args.staged)

    if not unplanned and not oversize:
        carried = f", {len(companions)} companion(s)" if companions else ""
        carried += f", {len(elsewhere)} carried by another ticket" if elsewhere else ""
        print(
            f"\nin plan: {len(changed & planned)} of {len(planned)} planned file(s) changed, "
            f"nothing outside{carried}"
        )
        return 0

    sys.stdout.flush()  # keep the file list above the failure that explains it
    if oversize:
        print(f"\nDEVIATION  {oversize}", file=sys.stderr)
        print(
            "\nThe light tier was granted on an estimate the diff has outgrown. Re-plan at\n"
            "standard: correct files[].lines, add steps and product to sdd.json,\n"
            "then re-run: wb sdd audit " + key,
            file=sys.stderr,
        )
    if not unplanned:
        return EXIT_AUDIT

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


def _oversize(key: str, doc: dict, root: Path, planned: list[str], *, staged: bool) -> str:
    """Why a light plan's real diff is over the light bound, or "".

    Only the planned paths are measured, against the commit the audit ran on:
    a file another ticket accounts for, or a companion that follows a planned
    file through, is not this plan's size. A diff that cannot be measured --
    a binary file, a failed git call -- counts as over, so the bound never
    fails open.
    """
    bound = sdd_lib.light_max_lines(root)
    if sdd_lib.tier(doc, bound)[0] != sdd_lib.LIGHT:
        return ""
    baseline = str(artifacts.read_json(key, "audit.json").get("baseline") or "") or "HEAD"
    measured = gitctx.lines_changed(root, baseline, planned, staged=staged)
    if measured is None:
        return f"light plan, but its diff against {baseline[:8]} cannot be measured (binary or unreadable)"
    if measured > bound:
        estimate, _ = sdd_lib.estimated_lines(doc)
        return f"light plan measured {measured} lines changed (estimated ~{estimate}, light is up to {bound})"
    print(f"  size      {measured} line(s) changed, light is up to {bound}")
    return ""


def _verify(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = _audited_plan(key)
    root = gitctx.checkout()

    commands = verify_lib.require_commands(doc.get("verify"))
    env, _ = verify_lib.resolve_env(doc.get("verify_env"))
    regression, base, carried = _regression_plan(doc, root) if args.regression else ([], None, [])
    wanted = verify_lib.entries([*commands, *(command for _, command in regression)], env, key)

    # The printed <KEY> form, or the command as the plan spells it: either way
    # what is stored is the <KEY> form, so the next ticket does not ask again.
    given = [verify_lib.abstract(entry, key) for entry in args.approve]
    stray = [entry for entry, form in zip(args.approve, given) if form not in wanted]
    if stray:
        raise UsageError(
            f"not in the plan for {key}: {', '.join(repr(entry) for entry in stray)}",
            fix=["--approve takes an entry exactly as wb impl verify printed it"],
        )
    if given:
        verify_lib.approve(root, given)

    pending = verify_lib.unapproved(root, wanted, key)
    if pending:
        # Nothing runs, and no evidence is written: a partial run would be a
        # verdict on a subset nobody chose.
        print(f"not approved on this machine ({len(pending)}):", file=sys.stderr)
        for entry in pending:
            print(f"  {entry}", file=sys.stderr)
        call = " ".join(f"--approve {shlex.quote(entry)}" for entry in pending)
        print(
            "\nRead them. These come from a plan, and they run with your permissions.\n"
            f"To approve and run: wb impl verify {key} {call}",
            file=sys.stderr,
        )
        # A gate waiting on a person, not a failed verification: the history
        # must not read it as a fragile step.
        events.note_held()
        return EXIT_AUDIT

    for command in commands:
        print(f"running: {command}", flush=True)

    tree, head = gitctx.tree(root), gitctx.head(root)
    evidence = verify_lib.run(key, commands, root, doc.get("verify_env"))
    evidence.tree, evidence.head, evidence.plan = tree, head, audit_lib.digest(doc)
    targets = verify_lib.test_targets(doc)
    if targets:
        # Only a plan that names tests owes the git call.
        evidence.tests_missing = verify_lib.missing_tests(targets, status_lib.branch_changes(root))
    if args.regression and base:
        for _target, command in regression:
            print(f"regression: {command}  (without the fix at {base[:12]}, then with it)", flush=True)
        evidence.regression_base = base
        evidence.regression = verify_lib.run_regression(regression, root, base, carried, doc.get("verify_env"))
    if evidence.env:
        print(f"environment: {', '.join(sorted(evidence.env))}", flush=True)
    artifacts.write_json(key, "evidence.json", evidence.to_dict())
    path = artifacts.write_text(key, "evidence.md", verify_lib.render(evidence))

    for result in evidence.results:
        status = "pass" if result.ok else f"FAIL exit {result.exit_code}"
        print(f"  {status:<14} {result.command}  ({result.duration_ms} ms)")
    for outcome in evidence.regression or []:
        status = "pass" if outcome.ok else "FAIL"
        print(
            f"  {status:<14} regression {outcome.target}  "
            f"(without fix exit {outcome.without_fix.exit_code}, with fix exit {outcome.with_fix.exit_code})"
        )
    sys.stdout.flush()
    for command, reason in evidence.refused:
        print(f"  refused        {command}\n                 {reason}", file=sys.stderr)
    for target in evidence.tests_missing:
        print(f"  not written    {target}\n                 the plan names this test; the branch never changed it",
              file=sys.stderr)
    for outcome in evidence.regression or []:
        if not outcome.ok:
            print(f"  regression     {outcome.target}\n                 {_regression_reason(outcome)}", file=sys.stderr)

    print(f"\nwrote {path}")
    if evidence.passed:
        return 0

    print("verification failed: fix the code, not the evidence", file=sys.stderr)
    return EXIT_AUDIT


def _regression_plan(doc: dict, root: Path) -> tuple[list[tuple[str, str]], str, list[str]]:
    """What --regression runs, against which commit, carrying which files.

    Refuses rather than runs something weaker: a plan with no regression tests,
    a runner with no per-file form, or a base git cannot resolve would each
    produce a verdict about something other than "fails without the fix".
    """
    targets = verify_lib.regression_targets(doc)
    if not targets:
        raise UsageError(
            "the plan names no regression tests",
            fix=['add tests[] entries with "kind": "regression" to sdd.json, then re-run the audit'],
        )
    conventions = profile_lib.resolve(root).conventions
    runner = conventions.get("test_runner")
    pairs = []
    for target in targets:
        command, refusal = verify_lib.regression_command(runner, target)
        if command is None:
            raise UsageError(f"cannot run {target} on its own: {refusal}",
                             fix=["run it yourself without the fix, and record the result"])
        pairs.append((target, command))

    fix = ["check the flow's source branch: wb flow show"]
    try:
        from .. import flow as flow_lib

        carry = flow_lib.carry_base(root, flow_lib.resolve(root).source.branch)
    except Exception as exc:  # noqa: BLE001 - same resolution status uses; here it refuses instead
        raise UsageError("cannot resolve the branch this work started from", fix=fix) from exc
    base = gitctx.merge_base(root, "HEAD", carry)
    if not base:
        raise UsageError(f"cannot resolve the commit this branch left {carry}", fix=fix)

    test_dir = str(conventions.get("test_dir") or "").strip("/")
    changed = status_lib.branch_changes(root)
    carried = sorted(
        path for path in changed if path in targets or (test_dir and path.startswith(test_dir + "/"))
    )
    return pairs, base, carried


def _regression_reason(outcome: verify_lib.Regression) -> str:
    code = outcome.without_fix.exit_code
    if code == 0:
        return "passes without the fix, so it does not test the fix"
    if code in verify_lib.NOT_A_TEST_FAILURE:
        return f"without the fix it did not run (exit {code}), which proves nothing"
    return f"fails with the fix too (exit {outcome.with_fix.exit_code})"


def _audited_plan(key: str) -> dict:
    """Load the plan, refusing unless its audit passed on this very plan."""
    doc = artifacts.read_json(key, "sdd.json")
    try:
        report = artifacts.read_json(key, "audit.json")
    except WbError:
        raise UsageError(
            f"{key} has no audit result",
            fix=[f"run: wb sdd audit {key}"],
        ) from None

    reason = audit_lib.standing(report, doc)
    if reason == audit_lib.STALE:
        raise UsageError(
            f"the plan for {key} changed since its audit",
            fix=["re-run: wb sdd audit " + key],
        )
    if reason:
        raise UsageError(
            f"the audit for {key} did not pass",
            fix=[
                f"see .workflow/{key}/audit.json",
                "fix the plan and re-run: wb sdd audit " + key,
            ],
        )
    return doc
