"""Build the Step 2 progress PDF.  python docs/build_report_step2.py"""
import json
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
status = json.loads((D / "status.json").read_text())
accts = json.loads((D / "accounts.json").read_text())
snap = json.loads((D / "parsed" / "snapshot_latest.json").read_text())
hist = [json.loads(line) for line in (D / "history" / "markets.jsonl").read_text().splitlines()]
n_cycles = len({h["block"] for h in hist})

INK, MUTED, ACCENT = colors.HexColor("#1b1f2a"), colors.HexColor("#5b6272"), colors.HexColor("#2b5fd9")
BAR = colors.HexColor("#2b5fd9")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=20, textColor=INK, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=4)
P = ParagraphStyle("P", parent=ss["BodyText"], fontSize=9.5, leading=13.5, textColor=INK)
SM = ParagraphStyle("SM", parent=P, fontSize=8.3, leading=11, textColor=MUTED)
BUL = ParagraphStyle("BUL", parent=P, leftIndent=12, bulletIndent=2)
CODE = ParagraphStyle("CODE", parent=P, fontName="Courier", fontSize=8.5, leading=11,
                      backColor=colors.HexColor("#f3f4f7"), borderPadding=5, spaceBefore=3, spaceAfter=6)
C = ParagraphStyle("c", parent=P, fontSize=8.3, leading=10.5)


def cell(s):
    return Paragraph(s, C)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5d8e0")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0fd")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def balance_chart():
    pos_ids = {str(p["account_id"]) for p in snap["positions"]}
    rows = sorted(((a["balance"], k) for k, a in accts.items() if k in pos_ids), reverse=True)[:10]
    W, H, left, bottom = 480, 170, 62, 22
    d = Drawing(W, H)
    mx = rows[0][0] * 1.08
    bh = (H - bottom - 8) / len(rows)
    for i, (bal, k) in enumerate(rows):
        y = H - 8 - (i + 1) * bh + 2
        w = (W - left - 70) * bal / mx
        d.add(Rect(left, y, w, bh - 5, fillColor=BAR, strokeColor=None))
        d.add(String(left - 5, y + 2.5, f"#{k}", fontSize=7.5, fillColor=INK, textAnchor="end"))
        d.add(String(left + w + 4, y + 2.5, f"${bal:,.0f}", fontSize=7.5, fillColor=MUTED))
    d.add(Line(left, bottom - 2, left, H - 6, strokeColor=MUTED, strokeWidth=0.5))
    return d, rows


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Step 2: Live feed")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


chart, top = balance_chart()
pos_ids = {str(p["account_id"]) for p in snap["positions"]}
total_bal = sum(a["balance"] for k, a in accts.items() if k in pos_ids)
n_pos_accts = len(pos_ids)
out = ROOT / "Undertow_Step2_Live_Feed.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=18 * mm, title="Undertow - Step 2: Live feed")
S = [Paragraph("Undertow - Step 2 complete: the live feed", H1),
     Paragraph(f"Built and tested on real Monad mainnet data, {status['written_at']} UTC (block {status['block']:,}).", SM),
     Paragraph("What this step delivers", H2),
     Paragraph("Step 1 could read Perpl once. Step 2 keeps that picture fresh, remembers how it changes, and says who each "
               "account is. It also settles a design question that decides how 'live' the product can honestly claim to be, "
               "by measuring how fast each way of reading Perpl really is.", P),
     Spacer(1, 4),
     table([[cell("<b>Refresh cycle</b>"), cell("<b>Accounts mapped to wallet address</b>"), cell("<b>History rows saved</b>"),
             cell("<b>Open-interest checks</b>"), cell("<b>Tests</b>")],
            [cell(f"{status['total_seconds']} s"),
             cell(f"{status['accounts_mapped_to_address']} of {status['accounts']}"),
             cell(f"{len(hist)} ({n_cycles} snapshots)"),
             cell(f"{status['oi_checks_passed']} / {status['oi_checks_total']} pass"),
             cell("13 / 13 pass")]], [32 * mm, 44 * mm, 36 * mm, 34 * mm, 28 * mm]),
     Paragraph("What was built", H2),
     table([[cell("<b>Piece</b>"), cell("<b>What it does</b>")],
            [cell("<font face='Courier'>refresher.py</font>"),
             cell("Loop that takes a full snapshot, parses and checks it, writes the latest file atomically (a screen never "
                  "reads a half-written file), appends one row per market to a history file, and writes a status file with "
                  "timings. Accepts your own RPC address (for example Alchemy).")],
            [cell("<font face='Courier'>onchain.py</font>"),
             cell("Reads Perpl's contract directly with batched requests, using only Python's standard library. Retries "
                  "politely when the public server says 'too many requests'.")],
            [cell("<font face='Courier'>accounts.py</font>"),
             cell("Registry of account number, wallet address and balance, cached on disk and refreshed hourly. This is what "
                  "the Wallet explorer needs to look up a trader by address.")],
            [cell("<font face='Courier'>blocks.py</font>"),
             cell("Decodes the raw events inside a block (trades, fills, orders, liquidations) and lists which accounts a "
                  "block touched.")],
            [cell("<font face='Courier'>abi/Exchange.json</font>"),
             cell("Perpl's contract definition, copied from the official SDK, ready for the Envio indexer.")]],
           [40 * mm, 134 * mm]),
     Paragraph("Measurements that decided the design", H2),
     table([[cell("<b>Way of reading Perpl</b>"), cell("<b>Measured speed</b>"), cell("<b>Verdict</b>")],
            [cell("Full snapshot (every account, every order)"), cell("33 to 48 s"),
             cell("<b>Used.</b> Fast enough for a screen refreshed under a minute.")],
            [cell("Replay one block's events"), cell("about 1.5 s per block"),
             cell("Not used for state. Monad makes a block every 0.4 s, so replay falls behind.")],
            [cell("<font face='Courier'>show account</font> for one trader"), cell("about 11 s"),
             cell("Too slow for lists. Kept only for one-wallet detail.")],
            [cell("Direct contract read, 10 accounts per request"), cell("432 accounts in 41 s"),
             cell("<b>Used.</b> Replaces roughly 80 minutes of CLI calls.")]],
           [62 * mm, 40 * mm, 72 * mm]),
     PageBreak(),
     Paragraph("What the new data shows", H1),
     Paragraph("Balances of the accounts that currently hold positions (AUSD, the deposit currency). "
               "This is deposit size, not position size.", SM),
     Spacer(1, 4), chart,
     Paragraph("Findings", H2)]
S += bullets([
    f"<b>Money is concentrated.</b> The {n_pos_accts} accounts with positions hold ${total_bal:,.0f} in total. The largest, "
    f"account #{top[0][1]}, holds ${top[0][0]:,.0f}, about {100 * top[0][0] / total_bal:.0f}% of it. For a liquidation map "
    "this means one account's choices can move the picture, which fits the 'big zones are visible targets' story but also "
    "means we must label whale-driven zones honestly.",
    "<b>Address lookup works.</b> Every account with a position now has a wallet address. Spot checks on four accounts "
    "matched Perpl's own CLI exactly (address and balance). Wallet explorer search by address is unblocked.",
    "<b>A full refresh needs no special connection.</b> About 45 seconds on the free public server. An Alchemy key would "
    "cut this sharply because it removes the 15-requests-per-second limit, but nothing is blocked without it.",
])
S += [Paragraph("Important discovery for the cascade simulator", H2),
      Paragraph("The roadmap describes a liquidation as forced selling into the order book. Perpl's contract is more complex. "
                "Its events include <font face='Courier'>PositionLiquidated</font> (with an "
                "<font face='Courier'>onOrderBook</font> flag), <font face='Courier'>PositionDeleveraged</font> "
                "(auto-deleveraging against opposing positions) and a <font face='Courier'>BuyToLiquidate</font> process in "
                "which a buyer takes over a position, with a slippage limit. So a liquidated position does not always dump "
                "into the book as a market sale.", P),
      Paragraph("Consequence: in Step 3 I will read the SDK's liquidation logic before writing the cascade simulator, and the "
                "simulator will say plainly which liquidations it assumes hit the book. If that is only partly true, the pitch "
                "wording ('forced selling pushes the price down') must be softened to match. A claim the contract does not "
                "support would hurt the 'trustworthy risk screen' that Track 1 judges look for.", P),
      Paragraph("How to run it", H2),
      Paragraph("python -m undertow.refresher --once<br/>python -m undertow.refresher --pause 5<br/>"
                "python -m undertow.refresher --rpc https://YOUR-ALCHEMY-URL --pause 0<br/>"
                "python -m unittest discover -s tests", CODE),
      PageBreak(),
      Paragraph("Honest limits and open items", H1)]
S += bullets([
    "<b>Fresh to about a minute, not to the block.</b> The data is a rolling snapshot, so the screen should say 'updated N "
    "seconds ago'. The roadmap's 'about 0.4 seconds' claim for liquidation-aware orders is not yet supported. Closing that "
    "gap needs an event feed on a faster connection, planned for Step 9.",
    "<b>The Envio indexer is not built.</b> Docker and the Envio tool are not installed on this machine, so I could not test "
    "one. The contract definition is saved and ready. This needs a decision from you: install Docker Desktop and Envio, use "
    "Envio's hosted option, or fall back to our own history file for charts. The Envio prize ($1,000) requires a real indexer.",
    "<b>No Alchemy key yet.</b> Optional. It would speed up everything, and the Alchemy sponsor offers $1,000 in credits.",
    "<b>Liquidation events are decoded but untested on a real liquidation.</b> The block I captured had none. The event names "
    "come from the contract definition and a unit test covers name matching, but I have not seen a real one parsed yet.",
    f"<b>History has only just started.</b> {n_cycles} snapshots so far, so there is nothing to chart over days yet. Leave the "
    "refresher running to build it. 24-hour volume needs trade history, which is the Envio job.",
    "<b>Public server limits.</b> It rejects large request bursts (we hit this and added backoff). Heavy use needs your own RPC.",
])
left = [["3", "Risk engine: read SDK liquidation rules, verify our formula against Perpl's liquidation prices, zones, "
              "cascade simulator", "Next"],
        ["4", "Radar map and plain-language sentence", "Not started"],
        ["5", "Protocol view (volume, deposits, fees, funding, liquidations)", "Not started; needs Envio or trade history"],
        ["6", "Wallet explorer, watchlist, compare, trader statistics", "Unblocked by address registry"],
        ["7", "Market-making bot: testnet, then mainnet with hard limits and Telegram", "Not started; needs funded wallet from you"],
        ["8", "Order form with risk preview, funding cost tracker", "Not started"],
        ["9", "Liquidation-aware orders, zone-aware bot", "Not started"],
        ["10", "Envio indexer, remaining correctness checks, logo, README, GitHub share, four videos, submission",
         "Not started"]]
S += [Paragraph("What is left", H2),
      table([[cell("<b>Step</b>"), cell("<b>Scope</b>"), cell("<b>Status</b>")]] +
            [[cell(a), cell(b), cell(c)] for a, b, c in left], [14 * mm, 106 * mm, 54 * mm]),
      Spacer(1, 6),
      Paragraph("<b>Schedule.</b> Three days remain to the 13 October deadline, and the bot has still not started. It is the "
                "one piece that needs days of real activity and your funding step. I recommend we do the bot on testnet "
                "immediately after Step 3.", P)]
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
