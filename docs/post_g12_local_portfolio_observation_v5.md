# Post-G12 local portfolio observation V5

`publish_capture_inspect_local_portfolio_v5` is a read-only, DENY-only
diagnostic. It calls the existing V4 entry in the same invocation, reopens its
immutable event receipt and observation sidecar, then makes two separate
PostgreSQL `read_portfolio_checkpoint` calls under the exact Demo account UID.
Each call reads all settlement currencies and the legacy local state. The V5
receipt is published to its own pre-provisioned no-clobber root and read back;
it contains hashes and fixed blocking codes, never the raw local checkpoint,
credential, UID or SQL detail. An old V4/V5 receipt cannot resume a run.

The opt-in `publish_capture_inspect_local_portfolio_v5_pinned` entry requires
the runtime-issued V7 Demo GET lease before delegating and checks that same
lease again after both local reads. A closed or changed credential/session
rejects the result, even if V4 or V5 already published valid diagnostic bytes;
those bytes remain available for forensic readback. The older entry remains a
legacy diagnostic and does not gain a lease or execution authority. Neither
entry is wired into the production Demo execution route; the source-owned
original candidate and complete account revision remain unavailable.

The two observations must have the same canonical local-state hash, exact
account/environment/currency, and strictly ordered UTC times after V4's event
read and before the original candidate deadline. A changed state, unknown
scope, clock conflict, failed read, corrupt V4 evidence, cancellation or late
V5 publication failure cannot create an authority claim. Already-published V4
and V5 bytes are never removed to make a later step pass.

V5 freezes the original candidate and G1 policy before V4 starts, binds the
returned V4 candidate, account plan, event and deadline to those originals,
then reopens the exact pinned public journal after the second local read. It
samples a new native UTC validation time after journal readback, requires it
to follow the second checkpoint receipt and precede the original deadline,
then replays G1 on the same packet. A stale packet yields
`public_g1_rejected_at_local_readback`; an unreadable or changed journal yields
`public_journal_readback_invalid_at_local_readback`. An unavailable native
clock, invalid ordering or elapsed deadline yields
`public_validation_clock_invalid_at_local_readback` before G1 runs. After G1,
V5 independently replays the public transport profile and frozen original
economics quote-age policy at the later checkpoint time. Rejection yields
`public_transport_profile_rejected_at_local_readback` or
`public_economics_quote_stale_at_local_readback`, respectively. All remain
`DENY`. V5 reads the exact native post-public capture stamp from the pinned,
replayed journal and pairs it with its own final native sample. Freshness uses
the later of native UTC and the monotonic elapsed-time floor; reversal, excess
skew or a lease over 60 seconds yields
`public_validation_clock_invalid_at_local_readback`. It does not rewrite the
source stamp or any accepted receipt; the
local flatness field still describes only the sampled local state. Publication
follows this sample, so the code is a historical diagnostic, not a claim of
current eligibility or execution authority. V5 does not fetch a new quote or
change the original trade geometry.

The reader validates the nested all-currency ledger, unresolved intents, and
legacy state shape and requires a bound 64-hex credential-session ID when a
legacy reconciliation checkpoint exists. Missing fields or an active exposure
omitted from the blocking reasons reject the checkpoint. An empty
blocking-reason list can produce `local_flat_at_reads=true` only for the two
sampled, internally
consistent local checkpoint reads. Matching hashes do not exclude an
intervening write that was reverted between reads. They do not prove
exchange-wide flatness,
future writer exclusion, historical completeness, authenticated original G1–G11,
complete post-G12 execution recheck, reservation, intent or submission
eligibility. All those flags remain false. Source-owned original qualification
still stops at G1–G5, and the V4 input remains caller-origin.
