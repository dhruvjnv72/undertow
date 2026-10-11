"""Build the Step 4 progress PDF (bot, Protocol view, watchlist).  python docs/build_report_step4.py"""
import json
import sys
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from undertow import risk, server  # noqa: E402
from undertow.bot import Params  # noqa: E402

D = ROOT / "data"
proto = server.api_protocol()
events = [json.loads(line) for line in (D / "bot" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
cycles = [e for e in events if e["event"] == "cycle"]
fills = [e for e in events if e["event"] == "fill"]
state = json.loads((D / "bot" / "state.json").read_text(encoding="utf-8"))
P = Params()

INK, MUTED, ACCENT = colors.HexColor("#1b1f2a"), colors.HexColor("#5b6272"), colors.HexColor("#2b5fd9")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=20, textColor=INK, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=4)
P_ = ParagraphStyle("P", parent=ss["BodyText"], fontSize=9.5, leading=13.5, textColor=INK)
SM = ParagraphStyle("SM", parent=P_, fontSize=8.3, leading=11, textColor=MUTED)
BUL = ParagraphStyle("BUL", parent=P_, leftIndent=12, bulletIndent=2)
CODE = ParagraphStyle("CODE", parent=P_, fontName="Courier", fontSize=8.5, leading=11,
                      backColor=colors.HexColor("#f3f4f7"), borderPadding=5, spaceBefore=3, spaceAfter=6)
C = ParagraphStyle("c", parent=P_, fontSize=8.3, leading=10.5)


def cell(s):
    return Paragraph(str(s), C)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5d8e0")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0fd")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def oi_chart(hist):
    W, H, left, bottom = 480, 170, 52, 24
    d = Drawing(W, H)
    ys = [p["oi_usd"] for p in hist]
    lo, hi = min(ys) * 0.99, max(ys) * 1.01
    n = len(hist)
    x = lambda i: left + i / max(n - 1, 1) * (W - left - 10)  # noqa: E731
    y = lambda v: bottom + (v - lo) / ((hi - lo) or 1) * (H - bottom - 12)  # noqa: E731
    for frac in (0, 0.5, 1):
        v = lo + (hi - lo) * frac
        d.add(Line(left, y(v), W - 10, y(v), strokeColor=colors.HexColor("#e6e8ee"), strokeWidth=0.4))
        d.add(String(left - 4, y(v) - 3, risk.fmt_usd(v), fontSize=7, fillColor=MUTED, textAnchor="end"))
    for i in range(n - 1):
        d.add(Line(x(i), y(ys[i]), x(i + 1), y(ys[i + 1]), strokeColor=ACCENT, strokeWidth=1.6))
    d.add(String(left, 6, hist[0]["ts"][5:16], fontSize=7, fillColor=MUTED))
    d.add(String(W - 10, 6, hist[-1]["ts"][5:16], fontSize=7, fillColor=MUTED, textAnchor="end"))
    return d


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Step 4: bot, Protocol view, watchlist")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


out = ROOT / "Undertow_Step4_Bot_and_Protocol.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=18 * mm, title="Undertow - Step 4: bot, Protocol view, watchlist")
S = [Paragraph("Undertow - Step 4: the bot, Protocol view and watchlist", H1),
     Paragraph("Built and tested on 10 October 2026. The bot runs in paper mode only; nothing here has traded real money.", SM),
     Paragraph("What was added", H2),
     table([[cell("<b>Piece</b>"), cell("<b>Status</b>")],
            [cell("<b>Market-making bot</b> (<font face='Courier'>undertow/bot.py</font>)"),
             cell("Built. <b>Paper mode</b> works and ran on live data. The live executor is written but <b>locked and never run against the chain</b>.")],
            [cell("<b>Bot tab</b> on the dashboard"), cell("Built. Shows mode, equity, position, quotes and recent events.")],
            [cell("<b>Protocol tab</b>"), cell("Built, partly. Open interest, accounts, balances, per-market positions and collateral. "
                                               "No volume, fees, deposits or liquidation history (needs event history).")],
            [cell("<b>Watchlist and compare</b> (Wallets tab)"), cell("Built. Save wallets, see them side by side. Saved in your browser only.")]],
           [62 * mm, 112 * mm]),
     Paragraph("How to run", H2),
     Paragraph("python -m undertow.server --refresh<br/>python -m undertow.bot --paper --cycles 10 --interval 45", CODE),
     Paragraph("Keep the server running: the bot reads the data it keeps fresh, and pulls its quotes if the data gets older than "
               "two minutes. Open the <b>Bot</b> tab at http://localhost:8000 to watch it.", P_),
     Paragraph("What the bot does", H1),
     Paragraph("Every cycle it keeps one buy order slightly below Perpl's mark price and one sell order slightly above, and earns the gap "
               "when both fill. It only places <b>post-only</b> orders, so it always pays the low maker fee (0.0045%) and never the high taker fee.", P_),
     table([[cell("<b>Rule</b>"), cell("<b>Setting</b>"), cell("<b>What happens</b>")],
            [cell("Gap"), cell(f"{P.half_spread_bps:g} bps each side"), cell("Quotes sit this far from the mark price. The round-trip fee is under 1 bp, so the gap is the profit.")],
            [cell("Order size"), cell(f"${P.size_usd:g} per side"), cell("Small on purpose, for a $50 to $100 account.")],
            [cell("Inventory lean"), cell(f"up to {P.inventory_skew_bps:g} bps"), cell("If it holds a long, both quotes shift down so it is more likely to sell (and the reverse).")],
            [cell("Position limit"), cell(f"${P.max_position_usd:g}"), cell("It stops quoting the side that would grow the position beyond this.")],
            [cell("Zone near"), cell(f"zone of ${P.zone_min_usd:,.0f}+ within {P.zone_pullback_pct}%"), cell(f"Gap widens x{P.widen_factor}.")],
            [cell("Zone near and book thin"), cell(f"zone value over {P.pull_ratio}x resting orders"), cell("<b>Pulls all quotes.</b> Uses the Radar's zone data.")],
            [cell("Stale data"), cell(f"older than {P.data_stale_s:.0f}s"), cell("Pulls quotes.")],
            [cell("Daily loss"), cell(f"${P.max_daily_loss_usd:g}"), cell("<b>Halts for good</b> and sends an alert.")],
            [cell("Leverage"), cell(f"above {P.max_leverage:g}x"), cell("<b>Halts.</b>")],
            [cell("Own liquidation"), cell(f"closer than {P.min_liq_distance_pct:g}%"), cell("<b>Halts.</b>")],
            [cell("Kill switch"), cell("a file named KILL in data/bot"), cell("<b>Halts</b> on the next cycle.")]],
           [32 * mm, 52 * mm, 90 * mm]),
     Paragraph("Built-in safety", H2)]
S += bullets([
    "<b>Every order expires by itself</b> about 60 seconds after it is placed (150 blocks). If the program crashes or your laptop sleeps, "
    "the orders vanish without any cancel command.",
    "<b>Every order has an increasing request number</b>, which the exchange treats as a duplicate guard, so the same order is never applied twice.",
    "<b>The private key is never put on a command line.</b> The tool reads it from an environment variable, and a test checks that no command contains it.",
    "<b>Live mode is locked.</b> It refuses to start unless a key is set in the environment and a second switch (UNDERTOW_ALLOW_LIVE=yes) is on. "
    "Neither is set. Two tests check the refusal.",
    "<b>Alerts</b> print to the screen and go to Telegram if you set a bot token and chat number. Telegram sending was not tested (no token).",
])
S += [PageBreak(), Paragraph("What the paper run actually showed", H1),
      Paragraph(f"A real run on live Perpl data: {len(cycles)} cycles, 45 seconds apart, BTC. Quotes are computed from the real mark price.", SM)]
rows = [[cell("<b>Cycle</b>"), cell("<b>Mark</b>"), cell("<b>Bid</b>"), cell("<b>Ask</b>"), cell("<b>Bid to ask (bps)</b>"), cell("<b>Action</b>")]]
for i, c in enumerate(cycles, 1):
    q = c["quotes"]
    bid = next((x["price"] for x in q if x["side"] == "bid"), None)
    ask = next((x["price"] for x in q if x["side"] == "ask"), None)
    gap = f"{(ask - bid) / c['mark'] * 1e4:.1f}" if bid and ask else "-"
    rows.append([cell(i), cell(f"{c['mark']:,.1f}"), cell(f"{bid:,.1f}" if bid else "-"), cell(f"{ask:,.1f}" if ask else "-"),
                 cell(gap), cell(c["action"])])
S += [table(rows, [18 * mm, 30 * mm, 30 * mm, 30 * mm, 28 * mm, 30 * mm]), Spacer(1, 6),
      Paragraph(f"<b>Result: {len(fills)} fills, profit and loss $0.00.</b> The BTC mark moved from "
                f"{min(c['mark'] for c in cycles):,.1f} to {max(c['mark'] for c in cycles):,.1f} (about "
                f"{(max(c['mark'] for c in cycles) / min(c['mark'] for c in cycles) - 1) * 1e4:.1f} bps) during the run, less than the 8 bps each-side "
                "distance from the mark to each quote, so no simulated order was reached. This shows that the quoting works on real data. It is <b>not</b> evidence that "
                "the bot makes money.", P_)]
S += [Paragraph("How well is it tested?", H2)]
S += bullets([
    "<b>48 automated tests pass</b> (20 for the bot). They cover: quotes straddle the mark and respect price and size steps; "
    "the gap beats fees; inventory leaning; position limit; stale data; kill switch; daily loss, leverage and liquidation-distance halts; "
    "a halt staying halted; big zone with a thin book pulls; big zone with a deep book only widens; small or distant zones ignored; "
    "simulated fills and a full round trip with fees; the live lock; and the shape of the order command.",
    "The fill model is crude: an order counts as filled when the mark price crosses it, checked once per data refresh (about every 45 seconds).",
    "<b>Not tested at all:</b> placing a real order on any network, Telegram sending, and cancelling by order number (orders rely on their expiry instead).",
])
S += [Paragraph("Settings are guesses", H2),
      Paragraph("The gap, order size, limits and zone thresholds above are my starting values, not tuned on data. The bot also only knows BTC's "
                "price step (0.1) and size step (0.00001), which I read from the live order book. Other markets need their own values.", P_),
      PageBreak(), Paragraph("Protocol view", H1),
      Paragraph(f"Exchange-wide numbers from the live data. Open interest now: <b>{risk.fmt_usd(proto['total_oi_usd'])}</b> across "
                f"{proto['accounts_with_positions']} accounts and {proto['positions']} positions. The accounts holding positions have "
                f"{risk.fmt_usd(proto['balances_of_those_accounts'])} in balances.", P_),
      Paragraph(f"Open interest since collection began ({len(proto['history'])} snapshots from {proto['history_started']} UTC):", SM),
      oi_chart(proto["history"])]
rows = [[cell("<b>Market</b>"), cell("<b>Open interest</b>"), cell("<b>Share</b>"), cell("<b>Longs</b>"), cell("<b>Shorts</b>"),
         cell("<b>Long collateral</b>"), cell("<b>Short collateral</b>")]]
for m in proto["markets"][:8]:
    rows.append([cell(m["name"]), cell(risk.fmt_usd(m["oi_usd"])), cell(f"{m['oi_usd'] / proto['total_oi_usd'] * 100:.1f}%"),
                 cell(m["longs"]), cell(m["shorts"]), cell(risk.fmt_usd(m["long_collateral"])), cell(risk.fmt_usd(m["short_collateral"]))])
S += [Spacer(1, 6), table(rows, [28 * mm, 28 * mm, 20 * mm, 20 * mm, 20 * mm, 28 * mm, 30 * mm]),
      Paragraph("What this view is missing", H2)]
S += bullets([
    "<b>Trading volume, fees, deposits and withdrawals, liquidation history.</b> These need every past event, which needs the Envio indexer. "
    "The Perpl Risk Tool bounty lists several of these, so this view does <b>not yet meet that bounty</b>.",
    "<b>Open-interest history only goes back to when the refresher first ran</b> (a few hours of snapshots), not to the exchange's launch.",
    "Funding rate is shown as the contract reports it; I have not verified its time unit.",
])
S += [Paragraph("Watchlist and compare", H2),
      Paragraph("On the Wallets tab, press Watch on any wallet. Saved wallets appear side by side with balance, position value, profit or loss, "
                "highest leverage, nearest liquidation and how many positions have other traders' zones in their path. It is stored in your "
                "browser only, so it is not shared or synced.", P_),
      PageBreak(), Paragraph("What is left, and what needs you", H1)]
left = [["Envio indexer", "Needed for volume, fees, liquidation history, backtesting the cascade model, and the $1,000 Envio prize", "Needs your choice: Docker + Envio, or Envio hosted"],
        ["Bot on Perpl's test network, then mainnet", "Needs a separate wallet with funds and its key in an environment variable on your machine", "Needs you. I will not handle keys or funds"],
        ["Bot tuning", "Settings are untuned guesses; needs real fills or an event replay", "After testnet"],
        ["Wallet statistics", "Win rate, profit factor, biggest fall, streaks, holding time", "Needs trade history (Envio)"],
        ["Order form with risk preview", "Shows your liquidation, zones in your path, lower-leverage option, funding cost per day", "Not started"],
        ["Funding cost tracker", "Cost growing block by block", "Not started; needs funding unit confirmed"],
        ["Liquidation-aware orders", "Cancel or shrink an order when a zone gets close", "Not started; needs a faster feed than 45 s"],
        ["Remaining correctness checks", "24h volume, own test account, order book vs Perpl's screen", "Volume needs Envio"],
        ["Public link, logo, GitHub, four videos, README", "Track 1 deliverables", "Not started"]]
S += [table([[cell("<b>Item</b>"), cell("<b>What it is</b>"), cell("<b>Status</b>")]] + [[cell(a), cell(b), cell(c)] for a, b, c in left],
            [44 * mm, 80 * mm, 50 * mm]),
      Spacer(1, 8),
      Paragraph("<b>Schedule.</b> The deadline is 13 October. The two items that decide whether the bot counts as 'real on-chain activity' "
                "(a funded wallet and days of live trading) both need you and time that cannot be compressed. If the wallet is funded "
                "today, the bot gets about three days of activity at best.", P_)]
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
