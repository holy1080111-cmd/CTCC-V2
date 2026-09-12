# Source-replayed G1–G11 pre-evidence engine

`app.trade_qualification.engine` extends the actual ordered
[G1–G7 qualification prefix](qualification_prefix.md) through structural
protection, costs and portfolio risk. This is a new composition layer; the
prefix module still ends at G7. The earlier prefix document describes that
module's own checkpoint, not the completion status of this extension.

Every visited gate calls its real evaluator. Evaluation stops at the first
failure. Eleven passes mean `pre_evidence_complete=True`, not evidence
completion, `ORDER_ELIGIBLE`, source authentication or permission to trade.
No runtime, deployment, trading flag or account state is changed here.

## Explicit inputs and original-source replay

```python
evaluate_pre_evidence(
    market,
    *,
    intent,
    quote,
    reference,
    policy,
    risk_inputs,
    consumed_event_keys,
    evaluated_at,
) -> PreEvidenceRun

verify_pre_evidence(run, market, **original_inputs) -> PreEvidenceRun
```

The market is the original raw `MarketSnapshot`. Quote and reference are the
same provenance-validated `CollectedQuote` and timestamped
`WSReferenceObservation` required by G1. No caller analysis, score, PASS gate,
event, zone, selected bracket, SL or TP is accepted. The original immutable
intent fixes report, instrument, strategy, direction, candidate entry, creation
time and expiry. One explicit aware evaluation time is normalized to UTC;
the engine does not obtain a wall clock, settings or network data.

`PreEvidencePolicy` requires an identifier and the complete prefix policy,
`ProtectionPolicy`, economics policy and portfolio policy. Explicit `None` for
economics or portfolio policy represents missing evidence, not a default pass.
The six structural parameters are expected slippage, cost bps, minimum net RR,
minimum stop distance in ATR, ATR buffer multiplier and minimum buffer bps.
G8 reuses the prefix's tick size; it does not invent another instrument tick.

`PortfolioInputs` fixes requested contracts, requested leverage and explicit
instrument, account and Demo-guard records. These three records may be `None`,
which fails G11 if earlier gates pass. Malformed policy/risk input contracts
raise during strict preflight; missing downstream evidence cannot mask an
earlier validly evaluated failure. Existing positions, pending reservations,
loss history and source stamps remain supplied claims, not authenticated
exchange/account receipts.

The engine reruns G1–G7 from these original inputs, preserving their complete
gate records. G8 restores market and rebuilt analysis from that exact G1 source
JSON. The public quote is independently revalidated and its bundle digest must
still match G1 before it is used again for G10. The run retains the prefix,
intent/policy/risk-input digests, canonical structural audit and digest, full
economics result and full portfolio result. Unvisited sub-results stay absent.

`verify_pre_evidence` strictly reconstructs the supplied run, reevaluates all
visited gates from the original inputs, and requires whole-record equality.
A changed input, audit annotation or policy cannot pass merely by recomputing
its hashes. Hashes identify content; they do not establish source authenticity.
Stored output is not itself an input-authority API.

## G8–G11 ordering and price preservation

| Gate | Actual responsibility |
| --- | --- |
| G8 | Run the source-bound structural selector over its bounded confirmed-OHLC anchor universe and audit the alternatives. A complete valid selected bracket is required before its stop may pass. |
| G9 | Retain the target from that same bracket, including its nearer-opposing-barrier checks. Do not switch targets to improve RR. |
| G10 | Recalculate explicit costs and net RR for the unchanged entry, selected SL/TP and independently validated quote. Stop on any cost failure. |
| G11 | Calculate risk for the same entry/SL and G10 cost per base, original requested contracts/leverage and the explicit account, instrument, policy and Demo-guard claims. |

G8/G9 use a joint selector, not two independent searches. If no complete bracket
is valid—even when the underlying cause is a missing target—the engine stops
at G8 with the selector's original failure code and full audit. It does not
invent an independently passing SL or claim that G9 ran. G9 is recorded only
after a valid joint selection. Original setup invalidation, multi-timeframe
alternatives, noise/liquidity checks, minimum stop distance, real tick rounding
and nearer structural barriers remain enforced by the selector.

Higher costs cannot tighten the stop, reprice entry, choose a farther target or
rerun selection. G10 retains the complete `EconomicsResult` for the actual
decision. Its evaluator's conservative cost-per-base rounding remains intact;
the engine passes that exact returned cost to G11 without another conversion.
The economics ratios retain their full evaluator precision. Only the
`EntryQualificationResult` reporting fields are rounded half-even to 30 decimal
places, after a passing economics decision. Those reporting values must agree
with the full economics record and are never used to rescue a failing RR.

G11 includes existing and pending exposure, directional/correlation limits,
margin, drawdown and loss-history checks. It never reduces requested size or
leverage to force a pass. All rejection causes remain in
`PortfolioRiskResult.causes`. Gate measurements retain a cause count, first
cause and result digest, avoiding a long joined string overflowing the bounded
gate record. A failed account calculation cannot replace earlier passing
source/protection/cost evidence.

## Strict records and false authority

Preflight requires exact declared model types and bounded raw trees before
serialization. It rejects hidden/missing fields, model subclasses, arbitrary
iterators, oversized ledgers, nonfinite numbers and wrong scalar types injected
through `model_copy`. Reconstruction restores immutable measurement views to
input dictionaries while preserving tuple and scalar types; it does not loosen
strict validation. Structural audit JSON is bounded and canonical, with
consistent source/identity/price records and non-authoritative markers.
UTC-equivalent clocks and hostile ambient Decimal contexts cannot change the
tested decisions or replay identities.

The run's `execution_authority`, `source_authenticity_verified`,
`account_evidence_authenticated`, `atomic_risk_reserved` and
`execution_recheck_performed` are exact `False`, not coercible truthy flags.
The underlying prefix also retains `event_ledger_verified=False`. Eleven passes
can reach the domain's `RISK_VALID` state and computed risk-pass indicator, but
`result.qualified` remains false. This is a risk calculation, not a reservation,
an authenticated guard decision or an order permit.

## Synthetic acceptance and remaining work

The FVG long/short fixtures start with the original synthetic raw-source prefix fixtures.
To add historically observable target/stop alternatives, each 240-candle
15m/1H/4H series changes only four earlier wick values: offsets `-30` high
`110.17`, `-25` low `98.50`, `-20` low `97.20`, and `-15` high `107.23`.
Short fixtures mirror prices as `200 - price` and swap high/low roles. These
are twelve raw field changes in total. Entry, current return/trigger candles,
timestamps and quote/reference inputs do not move. Analysis levels and PASS
records are never supplied as truth; all downstream evaluators recompute them.

The fixtures produce 60 assessed brackets and select long SL `98.32` / TP
`107.23`, or short SL `101.68` / TP `92.77`, under the explicit fixture policies.
The unmodified targetless source still fails at G8. A newly introduced nearer
barrier blocks selection, and increased costs can fail G10 without changing
price geometry. Complete empty Demo account/guard inputs are fictional typed
claims labelled as synthetic; their hashes are not exchange receipts. The
independent timeframe examples are not asserted to be one aggregated exchange
tape, real market sampling, Shadow/Demo trades or profitability evidence.

The focused acceptance scope is 200 tests:

- `test_qualification_engine.py`: 70 ordering, strict-input, replay, authority,
  reporting-precision and failure-record cases.
- `test_qualification_engine_sources.py`: 36 independent real-evaluator cases
  using the synthetic raw OHLC and explicit fictional risk claims.
- `test_qualification_engine_review.py`: 94 adversarial review cases, including
  rehashed audit changes, nested serializer/subclass attacks, missing-risk
  combinations and rejection of appended G12 records.

This count describes the focused engine/source/review suite, not a completed
Windows or Linux full-suite run, CI run or release. Exact run outcomes belong
to the corresponding checkpoint receipt; this document does not infer them
from earlier checkpoints.

The [G12 evidence coordinator](qualification_evidence_gate.md) is a separate
implementation, not part of `evaluate_pre_evidence`. Regardless of that gate's completion, the full
post-publication recheck still needs fresh source/event/price/cost/risk checks
against the unchanged original intent and intervening confirmed OHLC. Trusted
runtime adapters, authenticated account reconciliation, durable atomic risk
reservation and event-consumption locking remain separate prerequisites for
Demo integration. No submission route is wired by this increment.

The conservative strategy router is unchanged. History-dependent families that
it excludes do not become eligible because an extractor finds a pattern, and
existing analysis blockers are not removed to make G8 pass. Strategy, routing,
timing and structural-ranking policies remain uncalibrated engineering rules;
these synthetic acceptance tests do not validate them as profitable strategies.
