# Native archival source facts V1

This additive source owner acquires fresh Demo B1 all-product V5 raw pages,
observes and replays their exact B3 membership/latest head, captures native
positions-history pages, and requires a DB0046 append followed by separate
transaction readback. It does not create a portfolio, loss seed, HWM, complete
account revision, qualification, G12 receipt, reservation, intent or order.
All execution and predictive OOS authorities remain false.

The fixed entry is `acquire_native_history_source_facts_v1`. Its inputs are an
active exact Demo read lease and its actual private client, expected UID/mainUid,
declared global routing evidence and instrument configuration, native SQLAlchemy
factories, and two fresh private artifact roots. There is no input clock, raw
page, group, seed, complete/passed boolean, signer, callback, HTTP client or
alternative endpoint. Credentials remain in the active lease and signing memory.
Signup/registration site stays `UNKNOWN`; configured global origin and signed
GET success do not authenticate signup region.

The private native positions identity/archive has an explicit separate version
`ctcc.native_positions_history_raw.v1`. It binds the four original config keys
`uid`, `mainUid`, `acctLv`, `posMode`; `declared_scope_currency=USDT` is the B1
history/risk scope, never an exchange-attested settlement currency. Current OKX
docs do list config `settleCcy`, but describe a setting for USD-margined contracts.
That optional original value is exposed separately as
`account_usd_contract_settle_currency`; it is `None` if absent/empty. The private
archive preserves each original positions row `ccy` as margin currency, or `None`
if absent/empty. Neither a configured scope, config setting nor instrument name
fills missing row currency or proves P&L settlement. Original response bodies
are never edited. See the official [account configuration](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration)
and [positions history](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history)
definitions reviewed 2026-10-22.

The new historical contract does not call `_capture_initial_current`, bind old
V7 current proof as V5 history, or alter old proof hashes/contracts. It reuses
the existing one-use `_claim_initial_session`, `_initial_stage`, explicit B1
phase and bounded finalizer primitives. Position phases have a separate keyed
observer. The original diagnostic positions API retains its old clock-injected
behavior and 240-second budget. It still cannot issue an owner. The new path
requires native transport and non-null TLS evidence; its private mock-provider
unit cases are explicitly synthetic and never native acceptance.

Timing bounds remain separate:

- Account-current admission is 30 seconds; public-current admission is 5 seconds.
- DB0046 receipt consumption is within 5 seconds of issuance. The issuer consumes
  it immediately after the actual seal/readback, before later replays or awaits.
- This archival acquisition retains the original 60-second native anchor and
  5-millisecond drift checks through sources, companions, DB readbacks, issuance
  and consumption. A slow page population fails incomplete. No reanchor,
  timestamp repair, retry, smaller-population substitution or timeout widening
  makes it complete. Separate measured-page epochs need another reviewed version.

The original query end is derived internally from native time at millisecond
precision. B1 keeps the existing seven-day bound, 300-second generation tail,
120-second query-end-age and page/cursor/row/byte rules. Fill execution time,
exchange update/generation time and the new measured receipt remain different.
No after-close capture backfills a historical point-in-time measurement.

An actual fresh raw-facts owner can be earned even when older B3 prefix captures
lack native companions. The complete bounded B3 sequence, original sources,
findings and latest head must still replay; no sequence is skipped/reset and no
synthetic genesis is created. The resulting DTO preserves original sources,
references, raw row locators, B3 receipts and positions raw archive for a future
assembler. `native_history_prefix_verified` remains false. Old V8 obligations
and its `original_native_source_ownership_required` diagnosis stay unchanged.
An actual in-holding B1 witness cannot be reconstructed from later cTime/uTime.
Missing legacy ownership or a conflicting/invalidated prefix remains visible.

Funding is fixed `NOT_ACQUIRED`; accrual events and native account-wide funding
completeness remain unknown. No public funding transport is present in this
slice. Existing pure funding replay remains diagnostic and earns no ownership.

Before capture awaits, a fresh no-clobber claim is flushed and read back in a
private artifact root. Original B1 raw failure evidence stays in its existing
journal. Positions publish bounded native phase metadata at actual request,
dispatch, first post-send receipt, separate TLS validation, nonempty chunk,
exhaustion, closure and validation boundaries. An early/cancelled response close
has a distinct failed-closure record and can never satisfy success inventory.
The old B1 phase list is never extended with fabricated positions phases.

Position success bodies remain bounded in memory until signing stops, then all
bodies are scanned against every final known token and decoded JSON string.
The closed B1 credential/session/signature set seeds one private list shared
by every later positions request. Before bundle encoding or custody, the final
union is checked against original wire bodies across the entire B3 population,
all positions bodies and clock companions. Encoded wrappers cannot substitute
for that scan. Only the pinned nonsecret session label is allowed in metadata;
credentials and signatures have no metadata exemption.
Partial/timeout/cancel cleanup retains safe bytes or a fixed privacy refusal;
unverifiable partial JSON, including excessive nesting, and secret-bearing
bytes are withheld while the fixed refusal marker and failure footer survive.
A new fixed
limit is 18 position/config requests, 4MiB plus two 64KiB config bodies, 4096
phase events and eight empty chunks per response. Prefix hashing is incremental.
A small failure footer remains possible after the primary phase limit. No raw
headers, exception/DSN text or credentials are published. Artifacts and DB raw
bundles are private account evidence, not public reports.

DB0046 retains generic bytes up to 64MiB without a source-kind discriminator;
the new semantic proof is replayed before and after custody. Its original
deployment/catalog/durability checks stay intact. Private fresh native factories
and repositories make caller factory changes ineffective inside repository
awaits; outer phase boundaries recheck both configured and private pins. Shared
engine/class-global hostile mutation and hostile DBA are outside this bounded
in-process trust model. DB retention is not WORM or historical PIT authority.

The new bundle decoder has its own strict canonical/duplicate/finite integer
rules. It does not reuse or loosen the exchange packet decoder's small-number
or string limits: actual native nanoseconds and bounded raw hex are required.
Depth is limited to 32 and structural/node allocations to 262144. Source count
and aggregate original bytes are checked before hex assembly; the exact expanded
JSON size is computed without allocating its hex payloads, then checked against
the actual canonical output and 64MiB limit. The prefix reader checks aggregate
bytes after each bounded chain read, rather than accumulating an unbounded set
of individually bounded chains. An over-budget prefix is incomplete, never
truncated or substituted.

Artifact readback uses the exact produced filenames and byte/hash pins, not a
directory-discovered acceptance list. Unknown names are rejected before reading
any content. Each known file is read with its actual owned byte-length bound;
the raw-root aggregate is bounded by 64MiB bundle plus 4MiB+128KiB positions raw
plus 2MiB metadata, and the companion root by nine 512KiB parts. Native context,
lease/factory pins and the original deadline are checked between every read.
The same exact inventory is rechecked after consumer DB awaits. Late refusal
preserves all already published evidence and cannot restore the burned owner.

The opaque owner is task/thread/process/loop-bound. Consume burns synchronously
before returning its fixed awaitable; subsequent context, lease, clock, file,
head, membership or replay failure stays burned. The read lease also receives a
bounded in-process use tombstone (4096, no eviction/retry). Portable DTOs, saved
bundles, read-only DB reconciliation and caller booleans cannot mint an owner.
Owner state has no reverse strong task reference. Cancellation/failure clears
consumed state; an abandoned coroutine or retained traceback can still retain
private in-memory references until released. No prompt GC guarantee is claimed.

This component is prepared on exact 48eb source. Syntax/static inspection and
synthetic provider tests, if later executed, do not prove real native 60-second
completion, authenticated account history, full PostgreSQL acceptance, Gate3,
OOS, Demo or Live trading. Native/provider and full matching-source acceptance
remain separate work. No default production wiring or authority is enabled.
