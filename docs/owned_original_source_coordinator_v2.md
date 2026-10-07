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
