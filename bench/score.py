"""Scoring for the benchmark: one record per run, and the report over all of them.

Pure: nothing here runs a session, reads git or touches the disk, so every rule
that decides a number is unit-tested (tests/test_bench.py). The metrics and
their direction are defined in docs/bench.md before any run; this module only
computes them.
"""

from __future__ import annotations

ARMS = ("plain", "workbench")

# Metric -> which direction is better. The report reads a loss off this.
METRICS = {
    "hidden_pass": "higher",
    "out_of_scope": "lower",
    "rework": "lower",
    "tokens": "lower",
    "cost_usd": "lower",
    "wall_s": "lower",
}

# Workflow artifacts are the workbench arm's paperwork, not a change to the code.
IGNORED_PREFIXES = (".workflow/",)

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def out_of_scope(changed: list[str], expected: list[str]) -> list[str]:
    """Changed paths the ticket did not need, sorted."""
    allowed = set(expected)
    return sorted(
        path
        for path in set(changed)
        if path not in allowed and not path.startswith(IGNORED_PREFIXES)
    )


def rework(commits: int) -> int:
    """Commits after the first. A run that commits once or never scores 0."""
    return max(0, commits - 1)


def tokens(usage: object) -> int | None:
    """Every token the session paid for, or None when it did not say.

    None, never 0: a missing count read as zero would make the arm that lost it
    look cheap.
    """
    if not isinstance(usage, dict):
        return None
    counts = [usage.get(field) for field in _USAGE_FIELDS]
    if not any(isinstance(count, int) for count in counts):
        return None
    return sum(count for count in counts if isinstance(count, int))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def record(
    *,
    ticket: dict,
    arm: str,
    run: int,
    session: dict,
    changed: list[str],
    commits: int,
    hidden_passed: bool,
    wall_s: float,
    error: str | None = None,
) -> dict:
    """The JSON record written for one run."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {', '.join(ARMS)}")
    stray = out_of_scope(changed, ticket["expected_files"])
    return {
        "ticket": ticket["key"],
        "arm": arm,
        "run": run,
        "metrics": {
            "hidden_pass": 1 if hidden_passed else 0,
            "out_of_scope": len(stray),
            "rework": rework(commits),
            "tokens": tokens(session.get("usage")),
            "cost_usd": _number(session.get("total_cost_usd")),
            "wall_s": round(wall_s, 1),
        },
        "out_of_scope_files": stray,
        "changed": sorted(set(changed)),
        "commits": commits,
        "session_id": session.get("session_id"),
        "error": error,
    }


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of no values")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def summarize(records: list[dict]) -> dict:
    """summary[ticket][metric][arm] = {median, min, max, n}; absent values left out."""
    values: dict = {}
    for rec in records:
        for metric, value in rec["metrics"].items():
            if value is None:
                continue
            values.setdefault(rec["ticket"], {}).setdefault(metric, {}).setdefault(rec["arm"], []).append(value)

    summary: dict = {}
    for ticket, metrics in values.items():
        for metric, arms in metrics.items():
            for arm, found in arms.items():
                summary.setdefault(ticket, {}).setdefault(metric, {})[arm] = {
                    "median": median(found),
                    "min": min(found),
                    "max": max(found),
                    "n": len(found),
                }
    return summary


def losses(summary: dict) -> list[tuple[str, str, float, float]]:
    """(ticket, metric, plain median, workbench median) wherever workbench is worse."""
    found = []
    for ticket in sorted(summary):
        for metric in METRICS:
            arms = summary[ticket].get(metric, {})
            if "plain" not in arms or "workbench" not in arms:
                continue
            plain, bench = arms["plain"]["median"], arms["workbench"]["median"]
            worse = bench < plain if METRICS[metric] == "higher" else bench > plain
            if worse:
                found.append((ticket, metric, plain, bench))
    return found


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _cell(stats: dict | None) -> tuple[str, str]:
    if stats is None:
        return "-", "-"
    return _fmt(stats["median"]), f"{_fmt(stats['min'])}-{_fmt(stats['max'])} (n={stats['n']})"


def report(records: list[dict], *, commit: str, model: str, date: str, skipped: int, auth: str = "api-key") -> str:
    """The markdown report: metadata, one table per ticket, then the losses."""
    summary = summarize(records)
    spent = sum(rec["metrics"]["cost_usd"] or 0 for rec in records)
    errors = sum(1 for rec in records if rec.get("error"))
    lines = [
        "# Benchmark report",
        "",
        f"- workbench commit: `{commit}`",
        f"- model: `{model}`",
        f"- date: {date}",
        f"- runs recorded: {len(records)}, with an error: {errors}, skipped by the spend cap: {skipped}",
        f"- auth: {auth}",
        f"- spent: US${spent:.2f}"
        + (" (Claude Code's estimate; drawn from the subscription's usage limit, not billed)" if auth == "subscription" else ""),
        "",
        "Metrics and their direction are defined in docs/bench.md.",
    ]
    for ticket in sorted(summary):
        lines += [
            "",
            f"## {ticket}",
            "",
            "| metric | better | plain median | plain min-max | workbench median | workbench min-max |",
            "|---|---|---|---|---|---|",
        ]
        for metric, direction in METRICS.items():
            arms = summary[ticket].get(metric, {})
            plain = _cell(arms.get("plain"))
            bench = _cell(arms.get("workbench"))
            lines.append(f"| {metric} | {direction} | {plain[0]} | {plain[1]} | {bench[0]} | {bench[1]} |")

    lines += ["", "## Where workbench loses", ""]
    lost = losses(summary)
    if not lost:
        lines.append("Nowhere: on every ticket and metric, the workbench median is at least as good as plain's.")
    for ticket, metric, plain_median, bench_median in lost:
        lines.append(
            f"- {ticket} {metric}: workbench median {_fmt(bench_median)} against plain {_fmt(plain_median)}"
            f" ({METRICS[metric]} is better)"
        )
    return "\n".join(lines) + "\n"
