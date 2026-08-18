"""Citation audit. The mechanism the whole plan rests on.

A plan states claims about a codebase. Each claim carries a ``file:line`` and
the text of that line. This module reopens every one of them and checks the
text is really there.

It is a script, deliberately, and not a second pass by the model: a model
auditing its own work confirms its own errors. Reading bytes off disk cannot.

Failure is not advisory. An SDD with an unverified citation does not pass, and
the skill does not proceed to implementation on one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitctx

MAX_FILE_BYTES = 5 * 1024 * 1024
CONTEXT_LINES = 2
# A line shorter than this cannot, on its own, support a longer quotation.
# Without a floor, ``)`` or an empty line matches anything.
MIN_LINE_FOR_REVERSE_MATCH = 10

OK = "ok"
# The claim was true of the tree the plan was written against, but the working
# tree has since moved -- almost always because the plan is being implemented.
# A separate verdict rather than a silent "ok": a reader has to be able to tell
# which citations no longer describe the current code.
BASELINE = "baseline"
MOVED = "moved"
MISMATCH = "mismatch"
MISSING_FILE = "missing_file"
OUT_OF_RANGE = "out_of_range"
UNREADABLE = "unreadable"

PASSING = {OK, BASELINE}
# Passing only once a plan is under way. On the first audit a wrong line number
# is a defect the author should fix while the plan is cheap to change; after
# that, every edit shifts the lines below it and chasing the numbers is the
# churn this baseline exists to remove.
PASSING_WHEN_UNDER_WAY = {MOVED}


@dataclass
class Finding:
    index: int
    verdict: str
    file: str
    line: int
    claim: str
    detail: str = ""

    def to_dict(self) -> dict:
        data = {
            "index": self.index,
            "verdict": self.verdict,
            "file": self.file,
            "line": self.line,
            "claim": self.claim,
        }
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class Report:
    key: str
    # The commit citations fall back to. Recorded so a re-audit anchors to the
    # same point for the whole life of a plan, however far implementation has
    # gone.
    baseline: str = ""
    # True once this plan has been audited before, which is the signal that
    # implementation may have started.
    under_way: bool = False
    # The rigour tier the plan qualified for, and why. Recorded rather than
    # applied silently: a waived section must be visible in the artifact, or
    # "this plan has no steps" reads as an omission instead of a decision.
    tier: str = "standard"
    tier_reason: str = ""
    findings: list[Finding] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)

    @property
    def passing(self) -> set:
        return PASSING | PASSING_WHEN_UNDER_WAY if self.under_way else PASSING

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict not in self.passing]

    @property
    def passed(self) -> bool:
        return not self.failures and not self.structure and not self.missing_paths

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "key": self.key,
            "verdict": "pass" if self.passed else "fail",
            "tier": self.tier,
            "tier_reason": self.tier_reason,
            "baseline": self.baseline,
            "under_way": self.under_way,
            "citations_checked": len(self.findings),
            "citations_failed": len(self.failures),
            "findings": [f.to_dict() for f in self.findings if f.verdict not in self.passing],
            "structure": self.structure,
            "missing_paths": self.missing_paths,
        }


def run(doc: dict, root: Path, baseline: str | None = None) -> Report:
    """Audit a plan. ``baseline`` is the commit the plan was written against.

    It is passed only on a re-audit: the first audit of a plan is strict, and
    records the commit for the ones that follow. From then on the plan is
    treated as under way, so a citation the working tree no longer supports at
    the cited line is retried -- elsewhere in the file, then at that commit.

    That is what lets a plan be corrected while it is being implemented rather
    than only before, without loosening the check on a plan still being written.
    """
    from . import sdd

    report = Report(key=str(doc.get("key", "")))
    report.baseline = baseline or gitctx.head(root) or ""
    report.under_way = bool(baseline)
    report.tier, report.tier_reason = sdd.tier(doc)
    report.structure = sdd.validate(doc)

    for index, item in enumerate(doc.get("evidence") or []):
        report.findings.append(_check(index, item, root, baseline))

    # A plan may only claim to edit files that exist. Claiming to edit a file
    # that is not there is the same class of error as a false citation.
    for item in doc.get("files") or []:
        if not isinstance(item, dict):
            continue  # validate() reports the shape; this pass must not raise on it
        path = str(item.get("path", ""))
        if not path or item.get("change") == "add":
            continue
        if not _resolve(root, path).is_file():
            report.missing_paths.append(path)

    report.structure.extend(_preset_problems(doc, root))
    return report


def _preset_problems(doc: dict, root: Path) -> list[str]:
    """Is this plan held to the bar its own files demand?

    Only checked where the repo has said what its bars are -- a recorded preset
    or a ``preset_paths`` mapping. Absent that, detection is advice and this
    stays quiet: a plan should not fail an audit over a guess nobody made.

    Where the repo *has* said, the plan cannot come in under it. A monorepo
    change that touches the billing package while declaring the playground's
    preset is the case this exists for, and it is invisible to every other
    check: the citations are real, the files exist, and the bar is wrong.
    """
    from . import profile

    config = profile.repo_config(root)
    mapping = profile.preset_paths(root)
    recorded = config.get("preset") if config.get("preset") in profile.RANK else None
    if not mapping and not recorded:
        return []

    declared = str(doc.get("preset", ""))
    if declared not in profile.RANK:
        return [f"preset {declared!r} is not one of: {', '.join(profile.PRESETS)}"]

    paths = [
        str(item.get("path", ""))
        for item in doc.get("files") or []
        if isinstance(item, dict) and item.get("path")
    ]
    required, hits = profile.resolve_for(paths, mapping, recorded or declared)
    if profile.RANK[declared] >= profile.RANK[required]:
        return []

    where = ", ".join(sorted(hits.get(required, []))[:3]) or "this repo"
    return [
        f"the plan declares preset {declared} but {where} is held to {required}: "
        f"raise the preset, or split the change"
    ]


def _check(index: int, item: dict, root: Path, baseline: str | None = None) -> Finding:
    if not isinstance(item, dict):
        # Reported as a finding rather than raised: a malformed plan must fail
        # the audit, and failing it is not the same as crashing the checker.
        return Finding(
            index=index,
            verdict=MISMATCH,
            file="",
            line=0,
            claim="",
            detail=f"evidence[{index}] must be an object, not {type(item).__name__}",
        )

    raw_path = str(item.get("file", ""))
    claim = str(item.get("claim", ""))
    quote = " ".join(str(item.get("quote", "")).split())
    try:
        line_number = int(item.get("line", 0))
    except (TypeError, ValueError):
        line_number = 0

    finding = Finding(index=index, verdict=OK, file=raw_path, line=line_number, claim=claim)
    path = _resolve(root, raw_path)

    if not path.is_file():
        # A plan that deletes or renames a file it cited leaves the citation
        # pointing at nothing. The claim was still true when it was written.
        if baseline and _at_baseline(root, baseline, raw_path, quote):
            finding.verdict = BASELINE
            finding.detail = f"verified at baseline {baseline[:8]}; the file is gone from the working tree"
            return finding
        finding.verdict = MISSING_FILE
        finding.detail = "no such file; the path in the citation does not exist"
        return finding

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            finding.verdict = UNREADABLE
            finding.detail = "file is too large to audit"
            return finding
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        finding.verdict = UNREADABLE
        finding.detail = str(exc)
        return finding

    if line_number < 1 or line_number > len(lines):
        finding.verdict = OUT_OF_RANGE
        finding.detail = f"file has {len(lines)} lines"
        return finding

    if not quote:
        finding.verdict = MISMATCH
        finding.detail = "citation has no quote, so nothing could be verified"
        return finding

    if _matches(quote, lines[line_number - 1]):
        return finding

    # Tolerate drift, but never silently: an edit above the citation shifts it,
    # and the fix is to correct the number, not to loosen the check.
    for offset in range(1, CONTEXT_LINES + 1):
        for candidate in (line_number - 1 - offset, line_number - 1 + offset):
            if 0 <= candidate < len(lines) and _matches(quote, lines[candidate]):
                finding.verdict = MOVED
                finding.detail = f"quote found at line {candidate + 1}; update the citation"
                return finding

    for number, text in enumerate(lines, start=1):
        if _matches(quote, text):
            finding.verdict = MOVED
            finding.detail = f"quote found at line {number}; update the citation"
            return finding

    # Only now: the working tree is always tried first, so a plan audited
    # before any change behaves exactly as it did. Reaching here means the
    # quote is nowhere in the current file, and the usual reason is that this
    # plan is being implemented and the line has already been rewritten.
    if baseline and _at_baseline(root, baseline, raw_path, quote):
        finding.verdict = BASELINE
        finding.detail = f"verified at baseline {baseline[:8]}; the working tree has moved since"
        return finding

    finding.verdict = MISMATCH
    finding.detail = f"line {line_number} reads: {' '.join(lines[line_number - 1].split())[:120]!r}"
    return finding


def _at_baseline(root: Path, baseline: str, raw_path: str, quote: str) -> bool:
    """Was this quote in the file at the baseline commit?

    The line number is deliberately not checked. A citation that has survived
    into implementation has almost certainly shifted, and the question worth
    answering is whether the claim was ever true -- not whether the author kept
    the numbering up to date while working.
    """
    content = gitctx.file_at(root, baseline, raw_path.replace("\\", "/"))
    if content is None:
        return False
    return any(_matches(quote, line) for line in content.splitlines())


def _matches(quote: str, line: str) -> bool:
    """Does this line support this quote?

    The reverse containment is deliberate but guarded: a citation may quote a
    statement that wraps across lines. An empty or near-empty line is contained
    in every string, so without the guard a citation pointing at a blank line
    passes every time -- which is precisely the failure this module exists to
    catch.
    """
    normalised = " ".join(line.split())
    if not normalised or not quote:
        return False
    if quote in normalised:
        return True
    return len(normalised) >= MIN_LINE_FOR_REVERSE_MATCH and normalised in quote


def _resolve(root: Path, raw: str) -> Path:
    """Resolve a citation path inside the repo. Escapes resolve to nothing."""
    candidate = (root / raw.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return root / "__outside_repo__"
    return candidate
