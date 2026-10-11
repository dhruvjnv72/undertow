import unittest
from pathlib import Path

from undertow.checks import check_open_interest
from undertow.snapshot_parser import parse_snapshot

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "snapshot.txt"


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = parse_snapshot(SAMPLE.read_text(encoding="utf-8", errors="replace"))

    def test_header(self):
        self.assertEqual(self.snap.block, 111990497)

    def test_counts(self):
        self.assertEqual(len(self.snap.perps), 16)
        self.assertEqual(len({p.account_id for p in self.snap.positions}), 432)

    def test_known_position(self):
        # Account #5437: long BTC, entry 82474.8, liq 69281.231928
        p = next(x for x in self.snap.positions if x.account_id == 5437)
        self.assertEqual((p.perp_id, p.side, p.entry_price), (1, "Long", 82474.8))
        self.assertAlmostEqual(p.liq_price, 69281.231928)

    def test_btc_meta(self):
        btc = self.snap.perps[1]
        self.assertEqual(btc.name, "BTC")
        self.assertAlmostEqual(btc.mark, 82405.5)
        self.assertEqual((btc.initial_margin_x, btc.maintenance_margin_x), (15.0, 25.0))
        self.assertTrue(len(btc.orders) > 300)

    def test_open_interest_matches(self):
        bad = [c for c in check_open_interest(self.snap) if not c.ok]
        self.assertEqual(bad, [], bad)


if __name__ == "__main__":
    unittest.main()
