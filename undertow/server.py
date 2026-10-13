"""Local dashboard server (standard library only).

    python -m undertow.server                 # http://localhost:8000
    python -m undertow.server --refresh       # also keep data fresh in a background thread
    python -m undertow.server --port 8080

JSON API (all GET):
    /api/status                       data age, block, checks
    /api/markets                      one row per market
    /api/radar?perp=1&band=1          zones, headline sentence, order-book depth curve
    /api/cascade?perp=1&to=78000&share=1&feedback=1
    /api/wallet?q=<0x address | account id>
    /api/top                          largest accounts with positions
    /api/protocol                     exchange-wide totals and history since collection started
    /api/bot                          paper/live bot state and recent events
    /api/live?market=1&levels=12     live prices, book, trades and signals (Perpl WebSocket, updated every block)
    /api/candles?market=1&res=3600&n=120   OHLCV candles (Perpl REST, cached 15 s)
    /api/preview?perp=1&side=Long&usd=100&lev=5[&price=]   order preview (nothing is sent)
    /api/protect?perp=1&side=bid&min=25000&within=1         is a liquidation-aware rule triggered now
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import traceback
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import accounts as registry
from . import config, envio_client, history_store, live, preview, protect, protocol_extra, signals, verify, wallethistory
from . import risk

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
STATUS = config.DATA / "status.json"
HISTORY = config.DATA / "history" / "markets.jsonl"
BOT_STATE = config.BOT_DIR / "state.json"
BOT_LOG = config.BOT_DIR / "log.jsonl"

IN_PATH_MIN_USD = 5_000
_cache: dict = {"mtime": 0.0, "snap": None, "formula": None, "oi": None}
_lock = threading.Lock()


def snapshot() -> dict:
    """Latest parsed snapshot, reloaded only when the file changes."""
    m = risk.LATEST.stat().st_mtime
    with _lock:
        if m != _cache["mtime"]:
            snap = risk.load_latest()
            _cache.update(mtime=m, snap=snap, formula=risk.verify_formula(snap), oi=_oi_summary(snap))
        return _cache["snap"]


def _oi_summary(snap: dict) -> dict:
    """Open-interest check on the dict form: summed long and short size must equal Perpl's reported figure."""
    passed = total = 0
    for k, v in snap["perps"].items():
        rep = v["open_interest_size"]
        if rep is None:
            continue
        pos = risk.positions_for(snap, int(k))
        sides = [sum(p["size"] for p in pos if p["side"] == s) for s in ("Long", "Short")]
        total += 1
        passed += all(abs(x - rep) <= 0.01 * max(rep, 1e-12) + 1e-9 for x in sides)
    return {"passed": passed, "total": total}


def fmt_price(v: float) -> str:
    return f"{v:,.1f}" if v >= 1000 else f"{v:.2f}" if v >= 1 else f"{v:.4g}"


def _num(q: dict, key: str, default: float) -> float:
    try:
        return float(q.get(key, [default])[0])
    except ValueError:
        return default


def api_status() -> dict:
    snap = snapshot()
    st = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    f = _cache["formula"]
    age = time.time() - risk.LATEST.stat().st_mtime
    return {"block": snap["block"], "snapshot_time": snap["timestamp"], "age_seconds": round(age),
            "accounts": len({p["account_id"] for p in snap["positions"]}), "positions": len(snap["positions"]),
            "oi_checks": _cache["oi"], "formula_check": {"matched": f.within_tolerance, "total": f.total},
            "refresh_seconds": st.get("total_seconds"), "rpc": st.get("rpc")}


def api_markets() -> list[dict]:
    snap, rows = snapshot(), []
    for k, p in snap["perps"].items():
        pos = risk.positions_for(snap, int(k))
        if not pos and not p["open_interest_usd"]:
            continue
        h = risk.headline(snap, int(k))
        rows.append({"perp_id": int(k), "name": p["name"], "mark": p["mark"], "oracle": p["oracle"],
                     "funding_rate": p["funding_rate"], "oi_usd": p["open_interest_usd"],
                     "longs": sum(1 for x in pos if x["side"] == "Long"),
                     "shorts": sum(1 for x in pos if x["side"] == "Short"),
                     "max_leverage": p["initial_margin_x"], "risk": h["risk"], "sentence": h["sentence"]})
    return sorted(rows, key=lambda r: -(r["oi_usd"] or 0))


def _zone_dict(z: risk.Zone, mark: float) -> dict:
    return {"side": z.side, "low": z.low, "high": z.high, "notional": z.notional, "positions": z.positions,
            "accounts": len(z.accounts), "top_account_share": z.top_account_share,
            "distance_pct": (z.mid / mark - 1) * 100}


def _depth_curve(snap: dict, perp_id: int, bids: bool, span_pct: float = 30.0) -> list[list[float]]:
    mark = snap["perps"][str(perp_id)]["mark"]
    cum, out = 0.0, []
    for price, size in risk.book_levels(snap, perp_id, bids):
        if abs(price / mark - 1) * 100 > span_pct:
            break
        cum += price * size
        out.append([price, cum])
    return out


def api_radar(q: dict) -> dict:
    snap = snapshot()
    perp_id = int(_num(q, "perp", 1))
    perp = snap["perps"][str(perp_id)]
    mark = perp["mark"]
    zs = risk.zones(snap, perp_id, band_pct=_num(q, "band", 1.0))
    h = risk.headline(snap, perp_id)
    return {"perp_id": perp_id, "name": perp["name"], "mark": mark, "oracle": perp["oracle"], "last": perp["last"],
            "maintenance_x": perp["maintenance_margin_x"], "max_leverage": perp["initial_margin_x"],
            "zones": [_zone_dict(z, mark) for z in zs], "headline": h["sentence"], "risk": h["risk"], "headlines": risk.headlines(snap, perp_id),
            "bids": _depth_curve(snap, perp_id, True), "asks": _depth_curve(snap, perp_id, False)}


def api_cascade(q: dict) -> dict:
    snap = snapshot()
    perp_id = int(_num(q, "perp", 1))
    c = risk.simulate_cascade(snap, perp_id, _num(q, "to", snap["perps"][str(perp_id)]["mark"]),
                              book_share=min(1.0, max(0.0, _num(q, "share", 1.0))),
                              feedback=_num(q, "feedback", 1) != 0)
    d = asdict(c)
    d["move_pct"] = c.move_pct if c.rounds else 0.0
    if not c.rounds:
        d["sentence"] = "No liquidations are triggered at that price."
    else:
        word = "longs" if c.side == "Long" else "shorts"
        d["sentence"] = (f"A move to {fmt_price(c.trigger_price)} triggers ${c.triggered_notional:,.0f} of {word} "
                         f"({c.triggered_positions} positions). Forced orders could push the price to about "
                         f"{fmt_price(c.final_price)}" + (" or beyond: the visible order book runs out." if c.book_exhausted else "."))
        last = c.rounds[-1]
        act = "sells into the resting buy orders" if c.side == "Long" else "buys from the resting sell orders"
        side = "bids" if c.side == "Long" else "asks"
        d["sentence"] += (f" That forced flow {act}: it uses up ${last.book_taken_usd:,.0f} of the ${last.book_visible_usd:,.0f} visible on the {side} "
                          f"({last.book_levels} price levels)" + (f", and ${last.unfilled_usd:,.0f} of it finds no resting order at all." if last.unfilled_usd > 0 else "."))
    return d


def _account_id_for(q: str) -> int | None:
    q = q.strip()
    if q.isdigit():
        return int(q)
    if q.lower().startswith("0x") and len(q) == 42:
        a = registry.by_address(registry.load_cache(), q)
        return a.account_id if a else None
    return None


def api_wallet(q: dict) -> dict:
    snap = snapshot()
    query = q.get("q", [""])[0]
    aid = _account_id_for(query)
    if aid is None:
        return {"error": "Enter a wallet address (0x...) or an account number that currently has open positions."}
    acct = registry.load_cache().get(aid)
    rows = []
    for p in (x for x in snap["positions"] if x["account_id"] == aid):
        perp = snap["perps"][str(p["perp_id"])]
        mark, mx = perp["mark"], perp["maintenance_margin_x"]
        value = p["size"] * mark
        # Only zones worth worrying about: at least $5k of OTHER traders' positions (a heuristic floor),
        # biggest first. Otherwise a far-away liquidation would list every tiny zone on the way.
        ip = sorted((z for z in risk.in_path_zones(snap, p) if z["notional_others"] >= IN_PATH_MIN_USD),
                    key=lambda z: -z["notional_others"])
        rows.append({**p, "name": perp["name"], "mark": mark, "value": value,
                     "funding_per_day_usd": preview.funding_per_day_usd(value, perp["funding_rate"] or 0.0, p["side"]),
                     "leverage": value / p["deposit"] if p["deposit"] else None,
                     "liq_distance_pct": (p["liq_price"] / mark - 1) * 100,
                     "our_liq_price": risk.liquidation_price(p["entry_price"], p["size"], p["side"], p["deposit"],
                                                             p["premium_pnl"], mx),
                     "liq_at_5x": risk.liq_price_at_leverage(p["entry_price"], p["size"], p["side"], 5, mx),
                     "liq_at_3x": risk.liq_price_at_leverage(p["entry_price"], p["size"], p["side"], 3, mx),
                     "in_path": ip[:5], "in_path_count": len(ip), "in_path_total": sum(z["notional_others"] for z in ip)})
    # open (resting) orders of this account, from the snapshot
    orders = []
    for k, perp in snap["perps"].items():
        for o in perp.get("orders", []):
            if o["account_id"] == aid:
                orders.append({"name": perp["name"], "kind": o["kind"], "price": o["price"], "size": o["size"],
                               "value": o["price"] * o["size"], "leverage": o.get("leverage"),
                               "from_mark_pct": (o["price"] / perp["mark"] - 1) * 100})
    orders.sort(key=lambda o: -o["value"])
    # history and statistics from the indexer, if it is running
    history = None
    raw = history_store.wallet_events(aid)
    ev = None
    if raw is not None:
        names = {int(k): v["name"] for k, v in snap["perps"].items()}
        ev = {"window_start_ts": raw["window_start_ts"], "indexed_through_ts": raw["indexed_through_ts"], "events": [
            {"id": r[0], "block": r[1], "timestamp": r[3], "kind": r[4], "perpName": names.get(r[5], f"#{r[5]}"), "side": r[6], "price": r[7],
             "sizeBefore": r[8], "sizeAfter": r[9], "leverage": r[10], "realizedPnlUsd": r[11], "fundingPaidUsd": r[12]} for r in raw["rows"]]}
    if ev is not None:
        events = ev["events"]
        opened = wallethistory.opened_at(events)
        for r in rows:
            r["opened_ts"] = (opened.get(r["name"]) or {}).get("opened_ts")
        st = wallethistory.stats(events)
        st["profit_factor_infinite"] = st["profit_factor"] == float("inf")
        if st["profit_factor_infinite"]:
            st["profit_factor"] = None
        trips = wallethistory.trips(events)
        history = {"window_start_ts": ev["window_start_ts"], "indexed_through_ts": ev["indexed_through_ts"],
                   "stats": st, "insights": wallethistory.insights(events), "events": len(events), "recent": events[-25:][::-1],
                   "trips": [{"market": t.market, "side": t.side, "opened_ts": t.opened_ts, "closed_ts": t.closed_ts,
                              "realized_net": t.realized_net, "ended_by": t.ended_by, "opened_in_window": t.opened_in_window,
                              "hold_seconds": t.hold_seconds} for t in trips[-15:][::-1]]}
    if not rows and not acct:
        return {"error": f"Account {aid} not found."}
    gross = sum(r["value"] for r in rows)
    longv = sum(r["value"] for r in rows if r["side"] == "Long")
    margin = sum(r["deposit"] for r in rows)
    portfolio = {"free_balance": acct.balance if acct else None, "locked_in_orders": acct.locked if acct else None,
                 "margin_in_positions": margin, "position_value": gross, "long_value": longv, "short_value": gross - longv,
                 "net_exposure": longv - (gross - longv), "unrealised_pnl": sum(r["total_pnl"] for r in rows),
                 "avg_leverage": gross / margin if margin else None,
                 "nearest_liq_distance_pct": min((abs(r["liq_distance_pct"]) for r in rows), default=None),
                 "orders_value": sum(o["value"] for o in orders),
                 "by_market": [{"name": r["name"], "side": r["side"], "value": r["value"], "share": r["value"] / gross if gross else 0,
                                "margin": r["deposit"], "pnl": r["total_pnl"], "leverage": r["leverage"], "liq_distance_pct": r["liq_distance_pct"]}
                               for r in sorted(rows, key=lambda r: -r["value"])]}
    return {"account_id": aid, "orders": orders, "history": history, "activity": envio_client.wallet_activity(aid), "address": acct.address if acct else None,
            "balance": acct.balance if acct else None, "locked": acct.locked if acct else None,
            "positions": sorted(rows, key=lambda r: -r["value"]),
            "total_value": sum(r["value"] for r in rows), "total_pnl": sum(r["total_pnl"] for r in rows), "portfolio": portfolio}


_protocol_cache: dict = {"t": 0.0, "out": None}
_envio_cache: dict = {"t": 0.0, "out": None}


def _envio_stats():
    with _lock:
        if time.time() - _envio_cache["t"] < 60:
            return _envio_cache["out"]
    out = envio_client.stats()
    with _lock:
        _envio_cache.update(t=time.time(), out=out)
    return out



def api_protocol() -> dict:
    """Cached for 10 s: it reads the history database, the Envio indexer and several files."""
    with _lock:
        if _protocol_cache["out"] is not None and time.time() - _protocol_cache["t"] < 10:
            return _protocol_cache["out"]
    out = _api_protocol_uncached()
    with _lock:
        _protocol_cache.update(t=time.time(), out=out)
    return out


def _api_protocol_uncached() -> dict:
    """Exchange-wide view from the live snapshot plus the history we have collected since the refresher started."""
    snap = snapshot()
    reg = registry.load_cache()
    ids = {p["account_id"] for p in snap["positions"]}
    markets = []
    for k, perp in snap["perps"].items():
        pos = risk.positions_for(snap, int(k))
        if not pos:
            continue
        longs = [p for p in pos if p["side"] == "Long"]
        shorts = [p for p in pos if p["side"] == "Short"]
        markets.append({"name": perp["name"], "oi_usd": perp["open_interest_usd"], "funding_rate": perp["funding_rate"],
                        "longs": len(longs), "shorts": len(shorts),
                        "long_collateral": sum(p["deposit"] for p in longs),
                        "short_collateral": sum(p["deposit"] for p in shorts)})
    markets.sort(key=lambda m: -(m["oi_usd"] or 0))
    total_oi = sum(m["oi_usd"] or 0 for m in markets)
    series: dict[int, dict] = {}
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            row = series.setdefault(r["block"], {"block": r["block"], "ts": r["ts"], "oi_usd": 0.0, "positions": 0})
            row["oi_usd"] += r["oi_usd"] or 0
            row["positions"] += r["n_long"] + r["n_short"]
    pts = [series[b] for b in sorted(series)]
    ev = _envio_stats()  # None until the Envio indexer is running and has data (cached for a minute)
    try:
        tvl = protocol_extra.tvl_usd()
    except Exception:
        tvl = None
    return {"total_oi_usd": total_oi, "accounts_with_positions": len(ids), "positions": len(snap["positions"]),
            "balances_of_those_accounts": sum(reg[i].balance for i in ids if i in reg),
            "markets": markets, "history": pts,
            "history_started": pts[0]["ts"] if pts else None, "envio": ev, "tvl_usd": tvl, "tvl_history": protocol_extra.tvl_history(),
            "volume": protocol_extra.volume(), "activity": protocol_extra.active_and_liquidations(snap), "flows": protocol_extra.flows_summary(),
            "fees_est": protocol_extra.fee_estimate(protocol_extra.volume(), snap), "compare": protocol_extra.compare(protocol_extra.volume(), snap),
            "not_available": ["protocol revenue (how fees are split with the exchange is not data we can read)"]}


def api_bot() -> dict:
    if not BOT_STATE.exists():
        return {"running": False, "message": "No bot run yet. Start one: python -m undertow.bot --paper"}
    st = json.loads(BOT_STATE.read_text(encoding="utf-8"))
    events = []
    if BOT_LOG.exists():
        for line in BOT_LOG.read_text(encoding="utf-8").splitlines()[-400:]:
            e = json.loads(line)
            if e.get("event") in ("fill", "decision", "error", "live_order"):
                events.append(e)
    st["events"] = events[-15:][::-1]
    st["age_seconds"] = round(time.time() - BOT_STATE.stat().st_mtime)
    return st


def api_preview(q: dict) -> dict:
    snap = snapshot()
    perp_id = int(_num(q, "perp", config.DEFAULT_PERP))
    price = _num(q, "price", 0.0)
    try:
        r = preview.preview(snap, perp_id, "Short" if q.get("side", ["Long"])[0].lower().startswith("s") else "Long",
                            _num(q, "usd", 100.0), _num(q, "lev", 5.0), limit_price=price or None)
    except ValueError as e:
        return {"error": str(e)}
    return r


def api_protect(q: dict) -> dict:
    snap = snapshot()
    rule = protect.Rule(int(_num(q, "perp", config.DEFAULT_PERP)), "ask" if q.get("side", ["bid"])[0] == "ask" else "bid",
                        zone_min_usd=_num(q, "min", 25_000.0), within_pct=_num(q, "within", 1.0))
    return {"rule": asdict(rule), **protect.evaluate(snap, rule),
            "limits": ["Runs inside Undertow, not on Perpl: if this program stops, the rule stops.",
                       "Checked once per data refresh (about every 45 seconds), not every 0.4 s block."]}


LIVE = live.LiveFeed()


def api_live_markets() -> dict:
    return {"markets": LIVE.market_list(), "status": LIVE.status()}


def api_live(q: dict) -> dict:
    """Everything the Live tab shows, from one consistent copy of the live state plus the zone data."""
    mid = int(_num(q, "market", 1))
    v = LIVE.view(mid, levels=int(_num(q, "levels", 12)), trades=int(_num(q, "trades", 40)))
    status = LIVE.status()
    if v is None:
        return {"status": status, "error": "No live data yet. The first ticks arrive within a few seconds of the server starting."}
    snap, snap_age = None, None
    try:
        snap = snapshot()
        snap_age = time.time() - risk.LATEST.stat().st_mtime
    except FileNotFoundError:
        pass
    sig = signals.compute(v, snap, snap_age)
    keep = ("market_id", "perp_id", "name", "book_live", "oracle", "mark", "last", "mid", "best_bid", "best_ask", "price_24h_ago",
            "open_interest", "volume_24h_usd", "state_block", "state_ms", "bids", "asks", "book_block", "book_age_ms", "trades", "history")
    return {"status": status, "server_ms": int(time.time() * 1000), "market": {k: v[k] for k in keep}, "signals": sig}


_candle_cache: dict = {}
CANDLE_RES = (60, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400)


def api_candles(q: dict) -> dict:
    """Candles from Perpl's public REST API (cached 15 s). Prices and volume are already scaled; volume is in USD."""
    mid, res, n = int(_num(q, "market", 1)), int(_num(q, "res", 3600)), min(int(_num(q, "n", 120)), 1000)  # Perpl serves at most 1,024 candles per request
    if res not in CANDLE_RES:
        raise ValueError(f"res must be one of {CANDLE_RES}")
    key = (mid, res, n)
    hit = _candle_cache.get(key)
    if hit and time.time() - hit[0] < 15:
        return hit[1]
    mk = LIVE.markets.get(mid)
    if mk is None:
        raise ValueError("unknown market (live feed not ready yet)")
    now = int(time.time() * 1000)
    raw = live._rest(f"/market-data/{mid}/candles/{res}/{now - (n + 1) * res * 1000}-{now}")["d"]
    pd = 10 ** mk["pdec"]
    f = float
    out = {"market": mid, "res": res, "candles": [{"t": int(c["t"]), "o": f(c["o"]) / pd, "h": f(c["h"]) / pd, "l": f(c["l"]) / pd, "c": f(c["c"]) / pd,
                                                    "v": f(c["v"]) / 1e6, "n": c.get("n")} for c in raw][-n:]}
    _candle_cache[key] = (time.time(), out)
    return out


_verify_cache: dict = {"t": 0.0, "out": None}


def api_verify() -> dict:
    """Run the independent correctness checks against Perpl's public API (10 to 40 s). Cached for 60 s."""
    with _lock:
        if _verify_cache["out"] and time.time() - _verify_cache["t"] < 60:
            return _verify_cache["out"]
    checks = verify.run_all(live_feed=LIVE)
    out = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "checks": [asdict(c) for c in checks]}
    with _lock:
        _verify_cache.update(t=time.time(), out=out)
    return out


def api_top() -> list[dict]:
    snap = snapshot()
    reg = registry.load_cache()
    agg: dict[int, dict] = {}
    for p in snap["positions"]:
        mark = snap["perps"][str(p["perp_id"])]["mark"]
        a = agg.setdefault(p["account_id"], {"account_id": p["account_id"], "value": 0.0, "positions": 0})
        a["value"] += p["size"] * mark
        a["positions"] += 1
    for a in agg.values():
        r = reg.get(a["account_id"])
        a["address"], a["balance"] = (r.address, r.balance) if r else (None, None)
    return sorted(agg.values(), key=lambda a: -a["value"])[:15]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            routes = {"/api/status": lambda: api_status(), "/api/markets": lambda: api_markets(),
                      "/api/radar": lambda: api_radar(q), "/api/cascade": lambda: api_cascade(q),
                      "/api/wallet": lambda: api_wallet(q), "/api/top": lambda: api_top(),
                      "/api/protocol": lambda: api_protocol(), "/api/bot": lambda: api_bot(),
                      "/api/verify": lambda: api_verify(), "/api/live": lambda: api_live(q), "/api/live/markets": lambda: api_live_markets(), "/api/candles": lambda: api_candles(q), "/api/preview": lambda: api_preview(q), "/api/protect": lambda: api_protect(q)}
            if u.path in routes:
                return self._send(200, json.dumps(routes[u.path](), default=lambda o: asdict(o) if is_dataclass(o)
                                                  else str(o)).encode(), "application/json")
            self._send(404, b"not found", "text/plain")
        except FileNotFoundError:
            self._send(503, json.dumps({"error": "No data yet. Run: python -m undertow.refresher --once"}).encode(),
                       "application/json")
        except Exception as e:  # return the error to the page instead of a blank screen
            traceback.print_exc()
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")


def main() -> None:
    ap = argparse.ArgumentParser(prog="undertow.server")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))  # hosts such as Koyeb and Render set PORT
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--refresh", action="store_true", help="refresh data in a background thread")
    ap.add_argument("--rpc")
    a = ap.parse_args()
    if a.refresh:
        from .refresher import run_cycle

        def loop():
            while True:
                try:
                    s = run_cycle(a.rpc, None, False)
                    print(f"[refresh] block {s['block']} in {s['total_seconds']}s", flush=True)
                except Exception:
                    traceback.print_exc()
                    time.sleep(15)  # back off after an error (connection dropped, rate limit)
                time.sleep(2)
        threading.Thread(target=loop, daemon=True).start()
    follower = history_store.Follower(days=float(os.environ.get("UNDERTOW_HISTORY_DAYS", "1")),
                                      perp_ids_fn=lambda: [int(k) for k in snapshot()["perps"]])
    follower.start()  # keeps wallet history current; backfills on first start
    if LIVE.start():
        print("Live feed: connecting to Perpl's WebSocket", flush=True)
        protocol_extra.start(LIVE)  # volume totals from Perpl's candles, TVL samples
    else:
        print(f"Live feed disabled: {LIVE.last_error}", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Undertow dashboard: http://{a.host}:{a.port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
