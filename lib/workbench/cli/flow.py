"""``wb flow`` -- where work starts, where it lands, and what to carry across.

It computes the branch name, the base to start from, and the exact commits to
cherry-pick. By default it prints those commands and the user runs them, which
is what keeps a wrong computation a wasted paste rather than a wrong branch.

``--execute`` runs them, through the allowlist in ``gitrun``. The commands are
the same objects either way, so what runs is what was printed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import artifacts, contexts, contract, flow as flow_lib, gitctx, gitrun
from ..errors import UsageError, WbError

ACTIONS = ["show", "start", "carry", "set"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("flow", help="branching flow: source, validation targets, carrying")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    show = actions.add_parser("show", help="the resolved flow, and what this branch is doing in it")
    show.add_argument("--json", action="store_true")

    start = actions.add_parser("start", help="the branch name and base for a new piece of work")
    start.add_argument("key")
    start.add_argument("--title", required=True, help="ticket title, used for the slug")
    start.add_argument("--type", dest="kind", default="feature", help="value for {type} in the pattern")
    start.add_argument("--target", help="start against this target instead of the source branch")
    start.add_argument("--execute", action="store_true", help="run the commands instead of printing them")

    carry = actions.add_parser("carry", help="the commits to cherry-pick onto a validation target")
    carry.add_argument("key")
    carry.add_argument("--to", required=True, help="validation branch to carry onto")
    carry.add_argument("--execute", action="store_true", help="run the commands instead of printing them")

    configure = actions.add_parser("set", help="record the flow for this repo")
    configure.add_argument("--source", required=True, help="branch that holds the truth, e.g. main")
    configure.add_argument("--validation", action="append", default=[], help="repeatable, e.g. homolog")
    configure.add_argument("--strategy", choices=flow_lib.STRATEGIES, default="cherry-pick")
    configure.add_argument("--branch-pattern", help="e.g. feature/{key}-{slug} (must contain {key})")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb flow needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"show": _show, "start": _start, "carry": _carry, "set": _set}[args.action](args)


def _root() -> Path:
    root = gitctx.repo_root(Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])
    return root


def _show(args: argparse.Namespace) -> int:
    root = _root()
    flow = flow_lib.resolve(root)

    if args.json:
        print(contract.emit("flow.show", flow.to_dict()))
        return 0

    origin = "detected from remote branches" if flow.detected else "configured"
    print(f"strategy    {flow.strategy}  ({origin})")
    print(f"source      {flow.source.branch}    <- start here, PR here")
    for target in flow.validation:
        print(f"validation  {target.branch}    <- carried by {flow.strategy}")
    print(f"branches    {flow.pattern}")
    print(f"protected   {', '.join(flow.protected)}")

    current = gitctx.branch(root)
    print(f"\ncurrent     {current}")
    if current in flow.protected:
        print("            this is a protected branch; start a working branch before committing")
    if flow.detected:
        print("\nrecord it:  wb flow set --source <b> --validation <b> [--branch-pattern ...]")
    return 0


def _start(args: argparse.Namespace) -> int:
    root = _root()
    flow = flow_lib.resolve(root)
    key = artifacts.validate_key(args.key)

    base = flow.target(args.target).branch if args.target else flow.source.branch
    name = flow_lib.branch_name(flow, key, args.title, args.kind)

    if gitctx.branch_exists(root, name):
        print(f"branch {name} already exists")
        return 0

    print(f"branch  {name}")
    print(f"base    {base}")
    return _emit(flow_lib.start_actions(name, base), root, flow, key, execute=args.execute)


def _carry(args: argparse.Namespace) -> int:
    root = _root()
    flow = flow_lib.resolve(root)
    key = artifacts.validate_key(args.key)
    target = flow.target(args.to)

    if target.role != "validation":
        raise UsageError(
            f"{target.branch!r} is the source branch, not a validation target",
            fix=["carry onto a validation branch; the source gets a PR, not a cherry-pick"],
        )

    source_branch = _source_branch(root, flow, key)

    # Fetch before measuring, not alongside it. The range is "what the source
    # does not have yet", and answering that against stale remote-tracking refs
    # puts commits already merged upstream back into the carry.
    fetch = flow_lib.fetch_action()
    if args.execute:
        run = gitrun.apply([fetch], root, protected=flow.protected)
        gitrun.record(run, key, root)
        if not run.ok:
            print(gitrun.render(run))
            return 1

    base = flow_lib.carry_base(root, flow.source.branch)
    commits = flow_lib.carry_plan(root, source_branch, base, target.branch)
    if not commits:
        print(f"nothing to carry: {source_branch} has no commits that {base} lacks")
        return 0

    carry_branch = f"{source_branch}-{target.branch}"
    if gitctx.branch_exists(root, carry_branch):
        # Same guard ``start`` has. Without it a second run tries to create a
        # branch that exists, fails, and leaves the cherry-pick to be applied
        # somewhere it does not belong.
        print(f"branch {carry_branch} already exists; carry it by hand or delete it first")
        return 0

    print(f"from    {source_branch}  ({len(commits)} commit(s) {base} lacks, oldest first)")
    for line in commits:
        print(f"  {line}")

    actions = flow_lib.carry_actions(carry_branch, target.branch, commits)
    if not args.execute:
        # Printed for the user to run, the fetch included: the range above was
        # measured against the refs as they are now, so a stale checkout should
        # refresh and re-read it rather than trust this list.
        actions = [fetch, *actions]

    code = _emit(actions, root, flow, key, execute=args.execute)
    if code == 0:
        # Only where the commits are actually on the branch. Telling someone to
        # open a PR for a carry that conflicted is telling them to ship nothing.
        print(f"\nthen open a second PR: {carry_branch} -> {target.branch}")
    return code


def _emit(actions: list, root: Path, flow: flow_lib.Flow, key: str, *, execute: bool) -> int:
    """Print the series, or run it. Printing stays the default and the fallback.

    A refused or failed run prints what is left, because the user's own shell is
    always the way out -- that is what makes refusing cheap enough to do often.
    """
    if not execute:
        print()
        for action in actions:
            print(f"  {action.rendered}")
        return 0

    run = gitrun.apply(actions, root, protected=flow.protected)
    print()
    print(gitrun.render(run))
    gitrun.record(run, key, root)
    if run.ok:
        return 0

    # From the step that failed, not after it. The failed step did not happen,
    # and handing over a cherry-pick whose branch was never created applies the
    # commits onto whatever branch the user is standing on.
    remaining = actions[max(len(run.steps) - 1, 0):]
    if remaining:
        print("\nnot run -- these are yours to finish, in this order:")
        for action in remaining:
            print(f"  {action.rendered}")
    return 1


def _source_branch(root: Path, flow: flow_lib.Flow, key: str) -> str:
    """The working branch holding this ticket's commits."""
    current = gitctx.branch(root)
    if current and key.lower() in current.lower():
        return current
    for name in gitctx.remote_branches(root):
        short = name.split("/", 1)[-1]
        if key.lower() in short.lower() and short not in flow.protected:
            return short
    raise UsageError(
        f"cannot find a branch for {key}",
        fix=[
            f"switch to the branch holding {key}'s commits, or create it: wb flow start {key} --title ...",
        ],
    )


def _set(args: argparse.Namespace) -> int:
    root = _root()
    pattern = flow_lib.validate_pattern(args.branch_pattern) if args.branch_pattern else None

    config: dict = {
        "strategy": args.strategy,
        "source": args.source,
        "validation": args.validation,
        "protected": [args.source, *args.validation],
    }
    if pattern:
        config["branch_pattern"] = pattern

    path = root / contexts.REPO_CONFIG
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    data["flow"] = config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote flow to {path}")
    return _show(argparse.Namespace(action="show", json=False))
