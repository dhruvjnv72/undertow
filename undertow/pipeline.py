"""Command line: fetch a snapshot (or load a saved one), parse it, run checks, save JSON.

    python -m undertow.pipeline fetch            # take a fresh snapshot from Monad mainnet
    python -m undertow.pipeline parse FILE.txt   # parse an existing snapshot text file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, perpl_cli
from .checks import check_open_interest
from .snapshot_parser import Snapshot, parse_snapshot

RAW = config.DATA / "raw"
PARSED = config.DATA / "parsed"


def save(snap: Snapshot, raw_text: str | None) -> Path:
    PARSED.mkdir(parents=True, exist_ok=True)
    if raw_text is not None:
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / f"snapshot_{snap.block}.txt").write_text(raw_text, encoding="utf-8")
    out = PARSED / f"snapshot_{snap.block}.json"
    out.write_text(json.dumps(snap.to_dict()), encoding="utf-8")
    (PARSED / "snapshot_latest.json").write_text(json.dumps(snap.to_dict()), encoding="utf-8")
    return out


def summarize(snap: Snapshot) -> str:
    accounts = {p.account_id for p in snap.positions}
    lines = [
        f"Block {snap.block} @ {snap.timestamp}  ({snap.exchange})",
        f"{len(snap.perps)} markets, {len(accounts)} accounts with positions, "
        f"{len(snap.positions)} open positions, "
        f"{sum(len(p.orders) for p in snap.perps.values())} resting orders",
        "",
        f"{'Perp':<14}{'Mark':>12}{'OI (USD)':>14}{'Longs':>8}{'Shorts':>8}{'Orders':>8}  OI check",
    ]
    checks = {c.perp_id: c for c in check_open_interest(snap)}
    for pid, p in sorted(snap.perps.items()):
        n_long = sum(1 for x in snap.positions if x.perp_id == pid and x.side == "Long")
        n_short = sum(1 for x in snap.positions if x.perp_id == pid and x.side == "Short")
        c = checks[pid]
        lines.append(
            f"{p.name + (' (paused)' if p.paused else ''):<14}"
            f"{(p.mark or 0):>12,.4f}{(p.open_interest_usd or 0):>14,.0f}"
            f"{n_long:>8}{n_short:>8}{len(p.orders):>8}  {'PASS' if c.ok else 'FAIL ' + c.note}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="undertow.pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="take a fresh snapshot with perpl-cli")
    f.add_argument("--block", type=int)
    f.add_argument("--rpc")
    f.add_argument("--testnet", action="store_true", default=config.TESTNET)
    p = sub.add_parser("parse", help="parse a saved snapshot text file")
    p.add_argument("file")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        raw = perpl_cli.take_snapshot(block=args.block, rpc=args.rpc, testnet=args.testnet)
    else:
        raw = Path(args.file).read_text(encoding="utf-8", errors="replace")
    snap = parse_snapshot(raw)
    out = save(snap, raw if args.cmd == "fetch" else None)
    print(summarize(snap))
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
