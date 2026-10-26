"""Live market data straight from Perpl's public WebSocket (pushed every block, about every 0.4 s).

    wss://app.perpl.xyz/ws/v1/market-data   (testnet: wss://testnet.perpl.xyz/ws/v1/market-data), no authentication

What it keeps up to date, per market: oracle / mark / last / bid / ask prices, open interest and 24h volume, a local copy of the
L2 order book (a snapshot, then per-level changes; a level with 0 orders is removed), and the recent trade tape.

This is Perpl's own feed, so prices, book and trades are as accurate as Perpl's screen. Liquidation ZONES are not part of the feed:
they come from the positions snapshot (`risk.py`), which is up to about a minute old. `signals.py` combines the two.

Stream limits from Perpl's docs: about 5 connections per IP, 16 subscriptions per connection, 10 subscription requests per minute.
We use one connection and one subscription batch (heartbeat, market-state, and order-book + trades for the 7 busiest markets).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from collections import deque

from . import activity, config

try:  # the dashboard works without it; only the Live tab needs it
    import websockets
except ImportError:  # pragma: no cover
    websockets = None

REST = "https://testnet.perpl.xyz/api/v1" if config.TESTNET else "https://app.perpl.xyz/api/v1"
WS_URL = ("wss://testnet.perpl.xyz" if config.TESTNET else "wss://app.perpl.xyz") + "/ws/v1/market-data"
MAX_BOOK_MARKETS = 7  # 2 subscriptions each + 2 shared = 16, the per-connection cap
HISTORY_SECONDS = 900
TRADES_KEPT = 300


def _rest(path: str):
    req = urllib.request.Request(REST + path, headers={"User-Agent": "undertow-live"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


class LiveFeed:
    def __init__(self, max_book_markets: int = MAX_BOOK_MARKETS):
        self.max_book_markets = max_book_markets
        self.lock = threading.Lock()
        self.markets: dict[int, dict] = {}  # market id -> metadata and live state
        self.sid_stream: dict[int, str] = {}
        self.connected = False
        self.head_block = 0
        self.last_msg = 0.0
        self.msg_times: deque[float] = deque(maxlen=400)
        self.reconnects = 0
        self.last_error = ""
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        if websockets is None:
            self.last_error = "the 'websockets' package is not installed (pip install websockets)"
            return False
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True, name="live-feed")
        self._thread.start()
        return True

    def _load_markets(self) -> None:
        ctx = _rest("/pub/context")
        ticker = _rest("/market-data/ticker")["d"]
        by_volume = sorted(ticker, key=lambda k: -int(ticker[k].get("dva") or 0))
        want = by_volume[: self.max_book_markets]
        with self.lock:
            for m in ctx["markets"]:
                mid = m["id"]
                self.markets[mid] = {
                    "id": mid, "perp_id": m["perpetual_id"], "name": m["name"], "pdec": m["config"]["price_decimals"],
                    "sdec": m["config"]["size_decimals"], "book_live": str(mid) in want, "state": {}, "bids": {}, "asks": {},
                    "trades": deque(maxlen=TRADES_KEPT), "seen": set(), "history": deque(), "book_block": 0, "book_at": 0.0,
                    "book_hist": deque(maxlen=64),
                }
                self.markets[mid]["state"].update(ticker.get(str(mid), {}))

    async def _main(self) -> None:
        backoff = 1.0
        while True:
            while not activity.active():  # nobody on the site: stay disconnected
                await asyncio.sleep(2)
            try:
                if not self.markets:
                    await asyncio.get_running_loop().run_in_executor(None, self._load_markets)
                await self._session()
                backoff = 1.0
            except Exception as e:  # network drop, rate limit, server restart: reconnect with backoff
                self.last_error = f"{type(e).__name__}: {e}"
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _session(self) -> None:
        chain = config.CHAIN_ID
        subs = [{"stream": f"heartbeat@{chain}", "subscribe": True}, {"stream": f"market-state@{chain}", "subscribe": True}]
        with self.lock:
            for mid, m in self.markets.items():
                if m["book_live"]:
                    subs += [{"stream": f"order-book@{mid}", "subscribe": True}, {"stream": f"trades@{mid}", "subscribe": True}]
                    m["bids"].clear(); m["asks"].clear(); m["trades"].clear(); m["seen"].clear()
        async with websockets.connect(WS_URL, open_timeout=20, ping_interval=20, ping_timeout=30,
                                      additional_headers={"User-Agent": "undertow-live"}) as ws:
            await ws.send(json.dumps({"mt": 5, "subs": subs[:16]}))
            self.connected = True
            self.last_error = ""
            async for raw in ws:
                self._on_message(json.loads(raw))
                if not activity.active():
                    return  # close the stream: it is most of the bandwidth

    # ------------------------------------------------------------ message handling
    def _on_message(self, m: dict) -> None:
        now = time.time()
        self.last_msg = now
        self.msg_times.append(now)
        mt = m.get("mt")
        with self.lock:
            if mt == 100:
                self.head_block = m.get("h", self.head_block)
            elif mt == 6:
                for s in m.get("subs", []):
                    if s.get("status", {}).get("code") == 0:
                        self.sid_stream[s["sid"]] = s["stream"]
                    else:
                        self.last_error = f"subscription {s.get('stream')} rejected: {s.get('status')}"
            elif mt == 9:
                for k, st in (m.get("d") or {}).items():
                    mk = self.markets.get(int(k))
                    if mk and st:
                        mk["state"].update(st)
                        mark = st.get("mrk")
                        if mark is not None:
                            h = mk["history"]
                            h.append((now, mark / 10 ** mk["pdec"], (st.get("orl") or mk["state"].get("orl", 0)) / 10 ** mk["pdec"]))
                            while h and now - h[0][0] > HISTORY_SECONDS:
                                h.popleft()
            elif mt in (15, 16, 17, 18):
                stream = self.sid_stream.get(m.get("sid"), "")
                try:
                    mk = self.markets.get(int(stream.split("@")[1]))
                except (IndexError, ValueError):
                    mk = None
                if not mk:
                    return
                if mt in (15, 16):
                    if mt == 15:
                        mk["bids"].clear(); mk["asks"].clear()
                    for side, key in (("bid", "bids"), ("ask", "asks")):
                        for lv in m.get(side, []):
                            if lv["s"] == 0 or lv["o"] == 0:
                                mk[key].pop(lv["p"], None)
                            else:
                                mk[key][lv["p"]] = (lv["s"], lv["o"])
                    mk["book_block"], mk["book_at"] = (m.get("at") or {}).get("b", 0), now
                    top = (sorted(mk["bids"].items(), reverse=True)[:10], sorted(mk["asks"].items())[:10])
                    mk["book_hist"].append((mk["book_block"], top))
                else:
                    for t in m.get("d", []):
                        key = (t["at"].get("txid"), t["at"].get("l"))
                        if key in mk["seen"]:
                            continue
                        mk["seen"].add(key)
                        mk["trades"].append(t)
                    if len(mk["seen"]) > TRADES_KEPT * 3:
                        mk["seen"] = {(t["at"].get("txid"), t["at"].get("l")) for t in mk["trades"]}

    # ------------------------------------------------------------ read side
    def msgs_per_second(self) -> float:
        now = time.time()
        return sum(1 for t in self.msg_times if now - t <= 5) / 5

    def market_list(self) -> list[dict]:
        with self.lock:
            return [{"id": m["id"], "perp_id": m["perp_id"], "name": m["name"], "live_book": m["book_live"]}
                    for m in sorted(self.markets.values(), key=lambda x: -int(x["state"].get("dva") or 0))]

    def view(self, market_id: int, levels: int = 12, trades: int = 40) -> dict | None:
        """A consistent copy of one market's live state, scaled to real prices and sizes."""
        with self.lock:
            m = self.markets.get(market_id)
            if not m:
                return None
            pd, sd, st = 10 ** m["pdec"], 10 ** m["sdec"], m["state"]
            bids = sorted(m["bids"].items(), reverse=True)
            asks = sorted(m["asks"].items())

            def ladder(rows):
                cum, out = 0.0, []
                for p, (s, o) in rows[:levels]:
                    cum += s / sd
                    out.append({"price": p / pd, "size": s / sd, "orders": o, "cum": cum, "notional": p / pd * s / sd})
                return out

            full_bids = [(p / pd, s / sd) for p, (s, o) in bids]
            full_asks = [(p / pd, s / sd) for p, (s, o) in asks]
            hist = list(m["history"])
            now = time.time()
            return {
                "market_id": m["id"], "perp_id": m["perp_id"], "name": m["name"], "book_live": m["book_live"],
                "oracle": (st.get("orl") or 0) / pd, "mark": (st.get("mrk") or 0) / pd, "last": (st.get("lst") or 0) / pd,
                "mid": (st.get("mid") or 0) / pd, "best_bid": (st.get("bid") or 0) / pd, "best_ask": (st.get("ask") or 0) / pd,
                "price_24h_ago": (st.get("prv") or 0) / pd, "open_interest": (st.get("oi") or 0) / sd,
                "volume_24h_usd": int(st.get("dva") or 0) / 1e6, "state_block": (st.get("at") or {}).get("b", 0),
                "state_ms": (st.get("at") or {}).get("t", 0),
                "bids": ladder(bids), "asks": ladder(asks), "full_bids": full_bids[:200], "full_asks": full_asks[:200],
                "book_block": m["book_block"], "book_age_ms": int((now - m["book_at"]) * 1000) if m["book_at"] else None,
                "trades": [{"t": t["at"]["t"], "price": t["p"] / pd, "size": t["s"] / sd, "side": "buy" if t["sd"] == 1 else "sell",
                            "block": t["at"].get("b")} for t in list(m["trades"])[-trades:]][::-1],
                "all_trades": [{"t": t["at"]["t"], "notional": t["p"] / pd * t["s"] / sd, "side": "buy" if t["sd"] == 1 else "sell"}
                               for t in m["trades"]],
                "history": [(round(ts, 2), mk, orc) for ts, mk, orc in hist[:: max(1, len(hist) // 400)]],
            }

    def book_at_block(self, market_id: int, block: int):
        """Top 10 levels per side as of the book message for `block` (for accuracy checks against the chain)."""
        with self.lock:
            m = self.markets.get(market_id)
            if not m:
                return None
            for b, top in reversed(m["book_hist"]):
                if b == block:
                    pd, sd = 10 ** m["pdec"], 10 ** m["sdec"]
                    return ([(p / pd, s / sd, o) for p, (s, o) in top[0]], [(p / pd, s / sd, o) for p, (s, o) in top[1]])
        return None

    def status(self) -> dict:
        age = time.time() - self.last_msg if self.last_msg else None
        return {"connected": self.connected and age is not None and age < 10, "msgs_per_second": round(self.msgs_per_second(), 1),
                "head_block": self.head_block, "last_message_age_ms": int(age * 1000) if age is not None else None,
                "reconnects": self.reconnects, "last_error": self.last_error, "url": WS_URL}
