"""The ``.workflow/`` directory: how skills hand work to each other.

Chaining goes through files, not conversation. A later skill reads the artifact
a previous one wrote -- and reads only the slice it needs -- so a plan is paid
for once no matter how many steps consume it.

Layout, one directory per ticket:

    .workflow/<KEY>/triage.json   sdd.json   sdd.md   audit.json
                    evidence.md   commit.txt  pr.md    .cache/
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from . import gitctx
from .errors import ConfigError, NotFoundError, UsageError

WORKFLOW_DIR = ".workflow"
CACHE_TTL_SECONDS = 15 * 60

# Keys become path segments, so they are validated, not trusted.
_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$|^\d+$")
# Work that starts before a ticket exists -- an idea being framed, an incident
# being traced -- still needs somewhere to put its artifacts.
_SLUG_PATTERN = re.compile(r"^(idea|incident)-[a-z0-9][a-z0-9-]{0,40}$")


def validate_key(key: str) -> str:
    cleaned = key.strip()
    if _SLUG_PATTERN.match(cleaned.lower()):
        return cleaned.lower()
    if not _KEY_PATTERN.match(cleaned):
        raise UsageError(
            f"malformed key: {key!r}",
            fix=[
                "Jira keys look like ABC-123; Azure work item ids are plain numbers",
                "work with no ticket yet uses idea-<slug> or incident-<slug>",
            ],
        )
    return cleaned.upper()


def root(cwd: Path | None = None) -> Path:
    cwd = (cwd or Path.cwd()).resolve()
    return (gitctx.repo_root(cwd) or cwd) / WORKFLOW_DIR


def ticket_dir(key: str, cwd: Path | None = None) -> Path:
    return root(cwd) / validate_key(key)


def write_json(key: str, name: str, data: Any, cwd: Path | None = None) -> Path:
    path = ticket_dir(key, cwd) / name
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def write_text(key: str, name: str, text: str, cwd: Path | None = None) -> Path:
    path = ticket_dir(key, cwd) / name
    _write_atomic(path, text if text.endswith("\n") else text + "\n")
    return path


def read_json(key: str, name: str, cwd: Path | None = None) -> Any:
    path = ticket_dir(key, cwd) / name
    if not path.is_file():
        raise NotFoundError(
            f"{path} does not exist",
            fix=[f"run the skill that produces {name} first"],
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}", fix=["delete it and regenerate"]) from exc


def cache_get(key: str, name: str, cwd: Path | None = None, ttl: int = CACHE_TTL_SECONDS) -> Any | None:
    """Return a cached payload, or ``None`` when absent or stale.

    Repeating a fetch inside one session should cost no network. A miss is
    never an error -- callers just fetch.
    """
    path = ticket_dir(key, cwd) / ".cache" / name
    try:
        if time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(key: str, name: str, data: Any, cwd: Path | None = None) -> None:
    path = ticket_dir(key, cwd) / ".cache" / name
    try:
        _write_atomic(path, json.dumps(data, ensure_ascii=False))
    except OSError:
        pass  # A cache that cannot be written is a slowdown, not a failure.


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
