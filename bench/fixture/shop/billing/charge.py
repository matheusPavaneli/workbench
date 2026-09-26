"""Charges against the payment gateway. Amounts are integer cents."""

from __future__ import annotations

from dataclasses import dataclass

from shop.cart import Cart


class ChargeError(ValueError):
    """A charge that must not reach the gateway."""


@dataclass(frozen=True)
class Charge:
    amount_cents: int
    currency: str


def create_charge(cart: Cart, currency: str = "USD") -> Charge:
    amount = cart.total_cents()
    if amount <= 0:
        raise ChargeError("nothing to charge")
    return Charge(amount, currency)
