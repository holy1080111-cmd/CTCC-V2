# Windows offline snapshot viewer

This is a foreground, localhost-only viewer for an existing MarketSnapshot JSON
file. It has no Paper, database, OKX, Demo, Live, Docker or external asset route.
Imported data remains unverified and cannot authorize a trade.

Use Python 3.12 from a clean, exact CTCC checkout. On the current PC the CTCC
virtual environment contains Python 3.12; the Windows `py -3.12` launcher does
not find it. Run the following commands with that Python 3.12 executable from
the checkout root:

```powershell
& "<CTCC Python 3.12 executable>" -B -m scripts.prepare_local_analysis_installation --prepare
& "<CTCC Python 3.12 executable>" -B -m scripts.run_offline_snapshot_only --check "<installation_root from prepare>"
```

Preparation creates a new owner-only installation directory under
`%LOCALAPPDATA%` and prints its path. It verifies the source commit, tree and
manifest, then creates a local API token. It also writes files for the separate
Docker installation, but this viewer does not start Docker or connect to its
database. The viewer refuses an installation prepared from another source
commit or with altered private files. `--check` does not start a server.

To reopen a stopped viewer, reuse its installation root and the checkout used
to prepare it. Run `--check` again. Use the printed loopback port to check for
an existing Windows listener, substituting the port from your installation:

```powershell
Get-NetTCPConnection -LocalPort 8112 -State Listen -ErrorAction SilentlyContinue
```

If the port is free and `--check` succeeded, start the viewer:

```powershell
& "<CTCC Python 3.12 executable>" -B -m scripts.run_offline_snapshot_only --serve "<installation_root from prepare>"
```

If a listener already occupies the port, reuse it only after `/readiness`
returns `OFFLINE_SNAPSHOT_ONLY`, the expected source commit, and
`execution_authority: false`. Otherwise, prepare a separate installation
with `--prepare --host-port <unused-port>` and leave the existing listener running.

Open the printed `http://127.0.0.1:<port>/dashboard` address while `--serve`
is running. As the same Windows user, open the installation's private
`api_token` file and enter its contents into the page. Select an exported
MarketSnapshot JSON file. The page sends its exact bytes to the local analysis
route and displays the capture time, contract, price, market regime and the
recomputed direction, volatility and data quality for all four timeframes.
Expand **完整 JSON 結果** to inspect the complete response.
The token is not stored in browser
storage. Keep it out of chat, screenshots and reports.

Press Ctrl+C in the launcher window to stop. The viewer stores no analysis or
Paper account state. Its only HTTP paths are `/dashboard`, `/readiness`, and
`POST /api/analysis/snapshot`; the API documentation, exchange and Paper routes
are absent. An imported snapshot's source authenticity, trade readiness and
execution authority remain false even when descriptive analysis succeeds.

For an exported public snapshot with a receipt, select both files together in
the same file dialog. The browser checks the pair before analysis and keeps
the receipt in the browser. An invalid or mismatched receipt stops the import.
The viewer still accepts one snapshot for unverified offline analysis.

Open `/readiness` to confirm the viewer is running and see the source commit.
Choose your installation's printed address if you prepared it with a custom port.
