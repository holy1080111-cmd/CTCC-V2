# MIE Gate 1 — Contract Extraction

Gate 1 defines the complete shadow evidence chain without implementing a new
forecast model and without connecting to execution.

## Contract flow

~~~text
feature snapshot reference
  -> Evidence[]
  -> ProbabilityForecast
  -> RegimeSnapshot
  -> ModelHealth[]
  -> DecisionCandidate
  -> MieShadowTrace
~~~

Every record is immutable, rejects unknown fields, requires UTC timestamps,
uses an explicit instrument and horizon, carries version/provenance data, and
has execution_authority=false. Nested contract instances are always
revalidated, including instances produced through `model_copy(update=...)`.

## Contracts

### Evidence

Evidence separates direction, strength, reliability, uncertainty, data
quality, validation level, permitted use, dependency group, versions, and
immutable source provenance.

The validator enforces the authority matrix:

- auxiliary evidence can only shadow or break an otherwise exact tie;
- computational evidence is shadow-only;
- causal and prequential evidence can only shadow, downgrade, or block;
- a decision gate requires predictive OOS or higher validation;
- predictive OOS or higher validation requires an external frozen artifact;
- a claim cannot exceed the artifact's reviewer-attested validation level;
- evidence sample size must match the attached validation artifact;
- no evidence level grants execution authority.

### ProbabilityForecast

Long, short, and neutral probabilities must sum to one. A forecast may not call
itself calibrated unless it has at least prequential validation and an external
artifact matching its model identity and version. A predictive-OOS or higher
forecast separately requires an artifact attesting at least that claimed
validation level.

### RegimeSnapshot

Bull, bear, range, high-volatility, and transition probabilities must sum to
one. The dominant label must be the unique maximum; tied maxima are rejected as
ambiguous instead of being silently resolved by input order.

### ModelHealth

Healthy status requires fresh data, a passed leakage check, and no failure
codes. Predictive-OOS or higher health requires a frozen validation reference
and a recorded OOS validation time. Calibrated health requires prequential or
higher evidence, while degraded calibration cannot be reported as healthy. A
trace requires unambiguous evidence-source coverage plus exact model identity,
version, validation level, and artifact agreement for forecast, regime, logic,
and evidence health records.

### DecisionCandidate

The only actions are long_candidate, short_candidate, and no_trade. A
directional candidate requires all seven logic checks and positive net EV. It
contains no order id, quantity, contract count, leverage, margin, exchange
payload, or write authority.

### MieShadowTrace

The trace checks cross-record IDs, instrument/horizon consistency, source
health coverage, model/version consistency, monotonically nondecreasing data
cutoffs, causal timestamps, and nested execution-authority fields. Its
canonical JSON has a deterministic replay SHA-256. A static two-way package
boundary proves both that MIE cannot import execution modules and that existing
runtime modules cannot consume MIE during Gate 1.

## Legacy adapter

adapt_legacy_mathematical_core maps the frozen Gate 8 components into MIE
Evidence without changing the legacy strategy path:

| Legacy label | MIE validation | Permitted use |
|---|---|---|
| analytical | causal | risk downgrade/block |
| prequential | prequential | risk downgrade/block |
| auxiliary | auxiliary | tie-break only |

All legacy mathematical components are tagged legacy.price_path.shared. Future
Bayesian or ensemble work must therefore treat them as correlated evidence.

## Gate 1 boundary

Gate 1 intentionally does not add a database migration, probability fitting,
Bayesian multiplication, adaptive model weights, scenario simulation, EV
estimation, position sizing, execution adapter, or API route that changes
trading behavior.

## Verification

Run from the repository root with every write switch disabled:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_mie_gate1.ps1
~~~

The verifier builds Docker, checks runtime write flags, verifies Alembic remains
at 0012, runs Gate 1 tests, runs the full regression, validates Git whitespace,
and checks the canonical source manifest.
