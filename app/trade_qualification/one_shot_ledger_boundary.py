"""Read-only R7-to-DB0017 event binding, never a reservation or intent issuer.

The owned one-shot performs this invocation's G12 and post-barrier captures.
Its recorded recheck has incomplete account/source/path authority today. This
boundary pins that fixed candidate to a measured, locked event-journal read;
even an absent event is only an observation and cannot become reserve/submit
permission. No exchange client or ledger writer is reachable from this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture, public_market_collector
from app.trade_qualification.event_observation import LedgerEventObservation
from app.trade_qualification.one_shot import (
    OneShotCaptureResult,
    publish_capture_recheck,
)
from app.trade_qualification.recheck import copy_recorded_recheck
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.reservations import (
    LedgerScope,
    checked_bootstrap,
    digest,
    reservation_id,
)


@dataclass(frozen=True, slots=True, repr=False)
class OneShotLedgerBoundaryDiagnostic:
    code: Literal[
        "capture_incomplete",
        "capture_binding_invalid",
        "ledger_observation_unavailable",
        "ledger_observation_invalid",
        "original_event_already_recorded",
        "trusted_execution_inputs_missing",
    ]
    report_id: str
    scope_sha256: str
    original_event_key: str | None = None
    exact_reservation_id: str | None = None
    evidence_sha256: str | None = None
    recheck_sha256: str | None = None
    account_packet_sha256: str | None = None
    ledger_observation_sha256: str | None = None
    ledger_event_state: str | None = None
    record_kind: Literal["one_shot_ledger_observation_not_execution_permission"] = (
        "one_shot_ledger_observation_not_execution_permission"
    )
    admission: Literal["DENY"] = "DENY"
    source_authenticity_verified: Literal[False] = False
    account_complete: Literal[False] = False
    intrabar_path_verified: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    durable_intent_created: Literal[False] = False
    execution_authority: Literal[False] = False
    order_submitted: Literal[False] = False

    def __post_init__(self):
        if (
            self.admission != "DENY"
            or self.record_kind
            != "one_shot_ledger_observation_not_execution_permission"
            or any(
                getattr(self, name) is not False
                for name in (
                    "source_authenticity_verified",
                    "account_complete",
                    "intrabar_path_verified",
                    "atomic_risk_reserved",
                    "durable_intent_created",
                    "execution_authority",
                    "order_submitted",
                )
            )
        ):
            raise ValueError("one_shot_ledger_cannot_grant_execution")


def _fixed_pins(capture: OneShotCaptureResult, scope: LedgerScope, plan_sha256: str):
    """Validate a diagnostic's fixed original and captured lineage only.

    This is structural replay, not source authentication or a substitute for
    recorded R7 evaluation from its original/current raw market inputs.
    """
    if (
        type(capture) is not OneShotCaptureResult
        or capture.evidence is None
        or capture.market_packet is None
        or capture.account_packet is None
        or capture.account_payload_sha256 is None
        or capture.recheck is None
    ):
        raise ValueError("one_shot_capture_incomplete")
    origin = freeze_recheck_origin(capture.evidence)
    recheck = copy_recorded_recheck(capture.recheck)
    public = public_market_collector.validate_collected_public_market(
        capture.market_packet
    )
    frozen_account = account_capture.freeze_demo_account_packet(
        capture.account_packet, expected_plan_sha256=plan_sha256
    )
    account = account_capture.verify_demo_account_packet(
        frozen_account.payload,
        expected_sha256=capture.account_payload_sha256,
        expected_plan_sha256=plan_sha256,
    )
    intent = origin.evidence.pre_evidence.prefix.intent
    if (
        frozen_account.sha256 != capture.account_payload_sha256
        or recheck.origin != origin
        or capture.report_id != intent.report_id
        or public.report_id != intent.report_id
        or public.instrument_id != intent.instrument_id
        or public.barrier_completed_at != origin.publication_completed_at
        or account.barrier_completed_at != origin.publication_completed_at
        or account.plan.expected_uid != scope.account_id
        or account.plan.settlement_currency != scope.settlement_currency
        or public.completed_at > recheck.observed_at
        or account.completed_at > recheck.observed_at
        or not origin.publication_completed_at
        < recheck.observed_at
        <= capture.observed_at
        < origin.deadline
        or recheck.quote_bundle_sha256 != public.quote.bundle_sha256
    ):
        raise ValueError("one_shot_fixed_lineage_mismatch")
    return origin, recheck


async def publish_capture_inspect_event_ledger(
    *,
    ledger: QualificationLedgerRepository,
    scope: LedgerScope,
    **capture_kwargs,
) -> OneShotLedgerBoundaryDiagnostic:
    """Own G12/capture/recheck, then read the exact DB0017 event key once.

    The ledger read is diagnostic and starts only after the one-shot returns.
    It never invokes reserve, consume, intent construction or exchange POST.
    No caller-supplied old capture, G12 receipt or recheck is accepted here.
    """
    scope = checked_bootstrap(scope, LedgerScope)
    if type(ledger) is not QualificationLedgerRepository:
        raise ValueError("one_shot_exact_ledger_repository_required")
    plan = account_capture._checked_plan(
        capture_kwargs["account_plan"], capture_kwargs["expected_account_plan_sha256"]
    )
    if (
        scope.environment != "demo"
        or plan.expected_uid != scope.account_id
        or plan.settlement_currency != scope.settlement_currency
    ):
        raise ValueError("one_shot_ledger_scope_mismatch")
    capture = await publish_capture_recheck(**capture_kwargs)
    base = {
        "report_id": capture.report_id,
        "scope_sha256": digest(scope),
    }
    if (
        capture.evidence is None
        or capture.recheck is None
        or capture.market_packet is None
        or capture.account_packet is None
    ):
        return OneShotLedgerBoundaryDiagnostic(code="capture_incomplete", **base)
    try:
        origin, recheck = _fixed_pins(
            capture, scope, capture_kwargs["expected_account_plan_sha256"]
        )
        base.update(
            original_event_key=origin.original_event_key,
            exact_reservation_id=reservation_id(scope, origin.original_event_key),
            evidence_sha256=origin.evidence_sha256,
            recheck_sha256=recheck.evaluation_sha256,
            account_packet_sha256=capture.account_payload_sha256,
        )
    except Exception:  # noqa: BLE001 -- no raw private/source exception in result
        return OneShotLedgerBoundaryDiagnostic(code="capture_binding_invalid", **base)
    try:
        observation = await ledger.read_event_observation(
            scope, origin.original_event_key
        )
    except Exception:  # noqa: BLE001 -- preserve cancellation and redact SQL detail
        return OneShotLedgerBoundaryDiagnostic(
            code="ledger_observation_unavailable", **base
        )
    try:
        if type(observation) is not LedgerEventObservation:
            raise ValueError("exact_event_observation_required")
        replayed = LedgerEventObservation.model_validate_json(
            observation.canonical_json, strict=True
        )
        if (
            replayed != observation
            or observation.scope != scope
            or observation.original_event_key != origin.original_event_key
            or observation.request_started_at <= capture.observed_at
            or observation.received_at < observation.request_started_at
        ):
            raise ValueError("event_observation_lineage_invalid")
        base.update(
            ledger_observation_sha256=observation.sha256,
            ledger_event_state=(
                "absent_at_read"
                if observation.matched is None
                else observation.matched.state
            ),
        )
    except Exception:  # noqa: BLE001 -- no raw private/source exception in result
        return OneShotLedgerBoundaryDiagnostic(
            code="ledger_observation_invalid", **base
        )
    return OneShotLedgerBoundaryDiagnostic(
        code=(
            "original_event_already_recorded"
            if observation.matched is not None
            else "trusted_execution_inputs_missing"
        ),
        **base,
    )
