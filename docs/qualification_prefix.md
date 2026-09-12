# Ordered G1–G7 qualification prefix

This increment follows local checkpoint `6d87530`. It connects real evaluators,
not caller-supplied PASS records, and stops at the first failed gate. It is an
offline computational prefix: **even seven passes are not ORDER_ELIGIBLE**.
G8–G12 orchestration, the complete post-render recheck and Demo integration
remain unfinished. Existing independent protection/economics/portfolio/evidence
modules are not yet called by this coordinator.

## Interface and trust boundary

`app.trade_qualification.service.evaluate_qualification_prefix(market, *,
intent, quote, reference, policy, consumed_event_keys, evaluated_at)` accepts:

- An original raw `MarketSnapshot`, a provenance-validated `CollectedQuote`, and
  an independently timestamped `WSReferenceObservation`.
- `QualificationIntent`: report/instrument/strategy/direction, unchanged entry,
  original creation time and expiry. No score, gate, event or zone is accepted.
- `QualificationPrefixPolicy`: explicit G1 policy, minimum score, real-instrument
  tick-size input, maximum drift, strategy spread and adverse funding limits.
  Spread can be tightened from 8 bps and adverse funding from 15 bps, never
  loosened beyond those existing strategy-wide limits.
- An explicit bounded `frozenset` of consumed event digests. This function does
  not obtain, authenticate, reserve or update a durable ledger.
- One exact timezone-aware evaluation clock, normalized to UTC.

Malformed intent/policy/clock/ledger inputs raise before a run is emitted. Invalid
market evidence produces a failing G1. Neither outcome grants permission.
The typed WS and tick-size inputs still require trusted runtime adapters; type
validation and digests do not establish their real-world authenticity.

The output `QualificationPrefixRun` pins intent, caller policy, the fixed
strategy timing policy, the consumed-event-set digest, G1's rebuilt source and
all visited gate results. `policy_sha256` includes both the explicit prefix
policy and the selected timing policy. Each gate has a stable code, reason and
bounded measured values. Unvisited event/timing/location records stay absent.
The result contains no SL/TP, RR, risk permission, evidence completion or recheck.

## Ordered gate responsibilities

| Gate | Actual computation and stop condition |
| --- | --- |
| G1 | Rebuild quality and analysis from raw data at the explicit clock; retain public/WS source pins. Any failed data check stops before routing. |
| G2 | Route the rebuilt analysis, then require `ALLOW_SCORING` and requested-strategy membership. Diagnostic exclusions are not automatically whole-route failures. |
| G3 | Match requested and actual strategy direction; evaluate strategy-specific required/veto HTF predicates. Range requires explicitly neutral 4H/1H structure and bias. |
| G4 | Evaluate all shared required conditions and vetoes, current 5m state, explicit minimum score, and independently observed REST spread/adverse funding. No score overrides a missing required condition. |
| G5 | Derive the actual setup-bound closed-5m false→true event from the same G1 source; require a matching source pin, a present uninvalidated trigger and an unexpired original event. |
| G6 | Apply the fixed strategy timing window to fresh ask for long / bid for short. Reject old/future/expired candidates, consumed events and excessive impulse extension. |
| G7 | Build the source zone inward on the supplied instrument tick; cap expiry by the timing deadline. Check original entry and executable reference, drift and invalidation without repricing. |

The strategy-specific HTF rules are not a universal trend-alignment filter:

| Strategy | HTF handling |
| --- | --- |
| Trend pullback | Required 4H and 1H trend conditions |
| Breakout continuation | Required 4H permission |
| FVG / order-block return | Required 4H direction; 1H context remains optional score evidence |
| Range reversal | Neutral 4H and 1H structure/bias, plus its own setup conditions |
| Structure reversal | Shared 4H strong-opposition veto retained, but current G2 does not admit the family |
| Liquidity-sweep reversal / volatility expansion | No invented HTF formula; current G2 still excludes these history-dependent families |

The existing conservative router is unchanged. Finding a pattern in the event
extractor does not expand its allowed families. Range can carry non-alignment
diagnostics without acquiring a false trend requirement.

## Shared predicates, current costs and immutability

`app.strategies.conditions.assess_conditions(ctx, strategy)` dispatches only to
eight extracted pure condition builders. The legacy `evaluate()` wrappers use
the same builders, then retain their original candidate/evaluation behavior.
Recorded pre-refactor AST fingerprints verify that direction assignments and
condition formulas were preserved. Required flags, vetoes and score weights are
not newly calibrated. The prefix never calls the legacy candidate builder or
its wall-clock 20-minute expiry and synthetic protection geometry.

Predicate groups distinguish HTF, setup, current trigger state, quality, safety
and optional score. The current 5m state remains required where the original
strategy requires it; it does not replace G5's historical event transition.
For example, score 100 with continuing momentum but no new post-setup event
stops at G5. Losing current required momentum stops earlier at G4.

Fresh spread and direction-signed adverse funding use the validated public quote,
not unstamped legacy `market.funding_rate`/`mark_price` fields. Those historical
fields remain in the source pin and are never overwritten to fake freshness.
Exact rational comparisons handle spread/funding limits; a private Decimal
context makes the rest of the prefix independent of ambient precision/traps.
No score-to-probability or score-to-leverage mapping is introduced.

## Replay and result integrity

Use `verify_qualification_prefix(run, market, **original_inputs)` before any
future downstream consumer treats a stored run as evaluated evidence. It reruns
the entire visited prefix from the original inputs and compares the complete
record. A hash or a self-created all-PASS object is insufficient. Changed
source, report/intent, clock, policy, quote/reference or consumed-event input
cannot be silently substituted.

Record preflight rejects hidden fields, missing fields, arbitrary iterators,
oversized values and nested model subclasses. It copies raw declared fields
without serializing away subclass additions, then strictly validates the
complete tree. Immutable measurement views are converted back to input dicts
for reconstruction; scalar and tuple types are retained. Replay/digest tests
include the discovered nested-subclass erasure regression.

`execution_authority`, `source_authenticity_verified`, `event_ledger_verified`
and `execution_recheck_performed` are exact `False` only. Seven passes set
`prefix_complete=True`, while `result.qualified=False`. These safeguards are not
a substitute for trusted adapters or the still-missing execution integration.

## Validation scope and next work

The new fixtures generate fixed synthetic raw OHLC in both directions. G1
recomputes all analysis and every downstream evaluator runs normally. Their
independent timeframe examples are not claimed as one exchange tape, a real
source capture, Shadow/Demo samples, or evidence of profitability.

Tests cover formula parity, actual seven-gate traversal, missing current setup,
score-100/missing-event rejection, consumed-event report renaming, first-failure
short-circuiting, original-entry preservation, exact fresh-cost boundaries,
zone/drift failures, source replacement, nested subclass/hidden-field rejection,
hostile Decimal context and false authority. Exact checkpoint counts and the
frozen-source receipt are saved under local `reports/entry-acceptance-20260912/`.

Next: extend the same immutable source/run through G8–G11, bind actual published
and read-back evidence at G12, then perform the full post-publication recheck
against original event identity and intervening closed OHLC. A durable account
lock/reservation/event-consumption boundary must precede Demo integration.
Do not wire this prefix alone into any submission route.

The first restricted-session Windows pure-unit run passed 2443 tests with three
intentional pre-existing malformed-record warnings. After the user updated the
environment permissions, ordinary Docker and GitHub read access were restored.
The source is then frozen for its own isolated Linux/CI acceptance; actual
outcomes and synchronization status belong in the exact checkpoint receipt, not
an assumed inheritance from the earlier published `d0ff9b7` run. Deployment,
trading flags and operating-system clock are unchanged by this increment.
