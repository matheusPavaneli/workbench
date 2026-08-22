"""``wb status`` -- what a new session needs to know before it does anything.

One key: the pipeline for that ticket, and the one command that moves it on.
No key: every ticket with work in flight, one line each.

This is the command a session runs *first* after a ``/clear``. It reads only
artifacts already on disk -- no tracker call, no context needed -- so it works
in a repo whose credentials are not set up, and costs nothing but a few stats.
"""

from __future__ import annotations

import argparse
import json

from .. import events, status as status_lib
from ..errors import UsageError

ACTIONS: list[str] = []


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("status", help="where a ticket stands, and what to run next")
    parser.add_argument("key", nargs="?", help="a ticket key; omit to list everything in flight")
    parser.add_argument("--json", action="store_true", help="machine-readable, for a skill rather than a person")
    parser.add_argument("--stats", action="store_true", help="aggregate across tickets: where work is piling up")
    parser.add_argument(
        "--global",
        dest="everywhere",
        action="store_true",
        help="with --stats, read the history of every checkout on this machine, not just this one",
    )


def run(args: argparse.Namespace) -> int:
    if args.everywhere and not args.stats:
        raise UsageError(
            "--global reads the command history, which only --stats reports",
            fix=["wb status --stats --global"],
        )
    if args.stats:
        if args.key:
            raise UsageError("--stats aggregates every ticket", fix=["drop the key, or drop --stats"])
        return _stats(args)
    if args.key:
        return _one(args)
    return _all(args)


def _stats(args: argparse.Namespace) -> int:
    """Two halves that answer different questions.

    The snapshot says where work is stuck now; the history says where this repo
    keeps losing time. A stage that always passes on the second attempt looks
    fine in the snapshot and is the most expensive thing in the log.
    """
    changed = status_lib.branch_changes()
    snapshot = status_lib.summarise([status_lib.read(key, changed=changed) for key in status_lib.keys()])
    history = events.summarise(events.read(everywhere=args.everywhere))

    if args.json:
        print(json.dumps({"snapshot": snapshot, "history": history}, indent=2))
        return 0

    print(status_lib.render_summary(snapshot))
    print()
    if args.everywhere:
        # Said plainly, because the snapshot above is still this checkout only:
        # one command printing two scopes has to say which is which.
        print(f"history across every checkout on this machine ({events.global_path()}):")
    print(events.render(history))
    return 0


def _one(args: argparse.Namespace) -> int:
    status = status_lib.read(args.key)
    if args.json:
        print(json.dumps(status.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(status_lib.render(status))
    return 0


def _all(args: argparse.Namespace) -> int:
    keys = status_lib.keys()
    if not keys and not args.json:
        print("no work in progress")
        print("start one: wb task list, or wb task get <KEY>")
        return 0

    changed = status_lib.branch_changes()
    items = [status_lib.read(key, changed=changed) for key in keys]
    if args.json:
        print(json.dumps([s.to_dict() for s in items], indent=2, ensure_ascii=False))
        return 0
    print(status_lib.render_list(items))
    _warn_unconfirmed_preset()
    return 0


def _warn_unconfirmed_preset() -> None:
    """The bar every plan in this list will be held to, if nobody has looked.

    Said here because this is the command a session runs first. A detected
    preset that nobody reviewed is not wrong, it is unexamined -- and the
    difference only shows up much later, in a plan that met the wrong bar.
    """
    from pathlib import Path

    from .. import gitctx, profile as profile_lib

    try:
        root = gitctx.repo_root(Path.cwd()) or Path.cwd()
        resolved = profile_lib.resolve(root)
    except Exception:  # noqa: BLE001 - a status listing must never fail on this
        return
    if resolved.needs_confirmation:
        print(f"\npreset {resolved.preset} is detected and unconfirmed ({resolved.confidence} confidence)")
        print("  wb repo profile            # the evidence, and the alternatives it supports")
        print("  wb repo profile --confirm")
