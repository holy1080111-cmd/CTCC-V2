# MIE Gate 1 Verification Report

Date: 2026-08-12

## Result

Gate 1 was subsequently accepted by the Windows Docker/PostgreSQL boundary and
is present in the canonical `develop/v1.6.8` history. The hermetic-verifier
checkpoint `54964568a76c06afbdfdebd765353b03fc54e2b1` later revalidated the full
repository at migration `0013`, API health `healthy`, and a 317-file manifest.
Gate 2 may therefore proceed from that exact tree while remaining shadow-only.

## Verified scope

- Gate 0 rebaseline and validation registry.
- Strict immutable Evidence contract.
- ProbabilityForecast and sum-to-one probability invariant.
- RegimeSnapshot and dominant-regime invariant.
- ModelHealth with explicit per-model validation identity.
- DecisionCandidate restricted to long/short/no-trade candidates.
- MieShadowTrace with deterministic replay digest.
- Legacy Gate 8 mathematical-core adapter.
- Static package boundary preventing imports from exchange, Paper, Demo, Live,
  or execution packages.
- Runtime-default boundary keeping every write, automation, and adaptive Demo
  risk switch disabled.

## Authority invariants

Automated tests prove:

- unknown contract fields are rejected;
- copied/nested model instances are fully revalidated;
- every record is frozen and fixes execution authority to false;
- causal timestamps are UTC and future data is rejected;
- probabilities sum to one;
- analytical legacy evidence is represented as causal, not predictive;
- correlated legacy evidence shares one dependency group;
- auxiliary evidence cannot become a risk or decision gate;
- causal/prequential evidence cannot become a decision gate;
- OOS validation requires an external artifact, reviewer, dataset, sample size,
  model identity/version, attested validation level, metrics, and SHA-256;
- no Evidence, Forecast, or ModelHealth claim can exceed its artifact;
- evidence and artifact sample sizes must agree;
- future validation artifacts are rejected;
- calibrated forecasts require matching calibration evidence;
- positive-EV checks must agree with the numeric EV;
- a directional candidate requires every logic gate;
- a directional trace requires aligned OOS evidence and a uniquely dominant
  probability;
- evidence, forecast, regime, and logic each have health coverage;
- health coverage is unambiguous and model versions/artifacts agree;
- forecast and decision cutoffs cannot precede any linked input cutoff;
- forecast/regime/decision links cannot reference future records;
- the final trace contains no order geometry or execution-side imports.
- existing runtime modules cannot import or consume MIE during Gate 1.

## Test evidence

Executed in the isolated Python 3.12 environment:

| Check | Result |
|---|---|
| MIE targeted contract and integration flow | 43 passed |
| Entire unit suite | 373 passed |
| All tests not requiring PostgreSQL | 385 passed |
| Canonical manifest before Gate 1 | 284 files passed |
| Python compileall | passed |
| Git whitespace check | passed |

The complete suite collects 395 tests:

- 385 tests do not require PostgreSQL and passed;
- 10 tests are marked integration;
- the new MIE contract integration test passed directly;
- the nine existing PostgreSQL integration tests cannot resolve the Compose
  host `postgres` in this isolated environment because it has no Docker
  daemon.

The nine environment-blocked tests were also attempted. Their failures were
connection-name-resolution errors, not assertion or MIE failures. They are not
reported as passed.

One existing warning remains:

- Starlette TestClient deprecation warning concerning the future httpx2
  migration.

## Operator acceptance

From a clean `C:\CTCC-V2` with Docker Desktop running and all execution-authority
switches disabled:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_mie_gate1.ps1
```

The script checks the unsanitized running container first. Pytest then runs
through `scripts/hermetic_pytest.py`, which preserves only the configured
PostgreSQL and Redis URLs and resets every other application setting to safe
test defaults. Read-only analytical profile values therefore cannot alter MIE
contract-test expectations.

Acceptance requires:

```text
MIE_GATE1_VERIFIED=1
MIE_EXECUTION_AUTHORITY=0
ALEMBIC_HEAD=0014
API_HEALTH=healthy
```

Gate 1 is frozen only at the operator-verified canonical history; this report
does not promote any later Gate.

## Explicit exclusions

This candidate does not:

- change the legacy strategy decision;
- place, cancel, or close an order;
- enable Demo or Live writes;
- change leverage, margin, or position size;
- add a migration;
- claim predictive alpha;
- claim economic profitability;
- authorize Gate 2.
