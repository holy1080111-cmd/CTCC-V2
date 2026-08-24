# MIE Gate 2 Verification

Gate 2 is accepted only if its deterministic source checks and the operator's
Windows Docker/PostgreSQL gate both pass from the same reviewed tree.

## Targeted evidence

The Gate 2 test matrix must cover:

- immutable confirmed-bar contracts, UTC ordering, and exact horizon spacing;
- future-bar and invalid-OHLC rejection;
- exact constant-series descriptive statistics;
- smooth-versus-noisy signal residual behavior;
- field-for-field legacy dynamics characterization;
- 50 seeded randomized legacy-dynamics equivalence paths;
- bounded momentum and explicit absence of probability semantics;
- delayed right-side swing confirmation;
- deterministic snapshot/provenance SHA-256;
- 100 seeded randomized finite and bounded paths;
- missing-history fail-closed behavior;
- no execution imports, order geometry, or external runtime consumers;
- unchanged fail-safe runtime defaults.

These checks prove implementation properties, not predictive or economic
market validity.

## Operator acceptance

Keep every Paper, Demo, and Live execution-authority switch disabled. Read-only
analytical features may remain enabled because pytest is hermetically isolated
from deployment settings.

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_mie_gate2.ps1
```

Acceptance requires:

```text
MIE_GATE2_VERIFIED=1
MIE_GATE2_EXECUTION_AUTHORITY=0
MIE_GATE2_RUNTIME_CONSUMERS=0
ALEMBIC_HEAD=0014
API_HEALTH=healthy
```

The verifier resolves the Compose environment and rejects any enabled Paper,
Demo, or Live execution-authority flag before it starts or rebuilds the API.
After startup, it checks the unsanitized running `Settings` again before any
test isolation is applied.

The v1.6.8, MIE Gate 1, and MIE Gate 2 Docker verifiers all use the same safe
Windows boundary: no `python -c` quote transport, host authority checks before
container startup, literal probes piped to `python -`, exact `0014 (head)`, and
both staged and unstaged whitespace checks.

A canonical manifest pass and a clean reviewed worktree are mandatory before
commit. Gate 2 itself adds no migration; the reviewed repository head is now
`0014` after the Demo performance-equity safety correction.

## Explicit exclusions

Gate 2 does not authorize Gate 3 probability/calibration, runtime shadow
wiring, persistence, Demo execution, or Live promotion. Any predictive or
profitability claim remains invalid until separately frozen OOS evidence exists.
