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

### Optional 2,000 USDT capital buckets

`OKX_DEMO_CAPITAL_BUCKET_ENABLED=true` replaces the percentage-based margin
ceiling with an absolute, settlement-currency sizing ceiling. It requires the
score-risk gate and a verified single-currency USDT equity basis.

```text
target_position_margin(E) = min(E, 2000 USDT)
complete_slots(E)          = 1,                         when E <= 2000
                           = floor(E / 2000 USDT),      when E > 2000
effective_position_limit   = min(configured limit, complete_slots(E))
position_notional_ceiling  = min(target margin * selected leverage,
                                 available USDT * selected leverage,
                                 global notional ceiling)
```

This produces one full-equity capital slot when verified USDT risk equity is
below 2,000 USDT. Above the threshold, only complete 2,000 USDT slots can add a
position; residual capital smaller than a complete slot cannot create another
position. The equality case is unambiguous: 2,000 USDT is one slot.

The bucket is a maximum estimated initial-margin allocation, never a forced
minimum. Score-tier stop-risk, the shared mathematical downgrade/block gate,
exchange availability, global notional and contract caps, and lot rounding can
only reduce the order. They cannot enlarge it. A run records both the bucket
and the effective per-position cap for audit.

The 2,000 USDT threshold is an operator-selected capital constraint, not a
mathematically or statistically validated source of return. It never adds to an
analysis score, confidence, strategy rank, or reliability result. Validation in
this gate proves arithmetic partitioning, monotonic ceilings, write-path
enforcement, and failure behavior; it does not prove that 2,000 USDT is an
optimal allocation or that the strategy is profitable.

CTCC currently submits these Demo SWAP orders in OKX cross-margin mode. The
2,000 USDT value is therefore a local sizing ceiling (`notional / leverage`),
not an isolated exchange sub-account and not a maximum possible liquidation
loss. Cross-margin equity remains shared by the account. Enabling capital
buckets does not change margin mode or any Live boundary.

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

## Account-mode-aware capital basis

The adaptive Demo boundary supports USDT-settled SWAP instruments only. It
verifies the exchange `settleCcy` metadata before sizing a candidate.

For OKX account level 2 (single-currency margin), the risk denominator is the
USDT entry in `details[].eq`, and exchange buying-power capacity is bounded by
that entry's `details[].availEq`. A blank account-level `availEq` is expected in
this mode and is never converted into a false account-wide zero. BTC, ETH, OKB,
and other balances are neither numerically summed nor treated as USDT
collateral.

For account levels 3 and 4, the pooled risk denominator is adjusted account
equity and the capacity input is account-level available equity. Missing or
unsupported account-level, settlement-currency, or equity data blocks the run;
`totalEq` is retained only as account reporting and is never an availability
fallback.

Migration `0012` stores the selected equity basis. A legacy or changed basis is
rebased automatically only while the exchange is flat, no CTCC trade is
tracked, and the UTC session has not submitted a trade. Otherwise the
automation remains locked until a flat session can be verified.
Migration `0013` adds attributed rolling realized-PnL events and a separate
non-daily equity high-water mark. The continuous-session weekly-loss and
drawdown backstops therefore no longer depend on a UTC-daily proxy.

## Session stop and frequency gates

Each tracked instrument is finalized from filled closing-order realized PnL,
fees, rebates, and funding. A negative net close increments the consecutive
stop-loss count. A zero or positive net close resets it. At three consecutive
negative closes, new Demo entries are locked for the rest of that UTC day.
Standard mode also locks new entries when the configured daily realized-loss
limit or daily trade count is reached.

On the next UTC date, the counter, daily trade count, and daily baseline reset.
Open positions remain tracked. When more than one trade is involved, missing
instrument-level close evidence is not replaced with account-equity guessing;
the automation disarms and engages Emergency Stop.

### Optional continuous Demo session

`OKX_DEMO_CONTINUOUS_SESSION_ENABLED=true` removes the daily realized-loss
lock, daily trade-count lock, consecutive-negative-close lock, and post-close
symbol cooldown. Daily PnL and the counters remain persisted and exposed for
audit, but they do not determine eligibility while this mode is active.

This is not an unconditional or zero-delay order loop. The scheduler remains
non-overlapping and runs only at `OKX_DEMO_SCAN_INTERVAL_SECONDS` (minimum 60
seconds). Each scan still requires a fresh qualifying candidate and retains:

- protected stops, score tiers, mathematical downgrade/block, and strategy
  eligibility;
- the risk engine's weekly-loss and peak-equity drawdown backstops;
- one tracked position per instrument, aggregate stop-risk, capital-bucket,
  available-equity, global notional, contract, lot-size, and position limits;
- duplicate candidate fingerprints and per-run/execute-soak submission limits;
- exchange reconciliation, durable state, explicit Arm/Start, automatic
  disarm, and Emergency Stop.

The mode requires score risk, the 2,000 USDT capital-bucket boundary,
protection, and `OKX_DEMO_TRADE_COOLDOWN_SECONDS=0`. It is disabled by default,
does not enable Demo writes, is never restored as Armed after restart, and has
no effect on OKX Live. More eligible scans do not improve mathematical
expectancy and can increase fees, funding, slippage, and correlated loss.

`OKX_DEMO_DAILY_LOSS_LIMIT_PCT` remains a standard-mode and controlled-soak
configuration field, but it is not enforced by the normal automation path when
continuous mode is active. The controlled execute-soak retains its independent
loss budget and submission cap.

## Safe configuration

Keep all execution switches off while installing and testing. The following is
an example operator-reviewed Demo profile, not an instruction to enable it:

```env
OKX_DEMO_SCORE_RISK_ENABLED=true
OKX_DEMO_MAX_OPEN_POSITIONS=3
OKX_DEMO_MAX_TRADES_PER_DAY=6
OKX_DEMO_DAILY_LOSS_LIMIT_PCT=0.01
OKX_DEMO_AUTOMATION_MAX_CONSECUTIVE_LOSSES=3
OKX_DEMO_TRADE_COOLDOWN_SECONDS=1800

# Optional Demo-only continuous eligibility. Keep false until dry-run review.
# If true, set OKX_DEMO_TRADE_COOLDOWN_SECONDS=0.
OKX_DEMO_CONTINUOUS_SESSION_ENABLED=false

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

# Optional absolute Demo-only capital buckets. Keep false until dry-run review.
OKX_DEMO_CAPITAL_BUCKET_ENABLED=false
OKX_DEMO_POSITION_MARGIN_BUCKET_USDT=2000
```

Do not copy these values to OKX Live. Live promotion requires separate Demo
soak evidence, instrument-level close attribution, protection verification,
drawdown review, and a new production-boundary change with its own acceptance.

The optional confirmed-structure 3–20x profile has stricter dependencies,
isolated margin, cost-adjusted sizing, and separate formulas. See
`docs/demo_structural_dynamic_risk.md`; do not enable it by changing only the
leverage field.

To validate the 2,000 USDT policy without write authority, keep
`OKX_DEMO_ALLOW_ORDER_WRITES=false`, `OKX_DEMO_AUTO_EXECUTION=false`, and
`OKX_DEMO_SOAK_ALLOW_EXECUTE=false`; set only the score-risk and capital-bucket
feature flags to true, rebuild, and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_capital_bucket_dryrun.ps1 `
  -ExpectedBucketUsdt 2000
```

After explicitly configuring continuous Demo mode while all write switches
remain off, validate its status contract with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_capital_bucket_dryrun.ps1 `
  -ExpectedBucketUsdt 2000 `
  -ExpectContinuousSession
```

The verifier requires a disarmed/stopped automation, disabled Demo writes, a
USDT risk-equity basis, a 2,000 USDT bucket, no submitted result, every sized
margin at or below its recorded cap, and a shadow portfolio no larger than the
complete-slot limit. A run with no qualifying market signal may have zero sized
results and is still only a boundary check, not evidence of strategy quality.

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
5. In standard mode, prove three negative closes lock entries, a profitable
   close resets the sequence, and the next UTC day unlocks it. In continuous
   mode, prove daily PnL and counters remain observable but daily loss, trade
   count, streak, and cooldown do not lock entries. Prove weekly-loss,
   drawdown, duplicate, position, portfolio, and submission limits still block
   exactly at their configured boundaries.
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
