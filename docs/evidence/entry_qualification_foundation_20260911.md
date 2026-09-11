# Entry qualification foundation — verified checkpoint

Scope: Notion implementation steps 1–4 only, not the complete trading chain.

## Source and saved revision

- Branch: `develop/v1.7-entry-qualification-evidence`.
- Verified source: `e9902cceccfe5b37ca387c1ab8d34ba86c792697`.
- Local feature base: `9ef040251bdd5dc7d4792c6093690874716dbab0`.
- Remote main ancestor: `d3f206a59888ef3d72732fa30deaa8278ac72cc5`.
- [Source commit](https://github.com/holy1080111-cmd/CTCC-V2/commit/e9902cceccfe5b37ca387c1ab8d34ba86c792697).
- [Successful Docker hermetic CI](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/34616131857).
- Manifest verified locally and in CI: 441 source files at this checkpoint.
- No migration was added by the foundation; existing schema head is `0016`.

Earlier checkpoints `21b89a0` (123 core/source-bound MIE evidence) and `9ef0402`
(offline strategy metadata intake) are included in the pushed branch. Neither
this branch nor those milestones were merged to main or deployed by this run.

## Executed acceptance

| Check | Observed result |
| --- | --- |
| New qualification model suite after review repairs | 93 passed |
| Eight-strategy required-condition suite | 65 passed |
| New model/test selected Ruff rules | Passed |
| Windows full-unit attempt before final model-review additions | 1,238 passed, two existing symlink fixture errors (WinError 1314) |
| Earlier Linux network-none full-unit baseline before review repairs | 1,240 passed in 30.03 seconds |
| Final reviewed Linux full suite, isolated PostgreSQL/Redis | 1,277 passed, two dependency deprecation warnings, 31.36 seconds |
| Alembic upgrade from empty isolated DB | All migrations through 0016 succeeded |
| Alembic heads/current | Both 0016 (head) |
| Alembic drift check | No new upgrade operations detected |
| Protected-branch workflow on feature push | Success; execution authority disabled, schema/targeted/full verification passed |

The two Linux warnings concern Starlette/httpx and the AnyIO BlockingPortal
alias. They are not failed tests; no dependency migration was attempted merely
to hide warnings. The Windows symlink tests were not skipped or weakened. Their
Linux execution is included in the successful full run.

All currently collected unit and integration tests were executed in the final
local Linux run. This does not mean tests for unimplemented future modules
exist or have passed. Timing, event extraction, location/economics/risk engines,
rendering, final quote recheck, Demo wiring, durable Notion outbox, forensics,
old/new shadow and actual Demo soak remain tracked in the
[implementation plan](../entry_qualification_implementation.md).

## Review defects repaired before the checkpoint

1. Required setup/trigger predicates cannot be replaced by other score points.
   A 99-point synthetic result with one missing required point is rejected.
   Service selection also checks failure codes and failed required components.
2. Gross RR is checked against candidate entry/SL/TP to 12 decimal places.
3. Gate measured values are copied into an immutable mapping; serialized
   Decimal values carry a type tag so numeric-looking text remains text.
4. Drift and geometric RR use a fresh 100-digit Decimal context, with tests
   under multiple caller precisions and rounding modes.

These are deterministic contract/predicate repairs, not proof of external
source authenticity, strategy edge, calibrated win probability, or profitability.
Existing FVG-return and liquidity-sweep event limitations remain documented.

## Test isolation and deployment observation

The local test image was `ctcc-entry-check:20260911-reviewed`, image ID
`sha256:871cbb5ed8e17b1f7334e133addebba8fac37316a01bb908a416cbf2d25f68b4`.
It uses the repository Dockerfile's explicit source inputs, with no account
credential or host-data mounts. A separate Compose project
`ctcc-qualification-check-20260911` used an internal-only network, temporary
PostgreSQL/Redis data, no published ports, and a read-only, capability-dropped
runner with writable temporary storage. The runner invoked migrations and
pytest directly, not the application's server/automation command.

After success, exactly that project's two test database containers and internal
network were removed. Temporary test data was discarded; tests can recreate
it. No production or Demo database volume was removed. The local test image,
source backups and acceptance configuration remain available.

Read-only deployment observations after permissions became available:

- Existing deployment checkout `C:/CTCC-V2` remains at `d3f206a`.
- API/PostgreSQL/Redis were healthy. API container remained `74b1f3a70c27` before
  and after local testing.
- Deployed `LIVE_TRADING`, `OKX_LIVE_ALLOW_ORDER_WRITES` and
  `OKX_LIVE_AUTO_EXECUTION` were false.
- Deployed Demo write and automatic execution flags were already true. This
  run did not enable, disable, rebuild, restart or otherwise modify them.
- No exchange order was submitted by this work. This is not a claim that the
  user's independently running old Demo automation produced no orders.

## Local recovery copies

These files are local-only under `reports/entry-acceptance-20260911/`:

- `ctcc-e9902cc.bundle`: 76,852 bytes; `git bundle verify` passed. It requires
  parent `d3f206a`, preserving every new commit through `e9902cc`.
  SHA-256: `5d786e2bb29c32c195180d33c482fe5cdd36c31350fdca560b6716bb9cf30208`.
- `ctcc-source-e9902cc.zip`: 810,720 bytes; complete tracked source snapshot.
  SHA-256: `2013014240dc08575a035c6e2a147d7e5650b396d692caf1e792276e67755883`.
- `compose.test.yml`: the isolated acceptance configuration, not the existing
  deployment's Compose configuration. Its database password is a disposable
  synthetic test value and is not an account or deployment credential.

Reports, real archives, deployment configuration and account credentials were
not added to the GitHub commit or source ZIP.
