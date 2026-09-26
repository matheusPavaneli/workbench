import unittest

from shop.shipping import shipping_cents
from shop.tax import vat_rate


class HiddenBN5(unittest.TestCase):
    def test_a_lower_case_code_is_priced_by_its_zone(self):
        self.assertEqual(1200, shipping_cents("de"))
        self.assertEqual(500, shipping_cents("us"))

    def test_a_mixed_case_code_is_priced_by_its_zone(self):
        self.assertEqual(1800, shipping_cents("Ch"))

    def test_canonical_codes_and_unknown_countries_are_unchanged(self):
        self.assertEqual(1200, shipping_cents("DE"))
        self.assertEqual(3500, shipping_cents("jp"))

    def test_invoices_still_refuse_a_code_that_is_not_canonical(self):
        with self.assertRaises(ValueError):
            vat_rate("de")
