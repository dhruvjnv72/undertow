import json
import unittest
from pathlib import Path

from undertow import risk

SNAP = Path(__file__).resolve().parent.parent / "data" / "parsed" / "snapshot_latest.json"


class FormulaTests(unittest.TestCase):
    """Cases copied from the official SDK's own unit tests (perpl-sdk position.rs)."""

    def test_sdk_long_liq_price(self):
        # entry 100, size 10, deposit 100, maintenance 20 -> requirement 50 -> liq 95
        self.assertAlmostEqual(risk.liquidation_price(100, 10, "Long", 100, 0, 20), 95)

    def test_sdk_short_liq_price(self):
        self.assertAlmostEqual(risk.liquidation_price(100, 10, "Short", 100, 0, 20), 105)

    def test_sdk_after_funding(self):
        # funding paid 5/unit by longs -> premium pnl -50 -> liq 100 (SDK test: 95 -> 100)
        self.assertAlmostEqual(risk.liquidation_price(100, 10, "Long", 100, -50, 20), 100)
        self.assertAlmostEqual(risk.liquidation_price(100, 10, "Short", 100, -50, 20), 100)

    def test_bankruptcy(self):
        self.assertAlmostEqual(risk.bankruptcy_price(100, 10, "Long", 100, 0), 90)
        self.assertAlmostEqual(risk.bankruptcy_price(100, 10, "Short", 100, 0), 110)

    def test_lower_leverage_liquidates_further_away(self):
        a = risk.liq_price_at_leverage(100, 1, "Long", 10, 25)
        b = risk.liq_price_at_leverage(100, 1, "Long", 5, 25)
        self.assertLess(b, a)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = json.loads(SNAP.read_text(encoding="utf-8"))

    def test_formula_matches_perpl_for_every_position(self):
        c = risk.verify_formula(self.snap)
        self.assertEqual(c.within_tolerance, c.total, c.outliers)

    def test_zones_are_on_the_correct_side(self):
        mark = self.snap["perps"]["1"]["mark"]
        for z in risk.zones(self.snap, 1):
            self.assertTrue(z.high <= mark if z.side == "Long" else z.low >= mark - 1e-6)

    def test_zone_value_is_conserved(self):
        mark = self.snap["perps"]["1"]["mark"]
        zs = risk.zones(self.snap, 1, max_distance_pct=100)
        total = sum(z.notional for z in zs)
        expected = sum(p["size"] * mark for p in risk.positions_for(self.snap, 1)
                       if (p["side"] == "Long" and p["liq_price"] < mark) or (p["side"] == "Short" and p["liq_price"] > mark))
        self.assertAlmostEqual(total, expected, places=3)

    def test_cascade_monotonic(self):
        mark = self.snap["perps"]["1"]["mark"]
        sizes = [risk.simulate_cascade(self.snap, 1, mark * f).triggered_notional for f in (0.99, 0.95, 0.9)]
        self.assertEqual(sizes, sorted(sizes))

    def test_no_liquidations_at_current_price(self):
        mark = self.snap["perps"]["1"]["mark"]
        c = risk.simulate_cascade(self.snap, 1, mark * 1.0001)
        self.assertLessEqual(c.triggered_notional, 1e5)

    def test_lower_book_share_gives_smaller_impact(self):
        mark = self.snap["perps"]["1"]["mark"]
        full = risk.simulate_cascade(self.snap, 1, mark * 0.93, book_share=1.0, feedback=False)
        part = risk.simulate_cascade(self.snap, 1, mark * 0.93, book_share=0.25, feedback=False)
        self.assertGreaterEqual(part.final_price, full.final_price)

    def test_impact_of_exhausted_book(self):
        levels = [(100.0, 1.0), (99.0, 1.0)]
        move, exhausted = risk._impact(levels, 5.0, bids=True)
        self.assertTrue(exhausted)
        self.assertAlmostEqual(move, -0.01)

    def test_impact_ignores_far_junk_orders(self):
        levels = [(100.0, 1.0), (0.1, 1000.0)]
        move, exhausted = risk._impact(levels, 5.0, bids=True)
        self.assertTrue(exhausted)
        self.assertGreater(move, -0.31)

    def test_in_path_excludes_own_account(self):
        p = next(x for x in self.snap["positions"] if x["perp_id"] == 1 and x["side"] == "Long")
        for z in risk.in_path_zones(self.snap, p):
            self.assertGreater(z["notional_others"], 0)

    def test_fmt_usd(self):
        self.assertEqual(risk.fmt_usd(309_500), "$310k")
        self.assertEqual(risk.fmt_usd(850), "$850")


if __name__ == "__main__":
    unittest.main()
