"""Money formatting for receipts and emails."""

from __future__ import annotations


def format_money(cents: int, currency: str = "USD") -> str:
    return f"{currency} {cents // 100}.{cents % 100:02d}"
