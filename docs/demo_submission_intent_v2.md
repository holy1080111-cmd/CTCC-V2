# Optional exact FOK intent journal

`QualificationLedgerRepository.consume_with_submission_intent` accepts an
optional typed `SubmissionExecutionBinding`. A valid binding creates
`ctcc-demo-submit-intent-v2` inside the existing DB0017 consumed transition and
performs the existing independent-session readback. No new table or ledger is
created. Invalid bindings roll back consumption and the journal together.

Without the binding, the API retains its existing v1 audit record. Historical
v1 records replay as v1; neither a version change nor a recomputed hash can add
the missing binding. No migration, backfill, or restart path upgrades a record
to submission authority.

The binding contains bounded immutable JSON documents. Every build and readback
reconstructs their typed contracts and:

- Replays all account packet pages and their pinned plan, exact UID, environment,
  currency, publication barrier, chronology and configuration consistency.
- Derives net or hedged position side from the actual parsed configuration. No
  account mode is inferred. Isolated leverage, contract values, lot sizing and
  price tick metadata must match the reserved request.
- Replays the recorded G1–G12 origin and every recorded R7 computational check
  from original/current market data, quote and original inputs. The replay must
  use the same reserved quote, original candidate and risk inputs, and the
  account revision must still match the reservation input revision.
- Recomputes both unchanged-candidate and executable-sample economics at the
  consumption time. Both must pass and match the exact reserved operands.
- Uses exactly the reserved executable sample as the FOK limit: ask for a long,
  bid for a short. Original entry, SL, TP, size and leverage remain fixed. All
  prices must already align to the pinned tick; there is no rounding or adverse
  widening. Expiry milliseconds are truncated from the original deadline, so
  the exchange expiry cannot extend it.

The journal binds the exact POST path/body and permitted non-secret request
headers, client and protection IDs, isolated margin, actual position side, mark
triggers, account/environment, original/evidence/recheck/economics and binding
digests. The original requested leverage remains bound in the encompassing
intent, and its account readback must match; leverage is not invented as an OKX
order-body field.

The original replay-input decoder accepts the legacy policy or explicitly marked
history V2/V3 policies and their matching prefix versions. History V1, missing
markers, unknown versions and mixed families reject before defaults can create a
new contract. New-family source, G12 and R7 objects remain exact typed records;
the same original/current source replay is required before constructing an FOK
body. This adds no field to historical intent JSON. Independent old-module
comparison preserves exact legacy long/short v1/v2 canonical bytes.

New long/short expansion and reversal tests exercise native G12 publication,
source-derived recheck, sampled-risk reservation and intent replay. Changing
original Entry/SL/TP or relabelling the policy and recomputing hashes still fails.
These source/account fixtures are synthetic. Actual PostgreSQL new-family tests
and final exact-source regression have their own recorded results; adding those
tests does not itself establish their acceptance.

The 2026-09-19 working-source run executed all eight new-family PostgreSQL cases
in a newly created isolated database: both directions of V2/V3 each passed
concurrent single-consumer commit/restart readback and malformed-marker atomic
rollback. Its migration and schema check also passed. The 44 new unit cases and
293 existing intent/ledger cases passed separately. These results precede the
next immutable checkpoint and cannot replace its full regression.

This remains `durable_intent_not_execution_permission`. Source authenticity,
complete account history, same-invocation publication ownership and all possible
fill-price/intrabar coverage are not established by these stored inputs. A FOK
limit bounds adverse price; favorable fills can still require protection and
geometry checks. The v2 record keeps those claims false and the real Demo entry
transport remains closed. Readback of a consumed, uncertain or expired intent
is audit only and must never dispatch or retry an order.

Unit tests use fictional account bytes and real deterministic evaluators.
PostgreSQL integration tests use isolated synthetic account scopes to verify
atomic journal/consumption, restart readback and failed-binding rollback. These
tests are engineering evidence, not observed Demo or Live acceptance.
