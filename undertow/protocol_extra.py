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

from . import activity, config, history_store, live

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


def _market_candles(mid: int, name: str, now: float):
    """Daily and hourly candles for one market; None if this market could not be read (the others still count)."""
    try:
        return name, _candles(mid, 86400, 365), _candles(mid, 3600, 24)
    except Exception:
        return None


def refresh_volume(feed) -> None:
    """Fetch candles for every market in parallel (2 requests each) and rebuild the totals."""
    from concurrent.futures import ThreadPoolExecutor
    try:
        by_day: dict[int, dict] = {}
        per_market: list[dict] = []
        now = time.time()
        with feed.lock:
            mk = [(m["id"], m["name"]) for m in feed.markets.values()]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = [r for r in pool.map(lambda m: _market_candles(m[0], m[1], now), mk) if r]
        if not results:
            raise RuntimeError("Perpl's candle API did not answer")
        for name, daily, hourly in results:
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
               "markets": sorted(per_market, key=lambda m: -m["volume_24h"]), "computed_at": int(now),
               "markets_read": len(results), "markets_total": len(mk)}
        with _lock:
            _vol.update(state=out, error=None, at=now)
    except Exception as e:  # keep the last good numbers, report the problem
        with _lock:
            _vol.update(error=f"{type(e).__name__}: {e}", at=time.time())


def start(feed) -> None:
    """Background work, each piece in its own thread so none of them waits for another:
    volume totals, TVL samples and the exchange comparison, and the deposit/withdrawal backfill."""
    global _started
    if _started:
        return
    _started = True

    def numbers_loop():
        last_vol = last_tvl = 0.0
        while True:
            activity.wait()
            try:
                if feed.markets and (_vol["state"] is None or time.time() - last_vol > VOLUME_REFRESH_S):
                    refresh_volume(feed)
                    last_vol = time.time()
                if time.time() - last_tvl > 120:
                    _sample_tvl()
                    last_tvl = time.time()
                if time.time() - _cmp["at"] > 600:
                    refresh_compare()
            except Exception:
                pass
            time.sleep(4 if _vol["state"] is None else 20)  # check often until the first numbers exist

    def flows_loop():
        while True:
            activity.wait()
            try:
                history_store.flows_catch_up()
            except Exception:
                pass
            time.sleep(20)

    threading.Thread(target=numbers_loop, daemon=True, name="protocol-extra").start()
    threading.Thread(target=flows_loop, daemon=True, name="flows").start()


def volume() -> dict:
    with _lock:
        return {"data": _vol["state"], "error": _vol["error"]}


_act_cache: dict = {"t": 0.0, "out": None}


def active_and_liquidations(snap: dict) -> dict:
    """Cached for 60 s: counting distinct traders over a day of events is the slowest part of the Protocol screen."""
    with _lock:
        if _act_cache["out"] is not None and time.time() - _act_cache["t"] < 60:
            return _act_cache["out"]
    out = _active_and_liquidations(snap)
    with _lock:
        _act_cache.update(t=time.time(), out=out)
    return out


def _active_and_liquidations(snap: dict) -> dict:
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


# ------------------------------------------------------------------ deposits and withdrawals (from the chain, via the history loader)
def flows_summary() -> dict:
    """Deposits and withdrawals for the loaded window, with an hourly series for a chart."""
    if not history_store.DB_FILE.exists():
        return {"available": False}
    con = history_store.connect()
    try:
        last_block = history_store._meta(con, "flows_last_block")
        first_block = history_store._meta(con, "first_block")
        events_last = history_store._meta(con, "last_block") or 0
        if last_block is None or first_block is None:
            return {"available": False, "loading": True}
        now = int(time.time())
        first_ts = history_store._meta(con, "first_ts") or now
        last_ts = history_store._meta(con, "last_ts") or now
        rows = con.execute("SELECT ts, kind, amount FROM flows ORDER BY ts").fetchall()
    finally:
        con.close()
    s24 = max(now - 86400, first_ts)
    dep24 = sum(a for t, k, a in rows if k == "deposit" and t >= s24)
    wd24 = sum(a for t, k, a in rows if k == "withdrawal" and t >= s24)
    dep = sum(a for t, k, a in rows if k == "deposit")
    wd = sum(a for t, k, a in rows if k == "withdrawal")
    buckets: dict[int, dict] = {}
    for t, k, a in rows:
        b = buckets.setdefault(t // 3600 * 3600, {"ts": t // 3600 * 3600, "deposits": 0.0, "withdrawals": 0.0})
        b["deposits" if k == "deposit" else "withdrawals"] += a
    series = [dict(buckets[k], net=buckets[k]["deposits"] - buckets[k]["withdrawals"]) for k in sorted(buckets)]
    return {"available": True, "window_start_ts": first_ts, "window_hours": round((last_ts - first_ts) / 3600, 1), "hours_24": round((now - s24) / 3600, 1),
            "deposits_24h": dep24, "withdrawals_24h": wd24, "net_24h": dep24 - wd24, "deposits": dep, "withdrawals": wd, "net": dep - wd,
            "count": len(rows), "series": series,
            "complete": bool(last_block is not None and events_last - last_block < 60000), "loaded_pct": round(100 * (last_block - first_block) / max(1, events_last - first_block))}


# ------------------------------------------------------------------ fee estimate
def fee_estimate(vol: dict | None, snap: dict) -> dict | None:
    """Fees traders pay, estimated as volume x (taker + maker rate) per market. Fee tiers, rebates and builder fees are ignored."""
    if not vol or not vol.get("data"):
        return None
    v = vol["data"]
    rate = {p["name"]: (p.get("taker_fee") or 0) + (p.get("maker_fee") or 0) for p in snap["perps"].values()}
    est24 = sum(m["volume_24h"] * rate.get(m["name"], 0) for m in v["markets"])
    blended = est24 / v["volume_24h"] if v["volume_24h"] else 0.0
    return {"fees_24h": est24, "fees_7d": v["volume_7d"] * blended, "fees_30d": v["volume_30d"] * blended, "fees_all": v["volume_all"] * blended, "blended_rate": blended}


# ------------------------------------------------------------------ comparison with other perpetual exchanges (public APIs; fails soft)
_cmp: dict = {"data": None, "error": None, "at": 0.0}
COMPARE_COINS = ("BTC", "ETH", "SOL")


def _http_json(url: str, body: dict | None = None, timeout: int = 15):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json", "User-Agent": "undertow"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def parse_hyperliquid(payload) -> dict:
    meta, ctxs = payload[0]["universe"], payload[1]
    out = {}
    for m, c in zip(meta, ctxs):
        if m["name"] in COMPARE_COINS:
            out[m["name"]] = {"volume_24h": float(c["dayNtlVlm"]), "oi_usd": float(c["openInterest"]) * float(c["markPx"])}
    return out


def parse_dydx(payload) -> dict:
    out = {}
    for ticker, c in payload["markets"].items():
        coin = ticker.split("-")[0]
        if coin in COMPARE_COINS and ticker.endswith("-USD"):
            out[coin] = {"volume_24h": float(c["volume24H"]), "oi_usd": float(c["openInterest"]) * float(c["oraclePrice"])}
    return out


def refresh_compare() -> None:
    res, errs = {}, []
    for name, fn in (("Hyperliquid", lambda: parse_hyperliquid(_http_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"}))),
                     ("dYdX", lambda: parse_dydx(_http_json("https://indexer.dydx.trade/v4/perpetualMarkets")))):
        try:
            res[name] = fn()
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}")
    with _lock:
        _cmp.update(data=res or None, error=("; ".join(errs) or None), at=time.time())


def compare(vol: dict | None, snap: dict) -> dict:
    with _lock:
        c = dict(_cmp)
    perpl = {}
    if vol and vol.get("data"):
        oi = {p["name"]: p["open_interest_usd"] for p in snap["perps"].values()}
        for m in vol["data"]["markets"]:
            if m["name"] in COMPARE_COINS:
                perpl[m["name"]] = {"volume_24h": m["volume_24h"], "oi_usd": oi.get(m["name"]) or None}
    return {"perpl": perpl, "others": c["data"], "error": c["error"], "at": c["at"]}
