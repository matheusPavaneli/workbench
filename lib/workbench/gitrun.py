"""Running the git commands this tool computes, when explicitly asked to.

Everything else in this package reads. This module is the one place that
writes, and it is drawn the same way ``verify`` is drawn -- narrowly, and by
allowlist -- because the commands come from a plan rather than from a person:

- only the subcommands below, only with the flags listed against them
- no shell: no pipes, no chaining, no substitution, no ``git -c``
- nothing that rewrites or discards history: no reset, rebase, clean, or force
- never a write onto a protected branch
- preconditions checked immediately before each step, not once at the start
- off by default; on only for the call that passes ``--execute``

The default stays "print the command and let the user run it". That is not
timidity: the printed form is what a person approves, and an agent that can
compute the right cherry-pick range has already delivered most of the value.
What ``--execute`` removes is the copy-paste, not the review.

A refusal is cheap -- the command is printed and the user runs it. Running
something unexpected is not.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import gitctx
from .errors import UsageError

TIMEOUT_SECONDS = 120
OUTPUT_CAP = 2000

# Subcommand -> the flags it may carry. A flag not listed here is refused even
# on an allowed subcommand: "git commit" is safe, "git commit --amend" rewrites
# something that may already be pushed.
ALLOWED: dict[str, frozenset[str]] = {
    "fetch": frozenset({"--prune", "origin"}),
    "switch": frozenset({"-c", "--create"}),
    "cherry-pick": frozenset({"--continue", "--abort", "-x"}),
    "commit": frozenset({"-F", "--author"}),
    "push": frozenset({"-u", "--set-upstream", "origin"}),
}

# Refused wherever they appear, including as a value. These either rewrite
# history, discard work, or change what git itself will run -- the same class
# of hazard that FORBIDDEN_ENV covers on the verify side.
DENIED = (
    "--force", "--force-with-lease", "-f", "--hard", "--mixed", "--soft",
    "reset", "rebase", "clean", "gc", "prune-history", "filter-branch", "filter-repo",
    "reflog", "update-ref", "symbolic-ref", "config", "--exec", "--upload-pack",
    "--receive-pack", "--no-verify", "--amend",
)
# ``-c`` is deliberately absent above: it is git's config override before a
# subcommand, and also ``switch``'s create flag. The override form cannot reach
# here at all, because argv[0] must be a subcommand in ALLOWED -- so denying the
# token outright would only break the one legitimate use.

FORBIDDEN_CHARACTERS = (";", "&", "|", ">", "<", "`", "$(", "\n", "\r")

# ``<`` and ``>`` are redirection to a shell and punctuation to a human. An RFC
# 822 address is the second kind, and it is the only place this tool produces
# one: refusing it rejected every ``--author`` a context supplies, which is to
# say the whole feature, for every repo that had a context. Nothing is executed
# through a shell, so the pair is only ever a hazard in the *printed* form --
# which ``rendered`` now quotes.
REDIRECTION = ("<", ">")
VALUE_FLAGS = frozenset({"--author"})

CLEAN_TREE = "clean-tree"
NOT_PROTECTED = "not-protected"
NO_UPSTREAM = "no-upstream"


@dataclass(frozen=True)
class Action:
    """One git command, with the reason it is being run and what must hold first.

    The commands were always computed; they were only ever rendered as strings.
    Making them data is what lets the printed form and the executed form come
    from one place, so ``--execute`` cannot drift from what it showed you.
    """

    argv: list[str]
    why: str = ""
    precondition: str = ""

    @property
    def rendered(self) -> str:
        """Printed for a human to paste, so it has to survive their shell.

        An author string carries spaces and angle brackets; unquoted, the paste
        is parsed as redirection rather than as a name.
        """
        return "git " + " ".join(_quote(token) for token in self.argv)


@dataclass
class Step:
    action: Action
    exit_code: int = 0
    output: str = ""
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused and self.exit_code == 0

    def to_dict(self) -> dict:
        data = {"command": self.action.rendered, "exit_code": self.exit_code}
        if self.output:
            data["output"] = self.output
        if self.refused:
            data["refused"] = self.refused
        return data


@dataclass
class Run:
    steps: list[Step] = field(default_factory=list)
    stopped: str = ""

    @property
    def ok(self) -> bool:
        return not self.stopped and all(step.ok for step in self.steps)

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "at": int(time.time()),
            "ok": self.ok,
            "stopped": self.stopped,
            "steps": [step.to_dict() for step in self.steps],
        }


def disabled_reason(root: Path) -> str | None:
    """Why execution is off, or ``None`` when it is available.

    Two switches, either of which is enough: the environment, for a session or
    a CI job that must never write, and the repo config, for a checkout where
    it is a standing decision.
    """
    if os.environ.get("WB_NO_EXECUTE", "").strip() not in ("", "0", "false", "no"):
        return "WB_NO_EXECUTE is set in the environment"

    from . import profile

    if profile.repo_config(root).get("execute") is False:
        return 'this repo sets "execute": false in .workflow/config.json'
    return None


def _quote(token: str) -> str:
    return f'"{token}"' if any(character in token for character in (" ", *REDIRECTION)) else token


def check(action: Action) -> str | None:
    """Why this action may not run, or ``None`` when it passes the allowlist."""
    if not action.argv:
        return "empty command"

    subcommand = action.argv[0]
    if subcommand not in ALLOWED:
        return f"{subcommand!r} is not a git subcommand this tool runs; allowed: {', '.join(sorted(ALLOWED))}"

    previous = ""
    for token in action.argv:
        # The value of a flag that takes one is data, not syntax. Only the
        # angle brackets are relaxed for it -- everything that could chain or
        # substitute a command is still refused, in every position.
        forbidden = (
            tuple(c for c in FORBIDDEN_CHARACTERS if c not in REDIRECTION)
            if previous in VALUE_FLAGS
            else FORBIDDEN_CHARACTERS
        )
        previous = token
        if any(character in token for character in forbidden):
            return f"{token!r} contains a shell character; commands run without a shell"
        if token.startswith("-") and token in DENIED:
            return f"{token!r} is refused: it rewrites, discards, or redirects what git runs"
        if not token.startswith("-") and token in DENIED and token != subcommand:
            return f"{token!r} is refused"

    permitted = ALLOWED[subcommand]
    for token in action.argv[1:]:
        if token.startswith("-") and token not in permitted:
            return f"{token!r} is not allowed on git {subcommand}; allowed: {', '.join(sorted(permitted)) or 'none'}"
    return None


def precondition(action: Action, root: Path, protected: list[str]) -> str | None:
    """Checked immediately before the step, not once for the whole series.

    A series changes the thing its later steps depend on -- the branch, the
    tree -- so one check at the start would be a check of the wrong state.
    """
    if action.precondition == CLEAN_TREE and gitctx.tracked_changes(root):
        # Tracked changes only. ``git switch -c`` carries untracked files across
        # safely, and refusing on them made the common case -- a scratch file in
        # the checkout -- unrunnable, with advice that did not work either:
        # plain ``git stash`` leaves untracked files exactly where they were.
        return "the working tree has uncommitted changes; commit or stash them first"
    if action.precondition == NOT_PROTECTED:
        current = gitctx.branch(root)
        if current in protected:
            return f"{current!r} is a protected branch; start a working branch first"
    if action.precondition == NO_UPSTREAM and _upstream(root):
        return (
            "this branch already has an upstream; updating a published branch is yours to run, "
            "so that a force-push is never a decision this tool can reach"
        )
    return None


def apply(actions: list[Action], root: Path, *, protected: list[str] | None = None) -> Run:
    """Run the series, stopping at the first refusal or failure.

    Stopping matters more than reporting: a cherry-pick series that continues
    past a failed pick lands commits out of order, which is the exact mistake
    the carry computation exists to prevent.
    """
    reason = disabled_reason(root)
    if reason:
        raise UsageError(
            f"execution is disabled: {reason}",
            fix=["run the printed commands yourself, or clear the switch"],
        )

    run = Run()
    for action in actions:
        refusal = check(action) or precondition(action, root, protected or [])
        if refusal:
            run.steps.append(Step(action=action, refused=refusal))
            run.stopped = refusal
            break

        completed = _run(action, root)
        run.steps.append(completed)
        if not completed.ok:
            run.stopped = f"{action.rendered} exited {completed.exit_code}"
            break
    return run


def record(run: Run, key: str, root: Path) -> Path | None:
    """Append to the ticket's git log. A trail is the price of writing anything."""
    from . import artifacts

    try:
        path = artifacts.ticket_dir(key, root) / "git.log.json"
        history = []
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            history = loaded if isinstance(loaded, list) else []
        history.append(run.to_dict())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
        return path
    except (OSError, json.JSONDecodeError):
        return None  # A missing trail is a gap in the record, not a failed run.


def render(run: Run) -> str:
    lines = []
    for step in run.steps:
        if step.refused:
            lines.append(f"REFUSED  {step.action.rendered}")
            lines.append(f"         {step.refused}")
            continue
        lines.append(f"{'ok' if step.ok else 'FAIL':<8} {step.action.rendered}")
        for line in step.output.splitlines()[:6]:
            lines.append(f"         {line}")
    if run.stopped:
        lines.append("")
        lines.append(f"stopped: {run.stopped}")
        lines.append("nothing after this point ran")
    return "\n".join(lines)


def _run(action: Action, root: Path) -> Step:
    try:
        completed = subprocess.run(
            ["git", *action.argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return Step(action=action, exit_code=124, output=f"timed out after {TIMEOUT_SECONDS}s")
    except (OSError, subprocess.SubprocessError) as exc:
        return Step(action=action, exit_code=1, output=str(exc))

    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return Step(action=action, exit_code=completed.returncode, output=output[:OUTPUT_CAP])


def _upstream(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None
