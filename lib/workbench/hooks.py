"""Claude Code hooks: the scope guard at the moment of the edit.

``wb impl check`` reports scope when somebody runs it, and the skill asks the
agent to run it "after each step or two". A check that depends on being
remembered is a request, not a control -- so the same rule is applied here,
before the edit lands, by the harness rather than by the model.

Two decisions, both deliberately narrow:

- **PreToolUse** on a file-writing tool refuses a path the active ticket's
  audited plan does not list, and that no other audited plan accounts for --
  the rule ``impl check`` fails on, applied one edit earlier.
- **Stop** reports a ticket whose planned files changed while its verification
  is missing or no longer describes the code. It reports; it blocks only when
  the repo asked for ``"hooks": "strict"``.

Silence is the default. No ticket on the branch, no plan, or a plan that has
never passed its audit: every edit is allowed and nothing is printed. A hook
that got in the way of work it had no opinion about would be switched off, and
then it guards nothing.

The active ticket is the one the **branch** names, never the most recently
touched one: ``wb next`` may guess, a hook that refuses edits may not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import artifacts, audit, companions, gitctx, scope, status, verify

OFF = "off"
ON = "on"
STRICT = "strict"

# The tools that write a file, and the input field naming it.
PATH_FIELDS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}


def mode(root: Path) -> str:
    """``off``, ``on`` or ``strict``. The environment wins over the repo."""
    if os.environ.get("WB_NO_HOOKS", "").strip() not in ("", "0", "false", "no"):
        return OFF

    from . import profile

    setting = profile.repo_config(root).get("hooks")
    if setting is False:
        return OFF
    if setting == STRICT:
        return STRICT
    return ON


def active(root: Path) -> str | None:
    """The key the branch names among tickets with artifacts, or ``None``."""
    return status.key_from_branch(status.keys(root), root)


def pre_tool_use(payload: dict, root: Path) -> dict | None:
    """The answer to one edit: a deny, or ``None`` to stay out of the way."""
    if mode(root) == OFF:
        return None

    field = PATH_FIELDS.get(str(payload.get("tool_name", "")))
    tool_input = payload.get("tool_input")
    if not field or not isinstance(tool_input, dict) or not tool_input.get(field):
        return None

    relative = _relative(root, str(tool_input[field]))
    if relative is None or relative.split("/", 1)[0].casefold() == artifacts.WORKFLOW_DIR:
        # Outside the repo is not this tool's business, and the plan itself
        # has to be writable or it could never be corrected.
        return None

    key = active(root)
    if key is None:
        return None
    plan, report = _plan_and_audit(key, root)
    if plan is None or not _has_passed(report):
        # Still being planned. Refusing here would block writing the plan's
        # own groundwork, and nothing has been reviewed that could be broken.
        return None

    why = audit.standing(report, plan)
    if why is not None:
        return _deny(
            f"{key}: the plan {why}, so no edit is covered by it. "
            f"Re-run: wb sdd audit {key} -- then retry this edit."
        )

    planned = _planned(plan)
    if relative in planned or relative in scope.claims(key, root):
        return None
    if companions.reason(relative, planned, companions.generated_globs(root)):
        # The same tie impl check accepts: a test, declaration, lockfile or
        # generated file following a planned one.
        return None

    return _deny(
        f"{relative} is not in the audited plan for {key}. "
        "Either leave it alone, or say why the plan was wrong, add the file to "
        f".workflow/{key}/sdd.json and re-run: wb sdd audit {key}"
    )


def stop(payload: dict, root: Path) -> dict | None:
    """A report on unverified work, or ``None`` when there is nothing to say."""
    setting = mode(root)
    if setting == OFF:
        return None

    key = active(root)
    if key is None:
        return None
    plan, report = _plan_and_audit(key, root)
    if plan is None or audit.standing(report, plan) is not None:
        return None

    planned = _planned(plan)
    if not planned & set(gitctx.changed_files(root)):
        return None

    evidence = _json(artifacts.ticket_dir(key, root) / "evidence.json")
    why = verify.standing(evidence, audit.digest(plan), gitctx.tree(root))
    if why is None:
        return None

    message = f"{key}: planned files changed but the verification {why}. Run: wb impl verify {key}"
    if setting == STRICT and not payload.get("stop_hook_active"):
        # stop_hook_active means this stop already follows a block: blocking
        # again would loop, so the second time it only reports.
        return {"decision": "block", "reason": message}
    return {"systemMessage": message}


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _planned(plan: dict) -> set[str]:
    return {
        str(item.get("path", "")).replace("\\", "/")
        for item in plan.get("files") or []
        if isinstance(item, dict) and item.get("path")
    }


def _has_passed(report: object) -> bool:
    """Has this plan ever passed? ``under_way`` survives a failed re-audit."""
    return isinstance(report, dict) and (report.get("verdict") == "pass" or bool(report.get("under_way")))


def _plan_and_audit(key: str, root: Path) -> tuple[dict | None, object]:
    directory = artifacts.ticket_dir(key, root)
    plan = _json(directory / "sdd.json")
    return (plan if isinstance(plan, dict) else None), _json(directory / "audit.json")


def _relative(root: Path, raw: str) -> str | None:
    """``raw`` as a repo-relative posix path, or ``None`` outside the repo."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _json(path: Path) -> object:
    """A missing or half-written artifact reads as absent; a hook never raises on one."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
