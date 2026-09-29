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


if __name__ == "__main__":
    unittest.main()
