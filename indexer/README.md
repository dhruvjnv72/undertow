# Undertow indexer (Envio HyperIndex)

Records Perpl events on Monad (mainnet 143 and testnet 10143) into Postgres, served as GraphQL by Hasura.
The dashboard's Protocol tab reads it through `undertow/envio_client.py` and shows volume, fees, liquidations, deposits
and withdrawals when it is running.

What is indexed (`config.yaml`): `MakerOrderFilledV2` (trades and volume), `PositionLiquidated`, `PositionDeleveragedV2`,
`CollateralDeposit`, `CollateralWithdrawal`, `AccountCreated` (wallet address to account id), `FundingEventCompleted`.
Handlers: `src/handlers/exchange.ts`. Entities: `schema.graphql`. Market decimals: `src/perps.ts` (regenerate with
`python indexer/gen_perps.py` when Perpl lists markets).

## Running it (Windows)

Envio does not run natively on Windows. It needs WSL 2 and Docker. What worked on this machine:

1. Docker Desktop (`winget install -e --id Docker.DockerDesktop`) and an Ubuntu distro (`wsl --install -d Ubuntu`).
2. Inside Ubuntu: Node 24 (nodesource `setup_24.x`) and Docker Engine (`apt install docker.io docker-compose-v2`,
   `service docker start`). Docker Desktop's own WSL integration did not activate, so Docker runs inside Ubuntu.
3. Copy this folder and `abi/` into Ubuntu's own filesystem (much faster than `/mnt/c`), for example `~/work/indexer`, `~/work/abi`.
4. `npm install`, `npx envio codegen`, then `npx envio dev -r` (`-r` wipes the local database; use it whenever config start blocks change).
5. GraphQL: http://localhost:8080/v1/graphql, admin secret `testing` (Envio's local default).

Gotchas found:
* **TypeScript must be 5.x.** TypeScript 7 has no `ts.sys` and Envio crashes with "Cannot read properties of undefined (reading 'fileExists')".
* `package.json` needs `"type": "module"` (handlers are ES modules).
* The public Monad RPC limits `eth_getLogs` to 100 blocks, so config sets `initial_block_interval`/`interval_ceiling` to 100.

## Data source and history depth

`config.yaml` currently uses the public RPC from a recent start block (no account needed, slow, shallow history).
For the full history since Perpl's deployment use HyperSync: create a free token at https://envio.dev/app/api-tokens,
put `ENVIO_API_TOKEN=...` in `indexer/.env`, delete the two `rpc:` blocks and set the start blocks to
`54773010` (mainnet) and `62953` (testnet).
