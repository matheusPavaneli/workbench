"""Running the verification a plan declared, and recording what happened.

A PR that claims "tests pass" is worth nothing; a PR carrying the command and
its output is worth reading. This module executes the commands from the plan's
``verify`` list and writes down what they printed.

The commands come from a file a model wrote, so this is a real execution
boundary, and it is drawn narrowly on purpose:

- only commands already in the audited ``sdd.json`` -- nothing passed in ad hoc
- only known build, test and lint runners
- no shell: no pipes, no redirection, no chaining, no substitution
- a timeout, and a cap on how much output is kept

Anything outside that is refused with the command printed, for the user to run
themselves. Refusing is cheap; running an unexpected command is not.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import UsageError

TIMEOUT_SECONDS = 600
OUTPUT_HEAD = 1500
OUTPUT_TAIL = 2500

# Runners a verification step may invoke. Deliberately conservative: adding an
# entry is a decision, and the fallback (run it yourself) always works.
ALLOWED_RUNNERS = frozenset(
    {
        # python
        "python", "python3", "py", "pytest", "tox", "uv", "uvx", "poetry", "hatch",
        "ruff", "mypy", "black", "flake8", "pylint", "pyright",
        # javascript / typescript
        "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
        "tsc", "eslint", "prettier", "vitest", "jest", "playwright", "biome",
        # other ecosystems
        "go", "cargo", "rustc", "dotnet", "mvn", "gradle", "gradlew",
        "bundle", "rake", "rspec", "composer", "phpunit", "dart", "flutter", "swift",
        # task runners
        "make", "just", "task", "bazel",
    }
)

FORBIDDEN_CHARACTERS = (";", "&", "|", ">", "<", "`", "$(", "\n", "\r")


@dataclass
class Result:
    command: str
    exit_code: int
    duration_ms: int
    output: str
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict:
        data = {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "output": self.output,
        }
        if self.truncated:
            data["truncated"] = True
        return data


@dataclass
class Evidence:
    key: str
    results: list[Result] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results) and not self.refused

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "key": self.key,
            "verdict": "pass" if self.passed else "fail",
            "results": [r.to_dict() for r in self.results],
            "refused": [{"command": c, "reason": r} for c, r in self.refused],
        }


def check(command: str) -> str | None:
    """Return why a command is refused, or ``None`` if it may run."""
    if not command.strip():
        return "empty command"

    for character in FORBIDDEN_CHARACTERS:
        if character in command:
            return (
                f"contains {character!r}: shell features are not available here. "
                "Split it into separate verify entries, or run it yourself."
            )

    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"cannot be parsed: {exc}"

    if not parts:
        return "empty command"

    runner = Path(parts[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if runner.endswith(suffix):
            runner = runner[: -len(suffix)]

    if runner not in ALLOWED_RUNNERS:
        return f"{runner!r} is not a known test, build or lint runner; run it yourself and record the result"

    return None


def run(key: str, commands: list[str], root: Path) -> Evidence:
    evidence = Evidence(key=key)

    for command in commands:
        refusal = check(command)
        if refusal:
            evidence.refused.append((command, refusal))
            continue
        evidence.results.append(_execute(command, root))

    return evidence


def _execute(command: str, root: Path) -> Result:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        output = f"timed out after {TIMEOUT_SECONDS}s"
        exit_code = 124
    except FileNotFoundError:
        output = "command not found on PATH"
        exit_code = 127
    except OSError as exc:
        output = f"could not run: {exc}"
        exit_code = 126

    duration = int((time.monotonic() - started) * 1000)
    trimmed, truncated = _trim(output)
    return Result(command=command, exit_code=exit_code, duration_ms=duration, output=trimmed, truncated=truncated)


def _trim(output: str) -> tuple[str, bool]:
    """Keep the start and the end. Failures live at the end, context at the start."""
    text = output.strip()
    if len(text) <= OUTPUT_HEAD + OUTPUT_TAIL:
        return text, False
    return f"{text[:OUTPUT_HEAD]}\n\n[... {len(text) - OUTPUT_HEAD - OUTPUT_TAIL} chars omitted ...]\n\n{text[-OUTPUT_TAIL:]}", True


def render(evidence: Evidence) -> str:
    lines = [f"# {evidence.key} — verification evidence", ""]
    lines.append(f"**Verdict:** {'pass' if evidence.passed else 'fail'}")
    lines.append("")

    for result in evidence.results:
        status = "pass" if result.ok else f"FAIL (exit {result.exit_code})"
        lines += [f"## `{result.command}` — {status}", "", "```", result.output or "(no output)", "```", ""]

    if evidence.refused:
        lines += ["## Not run", "", "These were refused and must be run manually:", ""]
        lines += [f"- `{command}` — {reason}" for command, reason in evidence.refused]
        lines.append("")

    return "\n".join(lines)


def require_commands(commands: object) -> list[str]:
    if not isinstance(commands, list) or not commands:
        raise UsageError(
            "the plan declares no verification commands",
            fix=["add exact commands to verify[] in sdd.json, then re-run the audit"],
        )
    return [str(command) for command in commands]
