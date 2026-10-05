# Full-public V2 original candidate precursor

This additive diagnostic rebuilds an incomplete original intent from the actual
full-public V2 raw packet and captured Demo account instrument row. It is not
full qualification, a current source owner, a portfolio snapshot, an order
intent or execution permission. Its admission is always `DENY`; successful
derivation is `WAIT`. All native/current ownership and execution flags stay false.

The preregistered policy digest is
`6a2626a911e6c6497cec033472a0f5feeb7d99c41abcd967540467bc3e27d0bb`.
The exact canonical bytes are in
`docs/original_candidate_precursor_v2_preregistration.json`. This policy was
fixed before this implementation. The source-only design and dependency archive
are retained under `validation-results/original-precursor-v2-design-20260930`.

## Original source and fixed operands

`derive_original_candidate_precursor_v2` accepts the exact V2 public packet,
raw Demo account packet, fixed strategy selector, explicit source/policy pins,
DataQualificationPolicy and declared creation/service-deadline cutoffs. There
is no caller direction, entry, score, event, zone, quantity, leverage, bracket,
PASS result, engine contract or evaluator callback. Initial public stage with
null publication barrier is required. A post-publication packet cannot become
a new original candidate.

The adapter runs `data_v2.evaluate_public_market_data_v2` and its independent
verifier on the complete retained raw source. It then rebuilds market and
analysis from the verified G1 source bytes. G1 uses the existing shared data
math. There is no V1 executable quote or fabricated funding generation time.
The actual rate/upcoming `fundingTime` pair and its raw/profile/transport hashes
are retained; the following forecast is distinct. Generation time and past
first availability remain unknown.

Captured instrument rules are reconstructed from the exact raw account packet
and plan, including the unique instrument row and exact Demo identity/session
binding. The precursor records hashes of the existing private identity and
rules receipt. It does not duplicate UID/main UID, session identifiers or raw
private rows. Packet and metadata receipts must complete before creation.
Current ownership, metadata freshness and complete account state remain
unverified even when offline raw records are consistent.

Ask for long and bid for short come from the actual V2 ticker. The existing
source-derived strategy direction selects that side. Entry must be exactly on
the raw instrument tick grid; it is never rounded. Event, original entry,
original setup and zone are unchanged. Expiry is the minimum of the original
event expiry, fixed existing timing deadline and preexisting service deadline.
Later replay cannot renew the event; shorter service life can only shorten it.
An off-tick or out-of-zone entry cancels. No SL, TP or new trigger repairs it.

## Existing strategy functions

| Selector | Reused source path |
| --- | --- |
| trend_pullback | Existing regime route, complete condition set and HTF group, original trigger and zone |
| breakout_continuation | Same base path, actual confirmed swing-break event |
| fvg_return | Same base path, actual original FVG return event |
| order_block_return | Same base path, actual original order-block return event |
| structure_reversal | Existing regime-admission evaluator and verifier, reversal permission, opposed-trend history |
| volatility_expansion | Existing regime-admission evaluator and verifier, expansion HTF permission and compression history |
| range_reversal | Existing neutral-range permission/replay and original range anchor zone |
| liquidity_sweep_reversal | Existing sweep-history evaluator and verifier; actual Unknown/range-transition history retained |

Base predicate contexts retain score 85 and RR operand 2; history predicate
contexts retain score 85 and RR operand 1. These are existing condition-context
operands, not measured candidate economics. Actual RR, bracket, sizing, leverage,
portfolio risk and ledger revision remain unknown. Original required/veto
failures, history codes and route diagnostics are preserved. There is no score
adjustment or Unknown-to-Trend conversion.

Sweep history evidence does not open V6 Stage C. An admitted history record can
produce only an incomplete diagnostic original intent with
`WAIT/sweep_stage_C_closed`. The co-confirmed event and unknown intrabar ordering
remain visible. Full sweep qualification and protection selection do not run.

## Receipt replay and bounded input handling

The versioned result is a frozen `OriginalCandidatePrecursorV2` containing an
optional exact `QualificationIntent` and bounded canonical receipt bytes.
`verify_original_candidate_precursor_v2` first checks exact result/model fields,
hidden fields, native containers, primitive types, Decimal representation bounds
and exact UTC datetimes. A native mapping proxy is inspected through its native
referent before accessing items or length; a proxy around a foreign Mapping is
denied. Caller serializers, iterators and timezone callbacks cannot precede
these checks. Only then does the verifier independently recompute all original
raw inputs and compare exact receipt and intent trees. Self-signing a receipt
or copying an intent cannot prove derivation.

Each candle row retains its original page order, row ordinal, raw row/page
hashes, frame identity, request start, headers receipt, body completion,
open/close, confirmed state and cutoff. Setup, trigger and pivot prefix witnesses
join those actual rows. No sorting, deduplication, filling or past receipt
construction occurs. `historical_first_available_at` stays null: observations
at this cutoff are not predictive Point-in-Time Gate 3 evidence.

Malformed source/version/pin fails closed with redacted static errors. Valid
source failures produce reproducible `WAIT`, `CANCEL` or `NO_TRADE` receipts.
They have no full G1--G12, account completeness, protection, event-ledger,
reservation, durable submission intent or order authority. An empty consumed
event set is never supplied as an implied all-state ledger.

## Verification status

The accompanying tests use the actual owned full-public V2 acquisition flow
over explicitly synthetic raw transports, clock, TLS and storage, and actual raw
account packet validators. G1, analysis, routes, conditions, historical
permission, event and zone functions are not stubbed. Synchronous fixture
constructors run before capture event loops. Cases cover source-derived long and
short intent, all eight fixed selectors, historical routes, closed sweep Stage C,
raw pins/profile/initial stage, original expiry, tick cancellation, complete
receipt replay and callback-denial counterexamples.

This worker performs source review and short lint/format checks only. Behavioral
tests and unchanged legacy policy/output byte comparisons are `ROOT_RUN_REQUIRED`
until root produces bounded-run evidence for the exact frozen source. Synthetic
tests do not count as native candidates, Shadow/Demo/OOS samples or the four
real Notion evidence examples. A saved native G1 diagnostic is not promoted to
same-invocation original qualification by this adapter.
