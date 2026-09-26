"""VAT on invoices. Rates are percent."""

from __future__ import annotations

from shop.regions import zone_for

VAT = {
    "domestic": 0,
    "eu": 20,
    "europe": 0,
}


def vat_rate(country: str) -> int:
    # Invoices must carry the country code exactly as the tax authority
    # registered it (upper case). zone_for's KeyError on any other spelling is
    # what refuses one here; an invoice to "de" is a compliance error.
    try:
        return VAT[zone_for(country)]
    except KeyError as exc:
        raise ValueError(f"cannot invoice to {country!r}") from exc
