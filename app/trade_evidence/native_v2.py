"""Explicit V2 source evidence using the existing six-file renderer/publisher.

The outer image manifest keeps its existing file protocol. Its snapshot uses
an explicit native V2 schema and retains ExecutableQuoteV2, including the
fundingTime/forecast pair. No legacy quote or PreEvidenceRun is constructed.
"""

import hashlib
from dataclasses import fields
from datetime import datetime
from decimal import Context, Decimal, localcontext
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import TypeAdapter, WrapValidator

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha
from app.strategies import structural_protection as _family_selection_v3
from app.strategies.structural_protection import select_structural_protection
from app.trade_evidence import renderer, service
from app.trade_evidence.models import EvidencePanel, EvidenceSnapshot
from app.trade_qualification import owned_current_original_prefix_v1 as _owned_family_v3
from app.trade_qualification.executable_quote_v2 import (
    ExecutableQuoteV2,
    RestMarkObservationV2,
    RestTickerObservationV2,
    inspect_executable_quote_v2,
)
from app.trade_qualification.funding_observation_v2 import (
    FundingForecastV2,
    FundingObservationV2,
)
from app.trade_qualification.location import (
    build_entry_zone,
    build_original_range_anchor_zone,
)
from app.trade_qualification.models import EntryQualificationResult


def _replay_native_evidence_quote_v2(value, handler):
    """Keep every fixed V2 field while reconstructing its original dataclasses.

    Pydantic's strict dataclass revalidation treats init=False metadata as extra
    constructor kwargs. Validate those complete wire fields before removing
    ONLY the already checked fixed constructor arguments. Original V2 inspectors
    replay all funding bytes, hashes, pair semantics, clocks and false flags.
    """
    if type(value) is ExecutableQuoteV2:
        quote = value
    elif type(value) is dict:
        if (
            set(value) != {item.name for item in fields(ExecutableQuoteV2)}
            or type(value.get("funding")) is not dict
            or set(value["funding"])
            != {item.name for item in fields(FundingObservationV2)}
        ):
            raise ValueError("native_v2_complete_quote_wire_required")
        for component, expected_type in (
            (value["ticker"], RestTickerObservationV2),
            (value["mark"], RestMarkObservationV2),
            (value["funding"]["forecast"], FundingForecastV2),
        ):
            if type(component) is not dict or set(component) != {
                item.name for item in fields(expected_type)
            }:
                raise ValueError("native_v2_complete_quote_component_wire_required")
        copied = dict(value)
        funding = dict(value["funding"])
        for document, expected_fields in (
            (
                copied,
                (
                    ("schema_version", "ctcc.executable_quote.v2"),
                    ("region", "global"),
                    ("source_authenticity_verified", False),
                    ("execution_authority", False),
                    ("admission", "DENY"),
                ),
            ),
            (
                funding,
                (
                    ("schema_version", "ctcc.funding_observation.v2"),
                    ("rate_generated_at", None),
                    ("historical_first_available_at", None),
                    ("source_authenticity_verified", False),
                    ("receipt_authenticity_verified", False),
                    ("point_in_time_verified", False),
                    ("account_complete", False),
                    ("execution_authority", False),
                    ("admission", "DENY"),
                ),
            ),
        ):
            for name, expected in expected_fields:
                actual = document[name]
                if type(actual) is not type(expected) or actual != expected:
                    raise ValueError("native_v2_fixed_quote_metadata_changed")
                del document[name]
        copied["funding"] = funding
        # The wrap handler receives parsed Python values; the explicit JSON
        # adapter preserves strict JSON datetime/Decimal/bytes semantics.
        # Its target is the original dataclass, without this evidence wrapper.
        quote = TypeAdapter(ExecutableQuoteV2).validate_json(
            canonical(copied), strict=True
        )
    else:
        raise ValueError("native_v2_exact_quote_type_required")
    if type(quote) is not ExecutableQuoteV2:
        raise ValueError("native_v2_exact_quote_type_required")
    inspected = inspect_executable_quote_v2(
        quote, current_time=quote.capture_completed_at
    )
    if decode(inspected.receipt_json)["profile_satisfied"] is not True:
        raise ValueError("native_v2_quote_observation_replay_invalid")
    return quote


class NativeEvidenceSnapshotV2(EvidenceSnapshot):
    schema_version: Literal["ctcc_trade_evidence_native_v2"] = (
        "ctcc_trade_evidence_native_v2"
    )
    quote: Annotated[ExecutableQuoteV2, WrapValidator(_replay_native_evidence_quote_v2)]


class NativeFamilyEvidenceSnapshotV3(NativeEvidenceSnapshotV2):
    """Explicit history-family DATA snapshot, not a cast to the base evidence type."""

    schema_version: Literal["ctcc_trade_evidence_native_family_v3"] = (
        "ctcc_trade_evidence_native_family_v3"
    )
    qualification: _owned_family_v3.OwnedFamilyEntryQualificationResultV2


def _family_protection_v3(protection, event, market, analysis, qualification):
    expected = {
        "volatility_expansion": _family_selection_v3.StructuralProtectionSelection,
        "structure_reversal": _family_selection_v3.ReversalStructuralProtectionSelection,
        "range_reversal": _family_selection_v3.RangeStructuralProtectionSelection,
        "liquidity_sweep_reversal": _family_selection_v3.SweepStructuralProtectionSelectionV2,
    }.get(qualification.strategy)
    if expected is None or type(protection) is not expected:
        raise service.EvidenceError("native_family_exact_protection_required")
    service._guard(protection)
    policy = dict(protection.policy_inputs)
    if (
        set(policy)
        != {
            "tick_size",
            "expected_slippage_bps",
            "cost_bps",
            "min_net_rr",
            "min_stop_distance_atr",
            "atr_buffer_multiplier",
            "minimum_buffer_bps",
            "spread",
        }
        or len(policy) != len(protection.policy_inputs)
        or protection.observed_at is None
        or protection.observed_at > qualification.evaluated_at
    ):
        raise service.EvidenceError("native_family_original_protection_policy_invalid")
    policy.pop("spread")
    actual = _owned_family_v3._select_owned_family_protection_v2(
        event,
        market,
        analysis,
        observed_at=protection.observed_at,
        entry=qualification.candidate_entry,
        **policy,
    )
    if (
        type(actual) is not expected
        or actual != protection
        or not actual.protection_valid
    ):
        raise service.EvidenceError("native_family_original_protection_replay_changed")
    if (qualification.stop_loss, qualification.take_profit) != (
        actual.selected.stop.final_stop,
        actual.selected.target.final_target,
    ):
        raise service.EvidenceError("native_family_original_geometry_changed")
    if qualification.strategy == "structure_reversal" and (
        actual.history_admission_sha256 != qualification.history_admission_sha256
    ):
        raise service.EvidenceError("native_family_reversal_history_changed")
    if qualification.strategy == "range_reversal" and (
        actual.range_permission_sha256 != qualification.history_admission_sha256
    ):
        raise service.EvidenceError("native_family_range_history_changed")
    if qualification.strategy == "liquidity_sweep_reversal" and (
        actual.sweep_permission_sha256 != qualification.history_admission_sha256
        or actual.sweep_permission_policy_sha256
        != _family_selection_v3.SWEEP_PERMISSION_SWAP_V2_SHA256
        or actual.selection_policy_sha256
        != _family_selection_v3.SWEEP_SELECTION_SWAP_V2_SHA256
        or actual.original_extreme_anchor_id is None
    ):
        raise service.EvidenceError("native_family_sweep_history_changed")
    return actual.to_audit_json(), policy["tick_size"]


def prepare_native_evidence_v2(
    market,
    analysis,
    *,
    qualification,
    detection,
    quote,
    protection,
    prepared_at,
    zone_tick_size,
    candle_limit=80,
):
    if (
        type(qualification)
        not in (
            EntryQualificationResult,
            _owned_family_v3.OwnedFamilyEntryQualificationResultV2,
        )
        or len(qualification.gates) != 11
        or not all(gate.passed for gate in qualification.gates)
        or type(quote) is not ExecutableQuoteV2
        or type(candle_limit) is not int
        or not 80 <= candle_limit <= 200
    ):
        raise service.EvidenceError("native_v2_exact_original_g11_required")
    family = (
        type(qualification) is _owned_family_v3.OwnedFamilyEntryQualificationResultV2
    )
    expected_type = (
        _owned_family_v3.OwnedFamilyEntryQualificationResultV2
        if family
        else EntryQualificationResult
    )
    q = service._copy(qualification, expected_type)
    now = service.require_aware(prepared_at)
    if q.evaluated_at > now:
        raise service.EvidenceError("native_v2_preparation_clock_invalid")
    service._guard(market)
    service._guard(analysis)
    market, analysis, frames, source_sha = service._copy_source(
        market, analysis, q.evaluated_at
    )
    event = service._event(detection, market, analysis, q)
    audit, protection_tick = (
        _family_protection_v3(protection, event, market, analysis, q)
        if family
        else service._protection(protection, event, market, analysis, q)
    )
    if protection_tick != zone_tick_size:
        raise service.EvidenceError("native_v2_original_tick_changed")
    builder = (
        build_original_range_anchor_zone
        if family and q.strategy == "range_reversal"
        else build_entry_zone
    )
    zone, code = builder(
        event,
        tick_size=zone_tick_size,
        max_allowed_drift_bps=q.entry_zone.max_allowed_drift_bps,
        expires_at=q.entry_zone.expires_at,
    )
    if code != "passed" or zone != q.entry_zone:
        raise service.EvidenceError("native_v2_original_zone_changed")
    inspection = inspect_executable_quote_v2(quote, current_time=q.evaluated_at)
    reference = quote.ticker.ask if q.direction == "long" else quote.ticker.bid
    if (
        decode(inspection.receipt_json)["profile_satisfied"] is not True
        or quote.report_id != q.report_id
        or quote.instrument_id != market.instrument_id
        or q.reference_price != reference
    ):
        raise service.EvidenceError("native_v2_original_quote_changed")
    source_json = service._canonical(
        {
            "market": market.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        }
    )
    with localcontext(Context(prec=100)):
        panels = tuple(
            service._panel(tf, frames[tf], q.strategy, candle_limit)
            for tf in service.TIMEFRAMES
        )
    # Pydantic serializes the DECLARED V2 dataclass schema. This never calls a
    # caller-provided conversion or aliases V2 funding data into legacy fields.
    values = {
        "report_id": q.report_id,
        "instrument_id": market.instrument_id,
        "symbol": q.symbol,
        "strategy": q.strategy,
        "direction": q.direction,
        "qualification": q,
        "detection": event,
        "quote": quote,
        "panels": panels,
        "source_sha256": source_sha,
        "candidate_sha256": "0" * 64,
        "source_json": source_json,
        "protection_audit_json": audit,
        "prepared_at": now,
        "purpose": "observed",
        "candle_limit": candle_limit,
        "zone_tick_size": zone_tick_size,
    }
    snapshot_type = (
        NativeFamilyEvidenceSnapshotV3 if family else NativeEvidenceSnapshotV2
    )
    initial = snapshot_type(**values)
    doc = initial.model_dump(mode="json", round_trip=True)
    values["candidate_sha256"] = sha(
        canonical(
            {
                "source_sha256": source_sha,
                "qualification": doc["qualification"],
                "detection": doc["detection"],
                "quote_v2": doc["quote"],
                "protection_audit_json": audit,
                "zone_tick_size": str(zone_tick_size),
            }
        )
    )
    return snapshot_type(**values)


def validate_native_snapshot_v2(snapshot):
    if (
        type(snapshot) not in (NativeEvidenceSnapshotV2, NativeFamilyEvidenceSnapshotV3)
        or set(snapshot.__dict__) != set(type(snapshot).model_fields)
        or snapshot.__pydantic_extra__
        or type(snapshot.qualification)
        is not (
            _owned_family_v3.OwnedFamilyEntryQualificationResultV2
            if type(snapshot) is NativeFamilyEvidenceSnapshotV3
            else EntryQualificationResult
        )
        or type(snapshot.quote) is not ExecutableQuoteV2
        or type(snapshot.panels) is not tuple
        or any(type(panel) is not EvidencePanel for panel in snapshot.panels)
    ):
        raise service.EvidenceError("native_v2_exact_snapshot_required")
    # Validate quote components BEFORE any snapshot serializer traverses them.
    inspect_executable_quote_v2(
        snapshot.quote, current_time=snapshot.qualification.evaluated_at
    )
    for field_name, value in snapshot.__dict__.items():
        if field_name != "quote":
            service._guard(value)
    source = service._decode(snapshot.source_json)
    if (
        set(source) != {"market", "analysis"}
        or service._digest(snapshot.source_json) != snapshot.source_sha256
    ):
        raise service.EvidenceError("native_v2_source_digest_mismatch")
    market = MarketSnapshot.model_validate_json(
        service._canonical(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        service._canonical(source["analysis"]), strict=True
    )
    audit = service._decode(snapshot.protection_audit_json, ascii_only=True)
    policy = {
        name: Decimal(value)
        for name, value in audit["policy_inputs"]
        if name != "spread"
    }
    selector = (
        _owned_family_v3._select_owned_family_protection_v2
        if type(snapshot) is NativeFamilyEvidenceSnapshotV3
        else select_structural_protection
    )
    protection = selector(
        snapshot.detection,
        market,
        analysis,
        observed_at=datetime.fromisoformat(audit["observed_at"]),
        entry=snapshot.qualification.candidate_entry,
        **policy,
    )
    if protection.to_audit_json() != snapshot.protection_audit_json:
        raise service.EvidenceError("native_v2_original_protection_changed")
    rebuilt = prepare_native_evidence_v2(
        market,
        analysis,
        qualification=snapshot.qualification,
        detection=snapshot.detection,
        quote=snapshot.quote,
        protection=protection,
        prepared_at=snapshot.prepared_at,
        zone_tick_size=snapshot.zone_tick_size,
        candle_limit=snapshot.candle_limit,
    )
    if rebuilt != snapshot:
        raise service.EvidenceError("native_v2_snapshot_rebuild_mismatch")
    return rebuilt


def render_native_evidence_v2(snapshot):
    """Reuse the existing chart primitives and exact immutable six-file protocol."""
    checked = validate_native_snapshot_v2(snapshot)
    output, notes = {}, {}
    with localcontext(Context(prec=100)):
        for panel, name in zip(checked.panels, renderer.IMAGE_NAMES[:4], strict=True):
            canvas = renderer._panel(checked, panel)
            output[name] = renderer._png(
                canvas,
                checked,
                name,
                renderer_version=renderer.LIQUIDITY_RENDERER_VERSION,
            )
            notes[name] = sorted(set(canvas.truncated))
        canvas = renderer._summary(checked, liquidity_summary=True)
        output["summary.png"] = renderer._png(
            canvas,
            checked,
            "summary.png",
            renderer_version=renderer.LIQUIDITY_RENDERER_VERSION,
        )
        notes["summary.png"] = sorted(set(canvas.truncated))
    report = {
        "schema_version": "ctcc.trade_evidence.v1",
        "report_id": checked.report_id,
        "snapshot": checked.model_dump(mode="json", round_trip=True),
        "renderer": renderer._renderer_identity(liquidity_summary=True),
        "native_source_contract": "ctcc.native_original_candidate.v2",
        "images": {
            name: {
                "sha256": hashlib.sha256(output[name]).hexdigest(),
                "size_bytes": len(output[name]),
            }
            for name in renderer.IMAGE_NAMES
        },
        "display_overflow_fields": notes,
        "execution_authority": False,
        "gate_assessments_verified": False,
        "source_authenticity_verified": False,
    }
    output["report.json"] = renderer._json_bytes(report)
    if len(output["report.json"]) > renderer.MAX_REPORT_BYTES:
        raise service.EvidenceError("native_v2_report_byte_limit_exceeded")
    return MappingProxyType(output)
