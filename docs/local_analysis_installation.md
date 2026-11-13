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

## Export an OKX public snapshot for local analysis

Run the exporter separately from the offline viewer or Paper service using the
CTCC checkout and its installed dependencies. Use two fresh owner-private
directories outside the checkout, one for the raw journal and one for the
export. On Windows, the existing `scripts.local_analysis_private_dir.create_private_work()`
helper creates these directories with the same owner-only permissions as a
local installation. On Linux, create them with mode `0700`.

```powershell
python -B -m scripts.export_public_analysis_snapshot --instrument BTC-USDT-SWAP --journal "<private journal directory>" --output "<private output directory>" capture
```

The command also supports `ETH-USDT-SWAP`. It makes one anonymous attempt at the
fixed Global OKX public REST and WebSocket origins, keeps the original journal,
and verifies it again before exporting. It reads no account credentials and
does not start a trading service. Select the resulting `MarketSnapshot.json`
in the viewer. Keep `snapshot-receipt.json` with the journal so that the source
time, instrument, and hashes can be checked later. Incomplete capture leaves
the rejected journal for diagnosis and produces no usable snapshot. A failed
output readback may leave files in the output directory. Use files only after
the command succeeds and the receipt hash matches the snapshot.

To check the original pair in the browser, select the snapshot and
`snapshot-receipt.json` together in the same file dialog. The copied
`<name>.receipt.json` filename is also supported. The browser checks the exact
snapshot SHA-256, instrument and observation time against the receipt before
analysis. An invalid or mismatched receipt stops the import. The receipt stays
in the browser. A snapshot selected alone still runs offline analysis and
shows that no receipt was provided. A matching receipt does not authenticate
the source or grant trading authority.

To reproduce an existing accepted snapshot without network access, replace
`capture` with `replay --plan-sha256 <digest> --journal-sha256 <digest>` and use
another empty output directory. Replay retains the original observation time.
The exporter requires ticker/mark and quote receipts within five seconds of
capture completion and allows a funding data-return age of at most sixty
seconds for local analysis. Observations dated after receipt still fail.
These exports carry no trading eligibility or historical availability claim.

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

## Save and review the Paper ledger offline

The local Windows Paper installation on port 8102 can save its simulated
orders, positions and balances to a portable JSON file. Start the original
Paper installation and wait for the dashboard to be ready. Pause manual
operations during export and use a new private output directory outside the
checkout. On Windows, use the existing
`scripts.local_analysis_private_dir.create_private_work()` helper to create an
owner-protected working directory. On Linux, use a directory owned by the
operator with mode `0700`.

    python -B -m scripts.paper_portable_ledger export --token-file "<original Paper directory>\api_token" --directory "<private output directory>"

Record the returned export SHA-256 with the file. The tool makes two
authenticated reads of the same persistent local runtime and verifies that
the view is unchanged. It keeps the original simulated order and position
identifiers in `paper-state.json`. The token and its raw installation
binding stay out of the export. Existing files are preserved. Use an export
only after the tool reports verification.

    python -B -m scripts.paper_portable_ledger inspect --directory "<private output directory>" --sha256 "<recorded export SHA-256>"

Inspection works with Paper stopped. It checks the file against the
recorded digest, validates order-to-position links, recalculates statistics
from the Paper view, and recomputes the existing durable Paper state
checksum. The export captures the local API view at the time of the two
reads. Keep the original Paper database and recovery journal for service
restart and full database recovery.

This is a local analysis profile. It does not implement the separate DB0056 native trading deployment, grant privileges, Arm trading, or send orders. Building the pinned image may fetch locked build dependencies; the API and storage services use the internal network during operation. Actual Docker startup, volume persistence, and Windows bind-mount access need runtime acceptance on the target machine.
