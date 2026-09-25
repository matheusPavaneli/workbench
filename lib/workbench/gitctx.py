"""Thin git façade.

Read-only. Nothing here commits, pushes, or changes a working tree: those are
the user's calls, and they go through the agent's own tooling where the user
can see and approve them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import UsageError


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
    repo: str = ""  # last path segment, so a provider can address owner/repo

    @property
    def slug(self) -> str:
        return f"{self.org}/{self.repo}" if self.org and self.repo else ""


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


def _git_raw(args: list[str], cwd: Path) -> str | None:
    """As ``_git``, but without stripping: file contents must survive verbatim.

    Stripping is right for a branch name and wrong for a blob -- a file that
    opens with a blank line would come back shifted by one, and every line
    number checked against it would be off.
    """
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def repo_root(cwd: Path) -> Path | None:
    """The raw detector. Callers want ``checkout`` or ``require_checkout``."""
    root = _git(["rev-parse", "--show-toplevel"], cwd)
    return Path(root) if root else None


def checkout(cwd: Path | None = None) -> Path:
    """The repo this is running in, or the directory it was run from.

    Most commands read and write next to the work, so a directory that is not a
    checkout is a narrower answer, never a failed command. It lives here so
    that answer has one definition rather than a copy per caller.
    """
    here = cwd or Path.cwd()
    return repo_root(here) or here


def require_checkout(cwd: Path | None = None, *, fix: list[str] | None = None) -> Path:
    """The repo, or a refusal, for a command whose answer needs one.

    A branch to carry, a commit to check against the history, a push to
    compose: outside a checkout these have no answer, and inventing one is
    worse than saying so.
    """
    root = repo_root(cwd or Path.cwd())
    if root is None:
        raise UsageError("not a git repository", fix=fix or ["run this inside a checkout"])
    return root


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


def changed_since(cwd: Path, base: str, *, staged: bool = False) -> list[str]:
    """Everything this branch changed against ``base``: committed, and not yet.

    ``changed_files`` answers "what is uncommitted", which is the whole truth
    only until the first commit. After one it reads a finished change as no
    change at all -- `wb pr context` called a three-file feature trivial because
    it asked a minute too late, and `wb status` reported nothing done for work
    that was.

    Three dots, so commits the base collected after this branch left it are not
    counted as this branch's doing. An unknown base contributes nothing rather
    than failing: the working tree is still an answer.
    """
    committed = _git(["diff", "--name-only", f"{base}...HEAD"], cwd) or ""
    paths = {line.strip() for line in committed.splitlines() if line.strip()}
    paths.update(changed_files(cwd, staged=staged))

    normalised = (path.replace("\\", "/") for path in paths)
    return sorted(path for path in normalised if not _is_noise(path))


def is_ignored(cwd: Path, path: str) -> bool | None:
    """Whether git ignores ``path``. ``None`` when git could not answer.

    Here rather than in the caller because this module is the git façade: a
    second place that shells out to git is a second place that has to remember
    the timeout, the encoding and the failure mode.
    """
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 0 ignored, 1 not ignored, anything else is git failing to answer.
    return True if completed.returncode == 0 else (False if completed.returncode == 1 else None)


def tracked_changes(cwd: Path) -> list[str]:
    """Changes to files git already knows about, staged or not.

    ``changed_files`` counts untracked files on purpose -- scope has to be
    checked against a file a plan actually added. "Is it safe to switch branch"
    is the opposite question: an untracked file crosses a ``switch`` unharmed,
    and treating one as a dirty tree blocks the ordinary case, with advice that
    does not work either -- plain ``git stash`` leaves untracked files be.
    """
    tracked = _git(["diff", "--name-only", "HEAD"], cwd) or ""
    paths = {line.strip() for line in tracked.splitlines() if line.strip()}
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


def added_lines(cwd: Path, *, staged: bool = False) -> list[tuple[str, int, str]]:
    """Every line this diff adds, as ``(path, line number, text)``.

    Line numbers are the ones in the *new* file, so a finding can be reported
    at a location the author can open. Untracked files are included whole: a
    newly added file is entirely added lines, and git's diff will not say so.
    """
    args = ["diff", "--unified=0", "--no-color", "--cached"] if staged else ["diff", "--unified=0", "--no-color", "HEAD"]
    added: list[tuple[str, int, str]] = []
    path = ""
    line_number = 0

    for raw in (_git(args, cwd) or "").splitlines():
        if raw.startswith("+++ "):
            candidate = raw[4:].strip()
            path = "" if candidate == "/dev/null" else candidate[2:] if candidate.startswith("b/") else candidate
            path = path.replace("\\", "/")
        elif raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            line_number = int(match.group(1)) if match else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if path and not _is_noise(path):
                added.append((path, line_number, raw[1:]))
            line_number += 1

    if not staged:
        for name in (_git(["ls-files", "--others", "--exclude-standard"], cwd) or "").splitlines():
            name = name.strip().replace("\\", "/")
            if not name or _is_noise(name):
                continue
            try:
                text = (cwd / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            added.extend((name, index, line) for index, line in enumerate(text.splitlines(), start=1))

    return added


def default_branch(cwd: Path) -> str:
    """The branch a PR would target. Falls back rather than failing."""
    head = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd)
    if head:
        return head.rsplit("/", 1)[-1]
    for candidate in ("main", "master", "develop"):
        if _git(["rev-parse", "--verify", "--quiet", candidate], cwd):
            return candidate
    return "main"


def head(cwd: Path) -> str | None:
    """The commit the working tree is built on, or ``None`` outside a checkout."""
    return _git(["rev-parse", "HEAD"], cwd)


def tree(cwd: Path) -> str | None:
    """The git tree this working tree would commit as, or ``None`` if git cannot say.

    HEAD alone does not identify what a test run saw: verification usually
    happens before the commit, on uncommitted changes. This hashes what is on
    disk -- tracked, modified and untracked, ignored files left out -- into the
    tree object a ``git add -A && git commit`` would produce, so evidence
    recorded before the commit still matches the commit that holds exactly that
    code, and stops matching the moment anything else changes.

    ``.workflow`` is left out: writing the evidence must not invalidate it.

    Built in a scratch copy of the index, so the user's staging area is never
    touched. The copy keeps git's stat cache, which is what keeps this cheap on
    a large checkout. The one side effect is loose blobs in the object store,
    which ``git gc`` collects like any other unreferenced object.
    """
    index = _git(["rev-parse", "--git-path", "index"], cwd)
    if index is None:
        return None
    source = Path(index) if Path(index).is_absolute() else Path(cwd) / index

    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "index"
        if source.is_file():
            shutil.copyfile(source, copy)
        env = {**os.environ, "GIT_INDEX_FILE": str(copy)}
        steps = (
            ["add", "-A", "--", ".", f":(exclude){ARTIFACT_DIR}"],
            ["rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", ARTIFACT_DIR],
        )
        for args in steps:
            if _git_env(args, cwd, env) is None:
                return None
        return _git_env(["write-tree"], cwd, env) or None


def _git_env(args: list[str], cwd: Path, env: dict) -> str | None:
    """As ``_git``, under an explicit environment; ``""`` for success with no output."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60, check=False, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def file_at(cwd: Path, ref: str, path: str) -> str | None:
    """A file's contents at a commit, or ``None`` if it was not there.

    Used to check a citation against the tree a plan was written against, so
    implementing the plan does not invalidate the claims that justified it.
    A missing answer is never an error: the caller falls back to the tree.
    """
    if not ref or not path:
        return None
    return _git_raw(["show", f"{ref}:{path.replace(chr(92), '/')}"], cwd)


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

    # The repo is the last segment on every shape above, including the Azure
    # one, where the project sits between the org and "_git".
    repo = parts[-1] if len(parts) > 1 else ""

    return Remote(url=cleaned, host=host, org=org.lower(), repo=repo)
