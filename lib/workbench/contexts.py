"""Context resolution: which tracker, which account, which quality preset.

Configuration is central (``~/.workbench``) and matched to a repo by rule, so a
new project needs no setup. Resolution is a fixed four-step ladder, first hit
wins, and the winning step is always reported -- a silent guess is a bug that
shows up weeks later as a ticket filed in the wrong tracker.

    1. ``.workflow/config.json`` in the repo   (explicit, committable, no secrets)
    2. ``WORKBENCH_CONTEXT`` environment variable
    3. ``~/.workbench/rules.json``             (git remote, then path prefix)
    4. nothing matched -> stop and ask

Secrets never live in any of these files; a context only names where its
credential is found.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitctx
from .errors import ConfigError, unknown_choice

PROVIDERS = ["jira", "azure"]
PRESETS = ["prototype", "solo-saas", "startup", "scaleup", "enterprise"]

REPO_CONFIG = Path(".workflow") / "config.json"

# Keys that must never appear in a config file. Catching them here is cheaper
# than explaining a leaked token later.
_FORBIDDEN_KEYS = {"pat", "token", "password", "secret", "api_key", "apikey", "api_token"}


@dataclass(frozen=True)
class Context:
    name: str
    provider: str
    base_url: str
    project: str
    auth: dict[str, str]
    preset: str
    git: dict[str, str] = field(default_factory=dict)
    board: str | None = None
    flow: dict | None = None


@dataclass(frozen=True)
class Resolution:
    context: Context
    source: str  # human-readable: which ladder step won, and via what


def home() -> Path:
    """Central config root. Overridable for tests and for shared setups."""
    override = os.environ.get("WORKBENCH_HOME")
    return Path(override) if override else Path.home() / ".workbench"


def contexts_dir() -> Path:
    return home() / "contexts"


def rules_path() -> Path:
    return home() / "rules.json"


def available() -> list[str]:
    directory = contexts_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))


def resolve(cwd: Path | None = None) -> Resolution:
    cwd = (cwd or Path.cwd()).resolve()

    from_repo = _from_repo_config(cwd)
    if from_repo is not None:
        return from_repo

    env_name = os.environ.get("WORKBENCH_CONTEXT")
    if env_name:
        return Resolution(load(env_name), source="WORKBENCH_CONTEXT")

    from_rules = _from_rules(cwd)
    if from_rules is not None:
        return from_rules

    raise _unresolved(cwd)


def load(name: str, overrides: dict[str, Any] | None = None) -> Context:
    path = contexts_dir() / f"{name}.json"
    if not path.is_file():
        names = available()
        raise ConfigError(
            f"no context named {name!r}",
            fix=[
                f"known contexts: {', '.join(names)}" if names else "no contexts defined yet",
                f"create one: wb ctx add {name} --provider jira|azure --base-url URL --project KEY",
            ],
        )

    data = _read_json(path)
    _reject_secrets(data, path)
    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})
    return _build(name, data, path)


def _build(name: str, data: dict[str, Any], path: Path) -> Context:
    provider = str(data.get("provider", "")).lower()
    if provider not in PROVIDERS:
        raise unknown_choice("provider", provider or "<missing>", PROVIDERS)

    preset = str(data.get("preset", "startup")).lower()
    if preset not in PRESETS:
        raise unknown_choice("preset", preset, PRESETS)

    missing = [key for key in ("base_url", "project") if not data.get(key)]
    if missing:
        raise ConfigError(
            f"context {name!r} is missing: {', '.join(missing)}",
            fix=[f"add the missing keys to {path}"],
        )

    base_url = str(data["base_url"]).rstrip("/")
    if not base_url.lower().startswith("https://") and not data.get("allow_insecure"):
        # Every request carries an Authorization header. Over http it is readable
        # by anything on the path.
        raise ConfigError(
            f"context {name!r} uses a non-HTTPS base_url",
            fix=[
                "use https:// -- the API token is sent on every request",
                'for an internal host with no TLS, set "allow_insecure": true in the context file',
            ],
        )

    auth = data.get("auth") or {}
    if not isinstance(auth, dict):
        raise ConfigError(f"context {name!r}: auth must be an object", fix=[f"see {path}"])

    git = data.get("git") or {}
    if not isinstance(git, dict):
        raise ConfigError(f"context {name!r}: git must be an object", fix=[f"see {path}"])

    return Context(
        name=name,
        provider=provider,
        base_url=base_url,
        project=str(data["project"]),
        auth={str(k): str(v) for k, v in auth.items()},
        preset=preset,
        git={str(k): str(v) for k, v in git.items()},
        board=str(data["board"]) if data.get("board") else None,
        flow=data.get("flow") if isinstance(data.get("flow"), dict) else None,
    )


def _from_repo_config(cwd: Path) -> Resolution | None:
    root = gitctx.repo_root(cwd) or cwd
    path = root / REPO_CONFIG
    if not path.is_file():
        return None

    data = _read_json(path)
    _reject_secrets(data, path)

    name = data.get("context")
    if not name:
        raise ConfigError(
            f"{path} has no \"context\" key",
            fix=['add {"context": "<name>"}, naming a context in ~/.workbench/contexts'],
        )

    overrides = {k: v for k, v in data.items() if k in {"project", "preset", "board", "flow"}}
    return Resolution(load(str(name), overrides), source=f"repo config ({path})")


def _from_rules(cwd: Path) -> Resolution | None:
    path = rules_path()
    if not path.is_file():
        return None

    rules = _read_json(path)
    if not isinstance(rules, list):
        raise ConfigError(f"{path} must contain a JSON array of rules", fix=["see docs/configuration.md"])

    remote = gitctx.origin(cwd)

    # Remote rules first: a remote is stronger evidence than where a clone sits
    # on disk. Within each pass, file order decides.
    for want_remote in (True, False):
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ConfigError(f"{path}: rule {index} is not an object", fix=["see docs/configuration.md"])
            match = rule.get("match") or {}
            has_remote = bool(match.get("remote_host") or match.get("org"))
            if has_remote != want_remote:
                continue
            if _matches(match, remote, cwd):
                name = rule.get("context")
                if not name:
                    raise ConfigError(f"{path}: rule {index} has no \"context\"", fix=["add a context name"])
                return Resolution(load(str(name)), source=f"rules.json rule {index}")
    return None


def _matches(match: dict[str, Any], remote: gitctx.Remote | None, cwd: Path) -> bool:
    host = match.get("remote_host")
    org = match.get("org")
    if host or org:
        if remote is None:
            return False
        if host and remote.host != str(host).lower():
            return False
        if org and remote.org != str(org).lower():
            return False

    prefix = match.get("path_prefix")
    if prefix:
        try:
            wanted = Path(str(prefix)).expanduser().resolve()
        except OSError:
            return False
        if wanted != cwd and wanted not in cwd.parents:
            return False

    return bool(host or org or prefix)


def _unresolved(cwd: Path) -> ConfigError:
    names = available()
    remote = gitctx.origin(cwd)
    detail = f"remote {remote.host}/{remote.org}" if remote else "no git remote"
    return ConfigError(
        f"no context matches this repo ({detail})",
        fix=[
            f"known contexts: {', '.join(names)}" if names else "no contexts defined yet -- run: wb ctx add --help",
            "pick one for this repo:  wb ctx use <name>",
            "or match it for every repo like it:  wb ctx use <name> --remember remote|path",
        ],
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}", fix=["fix the syntax, or delete the file"]) from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}", fix=["check permissions"]) from exc


def _reject_secrets(data: Any, path: Path) -> None:
    """Config files hold references, never credentials."""
    if not isinstance(data, dict):
        return
    for key in data:
        if str(key).lower() in _FORBIDDEN_KEYS:
            raise ConfigError(
                f"{path} contains a credential key ({key!r})",
                fix=[
                    "remove it -- config files are committable and must stay secret-free",
                    'reference an environment variable instead: "auth": {"pat_env": "..."}',
                ],
            )
