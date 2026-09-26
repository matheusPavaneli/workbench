"""Scoring for the benchmark: one record per run, and the report over all of them.

Pure: nothing here runs a session, reads git or touches the disk, so every rule
that decides a number is unit-tested (tests/test_bench.py). The metrics and
their direction are defined in docs/bench.md before any run; this module only
computes them.
"""

from __future__ import annotations

import ast
import re

ARMS = ("plain", "workbench", "run")
# What a run measures unless told otherwise. The run arm drives `wb run`, which
# starts its own sessions; it costs a third more and is asked for by name.
DEFAULT_ARMS = ("plain", "workbench")

# Metric -> which direction is better. The report reads a loss off this.
METRICS = {
    "hidden_pass": "higher",
    "out_of_scope": "lower",
    "rework": "lower",
    "tokens": "lower",
    "cost_usd": "lower",
    "wall_s": "lower",
    "tests_missing": "lower",
    "invalid_refs": "lower",
    "unexpected_changes": "lower",
}

# Metrics where a run either made an error or did not; the caught-errors section
# compares how often each arm made it.
ERROR_METRICS = ("hidden_pass", "out_of_scope", "tests_missing", "invalid_refs", "unexpected_changes")

# Workflow artifacts are the workbench arm's paperwork, not a change to the code.
IGNORED_PREFIXES = (".workflow/",)
TEST_PREFIX = "tests/"

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


def _is_test(path: str) -> bool:
    return path.startswith(TEST_PREFIX)


def _is_logic(path: str) -> bool:
    return path.endswith(".py") and not _is_test(path) and not path.startswith(IGNORED_PREFIXES)


def tests_missing(changed: list[str]) -> int:
    """1 when a logic file changed and no test did, else 0."""
    logic = any(_is_logic(path) for path in changed)
    tested = any(_is_test(path) for path in changed)
    return 1 if logic and not tested else 0


def _module_file(module: str, sources: dict[str, str]) -> str | None:
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if candidate in sources:
            return candidate
    return None


def _local_roots(sources: dict[str, str]) -> set[str]:
    """Top-level modules and packages of the repo; imports of anything else are not ours to check."""
    roots = set()
    for path in sources:
        head, sep, rest = path.partition("/")
        if not sep and head.endswith(".py"):
            roots.add(head[: -len(".py")])
        elif rest == "__init__.py":
            roots.add(head)
    return roots


def _defined(tree: ast.Module) -> set[str] | None:
    """Names a module defines at top level, or None when a star import makes that unknowable."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _absolute(path: str, node: ast.ImportFrom) -> str:
    """The dotted module an import names, with a relative import resolved against path."""
    if node.level == 0:
        return node.module or ""
    package = path.split("/")[:-1]
    if node.level > 1:
        package = package[: len(package) - (node.level - 1)]
    return ".".join(package + ([node.module] if node.module else []))


def invalid_refs(changed: list[str], sources: dict[str, str]) -> list[str] | None:
    """Imports in the changed .py files that name a repo module or name that does not exist.

    sources holds the text of every .py file in the run's final tree. None when a
    changed file does not parse: its references cannot be read, which is not the
    same as having none.
    """
    roots = _local_roots(sources)
    trees: dict[str, ast.Module | None] = {}

    def parse(path: str) -> ast.Module | None:
        if path not in trees:
            try:
                trees[path] = ast.parse(sources[path])
            except SyntaxError:
                trees[path] = None
        return trees[path]

    found = []
    for path in sorted(set(changed)):
        if not path.endswith(".py") or path.startswith(IGNORED_PREFIXES) or path not in sources:
            continue
        tree = parse(path)
        if tree is None:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in roots and _module_file(alias.name, sources) is None:
                        found.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = _absolute(path, node)
                if node.level == 0 and module.split(".")[0] not in roots:
                    continue
                target = _module_file(module, sources)
                if target is None:
                    found.append(f"{path}: from {module} import {', '.join(alias.name for alias in node.names)}")
                    continue
                target_tree = parse(target)
                defined = _defined(target_tree) if target_tree is not None else None
                if defined is None:
                    continue
                for alias in node.names:
                    if alias.name not in defined and _module_file(f"{module}.{alias.name}", sources) is None:
                        found.append(f"{path}: from {module} import {alias.name}")
    return sorted(found)


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _diff_path(text: str, prefix: str) -> str | None:
    if text == "/dev/null":
        return None
    return text[len(prefix):] if text.startswith(prefix) else text


def diff_lines(diff: str) -> dict[str, list[int]]:
    """Changed line numbers per path, from `git diff -U0`.

    Added and replaced lines are numbered in the new file. A removed block has no
    line of its own there, so it maps once to the line it follows. A deleted file
    is recorded under its old path.
    """
    lines: dict[str, list[int]] = {}
    old: str | None = None
    path: str | None = None
    in_header = False
    for row in diff.splitlines():
        if row.startswith("diff --git "):
            in_header, old, path = True, None, None
        elif in_header and row.startswith("--- "):
            old = _diff_path(row[4:], "a/")
        elif in_header and row.startswith("+++ "):
            path = _diff_path(row[4:], "b/") or old
        elif row.startswith("@@"):
            in_header = False
            hunk = _HUNK.match(row)
            if hunk is None or path is None:
                continue
            start = int(hunk.group(1))
            count = 1 if hunk.group(2) is None else int(hunk.group(2))
            lines.setdefault(path, []).extend(range(start, start + count) if count else [start])
    return lines


def function_spans(source: str) -> dict[str, tuple[int, int]]:
    """qualname -> (first line, last line) for every function and class, decorators included."""
    spans: dict[str, tuple[int, int]] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
                spans[name] = (start, node.end_lineno or node.lineno)
                visit(node.body, name + ".")

    visit(ast.parse(source).body, "")
    return spans


def unexpected_changes(
    changed_lines: dict[str, list[int]], sources: dict[str, str], expected_functions: list[str] | None
) -> int | None:
    """Changed lines in logic files outside the functions the ticket needed.

    None when the ticket names no functions, or when a file holding one does not
    parse: without the spans there is nothing to measure against.
    """
    if not expected_functions:
        return None
    allowed: dict[str, list[tuple[int, int]]] = {}
    for entry in expected_functions:
        path, _, name = entry.partition("::")
        if path not in sources:
            continue
        try:
            spans = function_spans(sources[path])
        except SyntaxError:
            return None
        if name in spans:
            allowed.setdefault(path, []).append(spans[name])
    count = 0
    for path, lines in changed_lines.items():
        if not _is_logic(path):
            continue
        inside = allowed.get(path, [])
        count += sum(1 for line in lines if not any(start <= line <= end for start, end in inside))
    return count


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
    artifacts: list[str] | None = None,
    diff: str | None = None,
    sources: dict[str, str] | None = None,
) -> dict:
    """The JSON record written for one run.

    diff (`git diff -U0` against the base, new files included) and sources (the
    text of every .py file in the final tree) feed the error metrics; without
    them those metrics are absent, never 0.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {', '.join(ARMS)}")
    stray = out_of_scope(changed, ticket["expected_files"])
    refs = invalid_refs(changed, sources) if sources is not None else None
    unexpected = (
        unexpected_changes(diff_lines(diff), sources, ticket.get("expected_functions"))
        if diff is not None and sources is not None
        else None
    )
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
            "tests_missing": tests_missing(changed),
            "invalid_refs": None if refs is None else len(refs),
            "unexpected_changes": unexpected,
        },
        "out_of_scope_files": stray,
        "invalid_ref_list": refs,
        "changed": sorted(set(changed)),
        "commits": commits,
        "session_id": session.get("session_id"),
        "num_turns": session.get("num_turns"),
        # What the session left under .workflow/<KEY>/: the evidence that the
        # workbench arm actually ran the flow, and was not a plain run with a
        # plugin nobody called.
        "workflow_artifacts": sorted(artifacts or []),
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


def losses(summary: dict, arm: str = "workbench") -> list[tuple[str, str, float, float]]:
    """(ticket, metric, plain median, ``arm``'s median) wherever ``arm`` is worse than plain."""
    found = []
    for ticket in sorted(summary):
        for metric in METRICS:
            arms = summary[ticket].get(metric, {})
            if "plain" not in arms or arm not in arms:
                continue
            plain, bench = arms["plain"]["median"], arms[arm]["median"]
            worse = bench < plain if METRICS[metric] == "higher" else bench > plain
            if worse:
                found.append((ticket, metric, plain, bench))
    return found


def _made_error(metric: str, value: float) -> bool:
    return value < 1 if metric == "hidden_pass" else value > 0


def caught(records: list[dict], arm: str = "workbench") -> list[tuple[str, str, int, int, int, int]]:
    """(ticket, metric, plain runs with the error, plain n, ``arm``'s runs with it, ``arm``'s n).

    Only where the two arms made the error in a different share of their runs.
    An absent value is left out of n.
    """
    counts: dict[tuple[str, str], dict[str, list[int]]] = {}
    for rec in records:
        for metric in ERROR_METRICS:
            value = rec["metrics"].get(metric)
            if value is None:
                continue
            tally = counts.setdefault((rec["ticket"], metric), {}).setdefault(rec["arm"], [0, 0])
            tally[0] += int(_made_error(metric, value))
            tally[1] += 1
    found = []
    for (ticket, metric), arms in sorted(counts.items(), key=lambda item: (item[0][0], ERROR_METRICS.index(item[0][1]))):
        if "plain" not in arms or arm not in arms:
            continue
        (plain_hits, plain_n), (bench_hits, bench_n) = arms["plain"], arms[arm]
        if plain_hits * bench_n != bench_hits * plain_n:
            found.append((ticket, metric, plain_hits, plain_n, bench_hits, bench_n))
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
    present = [arm for arm in ARMS if any(rec["arm"] == arm for rec in records)]
    compared = [arm for arm in present if arm != "plain"]
    bench_runs = [rec for rec in records if rec["arm"] != "plain"]
    used_flow = sum(1 for rec in bench_runs if rec.get("workflow_artifacts"))
    lines = [
        "# Benchmark report",
        "",
        f"- workbench commit: `{commit}`",
        f"- model: `{model}`",
        f"- date: {date}",
        f"- runs recorded: {len(records)}, with an error: {errors}, skipped by the spend cap: {skipped}",
        f"- arms: {', '.join(present)}",
        f"- workbench and run sessions that left workflow artifacts: {used_flow} of {len(bench_runs)}",
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
            "| metric | better | " + " | ".join(f"{arm} median | {arm} min-max" for arm in present) + " |",
            "|---|---|" + "---|---|" * len(present),
        ]
        for metric, direction in METRICS.items():
            arms = summary[ticket].get(metric, {})
            cells = " | ".join(" | ".join(_cell(arms.get(arm))) for arm in present)
            lines.append(f"| {metric} | {direction} | {cells} |")

    lines += [
        "",
        "## Caught errors",
        "",
        "Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).",
        "",
    ]
    differ = [(arm, row) for arm in compared for row in caught(records, arm)]
    if not differ:
        lines.append("None: on every ticket, the arms made each error in the same share of their runs.")
    for arm, (ticket, metric, plain_hits, plain_n, bench_hits, bench_n) in differ:
        lines.append(f"- {ticket} {metric}: plain {plain_hits} of {plain_n} runs, {arm} {bench_hits} of {bench_n}")

    for arm in compared or ["workbench"]:
        lines += ["", f"## Where {arm} loses", ""]
        lost = losses(summary, arm)
        if not lost:
            lines.append(f"Nowhere: on every ticket and metric, the {arm} median is at least as good as plain's.")
        for ticket, metric, plain_median, bench_median in lost:
            lines.append(
                f"- {ticket} {metric}: {arm} median {_fmt(bench_median)} against plain {_fmt(plain_median)}"
                f" ({METRICS[metric]} is better)"
            )
    return "\n".join(lines) + "\n"
