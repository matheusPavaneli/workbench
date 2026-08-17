"""``wb flow`` -- where work starts, where it lands, and what to carry across.

Nothing here runs git. It computes the branch name, the base to start from, and
the exact commits to cherry-pick, then prints the commands for the user to run.
Creating branches and picking commits are theirs to approve.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import artifacts, contexts, flow as flow_lib, gitctx
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

    carry = actions.add_parser("carry", help="the commits to cherry-pick onto a validation target")
    carry.add_argument("key")
    carry.add_argument("--to", required=True, help="validation branch to carry onto")

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


def _flow(root: Path) -> flow_lib.Flow:
    """Repo config wins over context, context over detection."""
    config = _repo_flow(root)
    if config is None:
        try:
            config = contexts.resolve(root).context.flow
        except WbError:
            config = None
    return flow_lib.load(config, root)


def _repo_flow(root: Path) -> dict | None:
    path = root / contexts.REPO_CONFIG
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("flow") if isinstance(data, dict) else None


def _show(args: argparse.Namespace) -> int:
    root = _root()
    flow = _flow(root)

    if args.json:
        print(json.dumps(flow.to_dict(), indent=2))
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
    flow = _flow(root)
    key = artifacts.validate_key(args.key)

    base = flow.target(args.target).branch if args.target else flow.source.branch
    name = flow_lib.branch_name(flow, key, args.title, args.kind)

    if gitctx.branch_exists(root, name):
        print(f"branch {name} already exists")
        return 0

    print(f"branch  {name}")
    print(f"base    {base}")
    print(f"\n  git fetch origin && git switch -c {name} origin/{base}")
    return 0


def _carry(args: argparse.Namespace) -> int:
    root = _root()
    flow = _flow(root)
    key = artifacts.validate_key(args.key)
    target = flow.target(args.to)

    if target.role != "validation":
        raise UsageError(
            f"{target.branch!r} is the source branch, not a validation target",
            fix=["carry onto a validation branch; the source gets a PR, not a cherry-pick"],
        )

    source_branch = _source_branch(root, flow, key)
    commits = flow_lib.carry_plan(root, source_branch, flow.source.branch, target.branch)
    if not commits:
        print(f"nothing to carry: {source_branch} has no commits that {flow.source.branch} lacks")
        return 0

    carry_branch = f"{source_branch}-{target.branch}"
    print(f"from    {source_branch}  ({len(commits)} commit(s), oldest first)")
    for line in commits:
        print(f"  {line}")

    hashes = " ".join(line.split(" ", 1)[0] for line in commits)
    print(f"\n  git fetch origin && git switch -c {carry_branch} origin/{target.branch}")
    print(f"  git cherry-pick {hashes}")
    print(f"\nthen open a second PR: {carry_branch} -> {target.branch}")
    return 0


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
