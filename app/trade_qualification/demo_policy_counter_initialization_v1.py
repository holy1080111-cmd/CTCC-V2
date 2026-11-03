"""Data-only one-time prospective policy-counter acknowledgement values.

No original/lifetime streak seed is asserted, no loss/HWM history is reset,
no Arm is requested. Native source origin and full separate-session row
readback must be established outside this pure module before any use.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, DecimalException

from app.trade_qualification import (
    account_original_balance_measurement_v1 as measurement,
)
from app.trade_qualification.account_ledger_claims_v2 import (
    finite_risk_policy_sha256_v1,
)
from app.trade_qualification.demo_control import (
    ControlScope,
    DemoControlError,
    canonical,
    checked,
    digest,
    sha,
    utc,
)
from app.trade_qualification.demo_control_clear_v2 import _integer, _scope
from app.trade_qualification.portfolio import PortfolioRiskPolicy

COUNTER_SCHEMA_V1 = "ctcc.demo_policy_counter_init.v1"
COUNTER_OPERATOR_SCHEMA_V1 = "ctcc.demo_policy_counter_operator.v1"
COUNTER_CONFIRMATION_V1 = "CTCC DEMO INITIALIZE POLICY COUNTER ONCE"
COUNTER_PROOF_NAMES_V1 = (
    "current_identity",
    "flat_positions",
    "ordinary_pending",
    "algo_pending",
    "local_reconciliation",
    "operator_ack",
    "configured_policy",
    "measured_genesis",
)
MAX_POLICY_COUNTER_ACK_BYTES_V1 = 32768
MAX_CONFIGURED_POLICY_BYTES_V1 = 32768


def _forbidden_constant(_token):
    raise DemoControlError("policy_counter_json_nonfinite")


def _plain_canonical(raw, bound):
    if type(raw) is not str or not 1 <= len(raw) <= bound:
        raise DemoControlError("policy_counter_json_bound")
    try:
        value = json.loads(raw, parse_constant=_forbidden_constant)
        if type(value) is not dict or canonical(value) != raw:
            raise DemoControlError("policy_counter_json_noncanonical")
        return value
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise DemoControlError("policy_counter_json_invalid") from None


def _policy_and_genesis(
    configured_policy_json, measured_genesis_json, *, scope, settlement_currency
):
    """Check the exact original DATA shapes; source membership is still native."""
    p = _plain_canonical(configured_policy_json, MAX_CONFIGURED_POLICY_BYTES_V1)
    if set(p) != {
        "schema_version",
        "portfolio_risk_policy_sha256",
        "policy",
        "position_margin_bucket_usdt",
        "configured_risk_inputs",
        "selection",
        "new_authority",
    } or (
        p["schema_version"] != "ctcc.configured_owned_risk_policy.v1"
        or p["selection"]
        != "intersection_of_existing_generic_Demo_limits_and_fixed_hard_caps"
        or p["new_authority"] is not False
        or type(p["position_margin_bucket_usdt"]) is not str
        or type(p["configured_risk_inputs"]) is not dict
    ):
        raise DemoControlError("policy_counter_configured_policy_invalid")
    try:
        policy = PortfolioRiskPolicy.model_validate_json(
            canonical(p["policy"]), strict=True
        )
        if canonical(policy.model_dump(mode="json")) != canonical(p["policy"]):
            raise DemoControlError("policy_counter_configured_policy_invalid")
        bucket = Decimal(p["position_margin_bucket_usdt"])
        if not bucket.is_finite() or not Decimal(0) < bucket <= Decimal(300):
            raise DemoControlError("policy_counter_configured_policy_invalid")
        if finite_risk_policy_sha256_v1(policy) != p["portfolio_risk_policy_sha256"]:
            raise DemoControlError("policy_counter_configured_policy_invalid")
        if type(measured_genesis_json) is not str:
            raise DemoControlError("policy_counter_genesis_invalid")
        original = measured_genesis_json.encode("ascii")
        genesis = measurement._read(original)
        if (
            canonical(genesis) != measured_genesis_json
            or (
                genesis["environment"],
                genesis["account_id"],
                genesis["settlement_currency"],
            )
            != (scope.environment, scope.account_id, settlement_currency)
            or datetime.fromisoformat(genesis["body_completed_at"])
            != policy.drawdown_window_started_at
        ):
            raise DemoControlError("policy_counter_genesis_invalid")
        return policy, genesis
    except DemoControlError:
        raise
    except (
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        RecursionError,
        DecimalException,
    ):
        raise DemoControlError("policy_counter_policy_or_genesis_invalid") from None


def counter_operator_document_v1(ack):
    return {
        "schema_version": COUNTER_OPERATOR_SCHEMA_V1,
        "purpose": "policy_counter_initialize",
        "scope": _scope(ack.scope, ack.settlement_currency),
        "command_id": ack.command_id,
        "control_revision": ack.control_revision,
        "control_event_sha256": ack.control_event_sha256,
        "source_lineage_sha256": ack.source_lineage_sha256,
        "configured_policy_sha256": digest(ack.configured_policy_json),
        "measured_genesis_sha256": digest(ack.measured_genesis_json),
        "original_expires_at": ack.original_expires_at.isoformat(),
        "confirmation": COUNTER_CONFIRMATION_V1,
    }


@dataclass(frozen=True, slots=True, repr=False)
class PolicyCounterInitializationAckV1:
    """Claimed source/operator bytes only; constructor grants no DB permission."""

    scope: ControlScope
    settlement_currency: str
    command_id: str
    control_revision: int
    control_event_sha256: str
    control_state_sha256: str
    source_lineage_sha256: str
    account_revision: int
    ledger_revision: int
    claims_sha256: str | None
    configured_policy_json: str = field(repr=False)
    measured_genesis_json: str = field(repr=False)
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
            raise DemoControlError("policy_counter_scope_invalid")
        for value in (
            self.command_id,
            self.control_event_sha256,
            self.control_state_sha256,
            self.source_lineage_sha256,
        ):
            sha(value)
        _integer(self.control_revision, positive=True)
        _integer(self.account_revision)
        _integer(self.ledger_revision)
        if self.ledger_revision < self.account_revision:
            raise DemoControlError("policy_counter_revision_invalid")
        if self.account_revision == 0:
            if self.claims_sha256 is not None:
                raise DemoControlError("policy_counter_claims_pair_invalid")
        else:
            sha(self.claims_sha256)
        utc(self.owner_sampled_at)
        utc(self.original_expires_at)
        if self.owner_sampled_at >= self.original_expires_at:
            raise DemoControlError("policy_counter_expiry_invalid")
        _integer(self.db_role_oid, positive=True)
        _integer(self.db_backend_pid, positive=True)
        # The same exact unsigned xid8 text rule as the clear acknowledgement.
        if (
            type(self.db_transaction_id) is not str
            or not self.db_transaction_id.isascii()
            or (
                not self.db_transaction_id.isdecimal()
                or self.db_transaction_id.startswith("0")
                or len(self.db_transaction_id) > 20
                or int(self.db_transaction_id) >= 2**64
            )
        ):
            raise DemoControlError("policy_counter_transaction_id_invalid")
        _, genesis = _policy_and_genesis(
            self.configured_policy_json,
            self.measured_genesis_json,
            scope=self.scope,
            settlement_currency=self.settlement_currency,
        )
        if datetime.fromisoformat(genesis["body_completed_at"]) > self.owner_sampled_at:
            raise DemoControlError("policy_counter_genesis_future")
        if type(self.proofs) is not tuple or len(self.proofs) != len(
            COUNTER_PROOF_NAMES_V1
        ):
            raise DemoControlError("policy_counter_proof_membership_invalid")
        for index, pair in enumerate(self.proofs):
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or pair[0] != COUNTER_PROOF_NAMES_V1[index]
            ):
                raise DemoControlError("policy_counter_proof_membership_invalid")
            sha(pair[1])
        operator = canonical(counter_operator_document_v1(self))
        if (
            type(self.operator_ack_json) is not str
            or self.operator_ack_json != operator
        ):
            raise DemoControlError("policy_counter_operator_document_invalid")
        proofs = dict(self.proofs)
        if (
            proofs["operator_ack"] != digest(operator)
            or proofs["configured_policy"] != digest(self.configured_policy_json)
            or proofs["measured_genesis"] != digest(self.measured_genesis_json)
        ):
            raise DemoControlError("policy_counter_proof_bytes_changed")
        if (
            len(canonical(policy_counter_ack_document_v1(self)))
            > MAX_POLICY_COUNTER_ACK_BYTES_V1
        ):
            raise DemoControlError("policy_counter_ack_bound")

    @property
    def initialization_sha256(self):
        self.__post_init__()
        return digest(canonical(policy_counter_ack_document_v1(self)))


def policy_counter_ack_document_v1(ack):
    if type(ack) is not PolicyCounterInitializationAckV1:
        raise DemoControlError("policy_counter_exact_ack_required")
    return {
        "schema_version": COUNTER_SCHEMA_V1,
        "purpose": "policy_counter_initialize",
        "scope": _scope(ack.scope, ack.settlement_currency),
        "command_id": ack.command_id,
        "control_revision": ack.control_revision,
        "control_event_sha256": ack.control_event_sha256,
        "control_state_sha256": ack.control_state_sha256,
        "source_lineage_sha256": ack.source_lineage_sha256,
        "account_revision": ack.account_revision,
        "ledger_revision": ack.ledger_revision,
        "claims_sha256": ack.claims_sha256,
        "configured_policy_sha256": digest(ack.configured_policy_json),
        "measured_genesis_sha256": digest(ack.measured_genesis_json),
        "owner_sampled_at": ack.owner_sampled_at.isoformat(),
        "original_expires_at": ack.original_expires_at.isoformat(),
        "db_role_oid": ack.db_role_oid,
        "db_backend_pid": ack.db_backend_pid,
        "db_transaction_id": ack.db_transaction_id,
        "proofs": dict(ack.proofs),
        "operator_ack": json.loads(ack.operator_ack_json),
    }


def decode_policy_counter_ack_v1(raw, *, configured_policy_json, measured_genesis_json):
    value = _plain_canonical(raw, MAX_POLICY_COUNTER_ACK_BYTES_V1)
    try:
        scope = value["scope"]
        if (
            type(scope) is not list
            or len(scope) != 3
            or type(value["proofs"]) is not dict
        ):
            raise DemoControlError("policy_counter_ack_invalid")
        if set(value["proofs"]) != set(COUNTER_PROOF_NAMES_V1):
            raise DemoControlError("policy_counter_proof_membership_invalid")
        ack = PolicyCounterInitializationAckV1(
            scope=ControlScope(scope[0], scope[1]),
            settlement_currency=scope[2],
            command_id=value["command_id"],
            control_revision=value["control_revision"],
            control_event_sha256=value["control_event_sha256"],
            control_state_sha256=value["control_state_sha256"],
            source_lineage_sha256=value["source_lineage_sha256"],
            account_revision=value["account_revision"],
            ledger_revision=value["ledger_revision"],
            claims_sha256=value["claims_sha256"],
            configured_policy_json=configured_policy_json,
            measured_genesis_json=measured_genesis_json,
            owner_sampled_at=datetime.fromisoformat(value["owner_sampled_at"]),
            original_expires_at=datetime.fromisoformat(value["original_expires_at"]),
            db_role_oid=value["db_role_oid"],
            db_backend_pid=value["db_backend_pid"],
            db_transaction_id=value["db_transaction_id"],
            proofs=tuple(
                (name, value["proofs"][name]) for name in COUNTER_PROOF_NAMES_V1
            ),
            operator_ack_json=canonical(value["operator_ack"]),
        )
        if canonical(policy_counter_ack_document_v1(ack)) != raw:
            raise DemoControlError("policy_counter_ack_noncanonical")
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
        raise DemoControlError("policy_counter_ack_invalid") from None


@dataclass(frozen=True, slots=True, repr=False)
class PolicyCounterInitializationReadbackV1:
    """Actual-row DATA transport only. Native registry establishes provenance."""

    ack: PolicyCounterInitializationAckV1 = field(repr=False)
    initialized_at: datetime
    db_recorded_at: datetime
    row_json: bytes = field(repr=False)
    write_metadata: tuple = field(repr=False)
    readback_metadata: tuple = field(repr=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)

    def __post_init__(self):
        checked(self.ack, PolicyCounterInitializationAckV1)
        utc(self.initialized_at)
        utc(self.db_recorded_at)
        if (
            not self.ack.owner_sampled_at
            <= self.initialized_at
            == self.db_recorded_at
            < self.ack.original_expires_at
        ):
            raise DemoControlError("policy_counter_actual_init_time_invalid")
        if (
            type(self.row_json) is not bytes
            or not 1 <= len(self.row_json) <= 2 * 1024 * 1024
        ):
            raise DemoControlError("policy_counter_row_bound")
        if (
            type(self.write_metadata) is not tuple
            or type(self.readback_metadata) is not tuple
        ):
            raise DemoControlError("policy_counter_metadata_invalid")
