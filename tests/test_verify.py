import unittest
from pathlib import Path

from undertow import verify

FIX = Path(__file__).resolve().parent / "fixtures" / "book_btc.txt"


class ParseBookTests(unittest.TestCase):
    def test_parses_header_and_levels(self):
        b = verify.parse_cli_book(FIX.read_text(encoding="utf-8", errors="replace"))
        self.assertAlmostEqual(b["mark"], 82403.8)
        self.assertAlmostEqual(b["oi"], 8.14206)
        self.assertGreaterEqual(len(b["levels"]), 20)  # the CLI shows the top 10 levels per side

    def test_levels_are_price_size_orders(self):
        b = verify.parse_cli_book(FIX.read_text(encoding="utf-8", errors="replace"))
        price, size, n = b["levels"][0]
        self.assertGreater(price, 1000)
        self.assertGreater(size, 0)
        self.assertGreaterEqual(n, 1)

    def test_ignores_non_book_text(self):
        self.assertEqual(verify.parse_cli_book("hello\nworld")["levels"], [])


if __name__ == "__main__":
    unittest.main()
