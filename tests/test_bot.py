import os
import unittest
from unittest import mock

from undertow import bot, risk
from undertow.bot import Params, State, decide, paper_fills

MARK = 80_000.0


def make_snap(positions=(), bids=((79_900.0, 1.0), (79_000.0, 5.0)), asks=((80_100.0, 1.0), (81_000.0, 5.0))):
    orders = [{"price": p, "size": s, "kind": "Open Long"} for p, s in bids] + \
             [{"price": p, "size": s, "kind": "Open Short"} for p, s in asks]
    return {"block": 1000, "perps": {"1": {"mark": MARK, "maintenance_margin_x": 25.0, "orders": orders}},
            "positions": list(positions)}


def pos(account, side, size, liq):
    return {"account_id": account, "perp_id": 1, "side": side, "size": size, "liq_price": liq}


P = Params()


class DecideTests(unittest.TestCase):
    def test_normal_quotes_straddle_mark(self):
        d = decide(make_snap(), State(), P, 10)
        self.assertEqual(d.action, "quote")
        bid, ask = d.quotes
        self.assertEqual((bid.order_type, ask.order_type), ("open-long", "open-short"))
        self.assertAlmostEqual(bid.price, MARK * (1 - 8e-4), delta=0.1)
        self.assertAlmostEqual(ask.price, MARK * (1 + 8e-4), delta=0.1)
        self.assertLess(bid.price, MARK)
        self.assertGreater(ask.price, MARK)

    def test_prices_and_sizes_respect_tick_and_lot(self):
        d = decide(make_snap(), State(), P, 10)
        for q in d.quotes:
            self.assertAlmostEqual(q.price * 10, round(q.price * 10), places=6)
            self.assertAlmostEqual(q.size * 1e5, round(q.size * 1e5), places=6)

    def test_spread_beats_fees(self):
        # round trip earns 2 * half-spread; costs 2 * maker fee
        self.assertGreater(2 * P.half_spread_bps / 1e4, 2 * P.maker_fee * 5)

    def test_long_inventory_lowers_quotes_and_sells_with_close(self):
        flat = decide(make_snap(), State(), P, 10)
        st = State(cash=100, position=0.0004, entry=MARK)  # $32 long
        d = decide(make_snap(), st, P, 10)
        self.assertLess(d.quotes[0].price, flat.quotes[0].price)
        ask = next(q for q in d.quotes if q.side == "ask")
        self.assertEqual(ask.order_type, "close-long")
        self.assertLessEqual(ask.size, 0.0004 + 1e-9)

    def test_position_limit_stops_adding(self):
        st = State(cash=100, position=0.00074, entry=MARK)  # $59.2 long, +$20 would exceed $60
        d = decide(make_snap(), st, P, 10)
        self.assertNotIn("open-long", [q.order_type for q in d.quotes])
        self.assertIn("close-long", [q.order_type for q in d.quotes])

    def test_stale_data_pulls(self):
        self.assertEqual(decide(make_snap(), State(), P, 500).action, "pull")

    def test_kill_switch_halts(self):
        self.assertEqual(decide(make_snap(), State(), P, 10, kill=True).action, "halt")

    def test_daily_loss_halts(self):
        st = State(cash=94.0, day_start_equity=100.0)
        d = decide(make_snap(), st, P, 10)
        self.assertEqual(d.action, "halt")
        self.assertIn("daily loss", d.reason)

    def test_leverage_halts(self):
        st = State(cash=20.0, position=0.001, entry=MARK, day_start_equity=20.0)  # $80 on $20 = 4x
        d = decide(make_snap(), st, P, 10)
        self.assertEqual(d.action, "halt")
        self.assertIn("leverage", d.reason)

    def test_liquidation_distance_halts(self):
        p = Params(max_leverage=10.0, min_liq_distance_pct=25.0, max_position_usd=500)
        st = State(cash=20.0, position=0.001, entry=MARK, day_start_equity=20.0)  # 4x leverage
        d = decide(make_snap(), st, p, 10)
        self.assertEqual(d.action, "halt")
        self.assertIn("liquidation", d.reason)

    def test_halt_is_sticky(self):
        st = State(halted=True, halt_reason="x")
        self.assertEqual(decide(make_snap(), st, P, 10).action, "halt")

    def test_big_zone_near_thin_book_pulls(self):
        snap = make_snap([pos(2, "Long", 0.5, MARK * 0.99)], bids=((79_900.0, 0.01),))  # $40k zone, 1% below
        d = decide(snap, State(), P, 10)
        self.assertEqual(d.action, "pull")
        self.assertIn("zone", d.reason)

    def test_big_zone_near_deep_book_widens(self):
        snap = make_snap([pos(2, "Long", 0.5, MARK * 0.99)], bids=((79_900.0, 5.0),))  # $400k depth
        d = decide(snap, State(), P, 10)
        self.assertEqual(d.action, "quote")
        self.assertAlmostEqual(d.half_spread_bps, P.half_spread_bps * P.widen_factor)

    def test_small_or_far_zone_ignored(self):
        small = make_snap([pos(2, "Long", 0.05, MARK * 0.99)])  # $4k
        far = make_snap([pos(2, "Long", 0.5, MARK * 0.9)])
        for s in (small, far):
            self.assertAlmostEqual(decide(s, State(), P, 10).half_spread_bps, P.half_spread_bps)


class PaperFillTests(unittest.TestCase):
    def test_bid_fills_when_mark_falls_through_it(self):
        st = State(cash=100, resting=[{"side": "bid", "order_type": "open-long", "price": 79_936.0, "size": 0.00025}])
        self.assertEqual(paper_fills(st, 80_000.0, P), [])
        fills = paper_fills(st, 79_900.0, P)
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(st.position, 0.00025)
        self.assertAlmostEqual(st.entry, 79_936.0)
        self.assertGreater(st.fees, 0)

    def test_round_trip_realises_spread_minus_fees(self):
        st = State(cash=100, resting=[{"side": "bid", "order_type": "open-long", "price": 79_936.0, "size": 0.00025}])
        paper_fills(st, 79_900.0, P)
        st.resting = [{"side": "ask", "order_type": "close-long", "price": 80_064.0, "size": 0.00025}]
        paper_fills(st, 80_100.0, P)
        self.assertAlmostEqual(st.position, 0.0)
        gross = (80_064.0 - 79_936.0) * 0.00025
        self.assertAlmostEqual(st.realized, gross)
        self.assertAlmostEqual(st.cash, 100 + gross - st.fees)
        self.assertGreater(st.cash, 100)

    def test_unfilled_orders_stay(self):
        st = State(resting=[{"side": "ask", "order_type": "open-short", "price": 80_064.0, "size": 0.00025}])
        paper_fills(st, 80_000.0, P)
        self.assertEqual(len(st.resting), 1)


class LiveLockTests(unittest.TestCase):
    def test_refuses_without_flags(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(bot.LiveLocked):
                bot.LiveExecutor(P, testnet=True)

    def test_refuses_without_key(self):
        with mock.patch.dict(os.environ, {"UNDERTOW_ALLOW_LIVE": "yes"}, clear=True):
            with self.assertRaises(bot.LiveLocked):
                bot.LiveExecutor(P, testnet=True)

    def test_mainnet_needs_its_own_extra_switch(self):
        env = {"UNDERTOW_ALLOW_LIVE": "yes", "PERPL_PRIVATE_KEY": "x"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(bot.LiveLocked):
                bot.LiveExecutor(P, testnet=False)
        with mock.patch.dict(os.environ, {**env, "UNDERTOW_ALLOW_MAINNET": "yes"}, clear=True):
            bot.LiveExecutor(P, testnet=False)  # now allowed

    def test_key_file_path_is_used_not_key(self):
        q = bot.Quote("bid", "open-long", 79_936.0, 0.00025)
        cmd = bot.build_create_cmd(q, P, 1000, 7, True, key_file="/some/key.txt")
        self.assertEqual(cmd[cmd.index("--private-key-path") + 1], "/some/key.txt")
        self.assertGreater(cmd.index("--private-key-path"), cmd.index("create"))  # perpl-cli rejects it before `create`
        self.assertNotIn("--private-key", cmd)

    def test_command_is_post_only_expiring_and_keyless(self):
        q = bot.Quote("bid", "open-long", 79_936.0, 0.00025)
        cmd = bot.build_create_cmd(q, P, current_block=1000, request_id=42, testnet=True)
        self.assertIn("--post-only", cmd)
        self.assertEqual(cmd[cmd.index("--expiry-block") + 1], str(1000 + P.expiry_blocks))
        self.assertEqual(cmd[cmd.index("--request-id") + 1], "42")
        self.assertFalse(any("private" in c.lower() for c in cmd))
        self.assertIn("--testnet", cmd)


if __name__ == "__main__":
    unittest.main()
