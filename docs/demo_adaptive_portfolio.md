# Adaptive OKX Demo portfolio gate

This capability increases Demo flexibility without expanding production
authority. It is disabled by default, never enables itself, never arms itself,
and does not change the OKX Live one-position/one-submission boundary.

## Allocation model

Candidates are evaluated first and sorted by effective risk score, highest first.
For each different instrument, CTCC applies the lower of the tier ceiling and
the remaining portfolio budget.

| Effective risk score | Leverage | Stop-risk ceiling | Per-position margin ceiling |
|---:|---:|---:|---:|
| 72–79 | 1x | 0.50% equity | 15% equity |
| 80–89 | 2x | 0.75% equity | 20% equity |
| 90–100 | 3x | 1.00% equity | 25% equity |

Aggregate open stop-risk is capped at 2% of equity. Estimated cross margin is
capped at 60% of equity. Actual size is also limited by stop distance, the
global notional cap, configured contract cap, exchange minimum and lot size,
currently available exchange equity, and rounding. A ceiling is never a
requirement to consume the full amount.

## Shared causal mathematical confirmation

The raw strategy score cannot grant medium or high adaptive risk by itself.
CTCC first classifies every analysis family as analytical, prequential, or
auxiliary through the shared mathematical core.
For every timeframe it uses only **confirmed** candles; the still-forming bar
is excluded before all calculations.

The local derivative keeps the latest 21 candles and fits a recency-weighted
quadratic to log price:

```text
y(x) = beta0 + beta1*x + beta2*x^2
velocity     = dy/d(bar) at the latest closed candle
acceleration = d2y/d(bar)^2 at the latest closed candle
```

The fit is one-sided: the latest closed candle is the endpoint and every other
sample is in its past. It therefore does not use a centered window, a future
candle, or the still-forming candle. Velocity and acceleration are normalized
by log-return RMS. Weighted R-squared, residual noise, and signal strength form
a bounded confidence value.

The core also runs a robust 34-candle constant-acceleration state filter. It
reports model-based velocity/acceleration uncertainty and uses a Huber innovation
weight so an isolated wick is downweighted while still increasing the safety
`shock_score`. A 90% one-bar conformal interval is calibrated from 60
sequential past-only residuals.

The 4H, 1H, 15m, and 5m estimates are combined with weights 35%, 30%, 25%, and
10%. Derivative and state evidence enter the execution core after analytical
checks. Conformal evidence also requires at least 30 causal coverage outcomes
and a passing 95% Wilson coverage diagnostic. Structure, momentum, and failed
conformal evidence remain auxiliary. Missing checked evidence lowers coverage,
conflicting checked evidence lowers consensus, and data-quality failures,
state shocks, or extreme volatility increase instability. Execution evidence
may only preserve or reduce exposure:

A failed conformal diagnostic is retained for audit with zero bonus weight, so
failing validation can never improve candidate priority.

| Mathematical evidence | Maximum effective score | Resulting maximum tier |
|---|---:|---:|
| Confirmed high grade | Raw score | High / 3x |
| Confirmed medium grade | 89 | Medium / 2x |
| Mixed, insufficient, or lower confidence | 79 | Low / 1x |
| Opposed or unstable | No score | Candidate blocked |

The API retains both `score` and `effective_score`, the original derivative
audit, and mathematical status, grade, confidence, and reliability. The risk
engine reads the capped score when present. Mathematical evidence never adds
score, changes a stop, bypasses strategy conditions, or authorizes a write.
An aligned auxiliary bonus from 0 to 5 is retained only for audit and true
tie-breaking after execution score, raw score, and validated confidence. It is
never passed to the risk engine.
See `mathematical_core.md` for formulas and deliberate exclusions.

OKX cross-margin SWAP leverage is managed per instrument, so CTCC allows at
most one active CTCC trade per instrument. Multiple different allowlisted
instruments may coexist up to `OKX_DEMO_MAX_OPEN_POSITIONS`.

## Daily stop-loss lock

Each tracked instrument is finalized from filled closing-order realized PnL,
fees, rebates, and funding. A negative net close increments the consecutive
stop-loss count. A zero or positive net close resets it. At three consecutive
negative closes, new Demo entries are locked for the rest of that UTC day.

On the next UTC date, the counter, daily trade count, and daily baseline reset.
Open positions remain tracked. When more than one trade is involved, missing
instrument-level close evidence is not replaced with account-equity guessing;
the automation disarms and engages Emergency Stop.

## Safe configuration

Keep all execution switches off while installing and testing. The following is
an example operator-reviewed Demo profile, not an instruction to enable it:

```env
OKX_DEMO_SCORE_RISK_ENABLED=true
OKX_DEMO_MAX_OPEN_POSITIONS=3
OKX_DEMO_MAX_TRADES_PER_DAY=6
OKX_DEMO_DAILY_LOSS_LIMIT_PCT=0.03
OKX_DEMO_AUTOMATION_MAX_CONSECUTIVE_LOSSES=3

OKX_DEMO_SCORE_MEDIUM_MIN=80
OKX_DEMO_SCORE_HIGH_MIN=90
OKX_DEMO_SCORE_LOW_RISK_PCT=0.005
OKX_DEMO_SCORE_MEDIUM_RISK_PCT=0.0075
OKX_DEMO_SCORE_HIGH_RISK_PCT=0.01
OKX_DEMO_SCORE_LOW_LEVERAGE=1
OKX_DEMO_SCORE_MEDIUM_LEVERAGE=2
OKX_DEMO_SCORE_HIGH_LEVERAGE=3
OKX_DEMO_SCORE_LOW_MARGIN_PCT=0.15
OKX_DEMO_SCORE_MEDIUM_MARGIN_PCT=0.20
OKX_DEMO_SCORE_HIGH_MARGIN_PCT=0.25
OKX_DEMO_PORTFOLIO_MAX_RISK_PCT=0.02
OKX_DEMO_PORTFOLIO_MAX_MARGIN_PCT=0.60
```

Do not copy these values to OKX Live. Live promotion requires separate Demo
soak evidence, instrument-level close attribution, protection verification,
drawdown review, and a new production-boundary change with its own acceptance.

## Acceptance sequence

1. Run the full suite with every write and automation flag disabled.
2. Run repeated `execute=false` Demo dry-runs and inspect raw score, effective
   score, mathematical status/grade/confidence/reliability, validated component
   codes, auxiliary component codes/bonus, derivative audit, tier, selected
   leverage, stop-risk, estimated margin, and aggregate portfolio totals.
3. Enable only one-submission controlled Demo execute-soak and verify TP/SL at
   the exchange. The soak passes its remaining order count into `run_once`, so
   a multi-symbol scan cannot exceed the one-submission acceptance gate.
4. Increase to two different Demo instruments only after clean reconciliation.
5. Prove three negative closes lock entries, a profitable close resets the
   sequence, and the next UTC day unlocks it.
6. Collect the configured reliability sample before considering any Live
   design change.

## Numerical-method references

- Savitzky and Golay, *Smoothing and Differentiation of Data by Simplified
  Least Squares Procedures*, Analytical Chemistry 36(8), 1964,
  https://doi.org/10.1021/ac60214a047
- SciPy `savgol_filter` reference for polynomial-order, derivative-order, and
  sample-spacing semantics:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.savgol_filter.html
- Kalman, *A New Approach to Linear Filtering and Prediction Problems*, 1960,
  https://asmedigitalcollection.asme.org/fluidsengineering/article/82/1/35/397706/A-New-Approach-to-Linear-Filtering-and-Prediction
- Gibbs and Candès, *Adaptive Conformal Inference Under Distribution Shift*,
  2021, https://arxiv.org/abs/2106.00170

CTCC implements its own Decimal endpoint fit because the trading calculation
must be causal and the project does not depend on NumPy or SciPy.
