"""Live signals: Perpl's live prices, book and trades combined with the liquidation-zone data.

Inputs: a `LiveFeed.view()` (live, updated every block) and the positions snapshot (up to about a minute old).
Liquidations are decided on the MARK price, so each position's distance to liquidation is recomputed against the LIVE mark every
time. What can still be stale: positions opened, closed or changed since the snapshot, and small funding drift in liquidation prices.

All thresholds below are heuristics chosen by us, not Perpl's; they are returned with the signals so the screen can show them.
"""
from __future__ import annotations

import time

from . import risk

THRESHOLDS = {
    "zone_alert_pct": 1.0, "zone_warn_pct": 3.0, "fast_move_10s_pct": 0.15, "basis_warn_bps": 15.0,
    "flow_dominance": 0.70, "imbalance_extreme": 0.70, "near_liq_value_usd": 20_000.0,
}


def book_to_orders(view: dict) -> list[dict]:
    """The live book in the shape risk.py expects (aggregated levels as resting orders)."""
    return ([{"price": p, "size": s, "kind": "Open Long"} for p, s in view["full_bids"]] +
            [{"price": p, "size": s, "kind": "Open Short"} for p, s in view["full_asks"]])


def _change_pct(history: list, seconds: float) -> float | None:
    if len(history) < 2:
        return None
    now_ts, now_mark = history[-1][0], history[-1][1]
    old = next((h for h in history if now_ts - h[0] <= seconds), None)
    if old is None or old[1] == 0 or old is history[-1]:
        return None
    # use the oldest sample still inside the window, only if the window is mostly covered
    if now_ts - old[0] < seconds * 0.6:
        return None
    return (now_mark / old[1] - 1) * 100


def _depth_within(levels: list[tuple[float, float]], ref: float, pct: float, bids: bool) -> float:
    lim = ref * (1 - pct / 100) if bids else ref * (1 + pct / 100)
    return sum(p * s for p, s in levels if (p >= lim if bids else p <= lim))


def compute(view: dict, snap: dict | None, snap_age_s: float | None, now: float | None = None) -> dict:
    now = now or time.time()
    mark, oracle, last = view["mark"], view["oracle"], view["last"]
    out: dict = {"thresholds": THRESHOLDS, "snapshot_age_s": snap_age_s}
    hist = view["history"]

    # --- price signals
    out["basis_bps"] = (mark / oracle - 1) * 1e4 if oracle else None
    out["last_vs_mark_bps"] = (last / mark - 1) * 1e4 if mark else None
    out["spread_bps"] = ((view["best_ask"] - view["best_bid"]) / view["mid"] * 1e4) if view["mid"] else None
    out["change_24h_pct"] = (mark / view["price_24h_ago"] - 1) * 100 if view["price_24h_ago"] else None
    out["momentum_pct"] = {f"{s}s": _change_pct(hist, s) for s in (10, 60, 300)}

    # --- book imbalance (value within 0.5% of the mid, each side)
    ref = view["mid"] or mark
    bid_v, ask_v = _depth_within(view["full_bids"], ref, 0.5, True), _depth_within(view["full_asks"], ref, 0.5, False)
    out["book"] = {"bid_value_0_5pct": bid_v, "ask_value_0_5pct": ask_v,
                   "bid_share": bid_v / (bid_v + ask_v) if bid_v + ask_v else None}

    # --- trade flow over the last 60 s
    cutoff = (now - 60) * 1000
    recent = [t for t in view["all_trades"] if t["t"] >= cutoff]
    buy = sum(t["notional"] for t in recent if t["side"] == "buy")
    sell = sum(t["notional"] for t in recent if t["side"] == "sell")
    out["flow_60s"] = {"buy_usd": buy, "sell_usd": sell, "trades": len(recent),
                       "buy_share": buy / (buy + sell) if buy + sell else None,
                       "largest_usd": max((t["notional"] for t in recent), default=0.0)}

    # --- liquidation-aware signals, using the LIVE mark against the snapshot's positions
    zones_out, prox, past = [], {}, {"count": 0, "value": 0.0}
    if snap is not None and str(view["perp_id"]) in snap["perps"]:
        perp = snap["perps"][str(view["perp_id"])]
        live_snap = {"block": snap.get("block"), "positions": snap["positions"],
                     "perps": {str(view["perp_id"]): {**perp, "mark": mark, "orders": book_to_orders(view)}}}
        pos = risk.positions_for(live_snap, view["perp_id"])
        for k in (0.25, 0.5, 1.0, 2.0):
            lo = [p for p in pos if p["side"] == "Long" and 0 <= (mark - p["liq_price"]) / mark * 100 <= k]
            sh = [p for p in pos if p["side"] == "Short" and 0 <= (p["liq_price"] - mark) / mark * 100 <= k]
            prox[f"{k:g}%"] = {"long_count": len(lo), "long_usd": sum(p["size"] * mark for p in lo),
                               "short_count": len(sh), "short_usd": sum(p["size"] * mark for p in sh)}
        crossed = [p for p in pos if (p["side"] == "Long" and p["liq_price"] >= mark) or (p["side"] == "Short" and p["liq_price"] <= mark)]
        past = {"count": len(crossed), "value": sum(p["size"] * mark for p in crossed)}
        oi_usd = (perp.get("open_interest_usd") or 0) or view["open_interest"] * mark
        zone_min = max(5_000.0, 0.02 * oi_usd)
        zs = risk.zones(live_snap, view["perp_id"], band_pct=0.5, max_distance_pct=30)
        for side in ("Long", "Short"):
            side_z = sorted((z for z in zs if z.side == side and z.notional >= zone_min),
                            key=lambda z: min(abs(z.low / mark - 1), abs(z.high / mark - 1)))[:3]
            for z in side_z:
                edge = min(abs(z.low / mark - 1), abs(z.high / mark - 1)) * 100
                far = z.low if side == "Long" else z.high
                casc = risk.simulate_cascade(live_snap, view["perp_id"], far)
                depth = risk.depth_notional(live_snap, view["perp_id"], bids=(side == "Long"), within_pct=5.0)
                ratio = z.notional / depth if depth else float("inf")
                zones_out.append({"side": side, "low": z.low, "high": z.high, "notional": z.notional, "positions": z.positions,
                                  "accounts": len(z.accounts), "top_account_share": z.top_account_share,
                                  "distance_pct": edge, "depth_5pct_usd": depth,
                                  "risk": "low" if ratio < 0.5 else "medium" if ratio < 2 else "high",
                                  "cascade_end": casc.final_price, "cascade_move_pct": (casc.final_price / far - 1) * 100,
                                  "cascade_book_runs_out": casc.book_exhausted, "cascade_forced_usd": casc.triggered_notional})
        out["zone_min_usd"] = zone_min
    out["liq_proximity"], out["liq_crossed"], out["zones"] = prox, past, sorted(zones_out, key=lambda z: z["distance_pct"])

    # --- alerts (heuristic thresholds above)
    T, alerts = THRESHOLDS, []
    for z in out["zones"][:4]:
        word = "longs" if z["side"] == "Long" else "shorts"
        if z["distance_pct"] <= T["zone_alert_pct"]:
            alerts.append({"level": "alert", "key": f"zone-{z['side']}-{round(z['low'])}",
                           "text": f"Mark is {z['distance_pct']:.2f}% from ${z['notional'] / 1000:,.0f}k of {word} ({z['risk']} cascade risk)"})
        elif z["distance_pct"] <= T["zone_warn_pct"]:
            alerts.append({"level": "warn", "key": f"zone-{z['side']}-{round(z['low'])}",
                           "text": f"${z['notional'] / 1000:,.0f}k of {word} is {z['distance_pct']:.1f}% away ({z['risk']} cascade risk)"})
    m10 = out["momentum_pct"]["10s"]
    if m10 is not None and abs(m10) >= T["fast_move_10s_pct"]:
        alerts.append({"level": "warn", "key": "fast-move", "text": f"Fast move: mark {m10:+.2f}% in 10 seconds"})
    if out["basis_bps"] is not None and abs(out["basis_bps"]) >= T["basis_warn_bps"]:
        alerts.append({"level": "info", "key": "basis", "text": f"Mark is {out['basis_bps']:+.1f} bps from the oracle"})
    bs = out["flow_60s"]["buy_share"]
    if bs is not None and out["flow_60s"]["trades"] >= 5 and (bs >= T["flow_dominance"] or bs <= 1 - T["flow_dominance"]):
        alerts.append({"level": "info", "key": "flow", "text": f"{'Buying' if bs > 0.5 else 'Selling'} dominates the last minute ({max(bs, 1 - bs):.0%} of volume)"})
    sh = out["book"]["bid_share"]
    if sh is not None and (sh >= T["imbalance_extreme"] or sh <= 1 - T["imbalance_extreme"]):
        alerts.append({"level": "info", "key": "imbalance", "text": f"Book is {'bid' if sh > 0.5 else 'ask'}-heavy within 0.5% ({max(sh, 1 - sh):.0%})"})
    near = prox.get("0.5%")
    if near and near["long_usd"] + near["short_usd"] >= T["near_liq_value_usd"]:
        alerts.append({"level": "warn", "key": "near-liq", "text": f"${(near['long_usd'] + near['short_usd']) / 1000:,.0f}k of positions are within 0.5% of their liquidation price"})
    if past["count"]:
        alerts.append({"level": "info", "key": "crossed",
                       "text": f"{past['count']} positions (${past['value'] / 1000:,.1f}k) show a liquidation price the mark has already crossed: liquidation in progress, or the snapshot is behind"})
    out["alerts"] = alerts
    return out
