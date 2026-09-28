"""Each agent's hook protocol, around the one decision in ``workbench.hooks``.

Claude Code, Codex, Gemini CLI and Cursor all fire a hook before a tool runs
and when the agent stops, and all read the answer as JSON on stdout. What
differs is small and mechanical, and it is all here:

- which tool writes a file, and where its input names the path;
- how a refusal is spelled;
- how a stop is held open, and what stops that from looping.

The decisions themselves -- which path the audited plan covers, whether the
verification still describes the code -- are made once, in ``hooks``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import hooks

DEFAULT = "claude-code"

# Codex edits through apply_patch; the patch text names every path it touches.
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class HookAgent:
    name: str
    # The paths one tool call writes, or ``None`` when it writes no file.
    paths: Callable[[dict], list | None]
    deny: Callable[[str], dict]
    # The answer to a stop: (message, strict, payload) -> JSON, or ``None``.
    hold: Callable[[str, bool, dict], dict | None]


def pre_tool_use(agent: HookAgent, payload: dict, root: Path) -> dict | None:
    if agent.name == DEFAULT:
        return hooks.pre_tool_use(payload, root)
    if hooks.mode(root) == hooks.OFF:
        return None
    paths = agent.paths(payload)
    if not paths:
        return None
    reason = hooks.guard_edit(paths, root)
    return None if reason is None else agent.deny(reason)


def stop(agent: HookAgent, payload: dict, root: Path) -> dict | None:
    if agent.name == DEFAULT:
        return hooks.stop(payload, root)
    found = hooks.check_stop(root)
    if found is None:
        return None
    message, strict = found
    return agent.hold(message, strict, payload)


def root_hint(payload: dict) -> str:
    """The directory the agent is working in, as its payload names it."""
    if payload.get("cwd"):
        return str(payload["cwd"])
    roots = payload.get("workspace_roots")
    if isinstance(roots, list) and roots and isinstance(roots[0], str):
        return roots[0]
    return ""


def _input(payload: dict) -> dict:
    tool_input = payload.get("tool_input")
    return tool_input if isinstance(tool_input, dict) else {}


def _named(tools: dict[str, tuple[str, ...]]) -> Callable[[dict], list | None]:
    """Paths from a tool whose input carries the path in one of ``fields``."""

    def paths(payload: dict) -> list | None:
        fields = tools.get(str(payload.get("tool_name", "")))
        if not fields:
            return None
        tool_input = _input(payload)
        for field in fields:
            if tool_input.get(field):
                return [str(tool_input[field])]
        return None

    return paths


def patch_paths(patch: str) -> list[str]:
    """Every path an apply_patch body adds, updates, deletes or moves to."""
    return [match.strip() for match in _PATCH_PATH.findall(patch or "")]


def _codex_paths(payload: dict) -> list | None:
    if str(payload.get("tool_name", "")) == "apply_patch":
        tool_input = _input(payload)
        body = tool_input.get("command") or tool_input.get("input") or ""
        if isinstance(body, list):
            body = "\n".join(str(part) for part in body)
        # Patch paths are relative to the session's directory, which need not be
        # the checkout root the guard resolves a bare relative path against.
        here = Path(str(payload.get("cwd") or "."))
        return [str(path if Path(path).is_absolute() else here / path) for path in patch_paths(str(body))] or None
    return _named({"Edit": ("file_path",), "Write": ("file_path",)})(payload)


def _claude_deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _claude_hold(message: str, strict: bool, payload: dict) -> dict:
    # stop_hook_active: this stop already follows a block, and blocking again would loop.
    if strict and not payload.get("stop_hook_active"):
        return {"decision": "block", "reason": message}
    return {"systemMessage": message}


def _gemini_hold(message: str, strict: bool, payload: dict) -> dict:
    # AfterAgent: deny rejects the response and retries it, once.
    if strict and not payload.get("stop_hook_active"):
        return {"decision": "deny", "reason": message}
    return {"systemMessage": message}


def _cursor_hold(message: str, strict: bool, payload: dict) -> dict | None:
    # Cursor's stop cannot block, only submit a follow-up; loop_count keeps it to one.
    if strict and payload.get("loop_count", 0) == 0:
        return {"followup_message": message}
    return None


AGENTS: dict[str, HookAgent] = {
    agent.name: agent
    for agent in (
        HookAgent(DEFAULT, _named({tool: (field,) for tool, field in hooks.PATH_FIELDS.items()}), _claude_deny, _claude_hold),
        HookAgent("codex", _codex_paths, _claude_deny, _claude_hold),
        HookAgent(
            "gemini",
            _named({"write_file": ("file_path",), "replace": ("file_path",)}),
            lambda reason: {"decision": "deny", "reason": reason},
            _gemini_hold,
        ),
        HookAgent(
            "cursor",
            _named({"Write": ("file_path", "path")}),
            lambda reason: {"permission": "deny", "user_message": reason, "agent_message": reason},
            _cursor_hold,
        ),
    )
}
