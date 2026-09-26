"""Country codes to shipping and tax zones. Codes are upper-case ISO 3166 alpha-2."""

from __future__ import annotations

ZONES = {
    "US": "domestic",
    "DE": "eu",
    "FR": "eu",
    "NL": "eu",
    "GB": "europe",
    "CH": "europe",
}


def zone_for(country: str) -> str:
    """The zone of a canonical country code.

    Strict on purpose: invoices must carry the code exactly as registered, so a
    code in any other form is unknown here rather than guessed.
    """
    try:
        return ZONES[country]
    except KeyError:
        raise KeyError(f"unknown country code {country!r}") from None
