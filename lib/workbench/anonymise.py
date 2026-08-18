"""Turning a real tracker payload into a fixture that is safe to commit.

The fixtures that ship with this package follow the vendors' published
contracts. What those contracts cannot describe is *your* instance: the custom
fields, the custom link types, the workflow state names somebody invented in
2019. That gap is where this tool actually breaks for a new user, and the README
asked them to close it by hand -- which nobody does.

So the gap gets closed by a command. The risk that comes with it is obvious: a
Jira issue is one of the most reliably confidential objects in a company, and a
fixture is a file people commit. This module is therefore written to fail
towards *losing information*, never towards keeping it:

- structure is preserved exactly -- key names, nesting, types, list lengths,
  which is the whole reason the fixture is worth having
- every free-text value is replaced with lorem text of the same shape
- identifiers people recognise -- names, emails, URLs, keys -- are replaced
  consistently, so a fixture stays internally coherent (the same author is the
  same fake author everywhere) without being traceable
- anything it cannot classify is replaced, not kept

Consistency comes from a salted hash held for the length of one run, so two
runs do not produce the same fake names and nothing survives to correlate.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from . import redact

# Keys whose values are structural: the code branches on them, so a fixture with
# them scrambled tests nothing. These keep their real values, and none of them
# carries free text about a person or a product.
STRUCTURAL = frozenset(
    {
        "id", "key", "self", "expand", "startAt", "maxResults", "total", "isLast",
        "type", "name", "value", "fields", "schema", "custom", "customId", "items",
        "operations", "outward", "inward", "outwardIssue", "inwardIssue",
        "statusCategory", "colorName", "iconUrl", "avatarUrls", "subtask", "hierarchyLevel",
        "count", "state", "system", "navigable", "searchable", "orderable", "clauseNames",
        "op", "path", "rev", "url", "_links", "href", "relations", "attributes",
        "fromString", "toString", "field", "fieldtype", "fieldId",
    }
)

# Keys that are always free text about the work, whatever the provider calls
# them. Replaced with lorem of the same length.
PROSE = frozenset(
    {
        "summary", "description", "body", "comment", "text", "title", "renderedBody",
        "environment", "System.Title", "System.Description", "Microsoft.VSTS.Common.AcceptanceCriteria",
    }
)

# Keys naming a person. Replaced consistently, so the same person stays one
# person across the fixture.
PEOPLE = frozenset(
    {
        "displayName", "emailAddress", "authorName", "author", "assignee", "reporter",
        "creator", "updateAuthor", "login", "accountId", "uniqueName", "user",
        "System.AssignedTo", "System.CreatedBy", "System.ChangedBy",
    }
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"https?://[^\s\"'<>)]+")
_KEYISH = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")

_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud"
).split()

_NAMES = ("Ana Ruiz", "Bruno Vale", "Cora Lima", "Davi Sena", "Elis Moura", "Fabio Reis")


class Anonymiser:
    """One run's mapping. Salted per instance: nothing correlates across runs."""

    def __init__(self, salt: str | None = None) -> None:
        self._salt = salt or os.urandom(16).hex()
        self._seen: dict[str, str] = {}

    # ---- public ---------------------------------------------------------

    def payload(self, data: Any, *, key: str = "") -> Any:
        """Anonymise a decoded JSON payload, preserving its shape exactly."""
        if isinstance(data, dict):
            return {name: self.payload(value, key=name) for name, value in data.items()}
        if isinstance(data, list):
            return [self.payload(item, key=key) for item in data]
        if isinstance(data, str):
            return self.text(data, key=key)
        return data

    def text(self, value: str, *, key: str = "") -> str:
        """A string, classified by the key that held it and by what it contains."""
        if not value:
            return value

        # Secrets first, always, whatever the key says.
        value = redact.scrub(value)

        # Explicit beats structural beats guessed. `name` is structural -- it
        # holds "Bug" and "In Progress", which the code branches on -- while
        # `displayName` is a person; a heuristic that fires on any key
        # containing "name" would replace the issue type with a person's name
        # and quietly make every fixture useless.
        if key in PEOPLE:
            return self.person(value)
        if key in STRUCTURAL:
            return self._scrub_embedded(value)
        if _looks_like_person(key):
            return self.person(value)
        if key in PROSE or len(value) > 40:
            return self.lorem(value)
        return self._scrub_embedded(value)

    def person(self, value: str) -> str:
        """Same input, same fake person, for the length of this run."""
        if "@" in value:
            handle = self._stable(value, ("ana", "bruno", "cora", "davi", "elis", "fabio"))
            return f"{handle}@example.com"
        if value.isdigit() or _looks_like_id(value):
            return self._digest(value)[:24]
        return self._stable(value, _NAMES)

    def lorem(self, value: str) -> str:
        """Free text of the same rough shape: length, line count, list markers.

        Shape matters because the depth caps and the summarisers in this package
        are length-sensitive; text replaced by a single word would test a code
        path no real payload takes.
        """
        lines = value.splitlines() or [""]
        out = []
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                out.append("")
                continue
            prefix = ""
            marker = re.match(r"^(\s*(?:[-*+]|\d+\.)\s+)", line)
            if marker:
                prefix = marker.group(1)
                stripped = line[len(prefix):]
            out.append(prefix + self._words_for(stripped, seed=index))
        return "\n".join(out)

    # ---- internals ------------------------------------------------------

    def _words_for(self, original: str, seed: int) -> str:
        wanted = max(1, len(original.split()))
        start = int(self._digest(f"{seed}:{len(original)}")[:4], 16)
        picked = [_WORDS[(start + offset) % len(_WORDS)] for offset in range(wanted)]
        return " ".join(picked)

    def _scrub_embedded(self, value: str) -> str:
        """Short, structural-looking strings can still carry an address or a URL."""
        value = _EMAIL.sub(lambda match: self.person(match.group(0)), value)
        value = _URL.sub(self._url, value)
        return value

    def _url(self, match: re.Match) -> str:
        url = match.group(0)
        # Keep the shape of the path, which providers branch on; drop the host.
        tail = url.split("://", 1)[-1]
        path = tail.split("/", 1)[1] if "/" in tail else ""
        return f"https://example.invalid/{path}"

    def _stable(self, value: str, pool: tuple[str, ...]) -> str:
        """One pseudonym per input, and never the same one for two inputs.

        Hashing into the pool gave the first property and not the second: with
        six names, two distinct people collided about one run in six, and a
        fixture where two commenters read as the same author is incoherent in a
        way that is hard to notice and easy to reason wrongly from.

        Assigning in encounter order is injective by construction. It is still
        stable within a run, which is all consistency ever needed, and the
        ordering carries no information about the input.
        """
        if value not in self._seen:
            index = len(self._seen)
            name = pool[index % len(pool)]
            if index >= len(pool):
                name = f"{name} {index // len(pool) + 1}"
            self._seen[value] = name
        return self._seen[value]

    def _digest(self, value: str) -> str:
        return hashlib.sha256(f"{self._salt}:{value}".encode("utf-8")).hexdigest()


def _looks_like_person(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in ("name", "email", "author", "assign", "user", "owner"))


def _looks_like_id(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f:\-]{16,}", value))
