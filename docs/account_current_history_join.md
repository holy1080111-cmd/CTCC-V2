# Versioned fresh current Demo account source and recorded history join

The 34-stream v5 capture reads current inventory before historical product
chains. Its native proof starts the current-data lease at the first current
response, so a lengthy historical page chain can consume that lease before
proof readback. The new `ctcc.demo_current_account_plan.v6` is a separate,
13-stream current-only capture. It retains the original B1 raw/page-chain
journal, exact Demo session binding, UID/mainUid, global registration pin,
unfiltered positions and ordinary pending, four formerly pinned algo-pending
queries, balance, position risk, instrument metadata, both leverage modes and
the final config read. Nonempty paginated responses still require an explicit
empty terminal page. No v5 packet, receipt, plan hash or policy is relabeled.

The v6 packet is `ctcc.demo_current_account_capture.v6`. Its inherited plan
history timestamps are not queried and cannot establish a historical window.
The packet remains incomplete, even when the observed current inventory is
empty. The v6 recorded current verifier replays the complete original B1
journal, page bytes, TLS metadata, exact queries, causal timestamps, terminal,
plan/session/UID binding and local checkpoint hash before deriving a current
observation. The sealed v5/v6 plans omit four additional currently documented
algo types, so even empty queried subsets now yield
`algo_type_coverage_incomplete`, `observed_flat=false`, and `DENY`. The original
packets and policy hashes remain replayable; their earlier flat diagnostic is
revoked for current use. No v5/v6 result proves flat-start permission, all
products, or current DB state.

`join_recorded_account_sources` independently replays the old v5 original
history chain and the new v6 original current chain. It rejects changed exact
UID, mainUid, credential-session binding, registration pin, currency, account
level, position mode, source reference, local B1 checkpoint hash, or reversed
capture chronology. It records the requested history-to-current time gap and
flags gaps over 120 seconds. Matching recorded checkpoint hashes are only
evidence of two B1 observations; they are not a fresh database revision under
lock. The join never promotes a caller receipt or hash to source authenticity.

`AccountCaptureJournalRepository.read_locked_current_history_join` now opens a
new PostgreSQL transaction, acquires the existing DB0017 exact-UID advisory
and scope-row locks, rereads both complete B1 chains, and reads the persisted
ledger checkpoint before releasing the lock. The local checkpoint hash must
equal the hash recorded by both captures. A changed revision, missing chain,
wrong identity or reversed clock fails closed. Its separate versioned receipt
binds source references, the pure join receipt hash, local state hash,
account/ledger revision numbers and the measured locked read interval. A
zero revision remains explicitly uninitialized. This only proves a local DB
state match *at that readback time*; it does not keep the lock after return,
close exchange history, or prove an exchange-wide atomic revision. The
receipt keeps the original join blockers and always has `DENY` and no snapshot.
The versioned locked receipt separates `recorded_pre_lock_blocking_reasons`
from `locked_readback_blocking_reasons`. The former preserves the pure join's
original `current_local_revision_readback_required` finding without altering
its hash; the latter omits that one finding only after the under-lock state
hash matches. All other blockers remain, including the unclosed history tail.

The native initial issuer now selects a separate proof contract for the exact
v6 plan: `ctcc.demo_account_native_clock_proof.v3`, with its own policy digest,
versioned no-clobber `readback.v3`, and a v2 diagnostic. Replay requires the
original 13-stream B1 journal and packet, exact page/raw/TLS joins, native
phase witnesses, independent host/OKX time probes and separate durable
readback inside the first current page's 30-second lease. It never imports the
v5 historical verifier or relabels a v5 proof. The v5 plan continues to select
the byte-identical v2 native proof/readback contract. An old or synthetic
proof replay cannot issue a new owner; genuine private account acquisition is
still required to exercise this route outside tests.

The joined diagnostic always retains blockers for the unclosed history tail,
possible late records, funding accrual, complete net loss windows, streak seed,
historical native high-water proof, fresh DB revision readback and current
native owner. The pure join continues to return `snapshot=None`,
`account_complete=false`,
`account_revision_published=false`, `execution_authority=false` and `DENY`.
The separate `ctcc.recorded_account_current_history_join.v2` explicitly pins
the V7 eight-algo current source policy and plan while retaining the V5
original history plan. It replays both complete chains, checks exact
account/session/registration/config, recorded local checkpoint and chronology,
and emits a distinct policy hash and receipt schema. A V7
`current_observed_flat=true` remains a bounded source diagnostic; the join
still retains the unclosed history tail, funding/loss/HWM, current DB readback
and native owner blockers. It never publishes a snapshot or authority. The V1
policy hash, receipt bytes and default locked repository path stay unchanged.
The native V3 flat proof rejects the four-type current packet as incomplete;
a separate V7 native proof and readback contract can replay the eight-type
packet. The locked repository requires an explicit
`expected_policy_sha256=V7_POLICY_SHA256` to read the V5-history and V7-current
original B1 chains under one PostgreSQL exact-UID lock. It replays the pure
V2 join, compares both recorded checkpoints with the locked ledger checkpoint,
and emits `ctcc.demo_account_locked_source_join.v3` with the pure-join and V7
current-source policy digests. The original blockers remain in
`recorded_pre_lock_blocking_reasons`; the locked blocker list removes only
`current_local_revision_readback_required` after the checkpoint hash matches.
No missing chain or unknown revision becomes zero. The V3 locked receipt still
sets flat-start permission, account completeness and execution authority to
false, retains an unclosed history tail, and cannot become a
PortfolioRiskSnapshot. Synthetic lock tests pass; the separate isolated
PostgreSQL test is pending an explicit migrated `DATABASE_URL`. The V7 native
history-join coordinator now selects that locked V3 receipt only for an exact
V7 native current capture. In the same invocation it checks the V7 native
diagnostic, current-source and pure-join policy pins, source references,
session and local checkpoint, the recorded-to-locked blocker transition and
the original 30-second lease before consuming the one-use carrier. Its own
versioned V2 receipt remains `DENY`, with no flat-start permission or
portfolio snapshot. Synthetic coordinator tests exercise this wiring;
authenticated Demo acquisition and isolated PostgreSQL integration remain
unexecuted.

The offline `verify_history_tail_prerequisites` diagnostic now replays three
distinct original B1 chains in order: a v5 history capture, a v6 current
capture, and a later overlapping v5 history capture. It binds exact UID,
mainUid, credential session, registration evidence and origin, account mode,
currency, local checkpoint, query cursor proof and capture chronology. Late,
conflicting or missing overlapping rows are preserved as blocker findings.
Even matching repeated pages only prove the bounded requested generation-time
window at those observation times. They do not prove exchange-wide EOF,
funding accrual chronology, future late-arrival finality, an atomic exchange
revision or a complete loss seed. The versioned receipt therefore always has
`history_tail_closed=false`, `snapshot=None`, `account_complete=false` and
`execution_authority=false`; a saved receipt is compared with a fresh replay of
the original chains before its findings can be used. The tests use synthetic
B1 chains only.

The separate `verify_followup_current_diagnostic` adds a fourth original v6
current capture **after** the overlapping history continuation. It replays
the preceding three chains, independently joins the continued v5 history to
the fourth v6 current chain, and requires four distinct original capture IDs.
The joins enforce exact UID, mainUid, session/registration/mode, currency,
recorded local checkpoint, raw/page-chain proof and chronology. The new
receipt binds both replay hashes, retains any late/conflicting/missing row
findings, and marks the bounded follow-up query as observed. It removes only
the earlier diagnostic's `post_continuation_current_capture_required` blocker
from its own remaining-blocker list. An expired fourth-capture current-data
lease is rejected rather than recycled as fresh. It still has no native current
proof, locked current DB revision, exchange-wide EOF, funding-accrual chronology,
future-late-arrival finality, complete loss seed or PortfolioRiskSnapshot.
Therefore `history_tail_closed=false`, `snapshot=None`,
`account_complete=false`, and `execution_authority=false` remain immutable.
This is synthetic offline coverage, not authenticated Demo acceptance.
