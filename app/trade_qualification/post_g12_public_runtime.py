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

from app.public_market_source.public_clock import native_stamp
from app.public_market_source.public_market_receipts import (
    canonical,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_evidence.gates import publish_qualification_evidence
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.engine import PortfolioInputs
from app.trade_qualification.market_bridge import public_market_snapshot
from app.trade_qualification.public_market_collector import _policy_copy
from app.trade_qualification.public_source_runtime import (
    _capture_after_publication,
    _consume_public_capture,
)
from app.trade_qualification.recheck import evaluate_recorded_recheck
from app.trade_qualification.recheck_models import freeze_recheck_origin

_ISSUER = object()
_PUBLICATIONS = WeakKeyDictionary()


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
    if type(value) is not _Publication:
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
            "ws_origin": "wss://ws.okx.com:8443/ws/v5/public",
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
