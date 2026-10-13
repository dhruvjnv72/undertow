"""Exchange-wide numbers that work on any host (no Envio indexer needed).

* TVL: the collateral token (AUSD) balance held by the exchange contract, read from the chain.
* Volume: summed from Perpl's public candle API for every market (daily candles for the history, hourly for the last 24 h).
* Active traders and recent liquidations: from the wallet-history database the server keeps current.
* TVL history: a sample every couple of minutes while the server runs (so net change only covers that time).

Everything here is read-only.
"""
from __future__ import annotations

import json
import threading
import time

from . import config, history_store, live

TOKEN = "0x00000000efe302beaa2b3e6e1b18d08d69a9012a"  # AUSD, the collateral token (from Perpl's /pub/context)
TOKEN_DECIMALS = 6
EXCHANGE = "0x34b6552d57a35a1d042ccae1951bd1c370112a6f"
TVL_FILE = config.DATA / "history" / "tvl.jsonl"
VOLUME_REFRESH_S = 600

_vol: dict = {"state": None, "error": None, "at": 0.0}
_started = False
_lock = threading.Lock()


def tvl_usd() -> float | None:
    """AUSD held by the exchange contract (that is every user's deposited collateral)."""
    if config.TESTNET:
        return None
    data = "0x70a08231" + EXCHANGE[2:].rjust(64, "0")  # balanceOf(exchange)
    raw = history_store._rpc("eth_call", [{"to": TOKEN, "data": data}, "latest"], timeout=20, retries=2)
    return int(raw, 16) / 10 ** TOKEN_DECIMALS


def _sample_tvl() -> None:
    v = tvl_usd()
    if v is None:
        return
    TVL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TVL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "tvl": v}) + "\n")


def tvl_history(max_points: int = 600) -> list[dict]:
    if not TVL_FILE.exists():
        return []
    rows = [json.loads(x) for x in TVL_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(rows) > max_points:  # thin out evenly, keep the newest point
        step = len(rows) / max_points
        rows = [rows[int(i * step)] for i in range(max_points)] + [rows[-1]]
    return rows


def _candles(mid: int, res: int, n: int) -> list[dict]:
    now = int(time.time() * 1000)
    return live._rest(f"/market-data/{mid}/candles/{res}/{now - (n + 1) * res * 1000}-{now}")["d"]


def refresh_volume(feed) -> None:
    """Fetch candles for every market (about 2 requests each) and rebuild the totals."""
    try:
        by_day: dict[int, dict] = {}
        per_market: list[dict] = []
        now = time.time()
        with feed.lock:
            mk = [(m["id"], m["name"]) for m in feed.markets.values()]
        for mid, name in mk:
            daily = _candles(mid, 86400, 365)
            time.sleep(0.15)
            hourly = _candles(mid, 3600, 24)
            time.sleep(0.15)
            v24 = sum(float(c["v"]) / 1e6 for c in hourly if c["t"] / 1000 >= now - 86400)
            n24 = sum(int(c.get("n") or 0) for c in hourly if c["t"] / 1000 >= now - 86400)
            vtot = 0.0
            for c in daily:
                day = int(c["t"] // 1000)
                row = by_day.setdefault(day, {"ts": day, "volumeUsd": 0.0, "trades": 0})
                row["volumeUsd"] += float(c["v"]) / 1e6
                row["trades"] += int(c.get("n") or 0)
                vtot += float(c["v"]) / 1e6
            per_market.append({"name": name, "volume_24h": v24, "trades_24h": n24, "volume_total": vtot})
        days = [by_day[k] for k in sorted(by_day)]
        cut = lambda d: sum(r["volumeUsd"] for r in days if r["ts"] >= now - d * 86400)
        total24 = sum(m["volume_24h"] for m in per_market)
        out = {"volume_24h": total24, "trades_24h": sum(m["trades_24h"] for m in per_market),
               "volume_7d": cut(7), "volume_30d": cut(30), "volume_all": sum(r["volumeUsd"] for r in days),
               "days_available": len(days), "first_day": days[0]["ts"] if days else None, "daily": days,
               "markets": sorted(per_market, key=lambda m: -m["volume_24h"]), "computed_at": int(now)}
        with _lock:
            _vol.update(state=out, error=None, at=now)
    except Exception as e:  # keep the last good numbers, report the problem
        with _lock:
            _vol.update(error=f"{type(e).__name__}: {e}", at=time.time())


def start(feed) -> None:
    """Background refresh of the volume totals and the TVL samples (one daemon thread)."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        last_vol = 0.0
        while True:
            try:
                if time.time() - last_vol > VOLUME_REFRESH_S and feed.markets:
                    refresh_volume(feed)
                    last_vol = time.time()
                _sample_tvl()
            except Exception:
                pass
            time.sleep(120)

    threading.Thread(target=loop, daemon=True, name="protocol-extra").start()


def volume() -> dict:
    with _lock:
        return {"data": _vol["state"], "error": _vol["error"]}


def active_and_liquidations(snap: dict) -> dict:
    """Distinct traders with a position change in the last 24 h and recent liquidations, from the history database."""
    cov = history_store.coverage()
    if cov is None:
        return {"available": False}
    names = {int(k): v["name"] for k, v in snap["perps"].items()}
    now = int(time.time())
    con = history_store.connect()
    try:
        win = cov["window_start_ts"] or now
        start24 = max(now - 86400, win)
        traders = con.execute("SELECT COUNT(DISTINCT account_id) FROM events WHERE ts >= ?", (start24,)).fetchone()[0]
        liq_n = con.execute("SELECT COUNT(*) FROM events WHERE kind='liquidated'").fetchone()[0]
        rows = con.execute("SELECT ts, perp_id, account_id, side, price, size_before, size_after FROM events WHERE kind='liquidated' ORDER BY ts DESC LIMIT 15").fetchall()
    finally:
        con.close()
    liqs = [{"ts": ts, "market": names.get(p, f"#{p}"), "account": a, "side": s, "price": pr,
             "size": (sb or 0) - (sa or 0), "usd": ((sb or 0) - (sa or 0)) * (pr or 0)} for ts, p, a, s, pr, sb, sa in rows]
    return {"available": True, "traders_window_hours": round((now - start24) / 3600, 1), "traders": traders,
            "window_start_ts": win, "liquidations_in_window": liq_n, "recent_liquidations": liqs}
