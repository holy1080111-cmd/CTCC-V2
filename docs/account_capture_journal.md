# B1 private account ingestion journal

This slice records observations; it is not a complete-account issuer or an order
permit. `collect_bootstrap_recorded` uses the existing one-use
ControlledDemoAccountSession, bootstrap checkpoint and owned account collector.
The old bootstrap/runtime methods and packet schemas are unchanged. No alternate
collector, endpoint, signer, retry, redirect or order route is introduced.

All public receipts keep account_complete and execution_authority false and
admission DENY, including complete_recorded. A returned runtime dataclass, packet,
source label or hash is not an ownership capability. Before reducing an owned
capture to a portable runtime result, the journal binds the original collector
carrier, every actual raw buffer, page observation and TLS proof. The finally
recorded packet must retain that exact identity.

## Durable observations and private data

DB0020 adds one private immutable table, demo_account_capture_events. Capture
start is committed and read back before the first request. The event includes
the exact private UID/currency, plan/version/hash, session and invocation-owner
bindings, requested streams, local checkpoint and actual observation time.

Request-start, actual headers receipt, body progress, stream exhaustion, page
validation, terminal raw scan and capture outcome have separate chained events.
Before terminal secret scanning, source-derived query/cursor/row identities are
represented only by digests and row ordinals. Exact query/cursor values enter
raw_finalized only after the full invocation secret set is closed and checked;
unsafe values remain withheld. Exact safe row identities remain in the final
private raw/packet. Matching immutable-history overlaps have original row locators; conflicting
content denies completed recording. No malformed source is sorted, deduplicated,
filled or replaced. An absent request is unknown, not an empty account.

Non-200 status, media/encoding/size/TLS rejection before reading retains the known
headers-receipt time and bounded status only. Its body is **not_read**, with zero
observed bytes; this slice preserves the collector's existing rejection policy.
It does not drain foreign, closed or rejected responses to claim raw completeness.
Header values, credentials, signatures and exception text are never journaled.
If a clock sample fails after headers or bytes arrived, their known status/count
is retained with the missing observation time explicitly null. A later clock is
not substituted for that missing sample, and capture remains incomplete. Every
request, header, chunk (even below the progress persistence threshold), measured
EOF and portable parser completion sample is compared to the preceding actual
sample. A reversal persists the observed bytes/hash and both real sample times,
then rejects the packet and completed capture. A missing EOF clock retains the
observed exhaustion fact with a null completion time. No time is repaired.

Normal UID, balances, positions and exchange identifiers are private account
evidence and may occur unredacted in safe private raw bodies. They are excluded
from the public receipt allowlist. Public projections contain opaque IDs, hashes,
bounded states/counts and receipt times, not queries, raw bodies, UID/mainUID,
account amounts, source headers or SQL/transport diagnostics. Notion and release
artifacts must use only this public projection unless a separate private package
has been explicitly authorized.

## Secret-safe raw retention

Raw buffers stay in memory for the bounded invocation: at most 256 KiB per page
and 4 MiB total, further restricted by the pinned plan. Each incoming chunk is
counted and hashed as actually observed. A chunk exceeding a buffer bound records
its observed count/hash and incomplete-buffer state, then stops; no truncated
prefix becomes a complete response. Body progress is persisted at the first
chunk and fixed 4 KiB progress thresholds; final body/retention observations
include the actual last measured count. These are prefix hashes, not proof of
future bytes or a durable body.

No plaintext page is persisted until the invocation stops issuing signatures.
The final secret set includes all credentials and every signature issued during
that invocation. Each whole raw buffer and bounded decoded JSON is scanned at
terminal finalization, including on failure/cancellation. This catches secrets
split across chunks, JSON escapes and later signatures present in earlier pages.
The same rule applies to source-derived metadata: an early plaintext row identity
cannot be saved on the assumption that a later scan could retract it. Early row
locators use canonical identity hashes, row hashes and ordinals, never plaintext
row or instrument identities. The only early request values are fixed endpoint,
stream and page index; source-derived cursors and queries are hashed.
Every finalized page states that the terminal secret set was closed.

Complete safe pages remain evidence after later parsing, replay, cleanup,
checkpoint, commit-readback or public-receipt failure. A safely decodable partial
buffer may be stored but retains body_partial and no manufactured completion
time. Secret-bearing or unverifiable partial content is withheld; only its
actual observed metadata/hash/count remains. No redacted replacement is labelled
as the source body. Previously durable events/raw bytes are never deleted after
a late failure.

## Transactions, readback and bounds

Each append uses the existing DB0017 Demo UID advisory lock before scope rows.
The capture journal sequence is independent of account/ledger revisions. The
repository never changes claims, reservations, intents, tombstones, Arm, EStop or
control ownership. A changed local checkpoint makes the bootstrap incomplete.

Event bytes, optional safe raw/packet bytes and predecessor hash commit together.
Exact repeat persistence is idempotent; differing bytes under one identity are
rejected. An independent session reads back the exact event and predecessor
before returning an audit persistence receipt. This avoids rereading the full
chain after every append. Explicit read_chain and recovery audit the whole chain.

Actual source observation times must be no later than the database receipt, which
must be no later than the independent readback observation. Reversed database
receipt order is rejected. DB and host clocks are separate evidence: disagreement
denies acceptance, and neither clock is silently offset or substituted.

The table's trigger binds cumulative exact event/raw/packet octets, limited to
64 MiB and 8,192 events per capture. A page payload is at most 256 KiB and one
canonical packet at most 16 MiB. Full audit reads retain the bounded whole chain;
the result is an audit surface, not an unbounded history service. Chunk progress
can require roughly 1,024 commits at the maximum raw budget, plus page and terminal
events. This cost has not been benchmarked with a real exchange/DB deployment.
Cancellation finalization has a fixed ten-second budget; unfinished persistence
leaves earlier durable evidence and no accepted completion. Durable append cannot
guarantee that an asynchronous commit acknowledgment will arrive before timeout.

SQL triggers reject UPDATE/DELETE/TRUNCATE and enforce scope, chain, hashes and
false authority fields. Downgrade first takes ACCESS EXCLUSIVE NOWAIT in one
statement on qualification_account_scopes, then demo_account_capture_events, in
the migration transaction. Removing the child table also removes FK triggers on
the parent, so the parent lock is explicit and acquired before the child lock.
It then requires the owned journal table to be empty. It cannot discard durable
journal evidence through a check-before-lock race.

## Unknown owners and process loss

Hard process death can destroy raw bytes still in RAM. The journal does not claim
full raw survival, replay a missing response, or infer an unanswered request's
result. The last durable prefix/hash is retained; additional unobserved bytes
remain unknown. An encrypted quarantine is outside B1.

After the original capture deadline and exact head check, recover_interrupted
may append interrupted_owner_unknown with unavailable_owner_unverified. A
deadline alone does not prove process death. The recovery event explicitly says
owner liveness is unverified and process_death_confirmed is false. Recovery time
is labelled as a recovery observation, never the original receipt time. It grants
no new ownership and cannot resume HTTP acquisition. An old worker's later append
conflicts with the terminal head and fails closed.

Actual SIGKILL/DB-restart evidence belongs to the separate isolated durability
harness. Synthetic unit tests verify bookkeeping and rejection only. Native TLS,
actual account history, host clock admission, PostgreSQL runtime/schema/trigger
acceptance and real process-loss recovery remain separate required validations.
Opening/retention/funding/peak completeness and a trusted account revision issuer
remain unimplemented by B1; the journal supplies no complete-history watermark.
