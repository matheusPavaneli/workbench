"""Thin git façade.

Read-only. Nothing here commits, pushes, or changes a working tree: those are
the user's calls, and they go through the agent's own tooling where the user
can see and approve them.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ARTIFACT_DIR = ".workflow"

# Generated output is never part of a change under review. A repo with a proper
# .gitignore never surfaces these, but a fresh checkout without one reports
# bytecode as scope creep -- and worse, as touching a critical zone.
GENERATED = (
    "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".tox/",
    "node_modules/", "dist/", "build/", "target/", "coverage/", ".next/", ".venv/", "venv/",
)
GENERATED_SUFFIXES = (".pyc", ".pyo", ".class", ".o", ".so", ".map", ".lock.tmp")


@dataclass(frozen=True)
class Remote:
    url: str
    host: str
    org: str


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def repo_root(cwd: Path) -> Path | None:
    root = _git(["rev-parse", "--show-toplevel"], cwd)
    return Path(root) if root else None


def origin(cwd: Path) -> Remote | None:
    url = _git(["remote", "get-url", "origin"], cwd)
    return parse_remote(url) if url else None


def branch(cwd: Path) -> str | None:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def changed_files(cwd: Path, *, staged: bool = False) -> list[str]:
    """Paths changed against HEAD, repo-relative, forward slashes.

    Untracked files count: a plan that adds a file has to be checked against
    the file it actually added, and git does not report those in a plain diff.
    """
    args = ["diff", "--name-only", "--cached"] if staged else ["diff", "--name-only", "HEAD"]
    tracked = _git(args, cwd) or ""
    paths = {line.strip() for line in tracked.splitlines() if line.strip()}

    if not staged:
        untracked = _git(["ls-files", "--others", "--exclude-standard"], cwd) or ""
        paths.update(line.strip() for line in untracked.splitlines() if line.strip())

    normalised = (path.replace("\\", "/") for path in paths)
    return sorted(path for path in normalised if not _is_noise(path))


def _is_noise(path: str) -> bool:
    """This tool's own artifacts, and anything a build generated."""
    if path.startswith(ARTIFACT_DIR + "/"):
        return True
    if path.endswith(GENERATED_SUFFIXES):
        return True
    return any(segment in path for segment in GENERATED)


def diff_stat(cwd: Path, *, staged: bool = False) -> str:
    args = ["diff", "--stat", "--cached"] if staged else ["diff", "--stat", "HEAD"]
    return _git(args, cwd) or ""


def default_branch(cwd: Path) -> str:
    """The branch a PR would target. Falls back rather than failing."""
    head = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd)
    if head:
        return head.rsplit("/", 1)[-1]
    for candidate in ("main", "master", "develop"):
        if _git(["rev-parse", "--verify", "--quiet", candidate], cwd):
            return candidate
    return "main"


def merge_base(cwd: Path, base: str) -> str | None:
    return _git(["merge-base", "HEAD", base], cwd)


def subjects_since(cwd: Path, base: str, limit: int = 50) -> list[str]:
    """Commit subjects on this branch that the base does not have."""
    output = _git(["log", f"{base}..HEAD", "--no-merges", f"--max-count={limit}", "--format=%s"], cwd)
    return [line.strip() for line in (output or "").splitlines() if line.strip()]


def recent_subjects(cwd: Path, limit: int = 50) -> list[str]:
    output = _git(["log", "--no-merges", f"--max-count={limit}", "--format=%s"], cwd)
    return [line.strip() for line in (output or "").splitlines() if line.strip()]


def remote_branches(cwd: Path) -> list[str]:
    output = _git(["branch", "-r", "--format=%(refname:short)"], cwd)
    return [line.strip() for line in (output or "").splitlines() if line.strip()]


def commits_between(cwd: Path, base: str, branch: str) -> list[str]:
    """Commit hashes on ``branch`` that ``base`` lacks, **oldest first**.

    Order matters: a series applied newest-first conflicts on everything after
    the first commit.
    """
    output = _git(["log", "--reverse", "--no-merges", f"{base}..{branch}", "--format=%H %s"], cwd)
    return [line.strip() for line in (output or "").splitlines() if line.strip()]


def branch_exists(cwd: Path, name: str) -> bool:
    return _git(["rev-parse", "--verify", "--quiet", name], cwd) is not None


def identity(cwd: Path) -> dict[str, str]:
    """The author git would actually use here, whatever the context prefers."""
    return {
        "name": _git(["config", "user.name"], cwd) or "",
        "email": _git(["config", "user.email"], cwd) or "",
    }


def parse_remote(url: str) -> Remote | None:
    """Handle the three shapes that actually appear: ssh, scp-style, https."""
    cleaned = url.strip()
    match = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?$", cleaned)
    if not match:
        match = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?$", cleaned)
    if not match:
        return None

    host = match.group(1).lower()
    path = match.group(2).strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    # Azure DevOps https remotes carry a leading org segment and a "_git" marker:
    #   https://dev.azure.com/{org}/{project}/_git/{repo}
    if "_git" in parts:
        marker = parts.index("_git")
        org = parts[0] if marker > 0 else ""
    else:
        org = parts[0]

    return Remote(url=cleaned, host=host, org=org.lower())
