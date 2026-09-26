"""Stock checks before an order is accepted. Stock is a mapping of sku to units on hand."""

from __future__ import annotations


def available(stock: dict, sku: str, quantity: int = 1) -> bool:
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    return stock.get(sku, 0) > quantity


def restock(stock: dict, sku: str, units: int) -> None:
    if units < 1:
        raise ValueError("units must be at least 1")
    stock[sku] = stock.get(sku, 0) + units
