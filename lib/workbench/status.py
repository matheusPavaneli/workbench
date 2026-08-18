"""Where a piece of work stands, read off the artifacts it has produced.

The whole design chains through ``.workflow/<KEY>/`` rather than through the
conversation, which is what makes a plan cost tokens once instead of once per
consumer. That only pays if a *new* session can pick the thread back up, and
until this module existed it could not: the state was on disk but nothing read
it back, so resuming meant opening four files and inferring.

The pipeline below is the one fixed thing. Each stage names the artifact that
proves it happened, how to say so in one line, and the command that produces it
-- so "what now" is a lookup, not a judgement call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import artifacts, gitctx, sdd as sdd_lib

# Stage states, worst-first when deciding what to report as blocking.
FAIL = "fail"
PENDING = "pending"
TODO = "todo"
OK = "ok"
SKIP = "n/a"

_MARK = {OK: "ok", FAIL: "FAIL", PENDING: "part", TODO: "--", SKIP: "n/a"}


# Which skill does the work each stage names. A session that knows the command
# still has to know who runs it, and ten skills is more than anybody keeps in
# their head -- so the answer travels with the stage rather than being looked up.
SKILL_FOR = {
    "triage": "triage-task",
    "plan": "plan-change",
    "audit": "plan-change",
    "scope": "implement-change",
    "verify": "implement-change",
    "handover": "write-handover",
    "commit": "write-commit",
    "pr": "draft-pr",
    "review": "address-review",
}


@dataclass
class Stage:
    name: str
    state: str
    detail: str = ""
    command: str = ""

    @property
    def skill(self) -> str:
        return SKILL_FOR.get(self.name, "")

    @property
    def done(self) -> bool:
        return self.state in (OK, SKIP)


@dataclass
class Status:
    key: str
    title: str = ""
    kind: str = ""
    provider: str = ""
    stages: list[Stage] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.stages = self.stages or []

    @property
    def blocked(self) -> Stage | None:
        return next((s for s in self.stages if s.state == FAIL), None)

    @property
    def next_stage(self) -> Stage | None:
        return next((s for s in self.stages if not s.done), None)

    @property
    def next_command(self) -> str:
        stage = self.blocked or self.next_stage
        return stage.command if stage else ""

    @property
    def headline(self) -> str:
        """The furthest stage actually reached, for the one-line listing."""
        reached = [s.name for s in self.stages if s.state in (OK, PENDING)]
        return reached[-1] if reached else "not started"

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "key": self.key,
            "title": self.title,
            "type": self.kind,
            "provider": self.provider,
            "stages": [
                {"name": s.name, "state": s.state, "detail": s.detail, "command": s.command, "skill": s.skill}
                for s in self.stages
            ],
            "next": self.next_command,
        }


def keys(cwd: Path | None = None) -> list[str]:
    """Every key with a workflow directory, most recently touched first."""
    root = artifacts.root(cwd)
    if not root.is_dir():
        return []
    found = [
        path
        for path in root.iterdir()
        if path.is_dir() and path.name not in {"tasks", ".cache"} and any(path.iterdir())
    ]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in found]


# A branch name carries the key in every convention this tool detects, so the
# ticket a session is working on is a fact about the checkout, not a question.
#
# Anchored to the start of a path segment, because unanchored it read a key out
# of any branch name with a number in it: "chore/bump-node-20" became NODE-20,
# and since an unknown key reads as untouched work rather than as an error,
# `wb next` then reported that instead of the ticket actually in flight.
_KEY_IN_BRANCH = re.compile(r"(?:^|/)([A-Za-z]{2,}-\d+)")


def key_from_branch(available: list[str], cwd: Path | None = None) -> str | None:
    """The key this checkout is on, or ``None`` when the branch does not say.

    A key with artifacts already on disk wins over one merely spelled in the
    branch name: the artifacts are what a next step reads.
    """
    here = cwd or Path.cwd()
    name = gitctx.branch(gitctx.repo_root(here) or here)
    if not name:
        return None

    # Longest first, and on a boundary. Plain substring matching made ABC-1
    # match "feature/ABC-12-thing", so whichever of the two had been touched
    # more recently won -- the precise failure this resolution exists to avoid.
    for key in sorted(available, key=len, reverse=True):
        if _spelled_in(key, name):
            return key
    return None


def _spelled_in(key: str, branch: str) -> bool:
    """``key`` appears in ``branch`` as a whole token, not as a prefix of a longer one."""
    return re.search(rf"(?:^|[^A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", branch, re.IGNORECASE) is not None


def pick(key: str | None = None, cwd: Path | None = None) -> tuple[Status, str] | None:
    """The one piece of work a session should act on, and why it was chosen.

    Explicit key, then a branch naming work that exists, then the most recently
    touched ticket, and only then a key read out of the branch name. Narrowest
    evidence first: an argument always wins, a checkout on a ticket branch never
    reports a different ticket, and a guessed key never displaces real work --
    it is the answer only when there is nothing else to answer with.
    """
    if key:
        return read(key, cwd), "named"

    available = keys(cwd)
    from_branch = key_from_branch(available, cwd)
    if from_branch:
        return read(from_branch, cwd), "branch"
    if available:
        return read(available[0], cwd), "most recent"

    here = cwd or Path.cwd()
    name = gitctx.branch(gitctx.repo_root(here) or here) or ""
    match = _KEY_IN_BRANCH.search(name)
    if match:
        return read(match.group(1).upper(), cwd), "branch"
    return None


def read(key: str, cwd: Path | None = None) -> Status:
    key = artifacts.validate_key(key)
    directory = artifacts.ticket_dir(key, cwd)
    root = gitctx.repo_root(cwd or Path.cwd()) or (cwd or Path.cwd())

    triage = _json(directory / "triage.json")
    frame = directory / "frame.md"
    plan = _json(directory / "sdd.json")
    audit = _json(directory / "audit.json")
    evidence = _json(directory / "evidence.json")

    status = Status(
        key=key,
        title=str((triage or {}).get("title") or (plan or {}).get("objective") or ""),
        kind=str((triage or {}).get("type") or ""),
        provider=str((triage or {}).get("provider") or ""),
    )
    status.stages = [
        _intake(key, directory, triage, frame),
        _plan(key, plan),
        _audit(key, audit, plan),
        _scope(key, plan, audit, root),
        _evidence(key, evidence, audit),
        _handover(key, directory, plan, status.kind),
        _artifact(directory / "commit.txt", "commit", f"wb commit check {key}"),
        _artifact(directory / "pr.md", "pr", f"wb pr context {key}"),
    ]
    response = directory / "review-response.md"
    if response.is_file():
        status.stages.append(_artifact(response, "review", ""))
    return status


# ---- stages -------------------------------------------------------------


def _intake(key: str, directory: Path, triage: dict | None, frame: Path) -> Stage:
    if triage:
        open_questions = _count_lines(directory / "questions.md")
        detail = f"{triage.get('provider', '?')} {triage.get('status', '')}".strip()
        if open_questions:
            detail = f"{detail}, {open_questions} open question(s)"
        return Stage("triage", OK, detail, "")
    if frame.is_file():
        return Stage("triage", OK, "framed as an idea", "")
    return Stage("triage", TODO, "", f"wb task get {key}")


def _plan(key: str, plan: dict | None) -> Stage:
    if not plan:
        return Stage("plan", TODO, "", f"(plan-change) then wb sdd audit {key}")
    files = len(plan.get("files") or [])
    steps = len(plan.get("steps") or [])
    checks = len(plan.get("verify") or [])
    questions = len(plan.get("questions") or [])
    detail = f"{files} file(s), {steps} step(s), {checks} verify"
    if questions:
        detail += f", {questions} open question(s)"
    return Stage("plan", OK, detail, "")


def _audit(key: str, audit: dict | None, plan: dict | None) -> Stage:
    if not plan:
        return Stage("audit", TODO, "", "")
    if not audit:
        return Stage("audit", TODO, "", f"wb sdd audit {key}")
    checked = audit.get("citations_checked", 0)
    if audit.get("verdict") == "pass":
        return Stage("audit", OK, f"{checked} citation(s) verified", "")
    failed = audit.get("citations_failed", 0)
    reasons = audit.get("structure") or []
    detail = f"{failed} of {checked} citation(s) failed" if failed else "; ".join(reasons[:2]) or "structure"
    return Stage("audit", FAIL, detail, f"fix the plan, then: wb sdd audit {key}")


def _scope(key: str, plan: dict | None, audit: dict | None, root: Path) -> Stage:
    """Derived, not stored: the working tree is the only truth about scope."""
    if not plan or not audit or audit.get("verdict") != "pass":
        return Stage("scope", TODO, "", "")

    planned = {str(item.get("path", "")).replace("\\", "/") for item in plan.get("files") or []}
    planned.discard("")
    if not planned:
        return Stage("scope", SKIP, "the plan lists no files", "")

    changed = set(gitctx.changed_files(root))
    touched = changed & planned
    stray = changed - planned

    if stray:
        return Stage("scope", FAIL, f"{len(stray)} file(s) outside the plan", f"wb impl check {key}")
    if not touched:
        return Stage("scope", TODO, f"0 of {len(planned)} planned file(s) changed", f"wb impl check {key}")
    if touched < planned:
        return Stage("scope", PENDING, f"{len(touched)} of {len(planned)} planned file(s) changed", "")
    return Stage("scope", OK, f"all {len(planned)} planned file(s) changed", "")


def _evidence(key: str, evidence: dict | None, audit: dict | None) -> Stage:
    if not audit or audit.get("verdict") != "pass":
        return Stage("verify", TODO, "", "")
    if not evidence:
        return Stage("verify", TODO, "", f"wb impl verify {key}")
    results = evidence.get("results") or []
    refused = evidence.get("refused") or []
    if evidence.get("verdict") == "pass":
        return Stage("verify", OK, f"{len(results)} command(s) passed", "")
    failed = sum(1 for r in results if not r.get("ok"))
    parts = [f"{failed} failed"] if failed else []
    if refused:
        parts.append(f"{len(refused)} refused")
    return Stage("verify", FAIL, ", ".join(parts) or "no commands ran", f"wb impl verify {key}")


def _handover(key: str, directory: Path, plan: dict | None, kind: str) -> Stage:
    """Only a stage where the audit already requires one; otherwise it is not owed."""
    required = kind.lower() in sdd_lib.HANDOVER_TYPES or key.startswith("incident-")
    if not required:
        return Stage("handover", SKIP, "", "")
    if (directory / "handover.md").is_file():
        return Stage("handover", OK, "", "")
    if not plan:
        return Stage("handover", TODO, f"required for {kind or 'incident'} work", "")
    return Stage("handover", TODO, f"required for {kind or 'incident'} work", f"wb sdd handover {key}")


def _artifact(path: Path, name: str, command: str) -> Stage:
    if path.is_file():
        return Stage(name, OK, "", "")
    return Stage(name, TODO, "", command)


# ---- rendering ----------------------------------------------------------


def render(status: Status, *, width: int = 9) -> str:
    lines = []
    head = f"{status.key}"
    if status.title:
        head += f"  {status.title}"
    marks = [f"{status.kind}" if status.kind else "", status.provider]
    marks = [m for m in marks if m]
    if marks:
        head += f"  [{', '.join(marks)}]"
    lines.append(head)

    for stage in status.stages:
        if stage.state == SKIP and not stage.detail:
            continue
        row = f"  {stage.name:<{width}} {_MARK[stage.state]:<5}"
        lines.append(f"{row} {stage.detail}".rstrip())

    if status.next_command:
        lines.append(f"  next: {status.next_command}")
    else:
        lines.append("  next: nothing outstanding")
    return "\n".join(lines)


def _lines(*parts: str) -> str:
    return "\n".join(parts)


def render_next(status: Status, origin: str) -> str:
    """One line of state, one line of command. Nothing an agent has to parse.

    ``render`` prints eight stages because a person reading it wants the shape
    of the work. A session that only wants the next move pays for eight lines
    to use one, every time it asks.
    """
    stage = status.blocked or status.next_stage
    head = f"{status.key}"
    if origin != "named":
        head += f"  ({origin})"
    if stage is None:
        return _lines(f"{head}  complete", "  next: nothing outstanding")

    state = "BLOCKED" if status.blocked else stage.state
    detail = f"  {stage.detail}" if stage.detail else ""
    command = stage.command or (f"run the {stage.skill} skill" if stage.skill else "nothing outstanding")
    if stage.command and stage.skill:
        command = f"{command}   ({stage.skill})"
    return _lines(f"{head}  {stage.name} {state}{detail}", f"  next: {command}")


def next_dict(status: Status, origin: str) -> dict:
    stage = status.blocked or status.next_stage
    return {
        "schema": 1,
        "key": status.key,
        "origin": origin,
        "stage": stage.name if stage else "",
        "state": "blocked" if status.blocked else (stage.state if stage else "complete"),
        "reason": stage.detail if stage else "",
        "command": stage.command if stage else "",
        "skill": stage.skill if stage else "",
    }


def summarise(items: list[Status]) -> dict:
    """Where work piles up, across every ticket in this checkout.

    A snapshot, not a history: artifacts are overwritten in place, so this says
    where things are stuck now, not how often they got stuck. That is the
    cheaper question and usually the one being asked -- three tickets blocked
    at the same stage says more about the stage than about the tickets.
    """
    stuck: dict[str, int] = {}
    blocked: dict[str, int] = {}
    for status in items:
        target = status.blocked or status.next_stage
        if target is None:
            stuck["complete"] = stuck.get("complete", 0) + 1
            continue
        stuck[target.name] = stuck.get(target.name, 0) + 1
        if status.blocked:
            blocked[target.name] = blocked.get(target.name, 0) + 1

    return {
        "tickets": len(items),
        "waiting_at": dict(sorted(stuck.items(), key=lambda kv: kv[1], reverse=True)),
        "blocked_at": dict(sorted(blocked.items(), key=lambda kv: kv[1], reverse=True)),
    }


def render_summary(summary: dict) -> str:
    if not summary["tickets"]:
        return "no work in progress"

    lines = [f"{summary['tickets']} ticket(s) in this checkout"]
    lines.append("\nwaiting at:")
    for name, count in summary["waiting_at"].items():
        failing = summary["blocked_at"].get(name, 0)
        note = f"  ({failing} failing)" if failing else ""
        lines.append(f"  {name:<10} {count}{note}")

    if summary["blocked_at"]:
        worst = next(iter(summary["blocked_at"]))
        lines.append(f"\n{worst} is where this repo is losing time; fix that stage first")
    return "\n".join(lines)


def render_list(items: list[Status]) -> str:
    if not items:
        return "no work in progress"
    width = max(len(s.key) for s in items)
    lines = []
    for status in items:
        blocked = status.blocked
        state = f"BLOCKED at {blocked.name}" if blocked else status.headline
        title = status.title[:48]
        lines.append(f"{status.key:<{width}}  {state:<16}  {title}")
    return "\n".join(lines)


# ---- reading ------------------------------------------------------------


def _json(path: Path) -> dict | None:
    """A corrupt artifact reads as absent: status must never be the thing that fails."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _count_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip().startswith(("-", "*", "1.")))
