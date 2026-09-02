# MIE Gate 3 — Offline validation foundation

This foundation implements the automation-safe portion of the frozen Gate 3
plan. Its current validation claim is `computational`. It does not contain a
real retrospective-holdout result and does not claim predictive or economic
value.

## Implemented boundary

- strict immutable preregistration, dataset, bar, label, feature, candidate,
  partition, trial, baseline, cost, uncertainty, provenance, metric, reviewer,
  and evidence contracts;
- context-independent canonical Decimal/UTC JSON and SHA-256 freeze/verify;
- fixed-schema revalidation before an artifact can be frozen, including nested,
  `model_copy`, `model_construct`, and subclass tamper rejection;
- point-in-time confirmed-bar replay, deterministic replay hashes, exact UTC
  interval alignment, missing/late/duplicate rejection, and future-row
  isolation;
- outcome labels that remain unavailable until the exact forward row is
  observable;
- expanding purged walk-forward folds with strict purge equality exclusion,
  cross-fold embargo accounting, and persisted-index leakage assertions;
- Decimal Brier score, clipped log loss, equal-width reliability bins, ECE,
  development-only prevalence, no-skill, and frozen legacy-score baselines;
- seeded circular moving-block percentile intervals and exact
  Holm-Bonferroni correction;
- normalized offline return-path calculations for turnover, fees, funding,
  spread, slippage, terminal flattening, drawdown, and CVaR;
- predictive-claim schema checks that bind the selected candidate to one frozen
  trial/configuration, require every declared baseline, holdout metrics,
  confidence intervals, sample counts, reliability bins, exact ECE, all trial
  results, corrected p-values, costs, and independent review.

The cost calculator remains descriptive/computational. It consumes normalized
shadow exposures, applies a frozen observation/funding cadence, and has no
order quantity, leverage, account, exchange, or execution semantics.

## Authority boundary

Gate 3 imports no API, risk, Demo, Live, Paper, exchange, or execution package.
No package outside `app/mie` imports it. It adds no route, migration, database
write, scheduler, runtime consumer, risk budget, order geometry, or exchange
credential access. Its contracts fix `runtime_consumers=0` and
`execution_authority=false`.

Run the zero-authority foundation gate. It ignores the operator `.env` and uses
the committed credential-free test profile. Each run receives a random Compose
project, unique containers and volumes, no published host port, and an
internal-only runtime network. A `finally` cleanup removes only that run's
isolated resources, so the deployed `ctcc-v2-*` stack is never reused or
stopped. Image build inputs may still use the normal dependency cache or
registry; the hard network isolation applies to the running verification
services, explicitly clears proxy injection, and blocks market/account egress:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_mie_gate3_foundation.ps1
```

Acceptance output includes:

```text
MIE_GATE3_FOUNDATION_VERIFIED=1
MIE_GATE3_ISOLATED_COMPOSE=1
MIE_GATE3_NO_PUBLISHED_PORTS=1
MIE_GATE3_INTERNAL_NETWORK=1
MIE_GATE3_ISOLATED_VOLUMES=1
MIE_GATE3_RUNTIME_ISOLATION_VERIFIED=1
MIE_GATE3_EXTERNAL_CONNECTORS_DISABLED=1
MIE_GATE3_EXCHANGE_CREDENTIALS=0
MIE_GATE3_RUNTIME_PROXIES_DISABLED=1
MIE_GATE3_CURRENT_CLAIM=computational
MIE_GATE3_REAL_HOLDOUT_READS=0
MIE_GATE3_RUNTIME_CONSUMERS=0
MIE_GATE3_EXECUTION_AUTHORITY=0
ALEMBIC_HEAD=0016
```

## Evidence still pending

The foundation does not complete Gate 3 market evidence. The following remain
separate, explicit, reviewable operations:

1. verify the pinned 180 public artifacts and 259,200 expected rows;
2. freeze and commit the real preregistration before reading the holdout;
3. execute the real replay exactly once against that frozen holdout;
4. independently review leakage, exclusions, trials, uncertainty, calibration,
   costs, provenance, and the canonical artifact hash;
5. decide whether the evidence fails closed, remains `computational`, or may be
   attested at most `predictive_oos`.

None of these steps needs or permits an exchange order. Gate 4 remains blocked
until an independently reviewed real artifact exists.
