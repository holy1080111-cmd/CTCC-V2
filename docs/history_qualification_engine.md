# Versioned history qualification and expansion recheck

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
market truth. V1 result records cannot contain G12 or execution-recheck gates.

## Explicit expansion V2 (2026-09-19)

The separate `HistoryQualificationPrefixPolicyV2`,
`HistoryQualificationPrefixRunV2`, `HistoryEntryQualificationResultV2`,
`HistoryPreEvidencePolicyV2`, `HistoryPreEvidenceRunV2`, and
`HistoryEvidenceGateRunV2` preserve all V1 serialized fields and hashes.
The evaluator APIs end in `_v2`; V1 APIs reject V2 policies. V2 is scoped to
`volatility_expansion`. Reversal protection and sweep HTF blockers above remain.

The explicit policy `ctcc-expansion-htf-permission-v1` requires the existing
source-derived Expansion route: aligned 4H, non-opposed 1H with a known long,
short or neutral bias, directional 15m BOS, controlled high 15m volatility,
and no analysis blockers. G2 must first prove the original Compression →
Expansion chronology and event. G3 binds this permission to the exact G1 source,
analysis digest and G2 history digest. It does not raise a score, relabel Unknown,
replace an event, or weaken any later gate.

`publish_qualification_evidence` now dispatches exact V2 runs to V2 source replay,
then uses the existing structural replay, six-artifact renderer, no-clobber
publisher and actual readback. Its V2 record retains original G1–G11 and all
geometry. V1 history runs still cannot enter G12; old receipts cannot qualify a
new invocation. The report snapshot records the V2 qualification contract.

`freeze_recheck_origin` retains the typed V2 evidence and its pins.
`evaluate_recorded_recheck` replays the original V2 inputs and dispatches current
G1–G4 to `HistoryCurrentConditionsResultV2`. Current permission is recomputed
from current raw data with original history/event/origin pins. It does not call
the event extractor to replace the original event. The existing continuation,
timing, zone, fixed protection and current economics/risk checks remain in
order; changed HTF, stale capture, consumed event, expiry or original input
mutation stops the run. The original deadline is retained, never extended.

The owned `publish_capture_recheck` diagnostic orchestrator accepts exact V2
original inputs and retains its publication barrier before any new capture.
It is still not mounted in an execution route. Neither computational PASS,
native publication nor readback grants source authenticity or order authority.

The synthetic tests construct OHLC, volume and executable quotes before first
qualification. They exercise long/short G1–G12, native file hashes/readback,
post-publication recheck, JSON replay and denial of forged source/event/geometry,
changed HTF, reused receipt, extended expiry and malformed chronology. These are
engineering fixtures; they do not satisfy the original Notion real evidence
examples, OOS, Demo or Live acceptance.

## Explicit structure reversal V3 (2026-09-19)

The separate V3 prefix, pre-evidence, qualification, evidence, current-condition
and fixed-protection contracts use required exact version markers. Each union
dispatches on the declared marker; missing fields never upcast an old record
through subtype defaults. The original V1 reversal remains blocked at G8 and
the expansion V2 policy remains unchanged. V3 accepts `structure_reversal` only.
Liquidity sweep remains denied by `sweep_htf_policy_unspecified`.

`ctcc-reversal-history-protection-v1` derives permission from the existing
structural-reversal rules: independently rebuilt 1H CHoCH after an opposite
prior structural trend, a confirmed historical pivot available before the
break, and a subsequent closed 5m false-to-true momentum event. The 1H setup
must have closed before the trigger interval begins; same-interval evidence
does not prove chronology. G2 replays this history, while G3 retains the current
1H CHoCH direction, 15m follow-through, 5m momentum, mathematical safety vetoes,
strong opposing 4H veto and all data-quality requirements. No score repairs a
failed predicate. The original snapshot regime and diagnostic blockers remain
in the source; the explicit history classification never renames Unknown Trend.

The V3 structural selector independently recomputes this original history from
raw market rows, requires the exact original event and source digest, and emits
`ctcc_structural_selection_reversal_v1`. Its separate audit retains
`multi_timeframe_not_aligned` and the history digest. All structural anchors,
invalidation boundaries, noise clearance, cost floors, nearer opposing targets
and net-RR rules use the existing calculations. A caller's self-signed analysis,
event, score or PASS cannot replace raw-source replay. A source without a valid
complete bracket still fails G8. No stop or target is invented to obtain PASS.

G12 dispatches exact V3 replay through the existing six-artifact renderer,
no-clobber publisher and native readback. R7 replays the original V3 run and
checks new current G1–G4 with the original history/event pins. It does not search
for a new event. Original continuation, zone, timing, entry, SL and TP remain
fixed. V3 fixed protection replays the original versioned selector and checks
that same bracket against current constraints. Current 1H/4H permission loss,
changed original inputs, stale capture, consumed event or expiry stops recheck.
Candidate deadlines may shorten while retaining the exact event and geometry.
The older history contract fixes the event's own trigger TTL: a separately
rewritten or shortened trigger is conservatively rejected, not reauthenticated.

The source rules follow the existing [master E3 specification](https://app.notion.com/p/3d832165a6888173bfb1df896604fc7c?pvs=204)
and [strategy construction report](https://app.notion.com/p/3d832165a688813e8adcf68bde4003a1?pvs=204),
which permit strategy-specific reversal conditions rather than unconditional
4H/1H trend alignment. They do not define a complete sweep HTF permission table.
The [implementation plan](https://app.notion.com/p/3d832165a68881b3981cd9692986f031?pvs=204)
is the source of the originally recorded alignment and sweep gaps.

Long/short tests construct raw OHLC and quotes before qualification and exercise
G1–G12, file hashes/readback, fixed-protection recheck, forged history/analysis,
malformed chronology, rejected old receipts and explicit version boundaries.
They are synthetic engineering fixtures, not the four real Notion examples,
real qualification samples, OOS, Demo, Live or authenticated market acceptance.

## Downstream work is explicitly not complete

Legacy original pre-evidence verification and all V1 history contracts remain
unchanged. The separate V2/V3 paths have no conversion shim or runtime registration.
Sweep HTF policy, complete
authenticated account materialization, durable event-history scope, and final
atomic reservation/intent/submit authority wiring remain required. The separate
[exact intent replay](demo_submission_intent_v2.md) now accepts explicitly
versioned V2/V3 original policies and reconstructs the complete source/recheck
chain. Its synthetic tests do not authenticate the source or grant a submit
permit. Intrabar path completeness also remains unknown. No recheck record
can resume a previous invocation into a new order.

All execution/authenticity/reservation/recheck authority flags remain false.
Synthetic typed complete/armed account fields in tests are fictional claims,
not an account ingestion acceptance or a Demo permission. Tests use synthetic
OHLC and MockTransport quote payloads, not real Shadow/Demo observations,
profitability evidence, calibration or a soak. The final root acceptance log
records exact test counts; this document does not imply Linux/Windows parity,
deployment, matching remote CI, or actual order submission.
