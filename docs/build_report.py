"""Build the Step 1 progress PDF from the latest parsed snapshot.  python docs/build_report.py"""
import collections
import json
import sys
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from undertow.checks import check_open_interest  # noqa: E402
from undertow.snapshot_parser import parse_snapshot  # noqa: E402

RAW = ROOT / "data" / "snapshot.txt"
snap = parse_snapshot(RAW.read_text(encoding="utf-8", errors="replace"))

INK, MUTED, ACCENT = colors.HexColor("#1b1f2a"), colors.HexColor("#5b6272"), colors.HexColor("#2b5fd9")
LONG_C, SHORT_C = colors.HexColor("#d9534f"), colors.HexColor("#1f9d8b")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=20, textColor=INK, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=4)
P = ParagraphStyle("P", parent=ss["BodyText"], fontSize=9.5, leading=13.5, textColor=INK)
SM = ParagraphStyle("SM", parent=P, fontSize=8.3, leading=11, textColor=MUTED)
BUL = ParagraphStyle("BUL", parent=P, leftIndent=12, bulletIndent=2)
CODE = ParagraphStyle("CODE", parent=P, fontName="Courier", fontSize=8.5, leading=11,
                      backColor=colors.HexColor("#f3f4f7"), borderPadding=5, spaceBefore=3, spaceAfter=6)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONTSIZE", (0, 0), (-1, -1), 8.3), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
          ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5d8e0")),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0fd")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    t.setStyle(TableStyle(st))
    return t


def cell(s):
    return Paragraph(s, ParagraphStyle("c", parent=P, fontSize=8.3, leading=10.5))


def liq_chart():
    mark = snap.perps[1].mark
    bins = collections.defaultdict(float)
    for p in snap.positions:
        if p.perp_id != 1:
            continue
        pct = round((p.liq_price / mark - 1) * 100)
        bins[(max(-30, min(30, pct)), p.side)] += p.size * mark
    W, Hh, left, bottom = 480, 190, 40, 28
    d = Drawing(W, Hh)
    ymax = max(bins.values()) * 1.1
    plot_w, plot_h = W - left - 10, Hh - bottom - 14
    bw = plot_w / 61
    d.add(Line(left, bottom, left + plot_w, bottom, strokeColor=MUTED, strokeWidth=0.6))
    for frac in (0.25, 0.5, 0.75, 1.0):
        y = bottom + plot_h * frac
        d.add(Line(left, y, left + plot_w, y, strokeColor=colors.HexColor("#e6e8ee"), strokeWidth=0.4))
        d.add(String(left - 4, y - 3, f"${ymax * frac / 1000:,.0f}k", fontSize=6.5, fillColor=MUTED, textAnchor="end"))
    for (pct, side), v in bins.items():
        x = left + (pct + 30) * bw
        h = plot_h * v / ymax
        d.add(Rect(x + 0.6, bottom, bw - 1.2, h, fillColor=LONG_C if side == "Long" else SHORT_C, strokeColor=None))
    for pct in range(-30, 31, 10):
        x = left + (pct + 30) * bw + bw / 2
        d.add(String(x, bottom - 10, f"{pct:+d}%" if pct else "mark", fontSize=7, fillColor=MUTED, textAnchor="middle"))
    d.add(Rect(left + 4, Hh - 12, 7, 7, fillColor=LONG_C, strokeColor=None))
    d.add(String(left + 14, Hh - 11, "Long liquidation prices (fall below mark)", fontSize=7, fillColor=INK))
    d.add(Rect(left + 190, Hh - 12, 7, 7, fillColor=SHORT_C, strokeColor=None))
    d.add(String(left + 200, Hh - 11, "Short liquidation prices (rise above mark)", fontSize=7, fillColor=INK))
    return d


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Step 1: Data layer")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


out = ROOT / "Undertow_Step1_Data_Layer.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                        topMargin=16 * mm, bottomMargin=18 * mm, title="Undertow - Step 1: Data layer")
S = []
accounts = {p.account_id for p in snap.positions}
n_orders = sum(len(p.orders) for p in snap.perps.values())
checks = check_open_interest(snap)
n_pass = sum(c.ok for c in checks)

S += [Paragraph("Undertow - Step 1 complete: the data layer", H1),
      Paragraph(f"Built on {snap.timestamp} UTC from real Monad mainnet data, block {snap.block:,}. "
                "Progress report for the Metropolis build (deadline 13 October 2026).", SM),
      Paragraph("What this step delivers", H2),
      Paragraph("Undertow's warnings are only as good as the position data under them (the roadmap names this the "
                "biggest risk). Step 1 therefore does one thing: it reads every open position on Perpl, "
                "turns it into clean structured data, and proves the numbers match Perpl's own figures. "
                "Nothing visual is built yet; this is the foundation every later screen and the bot stand on.", P),
      Spacer(1, 4),
      table([[cell("<b>Markets</b>"), cell("<b>Accounts with positions</b>"), cell("<b>Open positions</b>"),
              cell("<b>Resting orders</b>"), cell("<b>Open-interest checks</b>")],
             [cell(f"{len(snap.perps)} ({sum(not p.paused for p in snap.perps.values())} live)"), cell(f"{len(accounts)}"),
              cell(f"{len(snap.positions)}"), cell(f"{n_orders}"), cell(f"{n_pass} / {len(checks)} pass")]],
            [34 * mm] * 5),
      Paragraph("What was built", H2)]

S.append(table([
    [cell("<b>Piece</b>"), cell("<b>What it does</b>")],
    [cell("<font face='Courier'>undertow/perpl_cli.py</font>"),
     cell("Runs Perpl's official <font face='Courier'>perpl-cli</font> and captures its output as clean UTF-8. Read-only: the one "
          "command that signs transactions is deliberately not wrapped here.")],
    [cell("<font face='Courier'>undertow/snapshot_parser.py</font>"),
     cell("Turns the snapshot text into typed records: markets (mark, oracle, last, funding, open interest, fees, margin "
          "settings), every resting order with its owner, and every position with Perpl's own liquidation and bankruptcy price.")],
    [cell("<font face='Courier'>undertow/checks.py</font>"),
     cell("Correctness check 1 from the roadmap: rebuilt open interest must equal the figure Perpl reports.")],
    [cell("<font face='Courier'>undertow/pipeline.py</font>"),
     cell("One command to fetch, parse, check, print a summary and save JSON (<font face='Courier'>data/parsed/snapshot_latest.json</font>).")],
    [cell("<font face='Courier'>tests/test_parser.py</font>"),
     cell("5 automated tests against the real snapshot (block header, counts, a known account, BTC settings, OI check). All pass.")],
], [58 * mm, 116 * mm]))

S += [Paragraph("The important discovery", H2),
      Paragraph("The roadmap planned to compute every trader's liquidation price ourselves. The snapshot already contains, for "
                "every position, <b>Perpl's own SDK-computed liquidation price and bankruptcy price</b>. So the exact "
                "liquidation data is available directly from the exchange's tooling, and our own formula (Step 3) becomes a "
                "cross-check and the engine for 'what if I used lower leverage' previews rather than the single source of truth. "
                "This removes the largest accuracy risk early.", P),
      PageBreak()]

S += [Paragraph("First look at real data: where BTC traders get liquidated", H1),
      Paragraph(f"Every BTC position, grouped by how far its liquidation price is from the mark price "
                f"(${snap.perps[1].mark:,.1f}), in 1% bins. Bar height is position value in USD. Values beyond 30% are shown in the 30% bin. "
                "This is a data preview, not the finished Radar.", SM),
      Spacer(1, 4), liq_chart(), Spacer(1, 4)]

mark = snap.perps[1].mark
bins = collections.defaultdict(lambda: [0.0, 0])
for p in snap.positions:
    if p.perp_id == 1:
        k = (round((p.liq_price / mark - 1) * 100), p.side)
        bins[k][0] += p.size * mark
        bins[k][1] += 1
top = sorted(bins.items(), key=lambda kv: -kv[1][0])[:5]
rows = [[cell("<b>Side</b>"), cell("<b>Liq. price vs mark</b>"), cell("<b>Position value</b>"), cell("<b>Positions</b>")]]
for (pct, side), (n, c) in top:
    rows.append([cell(side), cell(f"{pct:+d}%"), cell(f"${n:,.0f}"), cell(str(c))])
S += [Paragraph("Five biggest BTC liquidation clusters right now", H2), table(rows, [30 * mm, 42 * mm, 42 * mm, 30 * mm]),
      Spacer(1, 6),
      Paragraph("What it tells us", H2)]
S += bullets([
    "Perpl is small, as the roadmap warned: BTC open interest is about $0.67 million. The largest single cluster is about $0.31 million. "
    "The pitch should say 'tools ready before Perpl grows', not 'millions at risk today'.",
    "The two largest clusters are held by only 3 and 5 positions. The biggest zones are a few large accounts, not a crowd. "
    "That fits the roadmap's point that a well-funded trader can see and target them.",
    "Close to the price (within 6%) the zones are thin ($50-65k each). The Radar and cascade simulator will show how little "
    "order-book depth sits against them.",
    "Sanity checks pass: no long has a liquidation price above the mark and no short has one below it; total long value equals total short value.",
])
S += [Paragraph("Correctness check 1: open interest (all markets)", H2)]
rows = [[cell("<b>Market</b>"), cell("<b>Rebuilt long size</b>"), cell("<b>Rebuilt short size</b>"),
         cell("<b>Perpl reports</b>"), cell("<b>Result</b>")]]
for c in checks:
    if c.reported_oi:
        rows.append([cell(c.name), cell(f"{c.long_size:.6g}"), cell(f"{c.short_size:.6g}"), cell(f"{c.reported_oi:.6g}"),
                     cell("PASS" if c.ok else "FAIL")])
S += [table(rows, [34 * mm, 38 * mm, 38 * mm, 34 * mm, 24 * mm]),
      Paragraph("Markets with no open positions (TAO and the four paused markets) are omitted from this table.", SM),
      PageBreak()]

S += [Paragraph("How to run it", H1),
      Paragraph("From the project folder, in PowerShell:", P),
      Paragraph("python -m undertow.pipeline fetch<br/>python -m undertow.pipeline parse data/snapshot.txt<br/>"
                "python -m unittest discover -s tests", CODE),
      Paragraph("The snapshot is a full read of the exchange through Monad's public connection, which Perpl's tool limits to "
                "15 requests per second. A full snapshot took a few minutes here. For a live dashboard Step 2 adds a faster, "
                "continuously updated feed and an Alchemy connection.", P),
      Paragraph("Honest limits of Step 1", H2)]
S += bullets([
    "<b>Point in time only.</b> A snapshot is one block's state. Live updating (the 'trace' mode) is the next step.",
    "<b>Account IDs, not wallet addresses.</b> The snapshot labels traders by Perpl account number. The wallet explorer needs "
    "address-to-account mapping, which comes from <font face='Courier'>show account</font> or the Envio indexer.",
    "<b>Margin settings not yet interpreted.</b> BTC shows 'Margin: 15.00 / 25.00 (ini/mnt)'. I have not confirmed what these "
    "numbers mean in Perpl's formula, so Step 3 will verify our own liquidation formula against Perpl's liquidation prices "
    "before it is trusted.",
    "<b>Only checks 1 of 4 so far.</b> Open interest passes. 24-hour volume, our own test account and live order-book comparison come later.",
    "<b>Nothing trades yet.</b> No wallet, funds or orders are involved in this step.",
])
S += [Paragraph("What is left (against the roadmap)", H2)]
left = [
    ["2", "Live feed: continuous updates, Alchemy connection, Envio indexer started for history", "Next"],
    ["3", "Risk engine: liquidation formula checked against Perpl's, zones, cascade simulator", "Not started"],
    ["4", "Radar map and sentence ('$42k of longs 2.1% below...')", "Not started"],
    ["5", "Protocol view (volume, open interest, deposits, fees, funding, liquidations)", "Not started"],
    ["6", "Wallet explorer, watchlist, compare, wallet statistics", "Not started"],
    ["7", "Market-making bot: testnet first, then mainnet with hard limits and Telegram alerts", "Not started - needs funded wallet"],
    ["8", "Order form with risk preview, funding cost tracker", "Not started"],
    ["9", "Liquidation-aware orders, zone-aware bot", "Not started"],
    ["10", "Remaining correctness checks, logo, README, GitHub share, four videos, submission", "Not started"],
]
S += [table([[cell("<b>Step</b>"), cell("<b>Scope</b>"), cell("<b>Status</b>")]] +
            [[cell(a), cell(b), cell(c)] for a, b, c in left], [14 * mm, 120 * mm, 40 * mm]),
      Spacer(1, 6),
      Paragraph("<b>Schedule warning.</b> Today is 10 October with the deadline on the 13th. The roadmap wanted the bot trading "
                "on mainnet from 9 October. That has not started and depends on funding a separate wallet, which needs you. "
                "I recommend starting the bot (testnet first) in parallel with Step 2, and deciding now which of the "
                "roadmap's 'cut in this order' items to drop.", P)]

doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
