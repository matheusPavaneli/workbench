import unittest

from shop.billing.charge import ChargeError, create_charge
from shop.cart import Cart


def _cart(*units):
    cart = Cart()
    for n, unit in enumerate(units):
        cart.add(f"sku-{n}", unit)
    return cart


class HiddenBN2(unittest.TestCase):
    def test_no_coupon_charges_the_total(self):
        self.assertEqual(1000, create_charge(_cart(1000)).amount_cents)

    def test_save10_takes_ten_percent_off(self):
        self.assertEqual(900, create_charge(_cart(1000), coupon="SAVE10").amount_cents)

    def test_the_discount_is_rounded_down_to_whole_cents(self):
        # 10% of 999 is 99.9; the discount is 99, so 900 is charged.
        self.assertEqual(900, create_charge(_cart(999), coupon="SAVE10").amount_cents)

    def test_an_unknown_coupon_is_refused(self):
        with self.assertRaises(ChargeError):
            create_charge(_cart(1000), coupon="NOPE")

    def test_the_currency_is_kept(self):
        charge = create_charge(_cart(1000), currency="EUR", coupon="SAVE10")
        self.assertEqual("EUR", charge.currency)
