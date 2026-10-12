"""Wallet history and statistics from the indexer's PositionEvent rows.

An event is a dict: kind (opened | increased | decreased | closed | flipped | liquidated | deleveraged), perpName, side, price, sizeBefore,
sizeAfter, realizedPnlUsd, fundingPaidUsd, block, timestamp, id. Events must cover ONE account.

Definitions (all stated on the screen):
  * A ROUND TRIP is one position of one account in one market from the moment its size is 0 until it returns to 0 (closed, liquidated, or
    flipped to the other side).
  * Realized result of a round trip = sum of realized price profit of its events minus funding paid in them. The funding sign comes from the
    official SDK (it subtracts the event's funding from the position's premium profit), so positive funding paid is a cost.
  * A result within +/- FLAT_USD (one tenth of a cent) counts as BREAK-EVEN, not a win or a loss. Win rate = wins / (wins + losses), so break-even trips
    are left out of it. Profit factor = total wins / total losses.
  * Results are PRICE profit and funding only. Trading fees, maker rebates and builder fees are not in these events, so for accounts that trade
    very often the realized result says little about their true profit.
  * The indexer only covers a WINDOW of blocks. A position opened before the window has no opening event: its trip is marked
    `opened_in_window = False`, its holding time is unknown, and earlier partial closes are missing. Everything is a statement about the window.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FLAT_USD = 0.001  # results this close to zero are break-even


@dataclass
class Trip:
    market: str
    side: str
    opened_ts: int | None
    closed_ts: int
    realized_net: float
    ended_by: str
    opened_in_window: bool
    entry_price: float | None = None
    exit_price: float | None = None

    @property
    def hold_seconds(self) -> int | None:
        return self.closed_ts - self.opened_ts if self.opened_ts is not None else None


def _order_key(e: dict):
    # ids look like "<chain>-<block>-<logIndex>"; order by block, then log index
    parts = str(e.get("id", "")).split("-")
    try:
        return (int(e["block"]), int(parts[-1]))
    except (ValueError, IndexError, KeyError):
        return (int(e.get("block", 0)), 0)


def _net(e: dict) -> float:
    return (e.get("realizedPnlUsd") or 0.0) - (e.get("fundingPaidUsd") or 0.0)


def trips(events: list[dict]) -> list[Trip]:
    """Pair events into finished round trips (per market)."""
    out: list[Trip] = []
    state: dict[str, dict] = {}  # market -> running trip
    for e in sorted(events, key=_order_key):
        m = e["perpName"]
        cur = state.get(m)
        kind = e["kind"]
        if kind == "opened":
            state[m] = {"side": e["side"], "opened_ts": e["timestamp"], "entry": e.get("price"), "net": 0.0, "in_window": True}
            continue
        if cur is None:  # first thing we see for this market is mid-life: the opening was before the window
            cur = state[m] = {"side": e["side"], "opened_ts": None, "entry": None, "net": 0.0, "in_window": False}
            if kind == "flipped":  # the OLD side is what this event closes
                cur["side"] = "Long" if e["side"] == "Short" else "Short"
        cur["net"] += _net(e)
        if kind in ("closed", "liquidated", "deleveraged", "decreased") and (e.get("sizeAfter") or 0) <= 1e-12:
            out.append(Trip(m, cur["side"], cur["opened_ts"], e["timestamp"], cur["net"], kind, cur["in_window"], cur["entry"], e.get("price")))
            state.pop(m, None)
        elif kind == "flipped":
            out.append(Trip(m, cur["side"], cur["opened_ts"], e["timestamp"], cur["net"], "flipped", cur["in_window"], cur["entry"], e.get("price")))
            state[m] = {"side": e["side"], "opened_ts": e["timestamp"], "entry": e.get("price"), "net": 0.0, "in_window": True}
    return out


def stats(events: list[dict]) -> dict:
    ts = trips(events)
    wins = [t.realized_net for t in ts if t.realized_net > FLAT_USD]
    losses = [t.realized_net for t in ts if t.realized_net < -FLAT_USD]
    flat = len(ts) - len(wins) - len(losses)
    holds = [t.hold_seconds for t in ts if t.hold_seconds is not None]
    by_market: dict[str, float] = {}
    for t in ts:
        by_market[t.market] = by_market.get(t.market, 0.0) + t.realized_net
    streak = {"win": 0, "loss": 0}
    run_w = run_l = 0
    for t in sorted(ts, key=lambda x: x.closed_ts):
        if t.realized_net > FLAT_USD:
            run_w, run_l = run_w + 1, 0
        elif t.realized_net < -FLAT_USD:
            run_w, run_l = 0, run_l + 1
        streak["win"], streak["loss"] = max(streak["win"], run_w), max(streak["loss"], run_l)
    best = max(by_market.items(), key=lambda kv: kv[1]) if by_market else None
    worst = min(by_market.items(), key=lambda kv: kv[1]) if by_market else None
    return {
        "round_trips": len(ts), "wins": len(wins), "losses": len(losses), "break_even": flat,
        "win_rate": len(wins) / (len(wins) + len(losses)) if wins or losses else None,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else (None if not wins else float("inf")),
        "total_realized_usd": sum(t.realized_net for t in ts),
        "avg_win_usd": sum(wins) / len(wins) if wins else None, "avg_loss_usd": sum(losses) / len(losses) if losses else None,
        "largest_win_usd": max(wins) if wins else None, "largest_loss_usd": min(losses) if losses else None,
        "avg_hold_seconds": sum(holds) / len(holds) if holds else None, "hold_known_for": len(holds),
        "longest_win_streak": streak["win"], "longest_loss_streak": streak["loss"],
        "best_market": {"market": best[0], "usd": best[1]} if best else None,
        "worst_market": {"market": worst[0], "usd": worst[1]} if worst else None,
        "liquidations": sum(1 for t in ts if t.ended_by == "liquidated"),
        "trips_opened_before_window": sum(1 for t in ts if not t.opened_in_window),
    }


def opened_at(events: list[dict]) -> dict[str, dict]:
    """For each market, when the CURRENT position was opened, if the opening is inside the window."""
    cur: dict[str, dict] = {}
    for e in sorted(events, key=_order_key):
        m, kind = e["perpName"], e["kind"]
        if kind in ("opened", "flipped"):
            cur[m] = {"opened_ts": e["timestamp"], "price": e.get("price"), "leverage": e.get("leverage")}
        elif kind in ("closed", "liquidated", "deleveraged", "decreased") and (e.get("sizeAfter") or 0) <= 1e-12:
            cur.pop(m, None)
    return cur
