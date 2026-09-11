# Structural selection and economics/portfolio checkpoint — 2026-09-12

Scope: Notion implementation steps 8–9, on
`develop/v1.7-entry-qualification-evidence`, parent
`67be58f5af9ed2219d5b572a19ba83d4fe83d219`. See the
[implementation record](../entry_qualification_implementation.md) for the
bounded anchor universe, explicit engineering policies and unfinished runtime
work. This is not final CTCC acceptance or a claim of strategy profitability.

## Verified focused tests

| Suite | Passed |
| --- | ---: |
| Source-reconstructed multi-timeframe structural selection | 97 |
| Explicit economics, funding and monetary precision | 75 |
| Complete portfolio/Demo evidence claims and exact risk limits | 357 |
| Same-candidate source → timing/location → structure → cost → risk | 48 |

All 577 new tests above passed. Root additionally ran the three existing legacy
structural tests with the new structure/economics suites (175 passed in 47.77s),
and portfolio/economics/location together (771 passed in 0.96s). These totals
overlap and must not be added as independent observations. New modules and tests
pass Ruff lint/format and diff whitespace checks.

Adversarial review fixed three concrete defects before this checkpoint:

- Repeating spread-bps division followed by multiplication and upward rounding
  could create a phantom `1e-20` cost quantum. Exact monetary spread is now added
  directly, preserving the inclusive minimum-net-RR boundary.
- Same account ID did not distinguish Live versus Demo source records. Every
  account evidence stamp now requires the explicit Demo environment.
- Correlation identity was checked only for the candidate instrument. All
  existing/pending instruments now reject contradictory group assignments,
  preventing exposure from being split across inconsistent group labels.

## Previous full checkpoint and current acceptance boundary

Parent 67be58f passed 2,107 isolated Linux unit/integration tests, migration to
0016 with no schema drift, a 456-file manifest and matching
[GitHub Docker CI](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/34623970371).
Its complete source ZIP, Git bundle and verified receipt were saved under
`reports/entry-acceptance-20260912/`.

Full regression for this new source must run separately against its immutable
archive. Later full results, commit SHA, image, CI run and archive hashes belong
in its local verified receipt and linked Notion plan; they are not claimed in
advance by this pre-commit source document.

The source-to-risk tests are synthetic. Current new-pipeline shadow/Demo sample
counts remain zero. No actual market-history admission, trusted account collector,
durable risk reservation, renderer, actual twelve-gate orchestration, post-render
fresh quote, execution wiring, outbox or forensics is proven by these tests.
Neither typed claims nor hashes authenticate external evidence or authorize orders.

Read-only recheck confirmed `C:/CTCC-V2` remains at `d3f206a`, API/Redis/PostgreSQL
healthy and unchanged. Live trading/writes/automation are false. Demo writes and
automation were already true and remain untouched. Nothing was merged, deployed,
restarted or submitted to the exchange by this work.
