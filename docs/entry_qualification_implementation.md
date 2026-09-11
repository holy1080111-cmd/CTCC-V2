# Entry qualification and evidence implementation

Status: qualification foundation and conservative regime routing, not deployed.
Updated 2026-09-11.

Source: [CTCC complete construction specification](https://app.notion.com/p/3d832165a6888173bfb1df896604fc7c),
edited 2026-09-11 11:02:22 UTC. The user's directly supplied, newer specification
defines 12 gates plus a final execution recheck; it takes precedence over the
older eight-gate outline in its parent and sibling pages.

Progress: [Notion implementation plan](https://app.notion.com/p/3d832165a68881b3981cd9692986f031).
An initial targeted update was denied under the earlier restricted session.
After the user changed permissions, ordinary connector updates succeeded and
were read back, including the foundation's full regression and CI results.
The deployment path in the plan was also corrected to `C:/CTCC-V2`.
This is an addition to the [123-project core](ctcc_core_master.md), not permission
to promote research E0–E6 or MIE computational evidence into execution.

## Scope and preserved work

The requested feature branch is `develop/v1.7-entry-qualification-evidence`.
Its local base is `9ef040251bdd5dc7d4792c6093690874716dbab0`, following core
integration `21b89a0`. Remote main `d3f206a` is an ancestor. The two unpublished
local milestones were preserved instead of switching to an older main and
discarding that integration. After the permission change, the feature branch
was pushed through `e9902cc`; its Docker CI succeeded. See the
[verified foundation checkpoint](evidence/entry_qualification_foundation_20260911.md).
Publication and CI do not imply a main-branch merge or deployment.

Only the workspace checkout is being edited. No deployment directory, existing
container, account, firewall, credential, or trading flag is changed. The initial
restricted session could not access Docker. After the user changed permissions,
read-only preflight confirmed the existing API/PostgreSQL/Redis containers are
healthy, belong to `C:/CTCC-V2`, and the deployment checkout remains at `d3f206a`.
Its three Live authority flags are false, but Demo order writes and automatic
execution are already true. Those existing Demo settings were not changed;
this feature has not been deployed there. Never run the legacy verification
script against that Compose project: it starts/rebuilds the named services.
Only a separately named, no-credential test image/environment may be used.
Unit tests use the existing hermetic `analysis_only` environment with
execution authority disabled. The spec's `okx_demo` setting applies to a future,
separately isolated Demo acceptance environment, not offline unit tests.

## Foundation contracts

`app/trade_qualification/models.py` introduces immutable Pydantic domain
records for `EntryQualificationResult`, `EntryTrigger`, `EntryZone`, and
`GateAssessment`. They validate same-report identity, finite/strict values,
aware timestamps, bounds, chronology, and an ordered gate prefix. Unknown
early-stage evidence stays unknown. A failed gate ends the prefix; later
scores, RR values or passing gates cannot override it.

The decision, state, failure codes, drift and risk/evidence/recheck indicators
are derived output fields, not caller-set permission fields. Serialize inputs
with `model_dump_json(round_trip=True)` when reconstructing the model. Ordinary
output JSON includes computed fields for reporting and is not an input envelope.
Pydantic's `model_copy(update=...)` is not a validated update boundary.
Gate measurements are immutable scalar snapshots. Their JSON encoding tags
Decimal values as `{"type":"decimal","value":"1.2300"}` so numeric-looking
text stays text during reconstruction. Drift and geometric RR use a fresh
100-digit Decimal context, independent of caller precision/rounding. A reported
gross RR must match the candidate entry/SL/TP geometry to 12 decimal places;
net RR still requires the later cost evaluator and configured minimum.

Twelve passing gate records still cannot finish the chain without the final
recheck record. A passing SL gate alone is not `PROTECTION_VALID`; TP must also
pass. A structurally consistent all-pass record is **not proof that any gate
actually evaluated live market evidence**, nor an exchange execution permit.
No runtime consumer or order-writing API is registered by these new models.
Gate evidence is still caller-provided at this foundation stage; the actual
evaluators, evidence renderer, quote acquisition and execution authority checks
are separate tasks below. A model's frozen setting is not a security boundary
against arbitrary Python code; consumers must validate their own trusted inputs.

The forthcoming timing/location engines must use strategy-specific events and
policies. An unexpired 20-minute legacy candidate alone cannot establish entry
timeliness. The initial zone contract requires both candidate and reference
prices inside the explicit zone; no unspecified tolerance is invented.

## Strategy hard conditions

`Condition.required` follows the existing positional `veto` field. Required
failures use stable condition codes, and score components expose whether they
are required. Existing score thresholds, vetoes, mathematical downward caps,
operator disables and direction checks stay in place.

The required-condition map is strategy-specific:

| Strategy | Required conditions, in addition to data quality |
| --- | --- |
| trend_pullback | 4H trend, 1H trend, 15m EMA pullback, 5m momentum |
| breakout_continuation | 4H permission, 15m BOS, 5m trend |
| liquidity_sweep_reversal | 15m sweep predicate, 15m CHoCH, 5m momentum |
| fvg_return | 4H direction, 15m FVG predicate, 5m trigger |
| order_block_return | 4H direction, 15m order-block return, 5m trigger |
| range_reversal | range regime, range edge, 5m turn |
| structure_reversal | 1H CHoCH, 15m follow-through, 5m trigger |
| volatility_expansion | 15m expansion, 15m BOS, 5m momentum |

Existing optional volume/context confirmations remain score inputs. Some
strategies have no optional score inputs and therefore score 100 whenever all
their necessary conditions pass. That is not calibration or a probability;
required gates must not be loosened just to restore a score distribution.

Known semantic limitations remain explicit: the existing FVG predicate checks
an unfilled gap, not an observed return into that gap. The existing liquidity
sweep predicate does not independently prove the full piercing-and-reclaim
event. Hard-gating those predicates does not solve these event/zone gaps.
New API-domain fields also do not establish database persistence: the ORM
`StrategyEvaluation` is a different model, and its JSONB `score_breakdown`
integration must be assessed before durable evidence is claimed.

## Pre-score regime routing

`app/strategies/regime.py` derives a deterministic route from the existing
four-timeframe analysis snapshot and rechecks its legacy classification. It
does not accept a new caller-declared regime as permission. The route retains
the classification basis and a SHA-256 of the validated complete snapshot.
Missing, malformed, non-finite, conflicting-quality or mismatched snapshot
evidence fails closed. Existing risk/unknown blockers are not erased.

The named evaluator registry allows `StrategyService` to filter before calling
any scorer. Excluded strategies retain an audit row with
`scoring_performed=false`, no candidate and `regime_not_permitted`; its zero
score is only a placeholder. Returned evaluator/candidate identities must match
the routed strategy. Required failures, vetoes, operator disables and downward
mathematical ranking still apply. The API exposes a frozen `regime_route`; this is not
yet durable same-report evidence or a complete entry-qualification gate.

| Snapshot regime | Strategies allowed to reach their existing gates |
| --- | --- |
| Trend | Directionally permitted trend pullback, breakout continuation, FVG return, order-block return; pullback also requires 1H permission |
| Range | Range reversal, only with neutral HTFs and an intact 15m support/close/resistance bracket |
| Expansion | Breakout continuation only, with current 15m high volatility, BOS and HTF permission |
| Compression | None; await a confirmed event |
| HighVolatility, RiskOff, Unknown | None |

This is deliberately a snapshot-only conservative policy, not a claim that
all six/eight strategy families have complete event routing. Prior compression
is absent, so volatility-expansion scoring remains blocked. Sweep/reclaim and
range-to-structural-reversal chronology are also absent, so liquidity-sweep and
structural-reversal scoring remain blocked. The current structure engine cannot
generate 1H CHoCH with a neutral 1H trend; a synthetic contradictory snapshot
must not pretend that route is implemented. Step 6 must add actual event/history
evidence before these families can be admitted. Exclusion codes on an otherwise
allowed route describe blocked families, not a veto of every allowed family.

For range routing only, the legacy `multi_timeframe_not_aligned` blocker is not
a universal reversal veto; its original value stays in the audit and decision.
This change neither deletes downstream safety checks nor turns `trade_ready`
into execution authority. Current candle freshness, cross-source consistency,
trigger age, executable quote, entry location and the full twelve-gate chain
remain separate unfinished evaluations. Snapshot hashing proves identity of
the recorded inputs, not authenticity or freshness of external market data.

## Ordered remaining implementation

The source's order is retained; writing tests alongside each change does not
replace the final acceptance stages. Do not wire a partial chain into Demo.

| Steps | Work and acceptance | Status |
| --- | --- | --- |
| 1–2 | Workspace/safety preflight, preserve changes, create exact branch | Complete; existing deployment read-only |
| 3 | Qualification/zone/trigger/gate domain and consistency tests | Implemented locally |
| 4 | Required predicates for all eight strategies, stable failures, selection guard | Implemented; 65 targeted tests passed |
| 5 | Deterministic regime router before strategy evaluation/selection | Conservative snapshot routing implemented; event-dependent families fail closed pending step 6 |
| 6 | Actual trigger event, per-strategy timing, WAIT/CANCEL semantics | Pending |
| 7 | Zone provenance, executable quote, expiry/drift/location evaluation | Contract only; engine pending |
| 8 | Evaluate all legal 15m/1H/4H SL/TP brackets, noise/liquidity rejection | Pending; legacy first-complete behavior remains |
| 9 | Cost-adjusted economics and complete portfolio/Demo authority | Pending integration |
| 10 | Same-OHLC/report five charts and evidence packet | Pending |
| 11 | Actual 12 gate evaluators, measured values and fail codes | Record contract only; engine pending |
| 12 | Fresh executable quote after evidence, cancel stale old candidate | Pending |
| 13 | Guard every SafeDemoAutomation submit route | Pending |
| 14 | Post-submit durable Notion outbox, retry without duplicate orders | Pending |
| 15 | Same-report realized forensics, MFE/MAE/R and evidence-based attribution | Pending |
| 16–18 | Unit, full regression and isolated Docker hermetic acceptance | Latest route checkpoint: 1,370 full tests passed; foundation CI passed; rerun per later phase, not whole-project completion |
| 19–20 | Genuine old/new shadow and isolated Demo soak with sufficient samples | Not started; zero collected samples |
| 21 | Final audit and the four requested reproducible evidence examples | Pending |

Latest execution evidence: [regime routing acceptance](evidence/regime_routing_20260911.md).

## Dependencies and unresolved decisions

- Docker CLI/engine became readable after the user's permission change; the
  existing deployment is running Demo. New-feature isolation and acceptance
  are separate requirements. Do not rebuild/restart the deployment as a test.
- The previous Windows full-unit attempt had two symlink fixture failures due
  to WinError 1314. Both ran successfully in the complete isolated Linux suite;
  no tests were skipped or weakened and no Windows host privileges were changed.
- The foundation feature branch is published with successful Docker CI. Main
  remains protected and the new chain is not deployed.
- Existing Continuous Demo risk bypass settings must not silently waive the
  new portfolio chain. Existing score-tier leverage is not score calibration.
- Reversal HTF permission must be strategy-specific, not a universal 4H=1H
  constraint. Unknown and Risk-Off regimes do not automatically qualify.
- A robust SL/TP ranking needs explicit structure and cost evidence; nearest
  15m levels and invented RR cannot stand in for it. No claim of improvement
  is justified until observed shadow/Demo outcomes support it.
- A durable post-submit outbox needs order/report idempotency and recovery if
  local persistence fails after a successful or ambiguous submit. Notion retry
  must never re-enter an order submit path.

Full completion requires every source acceptance condition and all four real,
reproducible evidence examples. Synthetic unit fixtures are not Demo samples,
TradingView captures, source verification, or a claim of profitability.
