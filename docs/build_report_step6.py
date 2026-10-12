"""Build the final report: what is built, how it was verified, and what only the owner can do.  python docs/build_report_step6.py"""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

verify = json.loads((ROOT / "data" / "verify.json").read_text(encoding="utf-8"))
try:
    from undertow import testnet
    addr = testnet.address()
except Exception:
    addr = "(run: python -m undertow.testnet new)"

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


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Final report: what is built, verified, and what you need to do")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


out = ROOT / "Undertow_Final_Report_and_Your_Todo.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=18 * mm, title="Undertow - final report and your to-do list")

S = [Paragraph("Undertow - final report and your to-do list", H1),
     Paragraph("Written 12 October 2026, one day before the 13 October deadline.", SM),
     Paragraph("Where things stand", H2),
     Paragraph("The product is built and its numbers are checked against Perpl's own API. What is missing falls into two groups: things that "
               "need <b>your accounts, money or time</b> (listed in Part 3 with exact steps), and a few product gaps I could not close "
               "(Part 2). The bot has <b>not traded on any network</b>, and there is no public link yet.", P_),
     table([[cell("<b>Piece</b>"), cell("<b>State</b>")],
            [cell("Radar: liquidation zones, plain-language warning, cascade simulator"), cell("Done. Cascade is a labelled worst-case estimate, not validated on a real liquidation")],
            [cell("Trade: order preview (liquidation, zones in your path, fees, funding, slippage)"), cell("Done. Does not send orders")],
            [cell("Markets, Protocol, Wallets (search, watchlist, compare), Bot tabs"), cell("Done. Wallet profit-and-loss statistics are not built")],
            [cell("Liquidation-aware order rules"), cell("Done as a live check and rule engine. Checked about once a minute, not every block; live cancelling untested")],
            [cell("Independent correctness checks against Perpl's REST API"), cell("Done; results below")],
            [cell("Bot"), cell("Paper mode only. Testnet and mainnet need your wallet")],
            [cell("Envio indexer"), cell("Built and tested end to end, but the public RPCs rate-limit it, so it holds only a small recent window")],
            [cell("Logo, README, architecture, deploy guide, video scripts, submission checklist"), cell("Done (in docs/)")],
            [cell("Code in a local git repository"), cell("Committed locally. Not pushed anywhere")],
            [cell("Docker image for hosting"), cell("Built, run and fetching fresh data locally; hosts untested")]],
           [86 * mm, 88 * mm]),
     Paragraph("Part 1: how the numbers were verified", H1),
     Paragraph("Each check compares our value with Perpl's own public API (an independent source) or with Perpl's own SDK. They can be rerun with "
               "<font face='Courier'>python -m undertow.verify</font> or the button on the dashboard's Protocol tab.", P_)]
rows = [[cell("<b>Check</b>"), cell("<b>Result</b>"), cell("<b>Detail</b>")]]
for c in verify["checks"]:
    rows.append([cell(c["name"]), cell(c["status"].upper()), cell(c["detail"])])
S += [table(rows, [52 * mm, 20 * mm, 102 * mm]), Paragraph(f"Run at {verify['ran_at']} UTC on live mainnet data.", SM),
      Paragraph("What the checks found along the way", H2)]
S += bullets([
    "<b>Order book:</b> my first comparison failed (16 of 20 levels). The cause was a one-block offset: Perpl's REST book labelled block b is the contract "
    "state at block b minus 1. With that alignment all 20 levels match in price, size and order count. I did not weaken the test to make it pass; I measured the offset.",
    "<b>Funding units:</b> the funding rate in our data is a fraction per interval (a 4e-5 rate gave a payment of $3.3 per BTC at a price near $83,000, matching Perpl's series), "
    "and the interval is 2,580 seconds, <b>about 43 minutes</b>, not an hour as I had labelled it. Fixed on the dashboard. <b>Correction:</b> the earlier Step 5 PDF says funding is 'about hourly'; that line is superseded by this one.",
    "<b>Not done:</b> 'own test account' (needs a funded Perpl account), and the volume and funding checks against the indexer (the indexer has not covered a whole minute or "
    "hour yet because the public RPCs rate-limit it). They are written and will run once the indexer has data.",
])
S += [Paragraph("Mistakes I caught in my own work this round", H2)]
S += bullets([
    "The 'safer leverage' suggestion was logically inverted (it looked for a liquidation <i>below</i> every zone, which puts every zone in your path). Rewritten so a leverage is safe if "
    "your liquidation sits beyond where the modelled cascade from the biggest zone in your path ends. A unit test now covers deep versus thin books.",
    "The cascade was aimed at a zone's midpoint, which can sit above the liquidation prices of the very positions in it, so nothing triggered. It now aims at the zone's far edge "
    "(also fixed on the Radar headline).",
    "Order slippage was measured against the mark price, giving a nonsense negative number for a buy. It is now measured against the best price in the book.",
    "The logo first rendered as a black square because the renderer ignores gradients; redone with solid colours.",
])
S += [PageBreak(), Paragraph("Part 2: what I could not finish, and why", H1)]
S += bullets([
    "<b>The public Monad RPCs are unreliable from this machine.</b> rpc.monad.xyz timed out for hours (Windows and Ubuntu); rpc1 and rpc2 work from Windows but time out from "
    "Ubuntu, where only rpc-mainnet.monadinfra.com worked; all of them rate-limit. I made the data layer rotate through several endpoints automatically, but your own RPC "
    "(for example Alchemy, which is also a hackathon sponsor) is the real fix.",
    "<b>The Envio indexer cannot backfill on public RPCs.</b> It stalls on rate limits after a few hundred blocks. The proper source (HyperSync) needs a free Envio token, "
    "which means creating an account, something I cannot do. With the token it can read the full history since launch within minutes.",
    "<b>Docker image (updated 12 October):</b> now tested end to end locally. The fast build (Dockerfile.fast, a prebuilt Perpl tool, no Rust compile) takes about 15 seconds, runs as a "
    "normal user, and fetched fresh data inside the container through a fallback RPC. What is still untested is each hosting company's own build and run steps.",
    "<b>Bot on testnet:</b> needs a funded wallet (below). Nothing about order placement, cancelling or fills has run on any chain.",
    "<b>Wallet statistics</b> (win rate, profit factor, streaks, holding time) and trade history are not built. The indexer records only the resting side of each trade and no per-wallet "
    "profit and loss, so this needs more indexing work. The Perpl Risk Tool bounty lists these, so that entry is incomplete.",
    "<b>Several-days-of-live-bot-activity</b> for the API bounty cannot be achieved any more: even if a wallet is funded today there are about 1.5 days left.",
    "<b>Cascade model validation:</b> no real Perpl liquidation has appeared in the indexed window, so the model has never been tested against a real one.",
])
S += [PageBreak(), Paragraph("Part 3: what you need to do", H1),
      Paragraph("In priority order. The deadline is <b>13 October</b>; submit on the 13th, not the morning of the 14th. Times are rough.", P_),
      Paragraph("A. Needed to submit at all", H2),
      table([[cell("<b>#</b>"), cell("<b>What</b>"), cell("<b>Exactly how</b>"), cell("<b>Time</b>")],
             [cell("1"), cell("<b>Put the code on GitHub and share it</b> (Track 1 requires a public repo shared with metropolis@hackathon.monad.xyz)"),
              cell("Create an empty public repository on your GitHub account. In the project folder run: git remote add origin https://github.com/YOU/undertow.git then git push -u origin main. "
                   "Then Settings, Collaborators, add metropolis@hackathon.monad.xyz. The code is already committed locally and checked for keys; nothing has been pushed."), cell("10 min")],
             [cell("2"), cell("<b>Record the four videos</b>"),
              cell("Scripts, timings and what to show are in docs/VIDEO_SCRIPTS.md: Track 1 demo (3 min), Track 1 pitch (2 min), bot (2 min), dashboard (2 min). "
                   "Start the dashboard first (command in the README) and wait until 'Data Ns old' is under a minute. Say only what is true today; the checklist lists claims to avoid."), cell("1 to 2 h")],
             [cell("3"), cell("<b>Get a stable RPC and put it in the dashboard</b>"),
              cell("Create a free Alchemy account, make a Monad mainnet app, copy its HTTPS URL. Then in PowerShell: $env:UNDERTOW_RPC=\"YOUR-URL\" before starting the dashboard. "
                   "This stops the stale-data problems and unlocks the indexer. Alchemy's hackathon credits ($1,000) apply to projects using it."), cell("10 min")],
             [cell("4"), cell("<b>Publish a live link</b> (Track 1 and the Risk Tool bounty want one)"),
              cell("Create an account with any host that runs Docker containers, point it at your GitHub repository, set UNDERTOW_RPC to your Alchemy URL, deploy. Steps and caveats in docs/DEPLOY.md. "
                   "If you cannot, say in the submission that the demo runs locally and show the video."), cell("30 to 60 min")]],
            [7 * mm, 46 * mm, 98 * mm, 23 * mm]),
      Paragraph("B. Makes the Envio entry real", H2),
      table([[cell("<b>#</b>"), cell("<b>What</b>"), cell("<b>Exactly how</b>"), cell("<b>Time</b>")],
             [cell("5"), cell("<b>Free Envio API token</b>"),
              cell("Create a free token at https://envio.dev/app/api-tokens. Put ENVIO_API_TOKEN=... in indexer/.env, remove the two rpc: blocks and set start_block to 54773010 in indexer/config.yaml "
                   "(instructions in indexer/README.md), then restart the indexer in Ubuntu. Tell me when done and I can rerun the volume and funding checks and fill the Protocol view with full history."), cell("10 min")]],
            [7 * mm, 46 * mm, 98 * mm, 23 * mm]),
      Paragraph("C. Needed for the Perpl API (bot) bounty", H2),
      table([[cell("<b>#</b>"), cell("<b>What</b>"), cell("<b>Exactly how</b>"), cell("<b>Time</b>")],
             [cell("6"), cell("<b>Ask Perpl's team three questions</b> (the roadmap left these open)"),
              cell("Does testnet activity count for the bot bounty? Does using the Rust SDK count as using their API? Can one project enter Track 1 and both Perpl bounties? "
                   "The answers decide whether C is worth doing."), cell("5 min")],
             [cell("7"), cell("<b>Fund the testnet wallet</b>"),
              cell(f"Send testnet MON for gas (a small amount) and at least 100 test aUSD to this address: {addr}. Test aUSD comes from Perpl's testnet app (testnet.perpl.xyz) with your own wallet; "
                   "nobody can mint it freely. Then I can run python -m undertow.testnet fund 100 and the bot on testnet and review the log with you."), cell("10 to 20 min")],
             [cell("8"), cell("<b>Decide on mainnet</b>"),
              cell("Only after a clean testnet run you have reviewed: a separate wallet with about $50 to $100 AUSD, never your main wallet. I will not handle keys or funds; you set the environment "
                   "variables (README). Be honest in the submission: it would have under two days of activity."), cell("your call")]],
            [7 * mm, 46 * mm, 98 * mm, 23 * mm]),
      Paragraph("D. While you work", H2)]
S += bullets([
    "Keep the laptop awake and online: the data feed stopped twice while I worked (most likely the laptop sleeping or the network dropping; I did not confirm which). Keep the dashboard window (and for Envio, the Ubuntu indexer window) open while recording.",
    "Do not commit or share data/testnet/key.txt. It is a throwaway testnet key but treat it as a secret. It is git-ignored.",
    "If anything on the dashboard shows an error banner or 'Data' older than a few minutes, restart it with the command in the README; tell me and I will diagnose.",
])
S += [Paragraph("What to tell me when you have done something", H2),
      Paragraph("Steps 3, 5 and 7 each unlock work I can do next: stable data (3), full-history indexer and the remaining checks (5), and the testnet bot run (7). "
                "If you give me nothing else, doing 1, 2 and 3 is enough for a defensible Track 1 submission that is honest about its limits.", P_)]
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
