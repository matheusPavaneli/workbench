"""``wb run`` -- drive a ticket through the flow headless, stopping only for a person.

Every step of the flow is already a command or a skill, and ``wb next`` already
knows which one comes next. What was missing is something to keep going from
the shell: this starts agent sessions (``workbench.agent``) until the ticket
reaches its goal, and stops the moment a decision belongs to a person -- the
same decisions ``wb approve`` serves, printed with the token that approves them.

State lives where it always did, on the branch and under ``.workflow/<KEY>/``,
so a rerun resumes from wherever the last one stopped: after an approval, a
timeout or a crash alike. Each session is recorded in ``run.json`` with what it
cost.

Off by default. An autopilot over an expensive flow spends faster, and a
headless session is where a stop nobody sees would hide; turning it on is a
repo decision (``"run": {"enabled": true}``) or a one-off (``WB_RUN=1``).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .. import (
    agent,
    approve as approve_lib,
    artifacts,
    flow as flow_lib,
    gitctx,
    profile,
    status as status_lib,
)
from ..errors import ConfigError, UsageError, WbError
from . import start as start_cli

ACTIONS: list[str] = []

# A ticket waiting on a person: not a failure, and not done.
EXIT_WAITING = 8
MAX_SESSIONS = 4
RECORD = "run.json"
# The CLI this run is part of, so each session calls the same one.
WB = (Path(__file__).resolve().parents[2] / "wb.py").as_posix()
GOALS = ("commit", "pr")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "run", help="drive a ticket headless to a PR draft, stopping at every decision a person owns (off by default)"
    )
    parser.add_argument("key", help="the ticket to drive")
    parser.add_argument("--until", choices=GOALS, default="pr", help="stop once committed, or once the PR is drafted")
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS, metavar="N")
    parser.add_argument("--timeout", type=int, default=agent.DEFAULT_TIMEOUT_S, metavar="S", help="seconds per session")
    parser.add_argument(
        "--agent", default="", choices=["", *agent.AGENTS], metavar="NAME",
        help=f"the agent to drive: {', '.join(agent.AGENTS)}; run.agent in the repo config, else {agent.DEFAULT}",
    )
    parser.add_argument("--model", default="", help="the agent's model; its own default when omitted")
    parser.add_argument(
        "--permission-mode", default="",
        help="Claude Code only: run.permission_mode in the repo config, else acceptEdits",
    )
    parser.add_argument("--plugin-dir", default="", help="load workbench from this directory in each session")
    parser.add_argument(
        "--setting-sources", default="", help="which of the agent's setting sources to load (user, project, local)"
    )


def enabled(root: Path) -> bool:
    if os.environ.get("WB_RUN", "").strip() not in ("", "0", "false", "no"):
        return True
    setting = profile.repo_config(root).get("run")
    return setting is True or (isinstance(setting, dict) and setting.get("enabled") is True)


def run(args: argparse.Namespace) -> int:
    root = gitctx.require_checkout()
    key = artifacts.validate_key(args.key)
    if not enabled(root):
        raise UsageError(
            "wb run is off in this repo",
            fix=['turn it on: "run": {"enabled": true} in .workflow/config.json', "or for one call: WB_RUN=1"],
        )
    if args.max_sessions < 1:
        raise UsageError("--max-sessions must be at least 1")

    name = args.agent or str(_config(root).get("agent") or agent.DEFAULT)
    try:
        adapter = agent.select(
            name,
            model=args.model,
            permission_mode=args.permission_mode or _config(root).get("permission_mode") or "acceptEdits",
            plugin_dir=args.plugin_dir,
            setting_sources=args.setting_sources,
        )
    except ValueError as exc:
        raise ConfigError(f"wb run: {exc}", fix=[f'set "run": {{"agent": "{agent.DEFAULT}"}} in .workflow/config.json']) from exc
    missing = adapter.missing()
    if missing:
        raise ConfigError(f"wb run cannot start an agent: {missing}", fix=[adapter.install])

    start_cli.run(argparse.Namespace(key=key, refresh=False))
    record = _load(key, root)

    for number in range(1, args.max_sessions + 1):
        stopped = _stop(key, root, args.until)
        if stopped is not None:
            return stopped

        before, code_before = _where(key, root), _code(root)
        began = time.monotonic()
        session = adapter.session(_prompt(key, root, args.until), root, args.timeout)
        entry = {**session.to_dict(), "agent": adapter.name, "wall_s": round(time.monotonic() - began, 1),
                 "before": before, "after": _where(key, root)}
        record["sessions"].append(entry)
        artifacts.write_json(key, RECORD, record, root)
        print(f"session {number}: {_cost(entry)}; now at {entry['after']}", flush=True)

        if not session.ok:
            how = "timed out" if session.timed_out else "failed"
            raise WbError(
                f"{key}: the {adapter.name} session {how} ({session.error or f'exit {session.exit_code}'}). "
                f"The branch {gitctx.branch(root) or '?'} and .workflow/{key}/ are as it left them, "
                "so nothing is lost",
                fix=[f"resume: wb run {key}", f"or by hand: {_next(key, root)}"],
            )
        moved = entry["after"] != before or _code(root) != code_before
        if not moved and _stop(key, root, args.until, quiet=True) is None:
            raise WbError(
                f"{key}: a whole session made no progress at {before}; stopping rather than paying for another",
                fix=[f"run the step yourself: {_next(key, root)}", f"then: wb run {key}"],
            )

    stopped = _stop(key, root, args.until)
    if stopped is not None:
        return stopped
    raise WbError(
        f"{key}: not at its goal after {args.max_sessions} session(s); everything so far is on the branch",
        fix=[f"continue: wb run {key}", f"or by hand: {_next(key, root)}"],
    )


def _stop(key: str, root: Path, until: str, *, quiet: bool = False) -> int | None:
    """0 at the goal, EXIT_WAITING at a person's decision, ``None`` to keep going."""
    if _done(key, root, until):
        if not quiet:
            goal = "committed" if until == "commit" else f"PR drafted in .workflow/{key}/pr.md"
            print(f"{key}  done: {goal}")
        return 0
    decision = approve_lib.pending(key, root)
    if decision is None or (decision.kind == approve_lib.PUBLISH and until == "commit"):
        return None
    if not quiet:
        print(f"{key}  waiting on you: {decision.kind}: {decision.title}")
        for item in decision.items:
            print(f"  {item}")
        print(decision.effect)
        print(f"approve: wb approve {key} {decision.token}")
        print(f"then resume: wb run {key}")
    return EXIT_WAITING


def _done(key: str, root: Path, until: str) -> bool:
    if until == "pr":
        return (artifacts.ticket_dir(key, root) / "pr.md").is_file()
    base = flow_lib.carry_base(root, flow_lib.resolve(root).source.branch)
    committed = bool(gitctx.subjects_since(root, base))
    pending = [path for path in gitctx.changed_files(root) if not path.startswith(artifacts.WORKFLOW_DIR + "/")]
    return committed and not pending


def _where(key: str, root: Path) -> str:
    picked = status_lib.pick(key, root)
    if picked is None:
        return "not started"
    stage = picked[0].blocked or picked[0].next_stage
    return f"{stage.name} {stage.state}" if stage else "complete"


def _code(root: Path) -> tuple[str | None, str | None]:
    """What the code is: the commit and the tree on disk. Work that has not
    moved a stage yet still moves this."""
    return gitctx.head(root), gitctx.tree(root)


def _next(key: str, root: Path) -> str:
    picked = status_lib.pick(key, root)
    return (picked[0].next_command if picked else "") or f"wb next {key}"


def _prompt(key: str, root: Path, until: str) -> str:
    goal = "to a commit" if until == "commit" else f"to a drafted PR description in .workflow/{key}/pr.md"
    picked = status_lib.pick(key, root)
    where = status_lib.render_next(*picked).strip() if picked else ""
    return (
        f"Pick up ticket {key} and take it through the workbench flow {goal}.\n\n"
        f"Where it stands now:\n{where}\n\n"
        f'`wb` is not on PATH here; run it as: python "{WB}" <group> <action> ...\n\n'
        "Do not push and do not open a PR. Never approve anything on the user's behalf: "
        "when wb names `wb approve`, stop there and say which decision is waiting."
    )


def _config(root: Path) -> dict:
    setting = profile.repo_config(root).get("run")
    return setting if isinstance(setting, dict) else {}


def _load(key: str, root: Path) -> dict:
    path = artifacts.ticket_dir(key, root) / RECORD
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        data = {"schema": 1, "key": key, "sessions": []}
    return data


def _cost(entry: dict) -> str:
    parts = []
    if isinstance(entry.get("num_turns"), int):
        parts.append(f"{entry['num_turns']} turns")
    if isinstance(entry.get("total_cost_usd"), (int, float)):
        parts.append(f"US${entry['total_cost_usd']:.2f}")
    parts.append(f"{entry['wall_s']} s")
    return ", ".join(parts)
