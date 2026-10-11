"""Keep data/parsed/snapshot_latest.json fresh and build a history of market stats.

    python -m undertow.refresher                    # loop forever, public RPC (slow, ~15 req/s)
    python -m undertow.refresher --once             # one cycle
    python -m undertow.refresher --rpc https://monad-mainnet.g.alchemy.com/v2/KEY   # own RPC (unthrottled, much faster)

Each cycle: take a full snapshot, parse it, check open interest, write latest atomically
(readers never see a half-written file), append one row per market to
data/history/markets.jsonl and write data/status.json with the cycle duration.
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
import traceback
from pathlib import Path

from . import accounts as registry
from . import config
from . import perpl_cli
from .checks import check_open_interest
from .snapshot_parser import Snapshot, parse_snapshot

PARSED = config.DATA / "parsed"
HISTORY = config.DATA / "history" / "markets.jsonl"
STATUS = config.DATA / "status.json"
ARCHIVE = config.DATA / "archive"
ARCHIVE_EVERY_S = 600  # keep one compressed full snapshot every 10 minutes ...
ARCHIVE_KEEP = 432  # ... for about 3 days; lets zones be compared with what was liquidated later
ERROR_BACKOFF_S = 15


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def market_rows(snap: Snapshot) -> list[dict]:
    rows = []
    for pid, p in snap.perps.items():
        pos = [x for x in snap.positions if x.perp_id == pid]
        if not pos and not p.open_interest_usd:
            continue
        rows.append({
            "block": snap.block, "ts": snap.timestamp, "perp_id": pid, "name": p.name,
            "mark": p.mark, "oracle": p.oracle, "last": p.last, "funding_rate": p.funding_rate,
            "oi_usd": p.open_interest_usd, "oi_size": p.open_interest_size,
            "n_long": sum(1 for x in pos if x.side == "Long"),
            "n_short": sum(1 for x in pos if x.side == "Short"),
            "long_deposit": sum(x.deposit for x in pos if x.side == "Long"),
            "short_deposit": sum(x.deposit for x in pos if x.side == "Short"),
            "orders": len(p.orders),
        })
    return rows


def archive_snapshot(snap: Snapshot) -> bool:
    """Save a gzip copy of the parsed snapshot if the newest archived one is older than ARCHIVE_EVERY_S."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    files = sorted(ARCHIVE.glob("snapshot_*.json.gz"), key=lambda f: f.stat().st_mtime)
    if files and time.time() - files[-1].stat().st_mtime < ARCHIVE_EVERY_S:
        return False
    with gzip.open(ARCHIVE / f"snapshot_{snap.block}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f)
    for old in files[: max(0, len(files) + 1 - ARCHIVE_KEEP)]:
        old.unlink()
    return True


def run_cycle(rpc: str | None, throttle: int | None, testnet: bool) -> dict:
    t0 = time.time()
    raw = perpl_cli.take_snapshot(rpc=rpc, throttle=throttle, testnet=testnet)
    fetched = time.time()
    snap = parse_snapshot(raw)
    checks = check_open_interest(snap)
    d = snap.to_dict()
    _atomic_write(PARSED / "snapshot_latest.json", json.dumps(d))
    archive_snapshot(snap)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        for row in market_rows(snap):
            f.write(json.dumps(row) + "\n")
    # New accounts are mapped to addresses immediately; known ones are refreshed hourly.
    ids = sorted({p.account_id for p in snap.positions})
    try:
        reg = registry.fill_registry(ids, max_age_s=3600, rpc=rpc or registry.onchain.PUBLIC_RPC)
        mapped = sum(1 for i in ids if i in reg)
    except Exception:
        traceback.print_exc()
        mapped = 0
    status = {
        "block": snap.block, "snapshot_time": snap.timestamp,
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "fetch_seconds": round(fetched - t0, 1), "total_seconds": round(time.time() - t0, 1),
        "accounts": len({p.account_id for p in snap.positions}), "positions": len(snap.positions),
        "accounts_mapped_to_address": mapped,
        "oi_checks_passed": sum(c.ok for c in checks), "oi_checks_total": len(checks),
        "rpc": rpc or "public (15 req/s)",
    }
    _atomic_write(STATUS, json.dumps(status, indent=2))
    return status


def main() -> None:
    ap = argparse.ArgumentParser(prog="undertow.refresher")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--rpc")
    ap.add_argument("--throttle", type=int)
    ap.add_argument("--testnet", action="store_true", default=config.TESTNET)
    ap.add_argument("--pause", type=float, default=0, help="seconds to wait between cycles")
    a = ap.parse_args()
    while True:
        try:
            s = run_cycle(a.rpc, a.throttle, a.testnet)
            print(f"[{s['written_at']}] block {s['block']}  {s['positions']} positions  "
                  f"{s['fetch_seconds']}s  OI {s['oi_checks_passed']}/{s['oi_checks_total']}", flush=True)
        except Exception:
            traceback.print_exc()
            if not a.once:
                time.sleep(ERROR_BACKOFF_S)  # e.g. the connection dropped; do not hammer the RPC
        if a.once:
            break
        time.sleep(a.pause)


if __name__ == "__main__":
    main()
