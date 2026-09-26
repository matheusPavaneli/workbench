import unittest

from shop.tax import vat_rate


class TaxTest(unittest.TestCase):
    def test_an_eu_invoice_carries_vat(self):
        self.assertEqual(20, vat_rate("FR"))
        self.assertEqual(0, vat_rate("US"))

    def test_a_code_that_is_not_canonical_cannot_be_invoiced(self):
        with self.assertRaises(ValueError):
            vat_rate("fr")
