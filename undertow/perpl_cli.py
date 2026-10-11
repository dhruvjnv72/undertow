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


_rpc_index = 0  # which of config.RPC_LIST is currently preferred; advanced when one fails


def current_rpc() -> str:
    return config.RPC_LIST[_rpc_index % len(config.RPC_LIST)]


def _with_rpc(args: list[str], rpc: str) -> list[str]:
    a = list(args)
    if "--rpc" in a:
        a[a.index("--rpc") + 1] = rpc
    else:
        a = ["--rpc", rpc] + a
    return a


def run(args: list[str], timeout: int = 900, attempt_timeout: int | None = None) -> str:
    """Run perpl-cli and return stdout decoded as UTF-8 with ANSI codes removed.

    Bytes are decoded ourselves (not by the shell) so box-drawing characters survive on Windows. If the call fails or hangs,
    the next public RPC endpoint is tried (up to every endpoint once). A custom `--rpc` in `args` is tried first.
    """
    global _rpc_index
    last_err = "perpl-cli failed"
    explicit = args[args.index("--rpc") + 1] if "--rpc" in args else None
    endpoints = []
    for u in ([explicit] if explicit else []) + [current_rpc()] + config.RPC_LIST:
        if u not in endpoints:
            endpoints.append(u)
    for rpc in endpoints:
        try:
            proc = subprocess.run([find_cli(), *_with_rpc(args, rpc)], capture_output=True,
                                  timeout=attempt_timeout or (150 if timeout >= 600 else min(timeout, 60)), check=False)  # a full snapshot normally takes 35-75 s
        except subprocess.TimeoutExpired:
            last_err = f"timed out on {rpc}"
            _rpc_index += 1
            continue
        out = proc.stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not out.strip():
            last_err = proc.stderr.decode("utf-8", errors="replace").strip() or "perpl-cli failed"
            if "--help" in args or "unknown perpetual" in last_err or "invalid" in last_err.lower():
                raise PerplCliError(last_err)  # a usage error: another endpoint will not help
            _rpc_index += 1
            continue
        return ANSI.sub("", out)
    raise PerplCliError(last_err)


def global_args(rpc: str | None = None, testnet: bool = False, throttle: int | None = None) -> list[str]:
    """Options shared by every command. A custom `rpc` (e.g. Alchemy) is unthrottled by default."""
    args: list[str] = []
    rpc = rpc or config.RPC_OVERRIDE or None  # run() adds the preferred endpoint itself when none is given
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
