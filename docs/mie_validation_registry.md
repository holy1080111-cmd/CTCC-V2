# CTCC-V2 MIE Validation Registry

This registry separates mathematical correctness from market validity. A
formula is not treated as alpha merely because it is deterministic, causal, or
well tested.

## Validation levels

| Level | Meaning | Maximum permitted MIE use |
|---|---|---|
| auxiliary | Heuristic or descriptive evidence | shadow or deterministic tie-break |
| computational | Formula and implementation invariants pass | shadow only |
| causal | Past-only timing and leakage invariants pass | risk downgrade or block |
| prequential | Sequential forecast diagnostic has measured coverage | risk downgrade or block |
| predictive_oos | Purged walk-forward predictive improvement is frozen | decision gate |
| economic_oos | Net EV after costs is positive with uncertainty bounds | decision gate |
| demo_execution | Demo fills, protection, recovery, and attribution pass | decision gate; no exchange authority |
| production_eligible | Independent promotion review is frozen | decision gate; Safety Kernel still decides execution |

No evidence level grants exchange-write authority.

## Current component registry

| Component | Current validation | Dependency group | Current permitted use | Missing promotion evidence |
|---|---|---|---|---|
| causal derivative | causal | legacy price path | risk downgrade/block | purged OOS information gain and net EV |
| robust causal state | causal | legacy price path | risk downgrade/block | state-model sensitivity and OOS value |
| conformal return interval | prequential diagnostic | legacy price path | risk downgrade/block | sharpness, conditional coverage, OOS economics |
| structure | auxiliary | legacy price path | tie-break only | objective labels and OOS incremental value |
| momentum | auxiliary | legacy price path | tie-break only | OOS incremental value after costs |
| mathematical aggregate | computational/causal composition | legacy price path | risk downgrade/block | calibrated probability and dependency correction |
| legacy strategy score | computational rule set | legacy strategy rules | Demo research only | score-to-probability calibration |
| score-to-risk tiers | bounded configuration | Demo risk policy | controlled Demo only | score-conditioned loss and EV calibration |
| settlement equity basis | execution/account semantic verification | exchange account | Safety Kernel input | no predictive claim required |
| summed stop-risk budget | deterministic bound | portfolio positions | controlled Demo only | covariance, gap stress, CVaR, liquidation stress |
| MIE descriptive statistics | computational | `mie.price_path.shared` | shadow only | purged OOS incremental information and economic value |
| MIE causal signal processor | causal implementation | `mie.price_path.shared` | shadow only | frozen parameters, OOS information gain, calibration and net EV |
| MIE endpoint dynamics | causal; exact legacy characterization | `mie.price_path.shared` | shadow only in Gate 2 | OOS information gain and net EV beyond legacy |
| MIE normalized momentum | computational/causal implementation | `mie.price_path.shared` | shadow only | OOS incremental value after costs and dependency correction |
| MIE confirmed geometry | causal confirmation; auxiliary market claim | `mie.price_path.shared` | shadow only | objective labels and OOS incremental value after costs |

All five Gate 2 families consume the same confirmed OHLCV path and therefore
share the `mie.price_path.shared` dependency group in later evidence adapters.
Gate 4 must not multiply them as independent likelihoods.

Derivative, state, conformal, structure, and momentum currently share market
price inputs. They must be tagged with a common dependency group so Bayesian
or ensemble code cannot multiply them as independent likelihoods.

## Promotion requirements

An evidence source cannot promote itself. Promotion requires an immutable
validation artifact containing:

- the exact validation level attested by the reviewer;
- source and model version;
- dataset identifier and provenance hash;
- exact train, validation, test, embargo, and purge boundaries;
- sample size and event definition;
- baseline comparison;
- Brier score, log loss, and calibration error when probabilities exist;
- block-bootstrap confidence intervals;
- cost, slippage, funding, turnover, drawdown, and CVaR measurements;
- multiple-testing correction;
- reviewer and issue date.

Until that artifact exists, causal and prequential evidence remains
downward-only even when its local confidence is high.

The contract rejects any Evidence, Forecast, or ModelHealth claim above the
artifact's attested level. An artifact and its claim must also agree on source,
model version, sample size where applicable, issue time, and SHA-256 identity.

## Terminology correction

Existing fields named `validated_components` mean that the implementation was
analytically or prequentially checked. They do not mean predictive or economic
validation. New MIE contracts use an explicit validation level and permitted
use so this distinction is machine-enforced.
