"""Account registry: Perpl account ID <-> wallet address, plus balances.

Snapshots label traders only by account ID. The registry maps each ID to its wallet
address and balance with batched on-chain reads (undertow.onchain: ~10 accounts per
request). `perpl-cli show account` gives extra detail (unrealized PnL, fee tier) but
takes ~11 s per account, so it is only used for single-wallet drill-downs.
Results are cached in data/accounts.json.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, onchain, perpl_cli

CACHE = config.DATA / "accounts.json"
NON_ASCII = re.compile(r"[^\x00-\x7f]+")
HDR = re.compile(r"Account #(\d+) \((0x[0-9a-fA-F]{40})\)")
BAL = re.compile(
    r"Balance:\s*(-?[\d.]+)\s*\|\s*Available:\s*(-?[\d.]+)\s*\|\s*Locked:\s*(-?[\d.]+)\s*\|\s*"
    r"Unrealized PnL:\s*(-?[\d.]+)\s*\|\s*Fee Tier:\s*(\d+)"
)


@dataclass
class Account:
    account_id: int
    address: str
    balance: float
    locked: float
    fetched_at: float
    available: float | None = None  # only from `show account`
    unrealized_pnl: float | None = None  # only from `show account`
    fee_tier: int | None = None  # only from `show account`
    frozen: int = 0


def parse_account(text: str) -> Account:
    text = NON_ASCII.sub("|", text)
    h, b = HDR.search(text), BAL.search(text)
    if not h or not b:
        raise ValueError("unrecognised `show account` output")
    bal, avail, locked, upnl, tier = b.groups()
    return Account(int(h.group(1)), h.group(2), float(bal), float(locked), time.time(),
                   float(avail), float(upnl), int(tier))


def load_cache() -> dict[int, Account]:
    if not CACHE.exists():
        return {}
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    return {int(k): Account(**v) for k, v in raw.items()}


def save_cache(accts: dict[int, Account]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps({str(k): asdict(v) for k, v in accts.items()}), encoding="utf-8")
    tmp.replace(CACHE)


def fetch_account(account_id: int | str, **cli) -> Account:
    return parse_account(perpl_cli.show_account(account_id, **cli))


def fill_registry(account_ids: list[int], max_age_s: float = 600, rpc: str = onchain.PUBLIC_RPC
                  ) -> dict[int, Account]:
    """Fetch any account that is missing or older than max_age_s, in batches, and save the cache."""
    accts = load_cache()
    now = time.time()
    todo = [a for a in account_ids if a not in accts or now - accts[a].fetched_at >= max_age_s]
    for aid, d in onchain.get_accounts(todo, rpc=rpc).items():
        old = accts.get(aid)
        accts[aid] = Account(aid, d["address"], d["balance"], d["locked"], time.time(),
                             old.available if old else None, old.unrealized_pnl if old else None,
                             old.fee_tier if old else None, d["frozen"])
    save_cache(accts)
    return accts


def by_address(accts: dict[int, Account], address: str) -> Account | None:
    addr = address.lower()
    return next((a for a in accts.values() if a.address.lower() == addr), None)
