<img align="right" width="84" alt="Undertow" src="docs/logo.png">

<h1 align="center">Undertow</h1>

<p align="center">
  <strong>See the pull before it takes you.</strong><br>
  Read-only liquidation radar for <a href="https://perpl.xyz">Perpl</a> on Monad.
  Every position is public, so Undertow shows where traders get liquidated and what sits between the price and you.
</p>

<p align="center">
  <a href="https://perpl.xyz"><img alt="Perpl, read-only" src="https://img.shields.io/badge/Perpl-read--only-5b8cff"></a>
  <img alt="Monad chain 143" src="https://img.shields.io/badge/Monad-chain%20143-836EF9">
  <img alt="Python 3" src="https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white">
</p>

<p align="center">
  <a href="https://undertow-two.vercel.app/">https://undertow-two.vercel.app/</a>
  ·
  <a href="https://perpl.xyz">Perpl</a>
</p>

Live prices every block. Liquidation zones from a snapshot about every 45 seconds. Trade preview sends no orders.

**Live** · chart, book, trades, zone bands
**Radar** · zones, warning, cascade estimate
**Wallets** · any address: positions, path, history

## Screens

**Live**

![Live](docs/screenshots/live-1500.png)

<table>
<tr>
<td width="50%">

**Radar**

<img src="docs/img/radar.jpg" alt="Radar">

</td>
<td width="50%">

**Wallets**

<img src="docs/img/wallet.jpg" alt="Wallets">

</td>
</tr>
</table>

## Architecture

```mermaid
flowchart TD
  chain["Monad 143 · Perpl Exchange"]
  cli["perpl-cli snapshot"]
  envio["Envio indexer"]
  refresh["refresher.py · every ~45s"]
  snap["snapshot_latest.json"]
  gql["GraphQL"]
  risk["risk.py · zones and cascade"]
  preview["preview.py · no orders sent"]
  live["live.py · Perpl WebSocket"]
  signals["signals.py"]
  server["server.py · dashboard"]
  bot["bot.py · paper"]
  rest["Perpl REST · verify only"]
  verify["verify.py"]

  chain --> cli --> refresh --> snap --> risk
  chain --> envio --> gql --> risk
  risk --> preview
  risk --> signals
  live --> signals --> server
  risk --> bot
  rest --> verify
```

```mermaid
flowchart LR
  data["Data · snapshot, chain, indexer"] --> risk["Risk · zones, cascade"]
  risk --> screens["Screens · Live, Radar, Trade, Markets, Protocol, Wallets"]
  risk --> trading["Preview · nothing is sent"]
  risk --> protection["Protect · paper bot, liquidation-aware rules"]
```

## Setup

Python 3, [`perpl-cli` 0.2.9](https://crates.io/crates/perpl-cli), and a Monad RPC.

```bash
pip install -r requirements.txt
cargo install perpl-cli --version 0.2.9 --locked
python -m undertow.server --refresh
```

Open http://localhost:8000. First refresh takes about a minute.

```bash
UNDERTOW_RPC=https://YOUR-RPC-URL python -m undertow.server --refresh   # if the public RPC is slow
python -m undertow.verify                                                # compare numbers with Perpl
python -m undertow.history_loader --days 7                               # wallet history beyond the default 1 day
UNDERTOW_NETWORK=testnet python -m undertow.server --refresh --port 8001
```

Live site: https://undertow-two.vercel.app/

Hosting: [docs/DEPLOY.md](docs/DEPLOY.md). Indexer: [indexer/README.md](indexer/README.md).
