import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup


os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refresh_prices import extract_current_gbp_price, stored_price_matches


class RefreshPricesTests(unittest.TestCase):
    def test_extracts_decimal_gbp_offer_price(self):
        soup = BeautifulSoup(
            '<div class="priceToPay"><span class="a-price"><span class="a-offscreen">&pound;20.99</span></span></div>',
            "html.parser",
        )

        self.assertEqual(extract_current_gbp_price(soup), Decimal("20.99"))

    def test_ignores_non_gbp_price(self):
        soup = BeautifulSoup(
            '<div class="priceToPay"><span class="a-price"><span class="a-offscreen">EUR 40.78</span></span></div>',
            "html.parser",
        )

        self.assertIsNone(extract_current_gbp_price(soup))

    def test_compares_prices_to_two_decimal_places(self):
        self.assertTrue(stored_price_matches(20.99, Decimal("20.99")))
        self.assertFalse(stored_price_matches(2099, Decimal("20.99")))

    def test_extracts_price_when_offscreen_is_empty(self):
        markup = (
            '<span class="a-price priceToPay">'
            '<span class="a-offscreen"></span>'
            '<span aria-hidden="true">'
            '<span class="a-price-symbol">&pound;</span>'
            '<span class="a-price-whole">19<span class="a-price-decimal">.</span></span>'
            '<span class="a-price-fraction">99</span>'
            '</span></span>'
        )
        soup = BeautifulSoup(markup, "html.parser")
        self.assertEqual(extract_current_gbp_price(soup), Decimal("19.99"))


    def test_extracts_price_from_aok_offscreen(self):
        markup = '<span class="aok-offscreen">&pound;19.99</span>'
        soup = BeautifulSoup(markup, "html.parser")
        self.assertEqual(extract_current_gbp_price(soup), Decimal("19.99"))

    def test_extracts_price_from_hidden_inputs(self):
        markup = (
            '<input type="hidden" name="items[0.base][customerVisiblePrice][amount]" value="19.99"/>'
            '<input type="hidden" name="items[0.base][customerVisiblePrice][currencyCode]" value="GBP"/>'
        )
        soup = BeautifulSoup(markup, "html.parser")
        self.assertEqual(extract_current_gbp_price(soup), Decimal("19.99"))


if __name__ == "__main__":
    unittest.main()
