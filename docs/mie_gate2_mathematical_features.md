# MIE Gate 2 — Mathematical Feature Core

Gate 2 extracts five deterministic feature families behind a strict confirmed-
bar boundary. It is shadow-only, has no runtime consumer, adds no migration,
and cannot create a probability, decision, risk budget, order, leverage, or
exchange write.

## Input and replay boundary

`FeatureWindow` accepts only:

- positive, finite OHLC values;
- non-negative finite volume;
- `confirmed=true` bars;
- strictly increasing UTC close timestamps;
- an exact close-to-close interval equal to the declared horizon;
- a final close timestamp no later than `as_of`.

The complete window has a canonical SHA-256 provenance digest. The atomic
`MathematicalFeatureSnapshot` records that digest, its exact data cutoff, all
five family outputs, `authority=shadow_only`, and
`execution_authority=false`. Repeated evaluation of the same window produces
the same replay digest.

## 1. Descriptive statistics

For confirmed closes `C_t`, Gate 2 first computes log returns:

```text
r_t = ln(C_t / C_(t-1))
```

It exposes the population mean and standard deviation, median, robust scale
`1.4826 × MAD`, zero-target downside deviation, and the fraction farther than
three robust scales from the median. These are descriptive quantities, not
evidence that a return is predictable.

## 2. Causal signal processing

The signal engine applies an EWMA to log returns in arrival order:

```text
s_t = alpha * r_t + (1 - alpha) * s_(t-1)
e_t = r_t - s_t
```

It reports the endpoint smoothed return, raw-return RMS, residual RMS, bounded
noise ratio, and a bounded signal-strength diagnostic. No centered window,
zero-phase filter, later candle, or visual chart angle is used.

## 3. Causal dynamics

The dynamics engine extracts the already characterized v1.6.8 endpoint
derivative into MIE without importing or replacing the legacy runtime path. It
fits a recency-weighted quadratic to the latest log prices on normalized
one-sided coordinates `x in [-1, 0]`:

```text
y(x) = a + b*x + c*x^2
weight: 1 -> 3 from oldest to newest
velocity     = b / (window - 1)
acceleration = 2*c / (window - 1)^2
```

Velocity and acceleration are normalized by per-bar log-return RMS. Fit R²,
residual standard deviation, confidence, and direction remain bounded. Exact
characterization tests require field-for-field equality with the frozen
legacy estimator for the same log-price window.

## 4. Momentum

Fast and slow cumulative log returns are normalized by per-bar return RMS and
the square root of their respective horizons. Their fixed 60/40 descriptive
blend is accompanied by directional persistence and bounded volume
confirmation. The result is a dimensionless momentum diagnostic—not a win
probability and not a calibrated forecast.

## 5. Confirmed geometry

A swing is accepted only when it is the unique high or low in its configured
left/right neighborhood. A pivot at bar `i` is invisible until bar
`i + right_bars` has closed. The engine returns the latest confirmed swing
high/low, nearest confirmed support/resistance around the current close, and a
bounded position inside the most recent swing range.

This confirmation delay prevents future leakage at the decision cutoff. It
does not prove that a swing level has economic forecasting value.

## Validation status

Gate 2 proves:

- deterministic replay and finite outputs;
- strict timestamp and confirmed-bar invariants;
- exact legacy-dynamics characterization;
- 50 seeded randomized field-for-field legacy-dynamics comparisons;
- constant-series and invalid-input behavior;
- pivot confirmation delay;
- 100 deterministic randomized finite/bounded paths;
- source-provenance sensitivity;
- no execution-side imports or external runtime consumers;
- fail-safe runtime defaults remain unchanged.

The maximum claim is computational correctness plus causal implementation.
Statistics, signal processing, momentum, and geometry remain shadow-only.
Legacy dynamics retains its existing downward-only causal classification; Gate
2 does not promote it. No feature is `predictive_oos`, `economic_oos`, or
eligible for a decision gate.

## Explicitly deferred

Gate 2 does not implement probability normalization, calibration, outcome
matching, Bayesian updating, regime inference, AI/ML, scenarios, expected
value, position sizing, runtime shadow wiring, persistence, API routes, or
execution. Those remain separate later Gates.
