# Source-bound entry engines checkpoint — 2026-09-12

Branch: `develop/v1.7-entry-qualification-evidence`.
Parent: `97546d388e81b566f80fad2c1dc2e7a2a6e0227b`.
Scope: source events, timing, entry location and existing Demo safety repairs.
Structural selection is the next in-progress change, **not part of this checkpoint**.
No database migration, new exchange caller, runtime registration or deployment.

## Verified focused acceptance

One combined run completed **924 passed in 30.49 seconds**, with three expected
Pydantic serialization warnings from deliberately malformed model-copy inputs.
Those inputs were rejected; the tests did not loosen value validation.

| Suite | Tests passed |
| --- | ---: |
| Existing qualification consistency models | 93 |
| Source event contracts | 25 |
| Closed-source event extraction | 76 |
| Per-strategy timing | 145 |
| Source-derived entry location | 339 |
| Eight-strategy OHLC → event → timing → location integration | 64 |
| Existing Demo automation and shared Demo service | 182 |

Reproduce from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_trade_qualification_models.py tests/unit/test_qualification_event_models.py tests/unit/test_qualification_events.py tests/unit/test_qualification_timing.py tests/unit/test_qualification_location.py tests/unit/test_qualification_entry_chain.py tests/unit/test_demo_automation.py tests/unit/test_okx_demo_service.py -o addopts= -q
```

New qualification files pass Ruff checks and formatting. Existing Demo modules
have pre-existing style diagnostics; correctness rules and diff whitespace
checks pass. Nine owned strategy-format changes have AST equivalence with the
parent checkpoint. No unrelated work was reset or removed.

Adversarial checks exposed and fixed genuine integration defects: high-precision
EMA/ATR bands were initially rejected before tick alignment; an already known
15m invalidation could be missed by a 5m-only scan; local clock folds could reverse
time causality; and a same-report zone could otherwise be applied to a different
instrument. Tests cover these repaired boundaries, not just hand-filled passing
gate records.

## Safety and limits

Read-only recheck confirmed the existing deployment remains
`d3f206a59888ef3d72732fa30deaa8278ac72cc5`, with healthy API `74b1f3a70c27`,
Redis `3a7bd5e0840b` and PostgreSQL `7e2817609bc6` containers. The workspace
changes did not restart them. Live trading, Live writes and Live automation are
false. Demo writes and Demo automation were already true and remain unchanged.
No exchange order was submitted by this work.

All new source/quote tests are synthetic and run without credentials. Source
digests and typed provenance prove input consistency, not external authenticity.
The collectors, complete historical regime admission, all-gate pipeline,
rendered evidence, fresh post-render recheck, durable intent/outbox, forensics,
shadow and isolated Demo soak are not completed by these tests. Observed shadow
and new-pipeline Demo sample counts remain zero. Engineering timing/zone policies
are not OOS calibration, probabilities or evidence of profitability.

The new full Linux regression, empty-database migration check and GitHub CI must
be run against this checkpoint separately. This source document records the
focused pre-commit result; later run IDs, source archive hashes and full results
belong to the verified local receipt and linked Notion implementation plan, not
an unsupported claim made before those runs finish.
