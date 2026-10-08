# Owned original-source handoff V2

`capture_owned_original_sources_v2` is a bounded, read-only handoff intended
for the original candidate phase. It accepts a reviewed instrument and market
policy, an unused controlled Demo account session, configured DB access, and
separate evidence roots. It does not accept caller-supplied market packets,
gate results, report IDs, clocks, or a publication barrier.

The coordinator attempts native V2 public capture, consumes its one-use carrier
in the same task, and only then attempts native account capture. It checks the
public packet and the account diagnostic against the controlled plan, region,
source digests, readback receipt, task clock, and original lease. Evidence roots
must be local, disjoint, and free of symlink/junction ancestors. A failed or
cancelled attempt consumes the account session; no source or order request is
retried by this handoff.

The returned receipt is always `DENY`. It proves no G1–G11 calculation, G12
publication, post-G12 recheck, complete account, reservation, intent, or order
eligibility. The current native V2 public issuer still selects a Production
socket for its Demo-labelled plan and refuses capture before network I/O, so
the real path currently reports `original_source_public_unavailable`. Synthetic
join tests check only the handoff mechanism. A reviewed account-bound Demo
public origin issuer and full source-derived gate chain remain prerequisites
before this handoff can participate in a qualified candidate flow.

The reviewed Demo REST route check is offline policy only. It requires the
session-bound region, Demo header, exact expected SWAP instrument and an exact
role-specific query (including candle bar/limit/cursor or books size), and
rejects duplicate or extra parameters. It does not replace the native REST/WS
request issuer, TLS peer verification, journal replay or source authenticity
proof. The native hard refusal stays in place until all of those roles use the
same controlled Demo origin and independently verified account region.

## V4 original precursor inspection

`capture_owned_original_precursor_v4` extends the one-task diagnostic without
changing V2/V3 receipts. The caller chooses one of the eight fixed strategy
selectors, but cannot provide a candidate, entry, event, bracket, account
packet, G1 result, old receipt, clock, or publication barrier. The coordinator
consumes the native initial-public carrier, independently replays G1 under the
fixed **inspection** policy, then consumes the native V6 account raw-packet
lease only after its receipt and private source readback. It binds the exact
instrument, plan, packet, receipt, proof, native observation, account session,
and source chronology. The existing raw precursor is derived and independently
replayed from those two packets in the same task; its service deadline is the
earlier native lease expiry. A final native clock sample after replay must still
be before both expiries. A missing, reused, foreign-task, changed, or expired
lease returns a bounded `DENY` diagnostic with no precursor hash.

The V4 receipt contains only the precursor policy, result and optional intent
hashes plus `WAIT`/`CANCEL`/`NO_TRADE` action and failure code. It exposes no
raw account packet or reusable intent. Even an inspected source-derived intent
has `candidate_created=false`, `g1_g11_complete=false`, `g12_published=false`,
`account_complete=false`, `source_authenticity_verified=false`, and all
reservation, execution and order flags false. The fixed G1 bounds have not
been calibrated for trading; the precursor does not own complete account
history, G2–G11 risk/protection decisions, G12 publication, or R7. The current
native Demo public-origin hard refusal still runs before network I/O, so the
positive test uses explicitly synthetic source transports and account readback.
It is a mechanism check, not a real Demo candidate or acceptance sample.

## V5 owned candidate → G12 preflight boundary

`preflight_owned_publish_and_recheck_v5` is the owned entry shape for a future
R7 coordinator. It accepts roots, reviewed instrument/strategy/policy and an
unused controlled account session. It has no `run`, candidate, old V4 receipt,
`passed`, barrier, clock or callback argument. It enters the same native V4
acquisition core through a private V5 path in the current task; once capture
begins, that session is consumed. An early input validation error can return
before capture starts and leave the session unused. The public V2/V3/V4 entry
points and diagnostic bytes remain unchanged.

The private V5 path holds the exact consumed public packet bytes, the frozen
account packet bytes and the derived precursor only inside that invocation.
Before returning, it replays both packet byte sequences against their original
hashes and plan, then independently replays the precursor with the original
creation time, policy and service deadline. A final native clock sample must
remain before both source expiries. Changed bytes, pins, plan, precursor,
foreign-task or reused leases, and expiry fail closed with no precursor hash.
No raw packet, precursor, callback or reusable capability leaves the function;
only the existing hash-only V4 diagnostic reaches V5's bounded receipt builder.
This continuity check does not invoke G2–G11 or G12. Its canonical receipt is
always `DENY` with
`candidate_created=false`, `g1_g11_complete=false` and `g12_published=false`.
It distinguishes an unavailable V4 precursor, an inspected precursor with no
intent, and an inspected intent still lacking source-derived G1–G11 inputs.

The V5 receipt constructor checks shape, phase consistency and fixed denial
flags only. Anyone can construct a syntactically valid receipt and its SHA256;
neither proves a native capture happened. Treat the V4/V5 receipts as untrusted
diagnostics, **not** source authentication, qualification or acceptance
evidence. Only a future private same-task evaluation of the complete gate
chain from the retained raw inputs could cross the owned candidate boundary.

This stop is deliberate. The public V4 method still returns only hashes and
discards its native public/account packets and precursor before it returns. A
saved V4 receipt cannot recreate native ownership or qualify an intent for G12.
The private V5 path now has a frame in which a future version can evaluate the
full gates from those exact bytes, but its V6 account receipt explicitly lacks
complete portfolio history, local exposure/revision and authenticated risk
inputs. The precursor has no selected SL/TP, position size or complete G2–G11
run. The native V2 public quote and G1 result also need a versioned bridge into
the existing G1–G11 engine, whose current contract accepts the older quote and
G1 result types. A later implementation must independently replay every
source-derived gate, including complete account/risk/protection and event-ledger
inputs, before it may publish new G12 for the exact candidate. It must continue
to fail closed while Demo public-origin authentication is unresolved.
The inspected-intent branch in unit tests uses a synthetic V4 diagnostic solely
to verify that this boundary still stops before G12; it is not Demo evidence.

## V6 same-invocation base G1–G4 inspection

`preflight_owned_base_prefix_v6` accepts the same controlled source inputs as
V5, limited to `trend_pullback`, `breakout_continuation`, `fvg_return`, and
`order_block_return`. The caller cannot provide a market/account packet,
candidate, gate result, PASS flag, old receipt, clock or callback. Inside the
private V5-style raw-packet frame, the V6 path first replays the exact native
public and account bytes and the derived precursor. When that precursor has an
intent, it recomputes raw V2 G1 at the precursor creation time and applies the
shared base-strategy G2 regime, G3 HTF, and G4 setup arithmetic to the V2
bid/ask and upcoming funding forecast. The fixed policy uses the inspected
native G1 bounds, captured instrument tick, score 85, 30 bps zone drift, 3 bps
strategy spread, and 5 bps adverse funding. Its canonical result is replayed
twice in the same frame before returning a hash-only receipt. A mismatch,
changed source pin, failed gate or expiry is denied; no gate is repaired.

This is computational inspection, not a qualified original candidate. The
native Demo public issuer still refuses unauthenticated region provenance
before network I/O. The available account packet does not prove complete
portfolio history, local exposure, current protection or source authenticity.
The V6 receipt therefore keeps `candidate_created`, `g1_g11_complete`,
`g12_published`, `account_complete`, `qualification_performed`, reservation,
execution and order authority false, with `admission=DENY`. Non-base strategies
are rejected before source I/O. V4/V5 APIs and receipt bytes are unchanged.
Synthetic tests exercise the same-task arithmetic and denial; they are not
actual OKX Demo samples or Gate 3 evidence.

## V7 same-invocation base G5 event inspection

`preflight_owned_base_event_v7` is additive and limited to the same four base
strategies. It retains the original raw public/account packets inside the same
private task frame, replays V6 G1–G4, then derives G5 from the G1 market and
analysis at the original creation time. The fixed trigger, event identity,
expiry, raw candle timeline and event-prefix witness must match the precursor.
The G5 result is replayed twice in that frame before a hash-only receipt is
returned. Changed source, event, chronology or expiry fails closed. V4–V6
public APIs and receipt bytes remain unchanged.

V7 reports only that the G5 predicate was replayed on the observed synthetic
source. The raw historical rows still lack proved first availability, and
the owned frame has no authenticated all-state consumed-event ledger. G6
therefore remains unperformed; G7 cannot follow G6. The account also lacks
complete risk and local exposure. Every V7 receipt has `admission=DENY`,
`historical_first_availability_verified=false`,
`event_ledger_authenticated=false`, `g6_evaluated=false`,
`g7_evaluated=false`, and all candidate, G12, reservation, execution and
order authority flags false. The real Demo public-origin issuer still denies
capture; the V7 tests use synthetic packets only and provide no Demo
acceptance evidence.

## Local consumed-event journal inspection

`QualificationLedgerRepository.inspect_consumed_event_journal` is a read-only,
bounded inspection for one exact Demo UID. It expects the DB0023 UID uniqueness
and retention guards and the DB0027 closure policy; it does not prove those
migrations ran with the exact reviewed function bodies. It reads
all four reservation states across settlement currencies, including expired
`reconciled_flat` tombstones, and checks each stored request/event binding and
the observed ordered transition chains. It also checks the required DB0023
UID-event uniqueness and retention triggers before returning even an empty read. It
takes the existing UID advisory lock,
then a NOWAIT SHARE lock on the three journal tables; contention denies the
read. The source query runs after the UID lock under READ COMMITTED so a writer
that committed in another currency while the reader waited is visible. More
than 2,048 events, 8,192 transitions or 32 MiB of request bodies denies the
read; no truncated set is returned. The receipt contains hashes and counts,
not account payloads or an order permit.

The inspection pins relation lookup to `public` before `pg_temp`, rejects a
non-`origin` replication role, and checks the expected enabled trigger kinds,
trigger-function schema and identity, and exact UID unique-key columns. These
checks catch common runtime/schema drift but do not prove the historical
function bodies were unchanged or that every earlier database session used the
same retention controls.

This checks the row chains visible in the controlled qualification DB journal
at this read. It cannot establish that earlier history was not erased before
inspection or that legacy routes, exchange orders or external event history are
complete. Its receipt therefore says `db_journal_row_chain_verified=true`,
`db_journal_complete=false`, `external_event_history_complete=false` and
`admission=DENY`. It is not wired
to V7 or accepted as a G6 PASS input. An owned G6 diagnostic would require a
same-task read after G5, exact UID/source/session binding, independent account
and legacy/exchange reconciliation, and proof that no event outside this journal
was consumed. Missing evidence leaves G6 unperformed.

The separate [V8 G5-to-journal observation](owned_original_event_ledger_v8.md)
now invokes unchanged V7, then reads this controlled journal twice in the same
task. It can reject a visible duplicate and records an absent event only as a
limited DB observation. V7 itself remains unwired to the journal, and neither
V8 nor an empty pair of reads promotes G6 or execution authority.
