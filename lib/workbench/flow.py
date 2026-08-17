"""Branching flow: where work starts, where it lands, and how it gets carried.

Teams do not agree on this and never will, so it is configuration rather than
an assumption. The shape that prompted it:

    branch from main -> PR to main, left open
    branch from homolog -> cherry-pick those commits -> PR to homolog -> beta
    the main PR merges once beta passes

That is one **source** target and one or more **validation** targets that carry
the same commits. Trunk-based work is the same model with no validation
targets, so both are the same code path.

The commits to carry are computed, not remembered. Cherry-picking the wrong
range, or the right range in the wrong order, is a real and common cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import gitctx
from .errors import ConfigError, UsageError

STRATEGIES = ["cherry-pick", "merge", "trunk"]

DEFAULT_PATTERN = "{key}-{slug}"
SLUG_MAX = 32

# Branch names people actually use, most authoritative first. Only consulted
# when nothing is configured.
COMMON_SOURCES = ("main", "master", "trunk")
COMMON_VALIDATION = ("homolog", "homologacao", "staging", "stage", "beta", "qa", "uat", "develop", "development", "dev")

_PATTERN_FIELD = re.compile(r"\{(key|slug|type)\}")
_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


@dataclass
class Target:
    branch: str
    role: str  # "source" or "validation"

    def to_dict(self) -> dict:
        return {"branch": self.branch, "role": self.role}


@dataclass
class Flow:
    source: Target
    validation: list[Target] = field(default_factory=list)
    strategy: str = "cherry-pick"
    pattern: str = DEFAULT_PATTERN
    protected: list[str] = field(default_factory=list)
    detected: bool = False

    @property
    def targets(self) -> list[Target]:
        return [self.source, *self.validation]

    def target(self, branch: str) -> Target:
        for candidate in self.targets:
            if candidate.branch == branch:
                return candidate
        raise UsageError(
            f"{branch!r} is not a target in this flow",
            fix=[f"targets: {', '.join(t.branch for t in self.targets)}"],
        )

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "source": self.source.to_dict(),
            "validation": [t.to_dict() for t in self.validation],
            "branch_pattern": self.pattern,
            "protected": self.protected,
            "detected": self.detected,
        }


def load(config: dict | None, root: Path) -> Flow:
    """Configured flow, or one detected from the remote branches."""
    if not config:
        return detect(root)

    strategy = str(config.get("strategy", "cherry-pick"))
    if strategy not in STRATEGIES:
        raise ConfigError(
            f"unknown flow strategy: {strategy!r}",
            fix=[f"strategies: {', '.join(STRATEGIES)}"],
        )

    source = config.get("source")
    if not source:
        raise ConfigError('flow needs a "source" branch', fix=['e.g. "source": "main"'])

    validation = [Target(branch=str(b), role="validation") for b in config.get("validation") or []]
    protected = [str(b) for b in config.get("protected") or []] or [str(source)]

    return Flow(
        source=Target(branch=str(source), role="source"),
        validation=validation,
        strategy=strategy,
        pattern=str(config.get("branch_pattern") or DEFAULT_PATTERN),
        protected=protected,
    )


def detect(root: Path) -> Flow:
    """Guess from what the remote actually has. Proposes; never persists."""
    remote = {name.split("/", 1)[-1] for name in gitctx.remote_branches(root)}

    source = next((name for name in COMMON_SOURCES if name in remote), None) or gitctx.default_branch(root)
    validation = [Target(branch=name, role="validation") for name in COMMON_VALIDATION if name in remote]

    return Flow(
        source=Target(branch=source, role="source"),
        validation=validation,
        strategy="cherry-pick" if validation else "trunk",
        pattern=detect_pattern(gitctx.remote_branches(root)) or DEFAULT_PATTERN,
        protected=[source, *(t.branch for t in validation)],
        detected=True,
    )


def detect_pattern(branches: list[str]) -> str | None:
    """Read the naming convention off branches that already exist."""
    prefixes: dict[str, int] = {}
    bare_key = 0
    counted = 0

    for raw in branches:
        name = raw.split("/", 1)[-1] if raw.startswith("origin/") else raw
        if name in {"HEAD", *COMMON_SOURCES, *COMMON_VALIDATION}:
            continue
        counted += 1
        if "/" in name:
            prefixes[name.split("/", 1)[0]] = prefixes.get(name.split("/", 1)[0], 0) + 1
        elif re.match(r"^[A-Za-z]+-\d+", name):
            bare_key += 1

    if counted < 3:
        return None

    top = max(prefixes.items(), key=lambda item: item[1], default=None)
    if top and top[1] / counted >= 0.6:
        return f"{top[0]}/{{key}}-{{slug}}"
    if bare_key / counted >= 0.6:
        return DEFAULT_PATTERN
    return None


def branch_name(flow: Flow, key: str, title: str, kind: str = "feature") -> str:
    name = flow.pattern
    name = name.replace("{key}", key)
    name = name.replace("{slug}", slugify(title))
    name = name.replace("{type}", kind)
    return name.strip("/-")


def slugify(title: str) -> str:
    slug = _SLUG_CLEAN.sub("-", title.lower()).strip("-")
    if len(slug) <= SLUG_MAX:
        return slug
    cut = slug[:SLUG_MAX]
    return cut.rsplit("-", 1)[0] if "-" in cut else cut


def carry_plan(root: Path, source_branch: str, base: str, onto: str) -> list[str]:
    """The commits to cherry-pick, oldest first.

    Order is the whole point: applied newest-first, a series that builds on
    itself conflicts on every commit after the first.
    """
    return gitctx.commits_between(root, base, source_branch)


def validate_pattern(pattern: str) -> str:
    if "{key}" not in pattern:
        raise UsageError(
            "a branch pattern must contain {key}",
            fix=["e.g. feature/{key}-{slug}, or {key}-{slug}"],
        )
    unknown = set(re.findall(r"\{(\w+)\}", pattern)) - {"key", "slug", "type"}
    if unknown:
        raise UsageError(
            f"unknown placeholder(s): {', '.join(sorted(unknown))}",
            fix=["available: {key}, {slug}, {type}"],
        )
    return pattern
