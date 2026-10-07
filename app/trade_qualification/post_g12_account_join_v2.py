"""New G12 -> native public -> native account readback, diagnostic only.

The original G1--G11 run is independently replayed but still caller-origin.
Neither that replay nor this source join grants an event/risk reservation or
permission to submit an order. The native Demo public issuer currently refuses
capture before G12 until account registration-region provenance is established.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import PosixPath, WindowsPath
from typing import Literal

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original_source
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.one_shot import _original
from app.trade_qualification.timing import event_identity

_SCHEMA = "ctcc.post_g12_owned_public_account_diagnostic.v2"
_MAX_RECEIPT = 4096
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CODES = frozenset(
    {
        "new_g12_required",
        "public_unavailable",
        "account_unavailable",
        "joined_unqualified",
        "denied",
    }
)
_PIN_FIELDS = (
    "candidate_sha256",
    "original_event_key",
    "g12_evidence_sha256",
    "g12_report_sha256",
    "public_packet_sha256",
    "public_journal_sha256",
    "account_plan_sha256",
    "account_receipt_sha256",
    "account_packet_sha256",
)
_FALSE_FIELDS = (
    "original_source_verified",
    "account_complete",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "execution_authority",
    "order_submitted",
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "report_id",
        "instrument_id",
        "direction",
        "original_entry",
        "original_stop_loss",
        "original_take_profit",
        "publication_completed_at",
        "public_first_request_started_at",
        "account_first_request_started_at",
        "observed_at",
        "public_request_count",
        "account_page_count",
        "admission",
        *_PIN_FIELDS,
        *_FALSE_FIELDS,
    }
)


class PostG12AccountJoinError(ValueError):
    """Fixed, redacted input or receipt denial."""


def _utc_text(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
        raise ValueError
    return instant


@dataclass(frozen=True, slots=True, repr=False)
class PostG12OwnedPublicAccountDiagnosticV2:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if (
                type(self.receipt_json) is not bytes
                or not 0 < len(self.receipt_json) <= _MAX_RECEIPT
            ):
                raise ValueError
            raw = decode(self.receipt_json, _MAX_RECEIPT)
            if (
                type(raw) is not dict
                or set(raw) != _FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != _SCHEMA
                or raw["code"] not in _CODES
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or type(raw["report_id"]) is not str
                or not 1 <= len(raw["report_id"]) <= 128
                or type(raw["instrument_id"]) is not str
                or not 1 <= len(raw["instrument_id"]) <= 64
                or raw["direction"] not in ("long", "short")
                or type(raw["public_request_count"]) is not int
                or not 0 <= raw["public_request_count"] <= 1000
                or type(raw["account_page_count"]) is not int
                or not 0 <= raw["account_page_count"] <= 1000
            ):
                raise ValueError
            for name in _PIN_FIELDS:
                value = raw[name]
                if value is not None and (
                    type(value) is not str or _HEX.fullmatch(value) is None
                ):
                    raise ValueError
            for name in (
                "publication_completed_at",
                "public_first_request_started_at",
                "account_first_request_started_at",
                "observed_at",
            ):
                if raw[name] is not None:
                    _utc_text(raw[name])
            for name in (
                "original_entry",
                "original_stop_loss",
                "original_take_profit",
            ):
                value = raw[name]
                if (
                    type(value) is not str
                    or len(value) > 128
                    or not Decimal(value).is_finite()
                ):
                    raise ValueError
            barrier = raw["publication_completed_at"]
            first_public = raw["public_first_request_started_at"]
            first_account = raw["account_first_request_started_at"]
            if (
                raw["candidate_sha256"] is None
                or raw["original_event_key"] is None
                or raw["account_plan_sha256"] is None
                or (barrier is None) != (raw["g12_evidence_sha256"] is None)
                or (barrier is None) != (raw["g12_report_sha256"] is None)
                or (first_public is None) != (raw["public_packet_sha256"] is None)
                or (first_public is None) != (raw["public_journal_sha256"] is None)
                or (first_public is None) != (raw["public_request_count"] == 0)
                or (first_account is None) != (raw["account_receipt_sha256"] is None)
                or (first_account is None) != (raw["account_packet_sha256"] is None)
                or (first_account is None) != (raw["account_page_count"] == 0)
                or first_public is not None
                and (barrier is None or _utc_text(first_public) <= _utc_text(barrier))
                or first_account is not None
                and (
                    first_public is None
                    or _utc_text(first_account) <= _utc_text(barrier)
                    or _utc_text(first_account) < _utc_text(first_public)
                )
                or raw["code"] == "joined_unqualified"
                and first_account is None
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- never echo private source or receipts
            raise PostG12AccountJoinError("post_g12_account_receipt_invalid") from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def _public_request_starts(packet, barrier):
    checked, (_, _, quote_observations, candles, aux, ws) = public_v2._parts(packet)
    starts = (
        *(item.request_started_at for item in quote_observations),
        *(page.request_started_at for frame in candles.frames for page in frame.pages),
        *(item.request_started_at for item in aux.provenance),
        ws.connection_started_at,
    )
    if (
        checked.bundle_sha256 != packet.bundle_sha256
        or not starts
        or any(item <= barrier for item in starts)
    ):
        raise PostG12AccountJoinError("post_g12_public_start_not_after_barrier")
    return min(starts), len(starts)


async def publish_capture_public_account_v2(
    evidence_root,
    public_root,
    account_root,
    original_market,
    *,
    run,
    original_inputs,
    market_policy,
    account_session,
    session_factory,
) -> PostG12OwnedPublicAccountDiagnosticV2:
    """New G12/readback and both fresh native sources in one invocation.

    The original remains caller-origin and every result is a non-transferable
    diagnostic. The public issuer's Demo-origin hard denial is preserved.
    """
    if any(
        type(root) not in (PosixPath, WindowsPath) or not root.is_absolute()
        for root in (evidence_root, public_root, account_root)
    ):
        raise PostG12AccountJoinError("post_g12_account_roots_invalid")
    for first, second in (
        (evidence_root, public_root),
        (evidence_root, account_root),
        (public_root, account_root),
    ):
        original_source._roots(first, second)
    if (
        type(account_session) is not ControlledDemoAccountSession
        or account_session._used
        or not account_native._configured_factory(session_factory)
    ):
        raise PostG12AccountJoinError("post_g12_account_session_invalid")
    plan = account_capture._checked_plan(account_session._plan, account_session._pin)
    if (
        type(plan) is not account_capture.CurrentDemoAccountCapturePlanV6
        or plan.registration_region != "global"
        or plan.settlement_currency != "USDT"
    ):
        raise PostG12AccountJoinError("post_g12_account_scope_unsupported")
    selected = public_v2._policy_copy(market_policy)
    market, pre, inputs = _original(original_market, run, original_inputs)
    intent = inputs["intent"]
    risk_inputs = inputs["risk_inputs"]
    candidate = pre.result
    if not pre.pre_evidence_complete:
        raise PostG12AccountJoinError("post_g12_original_incomplete")
    original_key = event_identity(pre.prefix.detection)
    if (
        original_key is None
        or plan.leverage_instrument_ids is None
        or intent.instrument_id not in plan.leverage_instrument_ids
        or risk_inputs.account is None
        or risk_inputs.account.account_id != plan.expected_uid
        or risk_inputs.account.settlement_currency != plan.settlement_currency
        or risk_inputs.instrument is None
        or risk_inputs.instrument.instrument_id != intent.instrument_id
        or risk_inputs.authority is None
        or risk_inputs.authority.stamp.account_id != plan.expected_uid
        or candidate.candidate_entry != intent.candidate_entry
        or candidate.stop_loss is None
        or candidate.take_profit is None
    ):
        raise PostG12AccountJoinError("post_g12_original_geometry_invalid")
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    candidate_pin = sha(
        canonical(
            {
                "pre_evidence_sha256": pre.evaluation_sha256,
                "event_key": original_key,
                "entry": str(candidate.candidate_entry),
                "stop_loss": str(candidate.stop_loss),
                "take_profit": str(candidate.take_profit),
            }
        )
    )
    invocation = object()
    publication = None
    last = None
    code = "new_g12_required"
    pins = {
        "g12_evidence_sha256": None,
        "g12_report_sha256": None,
        "public_packet_sha256": None,
        "public_journal_sha256": None,
        "account_receipt_sha256": None,
        "account_packet_sha256": None,
    }
    barrier = public_first = account_first = None
    public_count = account_count = 0

    def finish():
        receipt = canonical(
            {
                "schema_version": _SCHEMA,
                "code": code,
                "report_id": intent.report_id,
                "instrument_id": intent.instrument_id,
                "direction": intent.direction,
                "original_entry": str(candidate.candidate_entry),
                "original_stop_loss": str(candidate.stop_loss),
                "original_take_profit": str(candidate.take_profit),
                "candidate_sha256": candidate_pin,
                "original_event_key": original_key,
                "g12_evidence_sha256": pins["g12_evidence_sha256"],
                "g12_report_sha256": pins["g12_report_sha256"],
                "public_packet_sha256": pins["public_packet_sha256"],
                "public_journal_sha256": pins["public_journal_sha256"],
                "account_plan_sha256": account_session._pin,
                "account_receipt_sha256": pins["account_receipt_sha256"],
                "account_packet_sha256": pins["account_packet_sha256"],
                "publication_completed_at": None
                if barrier is None
                else barrier.isoformat(),
                "public_first_request_started_at": None
                if public_first is None
                else public_first.isoformat(),
                "account_first_request_started_at": None
                if account_first is None
                else account_first.isoformat(),
                "observed_at": None
                if last is None
                else utc_from_ns(last["utc_ns"]).isoformat(),
                "public_request_count": public_count,
                "account_page_count": account_count,
                "admission": "DENY",
                **{name: False for name in _FALSE_FIELDS},
            }
        )
        return PostG12OwnedPublicAccountDiagnosticV2(receipt)

    try:
        publication, evidence, origin, last = public_runtime._publish_lineage_v2(
            evidence_root,
            market,
            pre=pre,
            inputs=inputs,
            selected=selected,
            invocation=invocation,
            demo_session=account_session,
        )
        if publication is None or origin is None:
            return finish()
        if (
            origin.candidate.candidate_entry != intent.candidate_entry
            or origin.candidate.stop_loss != candidate.stop_loss
            or origin.candidate.take_profit != candidate.take_profit
            or origin.original_event_key != original_key
            or origin.pre_evidence_sha256 != pre.evaluation_sha256
        ):
            raise PostG12AccountJoinError("post_g12_original_changed")
        barrier = origin.publication_completed_at
        pins["g12_evidence_sha256"] = evidence.evaluation_sha256
        pins["g12_report_sha256"] = evidence.receipt.report_sha256
        code = "public_unavailable"
        carrier = await public_runtime._capture_after_publication_v2(
            publication, selected, public_root
        )
        packet, journal, public_stamp = public_runtime._consume_public_capture_v2(
            carrier, invocation
        )
        validate_stamps((last, public_stamp))
        last = public_stamp
        public_at = utc_from_ns(public_stamp["utc_ns"])
        if public_at >= origin.deadline:
            raise PostG12AccountJoinError("post_g12_public_expired")
        raw = decode(packet.packet_json, public_v2.MAX_PACKET_BYTES)
        if (
            raw["stage"] != "post_publication"
            or raw["environment"] != "demo"
            or raw["report_id"] != intent.report_id
            or raw["instrument_id"] != intent.instrument_id
            or raw["barrier_completed_at"] != barrier.isoformat()
            or raw.get("account_plan_sha256") != account_session._pin
        ):
            raise PostG12AccountJoinError("post_g12_public_lineage_changed")
        public_first, public_count = _public_request_starts(packet, barrier)
        pins["public_packet_sha256"] = packet.bundle_sha256
        pins["public_journal_sha256"] = journal
        account_start = native_stamp()
        validate_stamps((last, account_start))
        last = account_start
        account_start_at = utc_from_ns(account_start["utc_ns"])
        if account_start_at <= barrier or account_start_at >= origin.deadline:
            raise PostG12AccountJoinError("post_g12_account_start_invalid")
        code = "account_unavailable"
        account = await account_native.capture_initial_native_account(
            account_session, session_factory=session_factory, proof_root=account_root
        )
        if type(account) is not account_native.InitialNativeAccountDiagnostic:
            raise PostG12AccountJoinError("post_g12_account_diagnostic_invalid")
        issued = native_stamp()
        validate_stamps((last, issued))
        last = issued
        issued_at = utc_from_ns(issued["utc_ns"])
        if issued_at >= origin.deadline:
            raise PostG12AccountJoinError("post_g12_account_expired")
        reference, account_observed = original_source._account_receipt(
            account.receipt_json,
            plan_pin=account_session._pin,
            public_completed_at=public_at,
            final_at=issued_at,
        )
        owned = account_native._consume_native_demo_raw_packet(account, account_session)
        account_doc = decode(
            account.receipt_json, original_source._MAX_ACCOUNT_RECEIPT_BYTES
        )
        if (
            type(owned) is not account_native._ObservedNativeDemoAccountRawPacket
            or owned.reference != reference
            or owned.receipt_sha256 != account.receipt_sha256
            or owned.proof_sha256 != account_doc["proof_sha256"]
            or owned.readback_sha256 != account_doc["proof_readback_sha256"]
            or owned.observed_at != account_observed
            or owned.packet.plan != plan
            or owned.packet.plan_sha256 != account_session._pin
            or owned.packet.barrier_completed_at <= barrier
            or not account_start_at <= owned.packet.barrier_completed_at
            or owned.packet.completed_at > owned.observed_at
            or owned.expires_at <= issued_at
        ):
            raise PostG12AccountJoinError("post_g12_account_packet_changed")
        pages = owned.packet.observations
        if not pages or any(
            page.request_started_at <= barrier
            or page.request_started_at < account_start_at
            or page.body_completed_at > owned.observed_at
            for page in pages
        ):
            raise PostG12AccountJoinError("post_g12_account_page_time_invalid")
        account_first, account_count = (
            min(page.request_started_at for page in pages),
            len(pages),
        )
        completed = native_stamp()
        validate_stamps((last, completed))
        last = completed
        completed_at = utc_from_ns(completed["utc_ns"])
        if completed_at >= min(origin.deadline, owned.expires_at):
            raise PostG12AccountJoinError("post_g12_join_expired")
        # The public snapshot may age out while private pages are fetched. Replay
        # its live profile at the final join time; no earlier PASS is transferable.
        public_market_context_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            evaluated_at=completed_at,
        )
        pins["account_receipt_sha256"] = account.receipt_sha256
        pins["account_packet_sha256"] = reference.packet_sha256
        code = "joined_unqualified"
        return finish()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- never serialize account/source failures
        if task.cancelling():
            raise asyncio.CancelledError from None
        if barrier is None:
            code = "denied"
        # Incomplete private readback cannot be represented as a complete join.
        account_first = None
        account_count = 0
        pins["account_receipt_sha256"] = None
        pins["account_packet_sha256"] = None
        return finish()
    finally:
        account_session._used = True
        if publication is not None:
            public_runtime._PUBLICATIONS.pop(publication, None)
