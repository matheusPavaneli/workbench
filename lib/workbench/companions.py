"""Files that follow a planned change through the scope guard unlisted.

A test, a type declaration, a lockfile and generated code change mechanically
when a planned file changes. Making the plan list every one of them is
ceremony, and forgetting one stopped a session mid-implementation over a file
nobody would call unplanned work.

A companion is released only by its **tie to a planned file**, and that tie is
what keeps this from becoming a hole through the guard:

- a test is released by the source it tests, never by looking like a test;
- a lockfile is released by its manifest, and a lockfile changed without one
  stays a deviation -- unplanned dependency drift is what the guard is for;
- generated code is released by a glob the repo itself declared.

A path in a critical zone is never a companion, whatever it matches: the raised
bar there is exactly the bar this would otherwise lower.

``impl check`` and the pre-tool-use hook both call ``reason``, so the two can
never disagree about the same path.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

# Language families: a test is tied to a source in the same family. A .js test
# of a .ts source is ordinary; a .py test of a .go source is a coincidence.
FAMILIES = {
    ".py": "python",
    ".go": "go",
    **{ext: "js" for ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")},
}

# Lockfile -> the manifests that own it, in the same directory.
LOCKFILES = {
    "package-lock.json": ("package.json",),
    "npm-shrinkwrap.json": ("package.json",),
    "yarn.lock": ("package.json",),
    "pnpm-lock.yaml": ("package.json",),
    "bun.lockb": ("package.json",),
    "bun.lock": ("package.json",),
    "poetry.lock": ("pyproject.toml",),
    "uv.lock": ("pyproject.toml",),
    "pdm.lock": ("pyproject.toml",),
    "Pipfile.lock": ("Pipfile",),
    "go.sum": ("go.mod",),
    "Cargo.lock": ("Cargo.toml",),
    "Gemfile.lock": ("Gemfile",),
    "composer.lock": ("composer.json",),
}

GENERATED_KEY = "generated"


def reason(path: str, planned: set[str] | list[str], generated: list[str] | None = None) -> str | None:
    """The tie that releases ``path``, or ``None`` if it is not a companion.

    ``planned`` is the plan's own file list. ``generated`` is the repo's
    ``generated`` globs (``generated_globs(root)``); none, no release.
    """
    from .profile import critical_zones

    path = _normal(path)
    planned_set = {_normal(item) for item in planned if item}
    if not path or path in planned_set or critical_zones([path]):
        return None
    return (
        _test_of(path, planned_set)
        or _declaration_of(path, planned_set)
        or _lockfile_of(path, planned_set)
        or _generated(path, generated or [])
    )


def generated_globs(root) -> list[str]:
    """The ``generated`` globs from .workflow/config.json; anything else is none."""
    from .profile import repo_config

    raw = repo_config(root).get(GENERATED_KEY)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


def _normal(path: str) -> str:
    return str(path).replace("\\", "/").removeprefix("./")


def _test_of(path: str, planned: set[str]) -> str | None:
    subject = test_subject(path)
    if subject is None:
        return None
    stem, family = subject
    for candidate in sorted(planned):
        pure = PurePosixPath(candidate)
        if test_subject(candidate) is None and FAMILIES.get(pure.suffix) == family and _stem(pure) == stem:
            return f"test of {candidate}"
    return None


def test_subject(path: str) -> tuple[str, str] | None:
    """``(stem, family)`` of the source a test file tests, or ``None``.

    The markers: test_x.py and x_test.py, x_test.go, x.test.* and x.spec.* in
    the js/ts family, and any file under a __tests__ directory.
    """
    pure = PurePosixPath(path)
    family = FAMILIES.get(pure.suffix)
    if family is None:
        return None
    name = _stem(pure)

    for marker in (".test", ".spec"):
        if family == "js" and name.endswith(marker):
            return name[: -len(marker)], family
    if family == "python" and name.startswith("test_") and len(name) > len("test_"):
        return name[len("test_"):], family
    if family in ("python", "go") and name.endswith("_test") and len(name) > len("_test"):
        return name[: -len("_test")], family
    if "__tests__" in pure.parts[:-1]:
        return name, family
    return None


def _stem(pure: PurePosixPath) -> str:
    return pure.name[: -len(pure.suffix)] if pure.suffix else pure.name


def _declaration_of(path: str, planned: set[str]) -> str | None:
    if not path.endswith(".d.ts"):
        return None
    base = path[: -len(".d.ts")]
    for suffix in (".ts", ".js"):
        if base + suffix in planned:
            return f"declaration of {base + suffix}"
    return None


def _lockfile_of(path: str, planned: set[str]) -> str | None:
    pure = PurePosixPath(path)
    for manifest in LOCKFILES.get(pure.name, ()):
        owner = str(pure.parent / manifest) if str(pure.parent) != "." else manifest
        if owner in planned:
            return f"lockfile of {owner}"
    return None


def _generated(path: str, globs: list[str]) -> str | None:
    for pattern in globs:
        glob = _normal(pattern)
        if fnmatch.fnmatchcase(path, glob) or (glob.endswith("/**") and path.startswith(glob[:-3] + "/")):
            return f"generated, matches {pattern}"
    return None
