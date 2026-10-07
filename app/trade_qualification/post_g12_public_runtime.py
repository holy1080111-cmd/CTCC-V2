"""Unmounted G12 -> owned public sources -> diagnostic recheck invocation.

Original inputs remain replay claims. This slice acquires no account credentials,
publishes no account revision, reserves no risk and grants no order authority.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PosixPath, WindowsPath
from threading import get_ident
from typing import Literal
from weakref import WeakKeyDictionary

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_evidence.gates import (
    publish_qualification_evidence,
    publish_qualification_evidence_liquidity_v2,
)
from app.trade_qualification import current_conditions_v2 as current_v2
from app.trade_qualification import (
    current_economics_v2,
    data_v2,
    original_event_v2,
)
from app.trade_qualification import demo_public_origin as demo_origin
from app.trade_qualification import demo_public_origin_preflight as origin_preflight
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import public_source_runtime as source_runtime
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.data_v2 import DataQualificationResultV2
from app.trade_qualification.engine import PortfolioInputs, PreEvidenceRun
from app.trade_qualification.market_bridge import public_market_snapshot
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.public_market_collector import _policy_copy
from app.trade_qualification.public_source_runtime import (
    _capture_after_publication,
    _capture_after_publication_v2,
    _consume_public_capture,
    _consume_public_capture_v2,
)
from app.trade_qualification.recheck import evaluate_recorded_recheck
from app.trade_qualification.recheck_models import freeze_recheck_origin

_ISSUER = object()
_PUBLICATIONS = WeakKeyDictionary()
_NATIVE_G1_RECEIPT_MAX_BYTES = 2048
_V2_DEMO_REST_ORIGIN = "https://www.okx.com"
_V2_DEMO_WS_ORIGIN = "wss://ws.okx.com:443/ws/v5/public"


class _Publication:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise ValueError("owned_publication_required")

    def __copy__(self):
        raise ValueError("publication_not_transferable")

    def __deepcopy__(self, memo):
        raise ValueError("publication_not_transferable")

    def __reduce_ex__(self, protocol):
        raise ValueError("publication_not_transferable")


class _PublicationV2(_Publication):
    """Issued only after this invocation's actual new publisher/readback."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class PublicRecheckDiagnostic:
    code: str
    report_id: str
    observed_at: datetime | None
    journal_sha256: str | None = None
    evidence: object | None = field(default=None, repr=False)
    packet: object | None = field(default=None, repr=False)
    recheck: object | None = field(default=None, repr=False)
    record_kind: Literal["post_g12_owned_public_diagnostic_v1"] = field(
        default="post_g12_owned_public_diagnostic_v1", init=False
    )
    execution_authority: Literal[False] = field(default=False, init=False)
    original_source_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


def _take_publication(value):
    return _take_publication_exact(value, _Publication)


def _take_publication_v2(value):
    return _take_publication_exact(value, _PublicationV2)


def _take_publication_exact(value, expected):
    if type(value) is not expected:
        raise ValueError("owned_publication_required")
    result = _PUBLICATIONS.pop(value, None)
    task = asyncio.current_task()
    if (
        result is None
        or result["parent"] is not task
        or result["pid"] != os.getpid()
        or result["thread"] != get_ident()
        or task is None
        or task.cancelling()
        or (
            expected is _PublicationV2
            and result["loop"] is not asyncio.get_running_loop()
        )
    ):
        raise ValueError("owned_publication_unavailable")
    return result


async def publish_capture_public_recheck(
    evidence_root,
    public_root,
    original_market,
    *,
    run,
    original_inputs,
    market_policy,
) -> PublicRecheckDiagnostic:
    """Fixed sources and clock; caller callbacks/clients/old receipts are absent.

    Both native roots must already exist and be controlled by the service. The new
    public root must be empty. A failed attempt is retained and is not retried there.
    """
    # Reuse only the existing bounded immutable-input/versioned replay helper;
    # do not invoke the old account collector or its diagnostic orchestration.
    from app.trade_qualification.one_shot import _original

    if (
        any(
            type(path) not in (PosixPath, WindowsPath) or not path.is_absolute()
            for path in (evidence_root, public_root)
        )
        or evidence_root == public_root
    ):
        raise ValueError("public_runtime_roots_invalid")
    market, pre, inputs = _original(original_market, run, original_inputs)
    selected = _policy_copy(market_policy)
    report = inputs["intent"].report_id
    if not pre.pre_evidence_complete:
        return PublicRecheckDiagnostic("public_runtime_original_rejected", report, None)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    invocation = object()
    last = None
    evidence = packet = recheck = None
    journal = None

    def clock():
        nonlocal last
        if task.cancelling():
            raise asyncio.CancelledError
        stamp = native_stamp()
        if last is not None:
            validate_stamps((last, stamp))
        last = stamp
        return utc_from_ns(stamp["utc_ns"])

    try:
        clock()
        evidence = publish_qualification_evidence(
            evidence_root, market, run=pre, **inputs, purpose="observed", clock=clock
        )
        if (
            not evidence.result.evidence_complete
            or evidence.receipt is None
            or evidence.receipt.status != "written"
        ):
            return PublicRecheckDiagnostic(
                "public_runtime_new_g12_required", report, clock(), evidence=evidence
            )
        origin = freeze_recheck_origin(evidence)
        completed = clock()
        if not origin.publication_completed_at <= completed < origin.deadline:
            raise ValueError("public_runtime_barrier_expired")
        publication = _Publication(_ISSUER)
        plan = {
            "invocation_id": uuid.uuid4().hex,
            "environment": "demo",
            "report_id": report,
            "instrument_id": inputs["intent"].instrument_id,
            "candidate_sha256": sha(
                canonical(origin.candidate.model_dump(mode="json", round_trip=True))
            ),
            "event_key": origin.original_event_key,
            "original_policy_sha256": origin.original_policy_sha256,
            "evidence_sha256": evidence.evaluation_sha256,
            "report_sha256": evidence.receipt.report_sha256,
            "publication_completed_at": origin.publication_completed_at.isoformat(),
            "barrier": last,
            "expires_at": origin.deadline.isoformat(),
            "rest_origin": "https://www.okx.com",
            "ws_origin": "wss://ws.okx.com:443/ws/v5/public",
        }
        _PUBLICATIONS[publication] = {
            "invocation": invocation,
            "parent": task,
            "pid": os.getpid(),
            "thread": get_ident(),
            "plan": plan,
            "report_id": report,
            "instrument_id": inputs["intent"].instrument_id,
            "barrier": last,
            "publication_completed_at": origin.publication_completed_at,
            "expires_at": origin.deadline,
        }
        carrier = await _capture_after_publication(publication, selected, public_root)
        packet, journal, observed = _consume_public_capture(carrier, invocation)
        last = observed
        current = public_market_snapshot(
            packet, expected_bundle_sha256=packet.bundle_sha256
        )
        ticker = packet.ws.ticker
        reference = WSReferenceObservation(
            report_id=report,
            instrument_id=packet.instrument_id,
            bid=ticker.bid,
            ask=ticker.ask,
            source_time=ticker.source_time,
            received_at=ticker.received_at,
        )
        # This slice never carries legacy account/authority claims forward.
        risk = PortfolioInputs(
            requested_contracts=inputs["risk_inputs"].requested_contracts,
            requested_leverage=inputs["risk_inputs"].requested_leverage,
            instrument=None,
            account=None,
            authority=None,
        )
        recheck = evaluate_recorded_recheck(
            market,
            current,
            origin=origin,
            original_inputs=inputs,
            quote=packet.quote,
            reference=reference,
            current_risk_inputs=risk,
            consumed_event_keys=inputs["consumed_event_keys"],
            observed_at=clock(),
        )
        return PublicRecheckDiagnostic(
            "public_runtime_captured_account_authority_missing",
            report,
            clock(),
            journal,
            evidence,
            packet,
            recheck,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed diagnostic, no source exception payload
        return PublicRecheckDiagnostic(
            "public_runtime_denied", report, None, journal, evidence, packet, recheck
        )


@dataclass(frozen=True, slots=True)
class PublicCaptureDiagnosticV2:
    code: str
    report_id: str
    observed_at: datetime | None
    journal_sha256: str | None = None
    evidence: object | None = field(default=None, repr=False)
    packet: object | None = field(default=None, repr=False)
    context: object | None = field(default=None, repr=False)
    current_g1: DataQualificationResultV2 | None = field(default=None, repr=False)
    current_conditions: current_v2.CurrentBaseConditionsDiagnosticV2 | None = field(
        default=None, repr=False
    )
    original_event_zone: original_event_v2.OriginalEventZoneDiagnosticV2 | None = field(
        default=None, repr=False
    )
    projected_economics: (
        current_economics_v2.CurrentProjectedEconomicsDiagnosticV2 | None
    ) = field(default=None, repr=False)
    final_g1: DataQualificationResultV2 | None = field(default=None, repr=False)
    record_kind: str = field(default="post_g12_owned_public_diagnostic_v2", init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    original_source_verified: Literal[False] = field(default=False, init=False)
    execution_recheck_performed: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class NativeOriginalPreparationDiagnosticV2:
    """A consumed native initial-source handoff, stopped before candidate/G12."""

    code: str
    initial_report_id: str | None
    observed_at: datetime | None
    initial_packet_sha256: str | None = None
    journal_sha256: str | None = None
    g1_receipt_json: bytes | None = field(default=None, repr=False)
    g1_evaluated: Literal[False] = field(default=False, init=False)
    g1_passed: Literal[False] = field(default=False, init=False)
    record_kind: str = field(
        default="native_original_g12_seam_diagnostic_v2", init=False
    )
    admission: Literal["DENY"] = field(default="DENY", init=False)
    original_source_verified: Literal[False] = field(default=False, init=False)
    candidate_created: Literal[False] = field(default=False, init=False)
    g12_published: Literal[False] = field(default=False, init=False)
    post_publication_captured: Literal[False] = field(default=False, init=False)
    execution_recheck_performed: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)

    @property
    def g1_receipt_sha256(self) -> str | None:
        return None if self.g1_receipt_json is None else sha(self.g1_receipt_json)


async def capture_native_original_for_g12_v2(
    initial_root, *, instrument_id, market_policy
) -> NativeOriginalPreparationDiagnosticV2:
    """Consume an actual initial-public V2 carrier inside this task, then deny.

    No caller market, candidate, run, G12 receipt, or publication barrier enters.
    No non-synthetic native G1 data policy is registered. A valid native packet
    therefore receives a bounded missing-policy receipt, not a fabricated G1
    PASS. A real G12 also needs a source-derived candidate and account-owned
    instrument/cost inputs. This seam cannot borrow caller replay inputs.
    """
    from app.trade_qualification import qualification_runtime as initial
    from app.trade_qualification.public_source_runtime import (
        _consume_initial_public_capture_v2,
    )

    invocation = object()
    report = journal = None
    try:
        carrier, report, expires = await initial._capture_initial_lineage_v2(
            initial_root,
            instrument_id=instrument_id,
            market_policy=market_policy,
            invocation=invocation,
        )
        packet, journal, observed = _consume_initial_public_capture_v2(
            carrier, invocation
        )
        finished = native_stamp()
        validate_stamps((observed, finished))
        task = asyncio.current_task()
        if task is None or task.cancelling():
            raise asyncio.CancelledError
        at = utc_from_ns(finished["utc_ns"])
        if at >= expires:
            raise ValueError("native_original_g12_seam_expired")
        raw = decode(packet.packet_json, public_v2.MAX_PACKET_BYTES)
        if (
            raw["stage"] != "initial_public"
            or raw["barrier_completed_at"] is not None
            or raw["environment"] != "demo"
            or raw["report_id"] != report
            or raw["instrument_id"] != instrument_id
        ):
            raise ValueError("native_original_g12_seam_lineage_mismatch")
        context = public_market_context_v2(
            packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=at
        )
        if context.packet_sha256 != packet.bundle_sha256:
            raise ValueError("native_original_g12_seam_context_mismatch")
        # The only existing fixed G1 profile belongs to synthetic numeric
        # fixtures. Its bounds must never be adopted as an operational policy.
        # Record the exact first missing dependency without inventing a policy
        # or exposing the consumed packet as a reusable capability.
        g1_receipt = canonical(
            {
                "schema_version": "ctcc.native_initial_g1_policy_gate.v1",
                "code": "native_g1_policy_unregistered",
                "initial_report_id": report,
                "initial_packet_sha256": packet.bundle_sha256,
                "journal_sha256": journal,
                "observed_at": at.isoformat(),
                "g1_policy_sha256": None,
                "g1_evaluation_sha256": None,
                "g1_evaluated": False,
                "g1_passed": False,
                "admission": "DENY",
                "execution_authority": False,
            }
        )
        if len(g1_receipt) > _NATIVE_G1_RECEIPT_MAX_BYTES:
            raise ValueError("native_original_g1_receipt_unbounded")
        return NativeOriginalPreparationDiagnosticV2(
            "native_original_v2_g1_policy_unregistered",
            report,
            at,
            packet.bundle_sha256,
            journal,
            g1_receipt,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no source/transport exception crosses this seam
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from None
        return NativeOriginalPreparationDiagnosticV2(
            "native_original_v2_denied", report, None, None, journal
        )


def _publish_lineage_v2(
    evidence_root, market, *, pre, inputs, selected, invocation, demo_session=None
):
    """Actual fixed publisher/issuer for a same-task candidate coordinator.

    This helper accepts no saved receipt, barrier or source capability. Its
    opaque result is registered only after new publication and readback. The
    caller must acquire and consume through the native runtime in this invocation;
    the diagnostic DTO is never a substitute for that private path.
    """
    if type(invocation) is not object:
        raise ValueError("public_v2_invocation_required")
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    declared_route = (
        origin_preflight._declared_demo_public_plan(demo_session)
        if demo_session is not None
        else {}
    )
    route = (
        demo_origin.reviewed_demo_public_route(declared_route["registration_region"])
        if declared_route
        else None
    )
    # The route-bound builders still lack authenticated registration-region
    # proof. Reject before clock sampling or immutable G12 publication, not
    # only at the collector boundary.
    source_runtime._require_trusted_v2_demo_origin_profile(
        {
            "environment": "demo",
            "rest_origin": _V2_DEMO_REST_ORIGIN,
            "ws_origin": _V2_DEMO_WS_ORIGIN,
            **declared_route,
        }
    )
    last = None

    def clock():
        nonlocal last
        if task.cancelling():
            raise asyncio.CancelledError
        stamp = native_stamp()
        if last is not None:
            validate_stamps((last, stamp))
        last = stamp
        return utc_from_ns(stamp["utc_ns"])

    clock()
    evidence = publish_qualification_evidence_liquidity_v2(
        evidence_root, market, run=pre, **inputs, purpose="observed", clock=clock
    )
    if (
        not evidence.result.evidence_complete
        or evidence.receipt is None
        or evidence.receipt.status != "written"
    ):
        clock()
        return None, evidence, None, last
    origin = freeze_recheck_origin(evidence)
    if not origin.publication_completed_at <= clock() < origin.deadline:
        return None, evidence, origin, last
    publication = _PublicationV2(_ISSUER)
    plan = {
        "stage": "post_publication",
        "invocation_id": uuid.uuid4().hex,
        "environment": "demo",
        "report_id": inputs["intent"].report_id,
        "instrument_id": inputs["intent"].instrument_id,
        "candidate_sha256": sha(
            canonical(origin.candidate.model_dump(mode="json", round_trip=True))
        ),
        "event_key": origin.original_event_key,
        "original_policy_sha256": origin.original_policy_sha256,
        "evidence_sha256": evidence.evaluation_sha256,
        "report_sha256": evidence.receipt.report_sha256,
        "publication_completed_at": origin.publication_completed_at.isoformat(),
        "barrier": last,
        "expires_at": origin.deadline.isoformat(),
        "rest_origin": _V2_DEMO_REST_ORIGIN,
        "ws_origin": _V2_DEMO_WS_ORIGIN,
        **declared_route,
        "policy_sha256": public_v2._policy_digest(selected, route),
        **public_v2._plan_pins(route),
    }
    _PUBLICATIONS[publication] = {
        "invocation": invocation,
        "parent": task,
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "plan": plan,
        "report_id": inputs["intent"].report_id,
        "instrument_id": inputs["intent"].instrument_id,
        "barrier": last,
        "publication_completed_at": origin.publication_completed_at,
        "expires_at": origin.deadline,
    }
    return publication, evidence, origin, last


async def publish_capture_public_v2(
    evidence_root,
    public_root,
    original_market,
    *,
    run,
    original_inputs,
    market_policy,
    demo_session=None,
):
    """Actual new G12, native v2 public acquisition and raw current G1.

    Original inputs undergo the existing full gate replay, but their caller
    origin is not authenticated. This entry cannot promote them to original
    source ownership. Base G2--G4, closed-bar event/zone, and fixed projected
    costs are diagnostic only; complete intrabar survival, history semantics,
    actual account economics and final authority are absent.
    """
    from app.trade_qualification.one_shot import _original

    if (
        any(
            type(path) not in (PosixPath, WindowsPath) or not path.is_absolute()
            for path in (evidence_root, public_root)
        )
        or evidence_root == public_root
    ):
        raise ValueError("public_runtime_roots_invalid")
    selected = public_v2._policy_copy(market_policy)
    market, pre, inputs = _original(original_market, run, original_inputs)
    report = inputs["intent"].report_id
    if not pre.pre_evidence_complete:
        return PublicCaptureDiagnosticV2("public_v2_original_rejected", report, None)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    invocation = object()
    last = publication = None
    evidence = packet = context = current_g1 = final_g1 = None
    current_conditions = event_zone = economics = journal = None

    def clock():
        nonlocal last
        if task.cancelling():
            raise asyncio.CancelledError
        stamp = native_stamp()
        if last is not None:
            validate_stamps((last, stamp))
        last = stamp
        return utc_from_ns(stamp["utc_ns"])

    try:
        publication, evidence, origin, last = _publish_lineage_v2(
            evidence_root,
            market,
            pre=pre,
            inputs=inputs,
            selected=selected,
            invocation=invocation,
            demo_session=demo_session,
        )
        if publication is None:
            return PublicCaptureDiagnosticV2(
                "public_v2_new_g12_required" if origin is None else "public_v2_denied",
                report,
                utc_from_ns(last["utc_ns"]),
                evidence=evidence,
            )
        carrier = await _capture_after_publication_v2(
            publication, selected, public_root
        )
        packet, journal, last = _consume_public_capture_v2(carrier, invocation)
        context = public_market_context_v2(
            packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=clock()
        )
        raw = decode(packet.packet_json, public_v2.MAX_PACKET_BYTES)
        if (
            raw["stage"] != "post_publication"
            or raw["barrier_completed_at"]
            != origin.publication_completed_at.isoformat()
            or raw["report_id"] != report
            or raw["instrument_id"] != inputs["intent"].instrument_id
        ):
            raise ValueError("public_v2_current_g1_lineage_mismatch")
        checked_at = clock()
        evaluated_g1 = data_v2.evaluate_public_market_data_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            policy=pre.policy.prefix.data,
            evaluated_at=checked_at,
        )
        current_g1 = data_v2.verify_public_market_data_v2(
            evaluated_g1,
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            policy=pre.policy.prefix.data,
            evaluated_at=checked_at,
        )
        if current_g1.passed and (
            type(pre) is PreEvidenceRun
            and pre.prefix.intent.strategy in current_v2.BASE_STRATEGIES
        ):
            current_conditions = current_v2.evaluate_current_base_conditions_v2(
                packet, origin=origin, current_g1=current_g1
            )
            if current_conditions.passed:
                event_zone = original_event_v2.evaluate_original_event_zone_v2(
                    packet, origin=origin, current_g1=current_g1, observed_at=clock()
                )
                if event_zone.passed:
                    economics = (
                        current_economics_v2.evaluate_current_projected_economics_v2(
                            packet,
                            origin=origin,
                            current_g1=current_g1,
                            observed_at=clock(),
                        )
                    )
        finished_at = clock()
        if finished_at >= origin.deadline:
            raise ValueError("public_runtime_barrier_expired")
        if event_zone is not None and event_zone.passed:
            public_market_context_v2(
                packet,
                expected_bundle_sha256=packet.bundle_sha256,
                evaluated_at=finished_at,
            )
            # A successful earlier G1 or cost calculation is not a freshness
            # claim at this final observation. Both original policy cutoffs can
            # be stricter than the fixed V2 quote transport profile.
            final_g1 = data_v2.evaluate_public_market_data_v2(
                packet,
                expected_bundle_sha256=packet.bundle_sha256,
                policy=pre.policy.prefix.data,
                evaluated_at=finished_at,
            )
            if not final_g1.passed:
                return PublicCaptureDiagnosticV2(
                    "public_v2_current_g1_rejected_at_finish",
                    report,
                    finished_at,
                    journal,
                    evidence,
                    packet,
                    context,
                    None,
                    None,
                    None,
                    None,
                    final_g1,
                )
            economics = current_economics_v2.evaluate_current_projected_economics_v2(
                packet,
                origin=origin,
                current_g1=current_g1,
                observed_at=finished_at,
            )
        return PublicCaptureDiagnosticV2(
            "public_v2_current_g1_rejected"
            if not current_g1.passed
            else "public_v2_history_current_checks_unavailable"
            if current_conditions is None
            else "public_v2_current_g2_g4_rejected"
            if not current_conditions.passed
            else "public_v2_original_event_zone_rejected"
            if event_zone is None or not event_zone.passed
            else "public_v2_projected_economics_rejected"
            if economics is None or not economics.projected_math_passed
            else "public_v2_base_projected_economics_passed_account_required",
            report,
            utc_from_ns(last["utc_ns"]),
            journal,
            evidence,
            packet,
            context,
            current_g1,
            current_conditions,
            event_zone,
            economics,
            final_g1,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed diagnostic, retain evidence on failure
        if task.cancelling():
            raise asyncio.CancelledError from None
        return PublicCaptureDiagnosticV2(
            "public_v2_denied",
            report,
            None,
            journal,
            evidence,
            packet,
            context,
            current_g1,
            current_conditions,
            event_zone,
            economics,
            final_g1,
        )
    finally:
        if publication is not None:
            _PUBLICATIONS.pop(publication, None)
