# Native Live manual tool

This is a separate one-shot Live route. All legacy Live order-write and
automation flags remain OFF. Installing source, loading configuration, opening
the UI, reading an account and passing calculation tests do not Arm Live.
Gate3 and actual exchange acceptance are deferred by the current project
scope. Nothing here claims profitability or completed Micro Live acceptance.

## Private configuration

`CTCC_NATIVE_LIVE_RUNTIME_CONFIG_FILE` must name an existing private JSON file
inside `%LOCALAPPDATA%/CTCC/private`. It explicitly pins one database/owner,
four distinct restricted operator/writer/financial-collector/reconciler roles, TLS CA
file/hash and three separate pre-existing private custody directories. It
cannot fall back to the Demo database or an unverified generic database URL.
Missing configuration returns `NATIVE_LIVE_NOT_CONFIGURED`; no database or
exchange request is attempted. The repository contains no actual configuration
file or credential.

The JSON schema is `ctcc.native_live.private_runtime.v1`. Required fields are
`schema`, `region`, `database_oid`, `owner_oid`, `ca_file`, `ca_sha256`,
`operator`, `writer`, `collector`, `reconciler`, `journal_key_file`, `account_source_root`, `public_root` and
`evidence_root`. Each role object has exactly `oid`, `name`, `dsn`; each DSN is
an explicit `postgresql+asyncpg` URL without URL query parameters. All four
roles must reach the same host/port/database and use the pinned CA. Secrets
belong only in this private file or existing private exchange configuration.

`native_live_operator_control_schema_v1.live_operator_control_grant_sql_v1`
provides15 fixed installation statements: schema usage,12 SELECT grants, and
INSERT/UPDATE on Live controls only. It cannot grant an intent, reservation,
financial or outcome write. It rejects PUBLIC/system role names. Grant
statements are DATA; this module does not install or execute them.

## Startup and operator flow

`inhibit_configured_live_runtime_v1(runtime_owner)` is the startup negative-only
database operation. It acquires the actual restricted operator profile,
account lock and original legacy exclusion, durably stops/disarms the pinned
account, commits, and reads back in a separate session. It creates no Control
and makes no exchange/flatness claim. Unknown commit/readback blocks readiness.

Use ONE background worker task for this entire sequence:

1. Open `configured_live_runtime_v1()`.
2. Open `open_live_operator_session_v1(runtime, instrument_id, strategy,
   direction, controlled_exit_authorized=...)`. The last value REQUESTS a
   distinct controlled-exit confirmation; it grants no permission.
3. Publish `session.challenge_data` only to an authenticated operator UI.
4. Immediately await `run_owned_live_operator_once_v1(session)` in that same
   worker. Another authenticated HTTP task may call
   `signal_live_operator_approval_v1(...)` to signal the nonce response.
5. Before leaving the context, call the separate owned lifecycle's
   `reconcile_owned_live_postsubmit_v1(qualified_owner, physical_handle)` in the
   SAME worker. A physical response alone is never protected/reconciled success.

The current human must enter both `ARM_OKX_LIVE_REAL_MONEY:<nonce>` and
`CLEAR_OKX_LIVE_STOP:<nonce>`. A requested controlled roundtrip additionally
requires `CLOSE_OKX_LIVE_MICRO_POSITION:<nonce>`. No candidate exists before
this confirmation. A stale/incorrect answer, WAIT, FAIL, cancellation or
timeout burns the challenge. Challenge lifetime is120 seconds; native clock
continuity is sampled while waiting. Actual Arm/qualification lifetime is at
most30 seconds with no extension. There is one submission per Arm.

The current positive route supports the Global public/account source profile. Other regions remain DATA-only until their own reviewed public bridge is installed. The source-proven account must be in net position mode; hedge mode is not
silently changed or reinterpreted. Only linear base-valued USDT SWAP contracts
with `ctMult=1` are currently supported. Existing isolated leverage must be
source-proven, integral, at most3x and within configured caps. This route never
changes leverage/account mode, cancels existing protection or flattens another
position to prepare deployment. Micro notional is capped at25 USDT; structural
stop risk is capped at0.25 USDT and the shared0.5%/1%/60% ceilings also apply.
Contract size is the actual instrument's minimum; an instrument that cannot
fit the caps is rejected.

## Qualification and final boundary

The worker retains the original configured credential lease. After approval
it captures fresh account data, records a genuine financial epoch observation,
clears the stop only through the explicit procedure, Arms through an exact CAS
and creates one OriginalCandidate. It runs the existing eight-family Live
G1–G11 math, publishes/reads back the six G12 files without clobbering, measures
the barrier, then captures NEW mutable account/public data after that barrier.

Incremental account refresh preserves old immutable history with its original
timestamps. It reads full cash archives from the original eight-day start,
recent fills/bills/order overlaps, full closed-position history and NEW current
balances/positions/pending/algo/instrument/leverage/config data. Missing,
conflicting, newly late prior cash rows or vanished archived rows fail closed.
Old current data is never relabelled fresh. Every new revision binds the old
source/bank hashes plus all retained/new bytes. Two refreshes are supported in
one original lease; no retry or source substitution is added.

The post-G12 recheck keeps the original event, zone, entry, SL, TP, policy and
expiry. It recomputes actual daily/seven-day signed cash flows at the new public
evaluation time and uses the original sampled financial epoch/HWM. A known
empty local ledger requires an actual scoped SQL read; missing rows do not
become an empty exposure list. Atomic reservation/intent commit and independent
readback precede the physical transport.

The four physical hooks require a registered original same-task opaque owner;
ordinary dataclasses/receipts/keys/flags do not create it. Each boundary reopens
the same public raw bank, replays immutable committed calculations and performs
an ADDITIONAL final-current fixed-candidate/risk check. This extra calculation
does not rewrite the committed recheck/evidence digest. Its risk, margin and
notional cannot exceed the durable reservation. Cash-window changes during the
synchronous final calculation cause rejection. Actual DB Control/catalog,
account/legacy locks and the physical one-shot guard remain required.

`revoke_live_operator_session_v1(session)` revokes local authority synchronously
before any database await. Context cleanup durably stops/disarms; a cleanup
failure is uncertain, never permission to resume. Restart uses the separate
negative startup procedure and never restores an Arm. Unknown submission keeps
the event/reservation/intent for reconciliation; it is not automatically sent
again. The lifecycle owns protection, reconciliation, durable resolution and
any separately authorized controlled exit.

## Catalog and private-file boundaries

The writer/operator catalog checks source-derived Live4 columns, defaults, nullability, CHECK predicates, primary/unique/foreign keys, indexes and the active-hold predicate, as well as the existing table/function/trigger and exact privilege checks. Unknown schema changes, schema/database CREATE privileges and delegable table/function grants fail closed. This compares reviewed source definitions; it never adopts observed database metadata as its baseline. Private runtime/CA/key files must be regular, single-link files with private ownership/ACLs, stable native file identities and no symlink/reparse ancestors. The CA context loads reviewed bytes, without reopening an unchecked path.

## Commands and validation limits

`python -m scripts.native_live_operator_v1` checks only local configuration and
prints OFF. `--run-once --instrument ... --strategy ... --direction long|short`
prepares the genuine source/challenge and waits for current manual phrases.
`--controlled-roundtrip` additionally requests the distinct exit confirmation.
No keys/DSNs/UIDs/IPs are command-line arguments. The private journal key descriptor has exactly `path` and `sha256`; raw key material is not returned in a handoff. Terminal input is cancellable.

Unit and authored isolated PostgreSQL tests use clearly declared synthetic
seams for arithmetic/locking/failure coverage. They are not actual account,
human Arm, exchange execution, protection or Micro Live acceptance evidence.
Do not label this tool `MICRO_LIVE_READY` until all exact-source engineering
checks and the separate lifecycle requirements genuinely pass.

Frozen Windows unit regression for this implementation:179 passed,0 failed,0 skipped. Ten isolated TLS PostgreSQL cases are authored and collected; they require the root-composed actual DB0058 migration and have NOT been run locally. Actual venue source/order/protection/Micro Live acceptance remains unexecuted.
