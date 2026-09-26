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

import hashlib
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import gitctx

MAX_FILE_BYTES = 5 * 1024 * 1024
CONTEXT_LINES = 2
# A quote shorter than this, whitespace aside, identifies no line: ``c`` or
# ``x = 1`` occurs somewhere in almost any file, so it verifies nothing.
MIN_QUOTE_CHARS = 8
# How many lines one quoted statement may wrap across.
MAX_QUOTE_SPAN = 8

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
# The citation points into this tool's own artifacts -- a plan quoting itself,
# or another ticket's notes. Text a session wrote about the code is not
# evidence about the code, however faithfully it is quoted.
ARTIFACT = "artifact"
# The quoted text is not in the commit the audit is anchored to: the file is
# untracked, ignored or added by the plan, or the line exists only as an
# uncommitted edit. A session can write any line it likes to disk and then
# quote it, so only committed code counts as evidence.
UNCOMMITTED = "uncommitted"
# An absence claim whose search matched: the thing said not to exist does.
FOUND = "found"
# How many matching files a failed absence claim names.
FOUND_SHOWN = 5

# Why a passing verdict no longer stands: the plan beside it is not the one
# it was reached on. Named, because callers answer it differently.
STALE = "changed since its audit"

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
    # The plan this verdict is about. A verdict is only worth trusting for the
    # document it was reached on; see standing().
    plan: str = ""
    findings: list[Finding] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    # Owed, but not by this audit: an incident's handover is enforced at the
    # PR, so an outage's hotfix does not wait on it. Never affects ``passed``.
    pending: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    # Every search an absence claim made the audit run, as run. Recorded for
    # passing claims too: "no match" is only worth what the search covered.
    searches: list[dict] = field(default_factory=list)

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
            "plan_sha256": self.plan,
            "citations_checked": len(self.findings),
            "citations_failed": len(self.failures),
            "findings": [f.to_dict() for f in self.findings if f.verdict not in self.passing],
            "structure": self.structure,
            "pending": self.pending,
            "missing_paths": self.missing_paths,
            "searches": self.searches,
        }


def run(doc: dict, root: Path, baseline: str | None = None) -> Report:
    """Audit a plan. ``baseline`` is the commit the plan was written against.

    It is passed only on a re-audit: the first audit of a plan is strict, and
    records the commit for the ones that follow. From then on the plan is
    treated as under way, so a citation the working tree no longer supports at
    the cited line is retried -- elsewhere in the file, then at that commit.

    That is what lets a plan be corrected while it is being implemented rather
    than only before, without loosening the check on a plan still being written.

    Either way a citation must point at code the anchor commit contains. The
    first audit reads the file as committed at HEAD, not as it sits on disk:
    evidence is what the repository says, not what a session last wrote.
    """
    from . import sdd

    report = Report(key=str(doc.get("key", "")), plan=digest(doc))
    report.baseline = baseline or gitctx.head(root) or ""
    report.under_way = bool(baseline)
    bound = sdd.light_max_lines(root)
    report.tier, report.tier_reason = sdd.tier(doc, bound)
    report.structure = sdd.validate(doc, bound)
    report.pending = sdd.pending(doc)

    for index, item in enumerate(doc.get("evidence") or []):
        if isinstance(item, dict) and item.get("kind") == sdd.ABSENCE:
            report.findings.append(_check_absence(index, item, root, report.baseline, report.searches))
            continue
        report.findings.append(_check(index, item, root, report.baseline, under_way=report.under_way))

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


def digest(doc: dict) -> str:
    """A fingerprint of a plan's content, blind to key order and formatting."""
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def standing(report: object, doc: object) -> str | None:
    """Why this plan may not be implemented from, or ``None`` when it may.

    The verdict alone is not enough. It was reached on one version of the plan,
    and a plan edited after it passed -- a wider file list, a weaker verify
    list -- would otherwise run on the strength of an audit it never had.
    An audit.json with no fingerprint is treated the same way: nothing ties it
    to the plan beside it.

    This catches drift, not forgery: whatever can write both files can make
    them agree.
    """
    if not isinstance(report, dict):
        return "has no audit result"
    if report.get("verdict") != "pass":
        return "did not pass its audit"
    if not isinstance(doc, dict) or report.get("plan_sha256") != digest(doc):
        return STALE
    return None


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


def verify_citation(item: dict, root: Path, anchor: str = "") -> Finding:
    """One ``{file, line, quote}`` citation with no plan around it.

    ``anchor`` is the commit to read the file at; empty reads the working
    tree. Either way the provenance rules hold: nothing under ``.workflow/``,
    and with an anchor, nothing that commit lacks.
    """
    return _check(0, item, root, anchor)


def _check(index: int, item: dict, root: Path, anchor: str = "", *, under_way: bool = False) -> Finding:
    """Decide one citation's verdict. ``anchor`` is the commit the audit runs
    against -- HEAD on a first audit, the recorded baseline after it -- and is
    empty outside a checkout, where nothing can say where a file came from."""
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
    relative = _relative(root, path)
    baseline = anchor if under_way else ""

    if relative.split("/", 1)[0].casefold() == gitctx.ARTIFACT_DIR:
        # Checked before anything else and without git: it holds outside a
        # checkout too, where provenance cannot be asked.
        finding.verdict = ARTIFACT
        finding.detail = "cites a workflow artifact; evidence is the code, not what a session wrote about it"
        return finding

    if path.is_dir():
        # ``git show <commit>:<dir>`` answers with a listing of the directory,
        # and a file name in it would otherwise verify as a quoted line.
        finding.verdict = MISSING_FILE
        finding.detail = "a directory, not a file; cite the file the claim is about"
        return finding

    committed = gitctx.file_at(root, anchor, relative) if anchor else None
    if anchor and committed is None and path.is_file():
        finding.verdict = UNCOMMITTED
        finding.detail = (
            f"not in commit {anchor[:8]}: the file is untracked, ignored or added since; cite committed code"
        )
        return finding

    if anchor and not under_way and committed is not None:
        # The first audit reads the commit, not the disk. An uncommitted edit
        # to a tracked file is text a session may have written; the committed
        # line is what the claim has to rest on.
        if len(committed) > MAX_FILE_BYTES:
            finding.verdict = UNREADABLE
            finding.detail = "file is too large to audit"
            return finding
        lines = committed.splitlines()
    else:
        if not path.is_file():
            # A plan that deletes or renames a file it cited leaves the citation
            # pointing at nothing. The claim was still true when it was written.
            if baseline and _at_baseline(root, baseline, relative, quote):
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
        if _uncommitted_only(anchor, under_way, path, quote):
            return _uncommitted_edit(finding, anchor)
        finding.verdict = OUT_OF_RANGE
        finding.detail = f"file has {len(lines)} lines"
        return finding

    if not quote:
        finding.verdict = MISMATCH
        finding.detail = "citation has no quote, so nothing could be verified"
        return finding

    if len(quote.replace(" ", "")) < MIN_QUOTE_CHARS:
        finding.verdict = MISMATCH
        finding.detail = f"quote is too short to identify a line; quote the whole of line {line_number}"
        return finding

    if _matches(quote, lines, line_number - 1):
        return finding

    # Tolerate drift, but never silently: an edit above the citation shifts it,
    # and the fix is to correct the number, not to loosen the check.
    for offset in range(1, CONTEXT_LINES + 1):
        for candidate in (line_number - 1 - offset, line_number - 1 + offset):
            if 0 <= candidate < len(lines) and _matches(quote, lines, candidate):
                finding.verdict = MOVED
                finding.detail = f"quote found at line {candidate + 1}; update the citation"
                return finding

    for index in range(len(lines)):
        if _matches(quote, lines, index):
            finding.verdict = MOVED
            finding.detail = f"quote found at line {index + 1}; update the citation"
            return finding

    # Only now: the working tree is always tried first, so a plan audited
    # before any change behaves exactly as it did. Reaching here means the
    # quote is nowhere in the current file, and the usual reason is that this
    # plan is being implemented and the line has already been rewritten.
    if baseline and _at_baseline(root, baseline, relative, quote):
        finding.verdict = BASELINE
        finding.detail = f"verified at baseline {baseline[:8]}; the working tree has moved since"
        return finding

    if _uncommitted_only(anchor, under_way, path, quote):
        return _uncommitted_edit(finding, anchor)

    finding.verdict = MISMATCH
    finding.detail = f"line {line_number} reads: {' '.join(lines[line_number - 1].split())[:120]!r}"
    return finding


def _check_absence(index: int, item: dict, root: Path, anchor: str, searches: list[dict]) -> Finding:
    """Run the search an absence claim rests on, at the anchor commit.

    The anchor, not the working tree, for the reason citations use it: a
    claim about the code is a claim about committed code, and a session must
    not be able to make one true by deleting a caller it has not committed.
    Under way the anchor is the baseline, so the claim is held to the code it
    was made about.
    """
    claim = str(item.get("claim", ""))
    finding = Finding(index=index, verdict=OK, file="", line=0, claim=claim)
    search = item.get("search")
    if not isinstance(search, dict) or not isinstance(search.get("pattern"), str):
        # validate() reports the shape; this pass must not raise on it.
        finding.verdict = UNREADABLE
        finding.detail = "absence claim has no search to run"
        return finding
    if not anchor:
        finding.verdict = UNREADABLE
        finding.detail = "an absence claim needs a commit to search; this is not a git checkout"
        return finding

    pattern = search["pattern"]
    paths = [str(path).replace("\\", "/") for path in search.get("paths") or []]
    allowed = {str(path).replace("\\", "/") for path in search.get("allow") or []}
    word = search.get("word") is True
    command = shlex.join(["git", *gitctx.grep_command(anchor, pattern, paths, word=word)])

    matched = gitctx.grep_files(root, anchor, pattern, paths, word=word)
    if matched is None:
        searches.append({"index": index, "command": command, "error": True})
        finding.verdict = UNREADABLE
        finding.detail = f"the search failed to run: {command}"
        return finding

    outside = [path for path in matched if path not in allowed]
    searches.append(
        {"index": index, "command": command, "matched": len(matched), "allowed": len(matched) - len(outside)}
    )
    if outside:
        shown = ", ".join(outside[:FOUND_SHOWN])
        more = f" and {len(outside) - FOUND_SHOWN} more" if len(outside) > FOUND_SHOWN else ""
        finding.verdict = FOUND
        finding.detail = f"{pattern!r} occurs in {shown}{more}; the claim is false, or allow the file and say why"
    return finding


def _uncommitted_only(anchor: str, under_way: bool, path: Path, quote: str) -> bool:
    """On a first audit, is the quote on disk although the commit lacks it?

    Only asked once the committed file has already failed the citation, to
    name the reason: "mismatch" would send the author looking for a typo when
    the line is real and simply not committed.
    """
    if not anchor or under_way or not quote or not path.is_file():
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(_matches(quote, lines, index) for index in range(len(lines)))


def _uncommitted_edit(finding: Finding, anchor: str) -> Finding:
    finding.verdict = UNCOMMITTED
    finding.detail = f"the quote is only in uncommitted changes, not in commit {anchor[:8]}; cite committed code"
    return finding


def _at_baseline(root: Path, baseline: str, relative: str, quote: str) -> bool:
    """Was this quote in the file at the baseline commit?

    The line number is deliberately not checked. A citation that has survived
    into implementation has almost certainly shifted, and the question worth
    answering is whether the claim was ever true -- not whether the author kept
    the numbering up to date while working.
    """
    content = gitctx.file_at(root, baseline, relative)
    if content is None:
        return False
    lines = content.splitlines()
    return any(_matches(quote, lines, index) for index in range(len(lines)))


def _matches(quote: str, lines: list[str], index: int) -> bool:
    """Does the text starting at ``lines[index]`` support this quote?

    A citation may quote a statement that wraps, so the quote may run on into
    the lines below -- but it has to *start* on the cited line and every word
    of it has to be there. The earlier rule accepted any quote that merely
    contained the cited line, which let a real line followed by invented text
    verify: exactly the failure this module exists to catch.
    """
    joined = " ".join(lines[index].split())
    if not joined or not quote:
        return False
    first = len(joined)
    if quote in joined:
        return True
    for following in lines[index + 1 : index + MAX_QUOTE_SPAN]:
        joined = f"{joined} {' '.join(following.split())}".strip()
        position = joined.find(quote)
        if position != -1:
            return position < first
    return False


def _resolve(root: Path, raw: str) -> Path:
    """Resolve a citation path inside the repo. Escapes resolve to nothing."""
    candidate = (root / raw.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return root / "__outside_repo__"
    return candidate


def _relative(root: Path, path: Path) -> str:
    """A resolved citation path as git names it: repo-relative, forward slashes."""
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name
