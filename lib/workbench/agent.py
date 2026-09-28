"""The agent a headless ``wb run`` drives, behind one interface.

``wb run`` needs exactly one thing from an agent: run one session on a prompt
in a checkout, within a time limit, and say what it cost and how it ended.
That is the whole seam. Claude Code, Codex, Gemini CLI and Cursor implement
it, each as one non-interactive invocation of its own CLI. Every adapter
normalises its agent's report into the same keys -- ``session_id``,
``num_turns``, ``usage``, ``total_cost_usd``, ``is_error`` -- so ``run.json``
reads the same whichever agent wrote it, and a key the agent does not report
is simply absent.

This module starts a process, so it is one of the few that may (see
``tests/test_structure.py``): with no shell, an empty stdin and a timeout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

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
    install: str

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
    setting_sources: str = ""
    name: str = "claude-code"
    install: str = "install Claude Code: npm i -g @anthropic-ai/claude-code"

    def missing(self) -> str | None:
        return None if shutil.which("claude") else "claude is not on PATH"

    def argv(self, prompt: str) -> list[str]:
        claude = shutil.which("claude") or "claude"
        argv = [claude, "-p", prompt, "--output-format", "json", "--permission-mode", self.permission_mode]
        if self.model:
            argv += ["--model", self.model]
        if self.plugin_dir:
            argv += ["--plugin-dir", self.plugin_dir]
        if self.setting_sources:
            argv += ["--setting-sources", self.setting_sources]
        return argv

    def session(self, prompt: str, root: Path, timeout_s: int) -> Session:
        return _session("claude", self.argv(prompt), root, timeout_s, parse)


@dataclass
class Codex:
    """``codex exec --json``: one non-interactive session, reported as JSON lines."""

    model: str = ""
    name: str = "codex"
    install: str = "install Codex: npm i -g @openai/codex"

    def missing(self) -> str | None:
        return None if shutil.which("codex") else "codex is not on PATH"

    def argv(self) -> list[str]:
        """The prompt is read from stdin (``-``); see ``_session``."""
        codex = shutil.which("codex") or "codex"
        argv = [codex, "exec", "--json", "--sandbox", "workspace-write"]
        if self.model:
            argv += ["--model", self.model]
        return argv + ["-"]

    def session(self, prompt: str, root: Path, timeout_s: int) -> Session:
        return _session("codex", self.argv(), root, timeout_s, parse_codex, prompt=prompt)


@dataclass
class Gemini:
    """``gemini -p --output-format json``: one headless session, edits auto-approved.

    Headless Gemini refuses any tool that would ask for confirmation, and
    ``auto_edit`` leaves the shell asking -- so a session could edit files but
    never run ``wb``. A policy file allows exactly the ``wb`` invocation the
    prompt names, rather than ``yolo``, which would allow every command. Gemini
    also runs headless only in a folder it trusts, and loads project hooks only
    there; the session is told to trust the checkout ``wb run`` was started in.
    """

    model: str = ""
    # The ``python "<path>/wb.py"`` prefix the session prompt tells the agent to use.
    wb: str = ""
    name: str = "gemini"
    install: str = "install Gemini CLI: npm i -g @google/gemini-cli"

    def missing(self) -> str | None:
        return None if shutil.which("gemini") else "gemini is not on PATH"

    def argv(self, policy: Path | None = None) -> list[str]:
        """The prompt is read from stdin, and ``-p`` is appended to it; see ``_session``."""
        gemini = shutil.which("gemini") or "gemini"
        argv = [gemini, "-p", GEMINI_FOLLOW, "--output-format", "json", "--approval-mode", "auto_edit"]
        if policy is not None:
            argv += ["--policy", str(policy)]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def session(self, prompt: str, root: Path, timeout_s: int) -> Session:
        policy = None
        if self.wb:
            policy = root / ".workflow" / GEMINI_POLICY
            policy.parent.mkdir(parents=True, exist_ok=True)
            policy.write_text(gemini_policy(self.wb), encoding="utf-8")
        env = {**os.environ, "GEMINI_CLI_TRUST_WORKSPACE": "true"}
        return _session("gemini", self.argv(policy), root, timeout_s, parse_gemini, env, prompt=prompt)


GEMINI_POLICY = "gemini-policy.toml"
# Gemini appends -p to what stdin carries; -p also keeps the run headless.
GEMINI_FOLLOW = "Follow the instructions above."


def gemini_policy(wb: str) -> str:
    """A Gemini policy allowing the shell to run ``wb`` and nothing else.

    Gemini checks each command of a chain on its own, so ``wb ... && rm -rf`` is
    still refused at ``rm``.
    """
    return (
        "# Written by wb run for each Gemini session: the shell may run workbench's CLI.\n"
        "[[rule]]\n"
        'toolName = "run_shell_command"\n'
        f"commandPrefix = {json.dumps(wb)}\n"
        'decision = "allow"\n'
        "priority = 100\n"
    )


@dataclass
class Cursor:
    """Cursor's ``agent -p --output-format json --force``: one headless session."""

    model: str = ""
    name: str = "cursor"
    install: str = "install the Cursor CLI: see https://cursor.com/docs/cli/using"

    def missing(self) -> str | None:
        return None if shutil.which("agent") else "agent (the Cursor CLI) is not on PATH"

    def argv(self, prompt: str, root: Path) -> list[str]:
        cursor = shutil.which("agent") or "agent"
        argv = [cursor, "-p", prompt, "--output-format", "json", "--force", "--workspace", str(root)]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def session(self, prompt: str, root: Path, timeout_s: int) -> Session:
        return _session("agent", self.argv(prompt, root), root, timeout_s, parse)


AGENTS: dict[str, type] = {"claude-code": ClaudeCode, "codex": Codex, "gemini": Gemini, "cursor": Cursor}
DEFAULT = "claude-code"


def select(name: str, **settings: str) -> Adapter:
    """The adapter called ``name``, given only the settings it understands.

    ``permission_mode``, ``plugin_dir`` and ``setting_sources`` are Claude
    Code's; another agent is never handed a flag it would reject.
    """
    if name not in AGENTS:
        raise ValueError(f"unknown agent {name!r}; expected one of: {', '.join(AGENTS)}")
    kind = AGENTS[name]
    accepted = set(getattr(kind, "__dataclass_fields__", {}))
    adapter: Adapter = kind(**{key: value for key, value in settings.items() if key in accepted and value})
    return adapter


def _session(
    program: str,
    argv: list[str],
    root: Path,
    timeout_s: int,
    reader: Callable[[str], dict],
    env: dict | None = None,
    prompt: str = "",
) -> Session:
    """Run one session. ``prompt``, when given, goes on stdin: on Windows npm
    installs gemini and codex as ``.cmd`` shims, and cmd.exe cuts an argument
    at its first newline, so a multi-line prompt in argv reaches the agent
    truncated. Otherwise stdin is empty, never the caller's."""
    try:
        done = subprocess.run(
            argv,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=prompt,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Session(exit_code=124, timed_out=True, error=f"timed out after {timeout_s}s")
    except OSError as exc:
        return Session(exit_code=126, error=f"could not start {program}: {exc}")
    report = reader(done.stdout)
    error = "" if done.returncode == 0 else (done.stderr or "").strip()[-300:]
    return Session(exit_code=done.returncode, report=report, error=error)


def parse(stdout: str) -> dict:
    """The JSON result ``claude -p --output-format json`` prints, or ``{}``."""
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_codex(stdout: str) -> dict:
    """The report in ``codex exec --json``'s event stream, or ``{}``.

    One JSON object per line: ``thread.started`` carries the id, each
    ``turn.completed`` its usage, and ``turn.failed`` or ``error`` a failure.
    A line that is not an object is skipped, never raised on.
    """
    report: dict = {}
    usage: dict = {}
    turns = 0
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started" and event.get("thread_id"):
            report["session_id"] = event["thread_id"]
        elif kind == "turn.completed":
            turns += 1
            _add(usage, event.get("usage"))
        elif kind in ("turn.failed", "error"):
            report["is_error"] = True
    if turns:
        report["num_turns"] = turns
    if usage:
        report["usage"] = usage
    return report


def parse_gemini(stdout: str) -> dict:
    """The report in ``gemini --output-format json``, or ``{}``.

    Token counts are summed over every model in ``stats.models``; Gemini reports
    no cost, so that key stays absent.
    """
    payload = parse(stdout)
    if not payload:
        return {}
    report: dict = {}
    if payload.get("session_id"):
        report["session_id"] = payload["session_id"]
    if payload.get("error"):
        report["is_error"] = True
    usage: dict = {}
    stats = payload.get("stats")
    models = stats.get("models") if isinstance(stats, dict) else None
    if isinstance(models, dict):
        for model in models.values():
            if isinstance(model, dict):
                _add(usage, model.get("tokens"))
    if usage:
        report["usage"] = usage
    return report


def _add(total: dict, counts: object) -> None:
    """Add every whole-number count in ``counts`` into ``total``."""
    if not isinstance(counts, dict):
        return
    for key, value in counts.items():
        if isinstance(value, int) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value
