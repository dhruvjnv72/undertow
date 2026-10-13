"""Wallet position history, read straight from the chain into a local SQLite file (standard library plus eth-abi).

    python -m undertow.history_loader --days 3     # backfill the last 3 days (about 2 minutes per day on a good RPC)
    (the dashboard also follows new blocks in the background and backfills 1 day by itself on first start)

Why this exists: Envio's indexer needs a block header per log batch, which the public RPCs rate-limit, so it could not backfill. Here we ask the RPC
for just the seven position event types in big block ranges (the response carries `blockTimestamp`), decode them with the contract ABI, and
store one row per position change. The Envio indexer in indexer/ records the same events (PositionEvent) and is the intended path when a
HyperSync token or your own RPC is available.

Events recorded (one row each): PositionOpenedV2 -> opened, PositionIncreasedV2 -> increased, PositionDecreased -> decreased,
PositionClosed -> closed, PositionInverted -> flipped, PositionLiquidated -> liquidated, PositionDeleveragedV2 -> deleveraged.
Realized profit = the event's deltaPnl; funding paid = the event's funding (positive = paid, per the SDK's handling of PositionLiquidated).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_utils import keccak

from . import config

ABI_FILE = Path(__file__).resolve().parent.parent / "abi" / "Exchange.json"
DB_FILE = config.DATA / "history" / "positions.sqlite"
BLOCKS_PER_DAY = 216_000  # 0.4 s blocks
CNS = 1e6

# contract event -> (kind, name of the account field)
EVENTS = {
    "PositionOpenedV2": ("opened", "accountId"), "PositionIncreasedV2": ("increased", "accountId"),
    "PositionDecreased": ("decreased", "accountId"), "PositionClosed": ("closed", "accountId"),
    "PositionInverted": ("flipped", "accountId"), "PositionLiquidated": ("liquidated", "posAccountId"),
    "PositionDeleveragedV2": ("deleveraged", "accountId"),
}

_abi = json.loads(ABI_FILE.read_text(encoding="utf-8"))
_abi = _abi["abi"] if isinstance(_abi, dict) else _abi
_events = {x["name"]: x for x in _abi if x.get("type") == "event"}
TOPIC = {n: "0x" + keccak(text=f"{n}({','.join(i['type'] for i in _events[n]['inputs'])})").hex() for n in EVENTS}
BY_TOPIC = {t: n for n, t in TOPIC.items()}
TYPES = {n: [i["type"] for i in _events[n]["inputs"]] for n in EVENTS}
FIELDS = {n: [i["name"] for i in _events[n]["inputs"]] for n in EVENTS}
# deposits and withdrawals (kept in their own table so wallet history logic never sees them)
FLOW_EVENTS = {"CollateralDeposit": "deposit", "CollateralWithdrawal": "withdrawal"}
FLOW_TOPIC = {n: "0x" + keccak(text=f"{n}({','.join(i['type'] for i in _events[n]['inputs'])})").hex() for n in FLOW_EVENTS}
FLOW_BY_TOPIC = {t: n for n, t in FLOW_TOPIC.items()}
_FIND_GETLOGS_SPAN = {"span": 1000}  # shrinks to 100 for endpoints that refuse more


def connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS events(
      id TEXT PRIMARY KEY, block INTEGER NOT NULL, log_index INTEGER NOT NULL, ts INTEGER NOT NULL, kind TEXT NOT NULL,
      perp_id INTEGER NOT NULL, account_id INTEGER NOT NULL, side TEXT NOT NULL, price REAL, size_before REAL, size_after REAL,
      leverage REAL, realized REAL, funding REAL);
    CREATE INDEX IF NOT EXISTS ix_account ON events(account_id, block, log_index);
    CREATE INDEX IF NOT EXISTS ix_block ON events(block);
    CREATE INDEX IF NOT EXISTS ix_ts ON events(ts);
    CREATE INDEX IF NOT EXISTS ix_kind_ts ON events(kind, ts);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS flows(
      id TEXT PRIMARY KEY, block INTEGER NOT NULL, ts INTEGER NOT NULL, kind TEXT NOT NULL, account_id INTEGER NOT NULL, amount REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_flows_ts ON flows(ts);
    """)
    return con


def _meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _set_meta(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


# ------------------------------------------------------------------ chain access
def _rpc(method: str, params: list, timeout: int = 40, retries: int = 6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last: Exception = RuntimeError("no endpoint")
    for url in config.RPC_LIST:
        for attempt in range(retries):
            req = urllib.request.Request(url, body, {"Content-Type": "application/json", "User-Agent": "undertow-history"})
            try:
                out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    time.sleep(1.5 * 2 ** attempt)
                    continue
                break
            except Exception as e:
                last = e
                break
            if "error" in out:
                last = RuntimeError(str(out["error"].get("message", out["error"])))
                if "range" in str(last).lower():
                    raise last  # the caller shrinks the block range
                break
            return out["result"]
    raise last


def head_block() -> int:
    return int(_rpc("eth_blockNumber", []), 16)


def perp_decimals(perp_ids: list[int]) -> dict[int, tuple[int, int]]:
    """(price decimals, lot decimals) per market, from Exchange.getPerpetualInfo."""
    fn = next(x for x in _abi if x.get("type") == "function" and x["name"] == "getPerpetualInfo")

    def tname(o):
        return "(" + ",".join(tname(c) for c in o["components"]) + ")" if o["type"] == "tuple" else o["type"]
    out_type = tname(fn["outputs"][0])
    sel = keccak(text="getPerpetualInfo(uint256)")[:4].hex()
    out = {}
    for pid in perp_ids:
        try:
            raw = _rpc("eth_call", [{"to": config.EXCHANGE, "data": "0x" + sel + f"{pid:064x}"}, "latest"])
            t = abi_decode([out_type], bytes.fromhex(raw[2:]))[0]
            out[pid] = (int(t[2]), int(t[3]))
        except Exception:
            continue
    return out


def get_logs(from_block: int, to_block: int, topics: list[str] | None = None) -> list[dict]:
    """Event logs in [from_block, to_block] (position events unless `topics` is given), splitting the range if the RPC says it is too large."""
    span = _FIND_GETLOGS_SPAN["span"]
    if to_block - from_block + 1 > span:
        out, b = [], from_block
        while b <= to_block:
            out += get_logs(b, min(b + span - 1, to_block), topics)
            b += span
        return out
    try:
        return _rpc("eth_getLogs", [{"fromBlock": hex(from_block), "toBlock": hex(to_block), "address": config.EXCHANGE,
                                       "topics": [topics or list(TOPIC.values())]}])
    except RuntimeError as e:
        if "range" in str(e).lower() and span > 100:
            _FIND_GETLOGS_SPAN["span"] = 100
            return get_logs(from_block, to_block, topics)
        raise


# ------------------------------------------------------------------ decoding
def decode_log(log: dict, dec: dict[int, tuple[int, int]]) -> dict | None:
    name = BY_TOPIC.get(log["topics"][0])
    if not name:
        return None
    vals = dict(zip(FIELDS[name], abi_decode(TYPES[name], bytes.fromhex(log["data"][2:]))))
    kind, acct_field = EVENTS[name]
    pid = int(vals["perpId"])
    if pid not in dec:
        return None
    pd, ld = 10 ** dec[pid][0], 10 ** dec[pid][1]
    price = lambda k: vals[k] / pd  # noqa: E731
    lots = lambda k: vals[k] / ld  # noqa: E731
    r = {"kind": kind, "perp_id": pid, "account_id": int(vals[acct_field]), "price": None, "size_before": None, "size_after": None,
         "leverage": None, "realized": None, "funding": None}
    if "positionType" in vals:
        r["side"] = "Long" if int(vals["positionType"]) == 0 else "Short"
    if name == "PositionOpenedV2":
        r.update(price=price("pricePNS"), size_before=0.0, size_after=lots("lotLNS"), leverage=vals["leverageHdths"] / 100)
    elif name == "PositionIncreasedV2":
        r.update(price=price("pricePNS"), size_before=lots("startLotLNS"), size_after=lots("endLotLNS"), leverage=vals["leverageHdths"] / 100)
    elif name == "PositionDecreased":
        r.update(size_before=lots("startLotLNS"), size_after=lots("endLotLNS"), realized=vals["deltaPnlCNS"] / CNS, funding=vals["fundingCNS"] / CNS)
    elif name == "PositionClosed":
        r.update(price=price("pricePNS"), size_after=0.0, realized=vals["deltaPnlCNS"] / CNS, funding=vals["fundingCNS"] / CNS)
    elif name == "PositionInverted":
        r.update(price=price("pricePNS"), size_before=lots("startLotLNS"), size_after=lots("endLotLNS"), leverage=vals["leverageHdths"] / 100,
                 realized=vals["deltaPnlCNS"] / CNS, funding=vals["fundingCNS"] / CNS)
    elif name == "PositionLiquidated":
        r.update(price=price("liqPricePNS"), size_before=lots("posLotLNS") + lots("liqLotLNS"), size_after=lots("posLotLNS"),
                 realized=vals["deltaPnlCNS"] / CNS, funding=vals["fundingCNS"] / CNS)
    elif name == "PositionDeleveragedV2":
        r.update(price=price("deleveragePricePNS"), size_before=lots("startLotLNS"), size_after=lots("endLotLNS"),
                 realized=vals["deltaPnlCNS"] / CNS, funding=vals["fundingCNS"] / CNS)
    blk, li = int(log["blockNumber"], 16), int(log["logIndex"], 16)
    r.update(id=f"{blk}-{li}", block=blk, log_index=li, ts=int(log["blockTimestamp"], 16))
    return r


def store(con: sqlite3.Connection, rows: list[dict]) -> int:
    con.executemany("""INSERT OR IGNORE INTO events(id,block,log_index,ts,kind,perp_id,account_id,side,price,size_before,size_after,leverage,realized,funding)
        VALUES(:id,:block,:log_index,:ts,:kind,:perp_id,:account_id,:side,:price,:size_before,:size_after,:leverage,:realized,:funding)""", rows)
    return len(rows)


# ------------------------------------------------------------------ loading
def load_range(con: sqlite3.Connection, from_block: int, to_block: int, dec: dict, chunk: int = 20_000, progress=None) -> int:
    """Fetch and store events in [from_block, to_block], in chunks so progress is saved as it goes."""
    total, b = 0, from_block
    while b <= to_block:
        e = min(b + chunk - 1, to_block)
        rows = [r for r in (decode_log(l, dec) for l in get_logs(b, e)) if r]
        total += store(con, rows)
        _set_meta(con, "last_block", e)
        if _meta(con, "first_block") is None or b < _meta(con, "first_block"):
            _set_meta(con, "first_block", b)
            _set_meta(con, "first_ts", block_timestamp(b))
        con.commit()
        if progress:
            progress(e, to_block, total)
        b = e + 1
    return total


def decode_flow(log: dict) -> dict | None:
    name = FLOW_BY_TOPIC.get(log["topics"][0])
    if not name:
        return None
    acct, amount, _bal = abi_decode(["uint256", "uint256", "uint256"], bytes.fromhex(log["data"][2:]))
    blk, li = int(log["blockNumber"], 16), int(log["logIndex"], 16)
    return {"id": f"{blk}-{li}", "block": blk, "ts": int(log["blockTimestamp"], 16), "kind": FLOW_EVENTS[name], "account_id": int(acct), "amount": amount / CNS}


def flows_catch_up(safety_blocks: int = 3, chunk: int = 20_000) -> int:
    """Load deposits and withdrawals for the same block range as the position events, resuming where it stopped."""
    con = connect()
    try:
        first, last = _meta(con, "first_block"), _meta(con, "last_block")
        if first is None:
            return 0
        done = _meta(con, "flows_last_block")
        b = (done + 1) if done is not None else first
        end, n = last, 0
        while b <= end:
            e = min(b + chunk - 1, end)
            rows = [r for r in (decode_flow(l) for l in get_logs(b, e, list(FLOW_TOPIC.values()))) if r]
            con.executemany("INSERT OR IGNORE INTO flows(id,block,ts,kind,account_id,amount) VALUES(:id,:block,:ts,:kind,:account_id,:amount)", rows)
            n += len(rows)
            _set_meta(con, "flows_last_block", e)
            con.commit()
            b = e + 1
        return n
    finally:
        con.close()


def block_timestamp(block: int) -> int:
    return int(_rpc("eth_getBlockByNumber", [hex(block), False])["timestamp"], 16)


def _decimals(con, perp_ids: list[int]) -> dict[int, tuple[int, int]]:
    cached = {int(k): tuple(v) for k, v in (_meta(con, "decimals", {}) or {}).items()}
    missing = [p for p in perp_ids if p not in cached]
    if missing:
        cached.update(perp_decimals(missing))
        _set_meta(con, "decimals", {str(k): list(v) for k, v in cached.items()})
        con.commit()
    return cached


def catch_up(days_if_empty: float = 1.0, safety_blocks: int = 3, perp_ids: list[int] | None = None, progress=None) -> dict:
    """Bring the store up to the chain head (minus a few blocks). On an empty store start `days_if_empty` days back."""
    con = connect()
    try:
        perp_ids = perp_ids or [1, 10, 20, 31, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150]
        dec = _decimals(con, perp_ids)
        head = head_block() - safety_blocks
        last = _meta(con, "last_block")
        start = (last + 1) if last is not None else head - int(days_if_empty * BLOCKS_PER_DAY)
        n = load_range(con, start, head, dec, progress=progress) if start <= head else 0
        _set_meta(con, "last_ts", block_timestamp(head))
        _set_meta(con, "updated", time.time())
        con.commit()
        return {"new_events": n, "last_block": head}
    finally:
        con.close()


def coverage() -> dict | None:
    if not DB_FILE.exists():
        return None
    con = connect()
    try:
        first, last = _meta(con, "first_block"), _meta(con, "last_block")
        if first is None:
            return None
        first_ts = con.execute("SELECT MIN(ts) FROM events WHERE block >= ?", (first,)).fetchone()[0]
        n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return {"first_block": first, "last_block": last, "last_ts": _meta(con, "last_ts"), "events": n, "first_event_ts": first_ts,
                "window_start_ts": _meta(con, "first_ts") or (_meta(con, "last_ts") - (last - first) * 0.4 if _meta(con, "last_ts") else first_ts)}
    finally:
        con.close()


def wallet_events(account_id: int) -> dict | None:
    """Same shape the wallet history logic expects: events oldest first, plus how much time is covered. None if nothing is stored."""
    cov = coverage()
    if cov is None:
        return None
    names = None
    con = connect()
    try:
        rows = con.execute("SELECT id,block,log_index,ts,kind,perp_id,side,price,size_before,size_after,leverage,realized,funding FROM events "
                           "WHERE account_id=? ORDER BY block, log_index", (account_id,)).fetchall()
    finally:
        con.close()
    return {"rows": rows, "window_start_ts": int(cov["window_start_ts"]), "indexed_through_ts": cov["last_ts"],
            "indexed_through_block": cov["last_block"], "events_total": cov["events"]}


class Follower(threading.Thread):
    """Background thread: keeps the store current. The first run backfills `days` days."""

    def __init__(self, days: float = 1.0, interval: float = 20.0, perp_ids_fn=None):
        super().__init__(daemon=True, name="history-follower")
        self.days, self.interval, self.perp_ids_fn = days, interval, perp_ids_fn
        self.state = {"running": False, "error": "", "last_new": 0, "backfilling": False}

    def run(self):
        while True:
            try:
                self.state["backfilling"] = coverage() is None
                self.state["running"] = True
                r = catch_up(self.days, perp_ids=self.perp_ids_fn() if self.perp_ids_fn else None)
                self.state.update(last_new=r["new_events"], error="", backfilling=False)
                flows_catch_up()
            except Exception as e:
                self.state["error"] = f"{type(e).__name__}: {e}"
                time.sleep(30)
            time.sleep(self.interval)
