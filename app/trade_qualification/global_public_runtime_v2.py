"""Versioned typed ticker readback for the anonymous Global raw journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.domain.source_primitives import canonical, sha
from app.exchange.okx.parsers import parse_swap_ticker_v2
from app.trade_qualification import global_public_runtime_v1 as raw
from app.trade_qualification import quote_collector
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy

_RECEIPT_SCHEMA = "ctcc.public.global_typed_ticker_receipt.v2"
_SuccessCode = Literal[
    "initial_global_public_typed_captured",
    "initial_global_public_typed_replayed",
]
_Refusal = Literal[
    "raw_capture_denied",
    "raw_readback_refused",
    "typed_ticker_incomplete",
]


@dataclass(frozen=True, slots=True)
class GlobalPublicCaptureV2:
    code: Literal["initial_global_public_typed_denied"] | _SuccessCode
    reason: _Refusal | None = None
    plan_sha256: str | None = None
    journal_sha256: str | None = None
    raw_packet_sha256: str | None = None
    typed_receipt_json: bytes | None = field(default=None, repr=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    account_requests: Literal[0] = field(default=0, init=False)
    order_writes: Literal[0] = field(default=0, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    qualification_performed: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def typed_receipt_sha256(self) -> str | None:
        return None if self.typed_receipt_json is None else sha(self.typed_receipt_json)


def _read_typed(
    root: Path,
    *,
    expected_plan_sha256: str,
    expected_journal_sha256: str,
    success_code: _SuccessCode,
) -> GlobalPublicCaptureV2:
    reopened = raw.read_global_public_capture_v1(
        root,
        expected_plan_sha256=expected_plan_sha256,
        expected_journal_sha256=expected_journal_sha256,
    )
    packet = reopened.packet
    if type(packet) is not raw.GlobalPublicComponentPacketV1:
        raise raw.GlobalPublicCaptureError("global_public_readback_unavailable")
    observation = packet.quote.provenance[0]
    if observation.role != "ticker":
        raise raw.GlobalPublicCaptureError("global_public_readback_unavailable")
    try:
        payload, _ = quote_collector._parse(observation.response_body)
        row = quote_collector._row(payload, packet.instrument_id)
        ticker = parse_swap_ticker_v2(row)
    except ValueError:
        return GlobalPublicCaptureV2(
            "initial_global_public_typed_denied",
            reason="typed_ticker_incomplete",
            plan_sha256=expected_plan_sha256,
            journal_sha256=expected_journal_sha256,
            raw_packet_sha256=packet.bundle_sha256,
        )
    packet_sha256 = packet.bundle_sha256
    receipt = canonical(
        {
            "schema_version": _RECEIPT_SCHEMA,
            "environment": packet.environment,
            "instrument_id": packet.instrument_id,
            "plan_sha256": expected_plan_sha256,
            "journal_sha256": expected_journal_sha256,
            "raw_packet_sha256": packet_sha256,
            "ticker_response_sha256": observation.body_sha256,
            "ticker": ticker.model_dump(mode="json"),
            "admission": "DENY",
            "account_requests": 0,
            "order_writes": 0,
            "source_authenticity_verified": False,
            "qualification_performed": False,
            "execution_authority": False,
        }
    )
    return GlobalPublicCaptureV2(
        success_code,
        plan_sha256=expected_plan_sha256,
        journal_sha256=expected_journal_sha256,
        raw_packet_sha256=packet_sha256,
        typed_receipt_json=receipt,
    )


async def capture_initial_global_public_market_v2(
    root: Path, *, instrument_id: str, market_policy: PublicMarketCollectionPolicy
) -> GlobalPublicCaptureV2:
    """Capture once via the owned raw path, then require exact typed readback."""
    capture = await raw.capture_initial_global_public_market_v1(
        root, instrument_id=instrument_id, market_policy=market_policy
    )
    if (
        capture.code != "initial_global_public_captured"
        or capture.plan_sha256 is None
        or capture.journal_sha256 is None
    ):
        return GlobalPublicCaptureV2(
            "initial_global_public_typed_denied", reason="raw_capture_denied"
        )
    try:
        return _read_typed(
            root,
            expected_plan_sha256=capture.plan_sha256,
            expected_journal_sha256=capture.journal_sha256,
            success_code="initial_global_public_typed_captured",
        )
    except raw.GlobalPublicCaptureError:
        return GlobalPublicCaptureV2(
            "initial_global_public_typed_denied", reason="raw_readback_refused"
        )


def read_global_public_capture_v2(
    root: Path, *, expected_plan_sha256: str, expected_journal_sha256: str
) -> GlobalPublicCaptureV2:
    """Replay the raw journal and regenerate the same separately versioned receipt."""
    return _read_typed(
        root,
        expected_plan_sha256=expected_plan_sha256,
        expected_journal_sha256=expected_journal_sha256,
        success_code="initial_global_public_typed_replayed",
    )
