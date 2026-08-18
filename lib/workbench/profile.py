"""Repo profiling: which quality bar applies, and where it is higher.

The preset is detected from evidence in the repo, not asked and not inferred by
a model at runtime -- if it lived in the model's judgement, two sessions on the
same repo would hold the work to two different bars.

Detection proposes; the user decides. ``wb repo profile --set`` overrides, and
the override is what gets written.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CI_MARKERS = (".github/workflows", ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile", ".circleci")
OWNERSHIP_MARKERS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
MIGRATION_MARKERS = ("migrations", "db/migrate", "alembic", "prisma/migrations", "supabase/migrations")
TEST_MARKERS = ("tests", "test", "spec", "__tests__")

# Paying users cannot be detected directly, but the machinery to charge them
# can. This is the difference between a prototype and a solo product.
MONEY_PATTERN = re.compile(r"stripe|paddle|lemonsqueezy|billing|subscription|checkout|invoice", re.IGNORECASE)

# Areas where the bar rises regardless of preset: rigour is not uniform inside
# a repo. Matched against the path of every file a plan proposes to touch.
CRITICAL_ZONES = {
    "billing": re.compile(r"billing|payment|invoice|subscription|checkout|stripe|paddle|quota|plan", re.I),
    "auth": re.compile(r"auth|login|session|password|token|oauth|permission|role|acl", re.I),
    # "user", "account", "pii" and "personal" name user data wherever they
    # appear. "profile" does not: it is just as often a performance profile or,
    # here, the repo quality profiler -- and a zone that fires on the wrong file
    # raises the bar for no reason, which is how a raised bar stops meaning
    # anything. It therefore only counts as a directory segment.
    "user-data": re.compile(r"(^|/)(users?|accounts?|pii|personal)(/|_|\.)|(^|/)profiles?/", re.I),
    "migration": re.compile(r"migrat|schema|alembic|liquibase", re.I),
    "secrets": re.compile(r"secret|credential|keystore|vault|\.env", re.I),
}

PRESETS = ["prototype", "solo-saas", "startup", "scaleup", "enterprise"]

# The floor. Applies to every preset, prototype included. A preset raises the
# bar; it never lowers these.
FLOOR = [
    "unit test for every changed or added logic branch",
    "every bug fix lands with a regression test that fails without it",
    "no error swallowed silently; preserve the cause",
    "no secret in code or in a committed file",
    "state a rollback path before implementing",
]

PRESET_GATES = {
    "prototype": [
        "prefer reversible over robust; no infrastructure that outlives the experiment",
        "no new dependency without a one-line justification",
    ],
    "solo-saas": [
        "operating budget is one person: managed over self-hosted, nothing that needs on-call tuning",
        "money paths (billing, quota, plan limits, trials) are tested to critical-infrastructure rigour",
        "state the product effect: which metric moves, cost per user, pricing impact, who asked",
        "support cost is a design criterion; a feature that generates recurring tickets is rejected",
        "anything user-facing ships behind a flag or with a one-step rollback",
    ],
    "startup": [
        "no new dependency or service without a stated owner",
        "state the product effect: which metric moves, who asked",
        "user-facing changes ship behind a flag or with a one-step rollback",
    ],
    "scaleup": [
        "migrations are ordered and reversible; read and write paths stay compatible during rollout",
        "changes land behind a feature flag with a staged rollout",
        "state the blast radius: which services and teams consume this",
    ],
    "enterprise": [
        "backwards compatibility for every consumer of a changed contract; deprecate, do not break",
        "migrations ordered and reversible; expand-migrate-contract, never a breaking single step",
        "rollout plan with stages and abort criteria; runbook entry for anything on-call may see",
        "state the blast radius across services and owning teams",
        "changes to critical zones need a named reviewer from the owning team",
    ],
}


HIGH = "high"
LOW = "low"

# Presets, weakest bar first. Used to pick a winner when a change spans two of
# them: rigour goes to the highest, never to the average.
RANK = {name: index for index, name in enumerate(PRESETS)}

# A repo that builds several things at once has no single answer, and a single
# answer is exactly what detection would otherwise hand back with full
# confidence. Workspace declarations are the cheap, exact signal.
WORKSPACE_MARKERS = ("pnpm-workspace.yaml", "go.work", "lerna.json", "nx.json", "turbo.json", "rush.json")
PACKAGE_DIRS = ("packages", "apps", "services", "libs")


@dataclass
class Profile:
    preset: str
    detected: str
    signals: list[str] = field(default_factory=list)
    conventions: dict[str, str] = field(default_factory=dict)
    confidence: str = HIGH
    alternatives: list[str] = field(default_factory=list)
    confirmed: bool = False

    @property
    def needs_confirmation(self) -> bool:
        """Low confidence is only a problem while nobody has looked at it."""
        return self.confidence == LOW and not self.confirmed

    def gates(self) -> list[str]:
        return FLOOR + PRESET_GATES.get(self.preset, [])

    def to_dict(self) -> dict:
        return {
            "preset": self.preset,
            "detected": self.detected,
            "confidence": self.confidence,
            "confirmed": self.confirmed,
            "alternatives": self.alternatives,
            "signals": self.signals,
            "conventions": self.conventions,
        }


def detect(root: Path) -> Profile:
    signals: list[str] = []

    has_ci = _any_exists(root, CI_MARKERS)
    has_owners = _any_exists(root, OWNERSHIP_MARKERS)
    has_migrations = _any_exists(root, MIGRATION_MARKERS)
    has_tests = _any_exists(root, TEST_MARKERS)
    has_money = _mentions_money(root)
    is_monorepo = _is_monorepo(root)
    contributors = _contributors(root)

    for label, present in (
        ("ci", has_ci),
        ("codeowners", has_owners),
        ("migrations", has_migrations),
        ("tests", has_tests),
        ("money-paths", has_money),
        ("monorepo", is_monorepo),
    ):
        if present:
            signals.append(label)
    signals.append(f"contributors={contributors if contributors is not None else 'unknown'}")

    # Each branch states its own confidence next to the preset it picks, so the
    # two can never drift apart. A guess that reads as a finding is the failure
    # this exists to prevent: the detection was always allowed to be wrong, and
    # was never allowed to be wrong *silently*.
    if has_owners:
        preset, confidence, alternatives = "enterprise", HIGH, []
    elif contributors is None:
        # No evidence of team size is not evidence of a small team. Fall back to
        # the middle, never to the bottom -- and say the evidence is missing.
        preset = "scaleup" if has_migrations else "startup"
        confidence, alternatives = LOW, ["solo-saas", "enterprise"]
    elif contributors >= 8:
        preset, confidence, alternatives = "enterprise", HIGH, []
    elif contributors >= 3:
        preset = "scaleup" if has_migrations else "startup"
        confidence, alternatives = HIGH, []
    elif has_money:
        preset, confidence, alternatives = "solo-saas", HIGH, []
    elif has_ci or has_tests:
        # CI on a one-person repo says someone was careful, not who it is for.
        preset, confidence, alternatives = "startup", LOW, ["prototype", "solo-saas"]
    else:
        preset, confidence, alternatives = "prototype", HIGH, []

    if is_monorepo:
        # One repo, several products, one bar: wrong by construction, whatever
        # the signals say. preset_paths is the fix; this is how it gets asked for.
        confidence = LOW
        alternatives = [name for name in PRESETS if name != preset]

    return Profile(
        preset=preset,
        detected=preset,
        signals=signals,
        conventions=_conventions(root),
        confidence=confidence,
        alternatives=alternatives,
    )


def repo_config(root: Path) -> dict:
    from . import contexts

    path = root / contexts.REPO_CONFIG
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def preset_paths(root: Path) -> dict[str, str]:
    """Per-path overrides, ignoring any rule naming a preset that is not real."""
    raw = repo_config(root).get("preset_paths")
    if not isinstance(raw, dict):
        return {}
    return {str(rule): str(preset) for rule, preset in raw.items() if preset in RANK}


def resolve(root: Path) -> Profile:
    """Detection, then what the repo has recorded on top of it.

    Every caller that needs "which bar applies here" goes through this. It used
    to be open-coded in ``wb repo`` alone, which is why ``wb sdd gates`` read
    the *detected* preset and silently ignored an override somebody had set --
    two commands answering the same question two ways.
    """
    profile = detect(root)
    config = repo_config(root)

    stored = config.get("preset")
    if stored in RANK:
        if stored != profile.preset:
            # A chosen preset is somebody's decision, not a guess about them.
            profile.confidence = HIGH
            profile.alternatives = []
        profile.preset = str(stored)
    profile.confirmed = bool(config.get("preset_confirmed"))
    return profile


def record(root: Path, preset: str, *, confirmed: bool = True) -> Path:
    """Merge into the repo config; the context binding and flow must survive."""
    from . import contexts

    path = root / contexts.REPO_CONFIG
    data = repo_config(root)
    data["preset"] = preset
    data["preset_confirmed"] = confirmed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _is_monorepo(root: Path) -> bool:
    """A workspace declaration, or several packages laid out as one."""
    if _any_exists(root, WORKSPACE_MARKERS):
        return True

    package_json = root / "package.json"
    if package_json.is_file():
        try:
            if '"workspaces"' in package_json.read_text(encoding="utf-8", errors="replace")[:200_000]:
                return True
        except OSError:
            pass

    for name in PACKAGE_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        try:
            children = [child for child in directory.iterdir() if child.is_dir()]
        except OSError:
            continue
        if len(children) >= 3:
            return True
    return False


def highest(presets: list[str]) -> str:
    """The strictest of several presets. Ties and unknowns fall back safely."""
    known = [name for name in presets if name in RANK]
    return max(known, key=lambda name: RANK[name]) if known else "startup"


def resolve_for(paths: list[str], mapping: dict[str, str], default: str) -> tuple[str, dict[str, list[str]]]:
    """The preset a change is held to, given where it lands.

    A monorepo has one repo and several bars. Matching by path is the only way
    to hold a billing package to a higher bar than a playground app without
    splitting the repo -- and a change spanning both is held to the higher one,
    because the alternative is a plan that meets neither.
    """
    hits: dict[str, list[str]] = {}
    for raw in paths:
        path = str(raw).replace("\\", "/").lstrip("./")
        preset = _preset_for_path(path, mapping) or default
        hits.setdefault(preset, []).append(path)

    return highest(list(hits) or [default]), hits


def _preset_for_path(path: str, mapping: dict[str, str]) -> str | None:
    """Longest matching rule wins, so a nested override beats its parent.

    Two rules of the same length are a genuine tie, and a tie goes to the higher
    bar. Comparing the tuples alone fell through to comparing the preset *name*,
    which sorts alphabetically -- so "prototype" beat "enterprise" and the tie
    silently lowered the bar, the one thing this module says it never does.
    """
    matched: list[tuple[int, str]] = []
    for rule, preset in mapping.items():
        if preset not in RANK:
            continue
        pattern = str(rule).replace("\\", "/").lstrip("./")
        if fnmatch.fnmatchcase(path, pattern) or path.startswith(pattern.rstrip("*/") + "/"):
            matched.append((len(pattern), preset))
    if not matched:
        return None
    return max(matched, key=lambda item: (item[0], RANK[item[1]]))[1]


def gates_for(preset: str, paths: list[str]) -> list[str]:
    """Every rule that applies to this change: the preset's, plus its zones."""
    lines = FLOOR + PRESET_GATES.get(preset, [])
    for zone in sorted(critical_zones(paths)):
        lines.append(f"critical zone {zone}: hold this change to the highest bar in the repo, whatever the preset")
    return lines


def critical_zones(paths: list[str]) -> dict[str, list[str]]:
    """Which critical zones a set of paths touches, and which paths did it."""
    hits: dict[str, list[str]] = {}
    for path in paths:
        for zone, pattern in CRITICAL_ZONES.items():
            if pattern.search(path):
                hits.setdefault(zone, []).append(path)
    return hits


def _any_exists(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).exists() for name in names)


def _mentions_money(root: Path) -> bool:
    """Look in manifests only. Grepping a whole tree is slow and noisy."""
    for manifest in ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Gemfile", "Cargo.toml"):
        path = root / manifest
        if not path.is_file():
            continue
        try:
            if MONEY_PATTERN.search(path.read_text(encoding="utf-8", errors="replace")[:200_000]):
                return True
        except OSError:
            continue
    try:
        return any(MONEY_PATTERN.search(child.name) for child in root.iterdir() if child.is_dir())
    except OSError:
        return False


def _contributors(root: Path) -> int | None:
    """Contributor count, or ``None`` when git could not answer.

    The distinction matters: a timeout on a large repository used to read as
    "one contributor", which quietly detected a lower preset and lowered the
    bar on exactly the repositories that need it highest.
    """
    try:
        completed = subprocess.run(
            ["git", "shortlog", "-sn", "--all", "--no-merges"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return max(1, len([line for line in completed.stdout.splitlines() if line.strip()]))


def _conventions(root: Path) -> dict[str, str]:
    """What this repo already does, so a plan follows it instead of inventing."""
    conventions: dict[str, str] = {}

    for manifest, ecosystem in (
        ("package.json", "node"),
        ("pyproject.toml", "python"),
        ("requirements.txt", "python"),
        ("go.mod", "go"),
        ("Cargo.toml", "rust"),
        ("Gemfile", "ruby"),
    ):
        if (root / manifest).is_file():
            conventions["ecosystem"] = ecosystem
            break

    for lockfile, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("bun.lockb", "bun"),
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
    ):
        if (root / lockfile).is_file():
            conventions["package_manager"] = manager
            break

    for marker, runner in (
        ("vitest.config.ts", "vitest"),
        ("vitest.config.js", "vitest"),
        ("jest.config.js", "jest"),
        ("jest.config.ts", "jest"),
        ("pytest.ini", "pytest"),
        ("tox.ini", "tox"),
    ):
        if (root / marker).is_file():
            conventions["test_runner"] = runner
            break

    for name in TEST_MARKERS:
        if (root / name).is_dir():
            conventions["test_dir"] = name
            break

    # Every marker above belongs to a tool that needs its own config file, so a
    # repo running the standard library's runner read as having no runner at
    # all -- which turned doctor's runner check into a no-op on exactly the
    # repos it was meant to cover, this one included. The inference is last, so
    # explicit evidence always outranks it.
    #
    # The signal is the test files themselves, not a manifest: a project with no
    # third-party dependencies has no manifest to read, and that is precisely
    # the project most likely to be running unittest.
    if "test_runner" not in conventions and conventions.get("test_dir"):
        if _has_unittest_files(root / conventions["test_dir"]):
            conventions["test_runner"] = "unittest"

    return conventions


def _has_unittest_files(directory: Path) -> bool:
    """Python test modules named the way unittest discovery requires."""
    try:
        return any(directory.glob("test_*.py")) or any(directory.glob("*_test.py"))
    except OSError:
        return False
