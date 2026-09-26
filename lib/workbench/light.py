"""The light path: small work with no plan up front, held to the floor at the end.

The first benchmark put a one-file chore at 18 s plain and 190 s through the
flow, with the same result. The light *tier* already waived two sections of the
plan; the plan, its audit, the verify approval and four skills were still paid
for before a one-line fix could land. A developer who watches a 5-minute task
become 15 routes around the tool, and then it guards nothing.

So small work skips the paperwork and keeps the guardrails, moved to the one
moment they can be checked against the real change instead of a forecast of
it. ``wb start`` puts eligible work on this path; ``wb finish`` then measures
what the branch actually did:

- the commit message against the repo's convention
- size and zones: past the light bound, or into a critical zone, the work
  leaves this path for the standard route -- nothing is lost, it gets a plan
- a logic change with no test change is refused
- the repo's own test suite, by the command its runner implies
- on a bug, each changed test must fail on the base the branch left and pass
  here: a regression test that passes without the fix proves nothing

It speaks only when something blocks. A pass prints one line and the commit
command. Nothing here is a file the user has to open, edit or approve.

The suite command is built here from the runner ``wb repo profile`` detects,
never read from a file a model wrote, which is why it runs without the approval
``wb impl verify`` asks for: that approval exists because a plan's verify list
is model-authored text.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import artifacts, commitmsg, gitctx, profile, review, sdd as sdd_lib, verify

MARKER = "light.json"
CONFIG_KEY = "light_path"

# The suite, per runner. A runner not here has no command this module will
# guess; finish then says so and relies on the change's own test having run.
SUITES: dict[str, list[str]] = {
    "unittest": ["python", "-m", "unittest", "discover", "-s", "{test_dir}", "-q"],
    "pytest": ["python", "-m", "pytest", "-q"],
    "vitest": ["npx", "vitest", "run"],
    "jest": ["npx", "jest"],
}

# Work that owes something a plan-free path cannot give: a product frame, or a
# note for a reader outside engineering. A bug is not here: on this path it owes
# a regression test proven to fail without the fix, which is the handover's
# engineering half, checked rather than written.
FRAMED = {"feature"}
AUDIENCE = sdd_lib.HANDOVER_TYPES - {"bug", "defect"}

REGRESSION_KINDS = {"bug", "defect"}


def enabled(root: Path) -> bool:
    """``"light_path": false`` in the repo config restores the full route for everything."""
    return profile.repo_config(root).get(CONFIG_KEY) is not False


def eligible(key: str, kind: str, root: Path) -> tuple[bool, str]:
    """Whether a ticket may start on the light path, and why (not)."""
    if not enabled(root):
        return False, f'"{CONFIG_KEY}": false in .workflow/config.json'
    if key.startswith(("incident-", "idea-")):
        return False, "incidents and ideas take the full route"
    lowered = kind.strip().lower()
    if lowered in FRAMED:
        return False, f"{lowered} work owes a product frame"
    if lowered in AUDIENCE:
        return False, f"{lowered} work has a reader outside engineering"
    return True, "no plan up front; wb finish holds the change to the floor"


def marker(key: str, root: Path) -> dict | None:
    """The light record for ``key``, or ``None`` when it is not on this path."""
    path = artifacts.ticket_dir(key, root) / MARKER
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def mark(key: str, root: Path) -> None:
    artifacts.write_json(key, MARKER, {"schema": 1, "key": key}, root)


def unmark(key: str, root: Path) -> None:
    (artifacts.ticket_dir(key, root) / MARKER).unlink(missing_ok=True)


def suite_command(conventions: dict[str, str]) -> str | None:
    template = SUITES.get(conventions.get("test_runner", ""))
    if template is None:
        return None
    test_dir = conventions.get("test_dir") or "tests"
    return shlex.join([part.replace("{test_dir}", test_dir) for part in template])


@dataclass
class Finish:
    """What ``wb finish`` found. ``blocked`` empty means the change is clear."""

    key: str
    changed: list[str] = field(default_factory=list)
    lines: int | None = None
    blocked: list[str] = field(default_factory=list)
    fix: list[str] = field(default_factory=list)
    # The change is too big or too sensitive for this path; it needs a plan.
    outgrew: str = ""
    ran: list[verify.Result] = field(default_factory=list)
    regression: list[verify.Regression] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked and not self.outgrew


def check(key: str, root: Path, message: str, base: str, kind: str) -> Finish:
    """Every guardrail, cheapest first; the first class of failure stops the rest.

    Running a suite on a change that has already been refused would only make
    the refusal slower to read.
    """
    result = Finish(key=key)
    result.changed = [
        path for path in gitctx.changed_since(root, base) if not path.startswith(artifacts.WORKFLOW_DIR + "/")
    ]
    if not result.changed:
        result.blocked.append("nothing has changed on this branch yet")
        result.fix.append(f"make the change and its test, then: wb finish {key} -m \"{message}\"")
        return result

    convention = commitmsg.resolve(root, gitctx.recent_subjects(root))
    problems = commitmsg.check(message, convention, key=key)
    if problems:
        result.blocked.extend(f"commit message: {problem}" for problem in problems)
        result.fix.append(f"e.g. {convention.describe()}")
        return result

    zones = profile.critical_zones(result.changed)
    result.lines = gitctx.lines_changed(root, base, result.changed)
    bound = sdd_lib.light_max_lines(root)
    if zones:
        result.outgrew = f"touches {', '.join(sorted(zones))}"
    elif result.lines is None:
        result.outgrew = "the size of the change cannot be measured"
    elif result.lines > bound:
        result.outgrew = f"{result.lines} lines changed (light is up to {bound})"
    if result.outgrew:
        return result

    logic = [path for path in result.changed if review.is_source(path)]
    tests = [path for path in result.changed if review.is_test(path)]
    if logic and not tests:
        result.blocked.append(f"{', '.join(logic)} changed and no test did")
        result.fix.append("add or change a test that covers the change, then run wb finish again")
        return result

    conventions = profile.resolve(root).conventions
    command = suite_command(conventions)
    if command is None:
        result.note = f"no suite command for runner {conventions.get('test_runner') or 'unknown'}; run your tests"
    else:
        evidence = verify.run(key, [command], root)
        result.ran = evidence.results
        for refused, why in evidence.refused:
            result.blocked.append(f"{refused}: {why}")
        for run in evidence.results:
            if not run.ok:
                result.blocked.append(f"{run.command} exited {run.exit_code}")
        if result.blocked:
            tail = next((run.output for run in evidence.results if not run.ok), "")
            if tail:
                result.fix.append(tail.strip().splitlines()[-1])
            result.fix.append("fix the failure, then run wb finish again")
            return result

    if kind.strip().lower() in REGRESSION_KINDS and tests:
        pairs = []
        for target in tests:
            command_for, why_not = verify.regression_command(conventions.get("test_runner"), target)
            if command_for is None:
                result.note = f"regression not checked: {why_not}"
                break
            pairs.append((target, command_for))
        else:
            result.regression = verify.run_regression(pairs, root, base, tests)
            weak = [outcome.target for outcome in result.regression if not outcome.ok]
            if weak:
                result.blocked.append(
                    f"{', '.join(weak)} passes without the fix too, so it does not prove the bug is fixed"
                )
                result.fix.append("make the test exercise the bug: it must fail on the base and pass here")
    return result


def record(result: Finish, root: Path) -> None:
    """The pass, bound to the tree it saw, so status can tell when it stops describing the code."""
    artifacts.write_json(
        result.key,
        MARKER,
        {
            "schema": 1,
            "key": result.key,
            "verdict": "pass",
            "tree": gitctx.tree(root),
            "changed": result.changed,
            "lines": result.lines,
            "commands": [run.command for run in result.ran],
        },
        root,
    )
