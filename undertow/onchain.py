"""Direct JSON-RPC reads of the Perpl Exchange contract (standard library only).

`perpl-cli show account` costs ~11 s per account, far too slow for 400+ accounts. The
contract's `getAccountById(uint256)` view returns the same identity data in one cheap
`eth_call`, and JSON-RPC lets us batch many calls per HTTP request.

Return layout (verified against Exchange.json in perpl-sdk and against `show account`):
    word 0 accountId | 1 balanceCNS | 2 lockedBalanceCNS | 3 frozen | 4 accountAddr | 5-8 position bitmap
CNS = collateral units of AUSD, 6 decimals.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config

EXCHANGE = config.EXCHANGE
PUBLIC_RPC = config.RPC
# keccak256("getAccountById(uint256)")[:4]; hard-coded to avoid a hashing dependency.
SEL_GET_ACCOUNT_BY_ID = "05aca141"
CNS_DECIMALS = 6


def rpc_batch(calls: list[dict], rpc: str = PUBLIC_RPC, timeout: int = 30, retries: int = 6) -> list[dict]:
    """POST a JSON-RPC batch. The public endpoint answers HTTP 429 when pushed; back off and retry."""
    body = json.dumps(calls).encode()
    for attempt in range(retries):
        req = urllib.request.Request(
            rpc, data=body, headers={"Content-Type": "application/json", "User-Agent": "undertow/0.2"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            return sorted(out, key=lambda x: x["id"])
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == retries - 1:
                raise
            time.sleep(1.5 * 2 ** attempt)
    raise RuntimeError("unreachable")


def decode_account(hexdata: str) -> dict:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    words = [h[i:i + 64] for i in range(0, len(h), 64)]
    if len(words) < 9:
        raise ValueError(f"short account payload ({len(words)} words)")
    return {
        "account_id": int(words[0], 16),
        "balance": int(words[1], 16) / 10 ** CNS_DECIMALS,
        "locked": int(words[2], 16) / 10 ** CNS_DECIMALS,
        "frozen": int(words[3], 16),
        "address": "0x" + words[4][-40:],
    }


def get_accounts(ids: list[int], rpc: str = PUBLIC_RPC, exchange: str = EXCHANGE,
                 batch_size: int = 10, pause: float = 0.6) -> dict[int, dict]:
    """Fetch identity + balance for many account IDs. Accounts that fail are skipped."""
    out: dict[int, dict] = {}
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        calls = [{"jsonrpc": "2.0", "id": n, "method": "eth_call",
                  "params": [{"to": exchange, "data": "0x" + SEL_GET_ACCOUNT_BY_ID + f"{aid:064x}"}, "latest"]}
                 for n, aid in enumerate(chunk)]
        if i:
            time.sleep(pause)  # stay under the public endpoint's rate limit; use pause=0 on your own RPC
        for aid, res in zip(chunk, rpc_batch(calls, rpc)):
            if "result" in res:
                try:
                    out[aid] = decode_account(res["result"])
                except ValueError:
                    pass
    return out
