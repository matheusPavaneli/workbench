import unittest

from shop.loyalty import points


class HiddenBN7(unittest.TestCase):
    def test_one_point_per_whole_dollar(self):
        self.assertEqual(45, points(4500))
        self.assertEqual(45, points(4599))
        self.assertEqual(1, points(100))

    def test_under_a_dollar_earns_nothing(self):
        self.assertEqual(0, points(99))
        self.assertEqual(0, points(0))
        self.assertEqual(0, points(-500))
