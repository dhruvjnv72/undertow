"""Testnet toolkit: a throwaway wallet, test collateral and a Perpl account, with local signing.

    python -m undertow.testnet new        # create a throwaway TESTNET key (data/testnet/key.txt), print the address
    python -m undertow.testnet status     # MON gas balance, test-token balance, Perpl account
    python -m undertow.testnet fund 1000  # mint 1000 test AUSD, approve, open/fund the Perpl account

Testnet only. The chain id is checked before every signature, so this cannot sign a mainnet transaction.
The key file holds a worthless testnet key; never reuse it anywhere else.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from eth_account import Account
from eth_utils import keccak

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "data" / "testnet" / "key.txt"
RPC = "https://testnet-rpc.monad.xyz"
CHAIN_ID = 10143  # Monad testnet
EXCHANGE = "0x1964C32f0bE608E7D29302AFF5E61268E72080cc"
TOKEN = "0xa9012a055bd4e0eDfF8Ce09f960291C09D5322dC"


def _rpc(method: str, params: list):
    req = urllib.request.Request(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "undertow/0.4"})
    out = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if "error" in out:
        raise RuntimeError(f"{method}: {out['error'].get('message', out['error'])}")
    return out["result"]


def selector(sig: str) -> str:
    return keccak(text=sig)[:4].hex()


def _word(x: int | str) -> str:
    return (f"{x:064x}" if isinstance(x, int) else x.lower().removeprefix("0x").rjust(64, "0"))


def call(to: str, sig: str, *args, sender: str | None = None) -> str:
    data = "0x" + selector(sig) + "".join(_word(a) for a in args)
    tx = {"to": to, "data": data}
    if sender:
        tx["from"] = sender
    return _rpc("eth_call", [tx, "latest"])


def load_key() -> str:
    if not KEY_FILE.exists():
        raise SystemExit("No testnet key yet. Run: python -m undertow.testnet new")
    return KEY_FILE.read_text().strip()


def address() -> str:
    return Account.from_key(load_key()).address


def new_wallet() -> str:
    if KEY_FILE.exists():
        raise SystemExit(f"{KEY_FILE} already exists; refusing to overwrite a key.")
    acct = Account.create()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(acct.key.hex())
    return acct.address


def token_decimals() -> int:
    return int(call(TOKEN, "decimals()"), 16)


def balances(addr: str) -> dict:
    dec = token_decimals()
    return {"address": addr, "mon": int(_rpc("eth_getBalance", [addr, "latest"]), 16) / 1e18,
            "token": int(call(TOKEN, "balanceOf(address)", addr), 16) / 10 ** dec,
            "allowance": int(call(TOKEN, "allowance(address,address)", addr, EXCHANGE), 16) / 10 ** dec}


def send(to: str, sig: str, *args) -> dict:
    """Sign locally and send one transaction; wait for the receipt."""
    if int(_rpc("eth_chainId", []), 16) != CHAIN_ID:
        raise SystemExit("RPC is not Monad testnet; refusing to sign.")
    acct = Account.from_key(load_key())
    data = "0x" + selector(sig) + "".join(_word(a) for a in args)
    gas = int(_rpc("eth_estimateGas", [{"from": acct.address, "to": to, "data": data}]), 16)
    gas = int(gas * 1.3)  # Monad charges for the gas limit, not gas used, so keep headroom modest
    price = int(_rpc("eth_gasPrice", []), 16)
    tx = {"chainId": CHAIN_ID, "nonce": int(_rpc("eth_getTransactionCount", [acct.address, "pending"]), 16),
          "to": to, "data": data, "value": 0, "gas": gas, "gasPrice": int(price * 1.2)}
    raw = acct.sign_transaction(tx).raw_transaction
    h = _rpc("eth_sendRawTransaction", ["0x" + raw.hex()])
    for _ in range(60):
        r = _rpc("eth_getTransactionReceipt", [h])
        if r:
            if int(r["status"], 16) != 1:
                raise RuntimeError(f"transaction reverted: {h}")
            return {"hash": h, "gas_used": int(r["gasUsed"], 16)}
        time.sleep(1)
    raise RuntimeError(f"no receipt after 60 s: {h}")


def can_mint(addr: str, amount_units: int) -> bool:
    """Dry-run mint with eth_call: true if the test token lets anyone mint."""
    try:
        call(TOKEN, "mint(address,uint256)", addr, amount_units, sender=addr)
        return True
    except RuntimeError:
        return False


def fund(amount: float) -> None:
    addr = address()
    dec = token_decimals()
    units = int(amount * 10 ** dec)
    b = balances(addr)
    if b["mon"] < 0.01:
        raise SystemExit(f"Need testnet MON for gas first (have {b['mon']}). Send some to {addr}")
    if b["token"] < amount:
        print("mint:", send(TOKEN, "mint(address,uint256)", addr, units - int(b["token"] * 10 ** dec)))
    if b["allowance"] < amount:
        print("approve:", send(TOKEN, "approve(address,uint256)", EXCHANGE, units))
    print("createAccount:", send(EXCHANGE, "createAccount(uint256)", units))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "new":
        print("New throwaway TESTNET address:", new_wallet())
    elif cmd == "status":
        b = balances(address())
        print(json.dumps(b, indent=2))
        print("public mint allowed:", can_mint(b["address"], 10 ** token_decimals()))
    elif cmd == "fund":
        fund(float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
