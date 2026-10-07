# Durable Demo control binding for reservation and intent

This component binds DB0019 current control to the existing DB0017 reservation
and submission-intent transaction. Every returned receipt and intent still has
`execution_authority=false`. New envelopes additionally record `admission=DENY`,
`order_retry_authority=false`, `owned_policy_verified=false`,
`account_complete=false`, and `source_authenticity_verified=false`.

This is not a running trading pipeline. Owned source/account/config producers,
the D0 admission connection, and the native first-order-byte fence remain
unavailable. No new exchange route, runtime Arm, recovery retry, or migration is
introduced. The existing D0 `require_ready` denial is unchanged.

## Repository interface

`reserve_control_bound(request, control_expectation=...)` retains all existing
original/current replay, account revision, expiry, event uniqueness, applicable
cap, and worst sampled risk checks. It additionally reads and locks current
durable control and binds that exact control revision and event to the reserved
transition. The ordinary receipt wire format is unchanged.

The bound route also repeats an all-state environment/exact-UID/event query
inside that same transaction, across settlement currencies. Any existing event,
including a reconciled-flat tombstone, denies the new hold. The separate bounded
diagnostic read and its limitations are described in
[ledger event observation](ledger_event_observation.md). Seven selected cases from the PostgreSQL control-bound integration module passed after thermal recovery; the exact scope and limits are recorded in the final validation log below. This updates only those selected cases; the full module and production handoff remain unaccepted.

`consume_with_control_bound_submission_intent(scope, event_key,
expected_revision=..., control_expectation=..., execution_binding=...)` requires
a reservation created by that new path. It repeats current control verification,
the existing account/risk/expiry checks, and exact original/current execution
replay. Consumption plus the new envelope commit in one transaction, followed by
separate-session readback and replay. An explicit v2 execution binding is
mandatory; an absent/invalid binding cannot fall back to a v1 intent.

The fully replayed v2 account packet's `plan.session_binding_id` is hashed as
SHA256 of its exact ASCII bytes, using the existing B1 journal convention. It
must equal the bound control's `credential_session_sha256`. Range V5
`ReservationRequestV3` carries the bounded replay packet before reservation; the
route also checks its exact UID and settlement currency before creating a hold.
Historical `ReservationRequestV2` payloads remain readable but have no
pre-reserve packet and are rejected by the control-bound route with a stable
missing-binding code. Legacy requests make no claim that account-session
equality has already been verified at reserve. This comparison does not turn an
unauthenticated packet, journal, or identifier into authority.

`read_control_bound_submission_intent(..., expected_sha256=...)` is historical
evidence readback, including after stop, expiry, restart, or a later uncertain
transition. It verifies the exact reserved journal and referenced immutable
DB0019 control event. It never restores Arm or grants retry/dispatch authority.

`ControlExpectation` is a detached comparison input, not a permit. Its exact
environment/UID, in-memory owner token, owner epoch, control revision, event hash,
config hash, policy hash, and credential-session hash must match the durable
control row. The token is omitted from representations and canonical evidence;
pickling the expectation is rejected. Only its existing one-way owner binding is
durable. No credential values enter this interface.

## Transaction and control ordering

The lock order for both new paths is:

1. Existing environment/exact-UID PostgreSQL transaction advisory lock.
2. DB0019 current control row, with current row/journal/hash validation.
3. Existing qualification account-scope row and reservation transition.

`DemoControlRepository.read_in_transaction` requires the caller's active
transaction. It does not open a nested session. Reacquiring the same advisory
lock through the existing ledger helper is reentrant in that transaction.
Different settlement-currency scopes still share the same UID lock.

Before reserve/consume and again after the final journal flush, current Arm must
remain requested, EStop must be false, clock ordering and both the owner lease
and Arm expiry must be valid, and the original candidate must be unexpired.
Control updates through the existing repository use the same UID lock. A stop
committed before this transaction is observed and denies it. A stop that waits
behind this transaction serializes afterwards and remains durable. Neither order
produces submission authority. This is database transition serialization, not a
claim that a database read makes external network submission atomic.

The consume expectation must match both current control and the exact control
that was bound at reservation. Renew, rebind, owner takeover, config/session
change, disarm, or EStop cannot silently update an existing reservation's binding.
Failed consumption leaves the original durable hold and journal in place.
Explicit reconciliation remains the existing resolution path.

## Versioned evidence and compatibility

The reserved transition uses reason `risk_reserved_control_bound_v1` with
`ctcc-control-bound-reservation-v1` evidence. The consumed transition uses reason
`consumed_with_control_bound_intent_v1` with
`ctcc-control-bound-submit-intent-v1` evidence. The latter embeds the exact
canonical existing v2 submission intent string and digest, without changing any
old v1/v2 intent or reservation bytes. Canonical typed equality rejects
rehashed changes such as Boolean/integer substitutions.

Legacy consume APIs reject a control-bound reservation. The new consume API
rejects an unbound legacy reservation; it does not upgrade old evidence after the
fact. Old intent readers cannot interpret the new envelope as old authority.
Existing DB0017 transition text fields and DB0019 rows/journals store the new
binding. Additive migration 0025 permits the exact control-bound V3 transition
pair in DB0018's immutable reporting journal, while preserving the old V2
predicate. Its reporter verifies the outer and inner intent digests, exact
reserved evidence and historical DB0019 control event before recording a
post-submit observation. This remains reporting, never a submission permit.

An uncertain commit/readback does not cause a second consume or order retry.
Committed intent and reserved risk remain durable, can be marked uncertain using
the existing transition, and remain independently inspectable after restart.

## Scope of risk and execution claims

The policy hash match proves identity only. There is no owned config mapper in
this component. It therefore does not claim that a caller-supplied policy has
inherited the configured structural ceiling 0.5%, portfolio stop-risk ceiling
1%, margin bucket 300 USDT, aggregate margin ceiling 60%, or leverage cap tiers
3/5/8/10/20. A future owned mapper must pin those values before admission can be
considered. Existing legacy aggregate-margin enforcement is a separate path.

The existing `all_fill_prices_covered=false` statement remains true. The accepted
execution design is controlled FOK with an adverse price boundary, original
candidate and executable-reference economics, worst sampled risk reservation,
and exact actual fill/RR/quantity/leverage/margin verification with mismatch
EStop. This component introduces no extra whole-price-interval proof condition.
No actual fill, protection, reconciliation, or Demo/Live acceptance is claimed.

Synthetic unit and real isolated PostgreSQL component tests cover exact inner
bytes, original/current Range V5 replay, private-token exclusion, matching all
control pins, races on one event/UID, a stop on either side of the transaction,
Arm expiry after flush, renew/session changes, legacy-route denial, lost commit
readback, and retained uncertain intent. Source pins and results belong to the
external immutable validation bundle; full final-source regression remains a
separate acceptance requirement.
