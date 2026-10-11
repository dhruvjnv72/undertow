"""Build the Step 5 PDF (testnet readiness and Envio indexer).  python docs/build_report_step5.py"""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from undertow import envio_client, risk  # noqa: E402

D = ROOT / "data"

# ---- live facts gathered at build time
g = envio_client.query("""{
  t:Trade_aggregate{aggregate{count}} l:Liquidation_aggregate{aggregate{count}} d:Deleverage_aggregate{aggregate{count}}
  c:CashFlow_aggregate{aggregate{count}} a:Account_aggregate{aggregate{count}} f:FundingEvent_aggregate{aggregate{count}}
  days:DailyStats(order_by:[{chainId:asc},{date:asc}]){chainId date volumeUsd trades makerFeesUsd depositsUsd withdrawalsUsd liquidations newAccounts}
  m:chain_metadata{chain_id latest_fetched_block_number block_height}
}""") or {}
counts = {k: g[k]["aggregate"]["count"] for k in ("t", "l", "d", "c", "a", "f")} if g else {}
days = envio_client._num(g.get("days", [])) if g else []
meta = {m["chain_id"]: m for m in g.get("m", [])} if g else {}

t_status = json.loads((D / "testnet" / "status.json").read_text(encoding="utf-8"))
t_snap = json.loads((D / "testnet" / "parsed" / "snapshot_latest.json").read_text(encoding="utf-8"))
t_fc = risk.verify_formula(t_snap)
bot_log = D / "testnet" / "bot" / "log.jsonl"
t_cycles = [json.loads(x) for x in bot_log.read_text(encoding="utf-8").splitlines()] if bot_log.exists() else []
t_cycles = [c for c in t_cycles if c["event"] == "cycle"]

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
    canvas.drawString(18 * mm, 10 * mm, "Undertow - Step 5: testnet readiness and Envio")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


addr = ""
try:
    from undertow import testnet
    addr = testnet.address()
except Exception:
    pass

out = ROOT / "Undertow_Step5_Testnet_and_Envio.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=18 * mm, title="Undertow - Step 5: testnet readiness and Envio")
S = [Paragraph("Undertow - Step 5: testnet readiness and the Envio indexer", H1),
     Paragraph("Built and tested on 10 October 2026. The bot has still not placed any order on any network.", SM),
     Paragraph("Summary", H2),
     table([[cell("<b>Item</b>"), cell("<b>Status</b>")],
            [cell("<b>Envio indexer</b>"), cell("<b>Working</b> on Monad mainnet and testnet, over the public RPC, for a recent window only. "
                                                "Full history needs a free Envio token (an account only you can create).")],
            [cell("<b>Dashboard Protocol tab</b>"), cell("Now shows volume, fees, deposits, withdrawals and liquidations from the indexer.")],
            [cell("<b>Whole project on testnet</b>"), cell("Done: one switch runs data, risk engine, dashboard and bot against Perpl's testnet.")],
            [cell("<b>Bot on testnet, paper mode</b>"), cell("Done, on real testnet prices.")],
            [cell("<b>Bot on testnet, sending real test orders</b>"), cell("<b>Not done.</b> Needs a testnet wallet funded with MON and aUSD, which only you can arrange. "
                                                                          "The order command was verified up to the chain.")]],
           [52 * mm, 122 * mm]),
     Paragraph("Part 1: testing the bot on testnet", H1),
     Paragraph("What I could verify without a funded wallet", H2)]
S += bullets([
    f"<b>Testnet is live and populated.</b> Chain 10143, BTC is market <b>16</b> (not 1), about {risk.fmt_usd(sum(p['open_interest_usd'] or 0 for p in t_snap['perps'].values()))} "
    f"of open interest, {len({p['account_id'] for p in t_snap['positions']})} accounts, {len(t_snap['positions'])} positions. "
    f"The open-interest check passes {t_status['oi_checks_passed']} of {t_status['oi_checks_total']} and our liquidation formula matches Perpl's for "
    f"{t_fc.within_tolerance} of {t_fc.total} testnet positions.",
    "<b>The whole project is now network-aware.</b> Setting one variable (UNDERTOW_NETWORK=testnet) points data, risk engine, dashboard, "
    "account list and bot at the testnet, with separate data folders, so the two networks never mix.",
    f"<b>Paper bot on testnet prices:</b> {len(t_cycles)} cycles ran, actions: {', '.join(sorted({c['action'] for c in t_cycles})) or 'none'}. "
    "An earlier attempt correctly <i>pulled its quotes</i> because the testnet data was 17 minutes old; that safety rule is working.",
    "<b>The real order command was tested up to the chain.</b> I built the exact command the bot would send and added a no-send flag. "
    "The first try failed because the key-file option belongs after the 'create' keyword, a bug no unit test could have found. "
    "After the fix, perpl-cli accepted every argument and reached the chain, which answered AccountDoesNotExist for my unfunded test wallet. "
    "That is the correct answer and proves the syntax.",
])
S += [Paragraph("What I could not do, and why", H2)]
S += bullets([
    "<b>Fund a testnet wallet.</b> Testnet MON comes from a faucet and test aUSD from Perpl; the token's mint function is restricted to approved "
    "minters (I checked), so nobody can mint it freely. Faucets and the Perpl testnet app need a connected wallet or a web verification step, "
    "which I will not do on your behalf. Perpl's testnet also requires a minimum account deposit of 100 aUSD.",
    "<b>Place a real testnet order.</b> Follows from the above. Fills, cancellations, expiry behaviour and the account creation step are therefore "
    "still untested on any chain.",
])
S += [Paragraph("What you need to do (about 10 minutes)", H2),
      Paragraph("I created a throwaway testnet wallet. Its key is saved on this computer in data/testnet/key.txt (never shown in chat, "
                "git-ignored). It holds nothing of value and must not be used anywhere else.", P_),
      Paragraph(f"Bot testnet address: {addr or '(run: python -m undertow.testnet new)'}", CODE),
      table([[cell("<b>Step</b>"), cell("<b>Action</b>")],
             [cell("1"), cell("Send that address testnet MON for gas from a Monad testnet faucet (a small amount, for example 0.5 MON).")],
             [cell("2"), cell("Using Perpl's testnet app (testnet.perpl.xyz) and your own wallet, obtain at least 100 test aUSD and send it to the bot address.")],
             [cell("3"), cell("python -m undertow.testnet status   (check balances), then   python -m undertow.testnet fund 100   (approve and create the Perpl account).")],
             [cell("4"), cell("Start the testnet data feed: $env:UNDERTOW_NETWORK='testnet'; python -m undertow.server --refresh --port 8001")],
             [cell("5"), cell("In a second window: $env:UNDERTOW_NETWORK='testnet'; $env:UNDERTOW_ALLOW_LIVE='yes'; python -m undertow.bot --live --cycles 20")],
             [cell("6"), cell("Watch the Bot tab at http://localhost:8001. If it behaves, tell me and we review the log together before mainnet.")]],
            [14 * mm, 160 * mm]),
      Paragraph("Mainnet stays doubly locked: it needs a second switch (UNDERTOW_ALLOW_MAINNET=yes) on top of the first, and a key for a separate wallet.", SM),
      PageBreak(), Paragraph("Part 2: the Envio indexer", H1),
      Paragraph("What it does", H2),
      Paragraph("Envio reads every event Perpl's contract emits and stores it in a database with a GraphQL interface. Undertow's Protocol "
                "view uses it for the numbers a snapshot cannot give: volume, fees, deposits, withdrawals, liquidations. "
                "It indexes 7 event types: trades (the resting side of each fill, so volume is counted once), liquidations, auto-deleveraging, "
                "deposits, withdrawals, account creation (which also gives wallet address to account number) and funding events.", P_),
      Paragraph("What had to be installed", H2)]
S += bullets([
    "Envio does <b>not run natively on Windows</b>. It needs WSL 2 (a Linux inside Windows) and Docker.",
    "Installed: Docker Desktop 4.94, Ubuntu 26.04 under WSL, Node 24 inside Ubuntu, and Docker Engine inside Ubuntu. "
    "Docker Desktop's own Ubuntu integration did not switch on, so Docker runs inside Ubuntu instead.",
    "Windows asked for administrator approval for the Docker and WSL installs.",
])
S += [Paragraph("Problems hit and fixed", H2)]
S += bullets([
    "<b>TypeScript 7 breaks Envio.</b> The newest TypeScript has no 'sys' object and Envio crashed with 'Cannot read properties of undefined'. "
    "Pinning TypeScript 5 fixed it.",
    "<b>Handlers must be ES modules</b>: package.json needed type = module.",
    "<b>HyperSync needs an account.</b> Envio's fast data source requires a free token from envio.dev. I cannot create accounts, so I switched the "
    "indexer to the public Monad RPC.",
    "<b>The public RPC allows only 100 blocks per log query</b> and runs at roughly 17 blocks per second here, so I first set a start block "
    "1.5 days back and it could never catch up (about 5 hours). I moved the start to a few hours back so it reaches the live head.",
    "<b>The dashboard crashed on real data</b>: the database returns numbers as text. Fixed, and the card now states the true indexed window "
    "(my first version claimed 'every event since launch', which was false).",
])
S += [Paragraph("What it has indexed so far", H2)]
rows = [[cell("<b>Network</b>"), cell("<b>Day</b>"), cell("<b>Trades</b>"), cell("<b>Volume</b>"), cell("<b>Maker fees</b>"),
         cell("<b>Deposits</b>"), cell("<b>Withdrawals</b>"), cell("<b>Liquidations</b>")]]
for dd in days:
    rows.append([cell("mainnet" if dd["chainId"] == 143 else "testnet"), cell(dd["date"]), cell(dd["trades"]), cell(risk.fmt_usd(dd["volumeUsd"])),
                 cell(f"${dd['makerFeesUsd']:,.2f}"), cell(risk.fmt_usd(dd["depositsUsd"])), cell(risk.fmt_usd(dd["withdrawalsUsd"])),
                 cell(dd["liquidations"])])
S += [table(rows, [20 * mm, 24 * mm, 18 * mm, 24 * mm, 24 * mm, 22 * mm, 24 * mm, 20 * mm]), Spacer(1, 4),
      Paragraph(f"Rows by type across both networks: trades {counts.get('t', '-')}, liquidations {counts.get('l', '-')}, deleverages {counts.get('d', '-')}, "
                f"deposits and withdrawals {counts.get('c', '-')}, accounts created {counts.get('a', '-')}, funding events {counts.get('f', '-')}.", SM),
      Paragraph("Limits you should know", H2)]
S += bullets([
    "<b>The window is hours, not history.</b> The 7-day, 30-day and all-time figures on the dashboard all equal the indexed window. For the full "
    "history since Perpl launched (mainnet block 54,773,010), create a free token at envio.dev and follow indexer/README.md (three edits).",
    f"<b>No liquidation has occurred in the window</b> (liquidations indexed: {counts.get('l', 0)}), so the liquidation handler has not been tested on a real event, "
    "and neither has the cascade model. This is the main reason to get the full history.",
    "<b>Not every handler has been checked against reality.</b> Trades and deposits show data; I did not independently verify the volume figure against Perpl's own screen "
    "(the roadmap's correctness check 2), so treat it as unverified until that is done.",
    "<b>It runs only while two things are open</b>: Docker inside Ubuntu, and the indexer window. Closing them stops it. The hackathon needs a hosted "
    "indexer or a recorded demo, which is a decision for later.",
    "<b>The dashboard secret for the local database is the public default</b> ('testing'); it is bound to your own computer only.",
])
S += [Paragraph("Facts learned from Perpl's documentation", H2)]
S += bullets([
    "<b>Liquidations go to the order book first.</b> If a position is too big, it is partly liquidated; what the book cannot absorb is bought "
    "by Perpl's own vault (PLP). This supports the cascade model's main assumption, and is why the 'share hitting the book' control matters.",
    "<b>Funding is applied every 8,571 blocks</b>. The docs say 'about hourly'; Perpl's REST API later showed 2,580 seconds (about 43 minutes), which the dashboard now uses.",
    "<b>Our liquidation formula agrees with the docs</b> (4% maintenance on BTC gives liquidation after a 6% fall for a 10x long).",
    "<b>Testnet account minimum is 100 aUSD</b>; mainnet is 10 AUSD.",
])
S += [PageBreak(), Paragraph("What is left", H1)]
left = [["Bot on testnet with real test orders", "Needs your funded testnet wallet (steps above)", "Waiting on you"],
        ["Envio full history", "Free token from envio.dev, then three config edits", "Waiting on you"],
        ["Verify volume against Perpl's screen; own-account check", "Roadmap correctness checks 2 and 3", "Next"],
        ["Test liquidation handler and cascade model on real liquidations", "Needs history long enough to contain some", "After Envio token"],
        ["Order form with risk preview, funding cost tracker", "Roadmap items", "Not started"],
        ["Wallet statistics (win rate, profit factor, streaks)", "Needs trade history per wallet, now possible with the indexer", "Not started"],
        ["Liquidation-aware orders", "Needs a faster feed than a snapshot every 45 s", "Not started"],
        ["Mainnet bot", "Only after a clean testnet run you have reviewed", "Locked"],
        ["Public hosting, logo, GitHub share, four videos, final README", "Track 1 deliverables", "Not started"]]
S += [table([[cell("<b>Item</b>"), cell("<b>Notes</b>"), cell("<b>Status</b>")]] + [[cell(a), cell(b), cell(c)] for a, b, c in left],
            [66 * mm, 70 * mm, 38 * mm]), Spacer(1, 8),
      Paragraph("<b>Schedule.</b> The deadline is 13 October. The two blockers above are both quick for you (testnet funding about 10 minutes, Envio token about "
                "5 minutes) but nothing downstream of them can start until you do them.", P_)]
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
