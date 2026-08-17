"""``wb doctor`` -- everything that has to be true, checked in one place.

``ctx test`` proves a credential works. It does not prove git is present, that
the checkout will commit under the right identity, that the runner named in a
plan's ``verify`` list exists, or that ``.workflow/`` is not about to be
committed. Those failures used to surface one at a time, several commands into
a session, each one a separate round trip to diagnose.

Every check answers the same three things: what was examined, what was found,
and -- when it is wrong -- the exact command that fixes it. Checks never abort
the run: a missing tracker credential must not hide a misconfigured git author
further down the list.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .. import contexts, gitctx, profile as profile_lib, providers, secrets, verify as verify_lib
from ..artifacts import WORKFLOW_DIR
from ..errors import EXIT_CONFIG, WbError

OK = "ok"
WARN = "warn"
FAIL = "fail"

MIN_PYTHON = (3, 9)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("doctor", help="check everything this repo needs, in one pass")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the tracker round trip; check only what can be answered locally",
    )


def run(args: argparse.Namespace) -> int:
    cwd = Path.cwd().resolve()
    root = gitctx.repo_root(cwd)
    results: list[tuple[str, str, str, list[str]]] = []

    def check(name: str, state: str, detail: str, fix: list[str] | None = None) -> None:
        results.append((name, state, detail, fix or []))

    _python(check)
    _git(check, cwd, root)
    resolution = _context(check, cwd)
    _credential(check, resolution)
    if not args.offline:
        _tracker(check, resolution)
    _identity(check, cwd, resolution)
    _flow(check, root or cwd)
    _runners(check, root or cwd)
    _ignore(check, root)

    width = max(len(name) for name, *_ in results)
    for name, state, detail, fix in results:
        stream = sys.stderr if state == FAIL else sys.stdout
        print(f"{state:<4} {name:<{width}}  {detail}", file=stream)
        for step in fix:
            print(f"       {' ' * width}  fix: {step}", file=stream)

    failed = sum(1 for _, state, *_ in results if state == FAIL)
    warned = sum(1 for _, state, *_ in results if state == WARN)
    print(f"\n{len(results)} checks, {failed} failed, {warned} warning(s)")
    return EXIT_CONFIG if failed else 0


# ---- checks -------------------------------------------------------------


def _python(check) -> None:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < MIN_PYTHON:
        check("python", FAIL, f"{text}, below the supported {'.'.join(map(str, MIN_PYTHON))}",
              ["install a newer Python, or point the plugin at one"])
    else:
        check("python", OK, text)


def _git(check, cwd: Path, root: Path | None) -> None:
    if shutil.which("git") is None:
        check("git", FAIL, "not on PATH", ["install git; every artifact path is resolved from the repo root"])
        return
    if root is None:
        check("git", WARN, f"{cwd} is not a git checkout",
              ["artifacts will land under the working directory instead of a repo root"])
        return
    check("git", OK, str(root))


def _context(check, cwd: Path):
    try:
        resolution = contexts.resolve(cwd)
    except WbError as exc:
        check("context", FAIL, exc.message, exc.fix)
        return None
    context = resolution.context
    check("context", OK, f"{context.name} ({context.provider}, {context.preset}) via {resolution.source}")
    return resolution


def _credential(check, resolution) -> None:
    if resolution is None:
        check("credential", WARN, "skipped: no context resolved", [])
        return
    context = resolution.context
    if context.provider == "local":
        check("credential", OK, "not needed: the local provider makes no requests")
        return
    if context.provider == "github" and not (context.auth.get("pat_env") or context.auth.get("pat_keychain")):
        found = shutil.which("gh") is not None
        check("credential", OK if found else FAIL,
              "gh auth token" if found else "no token configured and gh is not on PATH",
              [] if found else ["gh auth login", "or: wb ctx add <name> --provider github --pat-env GITHUB_TOKEN"])
        return
    try:
        secrets.resolve(context.auth, context.name)
    except WbError as exc:
        check("credential", FAIL, exc.message, exc.fix)
        return
    source = context.auth.get("pat_env") or context.auth.get("pat_keychain")
    check("credential", OK, f"resolved from {source}")


def _tracker(check, resolution) -> None:
    if resolution is None:
        check("tracker", WARN, "skipped: no context resolved", [])
        return
    try:
        identity = providers.for_context(resolution.context).probe()
    except WbError as exc:
        check("tracker", FAIL, exc.message, exc.fix)
        return
    check("tracker", OK, f"{identity.account} -- {identity.detail}")


def _identity(check, cwd: Path, resolution) -> None:
    actual = gitctx.identity(cwd)
    email = actual.get("email") or ""
    if not email:
        check("git author", FAIL, "this checkout has no user.email",
              ['git config user.email "you@example.com"'])
        return
    expected = (resolution.context.git.get("email") if resolution else "") or ""
    if expected and expected.lower() != email.lower():
        check("git author", FAIL, f"{email}, but this context expects {expected}",
              [f'git config user.email "{expected}"'])
        return
    check("git author", OK, email)


def _flow(check, root: Path) -> None:
    from .. import flow as flow_lib

    try:
        resolution = contexts.resolve(root)
        config = (resolution.context.flow or {})
    except WbError:
        config = {}
    try:
        flow = flow_lib.load(config or None, root)
    except WbError as exc:
        check("flow", FAIL, exc.message, exc.fix)
        return

    described = f"{flow.strategy}, source {flow.source.branch}"
    if flow.validation:
        described += f", validation {', '.join(t.branch for t in flow.validation)}"
    if flow.detected:
        check("flow", WARN, f"{described} -- detected, not recorded",
              [f"record it: wb flow set --source {flow.source.branch}"])
        return
    check("flow", OK, described)


def _runners(check, root: Path) -> None:
    """A plan's verify commands are worthless if the runner is not installed."""
    conventions = profile_lib.detect(root).conventions
    wanted = [conventions.get("test_runner"), conventions.get("package_manager")]
    wanted = [w for w in wanted if w]

    if not wanted:
        check("runners", OK, "no runner detected from the repo; verify commands will name their own")
        return

    missing = [name for name in wanted if shutil.which(name) is None]
    allowed = [name for name in wanted if name not in verify_lib.ALLOWED_RUNNERS]

    if missing:
        check("runners", FAIL, f"{', '.join(missing)} not on PATH but used by this repo",
              [f"install {missing[0]}, or wb impl verify will refuse the commands that need it"])
        return
    if allowed:
        check("runners", WARN, f"{', '.join(allowed)} is not in the verify allowlist",
              ["wb impl verify will refuse it; run it yourself and record the result"])
        return
    check("runners", OK, ", ".join(wanted))


def _ignore(check, root: Path | None) -> None:
    if root is None:
        check("gitignore", WARN, "skipped: not a git checkout", [])
        return
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", f"{WORKFLOW_DIR}/scratch"],
            cwd=str(root), capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        check("gitignore", WARN, "could not ask git whether .workflow is ignored", [])
        return
    if completed.returncode == 0:
        check("gitignore", OK, f"{WORKFLOW_DIR}/ is ignored")
        return
    check("gitignore", WARN, f"{WORKFLOW_DIR}/ is not ignored; artifacts will show up in every diff",
          [f"add to .gitignore:  {WORKFLOW_DIR}/*", f"then un-ignore what you share:  !{WORKFLOW_DIR}/config.json"])
