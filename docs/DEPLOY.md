# Putting the dashboard online (the "live product link")

The dashboard is read-only: it has no keys, cannot send orders, and its only inputs are public Monad data. That makes it safe
to host publicly. Hosting needs an account with a hosting provider, which only you can create.

## Option 1: any host that runs a Docker container (recommended)

The `Dockerfile` in the project root builds Perpl's CLI and runs the dashboard with its own data refresher.

    docker build -t undertow .
    docker run -p 8000:8000 -e UNDERTOW_RPC=https://YOUR-RPC-URL undertow

Tested on 12 October 2026: the image builds (216 MB) and starts, serves the page, and answers 503 until its first data refresh. The refresh
itself could not be tested on the machine this was built on (its Docker network could not reach any Monad RPC), so confirm it after deploying.

Point your host at the repository (it detects the Dockerfile) and set `UNDERTOW_RPC` to your own Monad RPC (for example an
Alchemy URL). The public RPC rate-limits at 15 requests per second and has been unreachable from some networks. Give the
container at least 512 MB of memory; the first data refresh takes about a minute, during which the page shows an error.

What the hosted version does not include: the Envio indexer (Protocol tab shows only snapshot-based numbers) and the bot.
For the Envio panel, host the indexer separately (Envio's own hosting, see the Envio documentation) and point
`undertow/envio_client.py` at its GraphQL URL.

## Option 2: run it on your laptop during the demo

    python -m undertow.server --refresh

and record the screen. Acceptable for videos; a judge-clickable link needs Option 1.

## Before you publish

* Check that no key file is in the repository: `data/` is git-ignored, and `data/testnet/key.txt` must never be committed.
* The dashboard sends nothing about visitors anywhere and stores nothing on the server.
