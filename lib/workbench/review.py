"""Facts about a diff that are worth computing rather than judging.

Nothing here decides whether a change is good. It answers the questions a
reviewer would otherwise have to reconstruct by hand every time: what moved,
which of it sits in a critical zone, and which source files changed without any
test changing with them.

Some of the quality gates are prose a reviewer has to weigh. A few are not:
"no secret in a committed file" and "no error swallowed silently" are claims
about bytes, and a script can settle them. Those live in :func:`gate_findings`.
Settling them mechanically matters more than the count of them -- a gate the
model grades itself against is a gate that passes whenever the model is
confident, and confidence is not evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import redact

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


# ---- mechanically checkable gates ---------------------------------------

HIGH = "high"
MEDIUM = "medium"


@dataclass
class Finding:
    gate: str
    severity: str
    file: str
    line: int
    quote: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "quote": self.quote,
            "detail": self.detail,
        }


# An error discarded with no handling and no comment. Each pattern is written
# to match the *discarding* form only: `except X: log(...)` and `catch (e) {
# report(e) }` are handling, and reporting them would train a reader to skip
# this whole section.
_SWALLOWED = [
    (re.compile(r"^\s*except\b[^:]*:\s*pass\s*$"), "except ...: pass discards the error and its cause"),
    # The brace-language form is almost always written as `} catch (e) {`, so
    # anchoring on `catch` at line start matched nothing real.
    (re.compile(r"^\s*\}?\s*catch\s*(\([^)]*\))?\s*\{\s*\}\s*$"), "empty catch block discards the error"),
    (re.compile(r"\.catch\s*\(\s*(\([^)]*\)|\w+)\s*=>\s*\{\s*\}\s*\)"), "empty .catch() discards a rejection"),
    (re.compile(r"(?i)^\s*_\s*=\s*err\b|^\s*_\s*,\s*_\s*:?=.*\berr\b"), "error assigned to _ and never checked"),
]

# The multi-line form is the one people actually write, and matching only the
# single-line form meant the common case passed silently. Pairs are (opener,
# body): an opener whose very next added line is the body and nothing else.
#
# The body patterns are bare on purpose. A swallow carrying a comment that says
# why -- `pass  # a lost statistic is not a failure` -- is a stated decision,
# and the gate exists to catch the unstated ones. Flagging a documented choice
# would leave no way to express it, and a gate with no way out is a gate people
# route around.
_SWALLOWED_PAIRS = [
    (re.compile(r"^\s*except\b.*:\s*$"), re.compile(r"^\s*pass\s*$"),
     "except ...: pass discards the error and its cause"),
    (re.compile(r"^\s*rescue\b.*$"), re.compile(r"^\s*end\s*$"), "empty rescue discards the error"),
    (re.compile(r"^\s*\}?\s*catch\s*(\([^)]*\))?\s*\{\s*$"), re.compile(r"^\s*\}\s*$"),
     "empty catch block discards the error"),
]

# Literal credentials. Deliberately narrow: a pattern that fires on the word
# "password" would flag every form field, and a check people learn to ignore
# is worse than no check.
# Keyword-shaped: "token = <something long>". Common, and commonly a false
# positive, so the placeholder exclusion below applies to these.
_SECRET_BY_KEYWORD = [
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token|pat)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']"),
     "a literal credential in source"),
]

# Issuer-shaped: only a real credential of that issuer has this form. No
# exclusion applies -- a token pasted into a doc as an example is still a
# credential-shaped string in a committed file, and the one time the exclusion
# would have been right is not worth the times it would hide a live key.
_SECRET_BY_SHAPE = [
    (re.compile(r"\bATATT[A-Za-z0-9_\-=]{16,}"), "an Atlassian API token"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
]

# An environment lookup or a placeholder is the correct shape, not a leak.
_NOT_A_SECRET = re.compile(
    r"(?i)(os\.environ|getenv|process\.env|env\[|\bENV\b|\{\{|\$\{|<[a-z_]+>|xxx+|\.\.\.|example|changeme|placeholder|redacted)"
)


def gate_findings(added: list[tuple[str, int, str]]) -> list[Finding]:
    """Findings that are settled by reading the added lines, not by judgement."""
    findings: list[Finding] = []
    swallowed = "no error swallowed silently; preserve the cause"

    for index, (path, line, text) in enumerate(added):
        if is_test(path):
            continue  # a fixture credential and a deliberate empty catch are the point of some tests

        secret = next((d for p, d in _SECRET_BY_SHAPE if p.search(text)), None)
        if secret is None and not _NOT_A_SECRET.search(text):
            secret = next((d for p, d in _SECRET_BY_KEYWORD if p.search(text)), None)
        if secret is not None:
            # Masked here, at the one place the tool deliberately captures a
            # credential. A finding travels further than the terminal it was
            # printed to -- into evidence.md, into --json, into a PR comment --
            # and the reader needs the location, never the value.
            findings.append(
                Finding(
                    "no secret in code or in a committed file",
                    HIGH,
                    path,
                    line,
                    redact.scrub(text.strip())[:120],
                    secret,
                )
            )

        if not is_source(path):
            continue

        matched = False
        for pattern, detail in _SWALLOWED:
            if pattern.search(text):
                findings.append(Finding(swallowed, MEDIUM, path, line, text.strip()[:120], detail))
                matched = True
                break
        if matched:
            continue

        # The pair form needs the following added line. Requiring it to be the
        # literal next line of the same file is what keeps this from firing on
        # an `except:` whose body simply was not part of the diff.
        nxt = added[index + 1] if index + 1 < len(added) else None
        if nxt is None or nxt[0] != path or nxt[1] != line + 1:
            continue
        for opener, body, detail in _SWALLOWED_PAIRS:
            if opener.match(text) and body.match(nxt[2]):
                findings.append(Finding(swallowed, MEDIUM, path, line, text.strip()[:120], detail))
                break

    return findings


# Manifests where an added line can mean a new dependency. Lockfiles are
# excluded: they change for a transitive bump nobody chose, and flagging those
# would bury the one line somebody did choose.
_MANIFESTS = {
    "package.json": re.compile(r'^\s*"[\w@./-]+"\s*:\s*"[^"]+"\s*,?\s*$'),
    "pyproject.toml": re.compile(r"^\s*[\"']?[A-Za-z][\w.-]*[\"']?\s*(==|>=|~=|>|\s*=\s*[\"'])"),
    "requirements.txt": re.compile(r"^\s*[A-Za-z][\w.-]*\s*(==|>=|~=|>|$)"),
    "go.mod": re.compile(r"^\s*[\w.-]+/[\w./-]+\s+v\d"),
    "Cargo.toml": re.compile(r"^\s*[A-Za-z][\w-]*\s*=\s*"),
    "Gemfile": re.compile(r"^\s*gem\s+[\"']"),
}


def dependency_findings(added: list[tuple[str, int, str]]) -> list[Finding]:
    """Lines that add a dependency, so the gate about it has something to point at.

    Whether a dependency is justified is a judgement; whether one was added is
    not. Reporting the line turns "no new dependency without a stated owner"
    from a question a reviewer has to remember into one they cannot miss.
    """
    findings = []
    for path, line, text in added:
        pattern = _MANIFESTS.get(Path(path).name)
        if pattern is None or not pattern.match(text):
            continue
        findings.append(
            Finding(
                "no new dependency without a stated owner and a justification",
                MEDIUM,
                path,
                line,
                text.strip()[:120],
                "a dependency was added; name the owner and why nothing already in the repo does this",
            )
        )
    return findings


def regression_test_missing(ticket_type: str, changed: list[str]) -> bool:
    """A bug fix with no test file in the diff. The floor states this outright."""
    return ticket_type.lower() in {"bug", "defect", "incident", "problem"} and not any(
        is_test(path) for path in changed
    )
