"""VAT on invoices. Rates are percent."""

from __future__ import annotations

from shop.regions import zone_for

VAT = {
    "domestic": 0,
    "eu": 20,
    "europe": 0,
}


def vat_rate(country: str) -> int:
    """The VAT rate for an invoice country; a code that is not canonical is refused."""
    try:
        return VAT[zone_for(country)]
    except KeyError as exc:
        raise ValueError(f"cannot invoice to {country!r}") from exc
