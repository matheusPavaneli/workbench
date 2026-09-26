import unittest

from shop.stock import available, restock


class StockTest(unittest.TestCase):
    def test_plenty_of_stock_is_available(self):
        self.assertTrue(available({"tea": 10}, "tea", 3))

    def test_an_unknown_sku_is_not_available(self):
        self.assertFalse(available({}, "tea"))

    def test_restocking_adds_units(self):
        stock = {"tea": 1}
        restock(stock, "tea", 4)
        self.assertEqual(5, stock["tea"])
