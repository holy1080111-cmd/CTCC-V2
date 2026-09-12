# Versioned history qualification through G11

`history_prefix.py` and `history_engine.py` are explicit offline entry points.
They integrate raw-source history admission into an ordered G1–G11 calculation;
they do not install a trading route or authorize an order. Old `service.py`,
`engine.py`, regime formulas, and their hashes/contracts are unchanged.

## Version and replay boundary

- Prefix contract: `ctcc-history-qualification-prefix-v1`.
- Result contract: `ctcc-history-qualification-result-v1`.
- Full calculation contract: `ctcc-history-pre-evidence-v1`.
- History policy: `ctcc-history-regime-admission-v1`.

The caller supplies original market, intent, quote, WS reference, explicit
clock, consumed-event set, cost/risk policy and account claims. It cannot supply
a passed history result, replace G2 with a boolean, override an event, or inject
a gate callback. G1 rebuilds the raw source and analysis; history G2 re-evaluates
that same source, fixed analysis version, identity and observation. G5 independently
re-extracts its event and must exactly match the history observation. G3–G7 retain
the original strategy conditions, minimum score, current spread/funding limits,
event expiry/consumption, timing and entry-zone checks. G8–G11 call the existing
structural selector, economics evaluator and portfolio evaluator in order.

`evaluate_history_pre_evidence` returns `HistoryPreEvidenceRun`;
`verify_history_pre_evidence` recomputes every visited gate from the original
inputs and compares the complete record. Matching self-signed hashes alone are
not evidence that an evaluator ran. The prefix has corresponding
`evaluate_history_qualification_prefix` / `verify_history_qualification_prefix`
APIs. Policy hashes include the new versioned fields and original timing policy.

Top-level and nested input preflight rejects undeclared models, hidden/private
state, unsupported timezones and opaque objects before serializer, timezone,
`__class__`, metaclass equality or hash callbacks. Nested prefix checks share
the full engine's traversal budget. Sources use the established exact raw-source
guard; caller market quality remains ignored and recomputed by G1.

## Deliberately preserved failures

| Family | Versioned history G2 | Remaining gate boundary |
| --- | --- | --- |
| Structure reversal | A verified historical reversal may use new `History Verified Reversal` classification when the unchanged snapshot router says `Unknown`. The original snapshot label and hash are retained separately, never renamed Trend. | Original 4H veto, 15m follow-through, current setup, trigger, timing and entry location remain required. The synthetic long/short examples reach G8, whose existing structural selector rejects `multi_timeframe_not_aligned` as `source_data_blockers`; this blocker is not stripped. |
| Volatility expansion | Actual prior compression, new confirmed break and subsequent momentum event may pass G2. | Legacy strategy has no explicit HTF condition group. G3 therefore fails `htf_strategy_permission_denied`; an empty group never becomes implicit permission. Promoting route-level 4H/1H observations into G3 requires a separately reviewed versioned policy. |
| Liquidity sweep reversal | Verified history does not resolve the conflicting HTF/range-transition specification. | G2 remains `sweep_htf_policy_unspecified`. |
| Other five families | Existing snapshot route decides G2. | Every original later gate remains; synthetic FVG long/short fixtures exercise all eleven actual evaluators and match the old G1 and G3–G11 results. |

No selector input is altered to remove a safety blocker. No stop/target or
economics/risk permission is invented to force a full historical pass. The new
classification is a bounded evidence-policy result, not exchange-authenticated
market truth. Result records cannot contain G12 or execution-recheck gates.

## Downstream work is explicitly not complete

Legacy G12 publication, one-shot publication/capture and original pre-evidence
verification reject these exact new types before filesystem, clock or capture
work. No conversion shim, silent downgrade or runtime registration is provided.
A reviewed versioned G12 snapshot/rendering/publication + current-history
recheck integration remains necessary. So do the historical strategy's G8
alignment policy decision, expansion G3 policy, sweep HTF policy, complete
authenticated account evidence, atomic reservation and durable submit wiring.

All execution/authenticity/reservation/recheck authority flags remain false.
Synthetic typed complete/armed account fields in tests are fictional claims,
not an account ingestion acceptance or a Demo permission. Tests use synthetic
OHLC and MockTransport quote payloads, not real Shadow/Demo observations,
profitability evidence, calibration or a soak. The final root acceptance log
records exact test counts; this document does not imply Linux/Windows parity,
deployment, matching remote CI, or actual order submission.
