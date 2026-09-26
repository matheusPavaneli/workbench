"""``wb cite check`` -- the citation audit, for artifacts that are not plans.

Review findings, incident chains, review replies and answers about the code all
quote the lines they rest on. This reopens each one, exactly as ``wb sdd
audit`` does for a plan, and fails on any that the code does not support.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import cite as cite_lib, events, gitctx
from ..errors import EXIT_AUDIT, UsageError

ACTIONS = ["check"]
MAX_BYTES = 1024 * 1024


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("cite", help="verify the file:line citations in a written artifact")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")

    check = actions.add_parser("check", help="reopen every quoted `path:line` citation in a markdown file")
    check.add_argument("file", help="the artifact to check, inside this checkout")
    check.add_argument(
        "--worktree",
        action="store_true",
        help="read the files on disk rather than at HEAD, for a review of an uncommitted diff",
    )


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb cite needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return _check(args)


def _check(args: argparse.Namespace) -> int:
    root = gitctx.checkout()
    path = Path(args.file)
    path = (path if path.is_absolute() else Path.cwd() / path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise UsageError(f"{args.file} is outside this checkout", fix=["check an artifact inside the repo"]) from None
    if not path.is_file():
        raise UsageError(f"no such file: {args.file}")
    if path.stat().st_size > MAX_BYTES:
        raise UsageError(f"{args.file} is larger than {MAX_BYTES} bytes")

    results = cite_lib.check(path.read_text(encoding="utf-8", errors="replace"), root, worktree=args.worktree)
    events.note_verdicts(result.verdict for result in results)
    against = "the working tree" if args.worktree else "HEAD"
    if not results:
        print(f"no citations found in {args.file}")
        return 0

    failed = [result for result in results if not result.ok]
    if not failed:
        print(f"pass  {len(results)} citation(s) verified against {against}")
        return 0

    print(f"FAIL  {len(failed)}/{len(results)} citation(s) unverified against {against}", file=sys.stderr)
    for result in failed:
        reference = result.reference
        print(
            f"  line {reference.at:<4} {result.verdict:<12} {reference.path}:{reference.line}  {result.detail}",
            file=sys.stderr,
        )
    print("\nfix the artifact, not the check: quote the line as it is, or drop the claim.", file=sys.stderr)
    return EXIT_AUDIT
