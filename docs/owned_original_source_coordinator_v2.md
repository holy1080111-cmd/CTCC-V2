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
