import unittest

from shop.cart import Cart


class HiddenBN1(unittest.TestCase):
    def test_quantity_multiplies_the_unit_price(self):
        cart = Cart()
        cart.add("tea", 450, quantity=3)
        self.assertEqual(1350, cart.total_cents())

    def test_a_merged_line_counts_every_unit(self):
        cart = Cart()
        cart.add("tea", 450)
        cart.add("tea", 450, quantity=2)
        cart.add("mug", 1200)
        self.assertEqual(2550, cart.total_cents())
