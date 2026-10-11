@echo off
REM Starts the Undertow dashboard with its own data refresher. Double-click this file.
REM Leave the window open while you use the page; close it to stop.
REM The RPC list below is tried in order; replace the first entry with your own (for example Alchemy) when you have one.
cd /d "%~dp0"
set UNDERTOW_RPC=https://rpc2.monad.xyz,https://rpc-mainnet.monadinfra.com
set PYTHONIOENCODING=utf-8
echo Starting Undertow at http://localhost:8000 (first data appears in about a minute)
start "" http://localhost:8000
python -m undertow.server --refresh
pause
