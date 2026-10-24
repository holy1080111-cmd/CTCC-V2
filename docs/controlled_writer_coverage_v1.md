# Prospective controlled Demo writer coverage V1

This component remains an unaccepted isolated final-completion draft. The real
PG17.11 admin catalog from source `585bd55` was retained and independently
reviewed: 27 columns, 20 ordinary constraints plus two constraint triggers,
seven indexes, six functions and eight triggers. Its ordered constraint/index
projection is now explicitly sealed as
`59c771896d4b7ea6b5e77f420fd0d242fdd13429a7ad0f0118c905cdd80e55e6`.
CI `40072095772` on exact `baaffa12` then saved 39 correctly typed integer OIDs
and passed the formal schema gate. Its next host-side validation failed with
`ModuleNotFoundError`; a separate isolated local reproduction identified the
repository's SQLAlchemy import. These observations do not establish complete
CI, runtime-role, migration/restart or concurrency acceptance.

The catalog declarations and comparator now have one standard-library-only
owner, `app/domain/controlled_writer_coverage_catalog_v1.py`. The database
repository reexports the identical objects and retains all native role,
transaction and readback checks. The adapter imports database readers only
when actual I/O is awaited; host verification uses the same pure policy.
The new policy file is also held in the issuer's source inventory, so moving
the implementation cannot escape package custody. DDL, SQL function bodies,
catalog query projections and the sealed comparison remain unchanged. Fresh
source-bound regression is required; see [validation state](final_completion_validation.md).

The narrow contract permits one process-owned fact that an explicitly
identified controlled CTCC Demo deployment observed its native current-query
empty inventory and clean local state while establishing a prospective durable
writer-coverage epoch. It supplies no full portfolio, owned account revision,
historical event completeness, per-event G6, reservation, submission intent,
flat-start permission or exchange write authority. Every existing non-GET
boundary stays DENY. Live, Arm and outbox defaults are unchanged.

## Source and control flow

The fixed acquire function takes the existing native V4 account inputs, not
caller receipts, hashes, clocks, policy, callbacks or `passed` flags. It checks
packaged source/manifest, held native identities and unchanged default DENY
before acquisition and again before acquiring Control ownership.

The sequence is:

1. Real V4 acquisition and DB0046 exact-byte custody readback.
2. Exact-key MOVE of its live owner, synchronously removing the V4 registry
   entry; replay the same original raw bundle and native proof.
3. Verify original unfiltered V7 current query chains and zero rows across
   positions, ordinary pending and all eight supported algo families. This
   observation remains distinct from account-wide completeness.
4. Read actual Control. Existing Arm or an unexpired conflicting lease blocks
   before mutation. Existing `acquire` atomically rechecks conflict under the
   same UID lock and retains any existing EStop.
5. After independent Control readback, call existing owner/epoch/revision/lease
   guarded `change(action='estop')`, then independently read back. No unowned
   `latch_stop`, clear-stop, renewal, takeover or retry is used. For genuinely
   missing Control, initial ownership is disarmed under the common write DENY;
   coverage requires the subsequent owned EStop and all later checks.
6. Actual same-transaction B5 audit, prepared commit and separate readback;
   repeat unchanged source/control/B5 checks for active commit and separate
   readback; perform final native/source checks before issuing the opaque owner.

The B5 extraction preserves its original all-currency body and bounds. Its
private helper requires an active PostgreSQL transaction and the exact requested
scope row already locked by the caller. It opens no nested session. Revision
zero/null claims are legitimate bootstrap data. No revision1 is fabricated and
no old event-inspection guard, risk policy or reservation/intent engine changes.

Missing or unattributed legacy rows, unknown reconciliation, tracked exposure,
local holds and uncertain/consumed unresolved intents block. They are retained;
there is no deletion, reassignment, empty-default substitution or genesis reset.

## Original deadlines and one-use consumption

Every new native stamp is checked against the original proof `stage.started`,
the existing 60-second/5-millisecond anchor bounds and the previous stamp.
Original current-query 30-second freshness, Control's original 30-second lease
and the separately consumed DB0046 5-second receipt remain distinct. Awaits are
bounded by remaining original limits, never by renewed source timestamps.

`consume_owned_controlled_demo_writer_coverage_v1` is an ordinary function. Its
call removes the owner before returning its fixed private awaitable. Never
awaiting, delayed awaiting, cross-task consumption, cancellation or a failed
readback cannot retain/recreate that owner. The continuation rechecks native
context, exact factory/session/source/files/inventory, actual current durable
epoch, Control and B5. A changed result fails closed.

The native activation cutoff is measured only after actual active commit and
separate readback. The earlier transition timestamp and database `recorded_at`
are not that cutoff. The measured serialized fact is audit data, not a saved
capability. Later G6 requires separately owned original-event chronology after
that bound; this component provides no such positive API.

## Durable scope and recovery

DB0048 stores `demo_controlled_writer_coverage_scopes` and immutable
`demo_controlled_writer_coverage_journal`. Each prepared/active/revoked action
advances exact UID-scoped sequence/hash CAS. Old generations, records and audits
remain. Commit or independent-readback uncertainty produces no owner and no
automatic retry.

Native registry state is lost on restart. A persisted active row cannot restore
coverage or Arm. A new acquisition, legitimate newer Control epoch and complete
local reconciliation are needed. Old incomplete/active epochs remain evidence
and require explicit journal revocation before a new generation.

Revocation after an OS restart preserves the old epoch's exact original anchor,
last eligibility transition stamp and process nonce. Its new revocation time
is separately measured UTC in the state/event, with database custody time;
it does not join two boots' monotonic clocks. Unknown or regressed UTC refuses
and retains the unresolved epoch. Prepare, activate and consume retain all
original live clock checks. A new generation needs its own genuine source and
Control acquisition; these rules cannot restore an old owner or extend expiry.

Function-only privileges apply only to the new coverage tables. The main native
factory retains its existing precise B1/B5/Control privileges, including required
scope and Control row locks. The custody repository uses its independently
audited DB0046 factory/role. No role is made owner/superuser or broadly granted
old privileges to pass acceptance. Root's R5 contract puts actual new-table head
and late table locks inside the five-argument VOLATILE PL/pgSQL SECURITY DEFINER
read function. Its internal discover mode locks UID/Control/head before B5;
append mode takes ROW EXCLUSIVE NOWAIT after B5 and exact CAS, and separate
readback mode takes SHARE NOWAIT with positive exact committed pins. Modes are
selected inside the repository. No SHARE-to-write upgrade or runtime direct
new-table SELECT/DML/LOCK privilege is added.

Administrative migration/schema capture is distinct from native factory
acceptance. Root's R6 contract requires the fixed native role/session/effective
privilege gate in deployment verification and every native transaction. Correct
schema hashes under an owner, superuser or SET ROLE session cannot prepare,
activate, consume or issue a native owner. Diagnostic structural comparison
cannot replace that gate; an unsealed PG17 baseline also cannot pass formal v5.

Public repository `read()` returns a diagnostic observation; safety-only
`revoke_observed()` can append an exact-CAS revocation. Neither certifies a
restricted database role or issues a source owner. Their records cannot remint
an owner. Role acceptance belongs to deployment verification and each private
native transition/readback; an administrative diagnostic cannot substitute for
those checks.

Every new function fixes its own search path to `pg_catalog, public, pg_temp`,
with the temporary schema explicitly last, and critical tables are qualified.
The restricted role cannot write the public schema. Separately, the existing
DB0046 caller-side catalog checks are being hardened to set and verify that
safe session path before reading role/catalog facts; caller connection settings
cannot choose another catalog context. This does not rewrite DB0046 stored
functions or migration history. Actual restricted-role and shadow-context
falsification remains required.

The new catalog check must bind each trigger to the exact OID and namespace of
its independently verified public function. Another schema's same-named
function must fail even when the trigger's other properties match. Empty-only
0048 downgrade must first lock the existing Control parent, then the new scope
and journal tables, all ACCESS EXCLUSIVE NOWAIT, before any read or DDL. The
parent lock covers foreign-key trigger removal. Conflict must retain the head,
catalog and rows through rollback; it cannot become a delayed lock timeout.
These source corrections still require actual isolated PostgreSQL acceptance.

This coverage describes the controlled deployed app domain. It does not exclude
future human/other-key/external exchange writes. Dedicated credential/deployment
custody is an explicit operational assumption; discrepancies in fresh
reconciliation revoke affected facts and preserve unknown state.

## Validation status

The authored unit positive uses explicitly synthetic private I/O providers and
real issuer/Control/wire/registry semantics. Its output is not native-source or
PostgreSQL acceptance. Negative cases cover call-time burn, native drift,
Control races, ambiguous commit, changed B5, late cancellation and saved-row
restart forgery. Bounded draft cohorts actually passed 34 such unit cases and
47 pure migration/catalog cases without skips. A later fixture-only newline
repair passed the selected original-positive and nine current-0048 union cases
without skips, plus scoped Ruff and formatting. Their distinct source identities
and receipts are recorded in [validation state](final_completion_validation.md).
The local full 119-case union cohort timed out and remains incomplete; ten
selected passes do not replace it. Actual PG17/catalog baseline, real
concurrency, hermetic migration/full-source CI and native source evidence remain
required. No engineering, Micro Live or final completion flag is raised by this
document.
