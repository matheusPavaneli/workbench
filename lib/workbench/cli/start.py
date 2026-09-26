"""``wb start`` -- pick up a ticket in one command.

Picking up work was three commands for one intent: ``wb task get``, ``wb flow
start`` and ``wb route``. Each is still there for the step it names; this runs
them in order and ends where ``wb next`` would, with the one command to run now.

Rerunning it is safe. A ticket already read is not fetched again, a branch that
exists is switched to rather than created, and a checkout already on the
ticket's branch changes nothing -- so the second run only says where it stands.

A repo with no origin remote still gets its branch, from the local source
branch: ``wb flow start`` refuses there because its printed commands branch
from ``origin/<base>``, but a branch named for the ticket is what ties the work,
the hooks and ``wb next`` to it, and nothing about that needs a remote.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import artifacts, contexts, flow as flow_lib, gitctx, gitrun, light, providers, status as status_lib
from ..errors import UsageError, WbError
from . import route as route_cli

ACTIONS: list[str] = []

# The ticket text printed with the start, so a session can begin without
# opening triage.json. The triage payload is already capped; this caps the echo.
DESC_SHOWN = 1200


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "start", help="pick up a ticket: read it, branch for it, print the route and the one next command"
    )
    parser.add_argument("key", help="the ticket to pick up")
    parser.add_argument("--refresh", action="store_true", help="read the ticket again even if triage.json exists")


def run(args: argparse.Namespace) -> int:
    root = gitctx.require_checkout()
    key = artifacts.validate_key(args.key)

    triage, fetched = _triage(key, root, refresh=args.refresh)
    where = _branch(key, triage, root)

    title = str(triage.get("title") or "")
    kind = str(triage.get("type") or "")
    print(f"{key}  {kind or 'task'}  {title}".rstrip())
    if fetched:
        desc = str(triage.get("desc") or "").strip()
        if desc:
            shown = desc if len(desc) <= DESC_SHOWN else desc[:DESC_SHOWN].rstrip() + " ..."
            print("\n" + "\n".join(f"  {line}" for line in shown.splitlines()) + "\n")
        print(f"triage  wrote {gitctx.shown(artifacts.ticket_dir(key, root) / 'triage.json', root)}")
    else:
        print("triage  already read (--refresh to read it again)")
    print(f"branch  {where}")

    _choose_path(key, kind, root)
    tier, reason, steps = route_cli.compute(key, root)
    print(f"route   {tier}: {', '.join(name for name, _, _ in steps)}  ({reason})")
    if light.marker(key, root) is not None:
        print("        no plan, no other skill: make the change and a test that covers it, then")
        print(f'        wb finish {key} -m "<type>: <summary>" --commit -- it runs the suite and every check,')
        print("        commits exactly the checked files, and speaks only if something blocks")

    picked = status_lib.pick(key, root)
    if picked is not None:
        print(status_lib.render_next(*picked).rstrip())
    return 0


def _choose_path(key: str, kind: str, root: Path) -> None:
    """Put eligible work on the light path, once, before anything is planned.

    A ticket that already has a plan keeps it: the path is chosen at the start,
    and a plan written by hand is never dropped from view.
    """
    if (artifacts.ticket_dir(key, root) / "sdd.json").is_file():
        light.unmark(key, root)
        return
    if light.marker(key, root) is not None:
        if not light.enabled(root):
            light.unmark(key, root)
        return
    allowed, _ = light.eligible(key, kind, root)
    if allowed:
        light.mark(key, root)


def _triage(key: str, root: Path, *, refresh: bool) -> tuple[dict, bool]:
    """The ticket, read once. True when this run fetched it."""
    path = artifacts.ticket_dir(key, root) / "triage.json"
    if not refresh:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict):
            return cached, False

    provider = providers.for_context(contexts.resolve().context)
    payload = provider.get_task(key, 1, [])
    artifacts.write_json(key, "triage.json", payload)
    return payload, True


def _branch(key: str, triage: dict, root: Path) -> str:
    """Put the checkout on the ticket's branch; say what that took."""
    current = gitctx.branch(root)
    if current and status_lib.key_from_branch([key], root) == key:
        return f"{current} (already on it)"

    flow = flow_lib.resolve(root)
    name = flow_lib.branch_name(flow, key, str(triage.get("title") or key), str(triage.get("type") or "feature"))
    base = flow.source.branch

    if gitctx.branch_exists(root, name):
        actions = [gitrun.Action(["switch", name], why=f"back to {name}", precondition=gitrun.CLEAN_TREE)]
        outcome = f"{name} (switched to it)"
    elif gitctx.has_origin(root):
        actions = flow_lib.start_actions(name, base)
        outcome = f"{name} (created from origin/{base})"
    else:
        start = [base] if gitctx.branch_exists(root, base) else []
        actions = [
            gitrun.Action(
                ["switch", "-c", name, *start],
                why=f"start {name} from {base if start else 'the current commit'}; there is no origin remote",
                precondition=gitrun.CLEAN_TREE,
            )
        ]
        outcome = f"{name} (created from {base if start else 'the current commit'}, no origin remote)"

    disabled = gitrun.disabled_reason(root)
    if disabled:
        raise UsageError(
            f"{key} is read, but its branch was not created: git writes are off ({disabled})",
            fix=[*(action.rendered for action in actions), f"then: wb start {key}"],
        )

    result = gitrun.apply(actions, root, protected=flow.protected)
    gitrun.record(result, key, root)
    if not result.ok:
        failed = result.steps[-1] if result.steps else None
        why = result.stopped or (failed.refused or failed.output.strip() if failed else "") or "git failed"
        remaining = actions[max(len(result.steps) - 1, 0):]
        raise WbError(
            f"{key} is read, but its branch was not created: {why}",
            fix=[*(action.rendered for action in remaining), f"then: wb start {key}"],
        )
    return outcome
