# Demo structural dynamic risk (3–20x, disabled by default)

This Gate integrates the 150-USDT / 2,000-USDT capital rule, confirmed K-line
structure, execution costs, score- and mathematics-capped leverage, multiple
instruments, and continuous Demo sessions without granting any new Live or
exchange-write authority.

## Authority boundary

The feature is inert by default:

```env
OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED=false
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
LIVE_TRADING=false
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
```

It can operate only after the existing authenticated Arm, Demo write flags,
reconciliation, exposure checks, duplicate suppression, portfolio gates, and
exchange protection checks pass. It never changes the OKX Live boundary.

Enabling the feature also requires `OKX_DEMO_MAX_LEVERAGE=20`, a portfolio
stop-risk ceiling of at least 6%, and a weekly-loss backstop of at least 6%.
The validation profile uses `MAX_WEEKLY_LOSS_PCT=0.10`; the default remains 5%
while this feature is disabled. Configuration validation fails closed if the
6% extreme per-trade ceiling could exceed either aggregate backstop.

A read-only validation profile is:

```env
OKX_DEMO_SCORE_RISK_ENABLED=true
OKX_DEMO_CAPITAL_BUCKET_ENABLED=true
OKX_DEMO_POSITION_MARGIN_BUCKET_USDT=2000
OKX_DEMO_CONTINUOUS_SESSION_ENABLED=true
OKX_DEMO_TRADE_COOLDOWN_SECONDS=0
OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED=true
OKX_DEMO_MAX_OPEN_POSITIONS=3
OKX_DEMO_MAX_LEVERAGE=20
OKX_DEMO_PORTFOLIO_MAX_RISK_PCT=0.10
MAX_WEEKLY_LOSS_PCT=0.10

OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
OKX_DEMO_SOAK_ALLOW_EXECUTE=false
LIVE_TRADING=false
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
```

`ORDER_SIZE_CAP_USDT`, exchange availability, contract limits, risk sizing,
and the 2,000-USDT bucket are all ceilings. The lowest ceiling wins; no setting
forces a full-margin order.

## Causal structure

The strategy snapshot is built from confirmed candles only. The swing engine
uses a symmetric pivot window, but a pivot becomes visible only after its
right-side confirmation candles have closed. At decision time no later candle
is read.

Timeframes are considered in this order: `15m`, `1H`, `4H`. A timeframe must
contain both sides of a complete bracket:

- Long: nearest confirmed support below entry and nearest confirmed resistance
  above entry.
- Short: nearest confirmed resistance above entry and nearest confirmed support
  below entry.

The volatility buffer is placed outside the stop anchor:

```text
buffer = max(ATR14 × 0.25, entry × 5 bps)
long stop  = support - buffer
short stop = resistance + buffer
target     = next confirmed structure in the trade direction
```

The target is never stretched to manufacture a favorable reward/risk ratio.
Missing or invalid structure blocks the candidate.

## Costs and net reward/risk

All rates below are fractions of notional. The default cost estimate is:

```text
round-trip fees       10 bps
round-trip slippage    4 bps
funding buffer         2 bps
total                 16 bps = 0.0016
```

For stop rate `s`, target reward rate `r`, and total cost rate `c`:

```text
gross RR = r / s
net RR   = (r - c) / (s + c)
```

The candidate is blocked when `r <= c` or `net RR < 2.0`. Risk sizing uses
`s + c`, not `s` alone:

```text
quantity = min(
    equity × tier_risk_pct / (price_stop_distance + entry × c),
    notional_ceiling / entry
)
```

This verifies unit consistency: price loss and estimated costs are both USDT
per base unit before quantity is applied. Fees are charged against notional,
not margin.

## Score bands and dynamic leverage

| Effective score | Risk ceiling | Leverage ceiling |
|---:|---:|---:|
| 72–79 | 1.5% | 3x |
| 80–89 | 2.5% | 5x |
| 90–94 | 3.0% | 8x |
| 95–97 | 4.0% | 10x |
| 98–100 | 6.0% | 20x |

The mathematical core remains downward-only: it can retain, reduce, or block
the effective score, but cannot increase the raw strategy score.

Confirmed swing prices are deterministic protection geometry, not predictive
evidence. They can make a stop/target pair tighter, wider, or ineligible and
therefore change worst-case sizing, but they never add direction score,
confidence, or 20x eligibility. Their market-alpha claim remains unverified.

Let `E` be account risk equity, `M` the current position-margin ceiling, `q`
the tier risk ceiling, and `s + c` total loss rate per notional:

```text
requested risk amount = E × q
required leverage = ceil((E × q) / (M × (s + c)))
```

The earlier shortcut `ceil(q / (s + c))` is valid only when `E = M`, which is
the below-bucket case. Above 2,000 USDT, omitting `E / M` understates the
leverage needed to deploy the account-level risk request from one fixed-size
position bucket.

CTCC selects the smallest value in `[3, 5, 8, 10, 20]` that meets the
requirement without exceeding the score-tier ceiling. If the requirement is
above the ceiling, the ceiling is used and actual risk is smaller than the
requested risk budget. Results retain both numbers and an explicit reason such
as `required_leverage_exceeds_score_tier_cap`,
`required_leverage_exceeds_approved_leverage_cap`, or
`required_leverage_exceeds_20x_safety_cap`.

20x additionally requires all of:

- effective score 98–100;
- structural protection and net-RR approval;
- mathematical status `confirmed`, grade `high`;
- mathematical confidence and reliability >= 0.65;
- mathematical instability <= 0.20;
- derivative status `confirmed` with confidence >= 0.65;
- isolated margin;
- normal portfolio, capital, reconciliation, protection, Arm, and write gates.

Failure of a 20x quality condition caps leverage at 10x. It does not make the
candidate eligible or add score.

Immediately before any Demo order, CTCC validates that the OKX set-leverage
response identifies the requested instrument, isolated margin mode, position
side, and exact leverage. A mismatch or unconfirmed response engages Emergency
Stop before the order call. After an order acknowledgement, attached or pending
mark-trigger TP/SL must also be confirmed; otherwise CTCC retains the exposure
record and stops without silently closing it.

The reviewed order boundary is mark-trigger only. A market long is risked from
the current ask and a market short from the current bid; a fresh public mark
must have acceptable basis and both the executable quote and mark must remain
inside the stop/target bracket at the final service check.

When this profile is enabled, configuration may only become stricter: score
thresholds may rise, risk/leverage ceilings may fall, estimated costs may rise,
the net-RR floor may rise, and 20x quality thresholds may tighten. Startup
validation rejects changes in the opposite direction, including a modeled
round-trip cost below 16 bps.

## Capital and portfolio rules

- Risk equity <= 2,000 USDT: one margin slot, capped by available risk equity.
- Risk equity > 2,000 USDT: one slot for each complete 2,000-USDT bucket, up to
  the configured position limit.
- One position per instrument.
- Total open worst-case stop risk includes estimated costs and cannot exceed the
  portfolio risk ceiling.
- A bucket is a ceiling, not a command to consume all available margin.
- Structural orders use isolated margin; a reconciled margin-mode mismatch
  engages Emergency Stop.

One deterministic 150-USDT boundary fixture uses a 0.10% structural stop,
0.16% cost buffer, score 99, and approved high-grade mathematics. The 6% risk
request needs more than 20x, so the 20x ceiling and one 150-USDT bucket cap
notional at 3,000 USDT. Estimated worst-case stop plus costs is 7.80 USDT
(5.2%), below the 9-USDT risk request. This verifies sizing mechanics only; it
is not a return forecast.

For comparison, with 10,000 USDT account equity and one 2,000-USDT bucket, the
same 6% request and 0.26% stop-plus-cost rate mathematically requires 116x.
CTCC still selects no more than 20x, reports the 116x requirement as an
unfunded risk-budget target, and sizes the position below the requested 6%
risk. `required_leverage` is therefore diagnostic, never permission to exceed
the approved cap.

Continuous Demo mode removes only the daily realized-loss entry gate, daily
trade-count gate, consecutive-loss gate, and post-close cooldown. It retains:

- rolling seven-day attributed net-PnL loss control;
- a high-water equity drawdown control that does not reset at UTC midnight;
- stop-risk, capital buckets, available equity, one-position-per-symbol,
  duplicate fingerprints, exchange reconciliation, protection, Arm, and
  Emergency Stop.

## What is and is not mathematically verified

Unit and invariant tests verify the causal data boundary, structure geometry,
cost units, net-RR equation, leverage ladder, 20x downgrade rules, isolated
order requests, rolling seven-day pruning/deduplication, non-daily high-water
mark, and fail-closed configuration dependencies.

These tests do **not** prove market profitability or that 150 USDT will grow to
2,000 USDT. Structure, momentum, and any uncalibrated market interpretation
remain auxiliary until walk-forward, out-of-sample, transaction-cost, Demo,
and execution-soak evidence meets the existing performance gates. Do not
enable Demo writes merely because deterministic tests pass.

Capital compounds geometrically only when realized cost-adjusted log growth is
positive. For win probability `p`, fractional net win `w`, and fractional net
loss `l`, the one-trade expectation is:

```text
g = p × ln(1 + w) + (1 - p) × ln(1 - l)
```

The time from 150 to 2,000 cannot be computed honestly until `p`, `w`, `l`,
trade frequency, dependence, gaps, and execution costs are measured out of
sample. CTCC therefore records predictions/outcomes and treats any unvalidated
growth estimate as auxiliary, never as leverage authority.

After configuring the structural feature and all of its dependencies while
keeping both Demo write switches false, rebuild and run the read-only probe:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_structural_dynamic_risk_dryrun.ps1 `
  -ExpectedBucketUsdt 2000 `
  -MinimumNetRiskReward 2.0
```

The probe accepts a run with zero approved candidates because current market
structure may legitimately block every symbol. It validates every candidate
that is approved and always requires `NO_ORDER_SUBMITTED=1`.
