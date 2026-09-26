"""``wb approve`` -- one verb for whatever human decision is pending.

Without a token it shows the decision and the token that approves exactly it.
With the token it recomputes the decision and carries it out only if nothing
changed since it was shown. See ``workbench.approve``.
"""

from __future__ import annotations

import argparse

from .. import approve as approve_lib, gitctx, status as status_lib
from ..errors import NotFoundError, UsageError

ACTIONS: list[str] = []


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("approve", help="show the decision waiting on you, or approve it by its token")
    parser.add_argument("key", nargs="?", help="a ticket key; omit to resolve it as wb next does")
    parser.add_argument("token", nargs="?", help="the token wb approve printed with the decision")


def run(args: argparse.Namespace) -> int:
    root = gitctx.require_checkout()
    picked = status_lib.pick(args.key, root)
    if picked is None:
        raise NotFoundError("no work in progress in this checkout", fix=["wb task list", "wb start <KEY>"])
    key = picked[0].key

    decision = approve_lib.pending(key, root)
    if decision is None:
        if args.token:
            raise UsageError(
                f"nothing is waiting on you for {key}, so there is nothing for that token to approve",
                fix=[f"wb next {key}"],
            )
        print(f"{key}  nothing waiting on you")
        print(status_lib.render_next(*picked).rstrip())
        return 0

    if args.token is None:
        print(f"{key}  {decision.kind}: {decision.title}")
        for item in decision.items:
            print(f"  {item}")
        print(decision.effect)
        if decision.otherwise:
            print(decision.otherwise)
        print(f"approve: wb approve {key} {decision.token}")
        return 0

    if args.token != decision.token:
        raise UsageError(
            f"what waits for {key} changed since that token was printed; nothing was approved",
            fix=[f"see it again: wb approve {key}"],
        )
    print(approve_lib.apply(decision, root))
    return 0
