"""The agent a headless ``wb run`` drives, behind one interface.

``wb run`` needs exactly one thing from an agent: run one session on a prompt
in a checkout, within a time limit, and say what it cost and how it ended.
That is the whole seam. Claude Code is the first and only implementation; a
second agent is its own ticket, written against this interface when someone
needs it -- designing a plugin system for adapters nobody asked for would be
the expensive part of this module and the unused one.

This module starts a process, so it is one of the few that may (see
``tests/test_structure.py``): with no shell, an empty stdin and a timeout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_TIMEOUT_S = 1800


@dataclass
class Session:
    """How one agent session ended."""

    exit_code: int
    timed_out: bool = False
    # The agent's own report, as it gave it: turns, usage, cost, its session id.
    report: dict = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and not self.report.get("is_error")

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "error": self.error,
            "session_id": self.report.get("session_id"),
            "num_turns": self.report.get("num_turns"),
            "usage": self.report.get("usage"),
            "total_cost_usd": self.report.get("total_cost_usd"),
        }


class Adapter(Protocol):
    name: str

    def missing(self) -> str | None:
        """Why this agent cannot run here, or ``None``."""
        ...

    def session(self, prompt: str, root: Path, timeout_s: int) -> Session:
        """Run one session to its end, or to the timeout."""
        ...


@dataclass
class ClaudeCode:
    """``claude -p``: one non-interactive session, reported as JSON."""

    model: str = ""
    permission_mode: str = "acceptEdits"
    plugin_dir: str = ""
    name: str = "claude-code"

    def missing(self) -> str | None:
        return None if shutil.which("claude") else "claude is not on PATH"

    def argv(self, prompt: str) -> list[str]:
        claude = shutil.which("claude") or "claude"
        argv = [claude, "-p", prompt, "--output-format", "json", "--permission-mode", self.permission_mode]
        if self.model:
            argv += ["--model", self.model]
        if self.plugin_dir:
            argv += ["--plugin-dir", self.plugin_dir]
        return argv

    def session(self, prompt: str, root: Path, timeout_s: int) -> Session:
        try:
            done = subprocess.run(
                self.argv(prompt),
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Session(exit_code=124, timed_out=True, error=f"timed out after {timeout_s}s")
        except OSError as exc:
            return Session(exit_code=126, error=f"could not start claude: {exc}")
        report = parse(done.stdout)
        error = "" if done.returncode == 0 else (done.stderr or "").strip()[-300:]
        return Session(exit_code=done.returncode, report=report, error=error)


def parse(stdout: str) -> dict:
    """The JSON result ``claude -p --output-format json`` prints, or ``{}``."""
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}
