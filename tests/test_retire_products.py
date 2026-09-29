import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from retire_products import get_positive_days


class RetireProductsTests(unittest.TestCase):
    def test_uses_default_when_value_is_missing(self):
        self.assertEqual(get_positive_days("UNSET_RETIRE_TEST_DAYS", 45), 45)

    def test_rejects_zero_or_negative_retention(self):
        os.environ["RETIRE_TEST_DAYS"] = "0"
        with self.assertRaises(ValueError):
            get_positive_days("RETIRE_TEST_DAYS", 45)

    def test_accepts_positive_retention(self):
        os.environ["RETIRE_TEST_DAYS"] = "180"
        self.assertEqual(get_positive_days("RETIRE_TEST_DAYS", 45), 180)


if __name__ == "__main__":
    unittest.main()
