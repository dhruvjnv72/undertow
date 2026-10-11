"""Which Perpl network the process works on.

    UNDERTOW_NETWORK=testnet python -m undertow.server --refresh      (PowerShell: $env:UNDERTOW_NETWORK="testnet")

Default is mainnet. Testnet data lives in data/testnet/ so the two never mix. Market ids differ per network
(BTC is 1 on mainnet and 16 on testnet), so DEFAULT_PERP follows the network.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NETWORK = os.environ.get("UNDERTOW_NETWORK", "mainnet").strip().lower()
if NETWORK not in ("mainnet", "testnet"):
    raise SystemExit(f"UNDERTOW_NETWORK must be 'mainnet' or 'testnet', got {NETWORK!r}")
TESTNET = NETWORK == "testnet"

DATA = ROOT / "data" / "testnet" if TESTNET else ROOT / "data"
BOT_DIR = DATA / "bot"
# UNDERTOW_RPC overrides the endpoint for everything (data refresh, account lookups, perpl-cli). Useful when the default is
# blocked or down, or to use your own (Alchemy etc). Working alternatives found 2026-10-11: https://rpc2.monad.xyz allows
# 1,000-block log queries; rpc1.monad.xyz and rpc-mainnet.monadinfra.com allow 100.
RPC_OVERRIDE = os.environ.get("UNDERTOW_RPC", "").strip()
RPC = RPC_OVERRIDE or ("https://testnet-rpc.monad.xyz" if TESTNET else "https://rpc.monad.xyz")
EXCHANGE = "0x1964C32f0bE608E7D29302AFF5E61268E72080cc" if TESTNET else "0x34B6552d57a35a1D042CcAe1951BD1C370112a6F"
COLLATERAL = "0xa9012a055bd4e0eDfF8Ce09f960291C09D5322dC" if TESTNET else "0x00000000eFE302BEAA2b3e6e1b18d08D69a9012a"
CHAIN_ID = 10143 if TESTNET else 143
DEFAULT_PERP = 16 if TESTNET else 1
