"""Keeping written output proportionate, and free of filler.

A three-line fix does not need a six-heading pull request. A template applied
regardless of size produces documents with more scaffolding than content, and a
reviewer learns to skip them -- which costs more than writing nothing would
have.

Two rules, both checkable:

- **Size decides shape.** The template scales down to a title and a sentence.
- **Empty sections are deleted, not left as headings.** A heading with nothing
  under it is a promise the document does not keep.
"""

from __future__ import annotations

import re

TRIVIAL_FILES = 2
SMALL_FILES = 6

TRIVIAL = "trivial"
SMALL = "small"
LARGE = "large"

SHAPE = {
    TRIVIAL: "title and one sentence; no headings",
    SMALL: "title, What, and Verification; nothing else",
    LARGE: "the full template, minus any section with nothing to say",
}

# Phrases that add length without adding information. Each one is a claim the
# diff already makes, an apology, or an artefact of how the text was produced.
FILLER = [
    re.compile(r"(?i)\bgenerated (with|by) \[?claude", re.IGNORECASE),
    re.compile(r"(?i)\bco-authored-by:\s*claude"),
    re.compile(r"(?i)\bas (requested|discussed|per your request)\b"),
    re.compile(r"(?i)\bthis (pr|change|commit) (simply|just|basically)\b"),
    re.compile(r"(?i)\bi (have )?(made|added|created|implemented) (the|these) (changes|updates)\b"),
    re.compile(r"(?i)\b(hope this helps|let me know if|feel free to)\b"),
    re.compile(r"(?i)\bno (other )?changes (were )?(made|needed)\b"),
]

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_UNCHECKED_BOX = re.compile(r"^\s*[-*]\s*\[ \]")
# Narrow on purpose. `<.*?>` matched every HTML tag and every generic type, so
# `List<String>` and `<br>` read as unfilled placeholders. A placeholder is an
# UPPERCASE angle-bracket slot, or a marker standing where content should be --
# not the word "TODO" appearing inside a sentence about removing one.
_PLACEHOLDER = re.compile(
    # An unfilled slot is UPPERCASE, so case sensitivity here is the point:
    # with IGNORECASE this would also match `<String>` and `<br>`.
    r"<[A-Z][A-Z0-9_ -]{1,30}>"
    # A marker standing where content should be -- not the word "TODO"
    # inside a sentence about having removed one.
    r"|^[ \t]*[-*+]?[ \t]*(?i:TBD|TODO|FIXME|lorem ipsum)\b",
    re.MULTILINE,
)


def size_class(changed: list[str], insertions: int = 0) -> str:
    if len(changed) <= TRIVIAL_FILES and insertions <= 20:
        return TRIVIAL
    if len(changed) <= SMALL_FILES:
        return SMALL
    return LARGE


def empty_sections(text: str) -> list[str]:
    """Headings with no content before the next heading."""
    lines = text.splitlines()
    empty: list[str] = []

    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match:
            continue
        for following in lines[index + 1 :]:
            if _HEADING.match(following):
                empty.append(match.group(2).strip())
                break
            if following.strip():
                break
        else:
            empty.append(match.group(2).strip())

    return empty


def check(text: str, *, expected_shape: str | None = None) -> list[str]:
    """Return problems with a written document. Empty means it is fine."""
    problems: list[str] = []

    for heading in empty_sections(text):
        problems.append(f"section {heading!r} has no content: delete the heading or fill it")

    for pattern in FILLER:
        found = pattern.search(text)
        if found:
            problems.append(f"remove filler: {found.group(0)!r}")

    for line in text.splitlines():
        if _UNCHECKED_BOX.match(line):
            problems.append(f"unticked checklist item: {line.strip()!r} -- fill it in or remove it")
            break

    placeholder = _PLACEHOLDER.search(text)
    if placeholder and placeholder.group(0) not in {"<KEY>"}:
        problems.append(f"placeholder left in the text: {placeholder.group(0)!r}")

    if expected_shape == TRIVIAL and _HEADING.search(text):
        problems.append("this change is trivial; a title and one sentence is the whole description")

    return problems
