"""Loyalty points earned on an order."""

from __future__ import annotations


def points(total_cents: int) -> int:
    """One point per whole dollar of the order total."""
    if total_cents <= 0:
        return 0
    return total_cents // 1000
