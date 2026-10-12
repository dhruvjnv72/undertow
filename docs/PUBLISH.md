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

## Hosts that work from your network (checked 2026-10-12)

Render and Railway time out from the laptop this was built on; these load fine: Hugging Face, Koyeb, Fly.io, Northflank, Zeabur, Heroku, Google Cloud.
Whatever you pick, the deployed site runs on the host's servers, so only the sign-up and button-clicking need your network.

**Tested locally (Docker Desktop on the author's laptop):** `Dockerfile.fast` builds in about 15 seconds (no Rust compile), runs as a normal user,
honours `$PORT`, and **fetched fresh data inside the container** (block 112,729,374, 679 positions, open-interest check 16/16, formula check 679/679)
about two minutes after starting, with `UNDERTOW_RPC` set to `https://rpc2.monad.xyz,https://rpc-mainnet.monadinfra.com`. From inside that container
`rpc2` was unreachable and `rpc-mainnet.monadinfra.com` worked, so the automatic fallback is what produced the data. With `UNDERTOW_RPC` unset the
first refresh can take several extra minutes while the unreachable default endpoints time out, so set it. The hosts' own build and run steps were NOT tested.

### Option A: Hugging Face Spaces (free Docker hosting)

1. Make an account at huggingface.co. Create a new Space: SDK **Docker**, Blank template, **Public**. Name it `undertow`.
2. In the Space's Settings, Variables and secrets, add a secret `UNDERTOW_RPC` (your Alchemy URL, or leave it unset to use the public fallbacks).
3. In the project folder (PowerShell), push the prepared branch. Git will ask for your Hugging Face username and, as the password, an access token
   with write permission from huggingface.co/settings/tokens:

       git remote add hf https://huggingface.co/spaces/YOUR-HF-NAME/undertow
       git push hf hf-space:main

4. The Space builds (about a minute, since the compiled Perpl tool is downloaded and checksum-verified, not compiled) and then shows the dashboard at
   `https://YOUR-HF-NAME-undertow.hf.space`. Free Spaces sleep after a period of inactivity, which pauses the data feed until the next visit.

### Option B: Koyeb, Northflank, Zeabur, Fly.io

Create a service from the GitHub repository `dhruvjnv72/undertow`, choose the **Dockerfile** builder, set the Dockerfile path to `Dockerfile.fast`
(so it does not compile Rust), expose port 8000 (or let the host set `$PORT`), and add the environment variable `UNDERTOW_RPC`. Fly.io also needs
`fly.toml` and its command-line tool. Check each host's current free-plan terms; I did not.
