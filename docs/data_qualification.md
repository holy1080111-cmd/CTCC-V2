# Explicit-clock G1 and executable-reference economics

This is an offline implementation increment after `d0ff9b7`, not the complete
12-gate engine, Execution Recheck, runtime source adapter or Demo integration.
All new outputs keep execution authority false. No trading flags are changed.

## Deterministic source reconstruction

`inspect_candles_at(candles, bar, *, current_time)` and
`analyze_snapshot_at(snapshot, *, evaluated_at, version)` are pure explicit-clock
entry points. The old wrappers retain their settings/wall-clock behavior and
existing quality policy. The new path does not instantiate a market service or
fetch network data, and uses a fixed Decimal context and canonical timeframe
order. UTC normalization precedes comparisons, including daylight-saving folds.

The strict inspector does not sort, fill gaps, infer confirmations or repair
prices. Wrong fields/types, nonfinite values, invalid OHLC/volume, duplicate or
unordered rows, off-grid times, missing bars, future closes and unconfirmed bars
are critical. A valid unconfirmed tail can be excluded for descriptive analysis,
but its quality remains critical and G1 rejects it. A confirmed close after the
actual market capture is invalid even if it precedes analysis time.

## G1 interface and policy

`evaluate_data(market, *, report_id, instrument_id, quote, reference, policy,
evaluated_at)` produces `DataQualificationResult` with one `GateAssessment` for
G1, deterministic failure code/reason, measured values and source/policy hashes.
It does not accept caller-supplied analysis or quality decisions.

`DataQualificationPolicy` requires explicit snapshot, public-quote, WS-reference,
per-timeframe candle-age, spread, reference-conflict, mark-dislocation and
absolute-funding limits. Minimum history is at least 200 confirmed candles per
timeframe (up to 1024); the analysis version is explicit and at most 64 characters.
These are engineering bounds, not trading calibration or profitability claims.

The raw market preflight validates exact model fields and types before
serialization, with bounded depth/node counts, lists, strings, Decimal
representations and total bytes. Caller quality is deliberately neither traversed
nor trusted; fresh quality replaces it. OHLC follows the same less-than-1e20,
at-most-20-decimal-place source contract as the existing event engine. Book depth
is 1–50 per side, with positive levels, strict ordering and no crossed top of book.
Required scalar and causal indicators are recomputed; missing, nonfinite or
timestamp/count/close-misaligned results fail, never become zero defaults.
The reused ADX implementation remains its existing final-DX approximation, not a
new claim of a fully reconstructed historical Wilder ADX series.

The separate `CollectedQuote` is reconstructed using the public collector's
provenance checks and then checked again at G1 evaluation time. Ticker, mark and
funding retain independent observations; a fresh merged receipt cannot rescue a
stale component. Legacy market `mark_price`, `funding_rate` and settlement fields
remain historical fields in the source archive, not current observations. Later
consumers must use the separately pinned collected quote for current prices and
costs, not those legacy fields. Current open interest is not authenticated here.

`WSReferenceObservation` requires matching report/instrument, fixed OKX / ws /
tickers semantics, positive bid/ask, source time and its own receipt time. This is
a **trusted-adapter input contract**; a real raw-frame WS adapter is not connected.
Neither its source label nor its hash proves a network observation. Missing WS
evidence is a failure; the legacy REST market snapshot is never relabeled as WS.

Reference conflict compares both corresponding bid and ask sides for WS, market
ticker and book against the collected REST quote, and market last against the REST
midpoint. The denominator is the collected REST midpoint. Mark dislocation uses
that same midpoint; each observed spread uses its own midpoint. Funding uses
absolute observed rate × 10000. Decisions use exact Fraction arithmetic; rounded
display bps are not used for limits. Equality at each age/price limit is allowed;
any excess fails. Decimal digit/exponent representation is bounded before hashing
or Fraction conversion, including otherwise valid numbers with huge zero tails.

## Output is evidence, not an authorization token

The canonical `source_json` has exactly the evaluator-produced market and analysis
payload, compatible with the existing event-source digest. Other fields pin the
public quote bundle and WS observation. `evaluation_sha256` strictly reconstructs
the record and binds the evaluation time, gate, policy and all source pins;
hidden fields injected via `model_copy` are rejected.

Domain records can still be constructed by callers. A matching self-computed
digest is not proof that G1 ran or that market data are authentic. A downstream
consumer must call `verify_data_result(result, market, *, report_id, instrument_id,
quote, reference, policy, evaluated_at)`, which reruns G1 against the original
inputs and compares the complete result. It does not authenticate a malicious
source adapter and does not replace G2–G12 or the final recheck.

## Separate executable-reference economics

`evaluate_executable_economics(...)` preserves the original entry, stop and target.
It calls the existing economics evaluator twice: once for the original candidate
entry and once for the current ask (long) or bid (short), keeping identical SL/TP,
quote and explicit cost policy. A candidate failure has priority and cannot be
repaired by a favorable sampled reference. Both scenarios must pass.

It reports signed/adverse entry displacement, both cost-adjusted risks, their
maximum, and the lower net RR. These are the worse of **two sampled scenarios**,
not the worst possible market fill. No entry repricing, stop tightening, leverage
increase, fill guarantee, historical-gate validation or publication-barrier proof
is added. Full recheck must additionally verify the original gate run, unchanged
event/zone, intervening candles, source deterioration and current account guards.

## Verification and remaining work

New tests cover explicit-time reconstruction (52), G1 source contracts and replay
(83), and candidate-versus-executable economics (116): 251 new cases. The broader
Windows pure-unit regression passed 1963 cases in 110.78 seconds with three
intentional malformed-model serialization warnings. These counts overlap. The
smaller analysis/data group passed 291 cases in 6.79 seconds. All new tests use
synthetic candles and mocked HTTP responses only; these are not market samples.

The broader suite includes all qualification tests except the previously separate
filesystem publication chain, plus domain, analysis, market, indicators, structure
and strategy tests. It is not the full unit/integration or Linux publication suite.

This session can run workspace Python tests, but attempting to execute Docker was
denied by the current restricted environment. No bypass, installation, service
restart or permissions change was attempted. This increment therefore must not
inherit the previous commit's complete Linux/Docker/CI acceptance. Its current
checkpoint receipt records which verification and publication actually succeeded.

Runtime prerequisites remain: a trusted WS adapter, aligned source/host clocks,
at least 200 closed candles (the old market snapshot default is only 100), actual
ordered G1–G12 coordination, historical regime admission, complete post-render
recheck, trusted account/history collection and atomic durable reservations.
Outbox, forensics and genuine new-pipeline shadow/Demo samples remain incomplete.
