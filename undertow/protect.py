"""Liquidation-aware orders: rules that act when a big liquidation zone gets close.

A rule is attached to a resting order, for example "if a zone of $25k or more is within 1% of the price on the
dangerous side, cancel it" (or halve it). `evaluate` says whether the rule is triggered NOW. `RuleWatcher` runs rules
against each new snapshot and calls an action handler.

IMPORTANT LIMITS (also shown to users):
  * Perpl's contract does not know this order type. It lives in this program; if the program stops, the rule stops.
  * Rules are checked once per data refresh (about every 45 s on the public RPC), NOT every 0.4 s block.
  * Cancelling by order id has not been run against the chain. In paper mode the action is only logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import risk


@dataclass
class Rule:
    perp_id: int
    order_side: str  # "bid" (a buy) or "ask" (a sell)
    action: str = "cancel"  # "cancel" | "halve"
    zone_min_usd: float = 25_000.0
    within_pct: float = 1.0


def _edge_distance_pct(z: risk.Zone, mark: float) -> float:
    return min(abs(z.low / mark - 1), abs(z.high / mark - 1)) * 100


def evaluate(snap: dict, rule: Rule) -> dict:
    """Is a zone worth worrying about close to the price on the side this order is exposed to?

    A buy order fills into a falling price, so it is exposed to LONG zones below the price; a sell order to SHORT zones above.
    """
    mark = snap["perps"][str(rule.perp_id)]["mark"]
    side = "Long" if rule.order_side == "bid" else "Short"
    near = [z for z in risk.zones(snap, rule.perp_id)
            if z.side == side and z.notional >= rule.zone_min_usd and _edge_distance_pct(z, mark) <= rule.within_pct]
    if not near:
        return {"triggered": False, "action": None, "zone": None}
    z = max(near, key=lambda x: x.notional)
    return {"triggered": True, "action": rule.action,
            "zone": {"side": z.side, "low": z.low, "high": z.high, "notional": z.notional,
                     "distance_pct": _edge_distance_pct(z, mark)}}


class RuleWatcher:
    """Checks rules on each snapshot; fires the handler once when a rule becomes triggered, again only after it clears."""

    def __init__(self, rules: list[Rule], handler: Callable[[Rule, dict], None]):
        self.rules, self.handler = rules, handler
        self._active: set[int] = set()

    def check(self, snap: dict) -> list[dict]:
        fired = []
        for i, r in enumerate(self.rules):
            res = evaluate(snap, r)
            if res["triggered"] and i not in self._active:
                self._active.add(i)
                self.handler(r, res)
                fired.append({"rule": i, **res})
            elif not res["triggered"]:
                self._active.discard(i)
        return fired
