"""Same-invocation G12, public source and committed account history/current join.

This is a DENY-only diagnostic. The original G1--G11 run is caller-origin;
neither a native source receipt nor this join is execution permission. V2 receipt
bytes and its V6 account path remain unchanged.
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
from app.trade_qualification import account_current_history_join as account_join
from app.trade_qualification import account_current_source_verifier as current_source
from app.trade_qualification import account_native_proof as native_proof
from app.trade_qualification import account_native_proof_storage as native_storage
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import account_observation_index as observed_account
from app.trade_qualification import original_source_coordinator_v2 as original_source
from app.trade_qualification import post_g12_account_join_v2 as prior
from app.trade_qualification import post_g12_final_clock_proof_v1 as final_clock_proof
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification import post_g12_recheck_v2 as public_recheck
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import public_source_runtime as source_runtime
from app.trade_qualification.account_capture_journal import digest
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.one_shot import _original
from app.trade_qualification.timing import event_identity

_SCHEMA = "ctcc.post_g12_owned_public_account_history_diagnostic.v3"
_CLOCK_SCHEMA = "ctcc.post_g12_owned_public_account_history_diagnostic.v3.clock1"
_MAX_RECEIPT = 4096
_MAX_NATIVE_JOIN_RECEIPT = 8192
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CAPTURE_ID = re.compile(r"[a-f0-9]{32}\Z")
_CODES = frozenset(
    {
        "new_g12_required",
        "public_unavailable",
        "account_unavailable",
        "joined_unqualified",
        "denied",
    }
)
_FALSE_FIELDS = (
    "original_source_verified",
    "account_complete",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "durable_intent_created",
    "execution_authority",
    "order_submitted",
)
_PIN_FIELDS = (
    "candidate_sha256",
    "original_event_key",
    "account_plan_sha256",
    "account_scope_sha256",
    "history_capture_id_sha256",
    "g12_evidence_sha256",
    "g12_report_sha256",
    "public_packet_sha256",
    "public_journal_sha256",
    "native_account_join_sha256",
    "native_account_proof_sha256",
    "native_account_readback_sha256",
    "history_db_chain_sha256",
    "history_reference_sha256",
    "current_reference_sha256",
    "public_only_recheck_sha256",
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
        "account_history_readback_started_at",
        "account_history_readback_completed_at",
        "account_history_replay_verified_at",
        "account_session_first_http_started_at",
        "observed_at",
        "public_request_count",
        "public_only_recheck_code",
        "public_only_recheck_observed_at",
        "public_only_recheck_receipt_persisted",
        "committed_history_readback_before_first_http",
        "admission",
        *_PIN_FIELDS,
        *_FALSE_FIELDS,
    }
)
_CLOCK_FIELDS = _FIELDS | {"candidate_deadline_at", "final_clock_proof"}


class PostG12AccountHistoryJoinError(ValueError):
    """Static rejection code; never includes private account or source bytes."""


def _utc(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
        raise ValueError
    return instant


@dataclass(frozen=True, slots=True, repr=False)
class PostG12OwnedPublicAccountHistoryDiagnosticV3:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if (
                type(self.receipt_json) is not bytes
                or not 0 < len(self.receipt_json) <= _MAX_RECEIPT
            ):
                raise ValueError
            value = decode(self.receipt_json, _MAX_RECEIPT)
            if (
                type(value) is not dict
                or set(value)
                != (
                    _CLOCK_FIELDS
                    if value.get("schema_version") == _CLOCK_SCHEMA
                    else _FIELDS
                )
                or canonical(value) != self.receipt_json
                or value["schema_version"] not in (_SCHEMA, _CLOCK_SCHEMA)
                or value["code"] not in _CODES
                or value["admission"] != "DENY"
                or any(value[field] is not False for field in _FALSE_FIELDS)
                or type(value["public_only_recheck_receipt_persisted"]) is not bool
                or type(value["committed_history_readback_before_first_http"])
                is not bool
                or type(value["public_request_count"]) is not int
                or not 0 <= value["public_request_count"] <= 1000
                or type(value["report_id"]) is not str
                or not 1 <= len(value["report_id"]) <= 128
                or type(value["instrument_id"]) is not str
                or not 1 <= len(value["instrument_id"]) <= 64
                or value["direction"] not in ("long", "short")
            ):
                raise ValueError
            for field in _PIN_FIELDS:
                pin = value[field]
                if pin is not None and (
                    type(pin) is not str or _HEX.fullmatch(pin) is None
                ):
                    raise ValueError
            for field in (
                "original_entry",
                "original_stop_loss",
                "original_take_profit",
            ):
                raw = value[field]
                if (
                    type(raw) is not str
                    or len(raw) > 128
                    or not Decimal(raw).is_finite()
                ):
                    raise ValueError
            for field in (
                "publication_completed_at",
                "public_first_request_started_at",
                "account_history_readback_started_at",
                "account_history_readback_completed_at",
                "account_history_replay_verified_at",
                "account_session_first_http_started_at",
                "public_only_recheck_observed_at",
                "observed_at",
            ):
                if value[field] is not None:
                    _utc(value[field])
            barrier = value["publication_completed_at"]
            public_first = value["public_first_request_started_at"]
            history_start = value["account_history_readback_started_at"]
            history_end = value["account_history_readback_completed_at"]
            history_verified = value["account_history_replay_verified_at"]
            account_first = value["account_session_first_http_started_at"]
            recheck_at = value["public_only_recheck_observed_at"]
            observed_at = value["observed_at"]
            if value["schema_version"] == _CLOCK_SCHEMA:
                deadline_text = value["candidate_deadline_at"]
                if deadline_text is not None and (
                    _utc(deadline_text).isoformat() != deadline_text
                ):
                    raise ValueError
                if (barrier is None) != (deadline_text is None):
                    raise ValueError
                proof = value["final_clock_proof"]
                if proof is not None:
                    cutoff = final_clock_proof.replay_proof(
                        proof,
                        expected_public_journal_sha256=value["public_journal_sha256"],
                    )
                    if (
                        public_first is None
                        or recheck_at is None
                        or observed_at
                        != utc_from_ns(proof["final_clock"]["utc_ns"]).isoformat()
                        or cutoff < _utc(observed_at)
                        or value["code"] == "joined_unqualified"
                        and (deadline_text is None or cutoff >= _utc(deadline_text))
                    ):
                        raise ValueError
                elif value["code"] == "joined_unqualified":
                    raise ValueError
            claimed_times = tuple(
                item
                for item in (
                    barrier,
                    public_first,
                    history_start,
                    history_end,
                    history_verified,
                    account_first,
                    recheck_at,
                )
                if item is not None
            )
            if (
                any(value[field] is None for field in _PIN_FIELDS[:5])
                or (barrier is None) != (value["g12_evidence_sha256"] is None)
                or (barrier is None) != (value["g12_report_sha256"] is None)
                or (public_first is None) != (value["public_packet_sha256"] is None)
                or (public_first is None) != (value["public_journal_sha256"] is None)
                or (public_first is None) != (value["public_request_count"] == 0)
                or (account_first is None)
                != (value["native_account_join_sha256"] is None)
                or (account_first is None)
                != (value["native_account_proof_sha256"] is None)
                or (account_first is None)
                != (value["native_account_readback_sha256"] is None)
                or (account_first is None) != (value["history_db_chain_sha256"] is None)
                or (account_first is None)
                != (value["history_reference_sha256"] is None)
                or (account_first is None)
                != (value["current_reference_sha256"] is None)
                or value["committed_history_readback_before_first_http"]
                != (account_first is not None)
                or (account_first is None) != (history_start is None)
                or (account_first is None) != (history_end is None)
                or (account_first is None) != (history_verified is None)
                or (recheck_at is None) != (value["public_only_recheck_sha256"] is None)
                or (recheck_at is None) != (value["public_only_recheck_code"] is None)
                or value["public_only_recheck_receipt_persisted"]
                != (recheck_at is not None)
                or value["public_only_recheck_code"] is not None
                and value["public_only_recheck_code"] not in public_recheck._CODES
                or public_first is not None
                and (barrier is None or _utc(public_first) <= _utc(barrier))
                or account_first is not None
                and (
                    public_first is None
                    or not _utc(barrier)
                    < _utc(history_start)
                    < _utc(history_end)
                    < _utc(history_verified)
                    < _utc(account_first)
                    or _utc(public_first) > _utc(history_start)
                )
                or recheck_at is not None
                and (account_first is None or _utc(recheck_at) < _utc(account_first))
                or value["code"] == "joined_unqualified"
                and (account_first is None or recheck_at is None)
                or claimed_times
                and (
                    observed_at is None
                    or any(_utc(item) > _utc(observed_at) for item in claimed_times)
                )
            ):
                raise ValueError
        except Exception:  # noqa: BLE001 -- no private exception text in a receipt
            raise PostG12AccountHistoryJoinError(
                "post_g12_history_receipt_invalid"
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


def read_post_g12_account_history_receipt_v3(
    root, *, expected_sha256, expected_root_identity
):
    try:
        if (
            type(root) not in (PosixPath, WindowsPath)
            or not root.is_absolute()
            or type(expected_sha256) is not str
            or _HEX.fullmatch(expected_sha256) is None
        ):
            raise ValueError
        with source_runtime._native_recheck_root(root) as directory:
            if prior._native_directory_identity(directory) != expected_root_identity:
                raise ValueError
            if set(directory.names()) != {"receipt.json"}:
                raise ValueError
            payload = directory.read("receipt.json", _MAX_RECEIPT)
            if sha(payload) != expected_sha256:
                raise ValueError
            result = PostG12OwnedPublicAccountHistoryDiagnosticV3(payload)
        if source_runtime._native_recheck_root_identity(root) != expected_root_identity:
            raise ValueError
        return result
    except Exception:  # noqa: BLE001 -- no native path disclosure
        raise PostG12AccountHistoryJoinError(
            "post_g12_history_readback_failed"
        ) from None


def _publish_receipt(root, receipt, *, expected_root_identity):
    if type(receipt) is not PostG12OwnedPublicAccountHistoryDiagnosticV3:
        raise PostG12AccountHistoryJoinError("post_g12_history_receipt_invalid")
    try:
        with source_runtime._native_recheck_root(root) as directory:
            if (
                prior._native_directory_identity(directory) != expected_root_identity
                or directory.names()
            ):
                raise ValueError
            directory.publish("receipt.json", receipt.receipt_json)
            if directory.read("receipt.json", _MAX_RECEIPT) != receipt.receipt_json:
                raise ValueError
        replayed = read_post_g12_account_history_receipt_v3(
            root,
            expected_sha256=receipt.receipt_sha256,
            expected_root_identity=expected_root_identity,
        )
        if replayed.receipt_json != receipt.receipt_json:
            raise ValueError
    except Exception:  # noqa: BLE001 -- retain any already published file
        raise PostG12AccountHistoryJoinError(
            "post_g12_history_publish_failed"
        ) from None


def _stamp_time(value):
    if type(value) is not dict or set(value) != {"utc_ns", "monotonic_ns"}:
        raise ValueError
    if any(type(value[key]) is not int or value[key] <= 0 for key in value):
        raise ValueError
    return utc_from_ns(value["utc_ns"])


def _account_companion_pins(
    root, *, current_reference, scope_sha256, first_http_at, root_identity=None
):
    """Read the sealed V7 companion under its native directory handle."""
    try:
        identity = native_storage._root_identity(root)
        if root_identity is not None and identity != root_identity:
            raise ValueError
        reference = observed_account.reference_document(
            observed_account.CaptureReference(**current_reference)
        )
        if reference != current_reference or type(first_http_at) is not datetime:
            raise ValueError
        with native_storage._root_context(root) as directory:
            if native_storage._root_identity(directory.path) != identity or set(
                directory.names()
            ) != {*native_proof.FILES, "proof.json", "readback.json"}:
                raise ValueError
            proof_raw = directory.read("proof.json", native_proof.MAX_PROOF)
            readback_raw = directory.read("readback.json", native_storage.MAX_PART)
            proof_doc = native_proof._canonical_document(proof_raw)
            readback = native_proof._canonical_document(
                readback_raw, native_storage.MAX_PART
            )
            file_hashes = {
                name: sha(directory.read(name, native_storage.MAX_PART))
                for name in native_proof.FILES
            }
            if (
                type(proof_doc) is not dict
                or proof_doc.get("schema_version") != native_proof.V7_FLAT_SCHEMA
                or proof_doc.get("policy_sha256") != native_proof.V7_FLAT_POLICY_SHA256
                or proof_doc.get("source_reference") != reference
                or proof_doc.get("scope_sha256") != scope_sha256
                or proof_doc.get("clock_file_sha256") != file_hashes
                or proof_doc.get("historical_hwm_clock_verified") is not False
                or proof_doc.get("account_complete") is not False
                or proof_doc.get("execution_authority") is not False
                or proof_doc.get("admission") != "DENY"
                or type(readback) is not dict
                or set(readback)
                != {
                    "schema_version",
                    "proof_sha256",
                    "clock_file_sha256",
                    "root_device",
                    "root_inode",
                    "readback_complete",
                    "execution_authority",
                }
                or readback["schema_version"] != native_storage.V7_FLAT_READBACK_SCHEMA
                or readback["proof_sha256"] != sha(proof_raw)
                or readback["clock_file_sha256"] != file_hashes
                or type(readback["root_device"]) is not int
                or type(readback["root_inode"]) is not int
                or (readback["root_device"], readback["root_inode"]) != identity
                or readback["execution_authority"] is not False
            ):
                raise ValueError
            persist_at = _stamp_time(proof_doc["proof_persist_start"])
            complete_at = _stamp_time(readback["readback_complete"])
            validate_stamps(
                (proof_doc["proof_persist_start"], readback["readback_complete"])
            )
            if (
                not first_http_at
                < persist_at
                < complete_at
                < _utc(proof_doc["expires_at"])
            ):
                raise ValueError
            if (
                directory.read("proof.json", native_proof.MAX_PROOF) != proof_raw
                or directory.read("readback.json", native_storage.MAX_PART)
                != readback_raw
                or any(
                    sha(directory.read(name, native_storage.MAX_PART))
                    != file_hashes[name]
                    for name in native_proof.FILES
                )
                or set(directory.names())
                != {*native_proof.FILES, "proof.json", "readback.json"}
            ):
                raise ValueError
        if native_storage._root_identity(root) != identity:
            raise ValueError
        return sha(proof_raw), sha(readback_raw), reference
    except Exception:  # noqa: BLE001 -- no private source/path in V3 receipt
        raise PostG12AccountHistoryJoinError(
            "post_g12_history_account_companion_invalid"
        ) from None


async def publish_capture_public_account_history_v3(
    evidence_root,
    public_root,
    account_root,
    recheck_root,
    join_root,
    original_market,
    *,
    run,
    original_inputs,
    market_policy,
    account_session,
    session_factory,
    history_capture_id,
    g12_custody_repository=None,
) -> PostG12OwnedPublicAccountHistoryDiagnosticV3:
    """Inspect one new G12 and one post-publication V7 current/history join.

    The history capture ID is a DB locator, not a source claim. The native
    account runtime independently rereads its committed chain under the UID
    lock before any new current-account HTTP request. No result permits risk
    reservation, durable intent or dispatch.
    """
    roots = (evidence_root, public_root, account_root, recheck_root, join_root)
    if any(
        type(root) not in (PosixPath, WindowsPath) or not root.is_absolute()
        for root in roots
    ):
        raise PostG12AccountHistoryJoinError("post_g12_history_roots_invalid")
    for index, first in enumerate(roots):
        for second in roots[index + 1 :]:
            original_source._roots(first, second)
    recheck_identity = prior._empty_native_receipt_root_identity(
        recheck_root, "post_g12_history_recheck_root_unavailable"
    )
    join_identity = prior._empty_native_receipt_root_identity(
        join_root, "post_g12_history_join_root_unavailable"
    )
    if (
        type(account_session) is not ControlledDemoAccountSession
        or account_session._used
        or not account_native._configured_factory(session_factory)
        or type(history_capture_id) is not str
        or _CAPTURE_ID.fullmatch(history_capture_id) is None
    ):
        raise PostG12AccountHistoryJoinError(
            "post_g12_history_session_or_locator_invalid"
        )
    plan = account_capture._checked_plan(account_session._plan, account_session._pin)
    if (
        type(plan) is not account_capture.CurrentDemoAccountCapturePlanV7
        or plan.registration_region != "global"
        or plan.settlement_currency != "USDT"
    ):
        raise PostG12AccountHistoryJoinError("post_g12_history_scope_unsupported")
    selected = public_v2._policy_copy(market_policy)
    market, pre, inputs = _original(original_market, run, original_inputs)
    intent, risk_inputs, candidate = inputs["intent"], inputs["risk_inputs"], pre.result
    if not pre.pre_evidence_complete:
        raise PostG12AccountHistoryJoinError("post_g12_history_original_incomplete")
    key = event_identity(pre.prefix.detection)
    if (
        key is None
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
        raise PostG12AccountHistoryJoinError(
            "post_g12_history_original_geometry_invalid"
        )
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    candidate_pin = sha(
        canonical(
            {
                "pre_evidence_sha256": pre.evaluation_sha256,
                "event_key": key,
                "entry": str(candidate.candidate_entry),
                "stop_loss": str(candidate.stop_loss),
                "take_profit": str(candidate.take_profit),
            }
        )
    )
    account_scope_pin = digest(
        canonical(["demo", plan.expected_uid, plan.settlement_currency])
    )
    pins = {
        "candidate_sha256": candidate_pin,
        "original_event_key": key,
        "account_plan_sha256": account_session._pin,
        "account_scope_sha256": account_scope_pin,
        "history_capture_id_sha256": sha(history_capture_id.encode("ascii")),
        "g12_evidence_sha256": None,
        "g12_report_sha256": None,
        "public_packet_sha256": None,
        "public_journal_sha256": None,
        "native_account_join_sha256": None,
        "native_account_proof_sha256": None,
        "native_account_readback_sha256": None,
        "history_db_chain_sha256": None,
        "history_reference_sha256": None,
        "current_reference_sha256": None,
        "public_only_recheck_sha256": None,
    }
    publication = custody_claim = None
    invocation = object()
    barrier = public_first = history_start = history_end = history_verified = (
        account_first
    ) = None
    recheck_at = recheck_code = last = None
    public_count = 0
    code = "new_g12_required"
    recheck_persisted = False
    final_proof = None
    candidate_deadline = None
    journal_clock = journal_root_identity = None

    def finish():
        receipt = PostG12OwnedPublicAccountHistoryDiagnosticV3(
            canonical(
                {
                    "schema_version": _CLOCK_SCHEMA,
                    "code": code,
                    "report_id": intent.report_id,
                    "instrument_id": intent.instrument_id,
                    "direction": intent.direction,
                    "original_entry": str(candidate.candidate_entry),
                    "original_stop_loss": str(candidate.stop_loss),
                    "original_take_profit": str(candidate.take_profit),
                    "candidate_deadline_at": None
                    if candidate_deadline is None
                    else candidate_deadline.isoformat(),
                    "publication_completed_at": None
                    if barrier is None
                    else barrier.isoformat(),
                    "public_first_request_started_at": None
                    if public_first is None
                    else public_first.isoformat(),
                    "account_history_readback_started_at": None
                    if history_start is None
                    else history_start.isoformat(),
                    "account_history_readback_completed_at": None
                    if history_end is None
                    else history_end.isoformat(),
                    "account_history_replay_verified_at": None
                    if history_verified is None
                    else history_verified.isoformat(),
                    "account_session_first_http_started_at": None
                    if account_first is None
                    else account_first.isoformat(),
                    "observed_at": None
                    if last is None
                    else utc_from_ns(last["utc_ns"]).isoformat(),
                    "final_clock_proof": final_proof,
                    "public_request_count": public_count,
                    "public_only_recheck_code": recheck_code,
                    "public_only_recheck_observed_at": None
                    if recheck_at is None
                    else recheck_at.isoformat(),
                    "public_only_recheck_receipt_persisted": recheck_persisted,
                    "committed_history_readback_before_first_http": account_first
                    is not None,
                    "admission": "DENY",
                    **pins,
                    **{name: False for name in _FALSE_FIELDS},
                }
            )
        )
        _publish_receipt(join_root, receipt, expected_root_identity=join_identity)
        return receipt

    try:
        custody_claim = await public_runtime._preclaim_lineage_v2(
            g12_custody_repository,
            evidence_root,
            market,
            pre,
            inputs,
            demo_session=account_session,
        )
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
        await public_runtime._complete_lineage_v2(
            g12_custody_repository,
            custody_claim,
            evidence_root,
            evidence,
            market,
            pre,
            inputs,
        )
        if (
            origin.candidate.candidate_entry != intent.candidate_entry
            or origin.candidate.stop_loss != candidate.stop_loss
            or origin.candidate.take_profit != candidate.take_profit
            or origin.original_event_key != key
            or origin.pre_evidence_sha256 != pre.evaluation_sha256
        ):
            raise PostG12AccountHistoryJoinError("post_g12_history_original_changed")
        candidate_deadline = origin.deadline
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
        raw = decode(packet.packet_json, public_v2.MAX_PACKET_BYTES)
        if (
            raw["stage"] != "post_publication"
            or raw["environment"] != "demo"
            or raw["report_id"] != intent.report_id
            or raw["instrument_id"] != intent.instrument_id
            or raw["barrier_completed_at"] != barrier.isoformat()
            or raw.get("account_plan_sha256") != account_session._pin
        ):
            raise PostG12AccountHistoryJoinError(
                "post_g12_history_public_lineage_changed"
            )
        public_first, public_count = prior._public_request_starts(packet, barrier)
        pins["public_packet_sha256"] = packet.bundle_sha256
        pins["public_journal_sha256"] = journal
        journal_clock, journal_root_identity = (
            final_clock_proof.read_persisted_public_clock(
                public_root,
                expected_journal_sha256=journal,
                expected_packet_sha256=packet.bundle_sha256,
            )
        )
        validate_stamps((journal_clock, public_stamp))
        current_started = native_stamp()
        validate_stamps((last, current_started))
        last = current_started
        if not barrier < utc_from_ns(current_started["utc_ns"]) < origin.deadline:
            raise PostG12AccountHistoryJoinError(
                "post_g12_history_account_start_invalid"
            )
        code = "account_unavailable"
        account = await account_native.capture_native_current_history_join(
            account_session,
            session_factory=session_factory,
            proof_root=account_root,
            history_capture_id=history_capture_id,
        )
        if (
            type(account) is not account_native.NativeCurrentHistoryJoinDiagnostic
            or account_session._used is not True
        ):
            raise PostG12AccountHistoryJoinError("post_g12_history_native_join_invalid")
        account_doc = decode(account.receipt_json, _MAX_NATIVE_JOIN_RECEIPT)
        native_fields = {
            "schema_version",
            "native_current_receipt_sha256",
            "locked_history_join_receipt_sha256",
            "history_source_reference",
            "current_source_reference",
            "locked_readback_blocking_reasons",
            "history_tail_closed",
            "native_current_source_observed",
            "historical_native_source_observed",
            "snapshot",
            "account_complete",
            "account_revision_published",
            "execution_authority",
            "admission",
            "join_policy_sha256",
            "current_source_policy_sha256",
            "native_current_proof_sha256",
            "native_current_proof_readback_sha256",
            "pre_http_history_source_reference",
            "pre_http_history_db_chain_sha256",
            "pre_http_history_query_receipt_sha256",
            "pre_http_history_readback_started",
            "pre_http_history_readback_completed",
            "pre_http_history_replay_verified",
            "first_http_request_started",
            "committed_history_readback_before_first_http",
            "flat_start_permission",
        }
        if (
            type(account_doc) is not dict
            or set(account_doc) != native_fields
            or canonical(account_doc) != account.receipt_json
            or account_doc.get("schema_version")
            != "ctcc.native_current_history_join_diagnostic.v3"
            or account_doc.get("admission") != "DENY"
            or account_doc.get("account_complete") is not False
            or account_doc.get("execution_authority") is not False
            or account_doc.get("committed_history_readback_before_first_http")
            is not True
            or account_doc.get("join_policy_sha256")
            != account_join.V7_ORDERED_POLICY_SHA256
            or account_doc.get("current_source_policy_sha256")
            != current_source.V7_POLICY_SHA256
            or account_doc.get("history_tail_closed") is not False
            or account_doc.get("flat_start_permission") is not False
            or account_doc.get("native_current_source_observed") is not True
            or account_doc.get("historical_native_source_observed") is not False
            or account_doc.get("snapshot") is not None
            or account_doc.get("account_revision_published") is not False
            or any(
                type(account_doc.get(name)) is not str
                or _HEX.fullmatch(account_doc[name]) is None
                for name in (
                    "native_current_receipt_sha256",
                    "locked_history_join_receipt_sha256",
                    "pre_http_history_db_chain_sha256",
                    "pre_http_history_query_receipt_sha256",
                    "native_current_proof_sha256",
                    "native_current_proof_readback_sha256",
                )
            )
        ):
            raise PostG12AccountHistoryJoinError("post_g12_history_native_join_denied")
        blockers = account_doc["locked_readback_blocking_reasons"]
        if (
            type(blockers) is not list
            or not blockers
            or blockers != sorted(set(blockers))
            or any(
                type(item) is not str or not item or len(item) > 128
                for item in blockers
            )
            or "history_tail_not_atomically_closed" not in blockers
        ):
            raise PostG12AccountHistoryJoinError("post_g12_history_blockers_invalid")
        history_ref = account_doc["history_source_reference"]
        current_ref = account_doc["current_source_reference"]
        pre_history_ref = account_doc["pre_http_history_source_reference"]
        for reference in (history_ref, current_ref, pre_history_ref):
            if type(reference) is not dict or set(reference) != set(
                observed_account.CaptureReference.__dataclass_fields__
            ):
                raise PostG12AccountHistoryJoinError(
                    "post_g12_history_reference_invalid"
                )
            checked_reference = observed_account.CaptureReference(**reference)
            if observed_account.reference_document(checked_reference) != reference:
                raise PostG12AccountHistoryJoinError(
                    "post_g12_history_reference_invalid"
                )
        expected_session = digest(plan.session_binding_id.encode("ascii"))
        if (
            history_ref != pre_history_ref
            or history_ref.get("capture_id") != history_capture_id
            or current_ref.get("capture_id") == history_capture_id
            or current_ref.get("plan_sha256") != account_session._pin
            or history_ref.get("plan_sha256") == account_session._pin
            or any(
                ref.get("session_binding_sha256") != expected_session
                for ref in (history_ref, current_ref)
            )
        ):
            raise PostG12AccountHistoryJoinError("post_g12_history_reference_mismatch")
        pre_start = account_doc["pre_http_history_readback_started"]
        pre_end = account_doc["pre_http_history_readback_completed"]
        pre_verified = account_doc["pre_http_history_replay_verified"]
        first_http = account_doc["first_http_request_started"]
        history_start, history_end, history_verified, account_first = map(
            _stamp_time, (pre_start, pre_end, pre_verified, first_http)
        )
        if not (
            utc_from_ns(current_started["utc_ns"]) <= history_start
            and barrier < history_start < history_end < history_verified < account_first
            and public_first <= history_start
            and account_first < origin.deadline
        ):
            raise PostG12AccountHistoryJoinError(
                "post_g12_history_account_chronology_invalid"
            )
        validate_stamps((current_started, pre_start, pre_end, pre_verified, first_http))
        completed = native_stamp()
        validate_stamps((first_http, completed))
        last = completed
        checked_at = utc_from_ns(completed["utc_ns"])
        if checked_at >= origin.deadline:
            raise PostG12AccountHistoryJoinError("post_g12_history_join_expired")
        context = public_market_context_v2(
            packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=checked_at
        )
        with source_runtime._reopen_runtime_journal(public_root) as directory:
            replay_inputs = {
                "expected_plan_sha256": sha(
                    directory.read("plan.json", source_runtime.MAX_RAW)
                ),
                "expected_journal_sha256": journal,
                "expected_packet_sha256": packet.bundle_sha256,
                "origin": origin,
                "account_plan_sha256": account_session._pin,
                "current_market_json": canonical(
                    context.market.model_dump(mode="json", round_trip=True)
                ).decode("ascii"),
                "reference_json": canonical(
                    context.reference.model_dump(mode="json", round_trip=True)
                ).decode("ascii"),
                "observed_at": checked_at,
            }
            replay = public_recheck.evaluate_post_g12_public_recheck_v2(
                directory, **replay_inputs
            )
            replay = public_recheck.verify_post_g12_public_recheck_v2(
                replay, directory, **replay_inputs
            )
        proof_pin, readback_pin, _ = _account_companion_pins(
            account_root,
            current_reference=current_ref,
            scope_sha256=account_scope_pin,
            first_http_at=account_first,
        )
        if (
            proof_pin != account_doc["native_current_proof_sha256"]
            or readback_pin != account_doc["native_current_proof_readback_sha256"]
        ):
            raise PostG12AccountHistoryJoinError(
                "post_g12_history_account_companion_changed"
            )
        pins["native_account_join_sha256"] = account.receipt_sha256
        pins["native_account_proof_sha256"] = proof_pin
        pins["native_account_readback_sha256"] = readback_pin
        pins["history_db_chain_sha256"] = account_doc[
            "pre_http_history_db_chain_sha256"
        ]
        pins["history_reference_sha256"] = sha(canonical(history_ref))
        pins["current_reference_sha256"] = sha(canonical(current_ref))
        prior._publish_public_only_recheck_receipt_v2(
            recheck_root, replay, expected_root_identity=recheck_identity
        )
        pins["public_only_recheck_sha256"] = replay.receipt_sha256
        recheck_code = decode(replay.receipt_json)["code"]
        recheck_at = checked_at
        recheck_persisted = True
        replayed_clock, replayed_identity = (
            final_clock_proof.read_persisted_public_clock(
                public_root,
                expected_journal_sha256=journal,
                expected_packet_sha256=packet.bundle_sha256,
                expected_root_identity=journal_root_identity,
            )
        )
        if (
            replayed_clock != journal_clock
            or replayed_identity != journal_root_identity
        ):
            raise PostG12AccountHistoryJoinError("post_g12_public_clock_changed")
        final_stamp = native_stamp()
        validate_stamps((last, final_stamp))
        last = final_stamp
        final_at = final_clock_proof.effective_cutoff(
            journal_clock, final_stamp, public_stamp
        )
        final_proof = final_clock_proof.make_proof(
            journal_clock,
            final_stamp,
            journal,
            consume_clock=public_stamp,
        )
        if final_at >= origin.deadline:
            raise PostG12AccountHistoryJoinError("post_g12_history_final_expired")
        prior._require_final_original_g1(packet, pre, final_at)
        # The last account/history await may outlive both the public transport
        # profile and the original, potentially stricter economics quote age.
        public_market_context_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            evaluated_at=final_at,
        )
        prior._require_final_original_economics_quote(packet, pre, final_at)
        code = "joined_unqualified"
        return finish()
    except asyncio.CancelledError:
        raise
    except PostG12AccountHistoryJoinError as exc:
        if str(exc) == "post_g12_history_publish_failed":
            raise
        if task.cancelling():
            raise asyncio.CancelledError from None
        code = "denied" if barrier is None or recheck_persisted else code
        if not recheck_persisted:
            history_start = history_end = history_verified = account_first = None
            pins["native_account_join_sha256"] = None
            pins["native_account_proof_sha256"] = None
            pins["native_account_readback_sha256"] = None
            pins["history_db_chain_sha256"] = None
            pins["history_reference_sha256"] = None
            pins["current_reference_sha256"] = None
        return finish()
    except prior._PublicRecheckReceiptPublicationError:
        raise
    except Exception:  # noqa: BLE001 -- never serialize source, SQL or private errors
        if task.cancelling():
            raise asyncio.CancelledError from None
        code = "denied" if barrier is None or recheck_persisted else code
        if not recheck_persisted:
            history_start = history_end = history_verified = account_first = None
            pins["native_account_join_sha256"] = None
            pins["native_account_proof_sha256"] = None
            pins["native_account_readback_sha256"] = None
            pins["history_db_chain_sha256"] = None
            pins["history_reference_sha256"] = None
            pins["current_reference_sha256"] = None
        return finish()
    finally:
        account_session._used = True
        if publication is not None:
            public_runtime._PUBLICATIONS.pop(publication, None)
