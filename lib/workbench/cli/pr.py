"""``wb pr`` -- everything a pull request description should be built from.

Assembled from what already exists: the branch, the commits, the plan's summary
and the recorded verification. A description written from this cites evidence
that was produced; one written from memory cites evidence that was hoped for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import artifacts, contract, flow as flow_lib, gitctx, profile as profile_lib, prose, sdd as sdd_lib
from ..errors import UsageError, WbError

ACTIONS = ["context", "check"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("pr", help="assemble the inputs for a pull request description")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    context = actions.add_parser("context", help="branch, commits, plan summary, verification verdict")
    context.add_argument("key")
    context.add_argument("--base", help="branch this would merge into (from the flow by default)")
    context.add_argument("--target", help="a validation target from the flow, e.g. homolog")

    check = actions.add_parser("check", help="reject filler, empty sections and placeholders in a draft")
    check.add_argument("--file", required=True)
    check.add_argument("--shape", choices=[prose.TRIVIAL, prose.SMALL, prose.LARGE])


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb pr needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"context": _context, "check": _check}[args.action](args)


def _check(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise UsageError(f"no such file: {args.file}", fix=["write the draft to a file first"])

    problems = prose.check(path.read_text(encoding="utf-8"), expected_shape=args.shape)
    if not problems:
        print("ok")
        return 0

    print("description needs work:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 2


def _context(args: argparse.Namespace) -> int:
    key = artifacts.validate_key(args.key)
    root = gitctx.repo_root(Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])

    flow = flow_lib.resolve(root)
    if args.target:
        base = flow.target(args.target).branch
    else:
        base = args.base or flow.source.branch
    changed = gitctx.changed_files(root)

    payload: dict = {
        "key": key,
        "branch": gitctx.branch(root),
        "base": base,
        "commits": gitctx.subjects_since(root, base),
        "changed": changed,
        "zones": profile_lib.critical_zones(changed),
        "preset": profile_lib.resolve(root).preset,
        "size": prose.size_class(changed),
        "shape": prose.SHAPE[prose.size_class(changed)],
    }

    payload["plan"] = _optional(key, "sdd.json", lambda doc: sdd_lib.section(doc, "summary"))
    payload["questions"] = _optional(key, "sdd.json", lambda doc: doc.get("questions") or [])
    payload["verification"] = _optional(key, "evidence.json", _verification)

    for name, value in (("plan", payload["plan"]), ("verification", payload["verification"])):
        if value is None:
            payload.setdefault("_missing", []).append(name)

    if payload["branch"] == base:
        # Otherwise the empty commit list reads as "this branch adds nothing".
        payload["_note"] = f"branch and base are both {base!r}; pass --base to compare against something"

    print(contract.emit("pr.context", payload))
    return 0


def _verification(evidence: dict) -> dict:
    """Only the verdict and the commands. Full output stays in evidence.md."""
    return {
        "verdict": evidence.get("verdict"),
        "commands": [
            {"command": result.get("command"), "exit_code": result.get("exit_code")}
            for result in evidence.get("results") or []
        ],
        "refused": [item.get("command") for item in evidence.get("refused") or []],
    }


def _optional(key: str, name: str, extract):
    """Missing artifacts are reported, never faked."""
    try:
        return extract(artifacts.read_json(key, name))
    except WbError:
        return None
