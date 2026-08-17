"""Secret resolution.

A context file names *where* a credential lives; it never holds the credential.
Two sources, in order of preference:

- ``pat_env``      -- an environment variable name. Works everywhere.
- ``pat_keychain`` -- an OS keychain entry. Opt-in, platform dependent.

Anything resolved here is registered with :mod:`redact` before it is returned,
so it cannot reach stdout, stderr or a traceback afterwards.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import redact
from .errors import AuthError, ConfigError

_KEYCHAIN_SERVICE = "workbench"


def resolve(auth: dict, context_name: str) -> str:
    """Return the credential for a context's ``auth`` block."""
    env_name = auth.get("pat_env")
    keychain_key = auth.get("pat_keychain")

    if env_name and keychain_key:
        raise ConfigError(
            f"context {context_name!r} sets both pat_env and pat_keychain",
            fix=["keep exactly one of pat_env / pat_keychain in the auth block"],
        )

    if env_name:
        value = os.environ.get(env_name)
        if not value:
            raise AuthError(
                f"environment variable {env_name} is unset or empty",
                fix=[
                    f"set {env_name} to the API token for context {context_name!r}",
                    "restart the shell (and the agent) so the variable is visible",
                ],
            )
        redact.register(value)
        return value

    if keychain_key:
        value = _from_keychain(keychain_key)
        redact.register(value)
        return value

    raise ConfigError(
        f"context {context_name!r} has no credential reference",
        fix=['add "auth": {"pat_env": "SOME_ENV_VAR"} to the context file'],
    )


def _from_keychain(key: str) -> str:
    if sys.platform == "darwin":
        cmd = ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-a", key, "-w"]
    elif sys.platform.startswith("linux"):
        cmd = ["secret-tool", "lookup", "service", _KEYCHAIN_SERVICE, "account", key]
    else:
        raise ConfigError(
            f"pat_keychain is not supported on {sys.platform}",
            fix=[
                "use pat_env instead, naming an environment variable",
                "see docs/configuration.md for per-platform options",
            ],
        )

    if shutil.which(cmd[0]) is None:
        raise ConfigError(
            f"{cmd[0]} not found on PATH, cannot read the keychain",
            fix=[f"install {cmd[0]}, or switch the context to pat_env"],
        )

    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthError(f"keychain lookup failed: {exc}", fix=["switch the context to pat_env"]) from exc

    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise AuthError(
            f"no keychain entry {key!r} under service {_KEYCHAIN_SERVICE!r}",
            fix=[f"store it, or switch the context to pat_env"],
        )
    return value
