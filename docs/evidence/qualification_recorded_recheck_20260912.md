# Recorded-only post-publication recheck checkpoint

Updated 2026-09-12. Code checkpoint
[`bf6c334`](https://github.com/holy1080111-cmd/CTCC-V2/commit/bf6c334f45fbe725f193f921f8d76748545ffde3)
implements the offline R1–R4 calculations and their ordered diagnostic composition.
It does not complete Execution Recheck, authenticate current IO/account data,
reserve risk, deploy the feature, submit orders or finish CTCC.

## Delivered behavior

- Replay the original source, G1–G11, stored G12/snapshot and original policies;
  freeze the original report/event, entry/SL/TP, zone, size/leverage and earliest
  deadline. A saved receipt can support diagnostics, never runtime admission.
- Rebuild current G1–G4 conditions for the original strategy, without generating
  a replacement event. Reject rewritten or incomplete four-timeframe history;
  check append-only closed-bar continuation and irreversible known invalidation.
- Recheck original timing, consumed-event claims and zone. Preserve each frame's
  unobserved intrabar interval; sampled data cannot establish the complete path.
- Validate the fixed original bracket using current ATR/noise/liquidity and
  opposing barriers. A newly confirmed nearer target obstacle cancels the old
  candidate; it does not produce a more convenient replacement TP.
- Run both original-entry and current executable-reference economics, followed
  by both same-account portfolio scenarios. Missing new account evidence cannot
  fall back to original G11. Original size/leverage and all policies stay fixed.
- Check cross-result source/policy/quote pins and strict nested model/JSON
  boundaries. Stop at the first failure. All eight computations passing still
  means `runtime_admissible=False`; no thirteenth PASS or ORDER_ELIGIBLE appears.

The [module guide](../recorded_recheck.md) documents exact responsibilities and
limits. The [R1–R7 plan](../qualification_recheck_plan.md) preserves full runtime
requirements; the [implementation tracker](../entry_qualification_implementation.md)
does not mark an offline calculation as a completed trading integration.

## Source integrity

Commit `bf6c334f45fbe725f193f921f8d76748545ffde3`, tree
`5240cdd581ef66fc9d120b5fbb1b54d55e5dde1f`, parent
`7dfc2df75437419c7eca04345d9bc2df36df6e0b` were saved and the pushed branch's
full SHA read back. The checkpoint has six new production modules, ten fixture/
test files, three docs and MANIFEST.sha256; no existing app/test/script/migration/
configuration/dependency/CI code changed. Unrelated user changes were not staged.

The worktree and exact Git archive passed the **522-file** canonical manifest.
Source ZIP SHA256 is
`c7b8c81e117718c19514c21b8ed5e04a7b1992072749ea342de2e7f225c9a174`;
the verified incremental Git bundle requires the exact parent above.
The unchanged standard Dockerfile was built from that archive as
`ctcc-entry-check:recheck-20260912`. All **516** applicable image-copied files
matched the frozen manifest. Local ZIP, bundle, image, full logs and receipt
remain under `reports/entry-acceptance-20260912`; runtime artifacts are excluded
from public Git, the source manifest and Docker build context.

## Tests and independent review

The final working-source new suite passed **1345 tests in 458.95 seconds**:
126 origin, 201 continuation, 218 current conditions, 300 fixed protection,
116 current risk, 139 coordinator, 4 native chain, 57 source and 184 independent
review cases. These are software test cases, not market samples; rerunning them
on another platform does not create additional independent cases.
All 16 new Python files passed Ruff check/format and Git whitespace checks.

Independent review found nested JSON round-trip and cross-result pin gaps; these
were fixed before the immutable code checkpoint. All owners' final focused runs
and the combined run passed. Favorable new quotes cannot repair a failed original
scenario; tests also demonstrate a genuine executable-scenario portfolio-cap
failure despite a passing original-price scenario.

Frozen full Linux unit/integration passed **5765 tests, 16 Windows-only skips,
5 warnings in 927.04 seconds**. The warnings are two existing dependency
deprecations and three intentional malformed-model serialization cases. Before
and after testing: Alembic **0016**, no schema drift, **522-file** manifest passed.
All **516** applicable image-copied source files matched the exact archive.

Frozen scoped Windows passed **4624 tests, 11 POSIX-only skips, 3 intentional
malformed-model warnings in 916.57 seconds**. Its explicit scope is qualification
tests, explicit candle quality, four trade-evidence files, qualification domain
models, analysis, market, indicators and strategies. This is not a full Windows
integration suite. The frozen manifest remained unchanged after testing.

[GitHub CI 34668676319](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/34668676319)
passed for the exact full code SHA. Job 103485706777 ran 02:48:42–03:14:34 UTC
(25m52s); complete verification and ephemeral cleanup succeeded. Its saved log
confirms execution authority zero, Alembic 0016/no drift and the 522-file manifest.
The full local Linux count above comes from its own complete log, not inferred
from quieter CI output. Any subsequent docs-only commit is a separate source
checkpoint and must not be mislabeled as this local platform run.

The first scoped Windows harness invocation named a nonexistent domain directory;
pytest exited 4 before running any test. Its log is retained. The corrected scope
uses the actual domain model test file without changing or skipping test code.

The frozen-source native Windows repeat passed **4 tests in 18.28 seconds**.
All four markers confirmed actual publication/readback before new requests, not
the conditional permission-denial path. These tests are already part of the
scoped suite and must not be added to its total as independent cases.

Native cases use real filesystem publication, six-file SHA/length readback and
then three MockTransport public requests. Their synthetic clocks/OHLC/account/
transport data remain explicit. A platform's conditional permission-denial path
is not successful publication; native receipt markers must be inspected separately.

## Safety, setup checks and remaining work

Acceptance uses a separately named Compose project, internal network, no host
ports, no credentials/proxies, no runtime hooks/order authority, read-only nonroot
runner and temporary PostgreSQL/Redis tmpfs data. It does not rebuild or restart
the existing `C:/CTCC-V2` deployment, which remains at `d3f206a` with the same
healthy service IDs and unchanged Live/Demo settings.

After checking exact container IDs, project labels, mounts, no host ports and
internal-network membership, only `ctcc-recheck-20260912` was removed: runner
`e89eb1e6a5a7`, PostgreSQL `9610084997d5`, Redis `af7212f01a89` and its internal
network. The tmpfs test data are recreatable; image/source/logs/backups remain.
Project container/network listings are empty, and the original three services'
healthy state and start times were verified unchanged.

Read-only setup inspection still found W32Time Stopped/Manual. After an initially
rejected public capture, two separate one-shot diagnostics passed an explicit
60-second age policy; one complete raw packet was retained and revalidated.
Its funding component was 43.087855 seconds old, not evidence that a stricter
candidate policy passed. A later comparison with one NTP server showed about
+17 to +22 milliseconds, but cannot diagnose or prove repaired the earlier
exchange timestamp disagreement. No OS clock, service, ACL or policy was changed.
All attempts and raw public diagnostics remain local; no private API was called.

R1–R3 are offline components only. R4 calculations do not provide an exact single
reservation amount or authenticated account completeness. R5 trusted per-TF OHLC/
WS/complete-account collection, R6 durable atomic event/risk reservation and R7
current-invocation publication-to-full-runtime composition remain unfinished.
The adapter inventory identifies actual existing parser/pagination/receipt gaps;
timestamp fields, hashes, merged snapshots and `complete=True` are not provenance.

Historical regime admission, guarded Demo submission, outbox, realized forensics,
real shadow/soak and final real evidence examples also remain pending. Genuine
new-pipeline Shadow / Demo samples are **0 / 0**; this work submitted zero orders.
Software tests and synthetic plots do not establish profitability.
