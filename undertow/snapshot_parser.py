"""Parse the text output of `perpl-cli snapshot` into structured data.

The snapshot contains, at one block height:
  * every perpetual market: prices, open interest, fees, margin settings
  * every resting order in each market's order book (with the owning account)
  * every account's open positions, including Perpl's own liquidation price and
    bankruptcy price calculated by the official SDK

Box-drawing characters are replaced by `|` before parsing, so the parser works
whether the file was decoded correctly or mangled by a Windows code page.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

NON_ASCII = re.compile(r"[^\x00-\x7f]+")
HEADER = re.compile(r"^#(\d+) @ (\S+ \S+) \| (0x[0-9a-fA-F]+) \|")
PERP_HDR = re.compile(r"^\* Perp #(\d+) (\S+)(?: (PAUSED))?")
ACCOUNT_HDR = re.compile(r"^Account #(\d+)")


@dataclass
class Order:
    price: float
    size: float
    kind: str  # "Open Long", "Close Long", "Open Short", "Close Short", ...
    order_id: int
    account_id: int
    leverage: float | None
    post_only: bool = False
    fill_or_kill: bool = False
    immediate_or_cancel: bool = False
    expiry_block: int | None = None
    expired: bool = False

    @property
    def is_bid(self) -> bool:
        # Open Long and Close Short both BUY the contract.
        return self.kind in ("Open Long", "Close Short")


@dataclass
class Perp:
    perp_id: int
    name: str
    paused: bool = False
    last: float | None = None
    mark: float | None = None
    oracle: float | None = None
    funding_rate: float | None = None
    open_interest_size: float | None = None
    open_interest_usd: float | None = None
    taker_fee: float | None = None
    maker_fee: float | None = None
    initial_margin_x: float | None = None  # "15.00" in the ini/mnt cell
    maintenance_margin_x: float | None = None  # "25.00" in the ini/mnt cell
    orders: list[Order] = field(default_factory=list)


@dataclass
class Position:
    account_id: int
    perp_id: int
    side: str  # "Long" | "Short"
    entry_price: float
    size: float
    deposit: float
    delta_pnl: float
    premium_pnl: float
    total_pnl: float
    liq_price: float
    bankruptcy_price: float


@dataclass
class Snapshot:
    block: int
    timestamp: str
    exchange: str
    perps: dict[int, Perp] = field(default_factory=dict)
    positions: list[Position] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["perps"] = {str(k): v for k, v in d["perps"].items()}
        return d


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace("$", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _cells(line: str) -> list[str]:
    parts = line.split("|")
    return [p.strip() for p in parts[1:-1]]


def _find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _parse_perp_meta(perp: Perp, text: str) -> None:
    perp.last = _num(_find(r"Last:\s*([\d.]+)", text))
    perp.mark = _num(_find(r"Mark:\s*([\d.]+)", text))
    perp.oracle = _num(_find(r"Oracle:\s*([\d.]+)", text))
    perp.funding_rate = _num(_find(r"Funding Rate:\s*(-?[\d.]+)", text))
    perp.open_interest_size = _num(_find(r"Open Interest:\s*([\d.]+)", text))
    perp.open_interest_usd = _num(_find(r"amount:\s*\$?([\d.]+)", text))
    m = re.search(r"Fees:\s*([\d.]+)\s*/\s*([\d.]+)", text)
    if m:
        perp.taker_fee, perp.maker_fee = float(m.group(1)), float(m.group(2))
    m = re.search(r"Margin:\s*([\d.]+)\s*/\s*([\d.]+)", text)
    if m:
        perp.initial_margin_x, perp.maintenance_margin_x = float(m.group(1)), float(m.group(2))


def _parse_order(cells: list[str]) -> Order | None:
    # columns: Price | Size | Type | Order ID | Account ID | Request ID | Client Order ID | Expiry Block | Leverage | PO | FoK | IoC | Builder
    if len(cells) < 10 or not re.match(r"^(Open|Close) (Long|Short)", cells[2]):
        return None
    price, size = _num(cells[0]), _num(cells[1])
    if price is None or size is None:
        return None
    exp = re.match(r"(\d+)", cells[7])
    return Order(
        price=price,
        size=size,
        kind=cells[2],
        order_id=int(cells[3]),
        account_id=int(cells[4]),
        leverage=_num(cells[8]),
        post_only=bool(cells[9]),
        fill_or_kill=bool(cells[10]) if len(cells) > 10 else False,
        immediate_or_cancel=bool(cells[11]) if len(cells) > 11 else False,
        expiry_block=int(exp.group(1)) if exp else None,
        expired="expired" in cells[7],
    )


def parse_snapshot(text: str) -> Snapshot:
    lines = [NON_ASCII.sub("|", ln).rstrip() for ln in text.splitlines()]

    snap: Snapshot | None = None
    perp: Perp | None = None
    perp_meta_lines: list[str] = []
    account_id: int | None = None

    for ln in lines:
        m = HEADER.match(ln)
        if m and snap is None:
            snap = Snapshot(block=int(m.group(1)), timestamp=m.group(2), exchange=m.group(3))
            continue

        m = PERP_HDR.match(ln)
        if m:
            if perp is not None:
                _parse_perp_meta(perp, "\n".join(perp_meta_lines))
            perp = Perp(perp_id=int(m.group(1)), name=m.group(2), paused=bool(m.group(3)))
            assert snap is not None, "perp section before snapshot header"
            snap.perps[perp.perp_id] = perp
            perp_meta_lines = []
            account_id = None
            continue

        m = ACCOUNT_HDR.match(ln)
        if m:
            if perp is not None:
                _parse_perp_meta(perp, "\n".join(perp_meta_lines))
                perp = None
            account_id = int(m.group(1))
            continue

        if not ln.startswith("|"):
            continue
        cells = _cells(ln)

        if perp is not None and account_id is None:
            order = _parse_order(cells)
            if order:
                perp.orders.append(order)
            else:
                perp_meta_lines.append(ln)
        elif account_id is not None and snap is not None and len(cells) >= 10:
            if cells[1] in ("Long", "Short") and cells[0].isdigit():
                f = [_num(c) for c in cells[2:10]]
                if None in f:
                    continue
                snap.positions.append(
                    Position(account_id, int(cells[0]), cells[1], *f)  # type: ignore[arg-type]
                )

    if perp is not None:
        _parse_perp_meta(perp, "\n".join(perp_meta_lines))
    if snap is None:
        raise ValueError("No snapshot header found; is this perpl-cli snapshot output?")
    return snap
