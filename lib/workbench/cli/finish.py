"""``wb finish`` -- close light-path work: every guardrail, then the commit command.

The other half of ``wb start`` for small work. See ``workbench.light`` for what
is checked and why the checks sit here rather than in a plan.
"""

from __future__ import annotations

import argparse
import shlex

from .. import artifacts, flow as flow_lib, gitctx, light, status as status_lib
from ..errors import EXIT_AUDIT, UsageError, WbError

ACTIONS: list[str] = []


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "finish", help="check light-path work (message, size, tests, suite) and print the commit command"
    )
    parser.add_argument("key", help="the ticket wb start put on the light path")
    parser.add_argument("-m", "--message", required=True, help="the commit message, in the repo's convention")


def run(args: argparse.Namespace) -> int:
    root = gitctx.require_checkout()
    key = artifacts.validate_key(args.key)

    if light.marker(key, root) is None:
        raise UsageError(
            f"{key} is not on the light path",
            fix=[f"pick it up first: wb start {key}", f"or follow its route: wb next {key}"],
        )
    if not light.enabled(root):
        light.unmark(key, root)
        raise UsageError(
            f'the light path is off here ("{light.CONFIG_KEY}": false); {key} takes the standard route',
            fix=[f"(plan-change) then wb sdd audit {key}"],
        )

    status = status_lib.read(key, root)
    base = light.base(key, root, flow_lib.carry_base(root, flow_lib.resolve(root).source.branch))
    result = light.check(key, root, args.message, base, status.kind)

    if result.outgrew:
        light.unmark(key, root)
        raise WbError(
            f"{key} outgrew the light path: {result.outgrew}. It now takes the standard route; the change stays",
            code=EXIT_AUDIT,
            fix=[f"plan it: (plan-change) then wb sdd audit {key}"],
        )
    if not result.ok:
        raise WbError(f"{key}: " + "; ".join(result.blocked), code=EXIT_AUDIT, fix=result.fix)

    artifacts.write_text(key, "commit.txt", args.message.strip() + "\n", root)
    light.record(result, root)

    size = f"{len(result.changed)} file(s), {result.lines} line(s)"
    checks = ", ".join(
        part
        for part in (
            f"{len(result.ran)} suite run(s) passed" if result.ran else "",
            f"{len(result.regression)} regression test(s) proven" if result.regression else "",
        )
        if part
    )
    print(f"{key}  clear: {size}" + (f", {checks}" if checks else ""))
    if result.note:
        print(f"  note: {result.note}")
    message = gitctx.shown(artifacts.ticket_dir(key, root) / "commit.txt", root)
    # The files checked, named: committing exactly what was checked is the point.
    print(f"  commit: git add -- {shlex.join(result.changed)} && git commit -F {message}")
    return 0
