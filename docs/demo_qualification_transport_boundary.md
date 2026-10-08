# Demo entry transport containment

The final Demo REST transport currently refuses new entry orders with
`demo_qualification_authority_unavailable`. This is a temporary architectural
block until the trusted qualification runtime can issue and consume an actual
submission authority. It is not Demo readiness or a completed qualification
authority implementation, and does not justify marking
`ALL_SUBMIT_ROUTES_GUARDED=PASS` in the final acceptance matrix.

Manual API orders and legacy automation market, limit, FOK, run-once and scheduled
entries all reach `OkxDemoPrivateRestClient._request`. Its synchronous boundary
runs before credentials/payload processing and again immediately before the HTTP
request. Direct transport calls, batch/algo/amend orders and unclassified writes
are denied too. `write=False`, `passed=True`, historical G12 receipts, journaled
intents and caller-supplied reduction flags cannot enable entry. There is no
configuration bypass or permit constructor.

Account/order/protection GET reads remain available. Demo cancel-order,
close-position and Cancel All After pass the entry classifier only to reach a
separate final-dispatch denial, `demo_maintenance_authority_unavailable`, after
signing and immediately before HTTP. Direct transport and service calls cannot
send these maintenance writes until a non-caller-forgeable, account-scoped,
one-use permit is implemented and checked there. Set-leverage and order
precheck remain denied as entry-related writes. This containment does not
cancel or alter existing exchange protection; any existing Demo exposure needs
operator handling outside this disabled transport until reviewed maintenance
authority exists. All non-GET requests are configured as single-attempt, even
when a direct caller supplies `write=False`.

The boundary does not enable or validate Live execution. Existing synthetic
service tests that inject a fake private client exercise their stated local
logic; they cannot prove the real Demo transport can submit an order.

Migration 0034 hardens the legacy `SafeDemoAutomation` control singleton with a
database revision, an Arm/EStop exclusion constraint and a sticky restart latch.
Every full-state write uses compare-and-swap. An explicit EStop locks the latest
row and changes only control fields, followed by a separate-session readback;
an unconfirmed write is not reported as success. After any prior Arm or EStop,
restart relatches EStop even if a clear-stop commit lost its acknowledgement.
The migration also rejects pre-0034 UPDATE writers at the database trigger:
they cannot advance the required revision. Cancellation after a legacy submit
now latches local EStop before attempting to retain uncertain exposure. That
post-submit state journal is not a pre-submit durable intent.
Cancellation of an Arm state write may arrive after its database commit; the
current process revokes Arm, latches local EStop and reports recovery
incomplete until it rereads durable control. A synthetic commit-then-cancel
regression covers this ambiguous acknowledgement.
This is defense in depth while the transport above remains closed. A legacy
automation worker now makes a fresh database read of the exact Arm, EStop,
lock, restart-latch and control revision after the service's last preflight
await. A changed row, missing row or failed read revokes its local Arm and
prevents the submit callback from reaching the private client. That read does
not mint permission or make the subsequent exchange POST atomic with an EStop;
the transport remains hard DENY. A future permitted dispatch requires a
database-backed, one-use final authority check at the transport boundary.
The singleton CAS also cannot retroactively journal an exchange fill that
arrives before the legacy worker persists its active-trade state. If another
process engages EStop during that I/O, the later state write can conflict and
recovery must treat exchange exposure as uncertain. Every future submit route
therefore still needs a durable reservation and intent before the POST, plus a
shared final authority check at dispatch; this legacy path remains hard DENY.
Downgrading migration 0034 removes the database revision trigger and sticky
control latch, so it is permitted only by a controlled rollback procedure with
the transport still hard DENY and no unresolved exchange exposure; an ordinary
online downgrade must never be treated as a safe trading state.

## Required integration before any authority can exist

1. `account_collector.py`, `account_capture.py`, `account_consistency.py` and
   `account_materializer.py`: bind the controlled credential session, exact UID,
   complete page chains and account revision to authenticated local guard,
   reservation, fill/funding/fee/outcome, seed and high-water history. Current
   `AccountMaterializationResult.account_complete` and source-authenticity claims
   intentionally remain false; a mapping with caller-supplied inputs is not an
   authenticated complete account.
2. `one_shot.py` and `recheck.py`: mount one controlled G12 publication/barrier,
   new public and account collection, fixed-candidate continuation and current
   risk invocation. Its present `OneShotCaptureResult` is diagnostic evidence,
   with execution authority false. Complete executable price-interval coverage
   and source-derived strategy/history policy are still required.
3. `reservations.py` and `database/repositories/qualification_ledger.py`: use the
   existing account-scoped lock/revision/expiry/arm/EStop validation to reserve
   the original event and risk. The optional [v2 intent binding](demo_submission_intent_v2.md)
   now records a replayed fixed FOK request atomically in that existing journal.
   Its replayable synthetic-contract support still needs authenticated runtime
   inputs and a controlled same-invocation owner; it does not issue permission.
4. `submission_intent.py`: its `SubmissionIntentRecord` currently declares
   `durable_intent_not_execution_permission`; the independent
   `consume_with_submission_intent` readback must not become a restart-dispatch
   permit. A future issuer must additionally establish fresh current scope and
   same-invocation ownership after durable readback and consume one-shot
   authority at this transport boundary. Final guards must be checked after the
   last await. Commit uncertainty or transport uncertainty must leave the event
   and reservation retained for reconciliation, with no retry.
5. Wire fill/price/size/protection readback, durable reconciliation and reporting
   outbox to that exact submission lineage. Only actual source-backed Demo
   acceptance can remove this architectural block after the required tests.

No exchange calls are used by the transport containment tests. Their HTTP
transport and data are synthetic and their result is engineering containment
evidence only.
