"""Risk-aware market-making bot (paper mode by default).

    python -m undertow.bot --paper --cycles 10 --interval 45      # simulated, real market data
    python -m undertow.bot --live                                  # LOCKED: see "Going live" below

What it does: keeps one post-only buy slightly below the mark price and one post-only sell slightly above it,
earning the gap. It leans against its own inventory, widens its gap near big liquidation zones, pulls its
orders when a zone is both close and large compared with the order book, and halts on any hard limit.

Safety design
  * every order is post-only (always the low maker fee) and carries an expiry, so if this program dies its
    orders disappear on their own within `expiry_blocks` (~60 s at 0.4 s per block);
  * every order has a strictly increasing request id (the exchange treats it as an idempotency key);
  * hard limits (daily loss, leverage, distance to own liquidation, kill-switch file) halt the bot;
  * stale data pulls the quotes.

Going live (not done by this project's author, needs the account owner): set PERPL_PRIVATE_KEY in the
environment for a SEPARATE small wallet, set UNDERTOW_ALLOW_LIVE=yes, pass --live. Without both, the live
executor refuses to send anything. The live path has NOT been run against the chain.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, perpl_cli, risk

BOT_DIR = config.BOT_DIR
KILL_FILE = BOT_DIR / "KILL"
STATE_FILE = BOT_DIR / "state.json"
LOG_FILE = BOT_DIR / "log.jsonl"
BLOCKS_PER_SECOND = 2.5  # Monad: one block about every 0.4 s


@dataclass
class Params:
    perp_id: int = config.DEFAULT_PERP  # BTC is 1 on mainnet and 16 on testnet
    half_spread_bps: float = 8.0  # each side's distance from fair price; maker fee is only ~0.45 bps
    size_usd: float = 20.0  # order size per side
    max_position_usd: float = 60.0  # stop adding on the side that grows inventory beyond this
    inventory_skew_bps: float = 6.0  # full-inventory shift of both quotes against the position
    max_leverage: float = 2.0  # position value / equity: above this the bot halts
    max_daily_loss_usd: float = 5.0
    min_liq_distance_pct: float = 25.0  # own liquidation must stay at least this far from the mark
    zone_pullback_pct: float = 1.5  # a zone this close counts as "near"
    zone_min_usd: float = 25_000.0  # ... if at least this big
    widen_factor: float = 2.5  # spread multiplier when a big zone is near
    pull_ratio: float = 2.0  # pull quotes if near zone value / book depth within 5% exceeds this
    data_stale_s: float = 120.0
    expiry_blocks: int = 150
    tick: float = 0.1  # BTC price tick (prices in the book have one decimal)
    lot: float = 0.00001  # BTC size step (sizes in the book have five decimals)
    maker_fee: float = 0.000045
    equity_start_usd: float = 100.0


@dataclass
class Quote:
    side: str  # "bid" | "ask"
    order_type: str  # open-long | open-short | close-long | close-short
    price: float
    size: float


@dataclass
class Decision:
    action: str  # "quote" | "pull" | "halt"
    reason: str
    quotes: list[Quote] = field(default_factory=list)
    fair: float = 0.0
    half_spread_bps: float = 0.0


@dataclass
class State:
    cash: float = 100.0  # AUSD, after fees and realised PnL
    position: float = 0.0  # signed size in base units (+long, -short)
    entry: float = 0.0
    realized: float = 0.0
    fees: float = 0.0
    fills: int = 0
    day_start_equity: float = 100.0
    day: str = ""
    halted: bool = False
    halt_reason: str = ""
    last_action: str = ""
    last_reason: str = ""
    resting: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def equity(self, mark: float) -> float:
        return self.cash + self.position * (mark - self.entry if self.position else 0.0)


def _round_to(x: float, step: float, down: bool) -> float:
    n = math.floor(x / step + 1e-9) if down else math.ceil(x / step - 1e-9)
    return round(n * step, 8)


# ---------------------------------------------------------------- decision (pure, unit-tested)
def decide(snap: dict, st: State, p: Params, data_age_s: float, kill: bool = False) -> Decision:
    perp = snap["perps"][str(p.perp_id)]
    mark = perp["mark"]
    equity = st.equity(mark)
    pos_usd = st.position * mark

    if kill:
        return Decision("halt", "kill switch file present")
    if st.halted:
        return Decision("halt", st.halt_reason or "halted earlier")
    if equity - st.day_start_equity <= -p.max_daily_loss_usd:
        return Decision("halt", f"daily loss limit: {equity - st.day_start_equity:+.2f} USD")
    if equity <= 0:
        return Decision("halt", "equity exhausted")
    if abs(pos_usd) / equity > p.max_leverage:
        return Decision("halt", f"leverage {abs(pos_usd) / equity:.2f}x above limit {p.max_leverage}x")
    if st.position:
        side = "Long" if st.position > 0 else "Short"
        lev = max(abs(pos_usd) / equity, 1.0)
        liq = risk.liq_price_at_leverage(st.entry, abs(st.position), side, lev, perp["maintenance_margin_x"])
        dist = abs(liq / mark - 1) * 100
        if dist < p.min_liq_distance_pct:
            return Decision("halt", f"liquidation only {dist:.1f}% away (minimum {p.min_liq_distance_pct}%)")
    if data_age_s > p.data_stale_s:
        return Decision("pull", f"data is {data_age_s:.0f}s old (limit {p.data_stale_s:.0f}s)")

    spread = p.half_spread_bps
    note = "normal"
    def edge_dist(z: risk.Zone) -> float:  # distance from the mark to the zone's nearest edge, in percent
        return min(abs(z.low / mark - 1), abs(z.high / mark - 1)) * 100

    near = [z for z in risk.zones(snap, p.perp_id) if edge_dist(z) <= p.zone_pullback_pct and z.notional >= p.zone_min_usd]
    if near:
        z = max(near, key=lambda x: x.notional)
        depth = risk.depth_notional(snap, p.perp_id, bids=(z.side == "Long"), within_pct=5.0)
        ratio = z.notional / depth if depth else float("inf")
        if ratio >= p.pull_ratio:
            return Decision("pull", f"{z.side.lower()} zone {risk.fmt_usd(z.notional)} within "
                                    f"{edge_dist(z):.1f}% away is {ratio:.1f}x the book depth")
        spread *= p.widen_factor
        note = f"widened x{p.widen_factor}: {z.side.lower()} zone {risk.fmt_usd(z.notional)} near"

    fair = mark
    inv_ratio = max(-1.0, min(1.0, pos_usd / p.max_position_usd)) if p.max_position_usd else 0.0
    shift = -inv_ratio * p.inventory_skew_bps  # long inventory -> lower both quotes -> more likely to sell
    bid_px = _round_to(fair * (1 - (spread - shift) / 1e4), p.tick, down=True)
    ask_px = _round_to(fair * (1 + (spread + shift) / 1e4), p.tick, down=False)
    size = _round_to(p.size_usd / fair, p.lot, down=True)
    if size < p.lot:
        return Decision("pull", "order size rounds to zero")

    quotes: list[Quote] = []
    can_add_long = pos_usd + size * fair <= p.max_position_usd
    can_add_short = -pos_usd + size * fair <= p.max_position_usd
    # Buy side: reduces a short if we hold one, otherwise opens/adds a long.
    if st.position < 0:
        quotes.append(Quote("bid", "close-short", bid_px, min(size, _round_to(-st.position, p.lot, down=True))))
    elif can_add_long:
        quotes.append(Quote("bid", "open-long", bid_px, size))
    # Sell side: reduces a long if we hold one, otherwise opens/adds a short.
    if st.position > 0:
        quotes.append(Quote("ask", "close-long", ask_px, min(size, _round_to(st.position, p.lot, down=True))))
    elif can_add_short:
        quotes.append(Quote("ask", "open-short", ask_px, size))
    if not quotes:
        return Decision("pull", "position limit reached on both sides", fair=fair)
    return Decision("quote", note, quotes, fair, spread)


# ---------------------------------------------------------------- paper execution
def paper_fills(st: State, mark: float, p: Params) -> list[dict]:
    """Fill resting paper orders if the mark price has crossed them. Coarse: one check per cycle."""
    fills, keep = [], []
    for o in st.resting:
        crossed = (o["side"] == "bid" and mark <= o["price"]) or (o["side"] == "ask" and mark >= o["price"])
        if not crossed:
            keep.append(o)
            continue
        signed = o["size"] if o["side"] == "bid" else -o["size"]
        fee = o["price"] * o["size"] * p.maker_fee
        new_pos = st.position + signed
        if st.position == 0 or (st.position > 0) == (signed > 0):  # opening or adding
            st.entry = (st.entry * abs(st.position) + o["price"] * o["size"]) / max(abs(new_pos), 1e-12)
        else:  # reducing: realise PnL on the closed part
            closed = min(abs(signed), abs(st.position))
            pnl = (o["price"] - st.entry) * closed * (1 if st.position > 0 else -1)
            st.realized += pnl
            st.cash += pnl
            if abs(new_pos) < 1e-12 or (new_pos > 0) != (st.position > 0):
                st.entry = o["price"] if abs(new_pos) > 1e-12 else 0.0
        st.cash -= fee
        st.fees += fee
        st.position = 0.0 if abs(new_pos) < 1e-12 else new_pos
        st.fills += 1
        fills.append({**o, "fee": fee, "filled_at_mark": mark})
    st.resting = keep
    return fills


# ---------------------------------------------------------------- live execution (locked)
class LiveLocked(RuntimeError):
    pass


def build_create_cmd(q: Quote, p: Params, current_block: int, request_id: int, testnet: bool,
                     account: str | None = None, key_file: str | None = None) -> list[str]:
    """The perpl-cli command for one post-only order. The key itself is never put on the command line:
    perpl-cli reads PERPL_PRIVATE_KEY from the environment, or the key from a file whose PATH is passed."""
    cmd = ["perpl-cli"] + (["--testnet"] if testnet else []) + ["order", "--perp", str(p.perp_id)]
    if account:
        cmd += ["--account", account]
    cmd += ["create", "--type", q.order_type, "--size", f"{q.size:.5f}", "--price", f"{q.price:.1f}",
            "--leverage", f"{p.max_leverage:g}", "--post-only",
            "--expiry-block", str(current_block + p.expiry_blocks), "--request-id", str(request_id)]
    if key_file:  # an option of `create`, so it must come after the subcommand
        cmd += ["--private-key-path", key_file]
    return cmd


class LiveExecutor:
    def __init__(self, p: Params, testnet: bool, account: str | None = None, key_file: str | None = None):
        if os.environ.get("UNDERTOW_ALLOW_LIVE") != "yes":
            raise LiveLocked("Set UNDERTOW_ALLOW_LIVE=yes to allow sending orders.")
        if not testnet and os.environ.get("UNDERTOW_ALLOW_MAINNET") != "yes":
            raise LiveLocked("MAINNET uses real money. Also set UNDERTOW_ALLOW_MAINNET=yes, only after a clean testnet run.")
        if testnet and not key_file and (config.DATA / "key.txt").exists():
            key_file = str(config.DATA / "key.txt")  # the throwaway testnet key made by undertow.testnet
        if not key_file and not os.environ.get("PERPL_PRIVATE_KEY"):
            raise LiveLocked("Set PERPL_PRIVATE_KEY (a SEPARATE small wallet) in the environment, or pass a key file.")
        self.p, self.testnet, self.account, self.key_file = p, testnet, account, key_file
        self._rid = int(time.time() * 1000)

    def place(self, q: Quote, current_block: int) -> str:
        self._rid += 1
        cmd = build_create_cmd(q, self.p, current_block, self._rid, self.testnet, self.account, self.key_file)
        out = subprocess.run(cmd, capture_output=True, timeout=60)
        return (out.stdout + out.stderr).decode("utf-8", "replace")[-400:]


# ---------------------------------------------------------------- alerts and logging
def alert(text: str) -> None:
    """Telegram message if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set; always printed."""
    print(f"[ALERT] {text}", flush=True)
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"Undertow bot: {text}"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=10)
        except Exception as e:  # an alert failure must never stop the bot
            print(f"[alert failed] {e}")


def log(event: dict) -> None:
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), **event}) + "\n")


def save_state(st: State, p: Params, mark: float, mode: str, decision: Decision | None) -> None:
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    out = {"mode": mode, "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "mark": mark,
           "equity": st.equity(mark), "pnl": st.equity(mark) - p.equity_start_usd, "state": asdict(st),
           "params": asdict(p), "decision": asdict(decision) if decision else None}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- main loop
def run(p: Params, mode: str, cycles: int | None, interval: float, testnet: bool = config.TESTNET) -> State:
    st = State(cash=p.equity_start_usd, day_start_equity=p.equity_start_usd)
    executor = LiveExecutor(p, testnet) if mode == "live" else None
    prev_action = ""
    n = 0
    while cycles is None or n < cycles:
        n += 1
        try:
            snap = risk.load_latest()
            age = time.time() - risk.LATEST.stat().st_mtime
            mark = snap["perps"][str(p.perp_id)]["mark"]
            today = time.strftime("%Y-%m-%d", time.gmtime())
            if st.day != today:
                st.day, st.day_start_equity = today, st.equity(mark)
            if mode == "paper":
                for f in paper_fills(st, mark, p):
                    log({"event": "fill", **f})
            d = decide(snap, st, p, age, kill=KILL_FILE.exists())
            if d.action == "halt" and not st.halted:
                st.halted, st.halt_reason = True, d.reason
                alert(f"HALTED: {d.reason}. All quotes pulled.")
            st.resting = []  # cancel/replace: previous quotes are dropped (live: they expire on their own)
            if d.action == "quote":
                if executor:
                    for q in d.quotes:
                        log({"event": "live_order", "q": asdict(q), "result": executor.place(q, snap["block"])})
                st.resting = [{**asdict(q)} for q in d.quotes]
            st.last_action, st.last_reason = d.action, d.reason
            if d.action != prev_action:
                log({"event": "decision", "action": d.action, "reason": d.reason})
                if d.action == "pull":
                    alert(f"quotes pulled: {d.reason}")
                prev_action = d.action
            log({"event": "cycle", "mark": mark, "action": d.action, "equity": st.equity(mark),
                 "position": st.position, "quotes": [asdict(q) for q in d.quotes]})
            save_state(st, p, mark, mode, d)
        except Exception as e:
            log({"event": "error", "error": f"{type(e).__name__}: {e}"})
            print(f"[error] {e}", flush=True)
        if st.halted:
            break
        if cycles is None or n < cycles:
            time.sleep(interval)
    return st


def main() -> None:
    ap = argparse.ArgumentParser(prog="undertow.bot")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="simulate (default)")
    g.add_argument("--live", action="store_true", help="send real orders (locked, see module docstring)")
    ap.add_argument("--cycles", type=int)
    ap.add_argument("--interval", type=float, default=45)
    ap.add_argument("--testnet", action="store_true", default=config.TESTNET)
    ap.add_argument("--perp", type=int, default=config.DEFAULT_PERP)
    a = ap.parse_args()
    p = Params(perp_id=a.perp)
    mode = "live" if a.live else "paper"
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    st = run(p, mode, a.cycles, a.interval, a.testnet)
    mark = risk.load_latest()["perps"][str(p.perp_id)]["mark"]
    print(f"done: {st.fills} fills, equity {st.equity(mark):.4f} ({st.equity(mark) - p.equity_start_usd:+.4f}), "
          f"position {st.position:.5f}, fees {st.fees:.4f}" + (f", HALTED: {st.halt_reason}" if st.halted else ""))


if __name__ == "__main__":
    main()
