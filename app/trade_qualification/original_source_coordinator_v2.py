"""One-task, read-only original-source handoff before any candidate or G12.

The native V2 public issuer currently refuses Demo capture before network IO.
This coordinator keeps that refusal. A future reviewed issuer can use the same
task-local handoff to join a fresh public packet to a controlled native account
diagnostic, but the returned receipt is never a qualification or order permit.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import PosixPath, WindowsPath
from typing import Literal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import data, data_v2
from app.trade_qualification import demo_public_origin_preflight as origin_preflight
from app.trade_qualification import native_original_g1_policy_v1 as native_g1
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import qualification_runtime as initial
from app.trade_qualification.account_observation_index import (
    CaptureReference,
    reference_document,
)
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.current_conditions import _evaluate_base_g2_g4
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.models import EntryQualificationResult, GateAssessment
from app.trade_qualification.public_source_runtime import (
    _consume_initial_public_capture_v2,
)
from app.trade_qualification.service import QualificationPrefixPolicy
from app.trade_qualification.sweep_history_permission import (
    POLICY_SHA256 as SWEEP_HISTORY_POLICY_SHA256,
)
from app.trade_qualification.sweep_history_permission import (
    evaluate_sweep_history_permission,
    verify_sweep_history_permission,
)
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

_MAX_RECEIPT_BYTES = 4096
_MAX_ACCOUNT_RECEIPT_BYTES = 65536
_DIGEST_FIELDS = (
    "account_plan_sha256",
    "declared_demo_route_policy_sha256",
    "public_packet_sha256",
    "public_journal_sha256",
    "account_receipt_sha256",
    "account_packet_sha256",
)
_FALSE_FIELDS = (
    "candidate_created",
    "g1_g11_complete",
    "g12_published",
    "account_complete",
    "source_authenticity_verified",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "execution_authority",
    "order_submitted",
)
_CODES = frozenset(
    {
        "original_source_public_unavailable",
        "original_source_account_unavailable",
        "original_sources_observed_g1_candidate_required",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "public_report_id",
        "observed_at",
        "admission",
        *_DIGEST_FIELDS,
        *_FALSE_FIELDS,
    }
)


class OriginalSourceCoordinatorError(ValueError):
    """Static preflight rejection; never includes credentials or source bytes."""


@dataclass(frozen=True, slots=True, repr=False)
class InitialOwnedSourcesDiagnosticV2:
    receipt_json: bytes

    def __post_init__(self):
        if (
            type(self.receipt_json) is not bytes
            or not 0 < len(self.receipt_json) <= _MAX_RECEIPT_BYTES
        ):
            raise OriginalSourceCoordinatorError("original_source_receipt_invalid")
        try:
            raw = decode(self.receipt_json, _MAX_RECEIPT_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _RECEIPT_FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != "ctcc.original_owned_sources_diagnostic.v2"
                or type(raw["code"]) is not str
                or raw["code"] not in _CODES
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or any(
                    type(raw[name]) is not str
                    or len(raw[name]) != 64
                    or any(char not in "0123456789abcdef" for char in raw[name])
                    for name in _DIGEST_FIELDS[:2]
                )
            ):
                raise ValueError
            for name in _DIGEST_FIELDS[2:]:
                value = raw[name]
                if value is not None and (
                    type(value) is not str
                    or len(value) != 64
                    or any(char not in "0123456789abcdef" for char in value)
                ):
                    raise ValueError
            report, observed = raw["public_report_id"], raw["observed_at"]
            if report is not None and (
                type(report) is not str
                or not report.startswith("initial-")
                or not 8 <= len(report) <= 96
                or any(
                    char not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                    for char in report
                )
            ):
                raise ValueError
            if observed is not None:
                if type(observed) is not str or len(observed) > 40:
                    raise ValueError
                at = datetime.fromisoformat(observed)
                if at.utcoffset() is None or at.utcoffset().total_seconds() != 0:
                    raise ValueError
            public_pins = raw["public_packet_sha256"], raw["public_journal_sha256"]
            account_pins = raw["account_receipt_sha256"], raw["account_packet_sha256"]
            if raw["code"] == "original_source_public_unavailable":
                if (
                    public_pins != (None, None)
                    or account_pins != (None, None)
                    or observed is not None
                ):
                    raise ValueError
            elif raw["code"] == "original_source_account_unavailable":
                if (
                    report is None
                    or None in public_pins
                    or account_pins != (None, None)
                ):
                    raise ValueError
            elif (
                report is None
                or None in (*public_pins, *account_pins)
                or observed is None
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- static bounded diagnostic input failure
            raise OriginalSourceCoordinatorError(
                "original_source_receipt_invalid"
            ) from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


_V3_G1_FIELDS = frozenset(
    {
        "g1_policy_record_sha256",
        "g1_policy_sha256",
        "g1_source_sha256",
        "g1_result_sha256",
        "g1_result_code",
        "g1_evaluated_at",
        "g1_evaluated",
        "g1_passed",
    }
)
_V3_CODES = {
    "original_source_public_unavailable",
    "original_source_account_unavailable",
    "original_g1_unavailable",
    "original_g1_rejected",
    "original_sources_g1_observed_candidate_required",
}


@dataclass(frozen=True, slots=True, repr=False)
class InitialOwnedSourcesDiagnosticV3:
    """Replayed native G1 arithmetic, still no original candidate or authority."""

    receipt_json: bytes

    def __post_init__(self):
        try:
            if (
                type(self.receipt_json) is not bytes
                or not 0 < len(self.receipt_json) <= _MAX_RECEIPT_BYTES
            ):
                raise ValueError
            raw = decode(self.receipt_json, _MAX_RECEIPT_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _RECEIPT_FIELDS | _V3_G1_FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"] != "ctcc.original_owned_sources_diagnostic.v3"
                or type(raw["code"]) is not str
                or raw["code"] not in _V3_CODES
                or raw["admission"] != "DENY"
                or any(raw[name] is not False for name in _FALSE_FIELDS)
                or raw["g1_policy_record_sha256"] != native_g1.POLICY_RECORD_SHA256
                or raw["g1_policy_sha256"] != native_g1.DATA_POLICY_SHA256
                or type(raw["g1_evaluated"]) is not bool
                or type(raw["g1_passed"]) is not bool
            ):
                raise ValueError
            # Reuse the old exact receipt checks without altering V2 bytes.
            base = {name: raw[name] for name in _RECEIPT_FIELDS}
            base["schema_version"] = "ctcc.original_owned_sources_diagnostic.v2"
            base["code"] = (
                "original_source_public_unavailable"
                if raw["code"] == "original_source_public_unavailable"
                else "original_sources_observed_g1_candidate_required"
                if raw["code"] == "original_sources_g1_observed_candidate_required"
                else "original_source_account_unavailable"
            )
            InitialOwnedSourcesDiagnosticV2(canonical(base))
            for name in ("g1_policy_record_sha256", "g1_policy_sha256"):
                _digest(raw[name])
            source = raw["g1_source_sha256"]
            if source is not None and (_digest(source) != raw["public_packet_sha256"]):
                raise ValueError
            if raw["public_packet_sha256"] is None and source is not None:
                raise ValueError
            if raw["public_packet_sha256"] is not None and source is None:
                raise ValueError
            if raw["g1_evaluated"]:
                _digest(raw["g1_result_sha256"])
                if (
                    source is None
                    or raw["g1_evaluated_at"] is None
                    or type(raw["g1_result_code"]) is not str
                    or not 1 <= len(raw["g1_result_code"]) <= 96
                    or any(
                        char not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                        for char in raw["g1_result_code"]
                    )
                    or raw["g1_passed"] != (raw["g1_result_code"] == "passed")
                ):
                    raise ValueError
                _utc_text(raw["g1_evaluated_at"])
            elif (
                any(
                    raw[name] is not None
                    for name in (
                        "g1_result_sha256",
                        "g1_result_code",
                        "g1_evaluated_at",
                    )
                )
                or raw["g1_passed"]
            ):
                raise ValueError
            if raw["code"] == "original_g1_unavailable" and (
                source is None
                or raw["g1_evaluated"]
                or raw["account_receipt_sha256"] is not None
                or raw["account_packet_sha256"] is not None
            ):
                raise ValueError
            if raw["code"] == "original_g1_rejected" and (
                not raw["g1_evaluated"]
                or raw["g1_passed"]
                or raw["account_receipt_sha256"] is not None
                or raw["account_packet_sha256"] is not None
            ):
                raise ValueError
            if raw["code"] == "original_source_public_unavailable" and (
                source is not None or raw["g1_evaluated"] or raw["g1_passed"]
            ):
                raise ValueError
            if raw["code"] == "original_source_account_unavailable" and (
                not raw["g1_evaluated"]
                or not raw["g1_passed"]
                or raw["account_receipt_sha256"] is not None
                or raw["account_packet_sha256"] is not None
            ):
                raise ValueError
            if raw["code"] == "original_sources_g1_observed_candidate_required" and (
                not raw["g1_evaluated"] or not raw["g1_passed"]
            ):
                raise ValueError
            if raw["g1_evaluated"] and _utc_text(raw["g1_evaluated_at"]) > _utc_text(
                raw["observed_at"]
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- never expose source or account content
            raise OriginalSourceCoordinatorError(
                "original_source_g1_receipt_invalid"
            ) from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


_V4_PRECURSOR_FIELDS = frozenset(
    {
        "precursor_policy_sha256",
        "precursor_receipt_sha256",
        "precursor_intent_sha256",
        "precursor_action",
        "precursor_code",
        "precursor_intent_derived",
    }
)
_V4_CODES = frozenset(
    {
        "original_source_public_unavailable",
        "original_source_account_unavailable",
        "original_g1_unavailable",
        "original_g1_rejected",
        "original_raw_account_unavailable",
        "original_precursor_unavailable",
        "original_precursor_inspected",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class InitialOwnedPrecursorDiagnosticV4:
    """One-task original arithmetic readback; never a candidate or permit."""

    receipt_json: bytes

    def __post_init__(self):
        try:
            raw = decode(self.receipt_json, _MAX_RECEIPT_BYTES)
            if (
                type(raw) is not dict
                or set(raw) != _RECEIPT_FIELDS | _V3_G1_FIELDS | _V4_PRECURSOR_FIELDS
                or canonical(raw) != self.receipt_json
                or raw["schema_version"]
                != "ctcc.original_owned_precursor_diagnostic.v4"
                or raw["code"] not in _V4_CODES
                or raw["precursor_policy_sha256"] != precursor.POLICY_SHA256
                or type(raw["precursor_intent_derived"]) is not bool
            ):
                raise ValueError
            base = {name: raw[name] for name in _RECEIPT_FIELDS | _V3_G1_FIELDS}
            base["schema_version"] = "ctcc.original_owned_sources_diagnostic.v3"
            if raw["code"] in {
                "original_raw_account_unavailable",
                "original_precursor_unavailable",
                "original_precursor_inspected",
            }:
                base["code"] = "original_sources_g1_observed_candidate_required"
            InitialOwnedSourcesDiagnosticV3(canonical(base))
            if raw["code"] == "original_precursor_inspected":
                _digest(raw["precursor_receipt_sha256"])
                if (
                    type(raw["precursor_action"]) is not str
                    or raw["precursor_action"] not in {"WAIT", "CANCEL", "NO_TRADE"}
                    or type(raw["precursor_code"]) is not str
                    or not 1 <= len(raw["precursor_code"]) <= 96
                    or any(
                        char
                        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
                        for char in raw["precursor_code"]
                    )
                    or raw["precursor_intent_derived"]
                    != (raw["precursor_intent_sha256"] is not None)
                ):
                    raise ValueError
                if raw["precursor_intent_sha256"] is not None:
                    _digest(raw["precursor_intent_sha256"])
            elif (
                any(
                    raw[name] is not None
                    for name in (
                        "precursor_receipt_sha256",
                        "precursor_intent_sha256",
                        "precursor_action",
                        "precursor_code",
                    )
                )
                or raw["precursor_intent_derived"]
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- fixed diagnostic rejection only
            raise OriginalSourceCoordinatorError(
                "original_source_precursor_receipt_invalid"
            ) from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def _digest(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("digest_invalid")
    return value


def _utc_text(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError("utc_invalid")
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None or at.utcoffset().total_seconds() != 0:
        raise ValueError("utc_invalid")
    return at


def _roots(public_root, account_root):
    if any(
        type(root) not in (PosixPath, WindowsPath) or not root.is_absolute()
        for root in (public_root, account_root)
    ):
        raise OriginalSourceCoordinatorError("original_source_absolute_roots_required")
    if any(
        root.anchor.startswith(("\\", "//"))
        or root.drive.startswith("\\")
        or ".." in root.parts
        for root in (public_root, account_root)
    ):
        raise OriginalSourceCoordinatorError("original_source_local_roots_required")
    if (
        public_root == account_root
        or public_root in account_root.parents
        or account_root in public_root.parents
    ):
        raise OriginalSourceCoordinatorError("original_source_roots_overlap")
    try:
        # Native storage opens each component without following reparse points.
        # Reject aliasing here too, before either root gets a source attempt.
        for root in (public_root, account_root):
            for part in (root, *root.parents):
                try:
                    info = part.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(info.st_mode) or (
                    os.name == "nt"
                    and (
                        os.path.isjunction(part)
                        or bool(
                            getattr(info, "st_file_attributes", 0)
                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                        )
                    )
                ):
                    raise OriginalSourceCoordinatorError("original_source_linked_root")
        resolved_public = public_root.resolve(strict=False)
        resolved_account = account_root.resolve(strict=False)
    except OriginalSourceCoordinatorError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise OriginalSourceCoordinatorError(
            "original_source_root_identity_unavailable"
        ) from None
    if (
        resolved_public == resolved_account
        or resolved_public in resolved_account.parents
        or resolved_account in resolved_public.parents
    ):
        raise OriginalSourceCoordinatorError("original_source_resolved_roots_overlap")


def _account_receipt(raw, *, plan_pin, public_completed_at, final_at):
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_ACCOUNT_RECEIPT_BYTES:
        raise OriginalSourceCoordinatorError("original_account_receipt_invalid")
    receipt = decode(raw, _MAX_ACCOUNT_RECEIPT_BYTES)
    if canonical(receipt) != raw or type(receipt) is not dict:
        raise OriginalSourceCoordinatorError("original_account_receipt_invalid")
    reference = CaptureReference(**receipt["source_reference"])
    observed = capture._utc(datetime.fromisoformat(receipt["observed_at"]))
    expires = capture._utc(datetime.fromisoformat(receipt["expires_at"]))
    if (
        receipt.get("schema_version") != "ctcc.initial_native_account_diagnostic.v2"
        or receipt.get("policy_sha256") != account_native.proof.V3_POLICY_SHA256
        or any(
            type(receipt.get(name)) is not str
            or len(receipt[name]) != 64
            or any(char not in "0123456789abcdef" for char in receipt[name])
            for name in (
                "proof_sha256",
                "proof_readback_sha256",
                "current_source_receipt_sha256",
            )
        )
        or receipt.get("current_native_source_observed") is not True
        or receipt.get("native_sampled_hwm_verified") is not False
        or receipt.get("snapshot") is not None
        or receipt.get("account_complete") is not False
        or receipt.get("account_revision_published") is not False
        or receipt.get("flat_start_permission") is not False
        or receipt.get("execution_authority") is not False
        or receipt.get("admission") != "DENY"
        or reference_document(reference) != receipt["source_reference"]
        or reference.plan_sha256 != plan_pin
        or not public_completed_at <= observed <= final_at < expires
    ):
        raise OriginalSourceCoordinatorError("original_account_receipt_invalid")
    return reference, observed


def _replay_owned_original_raw_continuity(
    public_packet,
    account_packet,
    original_precursor,
    *,
    public_pin,
    account_pin,
    plan_pin,
    precursor_inputs,
):
    """Replay both consumed packets while they remain local to the V5 owner.

    This is a source-continuity check, not a source or qualification issuer. It
    returns no packet, intent, receipt, lease or reusable capability.
    """
    if (
        type(public_packet) is not public_v2.CollectedPublicMarketV2
        or type(account_packet) is not capture.DemoAccountPacket
        or type(original_precursor) is not precursor.OriginalCandidatePrecursorV2
        or type(public_packet.packet_json) is not bytes
    ):
        raise OriginalSourceCoordinatorError("original_raw_continuity_invalid")
    public_raw = public_packet.packet_json
    account_raw = capture.freeze_demo_account_packet(
        account_packet, expected_plan_sha256=plan_pin
    )
    if account_raw.sha256 != account_pin:
        raise OriginalSourceCoordinatorError("original_raw_continuity_mismatch")
    replayed_public = public_v2.replay_collected_public_market_v2(
        public_raw, expected_sha256=public_pin
    )
    replayed_account = capture.verify_demo_account_packet(
        account_raw.payload,
        expected_sha256=account_pin,
        expected_plan_sha256=plan_pin,
    )
    if (
        replayed_public.packet_json != public_raw
        or replayed_account != account_packet
        or replayed_account.plan_sha256 != plan_pin
    ):
        raise OriginalSourceCoordinatorError("original_raw_continuity_mismatch")
    replayed_precursor = precursor.verify_original_candidate_precursor_v2(
        original_precursor, replayed_public, replayed_account, **precursor_inputs
    )
    if (
        replayed_precursor.receipt_json != original_precursor.receipt_json
        or replayed_precursor.intent != original_precursor.intent
    ):
        raise OriginalSourceCoordinatorError("original_raw_continuity_mismatch")


async def capture_owned_original_sources_v2(
    public_root,
    account_root,
    *,
    instrument_id,
    market_policy,
    account_session,
    session_factory,
) -> InitialOwnedSourcesDiagnosticV2:
    """Consume native sources in this task and stop before G1/candidate/G12.

    No caller packet, pre-evaluated gate, report ID, clock, barrier, signer or
    client is accepted. The account session is burned on any attempted capture.
    All source/DB/host failures yield a bounded DENY receipt; cancellation escapes.
    """
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=False,
        strategy=None,
    )


async def capture_owned_original_sources_v3(
    public_root,
    account_root,
    *,
    instrument_id,
    market_policy,
    account_session,
    session_factory,
) -> InitialOwnedSourcesDiagnosticV3:
    """Evaluate fixed diagnostic G1 from this invocation's native source only.

    The result never supplies a candidate, G12, risk reservation, intent or order
    permission. The V2 method and its exact receipt bytes remain unchanged.
    """
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=True,
        strategy=None,
    )


async def capture_owned_original_precursor_v4(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> InitialOwnedPrecursorDiagnosticV4:
    """Inspect one source-derived original precursor without granting authority.

    The only candidate operand supplied by the caller is a fixed strategy
    selector. Both raw packets must come from one-use native leases in this
    invocation; a caller packet, receipt, gate, event or bracket cannot enter.
    """
    if type(strategy) is not str or strategy not in precursor.STRATEGY_CATALOG:
        raise OriginalSourceCoordinatorError("original_precursor_strategy_invalid")
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=True,
        strategy=strategy,
    )


async def _capture_owned_original_precursor_for_boundary_v5(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> InitialOwnedPrecursorDiagnosticV4:
    """Keep both raw packets and the precursor in one fixed private invocation.

    The raw values are replayed before this call returns and never leave its
    stack. Only the existing hash-only V4 diagnostic crosses to the V5 boundary.
    """
    if type(strategy) is not str or strategy not in precursor.STRATEGY_CATALOG:
        raise OriginalSourceCoordinatorError("original_precursor_strategy_invalid")
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=True,
        strategy=strategy,
        retain_raw_for_boundary=True,
    )


@dataclass(frozen=True, slots=True, repr=False)
class _OwnedBasePrefixHandoffV6:
    original: InitialOwnedPrecursorDiagnosticV4
    prefix_receipt_json: bytes | None


@dataclass(frozen=True, slots=True, repr=False)
class _OwnedBaseEventHandoffV7:
    base: _OwnedBasePrefixHandoffV6
    event_receipt_json: bytes | None


@dataclass(frozen=True, slots=True, repr=False)
class _OwnedSweepHistoryHandoffV9:
    original: InitialOwnedPrecursorDiagnosticV4
    sweep_receipt_json: bytes | None


def _evaluate_owned_sweep_history_v9(
    public_packet,
    derived,
    *,
    public_pin,
    account_pin,
    plan_pin,
    data_policy,
    created_at,
):
    """Replay V6 sweep evidence inside the owned original-source stack.

    This is a source-lineage inspection. Historical first availability, the
    selected G1--G11 engine, account risk and execution remain unproved.
    """
    if type(derived) is not precursor.OriginalCandidatePrecursorV2:
        raise OriginalSourceCoordinatorError("owned_sweep_precursor_required")
    document = decode(derived.receipt_json, precursor._MAX_RECEIPT)
    if (
        document["strategy"] != "liquidity_sweep_reversal"
        or document["public_bundle_sha256"] != public_pin
        or document["account_packet_sha256"] != account_pin
        or document["account_plan_sha256"] != plan_pin
        or document["created_at"] != created_at.isoformat()
    ):
        raise OriginalSourceCoordinatorError("owned_sweep_lineage_changed")
    history = document["history_admission"]
    if history is None:
        return None
    checked, _ = public_v2._parts(public_packet)
    if checked.bundle_sha256 != public_pin:
        raise OriginalSourceCoordinatorError("owned_sweep_public_changed")
    g1 = data_v2.evaluate_public_market_data_v2(
        checked,
        expected_bundle_sha256=public_pin,
        policy=data_policy,
        evaluated_at=created_at,
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        checked,
        expected_bundle_sha256=public_pin,
        policy=data_policy,
        evaluated_at=created_at,
    )
    if (
        not g1.passed
        or g1.evaluation_sha256 != document["g1"]["evaluation_sha256"]
        or g1.source_sha256 != document["g1"]["source_sha256"]
    ):
        raise OriginalSourceCoordinatorError("owned_sweep_g1_changed")
    source = decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"], allow_nan=False), strict=True
    )
    inputs = {
        "report_id": document["report_id"],
        "direction": document["direction"],
        "observed_at": created_at,
        "analysis_version": data_policy.analysis_version,
        "expected_policy_sha256": SWEEP_HISTORY_POLICY_SHA256,
    }
    measured = evaluate_sweep_history_permission(market, **inputs)
    replayed = verify_sweep_history_permission(measured, market, **inputs)
    measured_history = decode(replayed.receipt_json, 4 * 1024 * 1024)
    if (
        history != measured_history
        or measured_history["source_sha256"] != g1.source_sha256
        or measured_history["event_key"] != document["event_key"]
        or measured_history["policy_sha256"] != SWEEP_HISTORY_POLICY_SHA256
    ):
        raise OriginalSourceCoordinatorError("owned_sweep_history_changed")
    intent = derived.intent
    if (intent is None) is not (document["intent"] is None):
        raise OriginalSourceCoordinatorError("owned_sweep_intent_changed")
    if intent is not None and document["intent"] != precursor._intent_tree(intent):
        raise OriginalSourceCoordinatorError("owned_sweep_intent_changed")
    return canonical(
        {
            "schema_version": "ctcc.owned_original_sweep_history_inspection.v9",
            "precursor_receipt_sha256": derived.receipt_sha256,
            "public_packet_sha256": public_pin,
            "account_packet_sha256": account_pin,
            "account_plan_sha256": plan_pin,
            "g1_evaluation_sha256": g1.evaluation_sha256,
            "g1_source_sha256": g1.source_sha256,
            "sweep_policy_sha256": SWEEP_HISTORY_POLICY_SHA256,
            "sweep_permission_sha256": replayed.evaluation_sha256,
            "sweep_code": replayed.code,
            "sweep_admitted": replayed.admitted,
            "event_key_sha256": measured_history["event_key"],
            "intent_sha256": None
            if intent is None
            else sha(canonical(document["intent"])),
            "event_expires_at": document["original_event_expires_at"],
            "event_prefix_witness_sha256": None
            if document["event_prefix_witness"] is None
            else sha(canonical(document["event_prefix_witness"])),
            "historical_first_availability_verified": False,
            "original_source_verified": False,
            "candidate_created": False,
            "g1_g11_complete": False,
            "g12_published": False,
            "account_complete": False,
            "execution_recheck_performed": False,
            "atomic_risk_reserved": False,
            "execution_authority": False,
            "order_submitted": False,
            "admission": "DENY",
        }
    )


def _evaluate_owned_base_prefix_v6(
    public_packet,
    account_packet,
    derived,
    *,
    public_pin,
    account_pin,
    plan_pin,
    data_policy,
    created_at,
):
    """Replay the native base G1--G4 arithmetic, never a qualification run."""
    if (
        type(derived) is not precursor.OriginalCandidatePrecursorV2
        or derived.intent is None
        or derived.intent.strategy not in precursor._BASE_STRATEGIES
    ):
        raise OriginalSourceCoordinatorError("original_base_prefix_intent_required")
    document = decode(derived.receipt_json, precursor._MAX_RECEIPT)
    intent = derived.intent
    if (
        document["public_bundle_sha256"] != public_pin
        or document["account_packet_sha256"] != account_pin
        or document["account_plan_sha256"] != plan_pin
        or document["created_at"] != created_at.isoformat()
        or document["intent"] != precursor._intent_tree(intent)
    ):
        raise OriginalSourceCoordinatorError("original_base_prefix_lineage_changed")
    checked, (_, quote, _, _, _, _) = public_v2._parts(public_packet)
    if checked.bundle_sha256 != public_pin:
        raise OriginalSourceCoordinatorError("original_base_prefix_public_changed")
    g1 = data_v2.evaluate_public_market_data_v2(
        checked,
        expected_bundle_sha256=public_pin,
        policy=data_policy,
        evaluated_at=created_at,
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        checked,
        expected_bundle_sha256=public_pin,
        policy=data_policy,
        evaluated_at=created_at,
    )
    if (
        not g1.passed
        or g1.evaluation_sha256 != document["g1"]["evaluation_sha256"]
        or g1.source_sha256 != document["g1"]["source_sha256"]
    ):
        raise OriginalSourceCoordinatorError("original_base_prefix_g1_changed")
    rules = derive_captured_instrument_rules(
        account_packet,
        instrument_id=intent.instrument_id,
        expected_plan_sha256=plan_pin,
        expected_packet_sha256=account_pin,
    )
    if rules.receipt_sha256 != document["instrument_rules_sha256"]:
        raise OriginalSourceCoordinatorError("original_base_prefix_rules_changed")
    source = decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"], allow_nan=False), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"], allow_nan=False), strict=True
    )
    policy = QualificationPrefixPolicy(
        policy_id="ctcc-native-original-base-g1-g4-inspection-v6",
        data=data_policy,
        minimum_score=85,
        tick_size=rules.tick_size,
        max_allowed_drift_bps=Decimal(30),
        maximum_strategy_spread_bps=Decimal(3),
        maximum_adverse_funding_bps=Decimal(5),
    )
    gates = [g1.gate]
    values = {
        "report_id": intent.report_id,
        "symbol": market.symbol,
        "strategy": intent.strategy,
        "direction": intent.direction,
        "evaluated_at": created_at,
        "candidate_entry": intent.candidate_entry,
        "raw_score": 0,
        "effective_score": 0,
    }

    def gate(kind, code, reason, measured):
        item = GateAssessment(
            report_id=intent.report_id,
            gate=kind,
            passed=code == "passed",
            code=code,
            reason=reason,
            measured_values=measured,
        )
        gates.append(item)
        return item.passed

    _evaluate_base_g2_g4(
        market,
        analysis,
        intent=intent,
        policy=policy,
        source_sha256=g1.source_sha256,
        quote_bundle_sha256=g1.quote_bundle_sha256,
        bid=quote.ticker.bid,
        ask=quote.ticker.ask,
        funding_rate=quote.funding.forecast.rate,
        values=values,
        gate=gate,
    )
    result = EntryQualificationResult(**values, gates=tuple(gates))
    if (
        len(result.gates) != 4
        or not all(item.passed for item in result.gates)
        or result.raw_score != document["conditions"]["score"]
        or result.direction != document["direction"]
        or result.candidate_entry != intent.candidate_entry
    ):
        raise OriginalSourceCoordinatorError("original_base_prefix_precursor_mismatch")
    return canonical(
        {
            "schema_version": "ctcc.owned_original_base_prefix_inspection.v6",
            "precursor_receipt_sha256": derived.receipt_sha256,
            "precursor_intent_sha256": sha(canonical(document["intent"])),
            "public_packet_sha256": public_pin,
            "account_packet_sha256": account_pin,
            "instrument_rules_sha256": rules.receipt_sha256,
            "g1_result_sha256": g1.evaluation_sha256,
            "g1_source_sha256": g1.source_sha256,
            "prefix_policy_sha256": sha(
                canonical(policy.model_dump(mode="json", round_trip=True))
            ),
            "result_sha256": sha(
                canonical(result.model_dump(mode="json", round_trip=True))
            ),
            "gates": [
                {"gate": item.gate.value, "code": item.code, "passed": item.passed}
                for item in result.gates
            ],
            "calibrated_for_trading": False,
            "original_source_verified": False,
            "candidate_created": False,
            "g1_g11_complete": False,
            "g12_published": False,
            "account_complete": False,
            "qualification_performed": False,
            "execution_recheck_performed": False,
            "atomic_risk_reserved": False,
            "execution_authority": False,
            "order_submitted": False,
            "admission": "DENY",
        }
    )


def _evaluate_owned_base_event_v7(
    public_packet,
    derived,
    *,
    public_pin,
    data_policy,
    created_at,
    prefix_receipt_json,
):
    """Replay the fixed G5 event from the same raw packet; never grant authority."""
    if (
        type(derived) is not precursor.OriginalCandidatePrecursorV2
        or derived.intent is None
        or derived.intent.strategy not in precursor._BASE_STRATEGIES
        or type(prefix_receipt_json) is not bytes
    ):
        raise OriginalSourceCoordinatorError("original_base_event_input_invalid")
    document = decode(derived.receipt_json, precursor._MAX_RECEIPT)
    intent = derived.intent
    prefix = decode(prefix_receipt_json, 4096)
    if (
        document["public_bundle_sha256"] != public_pin
        or document["created_at"] != created_at.isoformat()
        or document["intent"] != precursor._intent_tree(intent)
        or prefix.get("schema_version")
        != "ctcc.owned_original_base_prefix_inspection.v6"
        or prefix.get("precursor_receipt_sha256") != derived.receipt_sha256
        or prefix.get("public_packet_sha256") != public_pin
        or prefix.get("gates")
        != [
            {"gate": f"G{number}", "code": "passed", "passed": True}
            for number in range(1, 5)
        ]
        or prefix.get("admission") != "DENY"
    ):
        raise OriginalSourceCoordinatorError("original_base_event_lineage_changed")
    checked, (_, _, _, candle_packet, _, _) = public_v2._parts(public_packet)
    if checked.bundle_sha256 != public_pin:
        raise OriginalSourceCoordinatorError("original_base_event_public_changed")
    g1 = data_v2.evaluate_public_market_data_v2(
        checked,
        expected_bundle_sha256=public_pin,
        policy=data_policy,
        evaluated_at=created_at,
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        checked,
        expected_bundle_sha256=public_pin,
        policy=data_policy,
        evaluated_at=created_at,
    )
    if (
        not g1.passed
        or g1.evaluation_sha256 != document["g1"]["evaluation_sha256"]
        or g1.source_sha256 != document["g1"]["source_sha256"]
        or g1.evaluation_sha256 != prefix.get("g1_result_sha256")
        or g1.source_sha256 != prefix.get("g1_source_sha256")
    ):
        raise OriginalSourceCoordinatorError("original_base_event_g1_changed")
    source = decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"], allow_nan=False), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"], allow_nan=False), strict=True
    )
    timing_policy = TIMING_POLICIES[intent.strategy]
    detection = extract_trigger(
        market,
        analysis,
        report_id=intent.report_id,
        strategy=intent.strategy,
        direction=intent.direction,
        observed_at=created_at,
        trigger_ttl_seconds=timing_policy.trigger_ttl_seconds,
    )
    trigger = detection.trigger
    key = event_identity(detection)
    if (
        detection.model_dump(mode="json") != document["detection"]
        or detection.source_sha256 != g1.source_sha256
        or detection.fail_codes
        or trigger is None
        or detection.setup_time is None
        or trigger.invalidation_reason is not None
        or trigger.trigger_time > created_at
        or created_at >= min(trigger.expires_at, intent.expires_at)
        or key is None
        or key != document["event_key"]
        or trigger.expires_at.isoformat() != document["original_event_expires_at"]
    ):
        raise OriginalSourceCoordinatorError("original_base_event_changed")
    timeline = precursor._timeline(candle_packet, created_at)
    if (
        timeline is None
        or timeline != document["timeline"]
        or sha(canonical(timeline)) != document["timeline_sha256"]
    ):
        raise OriginalSourceCoordinatorError("original_base_event_timeline_changed")
    witness = precursor._prefix_witness(timeline, detection)
    if witness is None or witness != document["event_prefix_witness"]:
        raise OriginalSourceCoordinatorError("original_base_event_witness_changed")
    return canonical(
        {
            "schema_version": "ctcc.owned_original_base_event_inspection.v7",
            "precursor_receipt_sha256": derived.receipt_sha256,
            "precursor_intent_sha256": sha(canonical(document["intent"])),
            "public_packet_sha256": public_pin,
            "base_prefix_receipt_sha256": sha(prefix_receipt_json),
            "g1_result_sha256": g1.evaluation_sha256,
            "g1_source_sha256": g1.source_sha256,
            "detection_sha256": sha(canonical(document["detection"])),
            "event_key_sha256": key,
            "timeline_sha256": document["timeline_sha256"],
            "event_prefix_witness_sha256": sha(canonical(witness)),
            "trigger_expires_at": trigger.expires_at.isoformat(),
            "gate": {"gate": "G5", "code": "passed", "passed": True},
            "historical_first_availability_verified": False,
            "event_ledger_authenticated": False,
            "g6_evaluated": False,
            "g7_evaluated": False,
            "calibrated_for_trading": False,
            "original_source_verified": False,
            "candidate_created": False,
            "g1_g11_complete": False,
            "g12_published": False,
            "account_complete": False,
            "qualification_performed": False,
            "execution_recheck_performed": False,
            "atomic_risk_reserved": False,
            "execution_authority": False,
            "order_submitted": False,
            "admission": "DENY",
        }
    )


async def _capture_owned_original_base_prefix_for_boundary_v6(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> _OwnedBasePrefixHandoffV6:
    if type(strategy) is not str or strategy not in precursor._BASE_STRATEGIES:
        raise OriginalSourceCoordinatorError(
            "original_base_prefix_strategy_unsupported"
        )
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=True,
        strategy=strategy,
        retain_raw_for_boundary=True,
        inspect_base_prefix=True,
    )


async def _capture_owned_original_base_event_for_boundary_v7(
    public_root,
    account_root,
    *,
    instrument_id,
    strategy,
    market_policy,
    account_session,
    session_factory,
) -> _OwnedBaseEventHandoffV7:
    if type(strategy) is not str or strategy not in precursor._BASE_STRATEGIES:
        raise OriginalSourceCoordinatorError("original_base_event_strategy_unsupported")
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=True,
        strategy=strategy,
        retain_raw_for_boundary=True,
        inspect_base_prefix=True,
        inspect_base_event=True,
    )


async def _capture_owned_original_sweep_history_for_boundary_v9(
    public_root,
    account_root,
    *,
    instrument_id,
    market_policy,
    account_session,
    session_factory,
) -> _OwnedSweepHistoryHandoffV9:
    return await _capture_owned_original_sources(
        public_root,
        account_root,
        instrument_id=instrument_id,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        inspect_g1=True,
        strategy="liquidity_sweep_reversal",
        retain_raw_for_boundary=True,
        inspect_sweep_history=True,
    )


async def _capture_owned_original_sources(
    public_root,
    account_root,
    *,
    instrument_id,
    market_policy,
    account_session,
    session_factory,
    inspect_g1,
    strategy,
    retain_raw_for_boundary=False,
    inspect_base_prefix=False,
    inspect_base_event=False,
    inspect_sweep_history=False,
):
    if (
        type(inspect_g1) is not bool
        or type(retain_raw_for_boundary) is not bool
        or type(inspect_base_prefix) is not bool
        or type(inspect_base_event) is not bool
        or type(inspect_sweep_history) is not bool
        or inspect_base_event
        and not inspect_base_prefix
        or inspect_sweep_history
        and (
            not retain_raw_for_boundary
            or inspect_base_prefix
            or inspect_base_event
            or strategy != "liquidity_sweep_reversal"
        )
        or inspect_base_prefix
        and (not retain_raw_for_boundary or strategy not in precursor._BASE_STRATEGIES)
        or retain_raw_for_boundary
        and strategy is None
        or strategy is not None
        and (
            not inspect_g1
            or type(strategy) is not str
            or strategy not in precursor.STRATEGY_CATALOG
        )
    ):
        raise OriginalSourceCoordinatorError("original_g1_mode_invalid")
    _roots(public_root, account_root)
    if (
        type(instrument_id) is not str
        or instrument_id not in REVIEWED_DEMO_INSTRUMENT_IDS
        or type(account_session) is not ControlledDemoAccountSession
        or account_session._used
        or not account_native._configured_factory(session_factory)
    ):
        raise OriginalSourceCoordinatorError("original_source_inputs_invalid")
    try:
        plan = capture._checked_plan(account_session._plan, account_session._pin)
        if (
            type(plan) is not capture.CurrentDemoAccountCapturePlanV6
            or plan.registration_region != "global"
            or plan.settlement_currency != "USDT"
        ):
            raise OriginalSourceCoordinatorError(
                "original_source_account_scope_unsupported"
            )
        selected = public_v2._policy_copy(market_policy)
        g1_policy = native_g1.fixed_native_original_g1_policy() if inspect_g1 else None
        prepared = origin_preflight._prepare_controlled_demo_route(account_session)
        route, bound_plan_pin = origin_preflight._consume_controlled_demo_route(
            prepared, account_session
        )
        if bound_plan_pin != account_session._pin:
            raise OriginalSourceCoordinatorError("original_source_region_mismatch")
    except OriginalSourceCoordinatorError:
        raise
    except Exception:  # noqa: BLE001 -- never expose private plan/credential text
        raise OriginalSourceCoordinatorError("original_source_inputs_invalid") from None
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError

    plan_pin = account_session._pin
    route_pin = sha(
        canonical(
            {
                "registration_region": route.registration_region,
                "rest_origin": route.rest_origin,
                "ws_origin": route.ws_origin,
            }
        )
    )
    invocation = object()
    code = "original_source_public_unavailable"
    report = public_pin = public_journal = account_pin = account_packet_pin = None
    started = public_observed = finished = None
    g1_source_pin = g1_result_pin = g1_result_code = g1_evaluated_at = None
    g1_evaluated = g1_passed = False
    precursor_pin = precursor_intent_pin = precursor_action = precursor_code = None
    precursor_intent_derived = False
    prefix_receipt_json = None
    event_receipt_json = None
    sweep_receipt_json = None

    def result():
        fields = {
            "schema_version": (
                "ctcc.original_owned_precursor_diagnostic.v4"
                if strategy is not None
                else "ctcc.original_owned_sources_diagnostic.v3"
                if inspect_g1
                else "ctcc.original_owned_sources_diagnostic.v2"
            ),
            "code": code,
            "account_plan_sha256": plan_pin,
            "declared_demo_route_policy_sha256": route_pin,
            "public_report_id": report,
            "public_packet_sha256": public_pin,
            "public_journal_sha256": public_journal,
            "account_receipt_sha256": account_pin,
            "account_packet_sha256": account_packet_pin,
            "observed_at": None
            if finished is None
            else utc_from_ns(finished["utc_ns"]).isoformat(),
            "candidate_created": False,
            "g1_g11_complete": False,
            "g12_published": False,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_recheck_performed": False,
            "atomic_risk_reserved": False,
            "execution_authority": False,
            "order_submitted": False,
            "admission": "DENY",
        }
        if inspect_g1:
            fields.update(
                g1_policy_record_sha256=native_g1.POLICY_RECORD_SHA256,
                g1_policy_sha256=native_g1.DATA_POLICY_SHA256,
                g1_source_sha256=g1_source_pin,
                g1_result_sha256=g1_result_pin,
                g1_result_code=g1_result_code,
                g1_evaluated_at=g1_evaluated_at,
                g1_evaluated=g1_evaluated,
                g1_passed=g1_passed,
            )
        if strategy is not None:
            fields.update(
                precursor_policy_sha256=precursor.POLICY_SHA256,
                precursor_receipt_sha256=precursor_pin,
                precursor_intent_sha256=precursor_intent_pin,
                precursor_action=precursor_action,
                precursor_code=precursor_code,
                precursor_intent_derived=precursor_intent_derived,
            )
        receipt = canonical(fields)
        diagnostic = (
            InitialOwnedPrecursorDiagnosticV4(receipt)
            if strategy is not None
            else InitialOwnedSourcesDiagnosticV3(receipt)
            if inspect_g1
            else InitialOwnedSourcesDiagnosticV2(receipt)
        )
        if inspect_base_event:
            return _OwnedBaseEventHandoffV7(
                _OwnedBasePrefixHandoffV6(diagnostic, prefix_receipt_json),
                event_receipt_json,
            )
        if inspect_sweep_history:
            return _OwnedSweepHistoryHandoffV9(diagnostic, sweep_receipt_json)
        if inspect_base_prefix:
            return _OwnedBasePrefixHandoffV6(diagnostic, prefix_receipt_json)
        return diagnostic

    try:
        started = native_stamp()
        carrier, report, expires = await initial._capture_initial_lineage_v2(
            public_root,
            instrument_id=instrument_id,
            market_policy=selected,
            invocation=invocation,
        )
        packet, journal, public_observed = _consume_initial_public_capture_v2(
            carrier, invocation
        )
        validate_stamps((started, public_observed))
        at = utc_from_ns(public_observed["utc_ns"])
        if at >= expires:
            raise OriginalSourceCoordinatorError("original_public_expired")
        context = public_market_context_v2(
            packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=at
        )
        public_doc = decode(packet.packet_json, public_v2.MAX_PACKET_BYTES)
        if (
            public_doc.get("stage") != "initial_public"
            or public_doc.get("barrier_completed_at") is not None
            or public_doc.get("environment") != "demo"
            or public_doc.get("instrument_id") != instrument_id
            or public_doc.get("report_id") != report
            or context.packet_sha256 != packet.bundle_sha256
        ):
            raise OriginalSourceCoordinatorError("original_public_scope_invalid")
        public_pin = packet.bundle_sha256
        public_journal = journal
        code = "original_source_account_unavailable"
        if inspect_g1:
            # The one-use carrier above supplied this exact native packet. The
            # fixed policy is rebuilt in preflight; no caller G1/market/PASS
            # value enters this calculation or its independent raw-byte replay.
            g1_source_pin = public_pin
            finished = public_observed
            code = "original_g1_unavailable"
            evaluated = data_v2.evaluate_public_market_data_v2(
                packet,
                expected_bundle_sha256=public_pin,
                policy=g1_policy,
                evaluated_at=at,
            )
            replayed = data_v2.verify_public_market_data_v2(
                evaluated,
                packet,
                expected_bundle_sha256=public_pin,
                policy=g1_policy,
                evaluated_at=at,
            )
            if (
                replayed.public_bundle_sha256 != public_pin
                or replayed.policy_sha256 != native_g1.DATA_POLICY_SHA256
            ):
                raise OriginalSourceCoordinatorError("original_g1_replay_mismatch")
            g1_result_pin = replayed.evaluation_sha256
            g1_result_code = replayed.gate.code
            g1_evaluated_at = at.isoformat()
            g1_evaluated = True
            g1_passed = replayed.passed
            if not g1_passed:
                code = "original_g1_rejected"
                return result()
            code = "original_source_account_unavailable"
        _roots(public_root, account_root)
        account = await account_native.capture_initial_native_account(
            account_session, session_factory=session_factory, proof_root=account_root
        )
        if type(account) is not account_native.InitialNativeAccountDiagnostic:
            raise OriginalSourceCoordinatorError("original_account_result_invalid")
        final_stamp = native_stamp()
        validate_stamps((public_observed, final_stamp))
        finished = final_stamp
        final_at = utc_from_ns(finished["utc_ns"])
        if final_at >= expires:
            raise OriginalSourceCoordinatorError("original_public_expired")
        reference, account_observed = _account_receipt(
            account.receipt_json,
            plan_pin=plan_pin,
            public_completed_at=at,
            final_at=final_at,
        )
        account_pin = account.receipt_sha256
        account_packet_pin = reference.packet_sha256
        if strategy is not None:
            code = "original_raw_account_unavailable"
            owned = account_native._consume_native_demo_raw_packet(
                account, account_session
            )
            account_doc = decode(account.receipt_json, _MAX_ACCOUNT_RECEIPT_BYTES)
            if (
                type(owned) is not account_native._ObservedNativeDemoAccountRawPacket
                or owned.receipt_sha256 != account_pin
                or owned.reference != reference
                or owned.proof_sha256 != account_doc["proof_sha256"]
                or owned.readback_sha256 != account_doc["proof_readback_sha256"]
                or owned.observed_at != account_observed
                or not at <= owned.packet.completed_at <= owned.observed_at
                or owned.packet.plan != plan
                or owned.packet.plan_sha256 != plan_pin
                or owned.expires_at <= final_at
            ):
                raise OriginalSourceCoordinatorError("original_raw_account_mismatch")
            created_stamp = native_stamp()
            validate_stamps((finished, created_stamp))
            finished = created_stamp
            created_at = utc_from_ns(created_stamp["utc_ns"])
            if created_at >= min(expires, owned.expires_at):
                raise OriginalSourceCoordinatorError("original_precursor_expired")
            code = "original_precursor_unavailable"
            inputs = {
                "strategy": strategy,
                "expected_public_bundle_sha256": public_pin,
                "expected_account_plan_sha256": plan_pin,
                "expected_account_packet_sha256": account_packet_pin,
                "data_policy": g1_policy,
                "expected_data_policy_sha256": native_g1.DATA_POLICY_SHA256,
                "created_at": created_at,
                "service_deadline": min(expires, owned.expires_at),
            }
            derived = precursor.derive_original_candidate_precursor_v2(
                packet, owned.packet, **inputs
            )
            replayed = precursor.verify_original_candidate_precursor_v2(
                derived, packet, owned.packet, **inputs
            )
            if derived.receipt_json != replayed.receipt_json:
                raise OriginalSourceCoordinatorError(
                    "original_precursor_replay_changed"
                )
            completed_stamp = native_stamp()
            validate_stamps((created_stamp, completed_stamp))
            completed_at = utc_from_ns(completed_stamp["utc_ns"])
            if completed_at >= min(expires, owned.expires_at):
                raise OriginalSourceCoordinatorError("original_precursor_expired")
            precursor_doc = decode(replayed.receipt_json, precursor._MAX_RECEIPT)
            if (
                precursor_doc["public_bundle_sha256"] != public_pin
                or precursor_doc["account_plan_sha256"] != plan_pin
                or precursor_doc["account_packet_sha256"] != account_packet_pin
                or precursor_doc["report_id"] != report
                or precursor_doc["instrument_id"] != instrument_id
                or precursor_doc["strategy"] != strategy
                or precursor_doc["data_policy_sha256"] != native_g1.DATA_POLICY_SHA256
                or precursor_doc["g1"]["policy_sha256"] != native_g1.DATA_POLICY_SHA256
                or precursor_doc["g1"]["passed"] is not True
                or precursor_doc["admission"] != "DENY"
                or precursor_doc["execution_authority"] is not False
            ):
                raise OriginalSourceCoordinatorError("original_precursor_scope_changed")
            finished = completed_stamp
            if retain_raw_for_boundary:
                # Keep exact native packet bytes and the verified precursor in
                # this frame. Never return a raw packet or a transferable lease.
                _replay_owned_original_raw_continuity(
                    packet,
                    owned.packet,
                    replayed,
                    public_pin=public_pin,
                    account_pin=account_packet_pin,
                    plan_pin=plan_pin,
                    precursor_inputs=inputs,
                )
                continuity_stamp = native_stamp()
                validate_stamps((completed_stamp, continuity_stamp))
                if utc_from_ns(continuity_stamp["utc_ns"]) >= min(
                    expires, owned.expires_at
                ):
                    raise OriginalSourceCoordinatorError("original_precursor_expired")
                finished = continuity_stamp
            if inspect_base_prefix and replayed.intent is not None:
                inspected = _evaluate_owned_base_prefix_v6(
                    packet,
                    owned.packet,
                    replayed,
                    public_pin=public_pin,
                    account_pin=account_packet_pin,
                    plan_pin=plan_pin,
                    data_policy=g1_policy,
                    created_at=created_at,
                )
                checked = _evaluate_owned_base_prefix_v6(
                    packet,
                    owned.packet,
                    replayed,
                    public_pin=public_pin,
                    account_pin=account_packet_pin,
                    plan_pin=plan_pin,
                    data_policy=g1_policy,
                    created_at=created_at,
                )
                if inspected != checked:
                    raise OriginalSourceCoordinatorError(
                        "original_base_prefix_replay_changed"
                    )
                prefix_stamp = native_stamp()
                validate_stamps((finished, prefix_stamp))
                if utc_from_ns(prefix_stamp["utc_ns"]) >= min(
                    expires, owned.expires_at
                ):
                    raise OriginalSourceCoordinatorError("original_base_prefix_expired")
                finished = prefix_stamp
                prefix_receipt_json = checked
            if inspect_base_event and replayed.intent is not None:
                if prefix_receipt_json is None:
                    raise OriginalSourceCoordinatorError(
                        "original_base_event_prefix_missing"
                    )
                inspected_event = _evaluate_owned_base_event_v7(
                    packet,
                    replayed,
                    public_pin=public_pin,
                    data_policy=g1_policy,
                    created_at=created_at,
                    prefix_receipt_json=prefix_receipt_json,
                )
                checked_event = _evaluate_owned_base_event_v7(
                    packet,
                    replayed,
                    public_pin=public_pin,
                    data_policy=g1_policy,
                    created_at=created_at,
                    prefix_receipt_json=prefix_receipt_json,
                )
                if inspected_event != checked_event:
                    raise OriginalSourceCoordinatorError(
                        "original_base_event_replay_changed"
                    )
                event_stamp = native_stamp()
                validate_stamps((finished, event_stamp))
                if utc_from_ns(event_stamp["utc_ns"]) >= min(expires, owned.expires_at):
                    raise OriginalSourceCoordinatorError("original_base_event_expired")
                finished = event_stamp
                event_receipt_json = checked_event
            if inspect_sweep_history:
                inspected_sweep = _evaluate_owned_sweep_history_v9(
                    packet,
                    replayed,
                    public_pin=public_pin,
                    account_pin=account_packet_pin,
                    plan_pin=plan_pin,
                    data_policy=g1_policy,
                    created_at=created_at,
                )
                checked_sweep = _evaluate_owned_sweep_history_v9(
                    packet,
                    replayed,
                    public_pin=public_pin,
                    account_pin=account_packet_pin,
                    plan_pin=plan_pin,
                    data_policy=g1_policy,
                    created_at=created_at,
                )
                if inspected_sweep != checked_sweep:
                    raise OriginalSourceCoordinatorError(
                        "owned_sweep_history_replay_changed"
                    )
                sweep_stamp = native_stamp()
                validate_stamps((finished, sweep_stamp))
                if utc_from_ns(sweep_stamp["utc_ns"]) >= min(expires, owned.expires_at):
                    raise OriginalSourceCoordinatorError("owned_sweep_history_expired")
                finished = sweep_stamp
                sweep_receipt_json = checked_sweep
            precursor_pin = replayed.receipt_sha256
            precursor_action = precursor_doc["action"]
            precursor_code = precursor_doc["code"]
            if replayed.intent is not None:
                precursor_intent_pin = sha(canonical(precursor_doc["intent"]))
                precursor_intent_derived = True
            code = "original_precursor_inspected"
            return result()
        code = (
            "original_sources_g1_observed_candidate_required"
            if inspect_g1
            else "original_sources_observed_g1_candidate_required"
        )
        return result()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- retain existing raw proofs; return static DENY
        if task.cancelling():
            raise asyncio.CancelledError from None
        if inspect_base_event:
            # A later G5 refusal cannot leave an orphaned V6 inner receipt
            # alongside an unavailable V4 precursor in the hash-only handoff.
            prefix_receipt_json = event_receipt_json = None
        if inspect_sweep_history:
            sweep_receipt_json = None
        return result()
    finally:
        account_session._used = True
