# Source-replayed G1–G12 checkpoint acceptance

Code checkpoint: `7c7e9b57795527c1acba5ccb27e4dd5b1ef25f37` on
`develop/v1.7-entry-qualification-evidence`, parent `667577d`, frozen tree
`c942753c22098089fef8d4b85a2f065c7ee70c83`.
The follow-up documentation records acceptance of that exact code; it must not
be described as a different source tree's full local test execution.

## Implemented boundary

The [pre-evidence engine](../qualification_engine.md) calls the actual G1–G11
evaluators from original raw inputs, and strictly replays saved results. The
[G12 coordinator](../qualification_evidence_gate.md) replays all eleven gates,
prepares/renders the original source, then checks real one-shot publication and
complete readback. Entry/SL/TP never move to make costs or risk pass. Existing
reports cannot renew evidence permission; entry expiry remains exclusive before
and after publication.

463 added test cases comprise 200 engine/source/review, 212 G12 and 51 independent
G12 review cases, including OS-specific cases. Synthetic publisher-contract
tests do not count as native disk publication or exchange evidence.

## Frozen source and integrity

- 503-file manifest verified in the worktree and exact extracted Git archive.
- Canonical archive manifest SHA256:
  `58bc3ebb8de506d1b3211d5d2ae14d93ce6941ae25b66a73d4873aa307510039`.
- Source ZIP SHA256:
  `4e1c8fda817657f8e75e5d5285240c4b03028750d9a84c51500f8b2334081922`.
- Verified incremental Git bundle SHA256:
  `12b38f8acf4bee2bcaae59e68a7f435cb7695a8b3e5bac1a1b95361fddb91c02`;
  requires parent `667577d355f0ff7a6f8800c0c3ad93171ce207cf`.
- Source-built image `ctcc-entry-check:engine-c942753`, ID
  `sha256:28ae542cd35260385b318669d91b18ba4e50a919afae13b7a30b3fe009a4819b`.
  All 497 applicable copied source-file hashes matched the frozen manifest.
- No migration, dependency, runtime wiring or account configuration changed.
  Ruff check/format passed all eight added Python files; whitespace checks passed.

## Windows — exact source, explicit scope

- Expanded pure-unit suite: **2898 passed / 8 POSIX-only skipped / 3 warnings**,
  366.13 seconds. Includes all 51 independent G12 review cases. This is not the
  entire platform's unit/integration suite.
- Evidence models, renderer, storage, pipeline and collected-quote chain:
  **384 passed / 3 POSIX-only skipped**, 79.68 seconds, no warnings.
- Explicit native owned-root G12 long/short rerun: **2 passed**, 4.93 seconds;
  both printed `written_and_readback_verified`. These two cases are already in
  the expanded suite and are not two additional unique cases.
- The earlier child-process run correctly failed closed on native ancestor
  PermissionError. The main process subsequently used the user-restored
  permissions and successfully exercised actual publication. No ACL, sharing,
  path-safety or storage check was weakened. Earlier statements of an outstanding
  Windows blocker describe that restricted context, not this later main-process
  result. File flush/report-last completion does not claim power-loss atomicity.
- The three warnings are existing intentional malformed-model serialization
  tests. They are not new G1–G12 warnings.

## Linux and GitHub — passed

The exact source-built image passed the complete unit/integration suite in
separate project `ctcc-engine-20260912`, with internal networking and only tmpfs
test PostgreSQL/Redis. It has no host ports, account credentials, runtime hooks
or execution authority: **4420 passed / 16 Windows-only skipped / 5 warnings**,
466.66 seconds. Before and after testing, Alembic heads/current were 0016 with
no drift, and the 503-file frozen manifest passed. The skips are OS-specific,
not Windows acceptance; Windows was validated independently above. Two existing
dependency deprecations plus the three intentional serialization warnings remain.

[GitHub CI 34664846194](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/34664846194)
matches the complete code SHA and **succeeded**. Job `103474578529` finished at
2026-09-12 01:37:37 UTC, after 10 minutes 38 seconds. Complete verification,
503-file manifest, Alembic 0016/no drift, execution authority zero and ephemeral
CI cleanup were checked. The 4420 count above comes from the separate local full
Linux log, not an inferred count from the quieter CI output.

The local runner, PostgreSQL/Redis containers, their recreatable tmpfs data and
internal network were removed after exact project/identity/mount/port checks.
No persistent volume was present. Source archives, image, logs and visual packets
were retained. Existing deployment containers remained healthy with unchanged IDs.

## Visual and authority boundaries

Two native Linux packets were additionally produced by the actual frozen G12
coordinator. All twelve copied files matched receipt hashes/lengths, and all ten
PNGs were visually inspected. Original geometry, source pins, crop/UTC labels,
eleven pre-G12 report claims and unqualified warnings were readable and intact.
The long fixture retained entry 100.99 / SL 98.32 / TP 107.23; the short retained
99.01 / 101.68 / 92.77. These are synthetic OHLC and fictional typed account
claims, with explicit synthetic clocks, not observed trades.

All twelve gates passing still leaves `qualified=false`. Source/account
authentication, atomic reservation and full execution recheck remain false.
Trusted adapters, intervening-OHLC/event recheck, worst executable economics,
durable risk/event consumption, every Demo submit path, outbox, forensics and
real shadow/soak acceptance are unfinished. The four final real examples and
complete implementation-plan acceptance must not be marked Done.

Existing deployment `C:/CTCC-V2` remains separate and was not restarted or changed.
No exchange order was submitted. Full local receipts, logs, archives and visual
packets are retained under `reports/entry-acceptance-20260912`, excluded from Git.
