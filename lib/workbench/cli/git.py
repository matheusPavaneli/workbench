"""``wb git`` -- read-only repository facts, including the right author.

The commit identity comes from the resolved context, which is the point of
having contexts: work commits carry the work address, personal ones do not,
without anyone remembering to switch.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import artifacts, contexts, flow as flow_lib, gitctx, gitrun
from ..errors import NotFoundError, UsageError

ACTIONS = ["ctx", "diff", "commit", "push"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("git", help="repository facts and commit identity")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    actions.add_parser("ctx", help="branch, remote, and the author this context expects")

    diff = actions.add_parser("diff", help="changed files and a diffstat")
    diff.add_argument("--staged", action="store_true")

    commit = actions.add_parser("commit", help="commit the staged diff using the message written for this key")
    commit.add_argument("key")
    commit.add_argument("--execute", action="store_true", help="run it instead of printing it")

    push = actions.add_parser("push", help="publish this branch for the first time")
    push.add_argument("--execute", action="store_true", help="run it instead of printing it")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb git needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"ctx": _ctx, "diff": _diff, "commit": _commit, "push": _push}[args.action](args)


def _ctx(_: argparse.Namespace) -> int:
    cwd = Path.cwd()
    root = gitctx.repo_root(cwd)
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])

    remote = gitctx.origin(cwd)
    print(f"root      {root}")
    print(f"branch    {gitctx.branch(cwd) or '?'}")
    print(f"remote    {remote.host + '/' + remote.org if remote else '(none)'}")

    local = gitctx.identity(cwd)
    print(f"git user  {local.get('name') or '?'} <{local.get('email') or '?'}>")

    try:
        context = contexts.resolve(cwd).context
    except Exception:  # noqa: BLE001 - identity is useful even with no context
        return 0

    wanted = context.git
    if wanted.get("email") and wanted["email"] != local.get("email"):
        print(f"\nmismatch  context {context.name!r} expects {wanted.get('name', '?')} <{wanted['email']}>")
        print("          commits from this checkout would carry the wrong address")
    return 0


def _root() -> Path:
    root = gitctx.repo_root(Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])
    return root


def _protected(root: Path) -> list[str]:
    """The flow's protected branches, resolved the same way ``wb flow`` does."""
    return flow_lib.protected(root)


def _emit(actions: list, root: Path, key: str | None, *, execute: bool) -> int:
    if not execute:
        for action in actions:
            print(f"  {action.rendered}")
        print("\nrun it here with --execute, or paste it yourself")
        return 0

    run_result = gitrun.apply(actions, root, protected=_protected(root))
    print(gitrun.render(run_result))
    if key:
        gitrun.record(run_result, key, root)
    return 0 if run_result.ok else 1


def _commit(args: argparse.Namespace) -> int:
    """The one write that closes an open loop.

    ``wb commit check`` already writes a validated message to commit.txt and
    nothing consumed it, so the message was retyped -- which is exactly where a
    convention-checked message stops matching what lands.

    The author comes from the resolved context rather than from git config,
    which is the whole point of contexts: work commits carry the work address
    without anyone remembering to switch.
    """
    root = _root()
    key = artifacts.validate_key(args.key)
    message = artifacts.ticket_dir(key, root) / "commit.txt"
    if not message.is_file():
        raise NotFoundError(
            f"{message} does not exist",
            fix=[f"write one first: wb commit check {key}"],
        )

    if not gitctx.changed_files(root, staged=True):
        raise UsageError(
            "nothing is staged",
            fix=["stage the change first; choosing what goes in a commit stays yours"],
        )

    argv = ["commit", "-F", str(message)]
    author = _author()
    if author:
        argv += ["--author", author]

    return _emit(
        [gitrun.Action(argv, why=f"commit {key}", precondition=gitrun.NOT_PROTECTED)],
        root,
        key,
        execute=args.execute,
    )


def _author() -> str | None:
    try:
        git = contexts.resolve(Path.cwd()).context.git
    except Exception:  # noqa: BLE001 - no context just means git's own identity applies
        return None
    name, email = git.get("name"), git.get("email")
    return f"{name} <{email}>" if name and email else None


def _push(args: argparse.Namespace) -> int:
    """First publish only.

    Updating an already-published branch stays manual on purpose. Once a branch
    has an upstream, the recovery from a bad push is a force-push -- and a
    force-push should never be something this tool can reach.
    """
    root = _root()
    branch = gitctx.branch(root)
    if not branch:
        raise UsageError("no current branch", fix=["check out a branch first"])

    action = gitrun.Action(
        ["push", "-u", "origin", branch],
        why=f"publish {branch}",
        precondition=gitrun.NO_UPSTREAM,
    )
    return _emit([action], root, None, execute=args.execute)


def _diff(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    root = gitctx.repo_root(cwd)
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])

    files = gitctx.changed_files(root, staged=args.staged)
    if not files:
        print("no changes" + (" staged" if args.staged else ""))
        return 0

    for path in files:
        print(path)
    stat = gitctx.diff_stat(root, staged=args.staged).strip()
    if stat:
        print()
        print(stat.splitlines()[-1].strip())
    return 0
