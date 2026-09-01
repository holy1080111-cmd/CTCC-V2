# CTCC-V2 Conversation Issue Audit

This register reconciles the CTCC-V2 failures reported in operator
conversations through 2026-08-25 against the current reviewed source tree. It
separates source defects from host/operator failures so a command-line parsing
mistake is not mistaken for trading correctness, and a passing unit test is not
mistaken for predictive or economic validation.

## Closed source defects

| Reported problem | Current source evidence | Closure status |
| --- | --- | --- |
| OKX public WebSocket rejected the subscribe request with `60033 Parameter id error` | `OkxPublicWebSocket` sends only `op` and channel arguments; its subscription arguments have a unit test | Closed |
| Paper recovery reported unequal memory/database checksums for equivalent state | persistence checksum v2 hashes durable state only; decimal scale, runtime market fields, and collection order have regressions | Closed |
| Dashboard snapshot audit code was absent from the rebuilt source/container | repository, router, unit test, and PostgreSQL integration test for `dashboard_snapshot_generated` are present | Closed |
| Runtime watchdog container lacked `_refresh_runtime_exchange_safety` | method and four direct unit scenarios are present | Closed |
| Demo-only REST transport could not be shared safely with Live reads | `OkxPrivateApiClient` protocol plus structurally separate Demo and Live transports and contract tests are present | Closed |
| Live write retry/ack ambiguity could duplicate or misreport orders | writes are single-attempt; ambiguous transport/shape outcomes are persisted and engage Emergency Stop | Closed at implementation level; exchange acceptance is separate |
| Empty top-level OKX `availEq` exhausted Demo capital although USDT detail had funds | settlement-currency detail parsing and single-currency USDT risk-equity selection have parser/service regressions | Closed |
| Equity-basis changes could silently resize a non-flat session | basis is persisted and a changed basis locks a non-flat/traded session | Closed |
| Mathematical gate tests had a missing `MathematicalConfirmation` name and an unsupported SOL fixture | focused and full hermetic regressions later passed; current unit suite retains the corrected cases | Closed |
| 150 USDT / 2,000 USDT capital-bucket tests inherited unsafe host environment variables | `scripts/hermetic_pytest.py` removes deployment setting leakage; verifier regressions cover it | Closed |
| Funding/mark updates could refresh one merged WebSocket timestamp and make an old last price or quote look fresh | realtime snapshots now carry independent last, quote, mark, and book receive times; execution checks the fields it actually consumes | Closed by field-freshness regressions |
| Demo and Live entries sized from last price rather than the executable side | Demo uses fresh ask/bid plus a worst-fill FOK boundary; Live uses fresh ask/bid; symmetric regressions cover both directions | Closed |
| A Demo long filled at 2,491.39 even though its stop/target implied only about 1.755 gross reward/risk, below the configured 1.8 floor | automation intersects the RR-derived price boundary with a configurable adverse-slippage cap, sizes at the worst allowed fill, submits FOK, and verifies terminal full fill, `accFillSz`, `avgPx`, actual RR, and protection; the incident geometry is a direct regression | Closed at deterministic execution-boundary level; future exchange fills remain external evidence |
| Protection could be evaluated against last price while OKX was asked to trigger on mark | Demo/Live requests are mark-trigger only; execution uses side-specific bid/ask, bounds mark-to-quote basis, and rechecks both executable quote and mark inside the bracket | Closed at deterministic boundary level |
| Live risk sizing ran before stop/target tick alignment | Live now aligns protection first, rebuilds reward/risk at the executable quote, then calls the risk engine | Closed |
| Candle `ts` was treated as close time although OKX supplies interval start time | candle quality and analysis freshness now use `open timestamp + bar duration` | Closed |
| Unrealized equity movement or deposits could be mixed into rolling realized PnL | the seven-day gate now consumes only de-duplicated exchange-attributed close outcomes; the former single-trade account-equity-delta fallback was removed and an unattributed close retains the trade while engaging Emergency Stop | Closed |
| Dynamic leverage ignored the ratio between account equity and the 2,000-USDT per-position margin bucket | required leverage is now `ceil(equity × risk_pct / (position_margin_cap × loss_rate))`; 150/2,000/5,000/10,000 boundary tests cover it | Closed |
| A successful set-leverage transport response was accepted without checking its effective fields | Demo and Live require returned instrument, margin mode, position side, and leverage to match exactly before order submission | Closed |
| Demo order acknowledgement could be accepted without confirming attached TP/SL | attached request/order metadata is never sufficient; the service polls the exchange pending-Algo endpoint for `conditional,oco` and requires the exact generated Algo client ID, instrument, stop, target, positive size, and mark-trigger fields | Closed at implementation level; exchange acceptance remains separate |
| A tracked position could remain open after its protection Algo disappeared or no longer covered its size | every automation reconciliation matches the durable protection client ID, prices, mark trigger, and Algo size against the exchange position; execute mode disarms and engages Emergency Stop without guessing that exposure vanished | Closed at implementation level; exchange evidence remains separate |

## Closed verification and installation defects

| Reported problem | Current control | Closure status |
| --- | --- | --- |
| Docker Desktop Linux pipe missing / WSL distribution stopped | treated as a host precondition; verifiers fail before source commit when Docker is unavailable | Operational precondition |
| Patch failed because the base tree or line context differed | final installers pin exact parent/tree/hash and stop without partial commit | Closed in delivery workflow |
| Source, image, and container differed | rebuild plus canonical source manifest and same-tree verification are mandatory | Closed in verification workflow |
| Docker image omitted `scripts/manifest.py` | Docker source packaging and full regression now include manifest tests | Closed |
| Manifest failed on scratch helpers or CRLF differences | canonical manifest excludes non-source artifacts and normalizes text newlines | Closed |
| PowerShell 5.1 treated Alembic stderr INFO as `NativeCommandError` | native steps temporarily use `ErrorActionPreference=Continue`, capture output, and decide by exit code | Closed |
| PowerShell stripped quotes from `python -c`, producing `expected = 0013` | all three authoritative Docker verifiers pipe literal here-string probes to `python -`; a source regression forbids `python -c` | Closed by this audit patch |
| A verifier checked write authority only after starting containers | all three authoritative verifiers resolve Compose flags and fail before Docker build/start, then check running `Settings` again | Closed by this audit patch |
| Only the unstaged diff received whitespace validation | authoritative verifiers check both unstaged and staged diffs | Closed by this audit patch |
| The GitHub connector could not inspect the private repository | the repository was intentionally changed to public on 2026-09-01 and the connector now returns `visibility=public` with repository access | Closed externally; repository is intentionally public |
| A local control-center overlay aggregated six independently timed endpoints | the overlay is preserved only as operator evidence and is not promoted; the canonical dashboard snapshot remains the sole multi-source UI contract | Rejected from runtime by design |
| `[decimal]::Abs` failed in an ad-hoc PowerShell probe | no repository script uses that invalid method; use `[math]::Abs([decimal]$value)` in operator-only probes | Conversation command, not source defect |

## Intentionally fail-closed behavior

The following reports are configuration errors, not reasons to weaken safety:

- `OKX_DEMO_CAPITAL_BUCKET_ENABLED` requires
  `OKX_DEMO_SCORE_RISK_ENABLED=true`.
- `OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED` additionally requires capital
  buckets, continuous session mode, structural protection, and its fixed
  leverage/risk ceilings.
- Demo Arm requires a JSON body with the exact confirmation phrase. The
  packaged controlled-soak script supplies that body; `/start` correctly
  rejects an unarmed service.
- A missing Docker daemon, invalid `.env`, unhealthy API, migration mismatch,
  schema drift, manifest difference, or enabled write flag stops verification.

These controls must not auto-enable dependencies, auto-arm, submit an order, or
rewrite the operator's `.env`.

## Still unproven or pending

- MIE Gate 2 proves deterministic implementation properties only. Its features
  have zero runtime consumers, zero execution authority, and no predictive or
  profitability claim.
- Mathematical, derivative, conformal, and structural confirmations remain
  auxiliary or downward-only until frozen out-of-sample, cost-adjusted evidence
  validates incremental value.
- Passing mocks, parsers, unit tests, PostgreSQL integration, and Demo dry-runs
  does not prove a profitable strategy.
- The 3/5/8/10/20x structural model remains Demo-only. Live automation still
  uses its separately bounded cross-margin 1–3x ATR path; promoting structural
  20x to Live requires a new reviewed gate and exchange evidence.
- Choosing the first complete 15m/1H/4H structure bracket is deterministic but
  not yet proven superior out of sample. It must remain a model-selection
  hypothesis rather than an extra score or leverage authority.
- Real-money readiness still requires the documented read-only account check,
  one operator-controlled protected micro-order, exchange-side fill/active
  pending-Algo/TP/SL
  confirmation, reconciliation, restart behavior, and incident rollback
  evidence. None of those steps is authorized by this audit.
## Acceptance rule

No item moves from pending to closed merely because code exists. Closure needs
the narrow regression, full hermetic regression, exact Alembic head/current,
schema-drift check, canonical manifest, healthy API, reviewed commit/tree, and
matching remote commit. Market-validity claims additionally need separately
frozen out-of-sample economic evidence.
