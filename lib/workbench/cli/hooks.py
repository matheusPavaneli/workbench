"""``wb hooks`` -- put the scope guard into an agent that has no plugin for it.

Claude Code and Codex load workbench's hooks from the plugin itself
(hooks/hooks.json, hooks/codex.json). Gemini CLI and Cursor read hooks from the
project instead, so this proposes the entries for that agent's project config
and, with ``--write``, merges them in.

It proposes and you dispose, like ``wb init``: nothing is written without
``--write``, every key it did not write is kept, and a second run replaces its
own entries rather than adding more. The command names this checkout's
``wb_hook.py`` by absolute path, so moving the checkout means running it again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import gitctx
from ..errors import ConfigError, UsageError

ACTIONS = ["install"]
ENTRY = Path(__file__).resolve().parents[2] / "wb_hook.py"
MARK = "wb_hook.py"

# Where each agent reads project hooks, and the entries workbench adds there.
# Gemini counts timeouts in milliseconds, Cursor in seconds.
TARGETS = {
    "gemini": Path(".gemini") / "settings.json",
    "cursor": Path(".cursor") / "hooks.json",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("hooks", help="install the scope guard into Gemini CLI or Cursor for this repo")
    actions = parser.add_subparsers(dest="action", metavar="{" + ",".join(ACTIONS) + "}")
    install = actions.add_parser("install", help="propose this repo's hook config for an agent; --write merges it in")
    install.add_argument("agent", choices=sorted(TARGETS))
    install.add_argument("--write", action="store_true", help="write the config instead of only proposing it")


def run(args: argparse.Namespace) -> int:
    if not args.action:
        raise UsageError("wb hooks needs an action", fix=[f"actions: {', '.join(ACTIONS)}"])
    return _install(args)


def command(agent: str, event: str, windows: bool = os.name == "nt") -> str:
    line = f'"{Path(sys.executable).as_posix()}" "{ENTRY.as_posix()}" --agent {agent} {event}'
    # Gemini runs hooks through PowerShell on Windows, where a quoted program
    # is a string, not a command, until the call operator runs it.
    return f"& {line}" if agent == "gemini" and windows else line


def entries(agent: str) -> dict[str, list[dict]]:
    """The hook entries workbench adds for ``agent``, keyed by event."""
    if agent == "gemini":
        return {
            "BeforeTool": [{"matcher": "write_file|replace", "hooks": [
                {"type": "command", "command": command(agent, "pre-tool-use"), "timeout": 15000}]}],
            "AfterAgent": [{"hooks": [{"type": "command", "command": command(agent, "stop"), "timeout": 60000}]}],
        }
    return {
        "preToolUse": [{"command": command(agent, "pre-tool-use"), "matcher": "Write", "timeout": 15}],
        "stop": [{"command": command(agent, "stop"), "timeout": 60}],
    }


def merge(existing: dict, agent: str) -> dict:
    """``existing`` with workbench's entries for ``agent`` in place of any older ones."""
    merged = json.loads(json.dumps(existing))
    if agent == "cursor":
        merged.setdefault("version", 1)
    hooks = merged.get("hooks")
    if hooks is None:
        hooks = merged["hooks"] = {}
    if not isinstance(hooks, dict):
        raise ConfigError(f"'hooks' in the {agent} config is not an object; workbench will not guess how to merge it")
    for event, ours in entries(agent).items():
        current = hooks.get(event) or []
        if not isinstance(current, list):
            raise ConfigError(f"'hooks.{event}' in the {agent} config is not a list; workbench will not guess how to merge it")
        hooks[event] = [entry for entry in current if not _ours(entry)] + ours
    return merged


def _ours(entry: object) -> bool:
    """An entry an earlier ``wb hooks install`` wrote: it runs wb_hook.py."""
    return MARK in json.dumps(entry)


def _install(args: argparse.Namespace) -> int:
    root = gitctx.checkout()
    path = root / TARGETS[args.agent]
    existing: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"{gitctx.shown(path, root)} is not valid JSON; fix it before merging hooks into it") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{gitctx.shown(path, root)} is not a JSON object")
        existing = loaded
    merged = merge(existing, args.agent)

    shown = gitctx.shown(path, root)
    if merged == existing:
        print(f"up to date  {shown}: workbench's {args.agent} hooks are already there")
        _skills_note()
        return 0

    print(f"{'writing' if args.write else 'proposed'}  {shown}")
    print()
    for line in json.dumps({"hooks": entries(args.agent)}, indent=2).splitlines():
        print(f"  {line}")
    print()
    if not args.write:
        print(f"nothing written. To apply it:  wb hooks install {args.agent} --write")
        _skills_note()
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {shown}; every other key in it is as it was")
    _skills_note()
    return 0


def _skills_note() -> None:
    skills = (ENTRY.parent.parent / "skills").as_posix()
    bin_dir = (ENTRY.parent.parent / "bin").as_posix()
    print(f"skills: copy or link {skills}/* into .agents/skills/ (this repo) or ~/.agents/skills/ (every repo)")
    print(f"wb:     put {bin_dir} on PATH so the skills can call it")
