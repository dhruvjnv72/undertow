"""Order preview: what a trade would look like BEFORE it is placed. Nothing here sends an order.

Combines the risk engine (liquidation price, zones, cascade), the order book (slippage), fees and funding.

Funding units (checked against Perpl's public REST API on 2026-10-11): `Funding Rate: 0.00004` in the snapshot is a
FRACTION of position value per funding interval. The REST funding series for BTC showed rate 40 (units of 1e-6) and a
payment of 33 price-units (= $3.3 at one decimal) per BTC per interval, i.e. 4e-5 * 83,000 = $3.3. The interval is
2,580 s (8,571 blocks), about 33.5 intervals a day. Positive rate: longs pay shorts.
"""
from __future__ import annotations

from . import risk

FUNDING_INTERVAL_S = 2580
INTERVALS_PER_DAY = 86400 / FUNDING_INTERVAL_S
MIN_ZONE_USD = 5_000  # zones smaller than this are not worth warning about (a heuristic, same as the Wallet screen)


def funding_per_day_usd(notional: float, rate: float, side: str) -> float:
    """Dollars per day this position pays (positive) or receives (negative) at the CURRENT funding rate."""
    return notional * rate * INTERVALS_PER_DAY * (1 if side == "Long" else -1)


def slippage(snap: dict, perp_id: int, side: str, size: float) -> dict:
    """Walk the book for a market order of `size` base units. Buys lift asks; sells hit bids.

    Slippage is measured against the best price on the side being hit (the top of the book), not the mark price,
    which is an oracle-derived fair price and can sit away from the book.
    """
    levels = risk.book_levels(snap, perp_id, bids=(side == "Short"))
    if not levels:
        return {"avg_price": None, "slippage_bps": None, "filled_fraction": 0.0}
    best = levels[0][0]
    remaining, cost, filled = size, 0.0, 0.0
    for price, avail in levels:
        take = min(avail, remaining)
        cost += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    avg = cost / filled
    return {"avg_price": avg, "best_price": best, "slippage_bps": abs(avg / best - 1) * 1e4, "filled_fraction": filled / size}


def _zones_in_path(snap: dict, perp_id: int, side: str, mark: float, liq: float) -> list[dict]:
    out = []
    for z in risk.zones(snap, perp_id, max_distance_pct=100):
        if z.side != side or z.notional < MIN_ZONE_USD:
            continue
        between = (liq < z.mid < mark) if side == "Long" else (mark < z.mid < liq)
        if between:
            out.append({"low": z.low, "high": z.high, "notional": z.notional, "positions": z.positions,
                        "top_account_share": z.top_account_share, "distance_pct": (z.mid / mark - 1) * 100})
    return sorted(out, key=lambda x: abs(x["distance_pct"]))


def preview(snap: dict, perp_id: int, side: str, size_usd: float, leverage: float,
            limit_price: float | None = None, funding_rate: float | None = None) -> dict:
    """side is "Long" or "Short". limit_price None means a market (taker) order at the current mark."""
    perp = snap["perps"][str(perp_id)]
    mark, mx, max_lev = perp["mark"], perp["maintenance_margin_x"], perp["initial_margin_x"]
    if side not in ("Long", "Short"):
        raise ValueError("side must be Long or Short")
    if size_usd <= 0:
        raise ValueError("size must be positive")
    if not 1 <= leverage <= max_lev:
        raise ValueError(f"leverage must be between 1 and {max_lev:g} for this market")
    entry = limit_price or mark
    size = size_usd / entry
    margin = size_usd / leverage
    liq = risk.liq_price_at_leverage(entry, size, side, leverage, mx)
    dist = (liq / mark - 1) * 100

    in_path = _zones_in_path(snap, perp_id, side, mark, liq)
    biggest = max(in_path, key=lambda z: z["notional"]) if in_path else None

    def survives(lq: float) -> tuple[bool, dict | None]:
        """Does a position liquidated at `lq` survive the modelled cascade from the biggest zone between price and `lq`?"""
        zs = _zones_in_path(snap, perp_id, side, mark, lq)
        if not zs:
            return True, None
        z0 = max(zs, key=lambda z: z["notional"])
        # aim at the zone's far edge so every position in it has been reached
        c = risk.simulate_cascade(snap, perp_id, z0["low"] if side == "Long" else z0["high"])
        ok = (not c.book_exhausted) and ((c.final_price > lq) if side == "Long" else (c.final_price < lq))
        return ok, {"zone_notional": z0["notional"], "zone_distance_pct": z0["distance_pct"], "cascade_end": c.final_price,
                    "book_exhausted": c.book_exhausted, "triggered_notional": c.triggered_notional}

    ok_now, cascade = survives(liq)
    if cascade:
        cascade["reaches_your_liquidation"] = not ok_now
    safe_lev = leverage if ok_now else None
    lev = round(leverage - 0.5, 2)
    while safe_lev is None and lev >= 1:
        if survives(risk.liq_price_at_leverage(entry, size, side, lev, mx))[0]:
            safe_lev = lev
        lev = round(lev - 0.5, 2)
    alternatives = []
    for lv in sorted({leverage, max(1.0, leverage / 2), 5.0, 3.0, 2.0, 1.0}, reverse=True):
        if lv > leverage:
            continue
        lq = risk.liq_price_at_leverage(entry, size, side, lv, mx)
        alternatives.append({"leverage": lv, "margin": size_usd / lv, "liq_price": lq, "distance_pct": (lq / mark - 1) * 100,
                             "zones_in_path": len(_zones_in_path(snap, perp_id, side, mark, lq))})

    taker = limit_price is None
    fee_rate = perp["taker_fee"] if taker else perp["maker_fee"]
    rate = perp["funding_rate"] if funding_rate is None else funding_rate
    fund_day = funding_per_day_usd(size_usd, rate or 0.0, side)
    slip = slippage(snap, perp_id, side, size) if taker else None

    notes = []
    if in_path:
        notes.append(f"{risk.fmt_usd(sum(z['notional'] for z in in_path))} of other traders' {side.lower()}s sit between the price and your "
                     f"liquidation ({len(in_path)} zone{'s' if len(in_path) > 1 else ''}); the biggest is {risk.fmt_usd(biggest['notional'])}, "
                     f"{abs(biggest['distance_pct']):.1f}% away.")
        if cascade and cascade["reaches_your_liquidation"]:
            notes.append("In the worst-case cascade estimate, forced orders from that zone could push the price to your liquidation"
                         + (" (the visible order book runs out)." if cascade["book_exhausted"] else "."))
        elif cascade:
            notes.append("In the worst-case cascade estimate that zone's forced orders end before reaching your liquidation.")
    else:
        notes.append("No zone of $5k or more sits between the price and your liquidation.")
    if safe_lev is not None and safe_lev < leverage:
        notes.append(f"At {safe_lev:g}x your liquidation would survive the modelled cascade.")
    elif safe_lev is None:
        notes.append(f"No leverage from {leverage:g}x down to 1x survives the modelled cascade: the visible order book is too thin "
                     "against the zones in your path. (Lower leverage pushes your liquidation past more zones, so it is not always safer.)")
    return {
        "perp": perp["name"], "side": side, "entry": entry, "mark": mark, "size": size, "notional": size_usd, "margin": margin,
        "leverage": leverage, "max_leverage": max_lev, "liq_price": liq, "liq_distance_pct": dist,
        "in_path": in_path, "in_path_total": sum(z["notional"] for z in in_path), "cascade": cascade, "safe_leverage": safe_lev,
        "alternatives": alternatives, "order_type": "market (taker)" if taker else "limit (maker, post-only)",
        "fee_rate": fee_rate, "open_fee_usd": size_usd * fee_rate, "slippage": slip,
        "funding_rate": rate, "funding_per_day_usd": fund_day, "funding_per_day_pct": fund_day / size_usd * 100,
        "notes": notes,
    }
