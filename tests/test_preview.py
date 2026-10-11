import unittest

from undertow import preview, protect, risk

MARK = 80_000.0


def snap(positions=(), asks=((80_010.0, 1.0), (80_100.0, 5.0)), bids=((79_990.0, 1.0), (79_900.0, 5.0)), funding=0.00004):
    orders = [{"price": p, "size": s, "kind": "Open Long"} for p, s in bids] + \
             [{"price": p, "size": s, "kind": "Open Short"} for p, s in asks]
    perp = {"name": "BTC", "mark": MARK, "maintenance_margin_x": 25.0, "initial_margin_x": 15.0, "taker_fee": 0.000345,
            "maker_fee": 0.000045, "funding_rate": funding, "orders": orders}
    return {"block": 1, "perps": {"1": perp}, "positions": list(positions)}


def pos(account, side, size, liq):
    return {"account_id": account, "perp_id": 1, "side": side, "size": size, "liq_price": liq}


class PreviewTests(unittest.TestCase):
    def test_liq_price_matches_docs_example(self):
        # Perpl docs: 10x long, 4% maintenance -> liquidated after a 6% fall
        r = preview.preview(snap(), 1, "Long", 1000, 10)
        self.assertAlmostEqual(r["liq_price"], MARK * 0.94, delta=1)
        self.assertAlmostEqual(r["liq_distance_pct"], -6.0, places=1)

    def test_short_liquidates_above(self):
        r = preview.preview(snap(), 1, "Short", 1000, 10)
        self.assertGreater(r["liq_price"], MARK)

    def test_leverage_bounds(self):
        with self.assertRaises(ValueError):
            preview.preview(snap(), 1, "Long", 100, 20)
        with self.assertRaises(ValueError):
            preview.preview(snap(), 1, "Long", 100, 0.5)
        with self.assertRaises(ValueError):
            preview.preview(snap(), 1, "Long", -5, 5)

    def test_fees_taker_vs_maker(self):
        t = preview.preview(snap(), 1, "Long", 1000, 5)
        m = preview.preview(snap(), 1, "Long", 1000, 5, limit_price=79_000)
        self.assertAlmostEqual(t["open_fee_usd"], 1000 * 0.000345)
        self.assertAlmostEqual(m["open_fee_usd"], 1000 * 0.000045)
        self.assertIsNone(m["slippage"])

    def test_funding_sign_and_size(self):
        pay = preview.funding_per_day_usd(10_000, 0.00004, "Long")
        get = preview.funding_per_day_usd(10_000, 0.00004, "Short")
        self.assertAlmostEqual(pay, 10_000 * 0.00004 * 86400 / 2580)
        self.assertAlmostEqual(get, -pay)
        self.assertLess(preview.funding_per_day_usd(10_000, -0.00004, "Long"), 0)  # negative rate: longs receive

    def test_slippage_grows_with_size(self):
        small = preview.slippage(snap(), 1, "Long", 0.5)["slippage_bps"]
        big = preview.slippage(snap(), 1, "Long", 3.0)["slippage_bps"]
        self.assertEqual(small, 0.0)
        self.assertGreater(big, small)

    def test_zone_in_path_is_found_and_ignored_when_small(self):
        s = snap([pos(2, "Long", 0.5, 77_000.0)])  # $40k zone at -3.75%
        self.assertEqual(len(preview.preview(s, 1, "Long", 1000, 10)["in_path"]), 1)  # liq 75.2k is below 77k
        self.assertEqual(len(preview.preview(s, 1, "Long", 1000, 30 if False else 15)["in_path"]), 0)  # liq ~80.7k is above it
        tiny = snap([pos(2, "Long", 0.01, 77_000.0)])
        self.assertEqual(preview.preview(tiny, 1, "Long", 1000, 10)["in_path"], [])

    def test_deep_book_survives_thin_book_does_not(self):
        s_deep = snap([pos(2, "Long", 0.5, 77_000.0)], bids=((79_900.0, 3.0), (77_000.0, 20.0)))
        s_thin = snap([pos(2, "Long", 0.5, 77_000.0)], bids=((79_900.0, 0.05),))
        self.assertTrue(preview.preview(s_deep, 1, "Long", 1000, 10)["cascade"]["reaches_your_liquidation"] is False)
        self.assertTrue(preview.preview(s_thin, 1, "Long", 1000, 10)["cascade"]["reaches_your_liquidation"])

    def test_alternatives_are_not_above_requested_leverage(self):
        r = preview.preview(snap(), 1, "Long", 1000, 8)
        self.assertTrue(all(a["leverage"] <= 8 for a in r["alternatives"]))
        liqs = [a["liq_price"] for a in r["alternatives"]]  # listed from high to low leverage
        self.assertEqual(liqs, sorted(liqs, reverse=True))  # lower leverage -> liquidation further below


class ProtectTests(unittest.TestCase):
    def test_bid_exposed_to_long_zone_below(self):
        s = snap([pos(2, "Long", 0.5, MARK * 0.995)])  # $40k, ~0.5% below
        res = protect.evaluate(s, protect.Rule(1, "bid", zone_min_usd=25_000, within_pct=1.0))
        self.assertTrue(res["triggered"])
        self.assertFalse(protect.evaluate(s, protect.Rule(1, "ask", zone_min_usd=25_000, within_pct=1.0))["triggered"])

    def test_small_or_far_zone_does_not_trigger(self):
        s = snap([pos(2, "Long", 0.1, MARK * 0.995), pos(3, "Long", 0.5, MARK * 0.9)])
        self.assertFalse(protect.evaluate(s, protect.Rule(1, "bid", zone_min_usd=25_000, within_pct=1.0))["triggered"])

    def test_watcher_fires_once_until_cleared(self):
        near, far = snap([pos(2, "Long", 0.5, MARK * 0.995)]), snap()
        fired = []
        w = protect.RuleWatcher([protect.Rule(1, "bid")], lambda r, res: fired.append(res))
        w.check(near); w.check(near)
        self.assertEqual(len(fired), 1)
        w.check(far); w.check(near)
        self.assertEqual(len(fired), 2)


if __name__ == "__main__":
    unittest.main()
