"""Repo profiling: which quality bar applies, and where it is higher.

The preset is detected from evidence in the repo, not asked and not inferred by
a model at runtime -- if it lived in the model's judgement, two sessions on the
same repo would hold the work to two different bars.

Detection proposes; the user decides. ``wb repo profile --set`` overrides, and
the override is what gets written.
"""

from __future__ import annotations

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
    "user-data": re.compile(r"(^|/)(users?|accounts?|profiles?|pii|personal)(/|_|\.)", re.I),
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


@dataclass
class Profile:
    preset: str
    detected: str
    signals: list[str] = field(default_factory=list)
    conventions: dict[str, str] = field(default_factory=dict)

    def gates(self) -> list[str]:
        return FLOOR + PRESET_GATES.get(self.preset, [])

    def to_dict(self) -> dict:
        return {
            "preset": self.preset,
            "detected": self.detected,
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
    contributors = _contributors(root)

    for label, present in (
        ("ci", has_ci),
        ("codeowners", has_owners),
        ("migrations", has_migrations),
        ("tests", has_tests),
        ("money-paths", has_money),
    ):
        if present:
            signals.append(label)
    signals.append(f"contributors={contributors if contributors is not None else 'unknown'}")

    if has_owners:
        preset = "enterprise"
    elif contributors is None:
        # No evidence of team size is not evidence of a small team. Fall back to
        # the middle, never to the bottom.
        preset = "scaleup" if has_migrations else "startup"
    elif contributors >= 8:
        preset = "enterprise"
    elif contributors >= 3:
        preset = "scaleup" if has_migrations else "startup"
    elif has_money:
        preset = "solo-saas"
    elif has_ci or has_tests:
        preset = "startup"
    else:
        preset = "prototype"

    return Profile(preset=preset, detected=preset, signals=signals, conventions=_conventions(root))


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

    return conventions
