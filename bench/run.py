"""Run the benchmark: every seeded ticket, in two arms, several times each.

    WB_BENCH=1 CLAUDE_CODE_OAUTH_TOKEN=... python bench/run.py [--smoke]
    WB_BENCH=1 ANTHROPIC_API_KEY=... python bench/run.py [--smoke]

Gated on env and never part of the unit suite: a full run spends real money,
or a real share of a subscription's usage limit.
What is measured, and why the arms are isolated the way they are, is in
docs/bench.md. Scoring is in score.py; this file only drives sessions and
reads back what they did.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import score

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
FIXTURE = BENCH / "fixture"
HIDDEN = BENCH / "hidden"
RESULTS = BENCH / "results"
TICKETS = BENCH / "tickets.json"

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_RUNS = 5
DEFAULT_CAP_USD = 50.0
DEFAULT_TIMEOUT_S = 1800
SMOKE_TICKET = "BN-3"

# What the workbench arm loads. bench/ is left out so the hidden tests are not
# reachable through the plugin root.
PLUGIN_PARTS = (".claude-plugin", "hooks", "lib", "shared", "skills")

GIT_TIMEOUT_S = 60
HIDDEN_TIMEOUT_S = 120


def auth(env: dict) -> str | None:
    """Which credential the sessions will use, as Claude Code picks it.

    Both arms run with a clean config, so no stored login is read: the
    credential has to come from the environment. An API key wins over a
    subscription token when both are set.
    """
    if env.get("ANTHROPIC_API_KEY"):
        return "api-key"
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "subscription"
    return None


def gate(env: dict, claude: str | None) -> list[str]:
    """What is missing before anything may run; empty means go."""
    missing = []
    if env.get("WB_BENCH") != "1":
        missing.append("WB_BENCH=1 (a full run spends real money or usage limit; set it to say so)")
    if auth(env) is None:
        missing.append(
            "CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`, uses your subscription) or ANTHROPIC_API_KEY"
        )
    if not claude:
        missing.append("claude on PATH")
    return missing


def prompt(ticket: dict, arm: str) -> str:
    body = (
        f"{ticket['title']}\n\n{ticket['desc']}\n\n"
        "Work in this repository until the ticket is done, then commit your change with git."
    )
    if arm == "workbench":
        return f"Ticket {ticket['key']}.\n\n{body}"
    return body


def command(claude: str, arm: str, text: str, *, model: str, plugin_dir: Path | None) -> list[str]:
    argv = [
        claude,
        "-p",
        text,
        "--output-format",
        "json",
        "--model",
        model,
        "--permission-mode",
        "bypassPermissions",
    ]
    if arm == "workbench":
        if plugin_dir is None:
            raise ValueError("the workbench arm needs a plugin directory")
        argv += ["--plugin-dir", str(plugin_dir)]
    return argv


def copy_plugin(dest: Path, root: Path = ROOT) -> Path:
    """A copy of the plugin with only what it loads, and never bench/."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for part in PLUGIN_PARTS:
        shutil.copytree(root / part, dest / part, ignore=ignore)
    return dest


def plan_runs(tickets: list[dict], runs: int) -> list[tuple[dict, str, int]]:
    """Run 1 of every ticket and arm, then run 2, and so on.

    Interleaved so a spend cap reached part-way leaves both arms with the same
    number of runs per ticket, give or take one, rather than starving one arm.
    """
    return [(ticket, arm, n) for n in range(1, runs + 1) for ticket in tickets for arm in score.ARMS]


class Budget:
    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = 0.0

    def allows(self) -> bool:
        return self.spent_usd < self.cap_usd

    def add(self, cost_usd: float | None) -> None:
        self.spent_usd += cost_usd or 0.0


def parse_session(stdout: str) -> dict:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=True
    )
    return done.stdout


def _init_repo(repo: Path) -> str:
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "bench@workbench.invalid")
    _git(repo, "config", "user.name", "bench")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return _git(repo, "rev-parse", "HEAD").strip()


def _changed(repo: Path, base: str) -> list[str]:
    tracked = _git(repo, "diff", "--name-only", base).splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({path for path in tracked + untracked if path})


def _hidden_passes(repo: Path, key: str) -> bool:
    tests = HIDDEN / key
    env = {**os.environ, "PYTHONPATH": str(repo)}
    done = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-t", str(tests)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=HIDDEN_TIMEOUT_S,
    )
    return done.returncode == 0


def run_one(claude: str, ticket: dict, arm: str, n: int, *, model: str, timeout_s: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="wb-bench-") as tmp:
        work = Path(tmp)
        repo = work / "repo"
        base = _init_repo(repo)
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(work / "config")}

        plugin_dir = None
        if arm == "workbench":
            plugin_dir = copy_plugin(work / "plugin")
            subprocess.run(
                [
                    sys.executable,
                    str(plugin_dir / "lib" / "wb.py"),
                    "task",
                    "new",
                    ticket["title"],
                    "--type",
                    ticket["kind"],
                    "--desc",
                    ticket["desc"],
                    "--key",
                    ticket["key"],
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                check=True,
            )

        argv = command(claude, arm, prompt(ticket, arm), model=model, plugin_dir=plugin_dir)
        error = None
        stdout = ""
        started = time.monotonic()
        try:
            done = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout_s)
            stdout = done.stdout
            if done.returncode != 0:
                error = f"claude exited {done.returncode}: {done.stderr.strip()[:300]}"
        except subprocess.TimeoutExpired:
            error = f"timed out after {timeout_s}s"
        wall_s = time.monotonic() - started

        session = parse_session(stdout)
        if not session and error is None:
            error = "claude printed no JSON result"
        return score.record(
            ticket=ticket,
            arm=arm,
            run=n,
            session=session,
            changed=_changed(repo, base),
            commits=int(_git(repo, "rev-list", "--count", f"{base}..HEAD").strip()),
            hidden_passed=_hidden_passes(repo, ticket["key"]),
            wall_s=wall_s,
            error=error,
        )


def load_tickets(only: list[str] | None = None) -> list[dict]:
    tickets = json.loads(TICKETS.read_text(encoding="utf-8"))["tickets"]
    if not only:
        return tickets
    unknown = sorted(set(only) - {t["key"] for t in tickets})
    if unknown:
        raise SystemExit(f"error: unknown ticket(s) {', '.join(unknown)}")
    return [t for t in tickets if t["key"] in only]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the workbench benchmark (docs/bench.md).")
    parser.add_argument("--smoke", action="store_true", help=f"run {SMOKE_TICKET} once per arm")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--tickets", help="comma-separated keys; default all")
    parser.add_argument("--cap", type=float, default=DEFAULT_CAP_USD, help="spend cap in US$")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds per session")
    args = parser.parse_args(argv)

    claude = shutil.which("claude")
    missing = gate(dict(os.environ), claude)
    if missing or claude is None:
        for item in missing:
            print(f"error: missing {item}", file=sys.stderr)
        return 2

    if args.smoke:
        tickets, runs = load_tickets([SMOKE_TICKET]), 1
    else:
        tickets, runs = load_tickets(args.tickets.split(",") if args.tickets else None), args.runs

    commit = _git(ROOT, "rev-parse", "--short", "HEAD").strip()
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = RESULTS / f"{date}-{commit}{'-smoke' if args.smoke else ''}"
    out.mkdir(parents=True, exist_ok=True)

    budget = Budget(args.cap)
    records: list[dict] = []
    skipped = 0
    for ticket, arm, n in plan_runs(tickets, runs):
        if not budget.allows():
            skipped += 1
            continue
        print(f"{ticket['key']} {arm} run {n} ...", flush=True)
        rec = run_one(claude, ticket, arm, n, model=args.model, timeout_s=args.timeout)
        budget.add(rec["metrics"]["cost_usd"])
        records.append(rec)
        path = out / f"{ticket['key']}-{arm}-{n}.json"
        path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")

    text = score.report(
        records, commit=commit, model=args.model, date=date, skipped=skipped, auth=auth(dict(os.environ)) or "none"
    )
    (out / "report.md").write_text(text, encoding="utf-8")
    print(f"wrote {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
