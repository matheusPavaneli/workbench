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
    # A validation branch receives commits through a PR like the source does, so
    # it is protected unless the config says otherwise. Defaulting to the source
    # alone left a hand-written config declaring a validation target with that
    # target unprotected -- which ``wb flow set`` never produces, and which the
    # commit precondition then read as permission.
    protected = [str(b) for b in config.get("protected") or []] or [
        str(source),
        *(target.branch for target in validation),
    ]

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


def resolve(root: Path) -> Flow:
    """Repo config wins over context, context over detection.

    Every caller that needs "what is protected here" goes through this. It used
    to live in ``cli/flow.py`` alone, so ``wb git`` resolved the flow by calling
    ``load(None, root)`` -- which skips straight to detection and guesses the
    protected branches from the remote. A repo that had recorded
    ``--source develop --validation release/*`` got back ``["main"]``, and a
    commit onto a branch it had declared protected passed the check.
    """
    from . import contexts

    config = _repo_flow(root)
    if config is None:
        try:
            config = contexts.resolve(root).context.flow
        except Exception:  # noqa: BLE001 - no context just means fall through to detection
            config = None
    return load(config, root)


def _repo_flow(root: Path) -> dict | None:
    from . import contexts

    path = root / contexts.REPO_CONFIG
    if not path.is_file():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("flow") if isinstance(data, dict) else None


def protected(root: Path) -> list[str]:
    """Branches a write must never land on. Fails closed.

    A flow that cannot be resolved is not permission to commit anywhere: the
    fallback is the conventional set, so an unresolvable flow blocks the branch
    names that are protected in practice rather than blocking nothing.
    """
    try:
        return resolve(root).protected
    except Exception:  # noqa: BLE001
        return [*COMMON_SOURCES, *COMMON_VALIDATION]


def carry_base(root: Path, source: str) -> str:
    """The ref to measure "already on the source" against.

    ``origin/main`` when it exists, not local ``main``. A local source branch is
    only as fresh as the last time somebody checked it out and pulled, and a
    stale one makes the range too wide: commits already merged upstream come
    back into the carry and get picked onto the validation branch a second time.

    Nothing else in this flow reads a local branch either -- ``start`` and
    ``carry`` both branch from ``origin/<base>`` -- so this closes the one place
    that still did.
    """
    remote = f"origin/{source}"
    return remote if gitctx.branch_exists(root, remote) else source


def fetch_action() -> "object":
    """Refresh the remote-tracking refs. Runs before anything is measured."""
    from . import gitrun

    return gitrun.Action(["fetch", "origin"], why="so the base and target are current")


def start_actions(name: str, base: str) -> list:
    """Fetch, then branch. One list, whether it gets printed or run.

    Both forms came from the same computation before, but the rendering was
    duplicated -- and a duplicated rendering is how ``--execute`` ends up
    running something other than what it printed.
    """
    from . import gitrun

    return [
        fetch_action(),
        gitrun.Action(
            ["switch", "-c", name, f"origin/{base}"],
            why=f"start {name} from {base}",
            precondition=gitrun.CLEAN_TREE,
        ),
    ]


def carry_actions(carry_branch: str, target: str, commits: list[str]) -> list:
    """Branch off the validation target, then pick the series oldest first.

    No fetch here: the caller has already run one, because the commit range had
    to be computed against refs that were current when it was computed.
    """
    from . import gitrun

    hashes = [line.split(" ", 1)[0] for line in commits]
    return [
        gitrun.Action(
            ["switch", "-c", carry_branch, f"origin/{target}"],
            why=f"carry onto {target}",
            precondition=gitrun.CLEAN_TREE,
        ),
        gitrun.Action(["cherry-pick", *hashes], why=f"{len(hashes)} commit(s), oldest first"),
    ]
