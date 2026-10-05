# Original candidate source precursor v1

This additive pure replay slice produces a fixed `QualificationIntent` plus a
diagnostic receipt. It grants no original-source ownership, account completeness,
pre-evidence completion, reservation, or execution permission. Every receipt has
`admission=DENY`; even a derived intent remains `WAIT` for missing dependencies.
Supplied creation/deadline timestamps are declared replay cutoffs, not proof of
a native clock or a pre-acquisition owner. No runtime submission path uses it.

## Explicit supported adapter

`ctcc-original-base-precursor-v1` is the new adapter identifier for the existing
base `engine.PreEvidenceRun`/`events.extract_trigger` semantics. It is not an
invented version field on the older engine wire. Only `trend_pullback`,
`breakout_continuation`, `fvg_return`, and `order_block_return` are selectable.
Structure reversal, volatility expansion, range reversal, and liquidity sweep
remain blocked here. Selecting a history-engine contract never silently falls
back to base events. Their separately versioned history/protection policies need
their own explicit integration and independent full-engine comparison.

## Actual source inputs

`derive_original_candidate_precursor` requires the original collected public
packet and account packet, their exact external hashes, a selected strategy and
adapter, a pinned G1 data policy, creation time, and a preexisting service
deadline. It accepts no direction, entry, analysis, event, zone, caller PASS,
quantity, leverage, SL, or TP. A returned instrument-rules diagnostic is not an
input: the helper replays the actual account packet and derives the captured
instrument row, page, receipt, UID/environment/session, and exact tick binding.
The public bundle is likewise replayed before G1 rebuilds market and analysis.
Retain both complete packets and the rules receipt with this receipt; a hash
alone cannot reconstruct the source or prove native ownership.

The policy fixes predicate threshold 85 and zone drift 30 bps. The existing
strategy-context RR field is 2; that field is not an evaluated trade RR or a
generated protection bracket. G1's policy hash is independently pinned. A future
native owner must fix the entire profile, strategy, source scope, and service
deadline before acquisition. This pure function cannot attest when a caller made
that selection and must never be accepted as the owner by itself.

## Fixed original proposal

1. Creation must follow actual public and account capture completion, including
   the captured instrument row's measured body completion. Instrument `ts`
   absence and unknown endpoint timestamp semantics remain as in the rules
   receipt; measured receipt is never an exchange as-of substitute.
2. G1 runs from the actual packet. The source-derived regime must explicitly
   allow the chosen family. `assess_conditions` derives direction with required
   HTF permission, no veto/required failures, and the fixed score threshold.
3. The initial quote is ask for derived long or bid for derived short. An
   off-tick price is cancelled, never rounded. No alternate direction or strategy
   is tried. No candidate builder or ATR/RR bracket generator is invoked.
4. The selected base extractor derives the original closed-bar event and zone.
   Event source must equal G1's source. Creation cannot precede event close. The
   entry stays fixed; an original-zone mismatch cancels without clamping entry.
5. Expiry is the minimum of the original event expiry, the event's original
   `TIMING_POLICIES` deadline, and the preexisting service deadline. Replaying
   later cannot renew event expiry. No ledger timing PASS is synthesized from an
   empty/default consumed-event list.

Quantity, leverage, SL, and TP remain absent. Account completeness, all-state
event lookup, protection selection, real sizing, the independent full selected
G1–G11 replay, G12, fresh post-publication recheck, reservation, and durable intent
remain required. The future owner must compare its full engine outputs exactly
against these original source/event/entry/expiry bindings before any continuation.

## Validation status and scope

New tests use synthetic raw HTTP/socket transports and original synthetic account
pages, then run actual source parsers and evaluators. The positive cases are
long/short FVG sources; supporting the four selected adapters does not assert
that every strategy has an observed positive candidate. Tests also cover exact
tick rejection, no expiry renewal, service shortening, unsupported-version
blocking, source/policy pins, source chronology, forbidden caller proposal
fields, neutral data, and diagnostic-as-input rejection.

Tests are authored for scheduled bounded execution. They are not actual market
samples, Demo acceptance, native ownership, or an engineering-complete claim.
No legacy metadata/materializer/engine/collector wire, trading control, or
manifest is changed by this additive slice.
