import unittest

from undertow import wallethistory as wh


def ev(i, kind, ts, side="Long", market="BTC", **kw):
    return {"id": f"143-{i}-0", "block": i, "kind": kind, "timestamp": ts, "side": side, "perpName": market, **kw}


class TripTests(unittest.TestCase):
    def test_simple_win(self):
        t = wh.trips([ev(1, "opened", 100, price=80000.0, sizeBefore=0, sizeAfter=1), ev(2, "closed", 400, price=81000.0, sizeAfter=0, realizedPnlUsd=10.0)])
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0].realized_net, 10.0)
        self.assertEqual(t[0].hold_seconds, 300)
        self.assertTrue(t[0].opened_in_window)

    def test_funding_is_a_cost(self):
        t = wh.trips([ev(1, "opened", 0), ev(2, "closed", 60, sizeAfter=0, realizedPnlUsd=-5.0, fundingPaidUsd=1.0)])
        self.assertAlmostEqual(t[0].realized_net, -6.0)

    def test_partial_decreases_accumulate_into_one_trip(self):
        t = wh.trips([ev(1, "opened", 0, sizeAfter=2), ev(2, "decreased", 10, sizeBefore=2, sizeAfter=1, realizedPnlUsd=4.0),
                      ev(3, "closed", 20, sizeAfter=0, realizedPnlUsd=3.0)])
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0].realized_net, 7.0)

    def test_decrease_to_zero_ends_trip(self):
        t = wh.trips([ev(1, "opened", 0, sizeAfter=1), ev(2, "decreased", 10, sizeBefore=1, sizeAfter=0, realizedPnlUsd=2.0)])
        self.assertEqual(len(t), 1)

    def test_liquidation_ends_trip_and_is_counted(self):
        t = wh.trips([ev(1, "opened", 0, sizeAfter=1), ev(2, "liquidated", 50, sizeBefore=1, sizeAfter=0, realizedPnlUsd=-30.0)])
        self.assertEqual(t[0].ended_by, "liquidated")
        self.assertEqual(wh.stats([ev(1, "opened", 0, sizeAfter=1), ev(2, "liquidated", 50, sizeAfter=0, realizedPnlUsd=-30.0)])["liquidations"], 1)

    def test_partial_liquidation_does_not_end_trip(self):
        t = wh.trips([ev(1, "opened", 0, sizeAfter=2), ev(2, "liquidated", 5, sizeBefore=2, sizeAfter=1, realizedPnlUsd=-3.0)])
        self.assertEqual(t, [])

    def test_flip_closes_old_side_and_opens_new(self):
        t = wh.trips([ev(1, "opened", 0, side="Long", sizeAfter=1), ev(2, "flipped", 10, side="Short", sizeBefore=1, sizeAfter=2, realizedPnlUsd=5.0),
                      ev(3, "closed", 30, side="Short", sizeAfter=0, realizedPnlUsd=-1.0)])
        self.assertEqual([(x.side, round(x.realized_net, 2)) for x in t], [("Long", 5.0), ("Short", -1.0)])

    def test_position_opened_before_window(self):
        t = wh.trips([ev(5, "decreased", 100, sizeBefore=3, sizeAfter=1, realizedPnlUsd=1.0), ev(6, "closed", 200, sizeAfter=0, realizedPnlUsd=2.0)])
        self.assertEqual(len(t), 1)
        self.assertFalse(t[0].opened_in_window)
        self.assertIsNone(t[0].hold_seconds)

    def test_markets_are_independent(self):
        t = wh.trips([ev(1, "opened", 0, market="BTC"), ev(2, "opened", 1, market="ETH"), ev(3, "closed", 5, market="ETH", sizeAfter=0, realizedPnlUsd=1.0)])
        self.assertEqual([x.market for x in t], ["ETH"])

    def test_events_are_ordered_by_block_not_list_order(self):
        t = wh.trips([ev(2, "closed", 20, sizeAfter=0, realizedPnlUsd=3.0), ev(1, "opened", 10, sizeAfter=1)])
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].hold_seconds, 10)


class StatsTests(unittest.TestCase):
    def events(self):
        e, i = [], 0
        for k, (pnl, market) in enumerate([(10, "BTC"), (-4, "BTC"), (6, "ETH"), (-2, "ETH"), (-2, "ETH"), (5, "BTC")]):
            e.append(ev(i := i + 1, "opened", k * 100, market=market, sizeAfter=1))
            e.append(ev(i := i + 1, "closed", k * 100 + 50, market=market, sizeAfter=0, realizedPnlUsd=float(pnl)))
        return e

    def test_win_rate_profit_factor_streaks(self):
        s = wh.stats(self.events())
        self.assertEqual((s["round_trips"], s["wins"], s["losses"]), (6, 3, 3))
        self.assertAlmostEqual(s["win_rate"], 0.5)
        self.assertAlmostEqual(s["profit_factor"], 21 / 8)  # wins 10+6+5 = 21, losses 4+2+2 = 8
        self.assertAlmostEqual(s["total_realized_usd"], 13.0)
        self.assertEqual(s["longest_loss_streak"], 2)
        self.assertEqual(s["longest_win_streak"], 1)
        self.assertAlmostEqual(s["avg_hold_seconds"], 50.0)

    def test_best_and_worst_market(self):
        s = wh.stats(self.events())
        self.assertEqual(s["best_market"]["market"], "BTC")
        self.assertAlmostEqual(s["best_market"]["usd"], 11.0)
        self.assertEqual(s["worst_market"]["market"], "ETH")

    def test_no_events(self):
        s = wh.stats([])
        self.assertEqual(s["round_trips"], 0)
        self.assertIsNone(s["win_rate"])
        self.assertIsNone(s["profit_factor"])

    def test_only_wins_gives_infinite_profit_factor(self):
        s = wh.stats([ev(1, "opened", 0), ev(2, "closed", 5, sizeAfter=0, realizedPnlUsd=3.0)])
        self.assertEqual(s["profit_factor"], float("inf"))


class FlatTests(unittest.TestCase):
    def test_near_zero_results_are_break_even_not_wins_or_losses(self):
        e = []
        for i, pnl in enumerate([0.0004, -0.0005, 5.0, -2.0]):
            e += [ev(2 * i + 1, "opened", i * 10, sizeAfter=1), ev(2 * i + 2, "closed", i * 10 + 5, sizeAfter=0, realizedPnlUsd=pnl)]
        s = wh.stats(e)
        self.assertEqual((s["round_trips"], s["wins"], s["losses"], s["break_even"]), (4, 1, 1, 2))
        self.assertAlmostEqual(s["win_rate"], 0.5)  # break-even trips are left out of the win rate

    def test_all_break_even_has_no_win_rate(self):
        s = wh.stats([ev(1, "opened", 0), ev(2, "closed", 5, sizeAfter=0, realizedPnlUsd=0.0)])
        self.assertIsNone(s["win_rate"])
        self.assertEqual(s["break_even"], 1)


class OpenedAtTests(unittest.TestCase):
    def test_current_position_open_time(self):
        o = wh.opened_at([ev(1, "opened", 100, price=80000.0, leverage=5.0, sizeAfter=1), ev(2, "increased", 200, sizeAfter=2)])
        self.assertEqual(o["BTC"]["opened_ts"], 100)

    def test_closed_position_is_not_current(self):
        self.assertEqual(wh.opened_at([ev(1, "opened", 100), ev(2, "closed", 200, sizeAfter=0)]), {})

    def test_reopened_uses_latest_open(self):
        o = wh.opened_at([ev(1, "opened", 100), ev(2, "closed", 200, sizeAfter=0), ev(3, "opened", 300)])
        self.assertEqual(o["BTC"]["opened_ts"], 300)

    def test_unknown_when_opened_before_window(self):
        self.assertEqual(wh.opened_at([ev(1, "increased", 100, sizeAfter=3)]), {})


if __name__ == "__main__":
    unittest.main()


class DrawdownTests(unittest.TestCase):
    def test_max_drawdown_is_peak_to_trough_of_running_total(self):
        # running total: +10, +4 (win then loss -6), -6 (loss -10), +14 (win +20); peak 10 -> trough -6 = drawdown 16
        evs = []
        for i, pnl in enumerate([10.0, -6.0, -10.0, 20.0]):
            evs += [ev(10 * i + 1, "opened", 100 * i, sizeAfter=1), ev(10 * i + 2, "closed", 100 * i + 50, sizeAfter=0, realizedPnlUsd=pnl)]
        self.assertAlmostEqual(wh.stats(evs)["max_drawdown_usd"], 16.0)

    def test_no_losses_means_zero_drawdown(self):
        evs = [ev(1, "opened", 0, sizeAfter=1), ev(2, "closed", 50, sizeAfter=0, realizedPnlUsd=5.0)]
        self.assertEqual(wh.stats(evs)["max_drawdown_usd"], 0.0)
