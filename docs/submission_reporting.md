# Durable submission observations and report projection

DB0018 adds an immutable private observation journal, exact report spool and
projection receipts on top of DB0017. It does not enable an exchange POST. The
final qualified dispatch coordinator still must supply its actual bounded
transport capture immediately after its one-shot request, before fill/protection
polling. The legacy automation caller does not satisfy this lineage and is not
connected to the new producer.

The caller uses `QualificationLedgerRepository.record_submission_observation`
with the existing scope/event, expected ledger revision, independently read-back
v2 intent hash, `CapturedSubmission`, queue namespace and exact `OutboxPolicy`.
The repository reloads the original reservation and consumed-intent transition
under the existing account journal transaction, replays the v2 intent, reconstructs
the original candidate, and classifies the captured response. Caller-provided ACK
booleans, replacement candidates or report bytes are not inputs.

The private capture retains exact response bytes and their SHA256, HTTP status,
request start, header receipt, body completion and completion timestamps. It
accepts no authentication headers, credential values, exception text or callback.
Incomplete transport, conflicting IDs/codes, duplicate JSON keys, ambiguous
responses and a request starting after the fixed expiry remain uncertain. An ACK
requires one matching `CTQ` client ID and an unambiguous numeric order ID. ACK
means neither full fill nor active protection; transport authenticity is not
manufactured by this storage layer.

The original G12 `report.json` artifact hash and raw source identity have distinct
fields. Outcome bindings retain the original event, policy, candidate, reservation
request, consumed receipt, v2 intent, exact exchange request, `CTA` protection ID,
fixed entry/SL/TP, contracts, leverage, isolated margin, recheck and account-packet
identities. The existing v1 allowlisted report format and its hashes are unchanged;
the immutable database outcome/spool links that report to the additional v2 pins.

## Atomicity and inhibition of new exposure

The local trade-state observation, outcome and report spool commit together.
Uncertain results additionally move the reservation to its durable `uncertain`
state and append the existing DB0017 transition in that same transaction. Risk
holds and event tombstones remain. A separate session verifies committed bytes
and replays the intent/report linkage before returning a persistence receipt.

Every repository scope lock now first acquires a stable PostgreSQL transaction
advisory lock for **environment + exact account UID**, before taking the existing
settlement row lock. Its signed 64-bit key is derived from SHA256, not Python's
process-specific hash. This serializes concurrent operations in different
settlement scopes belonging to the same account.

Reserve and consume reject an active reservation in another settlement currency:
cross-currency conversion and aggregate caps are unsupported and are not claimed.
Within the selected settlement scope, any uncertain reservation or consumed hold
without a committed initial observation rejects new entry. This includes legacy
consumed records without a DB0018 observation. A lost outcome commit therefore
leaves the earlier durable consumed intent as the inhibit, even if a separate
EStop write cannot complete. Read and explicit verified reconciliation remain
available. Expiry/restart cannot release these holds or replay an order.

An identical retry of the **persistence operation** reads its existing outcome;
different captures, intent pins, policies or queue identities conflict. A failed
capture validation leaves the consumed intent unresolved. A missing observation
is never classified as rejection or zero fill.

## Independent projection

`SubmissionOutboxRuntime` owns a separate thread, event loop and database pool.
It queries bounded pending eligible spools, uses `FOR UPDATE SKIP LOCKED` per spool,
verifies the immutable lineage and exact report/policy bytes, then invokes the
existing native file outbox. Independent native readback precedes an immutable
database projection receipt and its separate-session readback. The process imports
no order client, Arm operation or trading callback, and makes no HTTP requests.

Configure these nonsecret fields only after provisioning a trusted native outbox
directory:

```dotenv
SUBMISSION_OUTBOX_ENABLED=false
SUBMISSION_OUTBOX_ROOT=
SUBMISSION_OUTBOX_QUEUE_NAMESPACE=ctcc-demo-submission-v1
SUBMISSION_OUTBOX_POLL_SECONDS=30
SUBMISSION_OUTBOX_PASS_TIMEOUT_SECONDS=30
SUBMISSION_OUTBOX_BATCH_SIZE=16
```

The namespace is an immutable local routing identity. Give different namespaces
separate roots; it is not a fabricated Notion property binding. To deliver this
queue with the existing Notion worker, configure that worker to read the same
root through its independently verified destination setup. Missing Notion token,
missing schema binding, Notion outage or ambiguous delivery cannot prevent local
DB→file projection and cannot change the saved trade observation. Both workers
remain default off, and neither grants execution authority.

File publication failure leaves the spool pending. In particular, a late envelope
failure may leave a successful durable state journal: it is preserved, and retries
refuse the incomplete pair. Do not delete the journal to make projection succeed.
Archive/hash the entire affected pair while workers are quiesced and use a reviewed
recovery procedure; this component deliberately supplies no destructive repair.
Complete envelopes replay identically after a crash before the DB receipt; their
original enqueue timestamp is retained. A failed reporter does not abort API
startup or roll back exchange state.

## Supported scope and validation

This API records the **initial submit return or unknown result**. The append-only
schema reserves a reconciliation observation kind, but this producer refuses to
turn a caller-supplied initial capture into a later reconciled ACK. A future
source-bound order/fill/protection reconciliation adapter must append that true
evidence; existing explicit verified flat reconciliation can retire holds without
rewriting prior outcomes. Rejected/uncertain reports remain in the private spool
and are ineligible for confirmed-submission delivery.

Migration 0018 preserves 0017's triggers and identities. New tables reject UPDATE
and DELETE, enforce relational binding and unique initial/eligible records, and
bound stored bytes. Downgrade to 0017 is refused while outcomes exist; empty
isolated downgrade/re-upgrade is supported. Operational rollback preserves the
database and keeps entry writes disabled rather than dropping durable observations.

Tests use actual isolated PostgreSQL, synthetic captures and native filesystem
readback. They cover atomic rollback, identical/concurrent initial observations,
unknown/rejected retention, immutable rows, missing/NULL v2 version rejection,
cross-settlement account locks, new-entry inhibition, actual child-process crashes,
late filesystem failures and token-free worker lifecycle. These engineering tests
are not authenticated exchange, Demo, Live, realized-forensics or Notion acceptance.

## Versioned reservation lineage limit

The DB0018 reporter decodes the persisted reservation request by its exact
canonical contract. This permits the legacy unbound V2 request to be replayed
without misreading its extra replay documents as a V1 request. A V3
control-bound request is explicitly rejected with
`submission_control_bound_lineage_unsupported`. Its distinct consumed
transition reason and outer control-bound intent must be replayed together
with the inner V2 intent before an outcome or outbox spool can be accepted;
replaying only the inner intent would lose the account-control binding.
The current change does not implement that V3 projection or authorize an
exchange POST. New synthetic V2/V3 integration cases require an isolated
PostgreSQL run before they count as validation.
