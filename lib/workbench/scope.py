"""Attributing a changed file to the ticket that accounts for it.

The scope guard compares the working tree against one plan's file list. That is
right when one ticket is in flight and wrong the rest of the time: a second
ticket in progress -- the normal state of a working day -- read as scope creep
on the first, and a guard that fails on ordinary work is a guard people learn
to ignore.

A file another plan already lists is accounted for. It is not this ticket's
work, but it is not unexplained either, and those are different findings.

The one rule that keeps this from being a hole through the guard: **only an
audited plan may account for a file.** An unaudited plan is a file somebody
wrote, and treating it as an excuse would let anyone silence the scope check by
listing a path in a document nothing verified.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import artifacts


def claims(exclude: str, cwd: Path | None = None) -> dict[str, list[str]]:
    """Map every path claimed by another *audited* plan to the keys claiming it.

    ``exclude`` is the ticket being checked; its own plan is never counted, or
    its own files would come back reported as belonging somewhere else.
    """
    root = artifacts.root(cwd)
    if not root.is_dir():
        return {}

    claimed: dict[str, list[str]] = {}
    excluded = artifacts.validate_key(exclude) if exclude else ""

    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name in {"tasks", ".cache"} or directory.name == excluded:
            continue
        if not _audit_passed(directory):
            continue
        for path in _paths(directory / "sdd.json"):
            claimed.setdefault(path, []).append(directory.name)

    return claimed


def _audit_passed(directory: Path) -> bool:
    report = _read(directory / "audit.json")
    return isinstance(report, dict) and report.get("verdict") == "pass"


def _paths(path: Path) -> list[str]:
    doc = _read(path)
    if not isinstance(doc, dict):
        return []
    found = []
    for item in doc.get("files") or []:
        if isinstance(item, dict) and item.get("path"):
            found.append(str(item["path"]).replace("\\", "/"))
    return found


def _read(path: Path):
    """A malformed artifact accounts for nothing. It never raises: the scope
    guard must fail on real deviations, not on a half-written file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
