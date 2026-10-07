"""One-task, read-only original-source handoff before any candidate or G12.

The native V2 public issuer currently refuses Demo capture before network IO.
This coordinator keeps that refusal. A future reviewed issuer can use the same
task-local handoff to join a fresh public packet to a controlled native account
diagnostic, but the returned receipt is never a qualification or order permit.
"""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass
from datetime import datetime
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
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import data_v2
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
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.public_source_runtime import (
    _consume_initial_public_capture_v2,
)

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
):
    if (
        type(inspect_g1) is not bool
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
        return (
            InitialOwnedPrecursorDiagnosticV4(receipt)
            if strategy is not None
            else InitialOwnedSourcesDiagnosticV3(receipt)
            if inspect_g1
            else InitialOwnedSourcesDiagnosticV2(receipt)
        )

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
            precursor_pin = replayed.receipt_sha256
            precursor_action = precursor_doc["action"]
            precursor_code = precursor_doc["code"]
            if replayed.intent is not None:
                precursor_intent_pin = sha(canonical(precursor_doc["intent"]))
                precursor_intent_derived = True
            finished = completed_stamp
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
        return result()
    finally:
        account_session._used = True
