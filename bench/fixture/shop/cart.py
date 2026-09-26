"""Cart lines and totals. Amounts are integer cents."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Line:
    sku: str
    unit_cents: int
    quantity: int = 1


@dataclass
class Cart:
    lines: list[Line] = field(default_factory=list)

    def add(self, sku: str, unit_cents: int, quantity: int = 1) -> None:
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        for line in self.lines:
            if line.sku == sku:
                line.quantity += quantity
                return
        self.lines.append(Line(sku, unit_cents, quantity))

    def count(self) -> int:
        return sum(line.quantity for line in self.lines)

    def total_cents(self) -> int:
        return sum(line.unit_cents for line in self.lines)
