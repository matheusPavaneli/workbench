"""Facts about a diff that are worth computing rather than judging.

Nothing here decides whether a change is good. It answers the questions a
reviewer would otherwise have to reconstruct by hand every time: what moved,
which of it sits in a critical zone, and which source files changed without any
test changing with them.
"""

from __future__ import annotations

import re
from pathlib import Path

CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".cs", ".swift", ".scala", ".c", ".cc", ".cpp", ".h", ".hpp", ".m",
}

_TEST_PATH = re.compile(r"(^|/)(tests?|spec|specs|__tests__)(/|$)|[._-](test|spec)s?\.", re.IGNORECASE)


def is_test(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def is_source(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS and not is_test(path)


def untested(changed: list[str]) -> list[str]:
    """Source files that changed with no test file changing alongside them.

    A heuristic, and named as one: matching is by file stem, so a test that
    covers several modules or is named differently will read as missing. It is
    a prompt to answer the question, not a verdict.
    """
    # Whole-token matching, not substring: "id" is inside "validator", so a
    # substring check reports src/id.py as covered by test_validator.py.
    covered: set[str] = set()
    for path in changed:
        if is_test(path):
            covered.update(_tokens(Path(path).name))

    missing = []
    for path in changed:
        if not is_source(path):
            continue
        stem = Path(path).stem.lower()
        if stem and stem in covered:
            continue
        missing.append(path)
    return missing


def _tokens(name: str) -> set[str]:
    """Split a filename into the identifiers a human would recognise in it."""
    return {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}
