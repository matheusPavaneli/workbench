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

MAX_FILE_BYTES = 5 * 1024 * 1024
CONTEXT_LINES = 2
# A line shorter than this cannot, on its own, support a longer quotation.
# Without a floor, ``)`` or an empty line matches anything.
MIN_LINE_FOR_REVERSE_MATCH = 10

OK = "ok"
MOVED = "moved"
MISMATCH = "mismatch"
MISSING_FILE = "missing_file"
OUT_OF_RANGE = "out_of_range"
UNREADABLE = "unreadable"

PASSING = {OK}


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
    # The rigour tier the plan qualified for, and why. Recorded rather than
    # applied silently: a waived section must be visible in the artifact, or
    # "this plan has no steps" reads as an omission instead of a decision.
    tier: str = "standard"
    tier_reason: str = ""
    findings: list[Finding] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict not in PASSING]

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
            "citations_checked": len(self.findings),
            "citations_failed": len(self.failures),
            "findings": [f.to_dict() for f in self.findings if f.verdict not in PASSING],
            "structure": self.structure,
            "missing_paths": self.missing_paths,
        }


def run(doc: dict, root: Path) -> Report:
    from . import sdd

    report = Report(key=str(doc.get("key", "")))
    report.tier, report.tier_reason = sdd.tier(doc)
    report.structure = sdd.validate(doc)

    for index, item in enumerate(doc.get("evidence") or []):
        report.findings.append(_check(index, item, root))

    # A plan may only claim to edit files that exist. Claiming to edit a file
    # that is not there is the same class of error as a false citation.
    for item in doc.get("files") or []:
        path = str(item.get("path", ""))
        if not path or item.get("change") == "add":
            continue
        if not _resolve(root, path).is_file():
            report.missing_paths.append(path)

    return report


def _check(index: int, item: dict, root: Path) -> Finding:
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

    finding.verdict = MISMATCH
    finding.detail = f"line {line_number} reads: {' '.join(lines[line_number - 1].split())[:120]!r}"
    return finding


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
