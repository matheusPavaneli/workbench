"""``wb sdd`` -- write, check and read back the implementation spec.

``audit`` is the gate: it reopens every ``file:line`` a plan cites and checks the
quoted text is really there. A plan that fails does not proceed, and the exit
code says so, so a chained skill cannot carry on past it by accident.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .. import artifacts, audit as audit_lib, contract, events, gitctx, profile as profile_lib, sdd as sdd_lib
from ..errors import EXIT_AUDIT, UsageError, WbError

ACTIONS = ["audit", "amend", "get", "render", "handover", "gates"]


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

    amend = actions.add_parser("amend", help="add files to an audited plan and re-audit it; exit 7 on failure")
    amend.add_argument("key")
    amend.add_argument("paths", nargs="+", metavar="path")
    amend.add_argument("--why", help="why the plan missed it; required")
    amend.add_argument("--new", action="store_true", help="the files do not exist yet; the change creates them")
    amend.add_argument("--lines", type=int, default=None, metavar="N",
                       help="estimated lines changed in each file; without it the plan is standard")

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
    handlers = {
        "audit": _audit, "amend": _amend, "get": _get, "render": _render, "handover": _handover, "gates": _gates,
    }
    return handlers[args.action](args)


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
        previous = artifacts.read_json(key, "audit.json")
    except WbError:
        return None  # no previous audit: this plan is still being written
    if not isinstance(previous, dict):
        return None
    # Only a plan that has passed is under way. A first audit that failed is
    # still the strict one, however many times it is re-run; once a plan has
    # passed, a failing correction keeps the anchor it already had.
    if previous.get("verdict") != "pass" and not previous.get("under_way"):
        return None
    return str(previous.get("baseline") or "") or None


def _audit(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    doc = artifacts.read_json(key, "sdd.json")
    root = gitctx.checkout()

    baseline = _baseline(key, root, rebaseline=args.rebaseline)
    report = audit_lib.run(doc, root, baseline)
    events.note_verdicts(finding.verdict for finding in report.findings)
    artifacts.write_json(key, "audit.json", report.to_dict())

    if args.json:
        print(contract.emit("sdd.audit", report.to_dict()))
        return 0 if report.passed else EXIT_AUDIT
    return _print_report(key, report)


def _print_report(key: str, report: audit_lib.Report) -> int:
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
        for item in report.pending:
            print(f"      pending   {item}")
        if report.pending:
            print(f"      owed before the PR: wb pr check --key {key} refuses until it is filled "
                  f"and wb sdd handover {key} has written handover.md")
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


def _amend(args: argparse.Namespace) -> int:
    """Add files to a plan that passed, and re-audit it where it stands.

    The narrow door for "the plan missed a file": no hand edit of sdd.json, no
    zones copied by hand, the baseline kept -- and a record, in the plan and in
    the event log, that the plan grew after its audit.
    """
    key = artifacts.validate_key(args.key)
    root = gitctx.checkout()
    doc = artifacts.read_json(key, "sdd.json")
    try:
        previous = artifacts.read_json(key, "audit.json")
    except WbError:
        previous = None

    why = audit_lib.standing(previous, doc)
    if why is not None:
        raise UsageError(
            f"the plan for {key} {why}; amend widens a plan that stands",
            fix=[f"run: wb sdd audit {key}"],
        )
    reason = str(args.why or "").strip()
    if not reason:
        raise UsageError("an amendment needs --why", fix=["say why the plan missed it: --why \"<reason>\""])

    planned = {str(item.get("path", "")).replace("\\", "/") for item in doc.get("files") or [] if isinstance(item, dict)}
    paths = []
    for raw in args.paths:
        path = _relative(root, raw)
        if path in planned or path in paths:
            raise UsageError(f"{path} is already in the plan for {key}")
        exists = (root / path).is_file()
        if not args.new and not exists:
            raise UsageError(f"{path} does not exist", fix=["to plan a file that will be created, pass --new"])
        if args.new and exists:
            raise UsageError(f"{path} already exists; --new is for a file the change creates")
        paths.append(path)

    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    files = list(doc.get("files") or [])
    amendments = list(doc.get("amendments") or [])
    for path in paths:
        entry: dict = {"path": path, "change": "add" if args.new else "edit", "why": reason}
        if args.lines is not None:
            entry["lines"] = args.lines
        files.append(entry)
        amendments.append({"path": path, "why": reason, "at": at})
    doc["files"] = files
    doc["amendments"] = amendments
    doc["zones"] = profile_lib.critical_zones(
        [str(item.get("path")) for item in files if isinstance(item, dict) and item.get("path")]
    )
    artifacts.write_json(key, "sdd.json", doc)
    events.note_amended(len(paths))

    # The anchor the plan passed at, so the citations are judged as before.
    report = audit_lib.run(doc, root, str(previous.get("baseline") or "") or None)
    events.note_verdicts(finding.verdict for finding in report.findings)
    for path in paths:
        print(f"amended   {path}  ({reason})")
    if report.passed:
        # Refreshed only on pass: a failed amendment leaves the plan amended
        # and not standing, so nothing implements from it until it is fixed.
        artifacts.write_json(key, "audit.json", report.to_dict())
    else:
        print(f"the plan is amended but no longer stands; fix it and re-run: wb sdd audit {key}", file=sys.stderr)
    return _print_report(key, report)


def _relative(root: Path, raw: str) -> str:
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root.resolve())
        except ValueError:
            raise UsageError(f"{raw} is outside this checkout") from None
    path = candidate.as_posix().removeprefix("./")
    if not path or path.startswith("../") or path.split("/", 1)[0] == artifacts.WORKFLOW_DIR:
        raise UsageError(f"{raw} is not a file this plan can list")
    return path


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
        root = gitctx.checkout()
        preset = profile_lib.resolve(root).preset
    print(f"preset {preset}")
    for gate in sdd_lib.gates_for(preset):
        print(f"  - {gate}")
    return 0
