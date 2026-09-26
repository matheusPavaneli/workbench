"""Shipping quotes for the checkout form. Amounts are integer cents."""

from __future__ import annotations

from shop.regions import zone_for

RATES = {
    "domestic": 500,
    "eu": 1200,
    "europe": 1800,
}
INTERNATIONAL = 3500


def shipping_cents(country: str) -> int:
    try:
        zone = zone_for(country)
    except KeyError:
        return INTERNATIONAL
    return RATES[zone]
