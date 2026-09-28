"""Entry point for every agent's hooks: hooks/hooks.json (Claude Code),
hooks/codex.json (Codex), and what ``wb hooks install`` writes for Gemini CLI
and Cursor.

    python "<plugin root>/lib/wb_hook.py" [--agent <name>] <pre-tool-use|stop>

With no ``--agent`` it speaks Claude Code's protocol, as it always has.

Thin on purpose: the decisions live in ``workbench.hooks`` and each agent's
protocol in ``workbench.hook_agents``, where they are tested. This reads the event from stdin, prints the answer as JSON (or nothing,
which lets the tool call through), and always exits 0.

Kept out of ``wb``: a hook fires on every edit, and routing it through the CLI
would write each one into the command history that ``wb status --stats`` reads.

An internal error allows the edit and says so in a ``systemMessage``. Failing
closed would block every edit in every session on one bug, and a hook like
that gets switched off -- after which it protects nothing. Failing silently
would hide that the guard was not running.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_PAYLOAD = 1024 * 1024
EVENTS = ("pre-tool-use", "stop")
# Agents that refuse a tool call whose hook answer does not match the event's schema.
SCHEMA_STRICT = ("cursor",)


def main(argv: list[str], stdin, stdout) -> int:
    name = ""
    if argv[:1] == ["--agent"]:
        name, argv = (argv[1] if len(argv) > 1 else ""), argv[2:]
    event = argv[0] if argv else ""
    try:
        from workbench import gitctx, hook_agents

        agent = hook_agents.AGENTS.get(name or hook_agents.DEFAULT)
        if agent is None:
            raise ValueError(f"unknown agent {name!r}; expected one of: {', '.join(hook_agents.AGENTS)}")
        if event not in EVENTS:
            raise ValueError(f"unknown hook event {event!r}; expected one of: {', '.join(EVENTS)}")
        raw = stdin.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError(f"hook payload larger than {MAX_PAYLOAD} bytes")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")

        root = gitctx.checkout(Path(hook_agents.root_hint(payload) or Path.cwd()))
        handler = hook_agents.pre_tool_use if event == "pre-tool-use" else hook_agents.stop
        answer = handler(agent, payload, root)
    except Exception as exc:  # noqa: BLE001 -- reported below, never swallowed
        message = f"workbench {event or 'hook'} did not run ({type(exc).__name__}: {exc}); edit allowed"
        if name in SCHEMA_STRICT:
            # A key outside the event's schema would refuse the edit here, so the
            # report goes to stderr instead.
            sys.stderr.write(message + "\n")
            answer = None
        else:
            answer = {"systemMessage": message}

    if answer:
        stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))
