import unittest

from shop.shipping import shipping_cents


class ShippingTest(unittest.TestCase):
    def test_each_zone_has_its_rate(self):
        self.assertEqual(500, shipping_cents("US"))
        self.assertEqual(1200, shipping_cents("DE"))
        self.assertEqual(1800, shipping_cents("CH"))

    def test_a_country_outside_every_zone_is_international(self):
        self.assertEqual(3500, shipping_cents("JP"))
