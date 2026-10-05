# Sweep history V6 — computation stages A and B

This version is an explicit opt-in source computation contract. It adds no
order authority, source authenticity, account authority, reservation, durable
submission intent or G12/recheck dispatch. Existing V1–V5 policies, router,
strategy outputs and old sweep WAIT remain in place. Existing downstream
version dispatch rejects V6 until separately reviewed Stage C is implemented.

## Admission and the actual market regime

V6 uses `ctcc-sweep-history-protection-v1`, SHA256
`abb0b422c2e5e0fa4c61596ebf9fb43aca57c2f13176f7ecc29770c4888f90ab`.
Its thresholds and event logic are fixed. The source must replay the original
prior range, sweep/reclaim, co-confirmed structure, chronology, neutral HTF,
current safety and complete existing strategy predicates.

`History Verified Sweep` is a V6 history admission classification. It is not a
market regime, Trend classification or source of score/HTF permission. The
shared result's base consistency validator rejects Unknown G2 PASS; this exact
V6 Literal records admitted history without altering that validator or the
base regime enum. The V6 prefix policy explicitly pins this record shape with
required `history_result_classification="History Verified Sweep"`.

G2 retains `regime=Unknown` and `actual_router_regime=Unknown`, the exact actual
router diagnostic, source/analysis digests, admission schema, permission schema
and evaluation digest. Its result classification must agree with G2 PASS and
that digest. Failed or unvisited G2 retains Unknown. G3 independently recomputes
current neutral HTF, measured operands, volatility, mathematical vetoes and
unchanged sweep predicates; a label cannot manufacture any passing operand.

`SweepHistoryAdmissionRecord` stores a bounded canonical receipt and exact
source/event coordinates. Receipt consistency is not proof of execution or
source authenticity. Verification always replays the full original raw source
and compares the complete record. Self-signed receipt hashes, copied policy
flags and supplied `passed=true` provide no authority. Hidden record fields,
drifted policy pins and other strategies are rejected.

The admission contract imports only leaf domain/journal/timing dependencies at
module initialization. Its local event/scalar guard preserves the original
non-source checks and bounds for `TriggerDetection`/`EntryTrigger`: hidden
fields, exact nested types, decimal bounds, canonical text, tuple/tree budgets
and supported exact timezone types are checked before serializers or foreign
timezone callbacks. The original regime evaluator remains unchanged. This
dependency direction avoids loading strategy/workflow evaluators from the
structural protection module's contract import.

## Ordered shared gates and original protection

The same prefix computes G1–G7 and stops at its first failure. V6 uses its G1
source rebuild, original history detection, fixed event/expiry and existing
timing/location logic. The original long quote 102.11 lies outside its source
zone [99,101.5]; its candidate remains cancelled. There is no quote/entry,
zone, stop, target, score or expiry rescue.

The same G8–G11 engine uses a new explicit selector policy
`ctcc-sweep-original-extreme-selection-v1`. Only one source-derived
`sweep_extreme_invalidation` anchor with the original 15m setup time and extreme
can be selected. Its stop uses the existing volatility/minimum-bps maximum,
spread/slippage/tick buffers and outward tick alignment. Missing or duplicate
original extremes deny selection.

All other stops retain their IDs and source operands with
`sweep_requires_original_extreme` rejection codes. Equal pivot pools remain
available to the unchanged liquidity-buffer checks. All target barriers remain
in the audit; the unchanged nearer-barrier, opposing-zone, cost and minimum net
RR checks can reject the original stop. Rejection cannot choose a tighter stop
or farther TP to rescue RR. Shared anchor extraction and bracket arithmetic are
unchanged.

## Validation status and limits

The first sweep-permission fixture freeze and its failed positive receipts are
retained. Root ran the second permission freeze: 29 focused synthetic cases
passed, including fixed-policy positive and denied histories. This is history
permission evidence only.

The V6 Stage A/B source freeze separately preregisters three synthetic raw
scenarios before evaluation: the original outside quote, an inside quote and a
boundary quote. The latter two have separate report identities and shift their
entire raw 5m OHLC/ticker/book/mark consistently. They are new declared source
scenarios, never post-render modification of an existing candidate. Their
expected outcomes are tests awaiting root's bounded runtime verification.
Synthetic fixtures do not satisfy trusted public/account, predictive PIT,
Shadow/Demo or Live acceptance.

This source slice has short formatting/lint validation only. Behavioral cases,
old/new receipt byte comparisons and old-version regressions must be run by
root against the frozen exact dependencies. Native capture, external order
writes, Docker and full regression are not claimed by this source review.

The first bounded V6 case against the second Stage A/B freeze failed during
collection because the contract imported regime admission while StrategyContext
was still initializing. No test was collected or passed. The second sealed
freeze and failed log/XML remain retained. A separate third source freeze
contains the leaf dependency correction and additional guard parity/hostile
field/timezone cases; root must rerun it before accepting behavior.

The subsequent root runs completed all 58 cases of the third scoped freeze
with zero skips, failures or errors. Six bounded groups preserve exact source
pins and native one-CPU process limits. The two account dependencies retain
their reviewed second-freeze bytes; current native account work is outside this
component acceptance. The same source also passed all 29 original permission
cases while comparing 32 complete old/new evaluator outputs without byte
normalization. Evidence aggregate SHA256:
`fe650c0c8551688adcd1be6b57dabc93308d8400d2986729b88c93a25857373d`.

These results accept the scoped Stage A/B behavior, including unchanged actual
Unknown regime, original outside-entry cancellation, original sweep-extreme
selection and refusal to repair failed RR. Stage C G12/recheck/ledger version
dispatch remains closed pending its own reviewed implementation and tests.
No native strategy, Demo, OOS, Shadow, Live or final regression follows from
these synthetic component runs.
