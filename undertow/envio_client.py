"""Read the Envio indexer's data (local Hasura GraphQL) for the Protocol view.

The indexer (indexer/, run with `npx envio dev` inside WSL) serves GraphQL at http://localhost:8080/v1/graphql with
the local admin secret "testing". If it is not running or has not synced yet, every function returns None and the
dashboard shows what it can without it.
"""
from __future__ import annotations

import json
import time
import urllib.request

from . import config

URL = "http://localhost:8080/v1/graphql"
SECRET = "testing"  # Envio's documented local-development admin secret; the endpoint is localhost-only
_cache: dict = {}


def query(gql: str, variables: dict | None = None, timeout: int = 10) -> dict | None:
    req = urllib.request.Request(URL, json.dumps({"query": gql, "variables": variables or {}}).encode(),
                                 {"Content-Type": "application/json", "x-hasura-admin-secret": SECRET})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return None
    return out.get("data") if "errors" not in out else None


NUMERIC = ("volumeUsd", "makerFeesUsd", "amountUsd", "makerFeeUsd", "liquidatedUsd", "depositsUsd", "withdrawalsUsd", "liqPrice", "markPrice",
           "liquidatedSize", "remainingSize", "notionalUsd")


def _num(rows: list[dict]) -> list[dict]:
    """Hasura returns Postgres numerics as strings; convert the ones we do arithmetic on."""
    return [{k: (float(v) if k in NUMERIC and v is not None else v) for k, v in r.items()} for r in rows]


def wallet_activity(account_id: int) -> dict | None:
    """What the indexer has seen for one account in its window. It records only the RESTING side of trades, so taker
    activity is not included; deposits, withdrawals and liquidations are complete for the window."""
    cid = config.CHAIN_ID
    d = query("""query($c:Int!,$a:Int!){
      Trade_aggregate(where:{chainId:{_eq:$c}, makerAccountId:{_eq:$a}}){ aggregate{ count sum{ notionalUsd makerFeeUsd } } }
      CashFlow(where:{chainId:{_eq:$c}, accountId:{_eq:$a}}, order_by:{timestamp:desc}, limit:50){ kind amountUsd timestamp }
      Liquidation(where:{chainId:{_eq:$c}, accountId:{_eq:$a}}, order_by:{timestamp:desc}, limit:10){
        perpName side liqPrice liquidatedSize notionalUsd timestamp }
      DailyStats(where:{chainId:{_eq:$c}}, order_by:{date:asc}, limit:1){ date }
    }""", {"c": cid, "a": account_id})
    if not d:
        return None
    agg = d["Trade_aggregate"]["aggregate"]
    flows = _num(d["CashFlow"])
    return {"window_start": d["DailyStats"][0]["date"] if d["DailyStats"] else None,
            "maker_trades": agg["count"], "maker_volume_usd": float((agg["sum"] or {}).get("notionalUsd") or 0),
            "maker_fees_usd": float((agg["sum"] or {}).get("makerFeeUsd") or 0),
            "deposits_usd": sum(f["amountUsd"] for f in flows if f["kind"] == "deposit"),
            "withdrawals_usd": sum(f["amountUsd"] for f in flows if f["kind"] == "withdrawal"),
            "liquidations": _num(d["Liquidation"])}


def stats(max_age_s: float = 20.0) -> dict | None:
    """Volume, fees, liquidations, flows and recent liquidations for the current network, or None if unavailable."""
    hit = _cache.get("stats")
    if hit and time.time() - hit[0] < max_age_s:
        return hit[1]
    cid = config.CHAIN_ID
    d = query("""query($c:Int!){
      DailyStats(where:{chainId:{_eq:$c}}, order_by:{date:desc}, limit:400){
        date volumeUsd trades makerFeesUsd liquidations liquidatedUsd deleverages depositsUsd withdrawalsUsd newAccounts }
      Liquidation(where:{chainId:{_eq:$c}}, order_by:{timestamp:desc}, limit:15){
        perpName accountId side liqPrice markPrice liquidatedSize remainingSize notionalUsd onOrderBook timestamp block }
      Account_aggregate(where:{chainId:{_eq:$c}}){ aggregate{ count } }
    }""", {"c": cid})
    if not d or not d.get("DailyStats"):
        return None
    days = _num(d["DailyStats"])  # newest first

    def window(n: int, key: str) -> float:
        return sum(x[key] for x in days[:n])

    out = {
        "days_indexed": len(days), "latest_day": days[0]["date"], "earliest_day": days[-1]["date"],
        "volume_1d": days[0]["volumeUsd"], "volume_7d": window(7, "volumeUsd"), "volume_30d": window(30, "volumeUsd"),
        "volume_all": window(len(days), "volumeUsd"), "fees_30d": window(30, "makerFeesUsd"),
        "fees_all": window(len(days), "makerFeesUsd"), "trades_all": int(window(len(days), "trades")),
        "liquidations_30d": int(window(30, "liquidations")), "liquidated_usd_30d": window(30, "liquidatedUsd"),
        "liquidations_all": int(window(len(days), "liquidations")),
        "deposits_30d": window(30, "depositsUsd"), "withdrawals_30d": window(30, "withdrawalsUsd"),
        "net_inflow_30d": window(30, "depositsUsd") - window(30, "withdrawalsUsd"),
        "accounts_created": d["Account_aggregate"]["aggregate"]["count"],
        "daily": list(reversed(days[:60])), "recent_liquidations": _num(d["Liquidation"]),
    }
    _cache["stats"] = (time.time(), out)
    return out
