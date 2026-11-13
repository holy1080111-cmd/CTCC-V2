"""Same-session Demo fee GET with original-byte custody; diagnostic only.

An active native V4 current-account owner supplies the exact V7 packet and
credential lease. The returned value is portable and never authorizes a cost,
portfolio revision, qualification, or exchange write.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal
from weakref import WeakKeyDictionary

import httpx

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_positions_history_collector_v1 as signed
from app.trade_qualification import account_swap_fee_source_v1 as fees
from app.trade_qualification import captured_instrument_rules as rules

SCHEMA = "ctcc.demo_swap_fee_native_record.v1"
_FEE_USES = WeakKeyDictionary()


class NativeSwapFeeError(ValueError):
    """Fixed, redacted local refusal codes."""


def _deny(code: str) -> None:
    raise NativeSwapFeeError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class RecordedNativeSwapFeeV1:
    """Durable original fee bytes and provenance, without reusable authority."""

    source_bundle_raw: bytes = field(repr=False)
    source_bundle_sha256: str
    source_receipt_sha256: str
    custody_bundle_id: str
    signed_maker_rate: Decimal = field(repr=False)
    signed_taker_rate: Decimal = field(repr=False)
    transport_classification: Literal["native_tls", "synthetic_mock"]
    account_complete: Literal[False] = field(default=False, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    fee_cost_authority: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _owner(current_source):
    state = (
        current._CURRENT.get(current_source)
        if type(current_source) is current.OwnedCurrentDemoAccountSourceV4
        else None
    )
    if type(state) is not dict:
        _deny("native_fee_current_owner_required")
    current._check_owner(state)
    stamp = native_stamp()
    validate_stamps((state["last"], stamp))
    replay = current.replay_current_account_bundle_v4(
        state["bundle_raw"], expected_sha256=state["bundle_sha256"]
    )
    current._check_current(replay, stamp)
    if (
        state["read_session"]._owner is not state["private_client"]
        or replay.packet.plan.session_binding_id != state["read_session"].binding_id
        or replay.packet.plan.origin != state["read_session"]._origin
    ):
        _deny("native_fee_original_session_changed")
    return state, replay, stamp


def _family(packet, instrument_id):
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    try:
        captured = rules.derive_captured_instrument_rules(
            packet,
            instrument_id=instrument_id,
            expected_plan_sha256=packet.plan_sha256,
            expected_packet_sha256=frozen.sha256,
        )
    except rules.CapturedInstrumentRulesError:
        _deny("native_fee_original_instrument_mapping_missing")
    row = json.loads(captured.raw_row_json)
    family, group_id = row.get("instFamily"), row.get("groupId")
    if (
        type(family) is not str
        or fees._FAMILY.fullmatch(family) is None
        or family != captured.base_currency + "-USDT"
        or type(group_id) is not str
        or fees._GROUP.fullmatch(group_id) is None
    ):
        _deny("native_fee_original_instrument_mapping_missing")
    return frozen, family


def _bundle(state, replay, raw, diagnostic):
    source = canonical(
        {
            "schema_version": SCHEMA,
            "original_current_bundle_sha256": state["bundle_sha256"],
            "original_current_reference_sha256": replay.reference.head_sha256,
            "fee_diagnostic_receipt_hex": diagnostic.receipt_json.hex(),
            "fee_observation": {
                "origin": replay.packet.plan.origin,
                "endpoint": fees.FEE_ENDPOINT,
                "status_code": raw.status_code,
                "content_type": raw.content_type,
                "content_encoding": raw.content_encoding,
                "redirected": raw.redirected,
                "request_started_at": raw.started.isoformat(),
                "headers_received_at": raw.headers.isoformat(),
                "body_completed_at": raw.completed.isoformat(),
                "raw_body_hex": raw.raw.hex(),
                "raw_body_sha256": sha(raw.raw),
                "peer_certificate_sha256": raw.certificate_sha256,
            },
            "source_authenticity_verified": False,
            "fee_cost_authority": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    if len(source) > fees.MAX_FEE_BYTES * 2 + 65536:
        _deny("native_fee_source_byte_limit")
    return source


async def record_native_demo_swap_fee_v1(*, current_source, instrument_id):
    """GET one captured USDT-SWAP family with the original active Demo lease.

    This function has no URL, HTTP client, credential, raw response, clock, or
    authenticity boolean input. Its only network path is the existing isolated
    signed GET transport. MockTransport is a synthetic test seam and remains DENY.
    """
    client = None
    cancelled = False
    try:
        state, replay, first = _owner(current_source)
        packet = replay.packet
        if current_source in _FEE_USES:
            _deny("native_fee_owner_already_attempted")
        frozen, family = _family(packet, instrument_id)
        _FEE_USES[current_source] = instrument_id
        read_session = state["read_session"]
        credentials = state["session"]._credentials
        session_pin = sha(read_session.binding_id.encode("ascii"))
        tokens = list(collector._credential_values(credentials)[:3])
        tokens.append(read_session.binding_id)
        client = collector._new_client()
        collector._client_guard(client)
        if signed.history.MAX_PAGE_BYTES != fees.MAX_FEE_BYTES:
            _deny("native_fee_transport_byte_policy_changed")
        previous = first

        def clock() -> datetime:
            nonlocal previous
            value = native_stamp()
            validate_stamps((previous, value))
            previous = value
            return utc_from_ns(value["utc_ns"])

        remaining = (replay.expires_at - utc_from_ns(first["utc_ns"])).total_seconds()
        if remaining <= 0:
            _deny("native_fee_current_source_expired")
        spec = signed._GetSpec(
            packet.plan.origin,
            fees.FEE_ENDPOINT,
            (("instFamily", family), ("instType", "SWAP")),
        )
        async with asyncio.timeout(min(5, remaining)):
            raw = await signed._signed_get(
                client,
                credentials,
                read_session,
                session_pin,
                clock,
                spec,
                previous_completed=packet.completed_at,
                secret_tokens=tokens,
                capture_envelope=True,
            )
        transport_classification = (
            "synthetic_mock"
            if type(client._transport) is httpx.MockTransport
            else "native_tls"
        )
        # A completed GET must close its isolated connection before any DB
        # custody await. No close failure can be hidden behind a saved receipt.
        closing, client = client, None
        await collector._close(closing)
        later, again, _ = _owner(current_source)
        if (
            later is not state
            or again != replay
            or (
                raw.certificate_sha256 is None
                and transport_classification != "synthetic_mock"
            )
            or raw.status_code != 200
            or type(raw.content_type) is not str
            or raw.content_type.split(";", 1)[0].strip().lower() != "application/json"
            or raw.content_encoding != "identity"
            or raw.redirected is not False
            or not 0 < len(raw.raw) <= fees.MAX_FEE_BYTES
        ):
            _deny("native_fee_original_or_transport_changed")
        observation = fees.SuppliedSwapFeeObservation(
            raw_body=raw.raw,
            origin=spec.origin,
            method="GET",
            endpoint=spec.endpoint,
            parameters=spec.parameters,
            request_started_at=raw.started,
            headers_received_at=raw.headers,
            body_completed_at=raw.completed,
            barrier_completed_at=packet.completed_at,
            status_code=raw.status_code,
            content_type=raw.content_type,
            content_encoding=raw.content_encoding,
            redirected=raw.redirected,
        )
        diagnostic = fees.derive_supplied_swap_fee_source(
            frozen.payload,
            observation,
            instrument_id=instrument_id,
            expected_account_packet_sha256=frozen.sha256,
            expected_account_plan_sha256=packet.plan_sha256,
            expected_fee_raw_sha256=sha(raw.raw),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(
                observation
            ),
        )
        fees.verify_supplied_swap_fee_receipt(
            diagnostic.receipt_json,
            expected_receipt_sha256=diagnostic.receipt_sha256,
            original_account_packet=frozen.payload,
            fee_observation=observation,
            instrument_id=instrument_id,
            expected_account_packet_sha256=frozen.sha256,
            expected_account_plan_sha256=packet.plan_sha256,
            expected_fee_raw_sha256=sha(raw.raw),
            expected_fee_observation_sha256=fees.supplied_observation_sha256(
                observation
            ),
        )
        source = _bundle(state, replay, raw, diagnostic)
        proof = sha(
            canonical(
                {
                    "schema_version": SCHEMA,
                    "original_current_bundle_sha256": state["bundle_sha256"],
                    "fee_source_bundle_sha256": sha(source),
                }
            )
        )
        if _owner(current_source)[0] is not state:
            _deny("native_fee_original_source_changed")
        receipt = await custody.RuntimeBundleCustodyRepository.seal_bundle(
            state["lineage_custody"],
            bundle_raw=source,
            expected_sha256=sha(source),
            proof_envelope_sha256=proof,
        )
        retained = custody._consume_runtime_bundle_custody_receipt(
            receipt,
            expected_bundle_sha256=sha(source),
            expected_proof_envelope_sha256=proof,
        )
        if retained.bundle_raw != source:
            _deny("native_fee_custody_readback_changed")
        if _owner(current_source)[0] is not state:
            _deny("native_fee_original_source_changed")
        return RecordedNativeSwapFeeV1(
            source,
            sha(source),
            diagnostic.receipt_sha256,
            retained.bundle_id,
            diagnostic.signed_maker_rate,
            diagnostic.signed_taker_rate,
            transport_classification,
        )
    except asyncio.CancelledError:
        cancelled = True
        raise
    except NativeSwapFeeError:
        raise
    except Exception:  # noqa: BLE001 -- fixed error, no raw/credential leakage
        _deny("native_fee_source_unavailable")
    finally:
        if client is not None and type(client) is httpx.AsyncClient:
            await collector._close(client, pending_cancel=cancelled)
