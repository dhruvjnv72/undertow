"""Risk engine: liquidation maths, liquidation zones and the cascade simulator.

Works on the parsed snapshot dict (data/parsed/snapshot_latest.json).

Liquidation formulas are taken from the official SDK (perpl-sdk 0.2.9, state/position.rs):
    maintenance requirement  MMR = entry * size / maintenance_x        (maintenance_x is the second number
                                                                         in "Margin: 15.00 / 25.00 (ini/mnt)")
    liquidation price        = entry + side * (MMR - deposit - premium_pnl) / size
    bankruptcy price         = entry - side * (deposit + premium_pnl) / size
    side = +1 for longs, -1 for shorts.  Liquidations are judged against the MARK price.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parent.parent
LATEST = config.DATA / "parsed" / "snapshot_latest.json"


def load_latest() -> dict:
    return json.loads(LATEST.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- formulas
def _side(side: str) -> int:
    return 1 if side == "Long" else -1


def maintenance_requirement(entry: float, size: float, maintenance_x: float) -> float:
    return entry * size / maintenance_x


def liquidation_price(entry: float, size: float, side: str, deposit: float, premium_pnl: float,
                      maintenance_x: float) -> float:
    mmr = maintenance_requirement(entry, size, maintenance_x)
    return max(0.0, entry + _side(side) * (mmr - deposit - premium_pnl) / size)


def bankruptcy_price(entry: float, size: float, side: str, deposit: float, premium_pnl: float) -> float:
    return max(0.0, entry - _side(side) * (deposit + premium_pnl) / size)


def liq_price_at_leverage(entry: float, size: float, side: str, leverage: float, maintenance_x: float,
                          premium_pnl: float = 0.0) -> float:
    """Liquidation price if the same position were opened with `leverage` (deposit = notional / leverage)."""
    return liquidation_price(entry, size, side, entry * size / leverage, premium_pnl, maintenance_x)


@dataclass
class FormulaCheck:
    total: int
    within_tolerance: int
    max_rel_error: float
    outliers: list[dict] = field(default_factory=list)


def verify_formula(snap: dict, rel_tol: float = 1e-3, abs_tol: float = 1e-6) -> FormulaCheck:
    """Correctness check: our formula vs the liquidation/bankruptcy prices printed by Perpl's SDK.

    A position passes if the relative error is within rel_tol, or the absolute error is within abs_tol
    (the snapshot prints prices with 6 decimals, which dominates for micro-priced markets).
    """
    ok, worst, outliers = 0, 0.0, []
    for p in snap["positions"]:
        m = snap["perps"][str(p["perp_id"])]["maintenance_margin_x"]
        liq = liquidation_price(p["entry_price"], p["size"], p["side"], p["deposit"], p["premium_pnl"], m)
        bk = bankruptcy_price(p["entry_price"], p["size"], p["side"], p["deposit"], p["premium_pnl"])
        err = max(abs(liq - p["liq_price"]) / max(p["liq_price"], 1e-12),
                  abs(bk - p["bankruptcy_price"]) / max(p["bankruptcy_price"], 1e-12))
        abs_err = max(abs(liq - p["liq_price"]), abs(bk - p["bankruptcy_price"]))
        if abs_err > abs_tol:
            worst = max(worst, err)
        if err <= rel_tol or abs_err <= abs_tol:
            ok += 1
        else:
            outliers.append({"account_id": p["account_id"], "perp_id": p["perp_id"], "side": p["side"],
                             "ours": liq, "perpl": p["liq_price"], "rel_error": err})
    return FormulaCheck(len(snap["positions"]), ok, worst, outliers)


# ---------------------------------------------------------------- zones
@dataclass
class Zone:
    side: str  # "Long" zones sit below the mark, "Short" zones above
    low: float
    high: float
    notional: float  # full position value being closed (size * mark), not the deposits
    positions: int
    accounts: list[int]
    top_account_share: float  # share of the zone's value held by its biggest account
    by_account: dict[int, float] = field(default_factory=dict)  # account id -> position value in the zone

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


def positions_for(snap: dict, perp_id: int) -> list[dict]:
    return [p for p in snap["positions"] if p["perp_id"] == perp_id]


def zones(snap: dict, perp_id: int, band_pct: float = 1.0, max_distance_pct: float = 40.0) -> list[Zone]:
    """Group liquidation prices into bands `band_pct` wide (percent of mark), within max_distance of the mark.

    Positions already past their liquidation price (should not exist) are ignored.
    """
    perp = snap["perps"][str(perp_id)]
    mark = perp["mark"]
    buckets: dict[tuple[str, int], list[dict]] = {}
    for p in positions_for(snap, perp_id):
        dist = (p["liq_price"] / mark - 1) * 100
        if (p["side"] == "Long" and dist >= 0) or (p["side"] == "Short" and dist <= 0):
            continue
        if abs(dist) > max_distance_pct:
            continue
        buckets.setdefault((p["side"], int(dist // band_pct)), []).append(p)
    out = []
    for (side, k), ps in buckets.items():
        notional = sum(p["size"] * mark for p in ps)
        by_acct: dict[int, float] = {}
        for p in ps:
            by_acct[p["account_id"]] = by_acct.get(p["account_id"], 0) + p["size"] * mark
        out.append(Zone(side, mark * (1 + k * band_pct / 100), mark * (1 + (k + 1) * band_pct / 100),
                        notional, len(ps), sorted(by_acct), max(by_acct.values()) / notional, by_acct))
    return sorted(out, key=lambda z: z.low)


def in_path_zones(snap: dict, position: dict) -> list[dict]:
    """Zones that sit between the mark price and this position's own liquidation price.

    Those are liquidations that happen BEFORE yours and can push the price on toward you. The position's
    own account is excluded from each zone's value.
    """
    perp_id = position["perp_id"]
    mark = snap["perps"][str(perp_id)]["mark"]
    liq, side = position["liq_price"], position["side"]
    out = []
    for z in zones(snap, perp_id, max_distance_pct=100):
        if z.side != side:
            continue
        between = (liq < z.mid < mark) if side == "Long" else (mark < z.mid < liq)
        if not between:
            continue
        others = z.notional - z.by_account.get(position["account_id"], 0.0)
        if others <= 0:
            continue
        out.append({"low": z.low, "high": z.high, "notional_others": others, "positions": z.positions,
                    "distance_pct": (z.mid / mark - 1) * 100})
    return sorted(out, key=lambda x: abs(x["distance_pct"]))


# ---------------------------------------------------------------- cascade
def book_levels(snap: dict, perp_id: int, bids: bool) -> list[tuple[float, float]]:
    """Aggregated resting liquidity as (price, size): bids best-first (high to low) or asks best-first."""
    levels: dict[float, float] = {}
    for o in snap["perps"][str(perp_id)]["orders"]:
        is_bid = o["kind"] in ("Open Long", "Close Short")
        if is_bid == bids:
            levels[o["price"]] = levels.get(o["price"], 0) + o["size"]
    return sorted(levels.items(), reverse=bids)


VISIBLE_DEPTH_PCT = 30.0  # resting orders further than this from the best price are ignored (mostly junk quotes)


def _impact(levels: list[tuple[float, float]], size: float, bids: bool) -> tuple[float, bool]:
    """Price impact of pushing `size` through the book from the best price.

    Returns (fractional move of the price, exhausted). The move is negative when selling into bids and
    positive when buying from asks. If the visible book (within VISIBLE_DEPTH_PCT of the best price) cannot
    absorb `size`, exhausted is True and the move is capped at that depth.
    """
    if not levels or size <= 0:
        return 0.0, not levels and size > 0
    best = levels[0][0]
    limit = VISIBLE_DEPTH_PCT / 100
    remaining, last = size, best
    for price, avail in levels:
        if abs(price / best - 1) > limit:
            return (last / best - 1) if remaining > 1e-12 else 0.0, remaining > 1e-12
        remaining -= avail
        last = price
        if remaining <= 1e-12:
            return last / best - 1, False
    return last / best - 1, True


def _consume(levels: list[tuple[float, float]], size: float) -> tuple[float, int, float, float]:
    """How much resting value `size` uses up, walking the book best price first (same depth limit as _impact).

    Returns (value taken, levels used, visible value, unfilled value).
    """
    if not levels:
        return 0.0, 0, 0.0, 0.0
    best, limit = levels[0][0], VISIBLE_DEPTH_PCT / 100
    remaining, taken, used, visible, last = size, 0.0, 0, 0.0, best
    for price, avail in levels:
        if abs(price / best - 1) > limit:
            break
        visible += price * avail
        last = price
        take = min(avail, remaining)
        if take > 1e-12:
            taken += take * price
            used += 1
            remaining -= take
    return taken, used, visible, max(0.0, remaining) * last


@dataclass
class Round:
    trigger_price: float
    positions: int
    notional: float
    cumulative_size: float
    end_price: float
    exhausted: bool
    book_taken_usd: float = 0.0  # resting order value used up so far (all rounds to here)
    book_levels: int = 0  # price levels (at least partly) used up so far
    book_visible_usd: float = 0.0  # all visible resting value on the side being hit
    unfilled_usd: float = 0.0  # forced size the visible book could not absorb


@dataclass
class Cascade:
    side: str  # "Long" = price falls, longs liquidated | "Short" = price rises, shorts liquidated
    trigger_price: float
    final_price: float
    triggered_positions: int
    triggered_notional: float
    forced_size: float
    rounds: list[Round]
    book_exhausted: bool
    assumptions: list[str]
    liq_between_positions: int = 0  # positions whose liquidation price lies between the mark and the target
    liq_between_usd: float = 0.0  # their value at the target price (before the "share hitting the book" setting)
    book_between_usd: float = 0.0  # resting orders on the side that is hit, between the mark and the target
    book_between_levels: int = 0

    @property
    def move_pct(self) -> float:
        return (self.final_price / self.trigger_price - 1) * 100


def simulate_cascade(snap: dict, perp_id: int, target_price: float, book_share: float = 1.0,
                     feedback: bool = True, max_rounds: int = 10) -> Cascade:
    """What if the mark price reaches `target_price`?  (worst-case estimate, see assumptions)

    1. Every position whose liquidation price has been crossed is liquidated; `book_share` of its size is
       assumed to go through the order book (the rest is taken by buy-to-liquidate buyers / deleveraging).
    2. That size pushes through the resting orders, best price first (sells hit bids, buys lift asks). The
       percentage the price moves in the book is applied to the trigger price (market-impact estimate).
    3. With feedback=True (stress assumption) the mark price is assumed to follow the book price, which can
       trigger more liquidations next round. Perpl's mark actually follows the oracle, so feedback=False
       gives the milder 'first round only' result.
    """
    perp = snap["perps"][str(perp_id)]
    mark = perp["mark"]
    falling = target_price < mark
    side = "Long" if falling else "Short"
    levels = book_levels(snap, perp_id, bids=falling)
    pending = [p for p in positions_for(snap, perp_id) if p["side"] == side]
    price, liquidated = target_price, set()
    rounds, cum_size, total_n, exhausted_any = [], 0.0, 0.0, False
    for _ in range(max_rounds):
        hit = [i for i, p in enumerate(pending) if i not in liquidated and
               ((falling and p["liq_price"] >= price) or (not falling and p["liq_price"] <= price))]
        if not hit:
            break
        liquidated.update(hit)
        add = sum(pending[i]["size"] for i in hit) * book_share
        cum_size += add
        total_n += add * price
        impact, exhausted = _impact(levels, cum_size, bids=falling)
        end = target_price * (1 + impact)
        taken, used, visible, unfilled = _consume(levels, cum_size)
        rounds.append(Round(price, len(hit), add * price, cum_size, end, exhausted, taken, used, visible, unfilled))
        exhausted_any = exhausted_any or exhausted
        price = end
        if not feedback or exhausted:
            break
    first = [p for p in pending if (falling and p["liq_price"] >= target_price) or (not falling and p["liq_price"] <= target_price)]
    between = [(p, sz) for p, sz in levels if (p >= target_price if falling else p <= target_price)]
    return Cascade(side, target_price, price, len(liquidated), total_n, cum_size, rounds, exhausted_any, [
        f"{book_share:.0%} of each liquidated position is assumed to go through the order book",
        "mark price assumed to follow the book price between rounds" if feedback
        else "first round only: mark price follows the oracle, not the book",
        f"only resting orders within {VISIBLE_DEPTH_PCT:.0f}% of the best price count as depth",
        "resting orders are those at the snapshot moment; makers may pull or add orders as the price moves",
    ], len(first), sum(p["size"] for p in first) * target_price, sum(p * sz for p, sz in between), len(between))


# ---------------------------------------------------------------- headline
def depth_notional(snap: dict, perp_id: int, bids: bool, within_pct: float) -> float:
    mark = snap["perps"][str(perp_id)]["mark"]
    lim = mark * (1 - within_pct / 100) if bids else mark * (1 + within_pct / 100)
    return sum(p * s for p, s in book_levels(snap, perp_id, bids) if (p >= lim if bids else p <= lim))


def fmt_usd(v: float) -> str:
    return f"${v / 1e6:,.2f}M" if v >= 1e6 else f"${v / 1e3:,.0f}k" if v >= 1e4 else f"${v:,.0f}"


def headline(snap: dict, perp_id: int, window_pct: float = 15.0) -> dict:
    """The plain-language sentence for the top of the Radar.

    Picks the biggest zone within `window_pct` of the mark. Risk label is a heuristic:
    forced value / resting order value between the mark and the zone's far edge: <0.5 low, <2 medium, otherwise high.
    """
    perp = snap["perps"][str(perp_id)]
    zs = [z for z in zones(snap, perp_id) if abs(z.mid / perp["mark"] - 1) * 100 <= window_pct]
    if not zs:
        return {"sentence": f"No liquidation zones within {window_pct:.0f}% of the {perp['name']} price.",
                "risk": "none", "zone": None}
    z = max(zs, key=lambda x: x.notional)
    dist = (z.mid / perp["mark"] - 1) * 100
    casc = simulate_cascade(snap, perp_id, z.low if z.side == "Long" else z.high)  # far edge: the whole zone is reached
    far_edge = (z.low if z.side == "Long" else z.high)
    reach = max(1.0, abs(far_edge / perp["mark"] - 1) * 100)  # count the resting orders between the price and the far edge of the zone
    depth = depth_notional(snap, perp_id, bids=(z.side == "Long"), within_pct=reach)
    ratio = z.notional / depth if depth else float("inf")
    risk = "low" if ratio < 0.5 else "medium" if ratio < 2 else "high"
    word = "longs" if z.side == "Long" else "shorts"
    direction = "below" if dist < 0 else "above"
    sentence = (f"{fmt_usd(z.notional)} of {word} {abs(dist):.1f}% {direction} the price. "
                f"Resting orders between the price and that zone ({reach:.1f}%): {fmt_usd(depth)}. Cascade risk: {risk}.")
    if z.top_account_share > 0.6 and z.positions > 1:
        sentence += f" {z.top_account_share:.0%} of this zone is one account."
    return {"sentence": sentence, "risk": risk, "ratio": ratio, "zone": z, "cascade": casc, "depth": depth}


def headlines(snap: dict, perp_id: int, n: int = 4, window_pct: float = 15.0) -> list[dict]:
    """Several plain-language lines: the biggest zone on each side, then the next biggest, each compared with the
    resting orders between the price and the zone (same rule as `headline`)."""
    perp = snap["perps"][str(perp_id)]
    zs = sorted((z for z in zones(snap, perp_id) if abs(z.mid / perp["mark"] - 1) * 100 <= window_pct), key=lambda x: -x.notional)
    picked = [next((z for z in zs if z.side == side), None) for side in ("Long", "Short")]
    picked = [z for z in picked if z] + [z for z in zs if z not in picked]
    out = []
    for z in picked[:n]:
        dist = (z.mid / perp["mark"] - 1) * 100
        far = z.low if z.side == "Long" else z.high
        reach = max(1.0, abs(far / perp["mark"] - 1) * 100)
        depth = depth_notional(snap, perp_id, bids=(z.side == "Long"), within_pct=reach)
        ratio = z.notional / depth if depth else float("inf")
        risk = "low" if ratio < 0.5 else "medium" if ratio < 2 else "high"
        sentence = (f"{fmt_usd(z.notional)} of {'longs' if z.side == 'Long' else 'shorts'} {abs(dist):.1f}% {'below' if dist < 0 else 'above'} the price "
                    f"(around {z.mid:,.0f}). Resting orders between the price and that zone: {fmt_usd(depth)}. Cascade risk: {risk}.")
        if z.top_account_share > 0.6 and z.positions > 1:
            sentence += f" {z.top_account_share:.0%} of this zone is one account."
        out.append({"sentence": sentence, "risk": risk, "side": z.side})
    return out
