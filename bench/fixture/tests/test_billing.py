import unittest

from shop.billing.charge import ChargeError, create_charge
from shop.cart import Cart


class ChargeTest(unittest.TestCase):
    def test_a_cart_is_charged_its_total(self):
        cart = Cart()
        cart.add("mug", 1200)
        charge = create_charge(cart)
        self.assertEqual(1200, charge.amount_cents)
        self.assertEqual("USD", charge.currency)

    def test_an_empty_cart_is_refused(self):
        with self.assertRaises(ChargeError):
            create_charge(Cart())
