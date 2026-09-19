"""Captured account metadata -> exact row evidence, without source authority.

The runtime calls this after its owned acquisition. Offline replay can perform the
same mapping but cannot acquire that invocation's transport provenance. No price,
contract value, unit, account history or missing product catalog is invented.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_materializer as mapping

MAX_REQUIRED_INSTRUMENTS = 128
_ERRORS = frozenset(
    {
        "captured_metadata_invalid",
        "captured_metadata_scope_mismatch",
        "captured_metadata_budget_exceeded",
        "captured_metadata_type_conflict",
        "supplied_metadata_source_missing",
        "supplied_metadata_source_conflict",
        "duplicate_supplied_metadata_identity",
    }
)


class CapturedMetadataError(ValueError):
    """Static local code only, without private account identifiers or raw rows."""


@dataclass(frozen=True, slots=True, repr=False)
class CapturedInstrumentMetadata:
    """Deterministic mapping evidence; not an authenticated account capability."""

    inputs: mapping.AccountMaterializationInputs
    required_instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    unsupported_instrument_ids: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    receipt_json: bytes
    receipt_sha256: str


def derive_captured_instrument_metadata(
    packet: capture.DemoAccountPacket,
    *,
    expected_plan_sha256: str,
    expected_packet_sha256: str,
    inputs: mapping.AccountMaterializationInputs,
    expected_inputs_sha256: str,
) -> CapturedInstrumentMetadata:
    """Rebuild metadata from captured rows and preserve their exact unit fields.

    Required identities come from the plan and every returned exposure/history
    row, including the aggregate position inventory. This is observed coverage;
    it cannot prove unrequested products or history beyond API retention absent.
    Supplied instrument rows are optional assertions: each supplied field must
    agree exactly with the captured field. Only the full captured row is used.
    """
    error = "captured_metadata_invalid"
    try:
        return _derive(
            packet,
            expected_plan_sha256=expected_plan_sha256,
            expected_packet_sha256=expected_packet_sha256,
            inputs=inputs,
            expected_inputs_sha256=expected_inputs_sha256,
        )
    except CapturedMetadataError as exc:
        if (
            type(exc) is CapturedMetadataError
            and len(exc.args) == 1
            and type(exc.args[0]) is str
            and exc.args[0] in _ERRORS
        ):
            error = exc.args[0]
    except Exception:  # noqa: BLE001, S110 -- redact source/validator exceptions
        pass
    raise CapturedMetadataError(error)


def _derive(
    packet,
    *,
    expected_plan_sha256,
    expected_packet_sha256,
    inputs,
    expected_inputs_sha256,
):
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=expected_plan_sha256
    )
    packet = capture.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=expected_packet_sha256,
        expected_plan_sha256=expected_plan_sha256,
    )
    checked = mapping.copy_materialization_inputs(inputs)
    if (
        type(expected_inputs_sha256) is not str
        or mapping.materialization_inputs_sha256(checked) != expected_inputs_sha256
    ):
        raise CapturedMetadataError("captured_metadata_invalid")
    if (checked.account_id, checked.settlement_currency) != (
        packet.plan.expected_uid,
        packet.plan.settlement_currency,
    ):
        raise CapturedMetadataError("captured_metadata_scope_mismatch")

    required = set(packet.plan.leverage_instrument_ids)
    kinds = {name: {"SWAP"} for name in required}
    captured = {}
    for observation in packet.observations:
        stream = observation.request.stream
        for record in observation.rows:
            row, _ = capture._decode_json(
                record.canonical_json.encode("utf-8"), limit=262144, wire=False
            )
            name = record.instrument_id
            if stream == "account_instruments":
                captured[name] = (record, row, observation)
                continue
            if name is not None:
                required.add(name)
                if row.get("instType") in capture.INSTRUMENT_TYPES:
                    kinds.setdefault(name, set()).add(row["instType"])
            if stream == "account_position_risk":
                for position in row["posData"]:
                    name = position["instId"]
                    required.add(name)
                    if position.get("instType") in capture.INSTRUMENT_TYPES:
                        kinds.setdefault(name, set()).add(position["instType"])
    if len(required) > MAX_REQUIRED_INSTRUMENTS:
        raise CapturedMetadataError("captured_metadata_budget_exceeded")
    for name in required & captured.keys():
        kinds.setdefault(name, set()).add(captured[name][1]["instType"])
    # SPOT and MARGIN can share an instId; both remain unsupported products.
    # They must not be mistaken for a conflicting SWAP contract specification.
    if any("SWAP" in values and len(values) != 1 for values in kinds.values()):
        raise CapturedMetadataError("captured_metadata_type_conflict")

    supplied = set()
    for evidence in checked.instruments:
        row = mapping._raw(evidence)
        name = row.get("instId")
        if type(name) is not str or name not in captured:
            raise CapturedMetadataError("supplied_metadata_source_missing")
        if name in supplied:
            raise CapturedMetadataError("duplicate_supplied_metadata_identity")
        supplied.add(name)
        current = captured[name][1]
        if any(
            key not in current or value != current[key] for key, value in row.items()
        ):
            raise CapturedMetadataError("supplied_metadata_source_conflict")

    missing = tuple(sorted(required - captured.keys()))
    unsupported = tuple(
        sorted(name for name in required if name in kinds and kinds[name] != {"SWAP"})
    )
    gaps = set()
    if missing:
        gaps.add("captured_instrument_metadata_missing")
    if unsupported:
        gaps.add("captured_instrument_product_unsupported")
    unknown_types = tuple(sorted(required - kinds.keys()))
    if unknown_types:
        gaps.add("captured_instrument_type_unknown")
    instruments = []
    bindings = []
    for name in sorted(required & captured.keys()):
        record, row, observation = captured[name]
        raw = record.canonical_json.encode("utf-8")
        # This endpoint does not supply an instrument as-of timestamp. The body
        # completion is the measured row-availability time, not exchange time.
        item = mapping.InstrumentEvidence(
            raw_response=raw,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            observed_at=observation.body_completed_at,
            received_at=observation.body_completed_at,
        )
        instruments.append(item)
        if mapping._fraction(row.get("tickSz"), positive=True) is None:
            gaps.add("captured_instrument_tick_missing")
        bindings.append(
            {
                "instrument_id": name,
                "row_sha256": item.expected_sha256,
                "page_receipt_sha256": observation.receipt_sha256,
                "response_body_sha256": observation.body_sha256,
                "available_at": observation.body_completed_at.isoformat(),
                "timestamp_semantics": "measured_body_completion",
            }
        )
    values = mapping._plain(checked)
    values["instruments"] = tuple(instruments)
    derived = mapping.copy_materialization_inputs(
        mapping.AccountMaterializationInputs.model_validate(values, strict=True)
    )
    receipt = capture._canonical(
        {
            "schema_version": "ctcc.captured_instrument_metadata.v1",
            "plan_sha256": expected_plan_sha256,
            "packet_sha256": expected_packet_sha256,
            "supplied_inputs_sha256": expected_inputs_sha256,
            "derived_inputs_sha256": mapping.materialization_inputs_sha256(derived),
            "required_instrument_ids": sorted(required),
            "missing_instrument_ids": missing,
            "unsupported_instrument_ids": unsupported,
            "unknown_type_instrument_ids": unknown_types,
            "row_bindings": bindings,
            "blocking_reasons": sorted(gaps),
            "coverage_scope": "observed_rows_and_plan_only",
            "source_authenticity_verified": False,
            "account_complete": False,
            "execution_authority": False,
        }
    ).encode("utf-8")
    return CapturedInstrumentMetadata(
        derived,
        tuple(sorted(required)),
        missing,
        unsupported,
        tuple(sorted(gaps)),
        receipt,
        hashlib.sha256(receipt).hexdigest(),
    )
