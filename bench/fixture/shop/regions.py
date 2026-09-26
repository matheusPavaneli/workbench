"""Country codes to shipping and tax zones."""

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
    try:
        return ZONES[country]
    except KeyError:
        raise KeyError(f"unknown country code {country!r}") from None
