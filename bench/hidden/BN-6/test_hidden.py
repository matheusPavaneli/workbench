import unittest

from shop.stock import available


class HiddenBN6(unittest.TestCase):
    def test_the_last_units_on_hand_can_be_ordered(self):
        self.assertTrue(available({"tea": 2}, "tea", 2))
        self.assertTrue(available({"tea": 1}, "tea"))

    def test_more_than_on_hand_is_refused(self):
        self.assertFalse(available({"tea": 2}, "tea", 3))

    def test_an_unknown_sku_is_still_refused(self):
        self.assertFalse(available({}, "tea"))
