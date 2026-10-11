"""Thin wrapper around the `perpl-cli` binary (Perpl's official Rust tool).

Only read-only commands are used here. `perpl-cli order ...` is the one command
that signs transactions and is deliberately NOT wrapped in the data layer.
"""
from __future__ import annotations

import re
import shutil
import subprocess

from . import config

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class PerplCliError(RuntimeError):
    pass


def find_cli() -> str:
    path = shutil.which("perpl-cli")
    if not path:
        raise PerplCliError("perpl-cli not found on PATH. Install with: cargo install perpl-cli")
    return path


def run(args: list[str], timeout: int = 900) -> str:
    """Run perpl-cli and return stdout decoded as UTF-8 with ANSI codes removed.

    Bytes are decoded ourselves (not by the shell) so box-drawing characters
    survive on Windows.
    """
    proc = subprocess.run(
        [find_cli(), *args], capture_output=True, timeout=timeout, check=False
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0 and not out.strip():
        raise PerplCliError(proc.stderr.decode("utf-8", errors="replace").strip() or "perpl-cli failed")
    return ANSI.sub("", out)


def global_args(rpc: str | None = None, testnet: bool = False, throttle: int | None = None) -> list[str]:
    """Options shared by every command. A custom `rpc` (e.g. Alchemy) is unthrottled by default."""
    args: list[str] = []
    rpc = rpc or config.RPC_OVERRIDE or None
    if rpc:
        args += ["--rpc", rpc]
    if throttle is not None:
        args += ["--rpc-throttle", str(throttle)]
    if testnet or config.TESTNET:
        args += ["--testnet"]
    return args


def take_snapshot(block: int | None = None, rpc: str | None = None, testnet: bool = False,
                  throttle: int | None = None) -> str:
    args = global_args(rpc, testnet, throttle)
    if block is not None:
        args += ["--block", str(block)]
    args += ["snapshot"]
    return run(args)


def show_account(account: int | str, rpc: str | None = None, testnet: bool = False,
                 throttle: int | None = None) -> str:
    """One account's state at the latest block (one account per call; ~0.7 s)."""
    args = global_args(rpc, testnet, throttle) + ["--account", str(account), "--num-blocks", "1"]
    return run(args + ["show", "account"], timeout=120)


def block_events(block: int, rpc: str | None = None, testnet: bool = False,
                 throttle: int | None = None) -> str:
    """Raw events inside one block (~1.5 s on the public RPC)."""
    return run(global_args(rpc, testnet, throttle) + ["block", str(block)], timeout=120)
