"""An append-only record of what the CLI was asked to do, and how it went.

``wb status --stats`` could only ever report a snapshot, because artifacts are
overwritten in place: a plan that failed its audit four times and passed on the
fifth is indistinguishable from one that passed first time. The snapshot
answers "where is work stuck now"; it cannot answer "where does this repo keep
losing time", which is the question worth acting on.

One line per invocation, appended locally. Deliberately small:

- **command and outcome only.** The group, the action, the exit code, the
  duration and a key if one was given. No arguments, no output, no paths --
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
from pathlib import Path

from . import artifacts

LOG_NAME = ".events.jsonl"
# Enough to see a pattern, small enough to read and to rewrite cheaply.
MAX_EVENTS = 2000
TRIM_TO = 1500

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
}


def path(cwd: Path | None = None) -> Path:
    return artifacts.root(cwd) / LOG_NAME


def record(group: str, action: str, key: str | None, exit_code: int, duration_ms: int) -> None:
    """Append one event. Never raises."""
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

    try:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim(target)
    except OSError:
        pass  # deliberate: a lost statistic is not a failure worth surfacing


def read(cwd: Path | None = None) -> list[dict]:
    """Every readable event. A malformed line is skipped, not fatal."""
    try:
        text = path(cwd).read_text(encoding="utf-8")
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
    }


def render(summary: dict) -> str:
    if not summary["events"]:
        return "no history yet"

    lines = [f"{summary['events']} recorded command(s)"]
    width = max(len(name) for name in summary["commands"])
    for name, row in summary["commands"].items():
        failed = f"{row['failed']} failed" if row["failed"] else "clean"
        lines.append(f"  {name:<{width}}  {row['runs']:>3} run(s)  {failed:<10} {row['avg_ms']:>6} ms avg")

    if summary["most_retried"]:
        lines.append(
            f"\n{summary['most_retried']} fails and then passes most often"
            " -- either the step is fragile or its requirements are not stated up front"
        )
    return "\n".join(lines)


def _trim(target: Path) -> None:
    """Cap by rewriting. A log that grows without bound is a log nobody keeps."""
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_EVENTS:
        return
    try:
        target.write_text("\n".join(lines[-TRIM_TO:]) + "\n", encoding="utf-8")
    except OSError:
        pass  # deliberate: an uncapped log is a slowdown, never a failed command
