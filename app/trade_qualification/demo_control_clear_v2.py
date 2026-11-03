"""Pure clear-stop values and complete journal replay; no operator/source permit.

The native current55 repository must independently authenticate the actual
operator acknowledgement, original CURRENT lineage and flat reconciliation.
Constructing any value here grants no permission to change durable Control.
Legacy v1 state documents and all legacy replay functions remain unchanged.
"""

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.trade_qualification.demo_control import (
    MAX_CONTROL_EVENT_BYTES,
    MAX_CONTROL_LINEAGE_BYTES,
    MAX_CONTROL_LINEAGE_ROWS,
    ControlJournalSourceRowV1,
    ControlScope,
    ControlState,
    DemoControlError,
    _lineage_transition,
    canonical,
    checked,
    decode,
    digest,
    document,
    sha,
    utc,
)

ACK_SCHEMA_V2 = "ctcc.demo_control_clear_ack.v2"
OPERATOR_SCHEMA_V2 = "ctcc.demo_control_clear_operator.v2"
EVENT_SCHEMA_V2 = "ctcc.demo_control_event.v2"
CLEAR_CONFIRMATION_V2 = "CTCC DEMO CLEAR STOP DISARMED"
MAX_CLEAR_ACK_BYTES_V2 = 32768
CLEAR_PROOF_NAMES_V2 = (
    "current_identity",
    "flat_positions",
    "ordinary_pending",
    "algo_pending",
    "local_reconciliation",
    "operator_ack",
)


def _integer(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value < 2**63:
        raise DemoControlError("control_clear_integer_invalid")
    return value


def _scope(scope, settlement_currency):
    checked(scope, ControlScope)
    if (
        type(settlement_currency) is not str
        or re.fullmatch(r"[A-Z0-9]{1,16}", settlement_currency) is None
    ):
        raise DemoControlError("control_clear_scope_invalid")
    return [scope.environment, scope.account_id, settlement_currency]


def operator_document_v2(ack):
    return {
        "schema_version": OPERATOR_SCHEMA_V2,
        "purpose": "clear_stop",
        "scope": _scope(ack.scope, ack.settlement_currency),
        "command_id": ack.command_id,
        "stopped_control_revision": ack.stopped_control_revision,
        "stopped_event_sha256": ack.stopped_event_sha256,
        "source_lineage_sha256": ack.source_lineage_sha256,
        "original_expires_at": ack.original_expires_at.isoformat(),
        "confirmation": CLEAR_CONFIRMATION_V2,
    }


@dataclass(frozen=True, slots=True, repr=False)
class ClearStopAckV2:
    """Strict finite DATA only, including a claimed operator document.

    Only the native repository may establish that these bytes actually came
    from its one-use operator interaction and original held source capability.
    """

    scope: ControlScope
    settlement_currency: str
    command_id: str
    stopped_control_revision: int
    stopped_event_sha256: str
    stopped_state_sha256: str
    clear_state_sha256: str
    source_lineage_sha256: str
    account_revision: int
    ledger_revision: int
    claims_sha256: str | None
    owner_sha256: str
    owner_epoch: int
    original_lease_until: datetime
    owner_sampled_at: datetime
    original_expires_at: datetime
    db_role_oid: int
    db_backend_pid: int
    db_transaction_id: str
    proofs: tuple[tuple[str, str], ...] = field(repr=False)
    operator_ack_json: str = field(repr=False)
    execution_authority: bool = field(default=False, init=False)

    def __post_init__(self):
        _scope(self.scope, self.settlement_currency)
        if self.settlement_currency != "USDT":
            raise DemoControlError("control_clear_scope_invalid")
        for value in (
            self.command_id,
            self.stopped_event_sha256,
            self.stopped_state_sha256,
            self.clear_state_sha256,
            self.source_lineage_sha256,
            self.owner_sha256,
        ):
            sha(value)
        _integer(self.stopped_control_revision, positive=True)
        if self.stopped_control_revision >= 2**63 - 1:
            raise DemoControlError("control_clear_revision_overflow")
        _integer(self.owner_epoch, positive=True)
        if self.owner_epoch > self.stopped_control_revision:
            raise DemoControlError("control_clear_epoch_invalid")
        _integer(self.account_revision)
        _integer(self.ledger_revision)
        if self.ledger_revision < self.account_revision:
            raise DemoControlError("control_clear_ledger_revision_invalid")
        if self.account_revision == 0:
            if self.claims_sha256 is not None:
                raise DemoControlError("control_clear_claims_pair_invalid")
        else:
            sha(self.claims_sha256)
        for value in (
            self.original_lease_until,
            self.owner_sampled_at,
            self.original_expires_at,
        ):
            utc(value)
        if (
            not self.owner_sampled_at
            < self.original_expires_at
            <= self.original_lease_until
        ):
            raise DemoControlError("control_clear_original_expiry_invalid")
        _integer(self.db_role_oid, positive=True)
        _integer(self.db_backend_pid, positive=True)
        if (
            type(self.db_transaction_id) is not str
            or re.fullmatch(r"[1-9][0-9]{0,19}", self.db_transaction_id) is None
            or int(self.db_transaction_id) >= 2**64
        ):
            raise DemoControlError("control_clear_transaction_id_invalid")
        if type(self.proofs) is not tuple or len(self.proofs) != len(
            CLEAR_PROOF_NAMES_V2
        ):
            raise DemoControlError("control_clear_proof_membership_invalid")
        for index, pair in enumerate(self.proofs):
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or pair[0] != CLEAR_PROOF_NAMES_V2[index]
            ):
                raise DemoControlError("control_clear_proof_membership_invalid")
            sha(pair[1])
        if (
            type(self.operator_ack_json) is not str
            or not 1 <= len(self.operator_ack_json) <= 8192
        ):
            raise DemoControlError("control_clear_operator_document_invalid")
        if self.operator_ack_json != canonical(operator_document_v2(self)):
            raise DemoControlError("control_clear_operator_document_invalid")
        if dict(self.proofs)["operator_ack"] != digest(self.operator_ack_json):
            raise DemoControlError("control_clear_operator_document_invalid")
        if (
            len(canonical(clear_ack_document_v2(self)).encode("ascii"))
            > MAX_CLEAR_ACK_BYTES_V2
        ):
            raise DemoControlError("control_clear_ack_bound")

    @property
    def ack_sha256(self):
        self.__post_init__()
        return digest(canonical(clear_ack_document_v2(self)))


def clear_ack_document_v2(ack):
    if type(ack) is not ClearStopAckV2:
        raise DemoControlError("control_clear_exact_ack_required")
    return {
        "schema_version": ACK_SCHEMA_V2,
        "purpose": "clear_stop",
        "scope": _scope(ack.scope, ack.settlement_currency),
        "command_id": ack.command_id,
        "stopped_control_revision": ack.stopped_control_revision,
        "stopped_event_sha256": ack.stopped_event_sha256,
        "stopped_state_sha256": ack.stopped_state_sha256,
        "clear_state_sha256": ack.clear_state_sha256,
        "source_lineage_sha256": ack.source_lineage_sha256,
        "account_revision": ack.account_revision,
        "ledger_revision": ack.ledger_revision,
        "claims_sha256": ack.claims_sha256,
        "owner_sha256": ack.owner_sha256,
        "owner_epoch": ack.owner_epoch,
        "original_lease_until": ack.original_lease_until.isoformat(),
        "owner_sampled_at": ack.owner_sampled_at.isoformat(),
        "original_expires_at": ack.original_expires_at.isoformat(),
        "db_role_oid": ack.db_role_oid,
        "db_backend_pid": ack.db_backend_pid,
        "db_transaction_id": ack.db_transaction_id,
        "proofs": dict(ack.proofs),
        "operator_ack": json.loads(ack.operator_ack_json),
    }


def decode_clear_ack_v2(raw):
    if type(raw) is not str or not 1 <= len(raw) <= MAX_CLEAR_ACK_BYTES_V2:
        raise DemoControlError("control_clear_ack_bound")
    try:
        value = json.loads(raw)
        if (
            type(value) is not dict
            or type(value["scope"]) is not list
            or len(value["scope"]) != 3
        ):
            raise DemoControlError("control_clear_ack_invalid")
        proofs = value["proofs"]
        if type(proofs) is not dict or set(proofs) != set(CLEAR_PROOF_NAMES_V2):
            raise DemoControlError("control_clear_proof_membership_invalid")
        ack = ClearStopAckV2(
            scope=ControlScope(value["scope"][0], value["scope"][1]),
            settlement_currency=value["scope"][2],
            command_id=value["command_id"],
            stopped_control_revision=value["stopped_control_revision"],
            stopped_event_sha256=value["stopped_event_sha256"],
            stopped_state_sha256=value["stopped_state_sha256"],
            clear_state_sha256=value["clear_state_sha256"],
            source_lineage_sha256=value["source_lineage_sha256"],
            account_revision=value["account_revision"],
            ledger_revision=value["ledger_revision"],
            claims_sha256=value["claims_sha256"],
            owner_sha256=value["owner_sha256"],
            owner_epoch=value["owner_epoch"],
            original_lease_until=datetime.fromisoformat(value["original_lease_until"]),
            owner_sampled_at=datetime.fromisoformat(value["owner_sampled_at"]),
            original_expires_at=datetime.fromisoformat(value["original_expires_at"]),
            db_role_oid=value["db_role_oid"],
            db_backend_pid=value["db_backend_pid"],
            db_transaction_id=value["db_transaction_id"],
            proofs=tuple((name, proofs[name]) for name in CLEAR_PROOF_NAMES_V2),
            operator_ack_json=canonical(value["operator_ack"]),
        )
        if canonical(clear_ack_document_v2(ack)) != raw:
            raise DemoControlError("control_clear_ack_noncanonical")
        return ack
    except DemoControlError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        RecursionError,
    ):
        raise DemoControlError("control_clear_ack_invalid") from None


def advance_clear_stop_v2(
    previous,
    *,
    owner_sha256,
    owner_epoch,
    expected_revision,
    previous_event_sha256,
    command_id,
    now,
    fresh_ack,
):
    checked(previous, ControlState)
    checked(fresh_ack, ClearStopAckV2)
    sha(previous_event_sha256)
    sha(owner_sha256)
    sha(command_id)
    utc(now)
    if type(owner_epoch) is not int or type(expected_revision) is not int:
        raise DemoControlError("control_clear_owner_or_revision_conflict")
    if (
        previous.emergency_stop is not True
        or previous.owner_epoch <= 0
        or previous.arm_request_id is not None
        or previous.arm_expires_at is not None
        or (previous.owner_sha256, previous.owner_epoch, previous.revision)
        != (owner_sha256, owner_epoch, expected_revision)
        or not previous.updated_at <= now < previous.lease_until
    ):
        raise DemoControlError("control_clear_owner_or_revision_conflict")
    target = replace(
        previous,
        revision=previous.revision + 1,
        emergency_stop=False,
        arm_request_id=None,
        arm_expires_at=None,
        updated_at=now,
    )
    if (
        fresh_ack.scope != previous.scope
        or fresh_ack.command_id != command_id
        or fresh_ack.stopped_control_revision != previous.revision
        or fresh_ack.stopped_event_sha256 != previous_event_sha256
        or fresh_ack.stopped_state_sha256 != digest(canonical(document(previous)))
        or fresh_ack.clear_state_sha256 != digest(canonical(document(target)))
        or fresh_ack.owner_sha256 != previous.owner_sha256
        or fresh_ack.owner_epoch != previous.owner_epoch
        or fresh_ack.original_lease_until != previous.lease_until
        or fresh_ack.owner_sampled_at != now
        or not now < fresh_ack.original_expires_at <= previous.lease_until
    ):
        raise DemoControlError("control_clear_ack_binding_invalid")
    return target


def clear_event_document_v2(state, *, command_id, previous_sha256, ack_sha256):
    checked(state, ControlState)
    sha(command_id)
    sha(previous_sha256)
    sha(ack_sha256)
    return {
        "version": EVENT_SCHEMA_V2,
        "command_id": command_id,
        "action": "clear_stop",
        "previous_sha256": previous_sha256,
        "state": document(state),
        "clear_ack_sha256": ack_sha256,
    }


@dataclass(frozen=True, slots=True, repr=False)
class ControlLineageV2:
    scope: ControlScope
    state: ControlState = field(repr=False)
    head_sha256: str
    source_rows: tuple[ControlJournalSourceRowV1, ...] = field(repr=False)
    clear_ack_rows: tuple[ClearStopAckV2, ...] = field(repr=False)
    receipt_json: str = field(repr=False)
    execution_authority: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return digest(self.receipt_json)


def replay_control_journal_lineage_v2(
    rows,
    *,
    scope,
    target_state,
    expected_head_sha256,
    clear_ack_rows,
):
    """Pure complete bounded replay, including exact immutable clear ACK rows.

    No DTO, claimed confirmation, row hash or replay receipt is source authority.
    The current55 native repository must read/authenticate every actual row.
    """
    checked(scope, ControlScope)
    checked(target_state, ControlState)
    sha(expected_head_sha256)
    if (
        type(rows) is not tuple
        or not 1 <= len(rows) <= MAX_CONTROL_LINEAGE_ROWS
        or target_state.scope != scope
        or target_state.revision != len(rows)
        or type(clear_ack_rows) is not tuple
        or len(clear_ack_rows) > len(rows)
    ):
        raise DemoControlError("control_lineage_complete_prefix_required")
    acknowledgements = {}
    total_bytes = 0
    for ack in clear_ack_rows:
        checked(ack, ClearStopAckV2)
        raw = canonical(clear_ack_document_v2(ack))
        total_bytes += len(raw.encode("ascii"))
        if total_bytes > MAX_CONTROL_LINEAGE_BYTES:
            raise DemoControlError("control_lineage_bound_or_duplicate_command")
        if ack.scope != scope or ack.ack_sha256 in acknowledgements:
            raise DemoControlError("control_clear_ack_duplicate_or_scope")
        acknowledgements[ack.ack_sha256] = ack
    previous = None
    previous_sha = None
    commands = set()
    original = []
    used = []
    try:
        for revision, row in enumerate(rows, start=1):
            if (
                type(row) is not ControlJournalSourceRowV1
                or type(row.scope) is not ControlScope
                or type(row.revision) is not int
                or row.revision != revision
                or type(row.action) is not str
                or row.action
                not in {
                    "acquire",
                    "renew",
                    "arm_requested",
                    "disarm",
                    "estop",
                    "rebind",
                    "release",
                    "clear_stop",
                }
                or (
                    row.previous_sha256 is not None
                    and type(row.previous_sha256) is not str
                )
                or type(row.event_json) is not str
            ):
                raise DemoControlError("control_lineage_row_invalid")
            checked(row.scope, ControlScope)
            if row.previous_sha256 is not None:
                sha(row.previous_sha256)
            if row.scope != scope or row.previous_sha256 != previous_sha:
                raise DemoControlError("control_lineage_row_invalid")
            sha(row.command_id)
            sha(row.event_sha256)
            sha(row.state_sha256)
            utc(row.occurred_at)
            raw_bytes = row.event_json.encode("ascii")
            total_bytes += len(raw_bytes)
            if (
                not 1 <= len(raw_bytes) <= MAX_CONTROL_EVENT_BYTES
                or total_bytes > MAX_CONTROL_LINEAGE_BYTES
                or row.command_id in commands
            ):
                raise DemoControlError("control_lineage_bound_or_duplicate_command")
            value = json.loads(row.event_json)
            fields = {"version", "command_id", "action", "previous_sha256", "state"}
            if row.action == "clear_stop":
                fields.add("clear_ack_sha256")
            if type(value) is not dict or set(value) != fields:
                raise DemoControlError("control_lineage_event_invalid")
            state = decode(canonical(value["state"]))
            if state.scope != scope or state.revision != revision:
                raise DemoControlError("control_lineage_state_invalid")
            if row.action == "clear_stop":
                ack_sha = value["clear_ack_sha256"]
                sha(ack_sha)
                if (
                    previous is None
                    or ack_sha not in acknowledgements
                    or ack_sha in used
                ):
                    raise DemoControlError("control_clear_ack_missing_or_reused")
                ack = acknowledgements[ack_sha]
                expected_state = advance_clear_stop_v2(
                    previous,
                    owner_sha256=previous.owner_sha256,
                    owner_epoch=previous.owner_epoch,
                    expected_revision=previous.revision,
                    previous_event_sha256=previous_sha,
                    command_id=row.command_id,
                    now=state.updated_at,
                    fresh_ack=ack,
                )
                expected_event = canonical(
                    clear_event_document_v2(
                        state,
                        command_id=row.command_id,
                        previous_sha256=previous_sha,
                        ack_sha256=ack_sha,
                    )
                )
                used.append(ack_sha)
            else:
                expected_state = _lineage_transition(
                    previous,
                    state,
                    action=row.action,
                    command_id=row.command_id,
                )
                expected_event = canonical(
                    {
                        "version": "ctcc.demo_control_event.v1",
                        "command_id": row.command_id,
                        "action": row.action,
                        "previous_sha256": previous_sha,
                        "state": document(state),
                    }
                )
            if (
                row.event_json != expected_event
                or digest(row.event_json) != row.event_sha256
                or digest(canonical(document(state))) != row.state_sha256
                or row.occurred_at != state.updated_at
                or canonical(document(expected_state)) != canonical(document(state))
            ):
                raise DemoControlError("control_lineage_event_binding_invalid")
            commands.add(row.command_id)
            original.append(row)
            previous, previous_sha = state, row.event_sha256
        if (
            len(used) != len(acknowledgements)
            or previous_sha != expected_head_sha256
            or canonical(document(previous)) != canonical(document(target_state))
        ):
            raise DemoControlError("control_lineage_target_head_invalid")
        receipt = canonical(
            {
                "schema_version": "ctcc.demo_control_complete_lineage.v2",
                "scope": [scope.environment, scope.account_id],
                "first_revision": 1,
                "through_revision": target_state.revision,
                "row_count": len(original),
                "original_event_and_ack_bytes": total_bytes,
                "head_sha256": previous_sha,
                "state_sha256": digest(canonical(document(target_state))),
                "event_sha256_chain": [row.event_sha256 for row in original],
                "clear_ack_sha256_chain": used,
                "native_source_owned": False,
                "execution_authority": False,
            }
        )
        return ControlLineageV2(
            scope, previous, previous_sha, tuple(original), clear_ack_rows, receipt
        )
    except DemoControlError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        raise DemoControlError("control_lineage_source_invalid") from None
