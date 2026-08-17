"""Secret scrubbing for everything that leaves the process.

Two layers, because either alone leaks:

1. Registered values -- the exact secrets we resolved this run. Exact-match is
   the only reliable way to catch a token that looks like ordinary text.
2. Patterns -- credentials we never resolved ourselves but that show up in
   provider responses and tracebacks (Authorization headers, PAT-shaped blobs).

Every write to stdout/stderr goes through ``scrub``. There is no bypass.
"""

from __future__ import annotations

import re

MASK = "[redacted]"

# Minimum length before a registered value is worth masking. Short values would
# match everywhere and turn output into noise.
_MIN_SECRET_LEN = 8

_registered: set[str] = set()

# (pattern, replacement) -- the replacement keeps the key so a reader can tell
# *what* was redacted, and drops only the value.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization header, both schemes.
    # The value may be "Basic xxx" (two tokens), so consume to end of line.
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*).+"), r"\1" + MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9+/=_\-.]{16,}"), r"\1 " + MASK),
    # Atlassian API tokens.
    (re.compile(r"\bATATT[A-Za-z0-9_\-=]{16,}"), MASK),
    # Azure DevOps PATs: base32-ish, fixed length in practice.
    (re.compile(r"\b[a-z2-7]{52}\b"), MASK),
    # "token=..." / "pat: ..." / "password=..." in URLs, logs, tracebacks.
    (re.compile(r"(?i)\b(token|pat|password|secret|api[_-]?key)(\s*[:=]\s*)\S+"), r"\1\2" + MASK),
    # Credentials embedded in a URL -- keep the host, drop user:pass.
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1" + MASK + "@"),
]


def register(value: str | None) -> None:
    """Mark a resolved secret so it is masked wherever it appears."""
    if value and len(value) >= _MIN_SECRET_LEN:
        _registered.add(value)


def scrub(text: str) -> str:
    if not text:
        return text
    for secret in _registered:
        text = text.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def reset() -> None:
    """Test hook. Not used at runtime."""
    _registered.clear()
