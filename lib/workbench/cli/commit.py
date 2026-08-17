"""``wb commit`` -- the repo's message convention, and a check against it.

Nothing here commits. Writing the message is the model's job, running git is the
user's; this reports what the house style is and whether a draft breaks it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import artifacts, commitmsg, contexts, gitctx
from ..errors import UsageError, WbError

ACTIONS = ["convention", "check"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("commit", help="commit message convention and validation")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    convention = actions.add_parser("convention", help="the style this repo already uses, or declare one")
    convention.add_argument("--json", action="store_true")
    convention.add_argument(
        "--set",
        dest="style",
        choices=["conventional", "ticket-prefixed", "free-form"],
        help="declare the style instead of detecting it -- for a repo adopting one it does not have yet",
    )

    check = actions.add_parser("check", help="validate a drafted message against that style")
    check.add_argument("--file", required=True, help="path to the drafted message")
    check.add_argument("--key", help="ticket key the change belongs to")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb commit needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return {"convention": _convention, "check": _check}[args.action](args)


def _repo_root() -> Path:
    root = gitctx.repo_root(Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=["run this inside a checkout"])
    return root


def _convention(args: argparse.Namespace) -> int:
    root = _repo_root()

    if args.style:
        commitmsg.declare(root, args.style)
        print(f"declared {args.style} for this repo")

    convention = commitmsg.resolve(root, gitctx.recent_subjects(root))

    if args.json:
        print(json.dumps(convention.to_dict(), indent=2))
        return 0

    print(f"style     {convention.describe()}")
    for example in convention.examples:
        print(f"  e.g.    {example}")

    local = gitctx.identity(root)
    print(f"author    {local.get('name') or '?'} <{local.get('email') or '?'}>")
    try:
        wanted = contexts.resolve(root).context.git
    except WbError:
        return 0
    if wanted.get("email") and wanted["email"] != local.get("email"):
        print(f"mismatch  this context expects {wanted.get('name', '?')} <{wanted['email']}>")
    return 0


def _check(args: argparse.Namespace) -> int:
    root = _repo_root()
    path = Path(args.file)
    if not path.is_file():
        raise UsageError(f"no such file: {args.file}", fix=["write the draft message to a file first"])

    message = path.read_text(encoding="utf-8")
    convention = commitmsg.resolve(root, gitctx.recent_subjects(root))
    problems = commitmsg.check(message, convention, key=args.key)

    if not problems:
        if args.key:
            # Only an accepted draft is stored: a rejected one would be picked up
            # by draft-pr as if it had passed.
            artifacts.write_text(artifacts.validate_key(args.key), "commit.txt", message)
        print(f"ok  {commitmsg.summary(message.splitlines()[0])}")
        return 0

    print("message needs work:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 2
