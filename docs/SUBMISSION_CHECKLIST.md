# Submission checklist (deadline 13 October 2026)

Status as of 11 October 2026 evening. "Me" means done by the assistant in this project; "You" means only the account owner can do it.

## Track 1: Onchain Finance and Trading

| Deliverable | Status |
|---|---|
| Logo | Done: `docs/logo.svg`, `docs/logo.png` |
| Public GitHub repository shared with metropolis@hackathon.monad.xyz | **You.** The code is ready to push (see "Publishing the code" below). Creating the repository and sharing it needs your GitHub account |
| Technical demo video, up to 3 minutes | **You** record it. Script: `docs/VIDEO_SCRIPTS.md` |
| Pitch video, up to 2 minutes | **You** record it. Script: `docs/VIDEO_SCRIPTS.md` |
| Live product link on Monad | **You.** Container is ready (`Dockerfile`, `docs/DEPLOY.md`); hosting needs your account |
| A real trading mechanism | Partly. The bot and order preview exist; the bot has not traded on any network (needs a funded wallet from you) |
| A trustworthy risk screen | Done and verified against Perpl's own API: mark, open interest, order book (20 of 20 levels identical), liquidation formula (all positions). Cascade model is a labelled worst-case estimate, not backtested |

## Perpl: Best Analytics / Risk Tool (checklist from the roadmap)

| Item | Status |
|---|---|
| Volume, open positions, deposits, fees, active users | Done through the Envio indexer for the indexed window; all-time needs the Envio token |
| Time-series charts with timeframe switching | Partial: open interest over time and daily volume; no 24h/7d/30d switch yet |
| Deposits, withdrawals, net inflows | Done for the indexed window |
| Per-market breakdown and long/short balance | Done (positions and collateral per side) |
| Liquidation data and funding rate overview | Liquidation list and funding rate shown; liquidation history is empty until a liquidation falls in the window |
| Search any address for a wallet profile | Done |
| Open positions with size, entry, leverage, PnL, liquidation price | Done |
| Trade history and realised PnL | **Not built** (the indexer records only the resting side of trades) |
| Win rate, profit factor, biggest fall, streaks, holding time | **Not built** |
| Save, watch and compare wallets | Done (stored in the browser) |
| Portfolio and margin overview | Partial (balance, locked, position value) |
| Dark mode, fast, no clutter | Done |
| 2-minute video and public link | **You** |

## Perpl: Best use of the API (bot)

| Item | Status |
|---|---|
| Bot on mainnet with real money (about $50 to $100) | **Not done. You** must fund a separate wallet; only after a clean testnet run |
| Several days of real trades before the deadline | Not achievable if started after 11 October |
| Hard risk limits working and logged | Done and unit-tested in paper mode; logged in `data/bot/log.jsonl` |
| Bot account address shared | **You**, once the wallet exists |
| 2-minute video and link | **You** |

Note: the roadmap listed "whether testnet activity counts" as still to confirm with Perpl's team. Ask them.

## Envio: Best use of Envio

| Item | Status |
|---|---|
| Indexer recording every Perpl event | Built (`indexer/`, 7 event types). Currently reads a recent window; **You** add a free Envio token for full history |
| History powering the Protocol view and wallet statistics | Protocol view: yes. Wallet statistics: only deposits, withdrawals, liquidations and maker-side activity |

## Publishing the code

    cd undertow
    git status                      # confirm no key files: data/ is ignored
    git remote add origin https://github.com/YOUR-NAME/undertow.git
    git push -u origin main

Then share the repository with metropolis@hackathon.monad.xyz in GitHub's settings. The assistant has created a local git
commit but has not pushed anything anywhere.

## Things never to claim (not true today)

* That the bot trades live on Monad mainnet.
* That liquidation-aware orders react "within about 0.4 seconds". They are checked about once a minute and live inside Undertow.
* That the cascade simulator has been validated on real liquidations.
* That the Protocol view covers Perpl's full history (until the Envio token is set up).
