"""The spec document: structure, gates, sections, rendering.

An SDD is written as ``sdd.json`` and rendered to ``sdd.md`` for people. JSON is
the source because four later skills each need one slice of it, and re-reading a
whole plan four times to use one section is three readings of waste.

Structure is validated here rather than trusted. A plan missing its tests, its
verification commands or its rollback is not a terse plan -- it is a plan whose
author has not decided yet, and shipping from it is how the decision gets made
by accident.
"""

from __future__ import annotations

from .errors import UsageError
from .profile import FLOOR, PRESET_GATES

SCHEMA_VERSION = 1

SECTIONS = [
    "objective",
    "evidence",
    "files",
    "zones",
    "steps",
    "tests",
    "verify",
    "rollback",
    "product",
    "handover",
    "questions",
    "summary",
]

# Presets that must state the product effect of a change. Above these, that
# call belongs to a product owner, not to the plan.
PRODUCT_REQUIRED = {"solo-saas", "startup"}

# Ticket types whose audience is not only engineering. A support ticket is read
# by whoever raised it and validated by QA, and neither of them can act on an
# implementation plan.
HANDOVER_TYPES = {"bug", "support", "incident", "defect", "problem", "service request"}

HANDOVER_FIELDS = {
    "symptom_plain": "what the reporter saw, in their words, no jargon",
    "cause_plain": "why it happened, in one sentence a non-engineer can repeat",
    "fix_plain": "what changes for them",
    "scope": "who was affected, since when, how many",
    "workaround": "what to do until it ships, or 'none'",
    "qa_steps": "numbered steps QA follows to confirm it, ending in the expected result",
}
# The two that carry the ticket on their own. The rest are strongly encouraged
# but a missing 'workaround' should not block a plan.
HANDOVER_REQUIRED = ("symptom_plain", "qa_steps")

CHANGE_KINDS = ["edit", "add", "delete", "rename"]
TEST_KINDS = ["unit", "regression", "integration", "e2e", "property"]

# ---- rigour tiers -------------------------------------------------------
#
# A seven-section plan for a one-line change costs more than the change, and a
# gate that does not pay for itself is a gate people route around. So the bar
# scales with the risk of the change -- but the scaling is computed here, from
# the plan's own file list, and never chosen by whoever is writing the plan.
#
# What LIGHT waives is deliberately narrow. It never touches the citations, the
# file list, the verification commands or the rollback: those are what make a
# plan checkable at all, and a small change is not a less checkable one. It
# waives only the two sections whose value genuinely comes from size -- an
# ordered step list for a change that is one step, and a product justification
# for a change with no user-visible surface.

LIGHT = "light"
STANDARD = "standard"

LIGHT_MAX_FILES = 2
LIGHT_WAIVES = ("steps", "product")


def blank(key: str, preset: str, persona: str) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "key": key,
        "preset": preset,
        "persona": persona,
        "objective": "",
        "evidence": [],
        "files": [],
        "zones": {},
        "steps": [],
        "tests": [],
        "verify": [],
        "rollback": "",
        "product": {},
        "handover": {},
        "questions": [],
    }


def tier(doc: dict) -> tuple[str, str]:
    """The rigour tier this plan qualifies for, and the reason it got it.

    Computed from the plan, so the same plan always lands on the same tier and
    a session cannot argue its way down to a lower bar.
    """
    from .profile import critical_zones

    paths = [str(item.get("path", "")) for item in doc.get("files") or [] if item.get("path")]

    if len(paths) > LIGHT_MAX_FILES:
        return STANDARD, f"{len(paths)} files (light is up to {LIGHT_MAX_FILES})"

    zones = critical_zones(paths)
    if zones:
        return STANDARD, f"touches {', '.join(sorted(zones))}"

    if needs_handover(doc):
        # Bug, support and incident work is read by people outside engineering
        # whatever its size, and the sections that serve them are not optional.
        return STANDARD, f"{doc.get('ticket_type', 'support')} work has a non-engineering audience"

    if not paths:
        return STANDARD, "no files listed"

    return LIGHT, f"{len(paths)} file(s), no critical zone"


def validate(doc: dict) -> list[str]:
    """Return structural problems. Empty means the shape is sound.

    This checks that a decision was made, not that it was a good one -- the
    citations are what make it checkable, and those are audited separately.
    """
    problems: list[str] = []
    waived = LIGHT_WAIVES if tier(doc)[0] == LIGHT else ()

    if doc.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema must be {SCHEMA_VERSION}")

    if not str(doc.get("objective", "")).strip():
        problems.append("objective is empty: state what changes and why, in one or two sentences")

    if not doc.get("evidence"):
        problems.append("evidence is empty: every claim about this codebase needs a file:line citation")
    for index, item in enumerate(doc.get("evidence") or []):
        for required in ("claim", "file", "line"):
            if not item.get(required):
                problems.append(f"evidence[{index}] has no {required}")
        if not str(item.get("quote", "")).strip():
            problems.append(f"evidence[{index}] has no quote: paste the line you are citing, so it can be checked")

    if not doc.get("files"):
        problems.append("files is empty: list every file this change touches, before touching any")
    for index, item in enumerate(doc.get("files") or []):
        if not item.get("path"):
            problems.append(f"files[{index}] has no path")
        if item.get("change") not in CHANGE_KINDS:
            problems.append(f"files[{index}].change must be one of: {', '.join(CHANGE_KINDS)}")
        if not str(item.get("why", "")).strip():
            problems.append(f"files[{index}] has no why: an untouched-for-no-reason file is scope creep")

    if not doc.get("steps") and "steps" not in waived:
        problems.append("steps is empty: an unordered plan cannot be executed or reviewed")

    if not doc.get("tests"):
        problems.append("tests is empty: the floor is a unit test for every changed logic branch")
    for index, item in enumerate(doc.get("tests") or []):
        if item.get("kind") not in TEST_KINDS:
            problems.append(f"tests[{index}].kind must be one of: {', '.join(TEST_KINDS)}")
        if not str(item.get("asserts", "")).strip():
            problems.append(f"tests[{index}] has no asserts: say what must hold, not what to call")

    if not doc.get("verify"):
        problems.append("verify is empty: list the exact commands that prove this works")

    if not str(doc.get("rollback", "")).strip():
        problems.append("rollback is empty: state how to undo this before doing it")

    preset = str(doc.get("preset", ""))
    if preset in PRODUCT_REQUIRED and "product" not in waived:
        product = doc.get("product") or {}
        missing = [k for k in ("metric", "who_asked") if not str(product.get(k, "")).strip()]
        if missing:
            problems.append(
                f"preset {preset} requires product.{{{', '.join(missing)}}}: "
                "which metric this moves, and who asked for it"
            )

    if needs_handover(doc):
        handover = doc.get("handover") or {}
        for name in HANDOVER_REQUIRED:
            if not handover.get(name):
                problems.append(
                    f"a {doc.get('ticket_type', 'support')} ticket needs handover.{name}: {HANDOVER_FIELDS[name]}"
                )
        steps = handover.get("qa_steps")
        if steps is not None and not isinstance(steps, list):
            problems.append("handover.qa_steps must be a list of steps")

    return problems


def needs_handover(doc: dict) -> bool:
    """Support work owes an answer to the person who raised it.

    Driven by the ticket type from triage, with an explicit override for the
    cases the tracker types badly.
    """
    override = doc.get("handover_required")
    if isinstance(override, bool):
        return override
    return str(doc.get("ticket_type", "")).strip().lower() in HANDOVER_TYPES


def gates_for(preset: str) -> list[str]:
    return FLOOR + PRESET_GATES.get(preset, [])


def section(doc: dict, name: str) -> object:
    if name not in SECTIONS:
        raise UsageError(f"unknown section: {name!r}", fix=[f"sections: {', '.join(SECTIONS)}"])
    if name == "summary":
        return {
            "key": doc.get("key"),
            "objective": doc.get("objective"),
            "files": [item.get("path") for item in doc.get("files") or []],
            "zones": sorted(doc.get("zones") or {}),
            "rollback": doc.get("rollback"),
        }
    return doc.get(name)


def render(doc: dict) -> str:
    """Markdown for humans. Never parsed back -- JSON stays the source."""
    lines = [f"# {doc.get('key', '?')} — implementation spec", ""]
    lines += [f"**Preset** `{doc.get('preset', '?')}` · **Persona** {doc.get('persona', '?')}", ""]
    lines += ["## Objective", "", str(doc.get("objective", "")).strip(), ""]

    zones = doc.get("zones") or {}
    if zones:
        lines += ["## Critical zones", ""]
        for zone, paths in sorted(zones.items()):
            lines.append(f"- **{zone}** — {', '.join(paths)}")
        lines.append("")

    lines += ["## Evidence", ""]
    for item in doc.get("evidence") or []:
        lines.append(f"- {item.get('claim')}  \n  `{item.get('file')}:{item.get('line')}` — `{item.get('quote')}`")
    lines.append("")

    lines += ["## Files touched", ""]
    for item in doc.get("files") or []:
        lines.append(f"- `{item.get('path')}` ({item.get('change')}) — {item.get('why')}")
    lines.append("")

    lines += ["## Steps", ""]
    for index, step in enumerate(doc.get("steps") or [], start=1):
        target = f" — `{step.get('file')}`" if step.get("file") else ""
        lines.append(f"{index}. {step.get('do')}{target}")
    lines.append("")

    lines += ["## Tests", ""]
    for item in doc.get("tests") or []:
        lines.append(f"- **{item.get('kind')}** `{item.get('target', '')}` — {item.get('asserts')}")
    lines.append("")

    lines += ["## Verification", ""] + [f"```\n{cmd}\n```" for cmd in doc.get("verify") or []] + [""]
    lines += ["## Rollback", "", str(doc.get("rollback", "")).strip(), ""]

    product = doc.get("product") or {}
    if product:
        lines += ["## Product", ""]
        for label in ("metric", "who_asked", "cost", "pricing_impact"):
            if product.get(label):
                lines.append(f"- **{label.replace('_', ' ')}** — {product[label]}")
        lines.append("")

    handover = doc.get("handover") or {}
    if handover:
        lines += ["## Handover", ""]
        for label in ("symptom_plain", "cause_plain", "fix_plain", "scope", "workaround"):
            if handover.get(label):
                lines.append(f"- **{label.replace('_plain', '').replace('_', ' ')}** — {handover[label]}")
        if handover.get("qa_steps"):
            lines += ["", "**How QA confirms it:**", ""]
            lines += [f"{i}. {step}" for i, step in enumerate(handover["qa_steps"], start=1)]
        lines.append("")

    questions = doc.get("questions") or []
    if questions:
        lines += ["## Open questions", ""] + [f"- {q}" for q in questions] + [""]

    return "\n".join(lines)


def render_handover(doc: dict) -> str:
    """A standalone note for the ticket: the reporter and QA read this, not the plan.

    Deliberately free of file paths, function names and commit hashes. If it
    cannot be said without them, it has not been understood well enough to hand
    over.
    """
    handover = doc.get("handover") or {}
    key = doc.get("key", "?")
    lines = [f"# {key} — what changed, and how to confirm it", ""]

    for label, heading in (
        ("symptom_plain", "What was happening"),
        ("cause_plain", "Why"),
        ("fix_plain", "What changes now"),
        ("scope", "Who was affected"),
        ("workaround", "Until this ships"),
    ):
        value = str(handover.get(label, "")).strip()
        if value:
            lines += [f"## {heading}", "", value, ""]

    steps = handover.get("qa_steps") or []
    if steps:
        lines += ["## How to confirm it", ""]
        lines += [f"{index}. {step}" for index, step in enumerate(steps, start=1)]
        lines.append("")

    note = str(handover.get("release_note", "")).strip()
    if note:
        lines += ["## Release note", "", note, ""]

    return "\n".join(lines)
