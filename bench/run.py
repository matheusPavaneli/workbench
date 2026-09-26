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
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from collections.abc import Iterator
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

# The arms whose repo is set up as a workbench user's: `wb init --write` and the
# ticket in the local backlog.
FLOW_ARMS = ("workbench", "run")
# How often the run arm answers a `wb run` that stopped for a person, and the
# exit code it stops with (workbench.cli.run.EXIT_WAITING).
MAX_APPROVALS = 4
WAITING = 8
# Sessions one `wb run` call may start (its --max-sessions default).
RUN_SESSIONS = 4

# What the workbench arm loads. bench/ is left out so the hidden tests are not
# reachable through the plugin root.
PLUGIN_PARTS = (".claude-plugin", "hooks", "lib", "shared", "skills")

GIT_TIMEOUT_S = 60
# Every process a run starts gets an empty stdin. Run from a terminal, an
# inherited console never reaches end of input: git shortlog in a repo with no
# commits reads the log from stdin and waits there until the timeout.
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
        # Asked the way a user who installed workbench asks. A bare "Ticket
        # BN-3." left the skills uncalled in the first smoke runs: the plugin
        # loaded, the flow never ran, and the arm measured a plain session.
        return f"Pick up ticket {ticket['key']} and take it through the workbench flow to a commit.\n\n{body}"
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


def plan_runs(tickets: list[dict], runs: int, arms: tuple[str, ...] = score.DEFAULT_ARMS) -> list[tuple[dict, str, int]]:
    """Run 1 of every ticket and arm, then run 2, and so on.

    Interleaved so a spend cap reached part-way leaves both arms with the same
    number of runs per ticket, give or take one, rather than starving one arm.
    """
    return [(ticket, arm, n) for n in range(1, runs + 1) for ticket in tickets for arm in arms]


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


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=True
    )
    return done.stdout


def _force_remove(function, path, _excinfo) -> None:
    # git writes its objects read-only; Windows refuses to delete those.
    os.chmod(path, stat.S_IWRITE)
    function(path)


@contextlib.contextmanager
def workdir(prefix: str, attempts: int = 10, pause_s: float = 0.5) -> Iterator[Path]:
    """A temp dir removed with retries.

    On Windows a file the session or a scanner still holds cannot be deleted
    for a moment after the last process exits (WinError 32). A leftover temp
    dir is reported, never allowed to end a run that has already paid.
    """
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        for attempt in range(attempts):
            try:
                shutil.rmtree(path, onerror=_force_remove)
                break
            except OSError as exc:
                if attempt == attempts - 1:
                    print(f"warning: left {path} behind: {exc}", file=sys.stderr)
                else:
                    time.sleep(pause_s)


def session_env(work: Path, env: dict | None = None) -> dict:
    """The environment every step of a run sees, setup and session alike.

    A fresh CLAUDE_CONFIG_DIR keeps the owner's plugins and CLAUDE.md out; a
    fresh WORKBENCH_HOME keeps their tracker contexts, approvals and event log
    out, so the workbench arm starts where a new user would.
    """
    empty = work / "gitconfig"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.touch()
    return {
        **(os.environ if env is None else env),
        "CLAUDE_CONFIG_DIR": str(work / "config"),
        "WORKBENCH_HOME": str(work / "home"),
        # The owner's ~/.gitconfig stays out too: its aliases, hooks and
        # core.fsmonitor (a daemon per repo that holds the temp dir open).
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _setup(argv: list[str], repo: Path, env: dict) -> None:
    done = subprocess.run(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    if done.returncode != 0:
        raise RuntimeError(f"setup step failed ({done.returncode}): {' '.join(argv[1:4])}\n{done.stderr.strip()}")


def prepare(repo: Path, ticket: dict, arm: str, env: dict, plugin_dir: Path | None) -> str:
    """Copy the fixture, set the arm up as a new user would, commit it as the base.

    The workbench arm's setup -- wb init --write and the ticket in the local
    backlog -- is part of the base commit, so none of it is scored as a change.
    """
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _git(repo, "init", "-q", env=env)
    _git(repo, "config", "user.email", "bench@workbench.invalid", env=env)
    _git(repo, "config", "user.name", "bench", env=env)
    if arm in FLOW_ARMS:
        if plugin_dir is None:
            raise ValueError(f"the {arm} arm needs a plugin directory")
        wb = [sys.executable, str(plugin_dir / "lib" / "wb.py")]
        _setup([*wb, "init", "--write"], repo, env)
        _setup(
            [*wb, "task", "new", ticket["title"], "--type", ticket["kind"], "--desc", ticket["desc"], "--key", ticket["key"]],
            repo,
            env,
        )
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "-q", "-m", "base", env=env)
    return _git(repo, "rev-parse", "HEAD", env=env).strip()


def preflight(ticket: dict) -> None:
    """Set up the workbench arm once before any paid session, so a broken setup costs nothing."""
    with workdir("wb-bench-preflight-") as work:
        prepare(work / "repo", ticket, "workbench", session_env(work), copy_plugin(work / "plugin"))


def _changed(repo: Path, base: str, env: dict | None = None) -> list[str]:
    tracked = _git(repo, "diff", "--name-only", base, env=env).splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", env=env).splitlines()
    return sorted({path for path in tracked + untracked if path})


def _diff(repo: Path, base: str, env: dict | None = None) -> str:
    """`git diff -U0` against base, with new files in it as added lines.

    Intent-to-add records a new file's path, not its content, so the diff sees it
    without anything being staged for real. Line endings are ignored: a session
    on Windows that rewrites a file with CRLF has not changed every line of it.
    """
    _git(repo, "add", "--intent-to-add", "--all", env=env)
    return _git(
        repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--ignore-cr-at-eol", base,
        "--", ".", ":(exclude).workflow",
        env=env,
    )


def _sources(repo: Path, env: dict | None = None) -> dict[str, str]:
    """The text of every .py file in the run's final tree, tracked or new; .workflow/ left out."""
    listed = _git(repo, "ls-files", "--cached", "--others", "--exclude-standard", env=env).splitlines()
    found = {}
    for path in listed:
        full = repo / path
        if path.endswith(".py") and not path.startswith(score.IGNORED_PREFIXES) and full.is_file():
            found[path] = full.read_text(encoding="utf-8", errors="replace")
    return found


def _artifacts(repo: Path, key: str) -> list[str]:
    folder = repo / ".workflow" / key
    if not folder.is_dir():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in folder.rglob("*") if path.is_file())


def _hidden_passes(repo: Path, key: str) -> bool:
    tests = HIDDEN / key
    env = {**os.environ, "PYTHONPATH": str(repo)}
    done = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-t", str(tests)],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL, capture_output=True,
        text=True,
        timeout=HIDDEN_TIMEOUT_S,
    )
    return done.returncode == 0


def run_one(
    claude: str, ticket: dict, arm: str, n: int, *, model: str, timeout_s: int, keep: Path | None = None
) -> dict:
    with workdir("wb-bench-") as work:
        try:
            return _run_in(work, claude, ticket, arm, n, model=model, timeout_s=timeout_s)
        finally:
            if keep is not None:
                keep_transcripts(work, keep)


def _run_in(work: Path, claude: str, ticket: dict, arm: str, n: int, *, model: str, timeout_s: int) -> dict:
    repo = work / "repo"
    env = session_env(work)
    plugin_dir = copy_plugin(work / "plugin") if arm in FLOW_ARMS else None
    base = prepare(repo, ticket, arm, env, plugin_dir)
    if arm == "run" and plugin_dir is not None:
        return _run_arm(ticket, n, repo, base, env, plugin_dir, model=model, timeout_s=timeout_s)

    argv = command(claude, arm, prompt(ticket, arm), model=model, plugin_dir=plugin_dir)
    error = None
    stdout = ""
    started = time.monotonic()
    try:
        done = subprocess.run(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s)
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
        changed=_changed(repo, base, env),
        commits=int(_git(repo, "rev-list", "--count", f"{base}..HEAD", env=env).strip()),
        hidden_passed=_hidden_passes(repo, ticket["key"]),
        wall_s=wall_s,
        error=error,
        artifacts=_artifacts(repo, ticket["key"]),
        diff=_diff(repo, base, env),
        sources=_sources(repo, env),
    )


def run_argv(key: str, plugin_dir: Path, *, model: str, timeout_s: int) -> list[str]:
    """`wb run` as a user who turned it on would call it, in a sandbox that allows every tool."""
    return [
        sys.executable, str(plugin_dir / "lib" / "wb.py"), "run", key, "--until", "commit",
        "--plugin-dir", str(plugin_dir), "--model", model, "--permission-mode", "bypassPermissions",
        "--timeout", str(timeout_s),
    ]


def approval(stdout: str) -> list[str] | None:
    """The `wb approve KEY TOKEN` a stopped `wb run` printed, as argv, or None."""
    for line in stdout.splitlines():
        if line.startswith("approve: wb approve "):
            return line[len("approve: wb "):].split()
    return None


def combined(sessions: list[dict]) -> dict:
    """Every session `wb run` started, as one session record: usage, cost and turns summed.

    A field no session reported stays absent rather than becoming 0, as it does
    for a single session.
    """
    usage: dict[str, int] = {}
    for entry in sessions:
        for field, count in (entry.get("usage") or {}).items():
            if isinstance(count, int) and not isinstance(count, bool):
                usage[field] = usage.get(field, 0) + count
    costs = [entry["total_cost_usd"] for entry in sessions if isinstance(entry.get("total_cost_usd"), (int, float))]
    turns = [entry["num_turns"] for entry in sessions if isinstance(entry.get("num_turns"), int)]
    return {
        "usage": usage or None,
        "total_cost_usd": sum(costs) if costs else None,
        "num_turns": sum(turns) if turns else None,
        "session_id": ",".join(str(entry.get("session_id")) for entry in sessions if entry.get("session_id")) or None,
    }


def _run_arm(ticket: dict, n: int, repo: Path, base: str, env: dict, plugin_dir: Path, *, model: str,
             timeout_s: int) -> dict:
    """`wb run` to a commit, with the benchmark answering each approval it stops for.

    The person's part is not the machine's cost, so the harness plays it: it
    approves exactly what `wb run` printed and resumes, up to MAX_APPROVALS
    times. The sessions `wb run` started are read back from its run.json.
    """
    key = ticket["key"]
    run_env = {**env, "WB_RUN": "1"}
    error = None
    started = time.monotonic()
    for _ in range(MAX_APPROVALS + 1):
        try:
            done = subprocess.run(run_argv(key, plugin_dir, model=model, timeout_s=timeout_s), cwd=repo, env=run_env,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=timeout_s * RUN_SESSIONS + 60)
        except subprocess.TimeoutExpired:
            error = "wb run timed out"
            break
        if done.returncode == 0:
            error = None
            break
        token = approval(done.stdout) if done.returncode == WAITING else None
        if token is None:
            error = f"wb run exited {done.returncode}: {(done.stderr or done.stdout).strip()[-300:]}"
            break
        _setup([sys.executable, str(plugin_dir / "lib" / "wb.py"), *token], repo, run_env)
    else:
        error = f"wb run still waiting after {MAX_APPROVALS} approvals"
    wall_s = time.monotonic() - started

    record_path = repo / ".workflow" / key / "run.json"
    try:
        sessions = json.loads(record_path.read_text(encoding="utf-8")).get("sessions") or []
    except (OSError, ValueError):
        sessions = []
    return score.record(
        ticket=ticket,
        arm="run",
        run=n,
        session=combined(sessions),
        changed=_changed(repo, base, env),
        commits=int(_git(repo, "rev-list", "--count", f"{base}..HEAD", env=env).strip()),
        hidden_passed=_hidden_passes(repo, key),
        wall_s=wall_s,
        error=error,
        artifacts=_artifacts(repo, key),
        diff=_diff(repo, base, env),
        sources=_sources(repo, env),
    )


def keep_transcripts(work: Path, prefix: Path) -> list[Path]:
    """Copy every session transcript a run left in its config dir next to its record.

    The work dir is deleted with the run, and with it the only account of where
    a session's turns went: the first probe of the light path cost 46 turns and
    nothing said why. Kept locally (they are gitignored): a transcript holds the
    whole session, tool output included, which is for reading, not publishing.
    """
    found = sorted((work / "config" / "projects").rglob("*.jsonl")) if (work / "config").is_dir() else []
    kept = []
    for index, source in enumerate(found, start=1):
        target = prefix.with_name(f"{prefix.name}.transcript{index}.jsonl")
        shutil.copyfile(source, target)
        kept.append(target)
    return kept


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
    parser.add_argument(
        "--arms", default=",".join(score.DEFAULT_ARMS), help=f"comma-separated, of {', '.join(score.ARMS)}"
    )
    args = parser.parse_args(argv)

    claude = shutil.which("claude")
    missing = gate(dict(os.environ), claude)
    if missing or claude is None:
        for item in missing:
            print(f"error: missing {item}", file=sys.stderr)
        return 2

    arms = tuple(arm.strip() for arm in args.arms.split(",") if arm.strip())
    unknown = sorted(set(arms) - set(score.ARMS))
    if unknown or not arms:
        print(f"error: unknown arm(s) {', '.join(unknown) or '(none)'}; expected {', '.join(score.ARMS)}", file=sys.stderr)
        return 2

    if args.smoke:
        tickets, runs = load_tickets([SMOKE_TICKET]), 1
    else:
        tickets, runs = load_tickets(args.tickets.split(",") if args.tickets else None), args.runs

    commit = _git(ROOT, "rev-parse", "--short", "HEAD").strip()
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = RESULTS / f"{date}-{commit}{'-smoke' if args.smoke else ''}"
    out.mkdir(parents=True, exist_ok=True)

    try:
        preflight(tickets[0])
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: the workbench arm cannot be set up; nothing was run\n{exc}", file=sys.stderr)
        return 3

    budget = Budget(args.cap)
    records: list[dict] = []
    skipped = 0
    for ticket, arm, n in plan_runs(tickets, runs, arms):
        if not budget.allows():
            skipped += 1
            continue
        print(f"{ticket['key']} {arm} run {n} ...", flush=True)
        rec = run_one(claude, ticket, arm, n, model=args.model, timeout_s=args.timeout,
                      keep=out / f"{ticket['key']}-{arm}-{n}")
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
