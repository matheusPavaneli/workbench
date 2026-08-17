"""Minimal JSON-over-HTTP client. Standard library only.

Every outbound call has a timeout and a bounded retry. Failures raise typed
errors carrying a short, scrubbed body excerpt -- enough to diagnose, small
enough that it does not flood an agent's context.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import redact
from . import __version__
from .errors import AuthError, NotFoundError, ProviderError

TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
MAX_RETRY_SLEEP = 8.0
# Response bodies are provider-controlled; cap what we read and what we quote.
MAX_BODY_BYTES = 4 * 1024 * 1024
ERROR_EXCERPT_CHARS = 300

# Derived, not written out again. This string had already fallen two releases
# behind the manifests, because a version copied by hand is a version nobody
# remembers to copy.
USER_AGENT = f"workbench/{__version__} (+https://github.com/matheusPavaneli/workbench)"


def basic_auth(user: str, secret: str) -> str:
    raw = f"{user}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def request(
    method: str,
    url: str,
    *,
    auth: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
) -> Any:
    """Perform a request and return parsed JSON (``None`` for an empty body)."""
    if query:
        pairs = {k: v for k, v in query.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(pairs, doseq=True)}"

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    all_headers = {
        "Accept": "application/json",
        "Authorization": auth,
        "User-Agent": USER_AGENT,
        **(headers or {}),
    }
    if payload is not None:
        all_headers.setdefault("Content-Type", "application/json")

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=payload, headers=all_headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_BODY_BYTES)
                return json.loads(raw.decode("utf-8")) if raw.strip() else None

        except urllib.error.HTTPError as exc:
            excerpt = _excerpt(exc)
            if exc.code in (401, 403):
                raise AuthError(
                    f"{exc.code} from {_safe_url(url)}: {excerpt}",
                    fix=[
                        "check the token is valid and not expired",
                        "check the token's scopes cover reading work items",
                        "re-test with: wb ctx test",
                    ],
                ) from exc
            if exc.code == 404:
                raise NotFoundError(f"404 from {_safe_url(url)}: {excerpt}") from exc
            if exc.code == 429 or exc.code >= 500:
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    time.sleep(_backoff(exc, attempt))
                    continue
                raise ProviderError(
                    f"{exc.code} from {_safe_url(url)} after {attempt} attempts: {excerpt}",
                    fix=["the tracker is rate-limiting or down; retry shortly"],
                ) from exc
            raise ProviderError(f"{exc.code} from {_safe_url(url)}: {excerpt}") from exc

        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(2.0 ** attempt, MAX_RETRY_SLEEP))
                continue
            raise ProviderError(
                f"cannot reach {_safe_url(url)}: {redact.scrub(str(exc.reason))}",
                fix=["check network access and base_url in the context file"],
            ) from exc

        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"{_safe_url(url)} returned a non-JSON body",
                fix=["check base_url points at the API root, not a web UI page"],
            ) from exc

    raise ProviderError(f"request to {_safe_url(url)} failed: {last_error}")


def _backoff(exc: urllib.error.HTTPError, attempt: int) -> float:
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return min(float(header), MAX_RETRY_SLEEP)
        except ValueError:
            pass
    return min(2.0 ** attempt, MAX_RETRY_SLEEP)


def _excerpt(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(ERROR_EXCERPT_CHARS * 4).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - a failed read must not mask the HTTP error
        return "<no body>"
    return redact.scrub(" ".join(raw.split()))[:ERROR_EXCERPT_CHARS]


def _safe_url(url: str) -> str:
    return redact.scrub(url)
