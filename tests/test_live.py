import time
import unittest
from collections import deque

from undertow import live, signals


def feed_with_market():
    f = live.LiveFeed()
    f.markets[1] = {"id": 1, "perp_id": 1, "name": "BTC", "pdec": 1, "sdec": 5, "book_live": True, "state": {}, "bids": {}, "asks": {},
                    "trades": deque(maxlen=300), "seen": set(), "history": deque(), "book_block": 0, "book_at": 0.0, "book_hist": deque(maxlen=64)}
    f.sid_stream = {10: "order-book@1", 20: "trades@1"}
    return f


def book(f, mt, bids, asks, block=100):
    f._on_message({"mt": mt, "sid": 10, "at": {"b": block}, "bid": [{"p": p, "s": s, "o": o} for p, s, o in bids],
                   "ask": [{"p": p, "s": s, "o": o} for p, s, o in asks]})


class FeedTests(unittest.TestCase):
    def test_snapshot_then_updates(self):
        f = feed_with_market()
        book(f, 15, [(800000, 100, 2), (799990, 50, 1)], [(800010, 70, 1)])
        book(f, 16, [(800000, 0, 0), (799980, 30, 1)], [(800010, 90, 2)], block=101)  # remove a level, add one, resize one
        v = f.view(1)
        self.assertEqual([round(x["price"], 1) for x in v["bids"]], [79999.0, 79998.0])
        self.assertAlmostEqual(v["asks"][0]["size"], 90 / 1e5)
        self.assertEqual(v["asks"][0]["orders"], 2)

    def test_snapshot_replaces_old_book(self):
        f = feed_with_market()
        book(f, 15, [(800000, 100, 2)], [(800010, 70, 1)])
        book(f, 15, [(700000, 10, 1)], [(700010, 10, 1)], block=102)
        v = f.view(1)
        self.assertEqual(len(v["bids"]), 1)
        self.assertAlmostEqual(v["bids"][0]["price"], 70000.0)

    def test_cumulative_size(self):
        f = feed_with_market()
        book(f, 15, [(800000, 100, 1), (799990, 50, 1)], [(800010, 70, 1)])
        b = f.view(1)["bids"]
        self.assertAlmostEqual(b[1]["cum"], 150 / 1e5)

    def test_book_hist_by_block(self):
        f = feed_with_market()
        book(f, 15, [(800000, 100, 2)], [(800010, 70, 1)], block=100)
        book(f, 16, [(800000, 200, 3)], [], block=101)
        old, new = f.book_at_block(1, 100), f.book_at_block(1, 101)
        self.assertAlmostEqual(old[0][0][1], 100 / 1e5)
        self.assertAlmostEqual(new[0][0][1], 200 / 1e5)

    def test_trades_dedupe_and_side(self):
        f = feed_with_market()
        t = {"at": {"t": 1000, "txid": "0xa", "l": 1, "b": 5}, "p": 800000, "s": 10, "sd": 1}
        f._on_message({"mt": 17, "sid": 20, "d": [t]})
        f._on_message({"mt": 18, "sid": 20, "d": [t, {**t, "at": {**t["at"], "l": 2}, "sd": 2}]})
        tr = f.view(1)["trades"]
        self.assertEqual(len(tr), 2)
        self.assertEqual({x["side"] for x in tr}, {"buy", "sell"})

    def test_market_state_merge_and_scaling(self):
        f = feed_with_market()
        f._on_message({"mt": 9, "d": {"1": {"at": {"b": 1, "t": 5}, "orl": 835957, "mrk": 836107, "lst": 835811, "oi": 908992, "dva": "12038428066159"}}})
        f._on_message({"mt": 9, "d": {"1": {"mrk": 836200}}})  # partial update keeps the other fields
        v = f.view(1)
        self.assertAlmostEqual(v["mark"], 83620.0)
        self.assertAlmostEqual(v["oracle"], 83595.7)
        self.assertAlmostEqual(v["open_interest"], 9.08992)
        self.assertAlmostEqual(v["volume_24h_usd"], 12038428.066159)

    def test_heartbeat_and_status(self):
        f = feed_with_market()
        f.connected = True
        f._on_message({"mt": 100, "sn": 5, "h": 12345})
        s = f.status()
        self.assertEqual(s["head_block"], 12345)
        self.assertTrue(s["connected"])


def sview(mark=80000.0, history=None, trades=(), bids=None, asks=None):
    return {"perp_id": 1, "mark": mark, "oracle": mark * 0.9998, "last": mark, "mid": mark, "best_bid": mark - 5, "best_ask": mark + 5,
            "price_24h_ago": mark * 0.99, "open_interest": 10.0, "history": history or [],
            "full_bids": bids or [(mark - 5 - i * 20, 1.0) for i in range(50)], "full_asks": asks or [(mark + 5 + i * 20, 1.0) for i in range(50)],
            "all_trades": list(trades)}


def ssnap(positions):
    return {"block": 1, "perps": {"1": {"mark": 80000.0, "maintenance_margin_x": 25.0, "open_interest_usd": 800000.0, "orders": []}},
            "positions": positions}


def lp(account, side, size, liq):
    return {"account_id": account, "perp_id": 1, "side": side, "size": size, "liq_price": liq}


class SignalTests(unittest.TestCase):
    def test_basis_and_spread(self):
        g = signals.compute(sview(), None, None)
        self.assertAlmostEqual(g["basis_bps"], 2.0, places=1)
        self.assertGreater(g["spread_bps"], 0)

    def test_zone_distance_uses_live_mark_not_snapshot_mark(self):
        snap = ssnap([lp(2, "Long", 1.0, 79_000.0)])  # $79k zone; snapshot mark says 80,000
        near = signals.compute(sview(mark=79_400.0), snap, 5)  # live mark has fallen: zone now 0.5% away
        far = signals.compute(sview(mark=80_000.0), snap, 5)
        self.assertLess(near["zones"][0]["distance_pct"], far["zones"][0]["distance_pct"])
        self.assertTrue(any(a["level"] == "alert" for a in near["alerts"]))

    def test_far_zone_gives_no_alert(self):
        g = signals.compute(sview(), ssnap([lp(2, "Long", 1.0, 70_000.0)]), 5)
        self.assertFalse(any(a["level"] == "alert" for a in g["alerts"]))

    def test_crossed_liquidation_is_reported(self):
        g = signals.compute(sview(mark=78_000.0), ssnap([lp(2, "Long", 1.0, 79_000.0)]), 5)  # mark already below the long's liq price
        self.assertEqual(g["liq_crossed"]["count"], 1)

    def test_flow_shares(self):
        now = time.time()
        tr = [{"t": (now - 5) * 1000, "notional": 900.0, "side": "sell"} for _ in range(9)] + [{"t": (now - 5) * 1000, "notional": 100.0, "side": "buy"}]
        g = signals.compute(sview(trades=tr), None, None, now=now)
        self.assertAlmostEqual(g["flow_60s"]["buy_share"], 100 / 8200, places=3)
        self.assertTrue(any("Selling dominates" in a["text"] for a in g["alerts"]))

    def test_old_trades_excluded_from_flow(self):
        now = time.time()
        g = signals.compute(sview(trades=[{"t": (now - 300) * 1000, "notional": 5000.0, "side": "buy"}]), None, None, now=now)
        self.assertEqual(g["flow_60s"]["trades"], 0)

    def test_momentum_needs_enough_history(self):
        now = time.time()
        short = [(now - 2, 80000.0, 80000.0), (now, 80100.0, 80000.0)]
        self.assertIsNone(signals.compute(sview(history=short), None, None)["momentum_pct"]["60s"])
        fast = [(now - 9, 80000.0, 80000.0), (now - 4, 80080.0, 80000.0), (now, 80160.0, 80000.0)]
        g = signals.compute(sview(mark=80160.0, history=fast), None, None)
        self.assertAlmostEqual(g["momentum_pct"]["10s"], 0.2, places=3)  # oldest sample inside the window is 9 s old
        self.assertTrue(any("Fast move" in a["text"] for a in g["alerts"]))
        calm = [(now - 9, 80000.0, 80000.0), (now, 80008.0, 80000.0)]
        self.assertFalse(any("Fast move" in a["text"] for a in signals.compute(sview(mark=80008.0, history=calm), None, None)["alerts"]))


if __name__ == "__main__":
    unittest.main()
