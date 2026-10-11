# Publishing: GitHub and a live link

Status (2026-10-12): the code is on GitHub at https://github.com/dhruvjnv72/undertow (public, 73 files, no keys or data files).
Still to do: share the repository with `metropolis@hackathon.monad.xyz` (Settings, Collaborators) and publish a live link (below).

## 2. A live link

Pick one host that runs Docker. Both files below are **untested templates** (the Dockerfile itself was built and run locally).

* **Render**: New, Blueprint, pick the repository; `render.yaml` is read automatically. Enter `UNDERTOW_RPC` in the dashboard.
* **Fly.io**: edit the app name in `fly.toml`, then `fly launch --copy-config --no-deploy`, `fly secrets set UNDERTOW_RPC=...`, `fly deploy`.
* Railway, Koyeb and similar also work from the Dockerfile.

Notes:
* The first build compiles Perpl's Rust command-line tool: expect 10 to 40 minutes.
* Set `UNDERTOW_RPC` to your own Monad RPC (Alchemy etc). Public endpoints are rate-limited and were unreachable from some networks.
* Keep one instance always running. If the host sleeps idle instances, the data feed and the live WebSocket pause.
* The container could not fetch data on the author's laptop (its Docker network could not reach any Monad RPC), so confirm the Radar shows fresh
  data once after deploying (`/api/status` should show `age_seconds` under a minute).

## PythonAnywhere (the tab open in your browser)

Not a good fit, as far as I know: free accounts can only reach a short list of outside websites (Perpl and the Monad RPCs are probably not on
it), web apps cannot hold WebSocket connections, they expect WSGI apps rather than this server, and the Perpl command-line tool is a Rust
program that would have to be compiled there. Check their current help pages before spending time on it.
