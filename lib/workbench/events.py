"""An append-only record of what the CLI was asked to do, and how it went.

``wb status --stats`` could only ever report a snapshot, because artifacts are
overwritten in place: a plan that failed its audit four times and passed on the
fifth is indistinguishable from one that passed first time. The snapshot
answers "where is work stuck now"; it cannot answer "where does this repo keep
losing time", which is the question worth acting on.

One line per invocation, appended locally. Deliberately small:

- **command and outcome only.** The group, the action, the exit code, the
  duration and a key if one was given; for a command that checks citations,
  how many landed on each verdict. No arguments, no output, no paths --
  those are where a secret or a customer name would end up.
- **local and disposable.** It lives under ``.workflow/``, which is ignored, and
  is capped by rewriting rather than by growing. Nothing is uploaded anywhere.
- **never load-bearing.** Every failure here is swallowed: a log that cannot be
  written is a lost statistic, and it must never be the reason a command fails.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from . import artifacts

LOG_NAME = ".events.jsonl"
# Enough to see a pattern, small enough to read and to rewrite cheaply.
MAX_EVENTS = 2000
TRIM_TO = 1500

# The same record, kept once per machine rather than once per checkout. The
# per-repo log answers "where does *this* repo lose time"; it cannot answer
# "where do I lose time", which is the question worth acting on -- a stage that
# is fine here and terrible in four other checkouts looks fine from inside any
# of them. Same rules apply: outcomes only, capped, never load-bearing.
GLOBAL_LOG_NAME = "events.jsonl"
MAX_GLOBAL_EVENTS = 8000
TRIM_GLOBAL_TO = 6000

# Commands whose outcome says something about the workflow. Reads that are
# pure inspection are skipped: logging every `status` would drown the signal
# in the command run to look at the signal.
TRACKED = {
    ("task", "get"),
    ("task", "new"),
    ("sdd", "audit"),
    ("impl", "check"),
    ("impl", "verify"),
    ("review", "gates"),
    ("commit", "check"),
    ("pr", "check"),
    ("cite", "check"),
}

# The audit's verdict names, spelled out rather than imported: this module is
# loaded by every command and must stay light. A test holds them to audit.py.
# Invented is a claim the code never supported -- the hallucination rate.
# Drifted is a claim that was true and whose line has since shifted -- the
# bookkeeping cost of implementing a plan. One exit code cannot tell them apart.
INVENTED = ("mismatch", "missing_file")
DRIFTED = ("moved", "out_of_range")

# Counts noted by the running command, taken by the next record(). A handler
# returns an exit code and nothing else, and the event is written after it.
_pending: dict[str, int] = {}


def path(cwd: Path | None = None) -> Path:
    return artifacts.root(cwd) / LOG_NAME


def global_path() -> Path:
    """Imported late: ``contexts`` is a heavier module than this one needs to be."""
    from . import contexts

    return contexts.home() / GLOBAL_LOG_NAME


def note_verdicts(verdicts: Iterable[str]) -> None:
    """Attach per-verdict citation counts to the event this command records.

    Names and numbers only: which verdicts, how many of each. Never the file, the
    line or the quote, which is where the no-arguments rule would break.
    """
    _pending.clear()
    _pending.update(Counter(str(verdict) for verdict in verdicts))


def record(group: str, action: str, key: str | None, exit_code: int, duration_ms: int) -> None:
    """Append one event, here and once per machine. Never raises."""
    # Taken before any early return, so counts never leak onto a later event.
    verdicts = dict(sorted(_pending.items()))
    _pending.clear()
    if (group, action) not in TRACKED:
        return
    if os.environ.get("WORKBENCH_NO_EVENTS"):
        return

    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "group": group,
        "action": action,
        "exit": int(exit_code),
        "ms": int(duration_ms),
    }
    if key:
        entry["key"] = key
    if verdicts:
        entry["verdicts"] = verdicts

    _append(_here, entry, MAX_EVENTS, TRIM_TO)
    _append(_everywhere, entry, MAX_GLOBAL_EVENTS, TRIM_GLOBAL_TO)


def _here(entry: dict) -> tuple[Path, dict]:
    return path(), entry


def _everywhere(entry: dict) -> tuple[Path, dict]:
    # The checkout's own name, not its path: a path is a fact about this
    # machine's disk, and the log is meant to be read rather than mapped.
    return global_path(), {**entry, "repo": artifacts.root().parent.name}


def _append(resolve, entry: dict, cap: int, trim_to: int) -> None:
    """One line, or nothing at all.

    ``resolve`` is called in here rather than by the caller because working out
    *where* to write can fail too -- reading a home directory, shelling out to
    git for the repo root. A log that cannot work out where it lives must still
    never be the reason a command failed.
    """
    try:
        target, payload = resolve(entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _trim(target, cap, trim_to)
    except Exception:  # noqa: BLE001 - deliberate, and the point of the module docstring
        pass  # a lost statistic is not a failure worth surfacing


def read(cwd: Path | None = None, *, everywhere: bool = False) -> list[dict]:
    """Every readable event. A malformed line is skipped, not fatal."""
    source = global_path() if everywhere else path(cwd)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return []

    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            events.append(entry)
    return events


def summarise(events: list[dict]) -> dict:
    """Attempts and failures per command, worst first.

    A command with a high failure rate is either a broken step or a step whose
    requirements are not being stated clearly enough up front. Either way it is
    where the time goes, and the snapshot cannot see it.
    """
    counts: dict[str, dict] = {}
    for entry in events:
        name = f"{entry.get('group', '?')} {entry.get('action', '?')}"
        row = counts.setdefault(name, {"runs": 0, "failed": 0, "ms": 0})
        row["runs"] += 1
        row["ms"] += int(entry.get("ms") or 0)
        if int(entry.get("exit") or 0) != 0:
            row["failed"] += 1

    for row in counts.values():
        row["avg_ms"] = row["ms"] // row["runs"] if row["runs"] else 0
        row.pop("ms")

    ordered = sorted(counts.items(), key=lambda kv: (kv[1]["failed"], kv[1]["runs"]), reverse=True)
    retried = sorted(
        ((name, row) for name, row in counts.items() if row["failed"] and row["runs"] > row["failed"]),
        key=lambda kv: kv[1]["failed"],
        reverse=True,
    )
    return {
        "events": len(events),
        "commands": dict(ordered),
        "most_retried": retried[0][0] if retried else "",
        "repos": _by_repo(events),
        "citations": _citations(events),
    }


def _citations(events: list[dict]) -> dict:
    """Citation verdicts summed over every event that recorded them.

    Lines written before counts were recorded carry none and are skipped, as is
    any value that is not a count: the log is read, never trusted.
    """
    runs = 0
    by_verdict: dict[str, int] = {}
    for entry in events:
        counts = entry.get("verdicts")
        if not isinstance(counts, dict):
            continue
        runs += 1
        for verdict, count in counts.items():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                by_verdict[str(verdict)] = by_verdict.get(str(verdict), 0) + count
    return {
        "runs": runs,
        "checked": sum(by_verdict.values()),
        "invented": sum(by_verdict.get(verdict, 0) for verdict in INVENTED),
        "drifted": sum(by_verdict.get(verdict, 0) for verdict in DRIFTED),
        "by_verdict": dict(sorted(by_verdict.items())),
    }


def _by_repo(events: list[dict]) -> dict:
    """Runs and failures per checkout, worst first. Empty for a local log.

    The one thing the global log knows that a local one cannot: whether a stage
    fails everywhere, which is a bad stage, or only here, which is this repo.
    """
    counts: dict[str, dict] = {}
    for entry in events:
        name = str(entry.get("repo") or "")
        if not name:
            continue
        row = counts.setdefault(name, {"runs": 0, "failed": 0})
        row["runs"] += 1
        if int(entry.get("exit") or 0) != 0:
            row["failed"] += 1
    return dict(sorted(counts.items(), key=lambda kv: (kv[1]["failed"], kv[1]["runs"]), reverse=True))


def render(summary: dict) -> str:
    if not summary["events"]:
        return "no history yet"

    lines = [f"{summary['events']} recorded command(s)"]
    width = max(len(name) for name in summary["commands"])
    for name, row in summary["commands"].items():
        failed = f"{row['failed']} failed" if row["failed"] else "clean"
        lines.append(f"  {name:<{width}}  {row['runs']:>3} run(s)  {failed:<10} {row['avg_ms']:>6} ms avg")

    repos = summary.get("repos") or {}
    if repos:
        lines.append("\nby checkout:")
        width = max(len(name) for name in repos)
        for name, row in repos.items():
            failed = f"{row['failed']} failed" if row["failed"] else "clean"
            lines.append(f"  {name:<{width}}  {row['runs']:>3} run(s)  {failed}")

    citations = summary.get("citations") or {}
    if citations.get("runs"):
        lines.append(f"\ncitations: {citations['checked']} checked in {citations['runs']} run(s)")
        lines.append(
            f"  invented  {citations['invented']:>4}  ({', '.join(INVENTED)}) -- a claim the code never supported"
        )
        lines.append(
            f"  drifted   {citations['drifted']:>4}  ({', '.join(DRIFTED)}) -- a true claim whose line has moved"
        )

    if summary["most_retried"]:
        lines.append(
            f"\n{summary['most_retried']} fails and then passes most often"
            " -- either the step is fragile or its requirements are not stated up front"
        )
    return "\n".join(lines)


def _trim(target: Path, cap: int = MAX_EVENTS, trim_to: int = TRIM_TO) -> None:
    """Cap by rewriting. A log that grows without bound is a log nobody keeps."""
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= cap:
        return
    try:
        target.write_text("\n".join(lines[-trim_to:]) + "\n", encoding="utf-8")
    except OSError:
        pass  # deliberate: an uncapped log is a slowdown, never a failed command
