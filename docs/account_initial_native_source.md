# Initial native account source: versioned companions

This slice is under source review and subsequent bounded validation. Genuine
account capture and native acceptance have not been performed. B5 diagnostic V2
still has no component owner or full risk snapshot. Historical captures without
original proofs remain unknown. The original design is retained in
[Native-clock proof contract](account_native_clock_proof.md).

`capture_initial_native_account` accepts an exact controlled Demo session, the
original application's configured PostgreSQL `async_sessionmaker`, and an empty
existing absolute companion directory. It accepts no UTC callback, datetime
publication barrier, caller client, native handle, proof DTO or accepted receipt.
The configured factory must use exact `AsyncSession`/`AsyncEngine`, the reviewed
`postgresql+asyncpg` driver and the original `autoflush=False`,
`expire_on_commit=False` session settings. Foreign factories/subclasses are denied.

The private initial issuer creates invocation/task/loop/PID/thread and the native
start. It admits existing reviewed OS clock observations and fixed OKX global
public-time GETs before account acquisition and again after source closure. This
slice has no post-G12 entry; integration must consume the genuine private G12
issuer rather than a supplied datetime. Historical proof replay never remints a
current carrier. The public result is always a source diagnostic with `DENY`,
`owner=None`, `snapshot=None`, no account revision and no order authority.

Both native entrypoints synchronously burn the controlled session before the
first native sample, host probe or await. A private in-memory claim binds that
exact session, plan and credential object to the original task, loop, PID and
thread. Only the matching initial stage's private observer may adopt it once
for the original B1 bootstrap. A concurrent caller, used boolean, foreign
observer or changed session cannot substitute for this handoff. Failure and
cancellation leave the session used; the private claim is removed on exit and
is never returned, persisted or reused after restart. Ordinary bootstrap and
materialization retain their existing early one-use behavior. This ownership
mechanism does not authenticate registration, create an account-complete
snapshot or grant public-source or order authority.

## Source phases and original B1 compatibility

The additive private collector/bootstrap observer defaults to `None`. Existing
public APIs, default calls and B1 event/packet bytes retain their original
meaning. Native acquisition uses original TLS guards, signing, raw parsing,
page-chain inventory, the B1 durable journal and separate DB chain readback.

Every page emits `request_start`, `request_dispatch`, `headers_received`,
`body_exhausted` and `response_closed`, with explicit collector request/page
ordinals. `source_closed` occurs after the owned HTTP client actually closes.
Each hook obtains its own native stamp and uses the same reviewed integer UTC
conversion for its original UTC value. `request_dispatch` is an additional
witness directly before the actual GET, with no intervening await; it does not
replace B1's earlier signing/request-start timestamp. Ordinary UTC dependency
calls do not infer, create or backfill phase witnesses.

Original B1 bounded source/terminal finalization creates child tasks. A one-use
private wrapper registers only that exact child for the duration of the original
coroutine. It records native start/end and invocation/finalizer identity binding,
revokes registration in `finally`, and grants only native UTC sampling. Foreign
tasks, subsequent children and the finalizer itself cannot claim source phases,
clock stages or carriers. Parent cancellation may finish durable cleanup; it
cannot admit new source GETs or mint ownership.

## Immutable companion and replay

No migration or new account/risk ledger is introduced. The existing native
no-clobber artifact directory stores `host-before.json`, `exchange-plan.json`,
`exchange-before.json`, `exchange-before.raw`, `exchange-before-trace.json`, the
corresponding after files, `proof.json`, and `readback.json`. Existing OS
observations retain the exact binary/resource profile. Account clock probes
use a separate versioned plan and receipt with the fixed global OKX public-time
endpoint, direct verified TLS and causal exchange timestamp brackets. The
single-request adapter has no credentials, proxy, redirect or retry path; it
does not reuse candle plan or receipt types.

The private time-probe trace uses account-owned explicit phase hooks for
request, headers, chunks, exhausted body and response closure, plus original
client closure. Hashes/ordinals bind that probe's raw bytes, receipt, scope,
invocation and phase. Rehashed missing/late phases, private headers, partial or
truncated chunks, absent closure, synthetic TLS and changed server timestamps
deny replay. Negative OS observations and safe public-time bytes/trace remain
retained. Malformed partial JSON and credential echoes are withheld with explicit
retention state. Missing/failed receipts never become complete probes. Private
signed account headers are never recorded.

`ctcc.demo_account_native_clock_proof.v2` has a preregistered policy digest. It
binds exact B1 capture/plan/packet/session digests, terminal sequence/head,
original journal owner/local checkpoint, every phase chain, page/raw/TLS/event
joins, both original finalizers and all clock files. Exact UID remains in private
B1 storage; the companion exposes only its bound scope digest. Current-source
replay requires complete supported queries, known balance/mode/identity,
consistent measured reads and observed flat inventory. This proves no global
atomic account revision or flat-start trading permission.

The exact `ctcc.demo_current_account_plan.v6` instead selects
`ctcc.demo_account_native_clock_proof.v3`, a separate policy digest and
`ctcc.demo_account_native_clock_readback.v3`. It replays the original 13-stream
current-only B1 journal under the v6 current-source policy, then applies the
same strict page/raw/TLS joins, clock files, native phase witnesses, finalizers,
30-second earliest-current lease and separately opened companion readback.
V5 retains its original v2 proof and readback bytes. A v6 proof cannot be
relabelled as v2, and neither version grants `account_complete`, a component
owner or execution authority. The history query and measured historical HWM
must be proved and joined separately before any full portfolio snapshot.

Nonempty V6 inventory uses a **separate** diagnostic proof contract,
`ctcc.demo_account_exposed_native_clock_proof.v4`, with its own policy digest
and `ctcc.demo_account_exposed_native_clock_readback.v4`. It applies the same
original raw/page/phase/host/OKX-time and no-clobber readback checks, but
requires at least one replayed current exposure row and no current-source
blocker other than the explicitly unresolved protection/local join. It never
relaxes the V3 flat-only replay. The resulting
`ctcc.native_demo_exposed_observation.v2`
retains row/page hashes and counts, both proof/readback hashes, and explicit
unknown history, local liabilities, protection and exchange revision. It has
no owner, packet lease, flat-start permission, portfolio snapshot or execution
authority. The original V1 recorded-only diagnostic remains unchanged.
Synthetic replay and storage tests do not establish genuine account acceptance.

Replay rejects foreign mapping, scope and chain objects before touching their
callbacks. Every original `JournalReadback` is revalidated before any later
packet payload is read. Source joins and phase metadata require the exact JSON
shape and scalar types before canonical byte comparison: `true` cannot match
index `1`, and `false` cannot match index `0`. Every native stamp is an exact
built-in dictionary with exact integer UTC/monotonic nanoseconds, validated
before timestamp conversion or expiry checks. The second source freeze's
numeric-alias counterexamples are preserved separately; the third freeze fixes
this ingress boundary without changing original B1 journal semantics.

Source persistence, proof file flush/readback and a separately opened root
readback all finish before the sole private current-only carrier issuance path.
The readback receipt pins exact proof/clock inventory and original root identity.
After that file readback, the issuer opens another original PostgreSQL journal
read transaction under the exact-UID account lock. It requires the same chain
length and head, every event/raw/packet byte, original DB-recorded timestamp,
and nonregressing readback timestamps before issuing the diagnostic carrier.
The second read is bounded by the original current-data clock lease and never
reconstructs source evidence from a supplied packet or receipt. It strengthens
original-source continuity but does not by itself verify a complete account,
historical provenance, exchange-wide atomic revision, or
`source_authenticity_verified` for execution.
Proof, receipt and already saved source journal remain on any late failure.
Existing Windows storage provides file flush/readback, without an atomic
power-loss directory durability claim. Partial/source-only crash tails remain
unaccepted and cannot resume acquisition.

The original earliest current page's native response-close fixes both UTC and
monotonic expiry at at most 30 seconds. After-health/probe acquisition, source/
proof readback, issuance and consumption finish inside that original lease.
UTC freezing or a later good read cannot extend it. Original 5ms deviation and
60s native causal lease bounds remain unchanged. The private consumer pops its
carrier first, burns its fence on every outcome, checks exact invocation/task/
loop/PID/thread/cancellation, samples the independent reviewed OS clock and
validates both expiry domains. No B5 component owner is registered or old HWM
sample upgraded. Complete history/lifecycle/streak authority remains absent.

## Same-invocation current/history diagnostic

`capture_native_current_history_join` accepts one controlled v6 current Demo
session, the configured PostgreSQL session factory, an empty native companion
directory and a recorded v5 history capture ID used only as a lookup key. It
uses the existing private native issuer and original B1 acquisition; no
caller-supplied packet, receipt, owner, clock, HTTP client or account-complete
flag is accepted. After native source/proof readback, it reads both original B1
chains and the local checkpoint under the existing exact-UID account lock.
The v5/v6 join independently checks exact UID, main UID, credential-session
binding, region, account/position mode, source chronology and local revision.
The locked read is bounded by the **original** v6 current-data monotonic lease.
It then consumes the one-use native carrier in the same task; it never copies,
exports, remints or renews that bearer. Mismatch, cancellation or expiry leaves
no carrier and retains the already durable source/proof journal.

The result is `ctcc.native_current_history_join_diagnostic.v1`, with both
receipt hashes, source references and unresolved locked-readback reasons.
Before accepting the locked readback as a diagnostic observation, the
same-invocation coordinator checks its exact scope and session binding, the
equal recorded/DB checkpoint hashes, nonnegative ordered account/ledger
revisions, local hold count, unresolved history blocker and fixed denial flags.
A mismatched readback burns the current carrier and returns only a denial.
The focused replacement-readback tests exercise this validation without
claiming a genuine PostgreSQL or authenticated account run.
It always has `owner=None`, `snapshot=None`, `account_complete=false`,
`account_revision_published=false`, `execution_authority=false` and `DENY`.
This is not a post-G12 account request: the existing initial stage supplies
its own barrier, not the G12 publication barrier. It does not close late
historical arrivals, attribute funding, derive net losses or streak seed, prove
an historical native HWM, import quarterly bills archives, or reserve risk.
The locked DB observation ends with its transaction and cannot authorize a
later order. Mechanism tests use no account network or database requests;
genuine controlled Demo acquisition and PostgreSQL acceptance remain pending.

## Controlled fresh-plan construction for later operator preflight

The operator-controlled runtime prepares a new pinned
`AllProductDemoAccountCapturePlan` or `CurrentDemoAccountCapturePlanV6` and
`ControlledDemoAccountSession` after the
original global registration, exact UID and credential-session binding are
verified. Use the reviewed native UTC observation to declare `created_at`, and
preserve explicit millisecond history cutoff/window and full V4 inventory/limits
chosen before acquisition. Pin `capture.plan_sha256(plan)` and construct the
session with the original credential handle's matching session binding. Pass the
application's original `AsyncSessionFactory` and a new empty private companion
directory to the initial entry. Do not alter an old plan's `created_at`, history
cutoff, digest or session binding to rescue a stale/rejected capture.

This document does not read credentials, invoke OS/network probes, change DB
settings, Arm Demo or authorize Live. Genuine native acceptance, PostgreSQL
durability/crash cases and original B1 byte/regression checks remain separate
required validations. New fixtures exercise mechanism/replay only.
