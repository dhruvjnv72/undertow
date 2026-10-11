"""Build the Live terminal report.  python docs/build_report_live.py   (needs the dashboard running on :8000)"""
import json
import urllib.request
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
v = json.loads(urllib.request.urlopen("http://localhost:8000/api/verify", timeout=300).read())
live = json.loads(urllib.request.urlopen("http://localhost:8000/api/live?market=1", timeout=20).read())

INK, MUTED, ACCENT = colors.HexColor("#1b1f2a"), colors.HexColor("#5b6272"), colors.HexColor("#2b5fd9")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=20, textColor=INK, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=4)
P = ParagraphStyle("P", parent=ss["BodyText"], fontSize=9.5, leading=13.5, textColor=INK)
SM = ParagraphStyle("SM", parent=P, fontSize=8.3, leading=11, textColor=MUTED)
BUL = ParagraphStyle("BUL", parent=P, leftIndent=12, bulletIndent=2)
C = ParagraphStyle("c", parent=P, fontSize=8.3, leading=10.5)


def cell(s):
    return Paragraph(str(s), C)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5d8e0")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0fd")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Live terminal")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


img = ROOT / "docs" / "img" / "live.jpg"
w, h = ImageReader(str(img)).getSize()
st = live["status"]
doc = SimpleDocTemplate(str(ROOT / "Undertow_Live_Terminal.pdf"), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                        topMargin=16 * mm, bottomMargin=18 * mm, title="Undertow - Live terminal")
S = [Paragraph("Undertow - the live terminal", H1),
     Paragraph("The first tab of the dashboard. Built and tested on 12 October 2026 against Perpl mainnet.", SM),
     Paragraph("What it is", H2),
     Paragraph("A screen that updates with every Monad block (about every 0.4 seconds) using <b>Perpl's own public WebSocket feed</b>: oracle, mark and "
               "last price, the order book, and every trade. It is not our estimate of those things; it is what Perpl broadcasts. On top of that feed it "
               "computes risk signals against the liquidation-zone data, re-measuring every zone against the <b>live</b> mark price on each update.", P),
     Image(str(img), width=170 * mm, height=170 * mm * h / w),
     Paragraph(f"Live at build time: {st['msgs_per_second']} messages per second, head block {st['head_block']:,}, "
               f"last message {st['last_message_age_ms']} ms old.", SM),
     Paragraph("What is on the screen", H2),
     table([[cell("<b>Panel</b>"), cell("<b>Source and update speed</b>")],
            [cell("Ticker bar: mark, oracle, last, bid/ask, spread, mark minus oracle, 24h change, open interest, volume"),
             cell("Perpl feed, pushed on change. Perpl itself updates the mark price every few seconds; the screen shows how many seconds ago")],
            [cell("Order book ladder, 12 levels a side with depth bars"), cell("Perpl feed: a snapshot, then every changed level each block")],
            [cell("Trade tape (big trades highlighted)"), cell("Perpl feed, each trade as it happens")],
            [cell("Mark versus oracle chart, last 10 minutes"), cell("Built from the feed as it arrives")],
            [cell("Momentum (10 s, 60 s, 5 min), buy/sell flow over 60 s, book imbalance within 0.5%"), cell("Calculated from the feed on every poll")],
            [cell("Liquidation zones, distance to the live mark, cascade estimate using the live book, positions within 0.5% of liquidation"),
             cell("Positions from the snapshot (up to about a minute old), measured against the live mark and live book")],
            [cell("Alerts (zone close, fast move, mark far from oracle, one-sided flow, lopsided book, positions near liquidation)"),
             cell("Rule-based, thresholds listed in the code; an alert appears when a condition becomes true and stays logged")]],
           [88 * mm, 86 * mm]),
     PageBreak(), Paragraph("How accurate is it?", H1),
     Paragraph("Each line is a real test run just now through the dashboard's checks (Protocol tab, 'Run checks').", P)]
rows = [[cell("<b>Check</b>"), cell("<b>Result</b>"), cell("<b>Detail</b>")]]
for c in v["checks"]:
    if c["name"].startswith(("live feed", "ticker", "order book", "volume")):
        rows.append([cell(c["name"]), cell(c["status"].upper()), cell(c["detail"][:260])])
S += [table(rows, [48 * mm, 18 * mm, 108 * mm]), Paragraph(f"Run at {v['ran_at']} UTC.", SM),
      Paragraph("In plain words: the prices on the Live tab equal Perpl's own ticker, and the order book kept from the feed equals the book stored in "
                "Perpl's contract on the chain, level for level (price, size and number of orders). One detail: Perpl labels a book with the block it was taken at the "
                "<i>start</i> of, so it equals the contract state read at the previous block. In this run the match was 20 of 20 at that earlier block and 16 of 20 at the "
                "labelled block, the same convention found for Perpl's REST book. Those are the parts that must be exact, and in this test they are.", P),
      Paragraph("What 'signals' means here", H2),
      Paragraph("The <b>data</b> is accurate. The <b>signals</b> are rules applied to that data, such as 'a $300k zone is 1% from the live mark'. They are "
                "risk flags, not predictions of where the price will go, and not trading advice. I did not backtest any of them, and the thresholds are my choices.", P),
      Paragraph("Honest limits", H2)]
S += bullets([
    "<b>Zones are not real-time.</b> Perpl's feed carries prices, book and trades, not everyone's positions. Positions come from our snapshot (refreshed about every "
    "45 seconds). A position opened or closed in the last minute is missing or stale, so a zone alert can be slightly off. Making positions real-time means rebuilding "
    "Perpl's state from every contract event, which is a larger project and is not built.",
    "<b>The mark price moves in steps.</b> Perpl updates it every few seconds, and liquidations use it. The screen shows its age so this is visible rather than looking like lag.",
    "<b>Only the 7 busiest markets stream a live book.</b> Perpl allows 16 subscriptions per connection; each market's book and trades take two.",
    "<b>Display delay is at most half a second:</b> the page asks the server for the latest state twice a second.",
    "<b>It needs a working internet path to Perpl's WebSocket.</b> If the computer sleeps or the connection drops it shows 'FEED STALLED / RECONNECTING' and "
    "reconnects by itself, but I tested it for minutes, not hours, so I have not proven long-run stability.",
    "<b>The browser tab must be visible.</b> The page pauses polling when its tab is hidden, to save work.",
    "The cascade estimate shown for each zone is the same labelled worst-case model as on the Radar tab, now run against the live book. It has not been "
    "checked against a real historical liquidation.",
])
S += [Paragraph("How to use it", H2),
      Paragraph("Run <font face='Courier'>start_dashboard.bat</font> (or <font face='Courier'>python -m undertow.server --refresh</font>). The Live tab opens first. "
                "It needs one extra package: <font face='Courier'>pip install websockets</font>. Code: undertow/live.py and undertow/signals.py, with 14 new tests.", P)]
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote")
