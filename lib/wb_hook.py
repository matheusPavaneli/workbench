"""Entry point for the Claude Code hooks in hooks/hooks.json.

    python "${CLAUDE_PLUGIN_ROOT}/lib/wb_hook.py" <pre-tool-use|stop>

Thin on purpose: the decisions live in ``workbench.hooks``, where they are
tested. This reads the event from stdin, prints the answer as JSON (or nothing,
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


def main(argv: list[str], stdin, stdout) -> int:
    event = argv[0] if argv else ""
    try:
        if event not in EVENTS:
            raise ValueError(f"unknown hook event {event!r}; expected one of: {', '.join(EVENTS)}")
        raw = stdin.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError(f"hook payload larger than {MAX_PAYLOAD} bytes")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")

        from workbench import gitctx, hooks

        root = gitctx.checkout(Path(str(payload.get("cwd") or Path.cwd())))
        handler = hooks.pre_tool_use if event == "pre-tool-use" else hooks.stop
        answer = handler(payload, root)
    except Exception as exc:  # noqa: BLE001 -- reported below, never swallowed
        answer = {"systemMessage": f"workbench {event or 'hook'} did not run ({type(exc).__name__}: {exc}); edit allowed"}

    if answer:
        stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))
