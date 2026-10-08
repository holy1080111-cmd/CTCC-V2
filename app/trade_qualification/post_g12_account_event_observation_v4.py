"""One-invocation V3-to-DB0017 event observation; never an order issuer.

The V3 source join remains unqualified. This bridge only demonstrates that its
fixed original event was inspected in the durable UID-scoped ledger *after* the
new G12/public/account diagnostic completed. An absent row is not a permit.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification.account_capture_journal import digest
from app.trade_qualification.event_observation import LedgerEventObservation
from app.trade_qualification.reservations import (
    LedgerScope,
    checked_bootstrap,
    reservation_id,
)
from app.trade_qualification.timing import event_identity


class PostG12AccountEventObservationError(ValueError):
    """A stable denial code without source, SQL, account or path details."""


_CODES = frozenset(
    {
        "v3_incomplete",
        "v3_binding_invalid",
        "v3_stale",
        "ledger_observation_unavailable",
        "ledger_observation_invalid",
        "original_event_already_recorded",
        "trusted_execution_inputs_missing",
    }
)
_SOURCE_PINS = (
    "v3_receipt_sha256",
    "g12_evidence_sha256",
    "g12_report_sha256",
    "public_packet_sha256",
    "public_journal_sha256",
    "native_account_join_sha256",
    "history_reference_sha256",
    "current_reference_sha256",
    "public_only_recheck_sha256",
)
_STATES = frozenset({"reserved", "consumed", "uncertain", "reconciled_flat"})
_HEX = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True, slots=True, repr=False)
class PostG12AccountEventObservationV4:
    code: Literal[
        "v3_incomplete",
        "v3_binding_invalid",
        "v3_stale",
        "ledger_observation_unavailable",
        "ledger_observation_invalid",
        "original_event_already_recorded",
        "trusted_execution_inputs_missing",
    ]
    report_id: str
    scope_sha256: str
    original_event_key: str
    reservation_id: str
    v3_receipt_sha256: str | None = None
    g12_evidence_sha256: str | None = None
    g12_report_sha256: str | None = None
    public_packet_sha256: str | None = None
    public_journal_sha256: str | None = None
    native_account_join_sha256: str | None = None
    history_reference_sha256: str | None = None
    current_reference_sha256: str | None = None
    public_only_recheck_sha256: str | None = None
    ledger_observation_sha256: str | None = None
    ledger_event_state: str | None = None
    admission: Literal["DENY"] = "DENY"
    original_source_verified: Literal[False] = False
    account_complete: Literal[False] = False
    execution_recheck_performed: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    durable_intent_created: Literal[False] = False
    execution_authority: Literal[False] = False
    order_submitted: Literal[False] = False

    def __post_init__(self):
        if (
            type(self.code) is not str
            or self.code not in _CODES
            or type(self.report_id) is not str
            or not 1 <= len(self.report_id) <= 128
            or any(
                type(getattr(self, name)) is not str
                or _HEX.fullmatch(getattr(self, name)) is None
                for name in ("scope_sha256", "original_event_key", "reservation_id")
            )
            or any(
                pin is not None
                and (type(pin) is not str or _HEX.fullmatch(pin) is None)
                for pin in (getattr(self, name) for name in _SOURCE_PINS)
            )
            or self.ledger_observation_sha256 is not None
            and (
                type(self.ledger_observation_sha256) is not str
                or _HEX.fullmatch(self.ledger_observation_sha256) is None
            )
            or self.admission != "DENY"
            or any(
                getattr(self, name) is not False
                for name in (
                    "original_source_verified",
                    "account_complete",
                    "execution_recheck_performed",
                    "atomic_risk_reserved",
                    "durable_intent_created",
                    "execution_authority",
                    "order_submitted",
                )
            )
        ):
            raise PostG12AccountEventObservationError("v4_cannot_grant_execution")
        source = tuple(getattr(self, name) for name in _SOURCE_PINS)
        if self.code in {"v3_binding_invalid", "v3_stale"}:
            complete = all(item is None for item in source)
        elif self.code == "v3_incomplete":
            complete = source[0] is not None and all(
                item is None for item in source[1:]
            )
        else:
            complete = all(item is not None for item in source)
        if not complete:
            raise PostG12AccountEventObservationError("v4_pin_state_invalid")
        if self.code == "trusted_execution_inputs_missing":
            valid_observation = (
                self.ledger_observation_sha256 is not None
                and self.ledger_event_state == "absent_at_read"
            )
        elif self.code == "original_event_already_recorded":
            valid_observation = (
                self.ledger_observation_sha256 is not None
                and type(self.ledger_event_state) is str
                and self.ledger_event_state in _STATES
            )
        else:
            valid_observation = (
                self.ledger_observation_sha256 is None
                and self.ledger_event_state is None
            )
        if not valid_observation:
            raise PostG12AccountEventObservationError("v4_observation_state_invalid")


def _original_binding(
    original_market, run, original_inputs, account_session, scope, history_capture_id
):
    """Copy the same original pins V3 derives before any asynchronous IO."""
    scope = checked_bootstrap(scope, LedgerScope)
    if type(account_session) is not joined.ControlledDemoAccountSession:
        raise PostG12AccountEventObservationError("v4_exact_account_session_required")
    plan = account_capture._checked_plan(account_session._plan, account_session._pin)
    if (
        type(plan) is not account_capture.CurrentDemoAccountCapturePlanV7
        or scope.environment != "demo"
        or scope.account_id != plan.expected_uid
        or scope.settlement_currency != plan.settlement_currency
        or type(history_capture_id) is not str
        or joined._CAPTURE_ID.fullmatch(history_capture_id) is None
    ):
        raise PostG12AccountEventObservationError("v4_account_scope_mismatch")
    _, pre, inputs = joined._original(original_market, run, original_inputs)
    intent, risk_inputs, candidate = inputs["intent"], inputs["risk_inputs"], pre.result
    key = event_identity(pre.prefix.detection)
    if (
        not pre.pre_evidence_complete
        or key is None
        or candidate.candidate_entry != intent.candidate_entry
        or candidate.stop_loss is None
        or candidate.take_profit is None
        or risk_inputs.account is None
        or risk_inputs.account.account_id != plan.expected_uid
        or risk_inputs.account.settlement_currency != plan.settlement_currency
        or risk_inputs.authority is None
        or risk_inputs.authority.stamp.account_id != plan.expected_uid
        or risk_inputs.instrument is None
        or risk_inputs.instrument.instrument_id != intent.instrument_id
    ):
        raise PostG12AccountEventObservationError("v4_original_incomplete")
    candidate_sha256 = sha(
        canonical(
            {
                "pre_evidence_sha256": pre.evaluation_sha256,
                "event_key": key,
                "entry": str(candidate.candidate_entry),
                "stop_loss": str(candidate.stop_loss),
                "take_profit": str(candidate.take_profit),
            }
        )
    )
    return {
        "report_id": intent.report_id,
        "instrument_id": intent.instrument_id,
        "direction": intent.direction,
        "original_entry": str(candidate.candidate_entry),
        "original_stop_loss": str(candidate.stop_loss),
        "original_take_profit": str(candidate.take_profit),
        "candidate_sha256": candidate_sha256,
        "original_event_key": key,
        "account_plan_sha256": account_session._pin,
        "account_scope_sha256": digest(
            canonical([scope.environment, scope.account_id, scope.settlement_currency])
        ),
        "history_capture_id_sha256": sha(history_capture_id.encode("ascii")),
        "deadline": min(
            pre.prefix.intent.expires_at,
            pre.prefix.detection.trigger.expires_at,
            pre.result.entry_zone.expires_at,
            pre.prefix.timing.latest_valid_entry_time,
        ),
    }


def _bound_v3(value, expected):
    """Inspect the exact read-back receipt, never a caller's `passed` flag."""
    if type(value) is not dict or any(
        value.get(name) != pin for name, pin in expected.items() if name != "deadline"
    ):
        raise PostG12AccountEventObservationError("v4_receipt_original_changed")
    if value.get("code") != "joined_unqualified":
        return False
    if (
        value.get("public_only_recheck_code")
        != "projected_math_consistent_account_path_required"
    ):
        return False
    if any(
        value.get(name) is None
        for name in (
            "g12_evidence_sha256",
            "g12_report_sha256",
            "public_packet_sha256",
            "public_journal_sha256",
            "native_account_join_sha256",
            "history_reference_sha256",
            "current_reference_sha256",
            "public_only_recheck_sha256",
        )
    ) or (
        value.get("committed_history_readback_before_first_http") is not True
        or value.get("public_only_recheck_receipt_persisted") is not True
        or value.get("admission") != "DENY"
        or any(value.get(name) is not False for name in joined._FALSE_FIELDS)
    ):
        raise PostG12AccountEventObservationError("v4_receipt_source_changed")
    observed = joined._utc(value["observed_at"])
    barrier = joined._utc(value["publication_completed_at"])
    if not barrier < observed < expected["deadline"]:
        raise PostG12AccountEventObservationError("v4_receipt_expired")
    return True


async def publish_capture_inspect_history_event_v4(
    evidence_root,
    public_root,
    account_root,
    recheck_root,
    join_root,
    original_market,
    *,
    run,
    original_inputs,
    market_policy,
    account_session,
    session_factory,
    history_capture_id,
    ledger: QualificationLedgerRepository,
    scope: LedgerScope,
) -> PostG12AccountEventObservationV4:
    """Create this call's V3, read it back, then inspect its exact UID/event.

    There is no reserve, reconcile, intent, Arm, transport or execution path.
    Every result remains DENY, including a read that observes no prior event.
    """
    if type(ledger) is not QualificationLedgerRepository:
        raise PostG12AccountEventObservationError("v4_exact_ledger_required")
    scope = checked_bootstrap(scope, LedgerScope)
    expected = _original_binding(
        original_market,
        run,
        original_inputs,
        account_session,
        scope,
        history_capture_id,
    )
    base = {
        "report_id": expected["report_id"],
        "scope_sha256": expected["account_scope_sha256"],
        "original_event_key": expected["original_event_key"],
        "reservation_id": reservation_id(scope, expected["original_event_key"]),
    }
    root_identity = joined.prior._empty_native_receipt_root_identity(
        join_root, "v4_join_root_unavailable"
    )
    receipt = await joined.publish_capture_public_account_history_v3(
        evidence_root,
        public_root,
        account_root,
        recheck_root,
        join_root,
        original_market,
        run=run,
        original_inputs=original_inputs,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        history_capture_id=history_capture_id,
    )
    if (
        type(receipt) is not joined.PostG12OwnedPublicAccountHistoryDiagnosticV3
        or account_session._used is not True
    ):
        return PostG12AccountEventObservationV4(code="v3_binding_invalid", **base)
    try:
        replayed = joined.read_post_g12_account_history_receipt_v3(
            join_root,
            expected_sha256=receipt.receipt_sha256,
            expected_root_identity=root_identity,
        )
        if replayed.receipt_json != receipt.receipt_json:
            raise PostG12AccountEventObservationError("v4_readback_changed")
        value = decode(replayed.receipt_json, joined._MAX_RECEIPT)
        if not _bound_v3(value, expected):
            return PostG12AccountEventObservationV4(
                code="v3_incomplete", v3_receipt_sha256=receipt.receipt_sha256, **base
            )
    except asyncio.CancelledError:
        raise
    except PostG12AccountEventObservationError as exc:
        return PostG12AccountEventObservationV4(
            code=(
                "v3_stale" if str(exc) == "v4_receipt_expired" else "v3_binding_invalid"
            ),
            **base,
        )
    except Exception:  # noqa: BLE001 -- neither raw source nor path enters a result
        return PostG12AccountEventObservationV4(code="v3_binding_invalid", **base)
    pins = {
        "v3_receipt_sha256": receipt.receipt_sha256,
        **{
            name: value[name]
            for name in (
                "g12_evidence_sha256",
                "g12_report_sha256",
                "public_packet_sha256",
                "public_journal_sha256",
                "native_account_join_sha256",
                "history_reference_sha256",
                "current_reference_sha256",
                "public_only_recheck_sha256",
            )
        },
    }
    try:
        observation = await ledger.read_event_observation(
            scope, expected["original_event_key"]
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- never expose private SQL details
        return PostG12AccountEventObservationV4(
            code="ledger_observation_unavailable", **base, **pins
        )
    try:
        if type(observation) is not LedgerEventObservation:
            raise ValueError
        replayed_observation = LedgerEventObservation.model_validate_json(
            observation.canonical_json, strict=True
        )
        v3_observed = joined._utc(value["observed_at"])
        if (
            replayed_observation != observation
            or observation.scope != scope
            or observation.original_event_key != expected["original_event_key"]
            or not v3_observed
            < observation.request_started_at
            <= observation.observed_at
            <= observation.received_at
            < expected["deadline"]
        ):
            raise ValueError
        state = (
            "absent_at_read"
            if observation.matched is None
            else observation.matched.state
        )
        return PostG12AccountEventObservationV4(
            code=(
                "trusted_execution_inputs_missing"
                if observation.matched is None
                else "original_event_already_recorded"
            ),
            ledger_observation_sha256=observation.sha256,
            ledger_event_state=state,
            **base,
            **pins,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- observed mismatch cannot grant absence
        return PostG12AccountEventObservationV4(
            code="ledger_observation_invalid", **base, **pins
        )
