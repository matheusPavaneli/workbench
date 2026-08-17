"""``wb sdd`` -- write, check and read back the implementation spec.

``audit`` is the gate: it reopens every ``file:line`` a plan cites and checks the
quoted text is really there. A plan that fails does not proceed, and the exit
code says so, so a chained skill cannot carry on past it by accident.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import artifacts, audit as audit_lib, gitctx, profile as profile_lib, sdd as sdd_lib
from ..errors import EXIT_AUDIT, UsageError, WbError

ACTIONS = ["audit", "get", "render", "handover", "gates"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("sdd", help="implementation spec: audit, read, render")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    check = actions.add_parser("audit", help="verify every citation and required section; exit 7 on failure")
    check.add_argument("key")
    check.add_argument("--json", action="store_true")
    check.add_argument(
        "--rebaseline",
        action="store_true",
        help="re-anchor the plan's citations to the current commit instead of the one already recorded",
    )

    get = actions.add_parser("get", help="print one section, so consumers do not read the whole plan")
    get.add_argument("key")
    get.add_argument("--section", required=True, choices=sdd_lib.SECTIONS)

    render = actions.add_parser("render", help="write sdd.md from sdd.json, for people")
    render.add_argument("key")

    handover = actions.add_parser("handover", help="write handover.md: the note for QA and the reporter")
    handover.add_argument("key")

    gates = actions.add_parser("gates", help="the quality gates that apply, as lines")
    gates.add_argument("--preset", choices=profile_lib.PRESETS)


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb sdd needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"audit": _audit, "get": _get, "render": _render, "handover": _handover, "gates": _gates}[args.action](args)


def _baseline(key: str, root: Path, *, rebaseline: bool) -> str | None:
    """The commit this plan's citations are anchored to.

    Recorded on the first audit and reused after, so re-auditing is stable for
    the whole life of a plan however far implementation has gone. Without this
    the second audit of a plan under way fails on every line already rewritten,
    which is the one moment an author most needs to correct the plan.
    """
    if rebaseline:
        return None  # start again from the current tree, strictly
    try:
        return str(artifacts.read_json(key, "audit.json").get("baseline") or "") or None
    except WbError:
        return None  # no previous audit: this plan is still being written


def _audit(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = artifacts.read_json(key, "sdd.json")
    root = gitctx.repo_root(Path.cwd()) or Path.cwd()

    baseline = _baseline(key, root, rebaseline=args.rebaseline)
    report = audit_lib.run(doc, root, baseline)
    artifacts.write_json(key, "audit.json", report.to_dict())

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.passed else EXIT_AUDIT

    checked = len(report.findings)
    tier = f"{report.tier} tier ({report.tier_reason})"
    drifted = [f for f in report.findings if f.verdict in (audit_lib.BASELINE, audit_lib.MOVED)]
    if report.passed:
        print(f"pass  {checked} citation(s) verified, structure complete")
        print(f"      {tier}")
        for finding in drifted:
            # Never folded into the pass count in silence: these no longer
            # describe the code at the line the plan gives.
            print(f"      {finding.verdict:<9} {finding.file}:{finding.line}  {finding.detail}")
        if drifted:
            print(f"      the plan is under way, so line numbers were not enforced; "
                  f"re-anchor with: wb sdd audit {key} --rebaseline")
        if report.tier == sdd_lib.LIGHT:
            print(f"      waived: {', '.join(sdd_lib.LIGHT_WAIVES)}; citations, files, verify and rollback still apply")
        return 0

    print(f"FAIL  {len(report.failures)}/{checked} citation(s) unverified  [{tier}]", file=sys.stderr)
    for finding in report.failures:
        print(f"  {finding.verdict:<13} {finding.file}:{finding.line}  {finding.detail}", file=sys.stderr)
    for path in report.missing_paths:
        print(f"  missing_path  {path}  listed for edit but does not exist", file=sys.stderr)
    for problem in report.structure:
        print(f"  structure     {problem}", file=sys.stderr)
    print("\nfix the plan, not the check. Do not implement from a failed audit.", file=sys.stderr)
    return EXIT_AUDIT


def _get(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = artifacts.read_json(key, "sdd.json")
    print(json.dumps(sdd_lib.section(doc, args.section), indent=2, ensure_ascii=False))
    return 0


def _render(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = artifacts.read_json(key, "sdd.json")
    path = artifacts.write_text(key, "sdd.md", sdd_lib.render(doc))
    print(f"wrote {path}")
    return 0


def _handover(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = artifacts.read_json(key, "sdd.json")
    handover = doc.get("handover") or {}
    if not handover:
        raise UsageError(
            f"{key} has no handover section",
            fix=["add handover to sdd.json: symptom_plain, cause_plain, fix_plain, scope, workaround, qa_steps"],
        )
    path = artifacts.write_text(key, "handover.md", sdd_lib.render_handover(doc))
    print(f"wrote {path}")
    return 0


def _gates(args: argparse.Namespace) -> int:
    preset = args.preset
    if not preset:
        root = gitctx.repo_root(Path.cwd()) or Path.cwd()
        preset = profile_lib.detect(root).preset
    print(f"preset {preset}")
    for gate in sdd_lib.gates_for(preset):
        print(f"  - {gate}")
    return 0
