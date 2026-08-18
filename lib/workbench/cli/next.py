"""``wb next`` -- the one command to run, and nothing else.

``wb status`` answers "where does this stand", which is eight lines because a
person resuming work wants the shape of it. A session that has already read the
shape, or that only needs the next move, pays for those eight lines to use one.

So this is deliberately not a shorter status: it resolves *which* ticket
without being told -- from the branch, then from what was touched last -- and
prints a single command. That resolution is the part a skill cannot do for
itself, and doing it in code means two sessions on the same checkout pick the
same work.
"""

from __future__ import annotations

import argparse
import json

from .. import contract, status as status_lib
from ..errors import NotFoundError

ACTIONS: list[str] = []


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("next", help="the single command to run now")
    parser.add_argument("key", nargs="?", help="a ticket key; omit to resolve from the branch")
    parser.add_argument("--json", action="store_true", help="machine-readable, for a skill rather than a person")


def run(args: argparse.Namespace) -> int:
    picked = status_lib.pick(args.key)
    if picked is None:
        raise NotFoundError(
            "no work in progress in this checkout",
            fix=["wb task list", "wb task get <KEY>"],
        )

    status, origin = picked
    if args.json:
        print(contract.emit("next", status_lib.next_dict(status, origin)))
        return 0
    print(status_lib.render_next(status, origin))
    return 0
