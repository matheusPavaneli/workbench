"""Commit message conventions: detect what a repo already does, then check.

The convention is read off the repo's own history rather than imposed. A house
style that has held for two thousand commits does not need an opinion from a
tool, and a message that breaks it is noise in the log forever.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import redact

SUBJECT_LIMIT = 72
SUBJECT_COMFORTABLE = 50
SAMPLE_SIZE = 50
# Below this share of matching history, a pattern is coincidence, not a house style.
ADOPTION_THRESHOLD = 0.6

CONVENTIONAL_TYPES = [
    "feat", "fix", "chore", "docs", "refactor", "test", "perf", "build", "ci", "style", "revert",
]

_CONVENTIONAL = re.compile(rf"^({'|'.join(CONVENTIONAL_TYPES)})(\([^)]+\))?!?: .+")
_TICKET_PREFIX = re.compile(r"^[A-Z][A-Z0-9]+-\d+[: ]")
_WIP = re.compile(r"^(wip\b|fixup!|squash!|amend!)", re.IGNORECASE)


@dataclass
class Convention:
    style: str  # "conventional", "ticket-prefixed", or "free-form"
    ticket_prefix: bool = False
    sample: int = 0
    examples: list[str] = field(default_factory=list)
    declared: bool = False

    def describe(self) -> str:
        if self.style == "conventional":
            detail = f"conventional commits ({', '.join(CONVENTIONAL_TYPES[:5])}, ...)"
        elif self.style == "ticket-prefixed":
            detail = "subject starts with the ticket key"
        else:
            detail = "free-form; keep it short, imperative, and specific"
        if self.declared:
            return f"{detail}  [declared for this repo]"
        return f"{detail}  [from {self.sample} recent commit(s)]"

    def to_dict(self) -> dict:
        return {
            "style": self.style,
            "ticket_prefix": self.ticket_prefix,
            "sample": self.sample,
            "declared": self.declared,
            "examples": self.examples,
        }


def resolve(root, subjects: list[str]) -> Convention:
    """A declared convention wins over a detected one.

    Detection reads what a repo *has* done. A team adopting a style it does not
    have yet needs to say so, or every message would be checked against the
    history it is trying to leave behind.
    """
    declared = _declared(root)
    if declared:
        convention = Convention(style=declared, sample=0, declared=True)
        convention.ticket_prefix = declared == "ticket-prefixed"
        return convention
    return detect(subjects)


def declare(root, style: str) -> None:
    import json

    from .contexts import REPO_CONFIG

    path = root / REPO_CONFIG
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data["commit_style"] = style
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _declared(root) -> str | None:
    import json

    from .contexts import REPO_CONFIG

    path = root / REPO_CONFIG
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    style = data.get("commit_style") if isinstance(data, dict) else None
    return style if style in {"conventional", "ticket-prefixed", "free-form"} else None


def detect(subjects: list[str]) -> Convention:
    sample = subjects[:SAMPLE_SIZE]
    if not sample:
        return Convention(style="free-form", sample=0)

    conventional = sum(1 for s in sample if _CONVENTIONAL.match(s))
    ticketed = sum(1 for s in sample if _TICKET_PREFIX.match(s))
    total = len(sample)

    if conventional / total >= ADOPTION_THRESHOLD:
        style = "conventional"
    elif ticketed / total >= ADOPTION_THRESHOLD:
        style = "ticket-prefixed"
    else:
        style = "free-form"

    return Convention(
        style=style,
        ticket_prefix=ticketed / total >= ADOPTION_THRESHOLD,
        sample=total,
        examples=sample[:3],
    )


def check(message: str, convention: Convention, *, key: str | None = None) -> list[str]:
    """Return problems with a message. Empty means it is fine to use."""
    problems: list[str] = []

    lines = message.replace("\r\n", "\n").rstrip().split("\n")
    subject = lines[0].strip() if lines else ""

    if not subject:
        problems.append("subject is empty")
        return problems

    if len(subject) > SUBJECT_LIMIT:
        problems.append(f"subject is {len(subject)} chars; keep it under {SUBJECT_LIMIT}")

    if subject.endswith("."):
        problems.append("subject ends with a period")

    if _WIP.match(subject):
        problems.append("subject marks unfinished work (wip/fixup/squash); finish or squash it first")

    if len(lines) > 1 and lines[1].strip():
        problems.append("line 2 must be blank: git treats the first paragraph as the subject")

    if convention.style == "conventional" and not _CONVENTIONAL.match(subject):
        problems.append(
            f"this repo uses conventional commits; start with one of: {', '.join(CONVENTIONAL_TYPES)}"
        )

    if convention.style == "ticket-prefixed" and not _TICKET_PREFIX.match(subject):
        problems.append("this repo prefixes subjects with the ticket key")

    if key and convention.ticket_prefix and key not in message:
        problems.append(f"the ticket key {key} does not appear in the message")

    if redact.scrub(message) != message:
        problems.append("the message contains something that looks like a credential")

    return problems


def summary(subject: str) -> str:
    """First line, trimmed the way git will show it."""
    return subject.strip()[:SUBJECT_LIMIT]
