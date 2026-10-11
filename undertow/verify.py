"""Correctness checks: our numbers against Perpl's own public REST API (an independent source).

    python -m undertow.verify            # run every check, print a table, save data/verify.json

Checks (roadmap "How we prove the numbers are right"):
  ticker   our CLI read of mark price and open interest  vs  REST ticker
  book     order book levels from perpl-cli               vs  REST L2 book
  volume   Envio-indexed volume per whole hour            vs  REST candles
  funding  Envio-indexed funding events                   vs  REST funding series
  formula  our liquidation formula                        vs  Perpl's own liquidation prices (snapshot)
Not done: "own test account" (needs a funded account).

REST base: https://app.perpl.xyz/api (mainnet). Testnet uses https://testnet.perpl.xyz/api.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import asdict, dataclass

from . import config, envio_client, perpl_cli, risk

BASE = "https://testnet.perpl.xyz/api/v1" if config.TESTNET else "https://app.perpl.xyz/api/v1"
NON_ASCII = re.compile(r"[^\x00-\x7f]+")


@dataclass
class Check:
    name: str
    status: str  # pass | fail | skipped
    detail: str


def rest(path: str):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "undertow-verify"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _market(perp_id: int) -> dict:
    ctx = rest("/pub/context")
    return next(m for m in ctx["markets"] if m["perpetual_id"] == perp_id)


def parse_cli_book(text: str) -> dict:
    """Parse `perpl-cli show book` into mark, open interest (size) and aggregated levels (price, size, orders)."""
    t = NON_ASCII.sub("|", text)
    mark = re.search(r"Mark:\s*([\d.]+)", t)
    oi = re.search(r"Open Interest:\s*([\d.]+)", t)
    levels = []
    for ln in t.splitlines():
        cells = [c.strip() for c in ln.split("|")[1:-1]]
        if len(cells) >= 5 and re.fullmatch(r"[\d.]+", cells[0]) and re.fullmatch(r"[\d.]+", cells[1]) and cells[3].isdigit():
            levels.append((float(cells[0]), float(cells[1]), int(cells[3])))
    return {"mark": float(mark.group(1)) if mark else None, "oi": float(oi.group(1)) if oi else None, "levels": levels}


def check_ticker_and_book(perp_id: int) -> list[Check]:
    mk = _market(perp_id)
    pdec, sdec = mk["config"]["price_decimals"], mk["config"]["size_decimals"]
    book = rest(f"/market-data/{mk['id']}/book?levels=100")
    tick = rest(f"/market-data/{mk['id']}/ticker")["d"][str(mk["id"])]
    # Read our book AT THE SAME BLOCK the REST book reports, so a moving market cannot cause a false mismatch.
    # Measured 2026-10-11: the REST book labelled block b equals the contract state read at block b - 1 (identical in all
    # 20 levels); reading at b itself differs by a block's worth of order changes.
    at_block = book["at"]["b"] - 1
    try:
        ours = parse_cli_book(perpl_cli.run(perpl_cli.global_args() + ["--block", str(at_block), "--perp", str(perp_id), "--num-blocks", "1",
                                                                         "show", "book"], timeout=120))
        same_block = True
    except Exception:  # state at that block unavailable: fall back to the latest block
        ours = parse_cli_book(perpl_cli.run(perpl_cli.global_args() + ["--perp", str(perp_id), "--num-blocks", "1", "show", "book"], timeout=120))
        same_block = False
    out = []
    # --- ticker: mark and open interest
    rest_mark, rest_oi = tick["mrk"] / 10 ** pdec, tick["oi"] / 10 ** sdec
    dm = abs(ours["mark"] / rest_mark - 1) * 100
    do = abs(ours["oi"] / rest_oi - 1) * 100
    out.append(Check("ticker: mark price", "pass" if dm < 0.1 else "fail", f"ours {ours['mark']:,.2f} vs Perpl {rest_mark:,.2f} ({dm:.3f}% apart)"))
    out.append(Check("ticker: open interest", "pass" if do < 1.0 else "fail", f"ours {ours['oi']:,.5f} vs Perpl {rest_oi:,.5f} ({do:.3f}% apart)"))
    # --- book: top levels each side; the two reads are a few seconds apart, so some movement is expected
    best_ask = book["ask"][0]["p"] / 10 ** pdec
    ours_asks = sorted([l for l in ours["levels"] if l[0] >= best_ask - 1e-9])[:10]
    ours_bids = sorted([l for l in ours["levels"] if l[0] < best_ask - 1e-9], reverse=True)[:10]
    rest_asks = [(x["p"] / 10 ** pdec, x["s"] / 10 ** sdec, x["o"]) for x in book["ask"][:10]]
    rest_bids = [(x["p"] / 10 ** pdec, x["s"] / 10 ** sdec, x["o"]) for x in book["bid"][:10]]

    def match(a, b):
        sb = {round(p, 8): (s, o) for p, s, o in b}
        same_price = sum(1 for p, s, o in a if round(p, 8) in sb)
        exact = sum(1 for p, s, o in a if round(p, 8) in sb and abs(sb[round(p, 8)][0] - s) < 10 ** -sdec * 1.5 and sb[round(p, 8)][1] == o)
        return same_price, exact, len(a)
    sp_a, ex_a, n_a = match(ours_asks, rest_asks)
    sp_b, ex_b, n_b = match(ours_bids, rest_bids)
    total, same, exact = n_a + n_b, sp_a + sp_b, ex_a + ex_b
    ok = total > 0 and (exact == total if same_block else same / total >= 0.7)
    out.append(Check("order book: top 10 levels per side", "pass" if ok else "fail",
                     f"{same}/{total} price levels present in both, {exact}/{total} identical in size and order count "
                     + (f"(both read at block {at_block:,})" if same_block else "(NOT the same block: two reads a few seconds apart, so some movement is expected)")))
    return out


def check_volume(perp_id: int, hours: int = 12) -> Check:
    mk = _market(perp_id)
    q = envio_client.query("""query($c:Int!,$p:Int!){ Trade(where:{chainId:{_eq:$c},perpId:{_eq:$p}}, order_by:{timestamp:asc}, limit:1){ timestamp }
        last:Trade(where:{chainId:{_eq:$c},perpId:{_eq:$p}}, order_by:{timestamp:desc}, limit:1){ timestamp } }""", {"c": config.CHAIN_ID, "p": perp_id})
    if not q or not q["Trade"]:
        return Check("volume: indexer vs Perpl candles", "skipped", "indexer has no trades yet")
    first, last = q["Trade"][0]["timestamp"], q["last"][0]["timestamp"]
    # Prefer whole hours; if the indexer has not covered one yet, fall back to whole minutes (Perpl serves 60 s candles too).
    for res, unit in ((3600, "hour"), (60, "minute")):
        start = (first // res + 1) * res  # first fully covered window
        end = (last // res) * res - res  # last window fully before the newest trade
        hrs = list(range(start, end + 1, res))[-(hours if res == 3600 else 20):]
        if hrs:
            break
    if not hrs:
        return Check("volume: indexer vs Perpl candles", "skipped", "indexer window does not yet cover one whole minute")
    candles = rest(f"/market-data/{mk['id']}/candles/{res}/{hrs[0] * 1000}-{(hrs[-1] + res) * 1000}")["d"]
    by_t = {c["t"] // 1000: c for c in candles}
    rows, worst = [], 0.0
    for h in hrs:
        a = envio_client.query("""query($c:Int!,$p:Int!,$a:Int!,$b:Int!){ Trade_aggregate(where:{chainId:{_eq:$c},perpId:{_eq:$p},timestamp:{_gte:$a,_lt:$b}}){
            aggregate{ count sum{ notionalUsd } } } }""", {"c": config.CHAIN_ID, "p": perp_id, "a": h, "b": h + res})
        ours = float(a["Trade_aggregate"]["aggregate"]["sum"]["notionalUsd"] or 0)
        c = by_t.get(h)
        theirs = int(c["v"]) / 1e6 if c else 0.0  # a missing candle means no trades in that window
        diff = abs(ours - theirs) / max(theirs, 1e-9) * 100 if theirs else (0.0 if ours == 0 else 100.0)
        worst = max(worst, diff)
        rows.append((h, ours, theirs, diff, a["Trade_aggregate"]["aggregate"]["count"], c["n"] if c else 0))
    fmt = "%m-%d %H:00" if res == 3600 else "%m-%d %H:%M"
    detail = "; ".join(f"{time.strftime(fmt, time.gmtime(h))}Z ours ${o:,.0f} vs Perpl ${t:,.0f} ({d:.2f}%, {n1} fills vs {n2} trades)"
                       for h, o, t, d, n1, n2 in rows[-4:])
    if not rows:
        return Check("volume: indexer vs Perpl candles", "skipped", "no overlapping candles")
    return Check(f"volume: {len(rows)} whole {unit}s, worst difference {worst:.2f}%", "pass" if worst < 1.0 else "fail", detail)


def check_funding(perp_id: int) -> Check:
    mk = _market(perp_id)
    d = envio_client.query("""query($c:Int!,$p:Int!){ FundingEvent(where:{chainId:{_eq:$c},perpId:{_eq:$p}}, order_by:{fundingBlock:asc}){ fundingBlock actualRatePct100k } }""",
                           {"c": config.CHAIN_ID, "p": perp_id})
    if not d or not d["FundingEvent"]:
        return Check("funding: indexer vs Perpl series", "skipped", "indexer has no funding events yet")
    ev = d["FundingEvent"]
    t0 = int(time.time() * 1000) - 72 * 3600 * 1000
    series = rest(f"/market-data/{mk['id']}/funding/{t0}-{int(time.time() * 1000)}")["d"]
    theirs = {x["feb"]: x["rate"] for x in series}
    pairs = [(e["fundingBlock"], e["actualRatePct100k"], theirs[e["fundingBlock"]]) for e in ev if e["fundingBlock"] in theirs]
    if not pairs:
        return Check("funding: indexer vs Perpl series", "skipped", "no common funding blocks")
    # indexer value is in units of 1e-5, Perpl REST in units of 1e-6 (checked: the same 4e-5 fraction is 4 and 40)
    bad = [(b, a, r) for b, a, r in pairs if abs(a * 10 - r) > 1]
    return Check(f"funding: {len(pairs)} events compared", "pass" if not bad else "fail",
                 f"{len(pairs) - len(bad)} match (indexer x10 equals REST); units: indexer 1e-5, REST 1e-6; first: block {pairs[0][0]} "
                 f"indexer {pairs[0][1]} vs REST {pairs[0][2]}")


def check_formula() -> Check:
    f = risk.verify_formula(risk.load_latest())
    return Check("formula: our liquidation price vs Perpl's", "pass" if f.within_tolerance == f.total else "fail",
                 f"{f.within_tolerance}/{f.total} positions match (relative error <= 0.1% or within the 6-decimal print)")


def run_all(perp_id: int | None = None) -> list[Check]:
    perp_id = perp_id or config.DEFAULT_PERP
    checks: list[Check] = []
    for fn in (lambda: check_ticker_and_book(perp_id), lambda: [check_volume(perp_id)], lambda: [check_funding(perp_id)],
               lambda: [check_formula()]):
        try:
            checks += fn()
        except Exception as e:  # one failing source must not hide the other checks
            checks.append(Check("(check could not run)", "fail", f"{type(e).__name__}: {e}"))
    checks.append(Check("own test account: size, entry and liquidation vs Perpl's Positions table", "skipped",
                        "needs a funded account on Perpl (not available)"))
    return checks


def main() -> None:
    checks = run_all()
    for c in checks:
        print(f"[{c.status.upper():7}] {c.name}\n          {c.detail}")
    out = config.DATA / "verify.json"
    out.write_text(json.dumps({"ran_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "checks": [asdict(c) for c in checks]}, indent=1),
                   encoding="utf-8")


if __name__ == "__main__":
    main()
