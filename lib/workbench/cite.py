"""Checking the ``file:line`` citations in any artifact, not only in a plan.

The plan audit reopens every citation in ``sdd.json``. Everything else a
session writes about the code -- review findings, an incident's chain from
symptom to cause, a reply to a reviewer, an answer to a question -- was told to
quote the line it relies on, and nothing looked. This reads a markdown file,
finds its citations and runs each through the audit's own verifier.

A citation is the form ``sdd render`` already writes: a code span holding
``path:line``, then the quoted line in a second code span::

    `src/billing/checkout.py:142` — `charge = stripe.Charge.create(amount=total)`

A ``path:line`` span with no quote after it is a claim nothing can check, and
fails as ``unquoted``. Fenced blocks are skipped: they hold examples of the
form, not claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import audit, gitctx

UNQUOTED = "unquoted"

# `path:line`, then optionally a separator and `quote`. The path must carry a
# '.' or '/' so a time of day or a ratio in backticks is not read as a file.
_CITATION = re.compile(
    r"`(?P<path>[^`\s:]*[./][^`\s:]*):(?P<line>\d+)`"
    r"(?:[ \t]*(?:—|–|-|:)?[ \t]*`(?P<quote>[^`]+)`)?"
)
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Reference:
    at: int  # line in the document
    path: str
    line: int
    quote: str | None


@dataclass
class Result:
    reference: Reference
    verdict: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == audit.OK


def extract(text: str) -> list[Reference]:
    found: list[Reference] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _CITATION.finditer(line):
            found.append(Reference(number, match["path"], int(match["line"]), match["quote"]))
    return found


def check(text: str, root: Path, *, worktree: bool = False) -> list[Result]:
    """Verify every citation in ``text``.

    By default against HEAD, as a first audit reads a plan: a claim is about
    committed code. ``worktree`` reads the disk instead, for a review of a diff
    that is not committed yet -- still refusing ``.workflow/`` and anything git
    ignores, which is never the code under review.
    """
    anchor = "" if worktree else (gitctx.head(root) or "")
    results = []
    for reference in extract(text):
        if reference.quote is None:
            results.append(
                Result(reference, UNQUOTED, "no quoted line follows it; write `path:line` — `the line`")
            )
            continue
        if worktree and gitctx.is_ignored(root, reference.path):
            results.append(Result(reference, audit.UNCOMMITTED, "git ignores this file; it is not the code"))
            continue
        item = {"file": reference.path, "line": reference.line, "quote": reference.quote}
        finding = audit.verify_citation(item, root, anchor)
        results.append(Result(reference, finding.verdict, finding.detail))
    return results
