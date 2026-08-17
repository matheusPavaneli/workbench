"""Actionable errors.

Error text is read by an agent, so it must be short and say exactly what to do.
Never interpolate a secret into a message: callers pass values through
``redact.scrub`` before they reach here.
"""

from __future__ import annotations

# Exit codes are part of the CLI contract; callers may branch on them.
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_AUTH = 4
EXIT_PROVIDER = 5
EXIT_NOT_FOUND = 6
EXIT_AUDIT = 7


class WbError(Exception):
    """A failure with a known fix.

    ``fix`` is an ordered list of concrete steps. Empty means "no known fix",
    which should be rare -- prefer saying something over saying nothing.
    """

    def __init__(self, message: str, *, code: int = EXIT_USAGE, fix: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.fix = fix or []

    def render(self) -> str:
        lines = [f"error: {self.message}"]
        for step in self.fix:
            lines.append(f"  fix: {step}")
        return "\n".join(lines)


class UsageError(WbError):
    def __init__(self, message: str, *, fix: list[str] | None = None) -> None:
        super().__init__(message, code=EXIT_USAGE, fix=fix)


class ConfigError(WbError):
    def __init__(self, message: str, *, fix: list[str] | None = None) -> None:
        super().__init__(message, code=EXIT_CONFIG, fix=fix)


class AuthError(WbError):
    def __init__(self, message: str, *, fix: list[str] | None = None) -> None:
        super().__init__(message, code=EXIT_AUTH, fix=fix)


class ProviderError(WbError):
    def __init__(self, message: str, *, fix: list[str] | None = None) -> None:
        super().__init__(message, code=EXIT_PROVIDER, fix=fix)


class NotFoundError(WbError):
    def __init__(self, message: str, *, fix: list[str] | None = None) -> None:
        super().__init__(message, code=EXIT_NOT_FOUND, fix=fix)


def unknown_choice(what: str, given: str, valid: list[str]) -> UsageError:
    """The anti-hallucination path: never guess, always list what exists."""
    return UsageError(
        f"unknown {what}: {given!r}",
        fix=[f"valid {what}s: {', '.join(sorted(valid))}"],
    )
