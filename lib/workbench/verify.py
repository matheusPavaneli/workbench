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

The allowlist bounds *which program* runs, not what it is told to do:
``python -c`` and ``node -e`` are on it by construction. So nothing runs until
a person has approved the exact string on this machine -- see ``approve``.

Evidence records the tree and the plan it verified, so a pass describes one
version of the code and stops standing once that version is gone.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import contexts
from .errors import UsageError

TIMEOUT_SECONDS = 600
OUTPUT_HEAD = 1500
OUTPUT_TAIL = 2500
MAX_ENV_VALUE = 4096

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Variables that change how the interpreter or linker loads code. Setting one
# of these makes a verify step run something the command allowlist never sees,
# which is the one thing this boundary exists to prevent.
FORBIDDEN_ENV = frozenset(
    {
        "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
        "PATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE", "PYTHONHOME", "NODE_OPTIONS", "BASH_ENV", "ENV",
        "PERL5OPT", "RUBYOPT", "GIT_SSH_COMMAND", "GIT_EXTERNAL_DIFF",
    }
)

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


# Exit codes that say the command never got as far as a test: timed out, could
# not run, not found. A regression test "failing" with one of these proves
# nothing about the fix.
NOT_A_TEST_FAILURE = frozenset({124, 126, 127})


@dataclass
class Regression:
    """One regression target, run without the fix and with it."""

    target: str
    command: str
    without_fix: Result
    with_fix: Result

    @property
    def ok(self) -> bool:
        failed = self.without_fix.exit_code != 0 and self.without_fix.exit_code not in NOT_A_TEST_FAILURE
        return failed and self.with_fix.ok

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "command": self.command,
            "ok": self.ok,
            "without_fix": self.without_fix.to_dict(),
            "with_fix": self.with_fix.to_dict(),
        }


@dataclass
class Evidence:
    key: str
    results: list[Result] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    # What was verified. ``tree`` is ``gitctx.tree`` before the first command
    # ran; ``plan`` is the audited plan's digest. Both ``None`` outside a checkout.
    tree: str | None = None
    head: str | None = None
    plan: str | None = None
    # Test targets the plan named that the branch never changed.
    tests_missing: list[str] = field(default_factory=list)
    # ``None`` when --regression was not asked for; the base it ran against.
    regression: list[Regression] | None = None
    regression_base: str | None = None

    @property
    def passed(self) -> bool:
        if self.tests_missing:
            return False
        if self.regression is not None and not all(r.ok for r in self.regression):
            return False
        return bool(self.results) and all(r.ok for r in self.results) and not self.refused

    def to_dict(self) -> dict:
        data = {
            "schema": 1,
            "key": self.key,
            "verdict": "pass" if self.passed else "fail",
            "results": [r.to_dict() for r in self.results],
            "refused": [{"command": c, "reason": r} for c, r in self.refused],
            "env": sorted(self.env),
            "tree": self.tree,
            "head": self.head,
            "plan_sha256": self.plan,
            "tests_missing": list(self.tests_missing),
        }
        if self.regression is not None:
            data["regression"] = {
                "base": self.regression_base,
                "targets": [r.to_dict() for r in self.regression],
            }
        return data


def test_targets(plan: dict) -> list[str]:
    """The files the plan's ``tests[]`` name, as repo-relative paths.

    A pytest node id (``tests/x.py::test_y``) names its file before the ``::``;
    the file is what the branch has to change.
    """
    targets = set()
    for item in plan.get("tests") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or "").split("::", 1)[0].replace("\\", "/").strip()
        if target:
            targets.add(target)
    return sorted(targets)


def regression_targets(plan: dict) -> list[str]:
    return test_targets({"tests": [t for t in plan.get("tests") or [] if isinstance(t, dict) and t.get("kind") == "regression"]})


def missing_tests(targets: list[str], changed: set[str]) -> list[str]:
    """Targets the branch never touched: a test that was named and not written."""
    return [target for target in targets if target not in changed]


# How to run one test file, per runner ``wb repo profile`` can report. A runner
# that is not here has no per-file form this module knows; --regression refuses
# it rather than guessing one.
REGRESSION_RUNNERS: dict[str, list[str]] = {
    "unittest": ["python", "-m", "unittest"],
    "pytest": ["python", "-m", "pytest"],
    "vitest": ["npx", "vitest", "run"],
    "jest": ["npx", "jest"],
}


def regression_command(runner: str | None, target: str) -> tuple[str | None, str | None]:
    """``(command, None)`` running one target, or ``(None, why not)``.

    The target comes from a plan a model wrote, so it is held to a path inside
    the checkout: never an option, never absolute, never climbing out.
    """
    prefix = REGRESSION_RUNNERS.get(runner or "")
    if prefix is None:
        known = ", ".join(sorted(REGRESSION_RUNNERS))
        return None, f"no per-file command for test runner {runner or 'unknown'!r} (known: {known})"
    path = target.replace("\\", "/")
    if path.startswith("-"):
        return None, f"{target!r} reads as an option, not a path"
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in path.split("/"):
        return None, f"{target!r} is not a path inside the checkout"
    command = shlex.join([*prefix, path])
    refusal = check(command)
    return (None, refusal) if refusal else (command, None)


def run_regression(
    targets: list[tuple[str, str]], root: Path, base: str, carried: list[str], env: dict | None = None
) -> list[Regression]:
    """Each ``(target, command)`` at ``base`` with the tests carried in, then here.

    "Without the fix" is the base the branch left, plus the test files the
    branch changed: the tests as written, the code as it was. A worktree, not a
    stash, so the user's tree is never touched and a fix already committed is
    still left out.
    """
    from . import gitctx

    overrides, _ = resolve_env(env)
    outcomes: list[Regression] = []
    with gitctx.worktree(root, base) as tree:
        for path in carried:
            source, destination = root / path, tree / path
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif destination.is_file():
                destination.unlink()
        without = [_execute(command, tree, overrides) for _, command in targets]
    for (target, command), before in zip(targets, without):
        outcomes.append(Regression(target, command, before, _execute(command, root, overrides)))
    return outcomes


STALE_TREE = "the code changed since it was verified"
STALE_PLAN = "the plan changed since it was verified"


def standing(evidence: object, plan_digest: str | None, tree: str | None) -> str | None:
    """Why this evidence does not describe the code as it is, or ``None`` when it does.

    A pass is a claim about one tree. Read after an edit, it describes code that
    is no longer there -- and ``wb status`` and ``wb pr context`` would repeat it
    as if it were current. Evidence written before trees were recorded has none,
    so it compares as changed: nothing ties it to the code beside it.

    Like ``audit.standing``, this catches drift, not forgery.
    """
    if not isinstance(evidence, dict):
        return "has no evidence"
    if evidence.get("verdict") != "pass":
        return "did not pass"
    if evidence.get("plan_sha256") != plan_digest:
        return STALE_PLAN
    if evidence.get("tree") != tree:
        return STALE_TREE
    return None


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


def run(key: str, commands: list[str], root: Path, env: dict | None = None) -> Evidence:
    evidence = Evidence(key=key)
    overrides, rejected = resolve_env(env)
    evidence.env = overrides
    evidence.refused.extend(rejected)

    for command in commands:
        refusal = check(command)
        if refusal:
            evidence.refused.append((command, refusal))
            continue
        evidence.results.append(_execute(command, root, overrides))

    return evidence


def resolve_env(env: dict | None) -> tuple[dict, list]:
    """Split a plan's ``env`` block into what may be applied and what may not.

    Shell is refused outright, so ``PYTHONPATH=lib python -m unittest`` cannot
    be expressed as a command -- which meant a repo whose tests need a variable
    could not be verified at all. The variables are therefore declared as data
    in the audited plan, where they are reviewed alongside the commands.

    Nothing here expands, interpolates or reads a file: a value is a literal
    string. Variables that change how the process itself is loaded are refused,
    because those turn a verify step into arbitrary code execution by a route
    the command allowlist cannot see.
    """
    if not env:
        return {}, []
    if not isinstance(env, dict):
        return {}, [("env", "must be an object of NAME: value pairs")]

    applied: dict = {}
    rejected: list = []

    for raw_name, raw_value in env.items():
        name = str(raw_name)
        if name.upper() in FORBIDDEN_ENV:
            rejected.append((f"env {name}", "changes how the process loads code; not applied"))
            continue
        if not _ENV_NAME.match(name):
            rejected.append((f"env {name}", "not a plain variable name"))
            continue
        if isinstance(raw_value, (dict, list)):
            rejected.append((f"env {name}", "value must be a string"))
            continue
        value = str(raw_value)
        if len(value) > MAX_ENV_VALUE:
            rejected.append((f"env {name}", f"value longer than {MAX_ENV_VALUE} characters"))
            continue
        applied[name] = value

    return applied, rejected


def _execute(command: str, root: Path, env: dict | None = None) -> Result:
    started = time.monotonic()
    environment = None
    if env:
        environment = {**os.environ, **env}
    try:
        completed = subprocess.run(
            shlex.split(command),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=environment,
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

    if evidence.env:
        # Names only. A value here is as likely to be a connection string as a
        # search path, and evidence.md is written to be pasted into a PR.
        lines += [f"**Environment:** {', '.join(sorted(evidence.env))}", ""]

    if evidence.tree:
        verified = f"tree `{evidence.tree[:12]}`"
        if evidence.head:
            verified += f" on `{evidence.head[:12]}`"
        lines += [f"**Verified:** {verified}", ""]

    for result in evidence.results:
        status = "pass" if result.ok else f"FAIL (exit {result.exit_code})"
        lines += [f"## `{result.command}` — {status}", "", "```", result.output or "(no output)", "```", ""]

    if evidence.refused:
        lines += ["## Not run", "", "These were refused and must be run manually:", ""]
        lines += [f"- `{command}` — {reason}" for command, reason in evidence.refused]
        lines.append("")

    if evidence.tests_missing:
        lines += ["## Planned tests not changed", "", "The plan names these tests; the branch never touched them:", ""]
        lines += [f"- `{target}`" for target in evidence.tests_missing]
        lines.append("")

    if evidence.regression is not None:
        base = f" against `{evidence.regression_base[:12]}`" if evidence.regression_base else ""
        lines += [f"## Regression{base}", ""]
        for outcome in evidence.regression:
            verdict = "pass" if outcome.ok else "FAIL"
            lines += [
                f"### `{outcome.target}` — {verdict}",
                "",
                f"Without the fix: exit {outcome.without_fix.exit_code} (must fail). "
                f"With it: exit {outcome.with_fix.exit_code} (must pass).",
                "",
                "```",
                outcome.without_fix.output or "(no output)",
                "```",
                "",
            ]

    return "\n".join(lines)


APPROVALS_NAME = "approvals.json"


def approvals_path() -> Path:
    """Per machine, never in the checkout: a plan must not be able to ship its own approval."""
    return contexts.home() / APPROVALS_NAME


KEY_PLACEHOLDER = "<KEY>"


def abstract(text: str, key: str) -> str:
    """``text`` with the plan's own key, as a whole token, written as ``<KEY>``.

    A plan's verify list usually names its own ticket (``wb sdd audit ABC-1``),
    so a verbatim approval asked again on every ticket for the same command --
    and an approval asked for every time is one people learn to give unread.
    Only the key of the plan being verified is abstracted, and only where no
    letter, digit, ``_`` or ``-`` touches it: ``ABC-12`` or ``x-ABC-1`` is a
    different string and stays one. ``<`` is refused in any command that runs,
    so the placeholder can never be mistaken for real text.
    """
    if not key:
        return text
    return re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(key)}(?![A-Za-z0-9_-])", KEY_PLACEHOLDER, text)


def entries(commands: list[str], env: dict, key: str = "") -> list[str]:
    """What a person approves: each command verbatim, each variable as ``env NAME=value``.

    Verbatim, because the approval is only worth the attention it got. A digest
    would be approved unread; the string itself shows up in the permission
    prompt of whatever runs the approve call. One changed character is a
    different entry, and asks again -- the plan's own key aside, see ``abstract``.
    """
    items = [*commands, *(f"env {name}={value}" for name, value in sorted(env.items()))]
    return [abstract(item, key) for item in items]


def unapproved(root: Path, wanted: list[str], key: str = "") -> list[str]:
    """Entries not yet approved. An approval stored verbatim, before keys were
    abstracted, still counts for the ticket it named."""
    approved = set(_approvals().get(_repo_id(root), []))
    return [
        entry
        for entry in wanted
        if entry not in approved and not (key and entry.replace(KEY_PLACEHOLDER, key) in approved)
    ]


def approve(root: Path, given: list[str]) -> None:
    """Record approvals for this checkout. Refuses to write over a file it cannot read."""
    data = _approvals()
    repo = _repo_id(root)
    data[repo] = sorted(set(data.get(repo, [])) | set(given))
    path = approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _repo_id(root: Path) -> str:
    # The checkout, not the remote: keyed by remote, a fork's plan would run on
    # the strength of approvals given to upstream's.
    return str(Path(root).resolve())


def _approvals() -> dict:
    path = approvals_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(
            f"cannot read {path}",
            fix=[f"fix or delete {path}; every command will ask for approval again"],
        ) from exc
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        raise UsageError(f"{path} is not an object of repo: [entries]", fix=[f"fix or delete {path}"])
    return data


def require_commands(commands: object) -> list[str]:
    if not isinstance(commands, list) or not commands:
        raise UsageError(
            "the plan declares no verification commands",
            fix=["add exact commands to verify[] in sdd.json, then re-run the audit"],
        )
    return [str(command) for command in commands]
