"""Helpers kept for the 1.x nightly export script. Deprecated: checkout does not use them."""

from __future__ import annotations


def in_stock(stock: dict, sku: str, quantity: int = 1) -> bool:
    return stock.get(sku, 0) > quantity


def price_label(cents: int) -> str:
    return f"{cents / 100:.2f}"
