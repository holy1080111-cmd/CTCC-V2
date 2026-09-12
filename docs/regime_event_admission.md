# Historical regime admission evidence, version 1

`app/trade_qualification/regime_admission.py` adds a pure, opt-in **evidence
component**, not a change to the existing [G1–G7 routing policy](qualification_prefix.md).
The old snapshot router, prefix/engine policy hashes and deployed strategy
selection are unchanged. A successful record is neither an order permit nor a
replacement for any qualification gate. There is no runtime registration here.

## Scope and preserved restrictions

| Strategy | Replayed history | Additional boundary | Current result |
| --- | --- | --- | --- |
| `structure_reversal` | On 1H: a previously opposed trend, an already-confirmed swing, then a new close through that swing; subsequently a closed 5m momentum False→True transition | Existing `4h_not_opposed` veto, real 15m follow-through, all existing required/veto conditions, data/mathematical safety | May produce history-route admission evidence |
| `volatility_expansion` | On 15m: preceding ATR14/close < 0.20%, then ATR14/close in [1%, 2.50%) and a newly confirmed swing break; subsequently a closed 5m momentum False→True transition | Existing Expansion route's 4H directional permission and 1H non-opposition, controlled volatility, all existing required/veto conditions | May produce history-route admission evidence |
| `liquidity_sweep_reversal` | A 15m wick beyond a prior confirmed swing with reclaim and co-confirmed opposing structure break, an opposed preceding trend, then a subsequent 5m momentum event | The snapshot policy's range-transition description does not establish an HTF permission formula compatible with the existing opposed-trend setup | Always blocked: `sweep_htf_policy_unspecified`, even when history is verified |
| Other five strategies | No new route policy | Existing router remains responsible | `legacy_route_only` |

These are the existing extractor's conservative closed-only definitions. The
last 50 native-timeframe setup candidates are searched; the entire supplied
history (200–1024 bars per timeframe) is available for prior indicators and
confirmed swings. No bars are sorted, padded, deduplicated or silently cropped.
A normal→high transition is **not** prior compression. Missing momentum
history (`None`) is not a known False state. A sweep and structure break in one
OHLC bar establish only co-confirmed observations: their intrabar order is
explicitly unknown, and the 5m trigger must follow the setup's confirmed close.

The history extractor checks all four confirmed timeframes for invalidation
touches after setup; a rebound does not erase a known touch. Bars whose open
precedes setup cannot establish the order of their wick relative to setup.
Consequently `complete_path_verified` remains false.

Risk-Off, High Volatility and current Compression safety blocks remain blocks.
Data and mathematical analysis blockers are not removed. The existing generic
`multi_timeframe_not_aligned` diagnostic is not made a universal reversal veto;
the reversal's own 4H and 15m conditions remain mandatory. Expansion's existing
quality condition still rejects *any* analysis blocker. Directional mathematical
opposition/instability may veto but never add score or authority. The recorded
condition score is diagnostic: this component does not replace G4's explicit
minimum score, fresh spread/adverse funding checks, or subsequent gates.

## API and replay boundary

```python
result = evaluate_regime_admission(
    market,
    report_id=report_id,
    strategy=strategy,
    direction=direction,
    observed_at=observed_at,
    analysis_version=analysis_version,
)
verified = verify_regime_admission(result, market, **original_inputs)
```

The component consumes no caller analysis or passed booleans. Exact bounded raw
market models are reconstructed, caller quality is ignored, and explicit-clock
quality and all analysis are recomputed. Exactly 4H/1H/15m/5m confirmed histories
are required. Candle timestamps mean **open**; close is open plus the timeframe
interval. Confirmed closes cannot exceed capture. Every timeframe must reach
the latest close knowable at the decision clock. Gaps, duplicates, unordered
bars, off-grid times, unconfirmed rows, future captures, invalid OHLC, nonfinite
or oversized numbers and malformed nested types fail closed. Supported timezone
values are normalized to UTC before causal calculations.

`RegimeAdmissionResult` is strict/frozen/bounded and records:

- Policy `ctcc-history-regime-admission-v1`, scope `history_route_evidence_only`;
- Report/instrument/strategy/direction/clock/version and full-source hash;
- The **unchanged** snapshot route/hash/allowed families/failure codes;
- Replayed detection, original event key, setup/trigger/expiry, required/veto
  failures and diagnostic condition score;
- Separate `history_verified`, `admitted` and deterministic failure code.

The existing strategy timing cap fixes trigger expiry (structure reversal:
600 seconds; expansion and sweep: 300 seconds). The existing setup-to-trigger
age limits also apply. Re-evaluation or a report rename cannot renew an event;
event identity is independent of report name. This component does **not** claim
that the event has not been consumed: the later timing/ledger boundary must
verify that against its original intent, deadline and durable state.

`validate_regime_admission` validates only record consistency. Its hash, or a
caller-created internally consistent result, does not prove the evaluator ran.
Consumers must use `verify_regime_admission` with the original raw source and
arguments. Source hashes bind bytes/metadata, not exchange authenticity.

## Integration and remaining work

Use this first as a replayed sidecar observation in a new versioned offline
coordinator. Do not reinterpret an old prefix's G2 result, modify its original
policy hash, replace G5 with a snapshot boolean, or silently admit these families
through the legacy strategy service. Enabling route evaluation later requires
an explicit versioned policy and tests preserving G1 and G3–G12, timing,
location, fixed protection, economics, current account and reservation checks.
Sweep additionally needs an explicit source-backed HTF/range-transition policy.

Executable quote freshness, WS/REST agreement, trusted capture/authentication,
continuous price-path coverage, account authority and order submission are not
performed. `execution_authority`, `runtime_admissible`,
`source_authenticity_verified`, `qualification_performed`,
`complete_path_verified` and `strategy_calibrated` are exact false.

Tests use synthetic OHLC with actual prefix/structure/event calculations,
including long/short positives and hostile-source negatives. They are not
Shadow/Demo observations, soak tests, evidence of profitability, or calibration
samples. Frozen full-platform/CI acceptance, if performed, is recorded separately;
this document does not claim it has already occurred.
