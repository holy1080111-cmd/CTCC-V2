# Windows tool package for physical0058

This is a separate exact-source installation, not the historical physical0056
trust descriptor. Its package/profile JSON is inert DATA. It never attests to
an account being flat, grants an Arm, or replaces current SQL/market/account
reconciliation. The preserved September DB0017 and its private archives remain
untouched. Do not point a fresh empty DB at an account to conceal old local
intents, unknown submissions, reservations or exposure.

The package offers four fixed applications.

| Tool | Application | Startup behavior |
| --- | --- | --- |
| Analysis | Existing offline snapshot viewer | No database, exchange or order calls; imported data remains unverified. |
| Paper | Persistent manual Paper and offline snapshot analysis | Requires a separately provisioned fresh PostgreSQL 0058 database, exact role checks and successful Paper restart recovery. |
| Demo | Canonical manual Demo application | Genuine physical0058 full role/catalog preflight, then genuine main startup/restart recovery. No restored Arm or scheduler. Normal protected holding and engineering round-trip remain separate UI actions. |
| Live | Dedicated manual Live application | Default OFF. Missing dedicated private runtime stays `NOT_CONFIGURED`. Configured startup only persists/reads the negative stop; a current exact human challenge is required for any one-shot entry. |

All listeners bind only `127.0.0.1`, use one worker, disable reload, proxy headers
and access logs. Opening the Live page does not supply trading authority. The
Demo compatibility flag still contains `0056` in its name; the new launcher
independently requires a real SQL head and full restricted graph at **0058**
before main startup, and checks main's actual recovery head again afterward.
No old descriptor or head projection is used.

## Prepare and install

Run the source-owned `scripts/Install-CTCC-0058.ps1`. `Plan` is the default.
Use a clean final checkout and a NEW release directory outside it. `Stage`
checks the full manifest, exact Git blobs/tree, Git archive commit and tar
tree, Windows dependency lock/metadata, and all59 migration-source files with
single actual source head0058. Both exact `source.tar` (compatible with the
owner maintenance tools) and `source.zip` are retained with independent hashes.
The application version comes from the archived `pyproject.toml`.

```powershell
.\scripts\Install-CTCC-0058.ps1 -Mode Plan -PackageRoot C:\CTCC-Releases\reviewed-final
.\scripts\Install-CTCC-0058.ps1 -Mode Stage -PackageRoot C:\CTCC-Releases\reviewed-final
```

Retain the printed public `package_identity_sha256` with final CI/release
evidence. Later steps require this exact digest. It does not signify CI passed.
`Install` requires a complete reviewed Windows CPython3.12 x64 wheel directory.
Every wheel is checked against the archived lock, and pip is invoked with
`--no-index --no-deps --require-hashes`. No dependency network fallback exists.
The isolated interpreter lives in this release's `runtime` directory. An OS
byte lock excludes installation/reinstallation while its tool is running.
Process exit/crash releases the lock without deleting safety records.

```powershell
.\scripts\Install-CTCC-0058.ps1 -Mode Install -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest> -Wheelhouse C:\CTCC-Wheels -DryRun
.\scripts\Install-CTCC-0058.ps1 -Mode Install -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest> -Wheelhouse C:\CTCC-Wheels
```

`Reinstall` repeats the same locked offline operation. It never deletes a
source directory, private profile, database, accepted journal or outbox. A
partial stage or failed install refuses; it is not automatically repaired,
deleted or activated. Keep it for diagnosis and stage a different directory.

The actual Windows PowerShell5.1 rehearsal found that embedded double quotes
in a native `-c` argument were removed before Python received them. The reviewed
bridge now uses a Python single-quoted `__main__` literal, preserving native
argument boundaries and source identity checks. A default Windows policy may
refuse `.ps1` execution. For a locally reviewed, hash-verified release, the
operator can invoke only that process as `powershell.exe -NoProfile
-ExecutionPolicy Bypass -File ...`; no machine/user policy change is needed.
The [double-click desktop launcher](windows_tool_desktop_0058_v1.md) uses the
release's isolated Python directly and does not invoke PowerShell.

## Private configuration and launch

`Configure` prompts locally and never accepts secrets as command arguments or
prints them. It creates a NEW NTFS directory under LocalAppData with only the
current user/System granted access. No machine policy or existing ACL changes.
Configure each desired tool separately, using different loopback ports.
The Demo wizard offers Standard (default) or the canonical HighVol Momentum
profile. This only selects the new immutable configuration. It neither changes
an existing Control policy nor bypasses complete reconciliation, flat start,
the current policy pin or a separate manual Arm. HighVol uses the same risk,
qualification, publication, recheck, reservation, intent and submission chain.

```powershell
.\scripts\Install-CTCC-0058.ps1 -Mode Configure -Tool analysis -Port 8128 -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest>
.\scripts\Install-CTCC-0058.ps1 -Mode Configure -Tool demo -Port 8129 -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest>
.\scripts\Install-CTCC-0058.ps1 -Mode Configure -Tool live -Port 8130 -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest>
```

The wizard prints only the resulting private file locations. The local API
token remains in the private `api_token` file; paste it only into the local
tool password field, never chat, a URL, logs or a report. The page holds it in
memory. Notion worker flags default OFF; existing reviewed destination/token
files can be bound by the private profile without moving them into a package.

For normal use, double-click `source/Open-CTCC.cmd`, select the matching private
`tool-profile.json`, and wait for the local page. Selection is explicit on each
launch. The desktop route opens a page only after checking the newly launched
process's real listener PID, one-use startup nonce and exact package identity.
It does not select an existing profile, run the wizard or restore Arm.

`CTCC_WINDOWS_TOOL_CONFIG_FILE` is the single opt-in configuration route. It
requires owner-only regular files, exact source/package/schema pins and a
closed per-tool field allowlist. It supplies **every Settings field** at init
priority with `_env_file=None`, including defaults and `None`, so neither a
working-directory `.env` nor ambient secret/risk/Live flags override it.
Without this variable, the existing Settings behavior is unchanged.

```powershell
.\scripts\Install-CTCC-0058.ps1 -Mode Check -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest> -ProfileFile <private profile path>
.\scripts\Install-CTCC-0058.ps1 -Mode Launch -PackageRoot C:\CTCC-Releases\reviewed-final -PackageIdentitySha256 <reviewed digest> -ProfileFile <private profile path>
```

`Check` verifies only local files/interpreter/dependencies/settings. Its output
explicitly says `database_head=NOT_OBSERVED`, with no account/flat/Arm claim.
Actual startup/recovery can still refuse. Demo credentials/three restricted
TLS roles and an existing stopped legacy state must already be genuinely
provisioned. Live uses its distinct dedicated runtime configuration, never a
Demo DSN/identity fallback. Its legacy write flags remain OFF even when the
dedicated source credentials are configured.

### Persistent Paper on Windows

Use the installed package with a fresh, dedicated PostgreSQL 17.11 database at
revision `0058`. It must have the reviewed fresh owner profile and separately
provisioned restricted Paper actor. Complete that maintenance step before
configuring Paper. Use authenticated, direct TLS connections for the two roles
and the SHA256 of the independently verified CA. The retained bootstrap-owner
ledger has a separate continuation. [Paper database and role requirements](paper58_owned_startup_design_v1.md)

```powershell
.\scripts\Install-CTCC-0058.ps1 -Mode Configure -Tool paper -Port 8131 -PackageRoot $ReleaseRoot -PackageIdentitySha256 $PackageIdentity
.\scripts\Install-CTCC-0058.ps1 -Mode Check -Tool paper -PackageRoot $ReleaseRoot -PackageIdentitySha256 $PackageIdentity -ProfileFile $PaperProfile
.\scripts\Install-CTCC-0058.ps1 -Mode Launch -Tool paper -PackageRoot $ReleaseRoot -PackageIdentitySha256 $PackageIdentity -ProfileFile $PaperProfile
```

`Configure` prompts for the two database URLs without displaying them. It
prints the new private `paper-profile.json` path for `$PaperProfile`. The
profile binds three separate secret files in the same owner/System-only NTFS
directory. `Check` verifies those files, the CA and the installed package
without connecting to PostgreSQL. `Launch` holds the package runtime lock
until the server stops. Use the printed loopback URL after `/readiness`
reports `ready`. The dashboard and Paper token use the same installation
binding as the recovered service. Stop with `Ctrl+C` before reopening or
reinstalling.

## Offline fault checks

Run the component checker from a reviewed checkout with the Windows CPython
3.12 validation lock and the exact source project installed in that isolated
environment. Use a short build directory when installing the source wheel so
the Windows wheel builder can create deeply nested module paths.

```powershell
& $ValidationPython -B -m scripts.verify_offline_chaos_subset_v1 --archive $SourceTar --archive-sha256 $VerifiedSourceTarSha256 --output $NewEvidenceDirectory
```

Use the independently verified TAR digest. The checker verifies the source and
installed package inventory, runs the fixed Windows cases on loopback, and
publishes a report, JUnit and SHA256SUMS. The report records
`scope=local_regression_only` and `full_chaos_acceptance=false`.

## Upgrade, activation and rollback

`Upgrade` is `Stage` into a different version directory. It neither changes a
current profile nor starts a process. It does not migrate DB0017 or automatically
adopt its data. The existing reviewed owner0057→0058 tool remains a separate
maintenance operation with its original source/route/role/flat local guards.
An account not yet reconciled against all preserved local state remains
ineligible for policy rollout. Complete current account, positions, ordinary
pending, algo pending, tracked exposure, local uncertain state, reservations
and unresolved intent reconciliation is required before any actual risk
configuration/rollout; the native action independently enforces it. No installer
closes positions, cancels protection, changes existing leverage or Arms.

`Rollback` verifies a preserved package. Launch only its matching private
profile after the existing recovery procedure; there is no DB downgrade,
automatic profile rebinding, restored Arm or order replay. If the installed
schema/catalog differs from the old release, its genuine startup must refuse.
Preserve existing protection and risk holds while reconciling. Do not erase
unresolved state to make a rollback start.

Staging and installation operate only in the explicitly chosen release
directory; they do not start an OS service, Docker, WSL or a database, or change
an existing deployment. Explicit Launch starts the chosen local tool. Final deployment remains gated by
the final exact-source CI and parent-controlled real reconciliation. Gate3 and
actual trading examples are deferred at the user's direction and are not
reported as passed by this installer.
