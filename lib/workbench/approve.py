"""The one human decision a ticket is waiting on, and approving exactly that.

Approval used to be per subsystem: ``wb impl verify --approve '<exact text>'``
retyped by hand, a scope deviation answered with ``wb sdd amend``, a publish
with ``wb git push --execute``. The user had to know which gate was waiting
before they could open it.

``pending`` finds it, in the order the flow meets them:

- **scope**: files changed that the audited plan does not list. Approving adds
  them to the plan and re-audits it, as ``wb sdd amend`` does, with the
  approval as the recorded reason.
- **verify**: the plan's verify commands, not yet approved on this machine.
  Approving records them; nothing runs until ``wb impl verify``.
- **publish**: a drafted PR on a branch that was never pushed. Approving runs
  the first push, the one push this tool may make.

Each decision carries a token: a digest of exactly what was shown. Approving
takes the token back and recomputes the decision; if anything changed in
between, the token no longer matches and nothing happens. So an approval can
never cover something the person was not shown in the output they approved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import artifacts, audit as audit_lib, companions, flow as flow_lib, gitctx, gitrun, scope, verify

SCOPE = "scope"
VERIFY = "verify"
PUBLISH = "publish"


@dataclass
class Decision:
    key: str
    kind: str
    title: str
    items: list[str] = field(default_factory=list)
    # What approving does, and what the alternative is: both in the output
    # that carries the token.
    effect: str = ""
    otherwise: str = ""

    @property
    def token(self) -> str:
        shown = json.dumps([self.key, self.kind, self.items], ensure_ascii=False)
        return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:10]


def pending(key: str, root: Path) -> Decision | None:
    """The decision ``key`` waits on, or ``None``."""
    plan = _json(artifacts.ticket_dir(key, root) / "sdd.json")
    audit = _json(artifacts.ticket_dir(key, root) / "audit.json")
    if (
        isinstance(plan, dict)
        and isinstance(audit, dict)
        and audit.get("verdict") == "pass"
        and audit_lib.standing(audit, plan) is None
    ):
        decision = _scope(key, plan, root) or _verify(key, plan, root)
        if decision:
            return decision
    return _publish(key, root)


def apply(decision: Decision, root: Path) -> str:
    """Carry out ``decision``; the line to print. Raises on a failure, with the fix."""
    from .errors import EXIT_AUDIT, WbError

    if decision.kind == VERIFY:
        verify.approve(root, decision.items)
        return f"approved {_entries(len(decision.items))} on this machine; next: wb impl verify {decision.key}"

    if decision.kind == SCOPE:
        return _amend(decision, root)

    if decision.kind == PUBLISH:
        branch = decision.items[0]
        action = gitrun.Action(["push", "-u", "origin", branch], why=f"publish {branch}",
                               precondition=gitrun.NO_UPSTREAM)
        result = gitrun.apply([action], root, protected=flow_lib.protected(root))
        gitrun.record(result, decision.key, root)
        if not result.ok:
            raise WbError(
                f"the push did not happen: {gitrun.render(result).strip()}",
                code=EXIT_AUDIT,
                fix=[action.rendered],
            )
        return f"pushed {branch}; open the PR with the description in .workflow/{decision.key}/pr.md"

    raise ValueError(f"unknown decision {decision.kind!r}")


def _scope(key: str, plan: dict, root: Path) -> Decision | None:
    from . import status

    planned = {str(item.get("path", "")).replace("\\", "/") for item in plan.get("files") or [] if isinstance(item, dict)}
    planned.discard("")
    changed = status.branch_changes(root)
    globs = companions.generated_globs(root)
    claimed = set(scope.claims(key, root))
    stray = sorted(
        path
        for path in changed - planned - claimed
        if not companions.reason(path, planned, globs) and (root / path).is_file()
    )
    if not stray:
        return None
    return Decision(
        key=key,
        kind=SCOPE,
        title=f"{len(stray)} changed file(s) the audited plan does not list",
        items=stray,
        effect="approving adds them to the plan and re-audits it (wb sdd amend), with your approval as the reason",
        otherwise="or revert them and leave the plan as it is",
    )


def _verify(key: str, plan: dict, root: Path) -> Decision | None:
    commands = [str(command) for command in plan.get("verify") or []]
    if not commands:
        return None
    env, _ = verify.resolve_env(plan.get("verify_env"))
    waiting = verify.unapproved(root, verify.entries(commands, env, key), key)
    if not waiting:
        return None
    return Decision(
        key=key,
        kind=VERIFY,
        title=f"{_entries(len(waiting))} from the plan wait for your approval; they run with your permissions",
        items=waiting,
        effect=f"approving records them for this checkout; nothing runs until wb impl verify {key}",
        otherwise="or change the plan's verify list and re-run wb sdd audit",
    )


def _publish(key: str, root: Path) -> Decision | None:
    if not (artifacts.ticket_dir(key, root) / "pr.md").is_file():
        return None
    branch = gitctx.branch(root)
    if not branch or not gitctx.has_origin(root) or branch in flow_lib.protected(root):
        return None
    probe = gitrun.Action(["push", "-u", "origin", branch], precondition=gitrun.NO_UPSTREAM)
    if gitrun.precondition(probe, root, []) is not None:
        return None
    return Decision(
        key=key,
        kind=PUBLISH,
        title=f"the PR is drafted and {branch} was never pushed",
        items=[branch],
        effect=f"approving runs: {probe.rendered}",
        otherwise="or push it yourself",
    )


def _amend(decision: Decision, root: Path) -> str:
    import argparse

    from .cli import sdd as sdd_cli
    from .errors import EXIT_AUDIT, WbError

    # Each file with its measured size, so the plan's tier reads the real
    # change: an amendment with no estimate reads as unmeasured, and fails.
    audit = _json(artifacts.ticket_dir(decision.key, root) / "audit.json")
    baseline = str(audit.get("baseline") or "") if isinstance(audit, dict) else ""
    for path in decision.items:
        lines = gitctx.lines_changed(root, baseline or "HEAD", [path])
        args = argparse.Namespace(
            key=decision.key, paths=[path], why="approved by a person with wb approve", new=False, lines=lines
        )
        if sdd_cli._amend(args) != 0:
            raise WbError(
                f"the plan for {decision.key} is amended with {path} but its audit failed",
                code=EXIT_AUDIT,
                fix=[f"fix the plan, then: wb sdd audit {decision.key}"],
            )
    return f"added {len(decision.items)} file(s) to the plan for {decision.key}; its audit passed"


def _entries(count: int) -> str:
    return f"{count} verify entr{'y' if count == 1 else 'ies'}"


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
