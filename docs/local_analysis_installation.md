# Local analysis installation

This installation runs the CTCC dashboard and offline analysis API on this PC. It does not connect to OKX or enable Demo or Live trading. The generated Compose file publishes the dashboard on `127.0.0.1` through a dedicated relay. CTCC, PostgreSQL and Redis stay on an internal Docker network. The relay listens on a separate network and can forward only to the API's mounted Unix socket. PostgreSQL and Redis use separate persistent volumes. It never reads the host `.env` or an OKX key.

Use the [Windows offline viewer](offline_snapshot_only_windows.md) for snapshot
analysis without Docker or Paper state.

## Prepare and verify

From a clean CTCC Git checkout with Python 3.12 available:

```powershell
py -3.12 -B -m scripts.prepare_local_analysis_installation --prepare
```

The command verifies `MANIFEST.sha256` and the exact Git commit and tree, then creates a new owner-only installation directory under `%LOCALAPPDATA%`. It prints the installation path and dashboard address, but no token or password. Keep that directory: it contains the local database password, API token, rendered Compose file, an empty `compose.env`, and their verification record. Preparation does not start Docker. The image build labels carry the exact Git commit and tree.

Before each start, check the same directory again:

```powershell
py -3.12 -B -m scripts.prepare_local_analysis_installation --check "<installation directory>"
docker compose --env-file "<installation directory>\compose.env" --project-directory "<installation directory>" -p "ctcc-analysis-<installation ID>" -f "<installation directory>\compose.yaml" config --quiet
```

Both checks must pass. The first check refuses changed files, extra files, a changed source manifest, and an incorrect private directory. The Compose file contains paths to secrets, not their values.

## Start and use

```powershell
docker compose --env-file "<installation directory>\compose.env" --project-directory "<installation directory>" -p "ctcc-analysis-<installation ID>" -f "<installation directory>\compose.yaml" up --build -d
```

Open the dashboard address printed during preparation. The default is `http://127.0.0.1:8100/dashboard`; readiness is `http://127.0.0.1:8100/readiness`. The dashboard asks for an API token. Open the private `api_token` file in the installation directory as the same Windows user, copy its contents into that prompt, and keep it out of chat, screenshots, and reports. The local analysis API uses the same token in the `X-CTCC-Token` header.

Manual Paper activity uses the same API token and persists through PostgreSQL across restarts. Automatic Paper ticks and execution stay off. To analyze historical data, select an exported MarketSnapshot JSON file. CTCC recomputes the analysis at its recorded capture time. To cancel or close, first read the local Paper ledger. Select the pending order or open position, enter an explicit close price when applicable, and confirm the action with the local token. The page uses only the local server. Readiness and the dashboard do not establish trading or market-data completeness.

The browser retains one unresolved-operation marker at this local origin across reloads and tabs. Use a browser with Web Locks and local storage enabled. The marker records only the operation type and opaque identifiers for the operation, target, runtime, and installation. The token, price, order payload, and ledger stay out of browser storage. If a write result is unknown, keep the marker. Restart the same local installation, read the persisted ledger with the local token, and explicitly confirm reconciliation before any further simulator write. A pending or missing record does not prove the previous action failed. A different installation, a same-process read, or unavailable browser storage keeps writes blocked. The browser never retries an unresolved write automatically.

## Stop, preserve, recover

```powershell
docker compose --env-file "<installation directory>\compose.env" --project-directory "<installation directory>" -p "ctcc-analysis-<installation ID>" -f "<installation directory>\compose.yaml" down
```

Do not add `-v`: PostgreSQL and Redis use installation-specific named volumes that retain local state. Do not delete the private installation directory while these volumes are in use. Re-running `--prepare` creates a different installation and does not overwrite one. Re-running `--check` verifies an existing installation without changing it.

Keep the exact old source checkout when trying a newer CTCC commit. Stop the
old project and retain its private installation directory and named volumes.
Back up both stopped volumes and verify the backup independently before
changing source. A new source is prepared with new volumes. If it fails, the
old source, installation directory and volumes can be restarted together.
The installer does not migrate saved Paper state across source versions.

This is a local analysis profile. It does not implement the separate DB0056 native trading deployment, grant privileges, Arm trading, or send orders. Building the pinned image may fetch locked build dependencies; the API and storage services use the internal network during operation. Actual Docker startup, volume persistence, and Windows bind-mount access need runtime acceptance on the target machine.
