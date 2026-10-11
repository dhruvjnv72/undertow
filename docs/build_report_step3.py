"""Build the Step 3 + dashboard progress PDF.  python docs/build_report_step3.py"""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from undertow import risk  # noqa: E402

snap = risk.load_latest()
fc = risk.verify_formula(snap)
mark = snap["perps"]["1"]["mark"]
head = risk.headline(snap, 1)

INK, MUTED, ACCENT = colors.HexColor("#1b1f2a"), colors.HexColor("#5b6272"), colors.HexColor("#2b5fd9")
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


def img(path, width_mm):
    from reportlab.lib.utils import ImageReader
    w, h = ImageReader(str(path)).getSize()
    return Image(str(path), width=width_mm * mm, height=width_mm * mm * h / w)


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Step 3 and local dashboard")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


# cascade table from the current snapshot (one moment in time)
rows = [[cell("<b>Scenario (BTC mark moves to)</b>"), cell("<b>Liquidations triggered</b>"), cell("<b>Forced value</b>"),
         cell("<b>Price after, first round only</b>"), cell("<b>Price after, with feedback</b>")]]
for f in (0.97, 0.93, 0.88, 1.05, 1.12):
    a = risk.simulate_cascade(snap, 1, mark * f, feedback=False)
    b = risk.simulate_cascade(snap, 1, mark * f, feedback=True)
    fmt = lambda c: f"{c.final_price:,.0f} ({c.move_pct:+.1f}%)" + (" book runs out" if c.book_exhausted else "")  # noqa: E731
    rows.append([cell(f"{(f - 1) * 100:+.0f}%  ({mark * f:,.0f})"), cell(f"{b.triggered_positions} positions"),
                 cell(risk.fmt_usd(b.triggered_notional)), cell(fmt(a)), cell(fmt(b))])

out = ROOT / "Undertow_Step3_Risk_Engine_and_Dashboard.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=18 * mm, title="Undertow - Step 3 and local dashboard")
S = [Paragraph("Undertow - Step 3 and the local dashboard", H1),
     Paragraph("The first thing you can open and use. Built and tested on real Monad mainnet data on 10 October 2026.", SM),
     Paragraph("What you can open now", H2),
     Paragraph("A dashboard that runs on your own laptop. It is not a public website yet; the link only works on your computer.", P),
     Paragraph("python -m undertow.server --refresh", CODE),
     Paragraph("then open <b>http://localhost:8000</b> in a browser. The <font face='Courier'>--refresh</font> part keeps the data "
               "updating itself about every 45 seconds. Leave the window open while you use the page.", P),
     Paragraph("The three screens", H2),
     table([[cell("<b>Screen</b>"), cell("<b>What it does</b>")],
            [cell("<b>Radar</b>"),
             cell("Pick a market. A plain sentence names the biggest danger. A chart shows every liquidation zone: red bars are longs "
                  "that get closed if the price falls, green bars are shorts that get closed if it rises, bar height is the value being closed. "
                  "A slider lets you move the price and see the cascade. A second chart shows the resting buy and sell orders, "
                  "and a table lists the biggest zones.")],
            [cell("<b>Markets</b>"), cell("All markets in one table with mark price, open interest, longs and shorts, and each market's biggest danger. "
                                          "Click a row to open its Radar.")],
            [cell("<b>Wallets</b>"), cell("Search any wallet address or account number. Shows balance, every open position with entry, PnL, "
                                          "liquidation price and distance, what the liquidation price would be at 5x and 3x, and a warning when other "
                                          "traders' liquidations sit between the price and yours. A list of the largest accounts is one click away.")]],
           [30 * mm, 144 * mm]),
     Paragraph("Header strip", H2),
     Paragraph(f"The top right always shows how old the data is, the block number, and two live self-checks: the open-interest check "
               f"(rebuilt from positions equals Perpl's number, 16 of 16 markets) and the formula check (our liquidation price equals Perpl's for "
               f"{fc.within_tolerance} of {fc.total} positions in the snapshot behind this report).", P),
     PageBreak(),
     Paragraph("Radar screen", H1),
     Paragraph("BTC with the slider at -12% (price 72,553). The dashed blue line is the scenario price. "
               "Everything on the page is calculated from real positions at that moment.", SM),
     img(ROOT / "docs" / "img" / "radar.jpg", 152),
     PageBreak(),
     Paragraph("Wallet screen", H1),
     Paragraph("Account #1767 (a $315k account). Each yellow box is the in-path warning: other traders' liquidations that would "
               "happen before this account's own.", SM),
     img(ROOT / "docs" / "img" / "wallet.jpg", 152),
     PageBreak(),
     Paragraph("The risk engine (Step 3)", H1),
     Paragraph("All the maths lives in one file, <font face='Courier'>undertow/risk.py</font>, so the screens and the future bot read "
               "the same numbers.", P),
     Paragraph("1. Liquidation prices, taken from Perpl's own code", H2),
     Paragraph("I read the official SDK instead of guessing. Perpl's rule: maintenance requirement = entry price x size / maintenance number, "
               "where the maintenance number is the second figure in 'Margin: 15.00 / 25.00' (so BTC needs 4% of position value kept as margin). "
               "Liquidation price = entry price +/- (requirement - deposit - funding PnL) / size. "
               "This answers the open question from Step 1.", P),
     Paragraph(f"<b>Check:</b> our formula matches Perpl's printed liquidation and bankruptcy price for {fc.within_tolerance} of {fc.total} "
               "positions in this snapshot. Earlier one micro-priced position looked wrong; the cause was that the snapshot prints only six "
               "decimals, so the check now allows for that. Tests also reuse the SDK's own worked examples.", P),
     Paragraph("2. Zones", H2),
     Paragraph("Positions are grouped into price bands (1% wide by default) by their liquidation price. A band's value is the full "
               "position value being closed, not the deposits. Each zone also records how much of it belongs to one account, "
               "because a zone that is one wallet is a different kind of risk from a zone that is a crowd.", P),
     Paragraph("3. Cascade simulator", H2),
     Paragraph("Question: if the mark price reaches X, what gets liquidated and how far could forced orders push the price? "
               "Method: find every position whose liquidation price was crossed; push their size through the resting order book "
               "starting at the best price; apply the percentage the book moved to the scenario price; optionally repeat if the "
               "new price triggers more (feedback mode).", P),
     Paragraph(f"Current BTC results (mark {mark:,.0f}). Figures change every refresh:", SM),
     table(rows, [34 * mm, 30 * mm, 24 * mm, 43 * mm, 43 * mm]),
     Paragraph("4. The warning sentence", H2),
     Paragraph(f"Right now for BTC it reads: <i>{head['sentence']}</i> The risk word compares the zone's value with the resting orders "
               "within 5% on the side it would hit: below 0.5x low, below 2x medium, above that high. These cut-offs are my own "
               "heuristic, not Perpl's; the page labels it simply as 'cascade risk'.", P),
     Paragraph("5. In-path warning", H2),
     Paragraph("For a position, finds zones that sit between the current price and the position's own liquidation price, "
               "excluding the account's own positions, and ignores zones under $5,000. It is shown in the Wallet screen "
               "and is the core of the 'danger between you and your liquidation' idea.", P),
     PageBreak(),
     Paragraph("What went wrong while building, and how it was fixed", H1)]
S += bullets([
    "<b>The first cascade model gave nonsense.</b> It showed a 7% drop turning into a 100% crash because junk resting orders at $0.10 "
    "defined the end price, and it assumed all orders above the scenario price were gone. I replaced it with the market-impact model "
    "above and ignore orders more than 30% from the best price. The old results were never shown on the dashboard.",
    "<b>The in-path warning was alarmist.</b> For a position 44% from liquidation it said '$668k of liquidations are in your path' and quoted "
    "a $60 zone as the nearest. It now quotes the biggest zone and ignores anything under $5,000.",
    "<b>Smaller fixes:</b> prices printed as '7.823e+04', tiny zones as '$0k', and a 'funding per 8 hours' label that I could not verify "
    "(the contract's funding interval is 8,571 blocks, about 57 minutes) now just says 'Funding rate'.",
    "<b>A stray background job</b> from Step 2 was overwriting the account list; it was stopped and the data rebuilt before this step.",
])
S += [Paragraph("Honest limits", H2)]
S += bullets([
    "<b>The cascade numbers are a worst-case estimate, not a prediction.</b> They assume liquidated size goes through the order book "
    "(the selector lets you test 50% or 25%), that makers do not pull orders, and in feedback mode that the mark price follows the book. "
    "Perpl's mark price follows an oracle, and part of liquidations go to buy-to-liquidate buyers. The page lists these assumptions under every result.",
    "<b>Not tested against a real historical liquidation.</b> I have no recorded Perpl liquidation event to replay, so I cannot say how close "
    "the model is. This needs the event history (the Envio step).",
    "<b>Risk labels (low, medium, high) are my own thresholds.</b>",
    "<b>Data is up to about a minute old</b>, and the page shows its age. It is not a per-block feed.",
    "<b>Localhost only.</b> No public link, no login. Nothing here can place orders or touch funds.",
    "<b>Wallet screen is partial.</b> No trade history, win rate, profit factor, streaks, watchlist or compare yet. Those need trade history.",
    "<b>Markets screen is not the Protocol view.</b> No volume, fees, deposits or liquidation history yet, for the same reason.",
    "<b>Visual check was on a laptop-sized pane only.</b> I did not test on a phone.",
])
S += [Paragraph("What is left", H2)]
left = [["3", "Risk engine, zones, cascade simulator", "Done (v1)"],
        ["4", "Radar map and sentence", "Done (v1), local only"],
        ["5", "Protocol view: volume, deposits, fees, liquidations over time", "Markets table only; needs event history (Envio)"],
        ["6", "Wallet explorer", "Positions and warnings done; stats, watchlist, compare, trade history left"],
        ["7", "Market-making bot (testnet, then mainnet, limits, Telegram)", "Not started; needs funded wallet from you"],
        ["8", "Order form with risk preview, funding cost tracker", "Not started"],
        ["9", "Liquidation-aware orders, zone-aware bot", "Not started"],
        ["10", "Envio indexer, remaining checks (24h volume, own test account, order book), logo, public hosting, GitHub, videos", "Not started"]]
S += [table([[cell("<b>Step</b>"), cell("<b>Scope</b>"), cell("<b>Status</b>")]] + [[cell(a), cell(b), cell(c)] for a, b, c in left],
            [12 * mm, 90 * mm, 72 * mm]),
      Spacer(1, 6),
      Paragraph("<b>Schedule.</b> The deadline is 13 October. The bot is still the biggest risk: it needs several days of real trading, "
                "and it has not started. It depends on a decision and an action from you: fund a separate wallet with a small amount "
                "of AUSD and MON. Until then I can build and test it against Perpl's test network only.", P)]
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
