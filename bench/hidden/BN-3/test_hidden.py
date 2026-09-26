import unittest

from shop.format import format_money


class HiddenBN3(unittest.TestCase):
    def test_positive_amounts_are_unchanged(self):
        self.assertEqual("USD 123.45", format_money(12345))
        self.assertEqual("USD 0.00", format_money(0))

    def test_a_negative_amount_keeps_its_value(self):
        self.assertEqual("USD -1.50", format_money(-150))

    def test_a_negative_amount_under_one_unit_is_signed(self):
        self.assertEqual("USD -0.05", format_money(-5))

    def test_the_currency_is_kept(self):
        self.assertEqual("EUR -12.00", format_money(-1200, "EUR"))
