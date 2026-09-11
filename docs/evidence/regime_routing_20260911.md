# Conservative regime routing — executed acceptance

This extends the [foundation checkpoint](entry_qualification_foundation_20260911.md)
on `develop/v1.7-entry-qualification-evidence`, parent
`e9902cceccfe5b37ca387c1ab8d34ba86c792697`. It is not deployed and does not
complete the Notion specification's whole entry/execution chain.

## Implemented and tested

- Derive a conservative route from a validated four-timeframe snapshot before
  invoking any scorer; preserve evidence basis and deterministic snapshot hash.
- Use a named, immutable eight-evaluator registry. Excluded strategies never
  run their scorer and have an explicitly unscored audit row, not a real zero
  score or eligible candidate.
- Reject missing/contradictory quality, malformed identity, future timestamps,
  non-finite measurements and inconsistent legacy classification. Decimal
  validation exceptions return Unknown/No Trade rather than escape.
- Preserve required failures, vetoes, operator disables, evaluator/candidate
  identity checks and the existing downward mathematical score cap.
- Freeze the API route audit. Independent review found that wrapping a frozen
  route in a mutable DTO allowed direct field changes; the DTO is now frozen,
  with assignment and serialization-round-trip regression tests.

The policy and allowed/excluded families are detailed in the
[implementation document](../entry_qualification_implementation.md#pre-score-regime-routing).
Prior compression, sweep/reclaim and range-to-reversal histories are absent,
so affected families remain blocked. The current structure engine cannot
generate neutral-1H CHoCH; a contradictory synthetic fixture was not retained
as proof of that route. Snapshot hashing is identity, not source authenticity.

## Observed verification

| Check | Actual result |
| --- | --- |
| New pure routing tests | 75 passed |
| New service routing/immutable API audit tests | 18 passed |
| All strategy tests plus qualification models | 281 passed |
| Selected Ruff E/F/I/UP/B/SIM/RUF rules on eight changed Python files | Passed |
| First Linux full regression before final DTO repair | 1,367 passed, two warnings, 35.12 seconds |
| Final rebuilt Linux image, complete unit and integration suite | 1,370 passed, two warnings, 35.05 seconds |
| Empty test DB upgrade; Alembic heads/current | Successful; both 0016 (head) |
| Alembic drift check | No new upgrade operations detected |

The final command ran `python -m scripts.hermetic_pytest` without selecting or
excluding test files, with cache disabled and an isolated temporary directory.
All collected tests ran, including the Windows-blocked symlink cases on Linux.
No test was skipped or weakened. The two warnings are the same Starlette/httpx
and AnyIO alias deprecations recorded at the foundation checkpoint.

Application and test code in the final image match this checkpoint's source;
this narrative and manifest are finalized after observing the run. The feature
push triggers GitHub's complete Docker regression; consult that run for this
commit's CI result, not the earlier foundation CI.

## Isolated execution and cleanup

Final image: `ctcc-entry-check:20260911-regime`, ID
`sha256:87d1db608241057bada8b1c9a3195b12739687a108b0037c88532d91eb7f0786`.
Local configuration is preserved under `reports/entry-acceptance-20260911/`:
`compose.test.yml` plus `compose.regime-test.yml`. The separately named project
`ctcc-qualification-check-20260911` used internal-only networking, temporary
databases, no host ports/mounts or exchange credentials, all execution flags
false, and a read-only capability-dropped pytest runner. The application server
and trading automation were not started by this runner.

After verification, exactly the two test database containers and internal
network were removed. Temporary synthetic data was discarded; tests can
recreate it. Source, backups, the test image and configuration remain available.
Existing Demo containers and persistent volumes were not removed or restarted.

Read-only post-test observation: deployment remains `d3f206a`, API container
`74b1f3a70c27` remains healthy. Earlier verified Live=false and existing Demo=true
flags were not changed. No order was submitted by this work; this says nothing
about independent activity of the old Demo deployment.

## Explicitly unfinished

Full freshness/data-conflict checks, actual event extraction and timing, entry
location, all-bracket structural SL/TP, economics/portfolio integration,
same-report charts, twelve actual gates, post-render quote recheck, all-submit
Demo guard, durable Notion outbox and realized forensics remain in the ordered
plan. No shadow or Demo samples were collected by this work. Tests for future
modules are not claimed implemented or passed. Offline regression is not an
entry permission, an observed trading benefit or proof of profitability.
