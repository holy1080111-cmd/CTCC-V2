# Finite Live portfolio risk

`LiveFinitePortfolioInputsV1` is a separate DATA contract for Live raw-account
and prospective financial-epoch operands. It cannot be admitted as a legacy
Demo or Live portfolio snapshot. It has no historical loss seed, synthetic
realized-outcome population or claim to a continuous all-time equity peak.

The original UTC daily and rolling-seven-day net cash windows remain required,
including losses before the prospective epoch. The epoch has its own explicit
start, policy, sample sequence, measured peak and lifecycle loss counter. Unknown
cash losses, lifecycle state, peak or source obligations deny qualification.
Freshness is limited to five seconds and the history must cover the actual
evaluation window. Financial identity, current equity, credential session and
drawdown-window bindings are checked again during shared risk evaluation.

The closed Live finite branch reuses the existing exact rational sizing,
reservation rounding, correlation, availability and hard-cap calculations.
Structural trade risk remains at most 0.5%, aggregate stop risk at most 1%,
per-instrument margin at most 300 USDT, and aggregate margin at most 60%.
An explicitly smaller source margin bucket remains binding.

Validation: 431 Windows tests passed with zero failures, errors or skips. This
includes the new finite Live cases and the unchanged Demo portfolio, Live
kernel, and Demo finite-financial regressions. Tests use declared synthetic
DATA. Genuine source/financial ownership, SQL reservation, final physical
dispatch and explicit human Live Arm remain the coordinator's responsibility.
Neither this codec nor a passing pure calculation grants trading authority.
