"""Exact CURRENT configuration/isolated leverage DATA, never source authority.

The native issued registry must supply and replay the actual packet. There is
no default net mode, leverage setting request, inferred side or caller override.
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture

_LEVERAGE = re.compile(r"[1-9][0-9]{0,2}(?:\.[0-9]{1,20})?")


class CapturedExecutionModeError(ValueError):
    """Closed failure codes; raw account configuration never escapes."""


def _deny(code="captured_execution_mode_invalid"):
    raise CapturedExecutionModeError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class CapturedLeverageObservationV1:
    instrument_id: str
    margin_mode: str
    position_side: str
    leverage: Decimal
    raw_member_json: bytes
    raw_member_sha256: str
    source_reference_json: bytes


@dataclass(frozen=True, slots=True, repr=False)
class CapturedExecutionModeV1:
    position_mode: str
    account_level: str
    before_config_raw_json: bytes
    after_config_raw_json: bytes
    before_config_source_reference_json: bytes
    after_config_source_reference_json: bytes
    leverage_observations: tuple[CapturedLeverageObservationV1, ...]
    packet_sha256: str
    plan_sha256: str
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def source_authenticity_verified(self):
        return False

    @property
    def execution_authority(self):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class SelectedIsolatedExecutionV1:
    position_mode: str
    position_side: str
    leverage: Decimal
    observation: CapturedLeverageObservationV1
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def execution_authority(self):
        return False


def _reference(packet, page, request_index, row_ordinal, raw, packet_pin):
    return canonical(
        {
            "packet_sha256": packet_pin,
            "packet_record_sha256": packet.packet_sha256,
            "plan_sha256": packet.plan_sha256,
            "stream": page.request.stream,
            "request_index": request_index,
            "row_ordinal": row_ordinal,
            "page_index": page.page_index,
            "row_sha256": sha(raw),
            "page_receipt_sha256": page.receipt_sha256,
            "response_body_sha256": page.body_sha256,
            "page_canonical_sha256": page.canonical_sha256,
            "request_query": list(page.request.parameters),
            "terminal": page.terminal,
            "request_started_at": page.request_started_at.isoformat(),
            "headers_received_at": page.headers_received_at.isoformat(),
            "body_completed_at": page.body_completed_at.isoformat(),
            "publication_barrier": page.barrier_completed_at.isoformat(),
            "endpoint_exchange_asof": None,
        }
    )


def derive_captured_execution_mode_v1(
    packet, *, expected_plan_sha256, expected_packet_sha256
):
    """Rebuild from exact complete raw observations; synthetic data stays DATA."""
    try:
        frozen = capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=expected_plan_sha256
        )
        replay = capture.verify_demo_account_packet(
            frozen.payload,
            expected_sha256=expected_packet_sha256,
            expected_plan_sha256=expected_plan_sha256,
        )
        if (
            type(replay.plan) is not capture.CurrentDemoAccountCapturePlanV7
            or replay.schema_version != "ctcc.demo_current_account_capture.v7"
        ):
            _deny("captured_execution_current_v7_required")
        configs, leverage = {}, []
        seen = set()
        for request_index, page in enumerate(replay.observations):
            stream = page.request.stream
            if stream not in {"config_before", "config_after", "leverage_isolated"}:
                continue
            for row_ordinal, record in enumerate(page.rows):
                raw = record.canonical_json.encode("utf-8")
                row = json.loads(raw)
                reference = _reference(
                    replay,
                    page,
                    request_index,
                    row_ordinal,
                    raw,
                    expected_packet_sha256,
                )
                if stream.startswith("config_"):
                    if stream in configs:
                        _deny("captured_execution_config_not_unique")
                    configs[stream] = (raw, reference, row)
                else:
                    instrument, margin, side, raw_leverage = (
                        row.get("instId"),
                        row.get("mgnMode"),
                        row.get("posSide"),
                        row.get("lever"),
                    )
                    if (
                        type(instrument) is not str
                        or instrument not in replay.plan.leverage_instrument_ids
                        or margin != "isolated"
                        or side not in {"net", "long", "short"}
                        or type(raw_leverage) is not str
                        or _LEVERAGE.fullmatch(raw_leverage) is None
                    ):
                        _deny("captured_isolated_leverage_member_invalid")
                    identity = (instrument, margin, side)
                    value = Decimal(raw_leverage)
                    if identity in seen or not Decimal(1) <= value <= Decimal(125):
                        _deny("captured_isolated_leverage_not_unique_or_bounded")
                    seen.add(identity)
                    leverage.append(
                        CapturedLeverageObservationV1(
                            instrument,
                            margin,
                            side,
                            value,
                            raw,
                            sha(raw),
                            reference,
                        )
                    )
        if (
            set(configs) != {"config_before", "config_after"}
            or not 1 <= len(leverage) <= 128
        ):
            _deny("captured_execution_configuration_incomplete")
        before, after = configs["config_before"], configs["config_after"]
        mode, level = before[2].get("posMode"), before[2].get("acctLv")
        if (
            before[0] != after[0]
            or mode not in {"net_mode", "long_short_mode"}
            or level not in {"1", "2", "3", "4"}
        ):
            _deny("captured_execution_configuration_changed_or_unknown")
        allowed_sides = {"net"} if mode == "net_mode" else {"long", "short"}
        if any(item.position_side not in allowed_sides for item in leverage):
            _deny("captured_leverage_side_conflicts_with_position_mode")
        receipt = canonical(
            {
                "schema_version": "ctcc.captured_execution_mode.v1",
                "packet_sha256": expected_packet_sha256,
                "plan_sha256": expected_plan_sha256,
                "position_mode": mode,
                "account_level": level,
                "before_config_sha256": sha(before[0]),
                "after_config_sha256": sha(after[0]),
                "before_config_source": json.loads(before[1]),
                "after_config_source": json.loads(after[1]),
                "isolated_leverage_sources": [
                    {
                        "instrument_id": item.instrument_id,
                        "position_side": item.position_side,
                        "leverage": str(item.leverage),
                        "raw_member_sha256": item.raw_member_sha256,
                        "source": json.loads(item.source_reference_json),
                    }
                    for item in leverage
                ],
                "source_authenticity_verified": False,
                "execution_authority": False,
            }
        )
        return CapturedExecutionModeV1(
            mode,
            level,
            before[0],
            after[0],
            before[1],
            after[1],
            tuple(leverage),
            expected_packet_sha256,
            expected_plan_sha256,
            receipt,
        )
    except CapturedExecutionModeError:
        raise
    except Exception:  # noqa: BLE001 -- packet errors may contain private raw account values.
        _deny()


def verify_captured_execution_mode_v1(value, packet, **pins):
    if type(value) is not CapturedExecutionModeV1:
        _deny("captured_execution_mode_exact_data_required")
    rebuilt = derive_captured_execution_mode_v1(packet, **pins)
    if value != rebuilt:
        _deny("captured_execution_mode_replay_mismatch")
    return rebuilt


def select_captured_isolated_execution_v1(
    value, *, instrument_id, direction, requested_leverage
):
    """Fixed direction selects the one ACTUAL side, never a default leverage."""
    if (
        type(value) is not CapturedExecutionModeV1
        or type(instrument_id) is not str
        or type(direction) is not str
        or direction not in {"long", "short"}
        or type(requested_leverage) is not int
        or not 1 <= requested_leverage <= 125
        or value.position_mode not in {"net_mode", "long_short_mode"}
        or type(value.leverage_observations) is not tuple
        or not 1 <= len(value.leverage_observations) <= 128
        or any(
            type(item) is not CapturedLeverageObservationV1
            for item in value.leverage_observations
        )
    ):
        _deny("captured_execution_selection_invalid")
    side = "net" if value.position_mode == "net_mode" else direction
    matched = tuple(
        item
        for item in value.leverage_observations
        if type(item) is CapturedLeverageObservationV1
        and item.instrument_id == instrument_id
        and item.margin_mode == "isolated"
        and item.position_side == side
    )
    if len(matched) != 1 or matched[0].leverage != requested_leverage:
        _deny("captured_isolated_leverage_missing_duplicate_or_mismatch")
    selected = matched[0]
    if (
        type(selected.raw_member_json) is not bytes
        or sha(selected.raw_member_json) != selected.raw_member_sha256
    ):
        _deny("captured_isolated_leverage_member_changed")
    receipt = canonical(
        {
            "schema_version": "ctcc.selected_isolated_execution.v1",
            "captured_execution_mode_sha256": value.receipt_sha256,
            "position_mode": value.position_mode,
            "position_side": side,
            "instrument_id": instrument_id,
            "margin_mode": "isolated",
            "leverage": str(selected.leverage),
            "raw_member_sha256": selected.raw_member_sha256,
            "source_reference_sha256": sha(selected.source_reference_json),
            "execution_authority": False,
        }
    )
    return SelectedIsolatedExecutionV1(
        value.position_mode, side, selected.leverage, selected, receipt
    )
