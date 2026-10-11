"""Correctness checks from the roadmap ("How we prove the numbers are right").

Check 1 (this step): the open interest we rebuild by adding up every open
position must match the open interest Perpl itself reports for that market.
Every long is matched with a short, so total long size == total short size
== open interest.
"""
from __future__ import annotations

from dataclasses import dataclass

from .snapshot_parser import Snapshot


@dataclass
class OiCheck:
    perp_id: int
    name: str
    long_size: float
    short_size: float
    reported_oi: float | None
    ok: bool
    note: str


def check_open_interest(snap: Snapshot, rel_tol: float = 0.01) -> list[OiCheck]:
    results: list[OiCheck] = []
    for pid, perp in sorted(snap.perps.items()):
        longs = sum(p.size for p in snap.positions if p.perp_id == pid and p.side == "Long")
        shorts = sum(p.size for p in snap.positions if p.perp_id == pid and p.side == "Short")
        rep = perp.open_interest_size
        if rep is None:
            results.append(OiCheck(pid, perp.name, longs, shorts, None, longs == shorts, "no reported OI"))
            continue
        # Reported OI is one side's total size; compare both sides against it.
        ok = all(abs(x - rep) <= rel_tol * max(rep, 1e-12) + 1e-9 for x in (longs, shorts))
        note = "match" if ok else f"mismatch: longs {longs:.6g}, shorts {shorts:.6g}, reported {rep:.6g}"
        results.append(OiCheck(pid, perp.name, longs, shorts, rep, ok, note))
    return results
