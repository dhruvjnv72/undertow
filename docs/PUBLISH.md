# Publishing: GitHub and a live link

Status: the repository is committed locally and checked for keys (`data/` is git-ignored; the testnet wallet key is never committed).
The remote is set to `https://github.com/TylerDurden14/undertow.git`, but **that repository does not exist yet** (checked 2026-10-12),
so nothing has been pushed. Creating it needs your signed-in GitHub session.

## 1. Push to GitHub (about 2 minutes)

1. Open https://github.com/new while signed in as TylerDurden14. Repository name: `undertow`. Choose **Public** (the hackathon needs a public
   repository). Do NOT tick "Add a README", ".gitignore" or a licence (an empty repository pushes cleanly).
2. In the project folder, in PowerShell:

       git push -u origin main

   A browser window from Git Credential Manager will ask you to sign in to GitHub the first time. If you use two-factor authentication or a
   different GitHub name, change the remote first: `git remote set-url origin https://github.com/YOUR-NAME/undertow.git`.
3. Share it: repository Settings, Collaborators, add `metropolis@hackathon.monad.xyz`.

Commits are already authored as `Dhruv Gupta <dhruvjnv72@gmail.com>` (your git identity on this machine). If GitHub does not attribute them to
your profile, add that email at https://github.com/settings/emails.

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
