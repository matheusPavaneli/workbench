import unittest

from shop.cart import Cart


class CartTest(unittest.TestCase):
    def test_an_empty_cart_totals_zero(self):
        self.assertEqual(0, Cart().total_cents())

    def test_single_items_add_up(self):
        cart = Cart()
        cart.add("tea", 450)
        cart.add("mug", 1200)
        self.assertEqual(1650, cart.total_cents())

    def test_adding_a_sku_again_merges_the_line(self):
        cart = Cart()
        cart.add("tea", 450)
        cart.add("tea", 450)
        self.assertEqual(1, len(cart.lines))
        self.assertEqual(2, cart.count())
