"""Decode `perpl-cli block N` output into a list of events.

Event lines look like:
    4: PositionIncreasedV2(PositionIncreasedV2 { perpId: 1, accountId: 5364, ... })
Fields are flat `name: value` pairs, so a light regex is enough. We keep the raw
integer fields (the contract's scaled units); price/size scaling is done only where
a screen needs it.

Used for: counting which accounts a block touched, and spotting liquidations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EVENT = re.compile(r"^\s+(\d+): (\w+)\((?:\w+ \{ (.*) \}|(\w*))\)\s*$")
TX = re.compile(r"^\*{4} Tx #(\d+) \((0x[0-9a-fA-F]+)\)")
FIELD = re.compile(r"(\w+): (0x[0-9a-fA-F]*|-?\d+|true|false)")


@dataclass
class Event:
    tx: str
    index: int
    name: str
    fields: dict[str, int | bool | str] = field(default_factory=dict)

    @property
    def is_liquidation(self) -> bool:
        # Names confirmed against the Exchange ABI shipped in perpl-sdk.
        return self.name == "PositionLiquidated"

    @property
    def is_deleverage(self) -> bool:
        return self.name in ("PositionDeleveraged", "PositionDeleveragedV2")


def _val(v: str):
    if v in ("true", "false"):
        return v == "true"
    if v.startswith("0x"):
        return v
    return int(v)


def parse_block(text: str) -> list[Event]:
    events: list[Event] = []
    tx = ""
    for ln in text.splitlines():
        m = TX.match(ln)
        if m:
            tx = m.group(2)
            continue
        m = EVENT.match(ln)
        if m:
            fields = {k: _val(v) for k, v in FIELD.findall(m.group(3) or "")}
            events.append(Event(tx, int(m.group(1)), m.group(2), fields))
    return events


def touched_accounts(events: list[Event]) -> set[int]:
    out: set[int] = set()
    for e in events:
        for key in ("accountId", "posAccountId"):
            v = e.fields.get(key)
            if isinstance(v, int):
                out.add(v)
    return out


def liquidations(events: list[Event]) -> list[Event]:
    return [e for e in events if e.is_liquidation]


def deleverages(events: list[Event]) -> list[Event]:
    return [e for e in events if e.is_deleverage]
