"""``wb git`` -- read-only repository facts, including the right author.

The commit identity comes from the resolved context, which is the point of
having contexts: work commits carry the work address, personal ones do not,
without anyone remembering to switch.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import contexts, gitctx
from ..errors import UsageError

ACTIONS = ["ctx", "diff"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("git", help="repository facts and commit identity")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    actions.add_parser("ctx", help="branch, remote, and the author this context expects")

    diff = actions.add_parser("diff", help="changed files and a diffstat")
    diff.add_argument("--staged", action="store_true")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb git needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"ctx": _ctx, "diff": _diff}[args.action](args)


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
