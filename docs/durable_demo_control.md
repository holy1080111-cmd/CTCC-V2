# Durable Demo account control — DB0019

This is the account control slice of the qualified Demo dispatcher. It records
operator intentions and safety revocation. It grants **no execution authority**,
does not change any transport DENY, and has no Live Arm, order client, legacy
singleton, credential value, or exchange side effect. The owner is not wired into
the legacy automation routes; those routes remain contained by their existing
transport denial.

`demo_account_controls` is keyed by exact environment `demo` and numeric account
UID, independent of settlement currency. It stores a monotonically increasing
control revision, owner epoch, owner SHA256 binding, UTC lease, Arm request ID and
expiry, EStop latch, and a canonical state document with config, policy and
credential-session SHA256 pins. A pin records the asserted identity; a hash is
not proof of approved policy, credential authenticity or complete reconciliation.

`DemoControlOwner` creates a fresh random 32-byte owner token in process memory.
Only its SHA256 is stored or returned. `acquire` and owner mutations require the
raw token and derive that binding before database access; reading a database row
does not supply the token. There is no restart/constructor input that restores a
token. Epochs are audit data, not bearer permits. Python process compromise and a
malicious database administrator are outside the trusted repository boundary.

Every repository operation first obtains the same signed 64-bit PostgreSQL
transaction advisory lock as DB0017, derived from SHA256 of
`ctcc-qualification-account-v1\0<environment>\0<exact UID>`, before the control row
lock. No alternate settlement-specific control lock exists. A new owner may
acquire only after the previous lease expires or is released; takeover increments
the epoch and always clears Arm. The maximum lease and Arm intention duration are
30 seconds. Renewal never extends an existing Arm expiry. Policy/session rebind
clears Arm. UTC and monotonic regression deny; local monotonic Arm expiry still
applies when UTC stops advancing. These checks do not attest host-clock accuracy.

An EStop before the first owner creates a **stopped genesis** under the same UID
lock: epoch zero, owner unknown (`null`), pins unknown (`null`), no Arm, expired
lease and EStop true. It does not invent account claims, a zero portfolio, a
credential session or a reconciliation revision. First acquisition advances to
epoch one and preserves the stop. Missing control storage therefore cannot make
an accepted pre-start stop disappear at restart.

`demo_control_journal` is append-only and hash-linked, with one event per control
revision and unique per-account command ID. Database triggers reject journal
update/delete/truncate, control deletion, identity/epoch regression, stop clearing,
incorrect event/state binding and a control commit without its matching journal.
The state mutation and event commit together. The repository closes that session
and verifies exact state/event bytes in another session, with monotonic UTC
observations before commit, after commit and at readback. Failure or uncertain
acknowledgement supplies no accepted result. A populated downgrade is refused.
DB0017, DB0018 and DB0019 acquire transaction-scoped `ACCESS EXCLUSIVE NOWAIT`
table locks before inspecting emptiness and retain them through the drops. This
closes the interval in which a new intent, outcome or EStop could otherwise commit
after the empty check and then be lost. An active reader/writer denies rollback;
the operator must quiesce activity and rerun the normal migration. DB0018 locks
its DB0017 foreign-key parents first so later FK removal cannot wait behind an
application transaction. No populated rollback deletes or releases risk state.

Runtime Disarm, EStop, policy change and shutdown revoke the local intention
synchronously before awaited IO. Each queued owner mutation captures a revocation
generation **before waiting for the mutex**; a later revocation rejects the queued
operation before database writes. Cancellation during database work, loss of
coherence or failed readback poisons that owner for further owner mutations.
Safety tightening remains available on a poisoned owner so an interrupted Arm can
still be durably stopped. No retry or recovered database record creates an order
permit.

EStop and Disarm are internal safety-tightening operations under the UID lock;
they read the current revision instead of dropping an operator stop because a
concurrent Arm advanced a caller's revision. Their durable linearization point is
the transaction commit. Local revocation precedes the await. Remote owners learn
of that change through a subsequent coherent database observation; this module
does not claim instantaneous distributed revocation or a final socket-write
boundary. An unacknowledged database failure is reported as uncertainty, never as
a persisted global stop. The future transport owner must add its explicit
dispatch fencing before any existing DENY can change.

Clear-stop always rejects with
`control_trusted_reconciliation_producer_unavailable`. Caller booleans, pin hashes
and diagnostic portfolio DTOs cannot clear it. A future version must consume
owned, authenticated, complete reconciliation through an explicit procedure.
`observed_arm_intent` is only a local UI observation; `execution_authority` remains
false on the owner, state and repository observation even after Arm is recorded.

The unit suite exercises state validation, cold-stop genesis, clock/lease expiry,
restart, memory-only tokens, before-await revocation, queued Arm cancellation,
commit/readback uncertainty, policy rebind and missing reconciliation. It uses
clearly named fault-injection doubles and is not database acceptance. The real
PostgreSQL integration suite covers concurrent owners and Arm/EStop, UID locking,
restart fencing, lost readback, immutable SQL journal, duplicate-command rollback,
deferred commit consistency, sticky stop and cold stopped genesis. It requires an
explicit isolated database upgraded through DB0019. Its execution and the full
migration/schema/crash cycle remain unverified until that PostgreSQL environment
is available; collecting those tests or rendering offline SQL does not pass them.
The separate durable-migration suite executes the actual DB0017–DB0019 migration
functions in disposable synthetic PostgreSQL schemas. It tests fresh creation,
empty downgrade/re-upgrade, busy-table rejection, writes attempted after the
emptiness guard, and exact durable-record retention after a denied downgrade.
Its isolated-schema cleanup is test disposal, not a deployed rollback procedure.
