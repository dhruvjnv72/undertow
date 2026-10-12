"""Command line for the wallet history store.

    python -m undertow.history_loader --days 3       backfill (or extend up to now) from 3 days back
    python -m undertow.history_loader --status       what is stored
    python -m undertow.history_loader --check        consistency check of the stored data (see history_store.py)
"""
from __future__ import annotations

import argparse
import sys
import time

from . import history_store as hs


def check() -> int:
    """Each event's size_before must equal the previous event's size_after for the same account and market (when both are known)."""
    con = hs.connect()
    rows = con.execute("SELECT account_id, perp_id, block, log_index, kind, size_before, size_after FROM events ORDER BY account_id, perp_id, block, log_index").fetchall()
    pairs = ok = 0
    bad = []
    prev = None
    for r in rows:
        if prev and prev[0] == r[0] and prev[1] == r[1] and prev[6] is not None and r[5] is not None:
            pairs += 1
            if abs(prev[6] - r[5]) <= 1e-9 * max(1.0, abs(prev[6])) + 1e-12:
                ok += 1
            elif len(bad) < 5:
                bad.append((r[0], r[1], prev[2], prev[4], prev[6], r[2], r[4], r[5]))
        prev = r
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
    print(f"{len(rows)} events; by kind: {kinds}")
    print(f"consecutive-size check: {ok}/{pairs} pairs consistent" + (f" ({ok / pairs:.2%})" if pairs else ""))
    for b in bad:
        print("  mismatch (account, perp, prev block, prev kind, prev after, block, kind, before):", b)
    return 0 if pairs and ok / pairs > 0.99 else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="undertow.history_loader")
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.status:
        print(hs.coverage() or "empty")
        return 0
    if a.check:
        return check()
    t0 = time.time()

    def progress(done, target, total):
        print(f"  block {done:,} of {target:,}  ({total:,} events so far, {time.time() - t0:.0f}s)", flush=True)

    r = hs.catch_up(days_if_empty=a.days, progress=progress)
    print(f"done: {r['new_events']:,} new events up to block {r['last_block']:,} in {time.time() - t0:.0f}s")
    print(hs.coverage())
    return 0


if __name__ == "__main__":
    sys.exit(main())
