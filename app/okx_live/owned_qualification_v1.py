"""One original human-approved Live invocation through the canonical kernel.

The background worker retains its task-bound credential lease while an
authenticated UI signals a nonce response. No candidate exists before that
response. Every WAIT, failure, timeout or cancellation burns the response.
Saved receipts, dataclasses, flags and another task cannot recreate an owner.
Legacy Live write flags remain OFF throughout this separate one-shot boundary.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Context, Decimal, localcontext
from pathlib import Path
from threading import get_ident
from weakref import WeakKeyDictionary

from sqlalchemy import text

from app.config.settings import get_settings
from app.database.repositories import native_live_financial_epoch_v1 as financial
from app.database.repositories import native_live_ledger_v1 as ledger
from app.database.repositories import native_live_operator_control_v1 as controls
from app.domain import native_clock as public_clock_primitives
from app.domain import native_live_intent_v1 as intent_source
from app.domain import source_primitives
from app.domain.native_clock import native_stamp
from app.domain.native_live_intent_v1 import (
    LiveIntentDataV1,
    exact_live_intent_document_v1,
)
from app.domain.native_live_legacy_safety_v1 import (
    require_legacy_live_negative_fence_v1,
)
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx import native_live_write_v1 as physical
from app.exchange.okx import parsers as public_parser_source
from app.exchange.okx import symbols as public_symbol_source
from app.okx_live import entry_math_closure_v1 as entry_math_closure
from app.okx_live import entry_original_math_v2 as entry_original
from app.okx_live import entry_owned_seal_v1 as entry_proof
from app.okx_live import final_recheck_v1 as final_recheck
from app.okx_live import native_runtime_v1 as runtime
from app.okx_live import owned_risk_adapter_v1 as adapter
from app.okx_live import qualification_math_v1 as math
from app.okx_live import trusted_source_bridge_v1 as source
from app.public_market_source import public_clock as public_clock_source
from app.public_market_source import public_market_receipts as public_receipt_contracts
from app.public_market_source import public_receipt_storage as public_receipt_source
from app.trade_evidence import live_v1 as evidence
from app.trade_evidence import storage
from app.trade_evidence.native_private_root_v2 import mkdir_private_evidence_child_v2
from app.trade_qualification import global_public_staged_v2 as public_staged_source
from app.trade_qualification import native_live_public_source_v1 as public
from app.trade_qualification import ws_reference as public_ws_reference_source
from app.trade_qualification.engine import _hash as policy_hash
from app.trade_qualification.portfolio import LiveEvidenceStamp
from app.trade_qualification.service import QualificationIntent

_PUBLIC_SOURCE_KEY_V1 = object()
_ENTRY_PROOF_KEY_V1 = object()
_SESSION_KEY = object()
_OWNED = WeakKeyDictionary()
_PUBLIC_REQUESTS = WeakKeyDictionary()
_STAGED_ACCOUNT_REQUESTS_V2 = WeakKeyDictionary()
_ENTRY_ORIGINAL_MATH_KEY_V2 = object()
_CLOSE_PUBLIC_REQUESTS = WeakKeyDictionary()
_CONTROL_REQUESTS = WeakKeyDictionary()
_QUALIFIED = WeakKeyDictionary()
_CHALLENGES = {}
_OPENING = {}
_PROCESS_NONCE = secrets.token_hex(32)
_MAX_SESSIONS = 4
ARM_PHRASE = "ARM_OKX_LIVE_REAL_MONEY"
CLEAR_STOP_PHRASE = "CLEAR_OKX_LIVE_STOP"
EXIT_PHRASE = "CLOSE_OKX_LIVE_MICRO_POSITION"
_NATIVE_GUARD = None
CHALLENGE_SECONDS = 120
ARM_SECONDS = 30
CONTROLLED_EXIT_SECONDS = 120
_STRATEGIES = frozenset(
    {
        "trend_pullback",
        "breakout_continuation",
        "liquidity_sweep_reversal",
        "fvg_return",
        "order_block_return",
        "range_reversal",
        "structure_reversal",
        "volatility_expansion",
    }
)


class OwnedLiveQualificationError(ValueError):
    pass


def _need(value, code="native_live_owned_qualification_unavailable"):
    if value is not True:
        raise OwnedLiveQualificationError(code) from None


def _stamp():
    return ClockStamp.model_validate(native_stamp(), strict=True)


class _Opaque:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False, "native_live_owner_not_constructible")

    def __copy__(self):
        _need(False, "native_live_owner_not_transferable")

    def __deepcopy__(self, memo):
        _need(False, "native_live_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        _need(False, "native_live_owner_not_transferable")


class OwnedLiveOperatorSessionV1(_Opaque):
    __slots__ = ()

    @property
    def challenge_data(self):
        # DATA may be read by the authenticated UI task. It does not move the
        # task-bound source or permit it to run qualification in that task.
        state = _OWNED.get(self)
        _need(type(state) is dict and not state["closed"])
        return bytes(state["challenge_json"])


class _PublicRequest(_Opaque):
    __slots__ = ()


class _ClosePublicRequest(_Opaque):
    __slots__ = ()


class _ControlRequest(_Opaque):
    __slots__ = ()


class OwnedLiveQualificationV1(_Opaque):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class LiveQualifiedExecutionFactsV1:
    intent_data: LiveIntentDataV1
    intent_readback: bytes
    reservation_id: str
    request_json: bytes
    request_sha256: str
    session_factory: object
    role_pins: object
    source_owner: object
    public_owner: object
    deadline_utc: datetime
    expiry_monotonic_ns: int
    credential_session_sha256: str
    account_uid: str
    main_uid: str
    arm_id: str


@dataclass(frozen=True, slots=True, repr=False)
class LivePostsubmitHandoffFactsV1:
    execution_facts: LiveQualifiedExecutionFactsV1
    runtime_owner: object
    physical_handle: object
    evidence_root: Path
    report_id: str
    publication_barrier: ClockStamp
    publication_receipt_json: bytes
    operator_roundtrip_id: str
    controlled_exit_authorized: bool
    policy_json: bytes
    original_candidate_json: bytes
    initial_math_json: bytes
    recheck_math_json: bytes
    final_dispatch_recheck_json: bytes


@dataclass(frozen=True, slots=True, repr=False)
class LiveControlledCloseConsentFactsV1:
    operator_roundtrip_id: str
    consent_received: ClockStamp
    expires_at: datetime
    expiry_monotonic_ns: int
    consent_sha256: str
    account_uid: str
    main_uid: str
    credential_session_sha256: str
    reservation_id: str
    original_intent_sha256: str
    process_sha256: str
    maximum_close_submissions: int = field(default=1, init=False)
    execution_authority: bool = field(default=False, init=False)


def _settings_pin_v1(settings):
    from app.config.settings import Settings

    _need(
        type(settings) is Settings
        and set(settings.__dict__) == set(Settings.model_fields)
        and not settings.__pydantic_extra__
    )
    integers = ("strategy_min_score", "okx_live_max_leverage")
    decimals = (
        "okx_live_max_notional_usdt",
        "okx_live_max_order_size_contracts",
        "okx_live_session_loss_limit_pct",
        "okx_live_scan_max_entry_drift_bps",
    )
    booleans = (
        "okx_live_require_protection",
        "okx_live_require_ip_bound_key",
        "okx_live_forbid_withdraw_permission",
        "okx_live_require_flat_start",
        "okx_live_auto_disarm",
    )
    _need(
        all(type(getattr(settings, name)) is int for name in integers)
        and all(
            type(getattr(settings, name)) is Decimal
            and getattr(settings, name).is_finite()
            for name in decimals
        )
        and all(getattr(settings, name) is True for name in booleans)
        and type(settings.okx_live_allowed_symbols) is str
    )
    return canonical(
        {
            **{name: getattr(settings, name) for name in integers},
            **{name: format(getattr(settings, name), "f") for name in decimals},
            **{name: getattr(settings, name) for name in booleans},
            "okx_live_allowed_symbols": settings.okx_live_allowed_symbols,
        }
    )


def _task(state, *, negative=False):
    _NATIVE_GUARD()  # Populated by the source-owned import-time guard.
    task = asyncio.current_task()
    _need(
        task is not None
        and task is state["task"]
        and (negative or not task.cancelling())
        and asyncio.get_running_loop() is state["loop"]
        and os.getpid() == state["pid"]
        and get_ident() == state["thread"]
        and not state["closed"]
    )
    runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    if not negative:
        _need(_settings_pin_v1(get_settings()) == state["settings_json"])


def _session(owner, *, current=False, negative=False):
    _need(type(owner) is OwnedLiveOperatorSessionV1)
    state = _OWNED.get(owner)
    _need(type(state) is dict)
    _task(state, negative=negative)
    lease = source._SOURCES[state["source_owner"]]["lease"]
    lease.verify_current()
    source._configured(source._SOURCES[state["source_owner"]]["plan"])
    now = _stamp()
    validate_stamps((state["last"].model_dump(), now.model_dump()))
    state["last"] = now
    if current:
        _need(
            state["active"] is True
            and state["approval_used"] is True
            and now.monotonic_ns < state["expiry_monotonic_ns"]
            and utc_from_ns(now.utc_ns) < state["deadline"]
        )
    return state


def _caps(view):
    from fractions import Fraction

    equity = Fraction(view["current_readback"].usdt_available_equity.available_equity)
    settings = get_settings()
    limits = (
        min(Fraction(1, 4), equity / 200),
        min(Fraction(1, 4), equity / 100),
        min(Fraction(25), equity * Fraction(3, 5)),
        min(Fraction(25), Fraction(settings.okx_live_max_notional_usdt)),
    )
    # Exact source equity decimals have finite base-ten representations. Caps
    # round DOWN if a fractional ceiling is finer than the DB numeric scale.
    from decimal import ROUND_FLOOR

    with localcontext(Context(prec=100)):
        result = tuple(
            (Decimal(v.numerator) / Decimal(v.denominator)).quantize(
                Decimal("1e-20"), rounding=ROUND_FLOOR
            )
            for v in limits
        )
    _need(all(v > 0 for v in result))
    return result


def _decode_control(readback):
    _need(
        type(readback) is controls.LiveOperatorControlReadbackV1
        and sha(readback.row_json) == readback.row_sha256
    )
    doc = decode(readback.row_json, maximum=16384)
    for name in ("updated_at", "lease_until", "arm_expires_at"):
        if doc[name] is not None:
            doc[name] = datetime.fromisoformat(doc[name])
            _need(doc[name].tzinfo is UTC)
    for name in (
        "per_trade_risk_cap",
        "portfolio_risk_cap",
        "margin_cap",
        "micro_notional_cap",
    ):
        doc[name] = Decimal(doc[name])
    return doc


def _consume_owned_live_operator_request_v1(owner, *, key):
    _need(key is controls._SOURCE_KEY_V1 and type(owner) is _ControlRequest)
    record = _CONTROL_REQUESTS.pop(owner, None)
    _need(type(record) is tuple and len(record) == 2)
    session, request = record
    state = _session(
        session, current=request.action == "refresh", negative=request.action == "stop"
    )
    _need(
        request.source_owner is state["source_owner"]
        and request.policy_sha256 == state["policy_sha256"]
        and request.process_sha256 == state["process_sha256"]
    )
    if request.action in ("clear_stop", "arm", "refresh"):
        _need(
            state["approval_used"] is True
            and state["approval_sha256"] == request.approval_sha256
        )
    return request


async def _control(session, action):
    state = _session(session, negative=action == "stop")
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    view = (
        source._replay_owned_live_source_v1(
            state["source_owner"], key=source._SOURCE_KEY
        )
        if action != "stop"
        else None
    )
    previous = state.get("control")
    if view is None:
        view = state["identity"]
    request = controls.LiveOperatorControlRequestV1(
        action,
        state["source_owner"],
        state.get("financial_owner"),
        view["uid"],
        view["main_uid"],
        view["credential_session_sha256"],
        view["source_revision_sha256"],
        state["policy_sha256"],
        state["process_sha256"],
        None if previous is None else previous["revision"],
        state.get("arm_id"),
        state.get("deadline"),
        state["lease_until"],
        state["caps"],
        state.get("approval_sha256"),
    )
    token = object.__new__(_ControlRequest)
    _CONTROL_REQUESTS[token] = session, request
    try:
        readback = await controls.NativeLiveOperatorControlRepositoryV1(
            cfg["operator_factory"], cfg["operator_pins"]
        ).apply_owned_request_v1(token)
        state["control"] = _decode_control(readback)
        state["control_readback"] = readback
        return state["control"]
    finally:
        _CONTROL_REQUESTS.pop(token, None)


def _consume_live_public_request_v1(request, *, key):
    _need(key is public._COORDINATOR_REQUEST_KEY_V1)
    if type(request) is _ClosePublicRequest:
        _NATIVE_GUARD()
        from app.okx_live import owned_lifecycle_v1 as lifecycle

        record = _CLOSE_PUBLIC_REQUESTS.pop(request, None)
        _need(type(record) is tuple and len(record) == 3)
        owner, handle, parameters = record
        consent = _replay_spent_owned_live_close_permission_v1(
            owner, handle, key=lifecycle._CLOSE_PERMISSION_KEY_V1
        )
        qualified, state = _close_consent_record_v1(
            owner, handle, used=True, key=lifecycle._CLOSE_PERMISSION_KEY_V1
        )
        _need(
            qualified["close_public_used"] is True
            and parameters.stage == "controlled_exit"
            and parameters.invocation is state["invocation"]
            and parameters.expires_at == consent.expires_at
            and parameters.expires_monotonic_ns == consent.expiry_monotonic_ns
            and parameters.public_policy_sha256 == public.CLOSE_POLICY_SHA256
            and parameters.candidate_sha256 == state["snapshot"].candidate_sha256
            and parameters.publication_barrier
            == state["publication"].publication_barrier
            and parameters.publication_receipt_sha256 == sha(state["publication_json"])
        )
        return parameters
    _need(type(request) is _PublicRequest)
    record = _PUBLIC_REQUESTS.pop(request, None)
    _need(type(record) is tuple and len(record) == 2)
    session, parameters = record
    state = _session(session, current=True)
    _need(
        parameters.invocation is state["invocation"]
        and parameters.expires_at <= state["deadline"]
        and parameters.expires_monotonic_ns <= state["expiry_monotonic_ns"]
    )
    return parameters


def _private_child(base, name):
    context = storage._windows_root if os.name == "nt" else storage._posix_root
    with context(base) as directory:
        _need(name not in directory.names())
        mkdir_private_evidence_child_v2(directory, name)
    return base / name


async def _public(session, stage):
    state = _session(session, current=True)
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    root = _private_child(cfg["public_root"], state["report_id"] + "_" + stage)
    publication = state.get("publication")
    parameters = public.LivePublicCaptureParametersV1(
        root,
        state["report_id"],
        state["instrument_id"],
        state["invocation"],
        stage,
        state["deadline"],
        state["expiry_monotonic_ns"],
        None if publication is None else state["snapshot"].candidate_sha256,
        None if publication is None else publication.publication_barrier,
        None if publication is None else sha(state["publication_json"]),
        public.POLICY_SHA256,
    )
    request = object.__new__(_PublicRequest)
    _PUBLIC_REQUESTS[request] = session, parameters
    try:
        owner = await public.capture_owned_live_public_v1(
            request, key=_PUBLIC_SOURCE_KEY_V1
        )
        state["public_owners"].append(owner)
        return owner, public._replay_owned_live_public_v1(
            owner, key=_PUBLIC_SOURCE_KEY_V1
        )
    finally:
        _PUBLIC_REQUESTS.pop(request, None)


@asynccontextmanager
async def _post_g12_public_stage_v2(session):
    """Own immutable post-publication OHLC before the final account capture."""
    state = _session(session, current=True)
    _publication(state)
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    root = _private_child(cfg["public_root"], state["report_id"] + "_post_g12")
    params = public.LivePublicCaptureParametersV1(
        root,
        state["report_id"],
        state["instrument_id"],
        state["invocation"],
        "post_g12",
        state["deadline"],
        state["expiry_monotonic_ns"],
        state["snapshot"].candidate_sha256,
        state["publication"].publication_barrier,
        sha(state["publication_json"]),
        public.STAGED_POLICY_SHA256,
    )
    request = object.__new__(_PublicRequest)
    _PUBLIC_REQUESTS[request] = session, params
    stage = None
    try:
        async with public.begin_owned_live_post_g12_public_v2(
            request, key=_PUBLIC_SOURCE_KEY_V1
        ) as stage:
            data = public.replay_owned_live_public_ohlc_stage_v2(
                stage, key=_PUBLIC_SOURCE_KEY_V1
            )
            _need(type(data) is public.LivePublicOHLCStageDataV2)
            _need(
                data.invocation is state["invocation"]
                and data.report_id == state["report_id"]
                and data.instrument_id == state["instrument_id"]
                and data.candidate_sha256 == state["snapshot"].candidate_sha256
                and data.publication_barrier == state["publication"].publication_barrier
                and data.publication_receipt_sha256 == sha(state["publication_json"])
                and data.execution_authority is data.public_complete is False
            )
            _STAGED_ACCOUNT_REQUESTS_V2[stage] = (
                session,
                state["source_owner"],
                data,
                tuple(
                    state["identity"][name]
                    for name in ("uid", "main_uid", "credential_session_sha256")
                ),
            )
            yield stage, data
    finally:
        _PUBLIC_REQUESTS.pop(request, None)
        if stage is not None:
            _STAGED_ACCOUNT_REQUESTS_V2.pop(stage, None)


def _consume_live_staged_account_ready_v2(stage, *, key):
    """One original stage + genuinely NEW account; no caller completeness flag."""
    _NATIVE_GUARD()
    _need(key is public._ACCOUNT_READY_KEY_V2)
    record = _STAGED_ACCOUNT_REQUESTS_V2.pop(stage, None)
    _need(type(record) is tuple and len(record) == 4)
    session, old_source, data, identity = record
    state = _session(session, current=True)
    owner = state["source_owner"]
    _need(owner is not old_source and type(owner) is source.OwnedLiveSourceV1)
    actual = source._SOURCES.get(owner)
    _need(type(actual) is dict and actual.get("previous_source_owner") is old_source)
    _need(actual["lease"] is source._SOURCES[old_source]["lease"])
    view = source._replay_owned_live_source_v1(owner, key=source._SOURCE_KEY)
    _need(
        tuple(view[name] for name in ("uid", "main_uid", "credential_session_sha256"))
        == identity
    )
    _need(view["plan"].publication_barrier == data.completed)
    _need(
        view["source_revision_sha256"]
        != source._SOURCES[old_source]["source_revision_sha256"]
    )
    _need(
        data.invocation is state["invocation"]
        and state["control"] == _decode_control(state["control_readback"])
    )
    control = state["control"]
    _need(
        control["environment"] == "live"
        and control["account_uid"] == view["uid"]
        and control["main_uid"] == view["main_uid"]
        and control["credential_session_sha256"] == view["credential_session_sha256"]
        and control["source_revision_sha256"] == view["source_revision_sha256"]
        and control["policy_sha256"] == state["policy_sha256"]
        and control["process_sha256"] == state["process_sha256"]
        and control["arm_id"] == state["arm_id"]
        and control["arm_expires_at"] == state["deadline"]
        and control["armed"] is True
        and control["emergency_stop"] is False
    )
    completed = view["current_completed"]
    validate_stamps((data.completed.model_dump(), completed.model_dump()))
    _need(
        completed.utc_ns > data.completed.utc_ns
        and completed.monotonic_ns > data.completed.monotonic_ns
    )
    return public.LiveStagedAccountReadyV2(
        state["invocation"], owner, completed, view["source_revision_sha256"]
    )


async def _capture_owned_live_close_public_v1(owner, physical_handle, *, key):
    """One NEW anonymous-public capture under the distinct close consent."""
    _NATIVE_GUARD()
    record, state = _close_consent_record_v1(owner, physical_handle, used=True, key=key)
    _need(record["close_public_used"] is False)
    record["close_public_used"] = True
    consent = record["close_permission_facts"]
    _need(consent == _close_consent_facts_v1(record, state))
    _publication(state)
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    root = _private_child(cfg["public_root"], state["report_id"] + "_controlled_exit")
    parameters = public.LivePublicCaptureParametersV1(
        root,
        state["report_id"],
        state["instrument_id"],
        state["invocation"],
        "controlled_exit",
        consent.expires_at,
        consent.expiry_monotonic_ns,
        state["snapshot"].candidate_sha256,
        state["publication"].publication_barrier,
        sha(state["publication_json"]),
        public.CLOSE_POLICY_SHA256,
        request_not_before=_stamp(),
    )
    request = object.__new__(_ClosePublicRequest)
    _CLOSE_PUBLIC_REQUESTS[request] = owner, physical_handle, parameters
    try:
        captured = await public.capture_owned_live_public_v1(
            request, key=_PUBLIC_SOURCE_KEY_V1
        )
        state["public_owners"].append(captured)
        _replay_spent_owned_live_close_permission_v1(owner, physical_handle, key=key)
        return captured, public._replay_owned_live_controlled_quote_v1(
            captured, key=_PUBLIC_SOURCE_KEY_V1
        )
    finally:
        _CLOSE_PUBLIC_REQUESTS.pop(request, None)


async def _financial(session):
    state = _session(session)
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    owner = await financial.observe_owned_live_financial_epoch_v1(
        state["source_owner"],
        factory=cfg["collector_factory"],
        role_pins=cfg["collector_pins"],
    )
    state["financial_owner"] = owner
    facts = financial._replay_owned_live_financial_epoch_v1(owner, key=financial._KEY)
    _need(facts.missing_obligations == ())
    return facts


async def _local(session):
    state = _session(session, current=True)
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    async with cfg["writer_factory"]() as sql, sql.begin():
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
            *ledger._SETTINGS[1:],
        ):
            await sql.execute(text(statement))
        await ledger._profile(sql, cfg["writer_pins"])
        await require_legacy_live_negative_fence_v1(sql)
        uid = state["identity"]["uid"]
        rows = (
            (
                await sql.execute(
                    text(
                        "SELECT * FROM public.native_live_controls_v1 WHERE environment='live' AND account_uid=:uid"
                    ),
                    {"uid": uid},
                )
            )
            .mappings()
            .all()
        )
        _need(len(rows) == 1 and dict(rows[0]) == state["control"])
        active, count = (
            await sql.execute(
                text(
                    "SELECT count(*) FILTER(WHERE state<>'reconciled'),count(*) "
                    "FROM public.native_live_reservations_v1 WHERE environment='live' AND account_uid=:uid"
                ),
                {"uid": uid},
            )
        ).one()
        _need(
            type(active) is int and type(count) is int and active == 0 and count <= 4096
        )
        keys = (
            (
                await sql.execute(
                    text(
                        "SELECT event_key FROM public.native_live_reservations_v1 "
                        "WHERE environment='live' AND account_uid=:uid ORDER BY event_key"
                    ),
                    {"uid": uid},
                )
            )
            .scalars()
            .all()
        )
        _need(len(keys) == count and len(set(keys)) == count)
        now = await sql.scalar(text("SELECT clock_timestamp()"))
        _need(now.tzinfo is UTC)
    stamp = LiveEvidenceStamp(
        environment="live",
        account_id=uid,
        complete=True,
        source_sha256=sha(
            canonical(
                [
                    "native_live_actual_local_read_v1",
                    state["control_readback"].row_sha256,
                    keys,
                ]
            )
        ),
        observed_at=now,
        received_at=utc_from_ns(_stamp().utc_ns),
    )
    return frozenset(keys), stamp, active


def _publication(state):
    publication = state["publication"]
    _need(
        canonical(publication.receipt.model_dump(mode="json"))
        == state["publication_json"]
        and publication.receipt.status == "written"
        and publication.snapshot_sha256
        == sha(canonical(state["snapshot"].model_dump(mode="json", round_trip=True)))
    )
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    context = storage._windows_root if os.name == "nt" else storage._posix_root
    with (
        context(cfg["evidence_root"]) as directory,
        directory.child(state["report_id"]) as child,
    ):
        _need(set(child.names()) == set(storage.FILE_NAMES))
        for record in publication.receipt.files:
            raw = child.read(
                record.name,
                storage.MAX_REPORT_BYTES
                if record.name == "report.json"
                else storage.MAX_PNG_BYTES,
            )
            _need(len(raw) == record.size_bytes and sha(raw) == record.sha256)


def _qualified(owner, *, spent, current=True):
    _need(type(owner) is OwnedLiveQualificationV1)
    record = _QUALIFIED.get(owner)
    _need(type(record) is dict and record["spent"] is spent)
    state = _session(record["session"], current=current, negative=not current)
    if current:
        entry_proof.replay_native_entry_v1(
            owner, record, state, key=_ENTRY_PROOF_KEY_V1
        )
    return record, state


def _require_native_entry_public_owner_v1(owner, public_owner, *, key):
    """The raw ENTRY reader accepts only this original private R7 proof."""
    if type(owner) is entry_original._OriginalMathProofV2:
        entry_original.require_public_bank_scope_v2(owner, public_owner, key=key)
    else:
        entry_proof.require_original_public_owner_v1(owner, public_owner, key=key)


def _replay_owned_live_qualification_v1(owner, *, key):
    _need(key is physical._QUALIFICATION_SOURCE_KEY_V1)
    record, _ = _qualified(owner, spent=False)
    return record["facts"]


def _consume_owned_live_qualification_v1(owner, *, key):
    _need(key is physical._QUALIFICATION_SOURCE_KEY_V1)
    record, _ = _qualified(owner, spent=False)
    record["spent"] = True
    return record["facts"]


def _verify_spent_live_final_dispatch_v1(owner, *, key):
    _need(key is physical._QUALIFICATION_SOURCE_KEY_V1)
    record, _ = _qualified(owner, spent=True)
    gate = physical._ACTIVE.get()
    held = [
        item
        for item in physical._HELD.values()
        if item["owner"] is owner
        and item["facts"] is record["facts"]
        and item.get("consumed") is True
    ]
    _need(
        len(held) == 1
        and held[0]["session"].get_transaction() is held[0]["transaction"]
    )
    if gate is not None:
        _need(physical._record(gate) is held[0])
    else:
        _need("gate" not in held[0])
    return record["facts"]


def _inhibit_owned_live_qualification_v1(owner, *, code, key):
    _need(
        key is physical._QUALIFICATION_SOURCE_KEY_V1
        and type(code) is str
        and 0 < len(code) <= 128
    )
    record = _QUALIFIED.get(owner)
    _need(type(record) is dict)
    record["revoked"] = True
    state = _OWNED.get(record["session"])
    if type(state) is dict:
        state["active"] = False
        state["inhibit_code"] = code


def _consume_owned_live_postsubmit_v1(owner, physical_handle, *, key):
    _NATIVE_GUARD()
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(key is lifecycle._POSTSUBMIT_HANDOFF_KEY_V1)
    record, state = _qualified(owner, spent=True, current=False)
    _need(record["postsubmit_used"] is False)
    actual = physical._record(physical_handle)
    _need(
        actual["owner"] is owner
        and actual["facts"] is record["facts"]
        and actual["consumed"] is True
    )
    record["postsubmit_used"] = True
    cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
    return LivePostsubmitHandoffFactsV1(
        record["facts"],
        state["runtime"],
        physical_handle,
        cfg["evidence_root"],
        state["report_id"],
        state["publication"].publication_barrier,
        state["publication_json"],
        state["challenge_id"],
        state["exit_authorized"],
        canonical(state["policy"].model_dump(mode="json")),
        state["original_candidate_json"],
        state["initial_math"].receipt_json,
        state["current_math"].receipt_json,
        record["last_final_recheck_json"],
    )


def _close_consent_record_v1(owner, physical_handle, *, used, key):
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(key is lifecycle._CLOSE_PERMISSION_KEY_V1)
    record, state = _qualified(owner, spent=True, current=False)
    _need(record["postsubmit_used"] is True and record["close_permission_used"] is used)
    actual = physical._record(physical_handle)
    _need(
        actual["owner"] is owner
        and actual["facts"] is record["facts"]
        and actual["consumed"] is True
        and state["physical_handle"] is physical_handle
    )
    _need(
        record["revoked"] is False
        and state["active"] is True
        and state["exit_requested"] is True
        and state["exit_authorized"] is True
        and state["closed"] is False
        and state.get("inhibit_code") is None
    )
    _need(
        type(state["close_consent_received"]) is ClockStamp
        and state["close_expiry_monotonic_ns"]
        == state["close_consent_received"].monotonic_ns
        + CONTROLLED_EXIT_SECONDS * 1_000_000_000
        and state["close_expires_at"]
        == utc_from_ns(state["close_consent_received"].utc_ns)
        + timedelta(seconds=CONTROLLED_EXIT_SECONDS)
    )
    now = _stamp()
    # _qualified/_session just extended the measured <=60s clock chain. The
    # separate120s consent lifetime never widens a native clock batch.
    validate_stamps((state["last"].model_dump(), now.model_dump()))
    _need(
        now.monotonic_ns >= state["close_consent_received"].monotonic_ns
        and now.utc_ns >= state["close_consent_received"].utc_ns
    )
    _need(
        now.monotonic_ns < state["close_expiry_monotonic_ns"]
        and utc_from_ns(now.utc_ns) < state["close_expires_at"],
        "native_live_controlled_close_consent_expired",
    )
    return record, state


def _close_consent_facts_v1(record, state):
    facts = record["facts"]
    received = state["close_consent_received"]
    _need(type(received) is ClockStamp)
    expires = utc_from_ns(received.utc_ns) + timedelta(seconds=CONTROLLED_EXIT_SECONDS)
    expiry_monotonic = received.monotonic_ns + CONTROLLED_EXIT_SECONDS * 1_000_000_000
    _need(
        state["close_expires_at"] == expires
        and state["close_expiry_monotonic_ns"] == expiry_monotonic
    )
    expected_sha = sha(
        canonical(
            [
                "ctcc-native-live-human-controlled-close-v1",
                state["approval_sha256"],
                state["close_consent_received"].model_dump(),
                CONTROLLED_EXIT_SECONDS,
                state["challenge_id"],
                state["identity"]["uid"],
                state["identity"]["main_uid"],
                state["process_sha256"],
            ]
        )
    )
    _need(state["close_consent_sha256"] == expected_sha)
    return LiveControlledCloseConsentFactsV1(
        state["challenge_id"],
        received,
        expires,
        expiry_monotonic,
        expected_sha,
        facts.account_uid,
        facts.main_uid,
        facts.credential_session_sha256,
        facts.reservation_id,
        exact_live_intent_document_v1(facts.intent_data)[2],
        state["process_sha256"],
    )


def _consume_owned_live_close_permission_v1(owner, physical_handle, *, key):
    """Burn only the exact separately confirmed close permission before await."""
    _NATIVE_GUARD()
    record, state = _close_consent_record_v1(
        owner, physical_handle, used=False, key=key
    )
    data = _close_consent_facts_v1(record, state)
    record["close_permission_used"] = True
    record["close_permission_facts"] = data
    return data


def _replay_spent_owned_live_close_permission_v1(owner, physical_handle, *, key):
    _NATIVE_GUARD()
    record, state = _close_consent_record_v1(owner, physical_handle, used=True, key=key)
    data = record["close_permission_facts"]
    _need(type(data) is LiveControlledCloseConsentFactsV1)
    _need(data == _close_consent_facts_v1(record, state))
    return data


def signal_live_operator_approval_v1(
    *, challenge_id, arm_phrase, clear_stop_phrase, controlled_exit_phrase=""
):
    """Only signal DATA; the original worker consumes/validates it once.

    The tool HTTP route must authenticate its operator session before exposing
    this function. Knowing a saved challenge after retirement has no effect.
    """
    _need(
        type(challenge_id) is str
        and type(arm_phrase) is str
        and type(clear_stop_phrase) is str
        and type(controlled_exit_phrase) is str
    )
    state = _CHALLENGES.pop(challenge_id, None)
    _need(
        type(state) is dict
        and not state["closed"]
        and state["approval_used"] is False
        and asyncio.get_running_loop() is state["loop"]
        and not state["approval_future"].done()
    )
    received = _stamp()
    validate_stamps((state["last"].model_dump(), received.model_dump()))
    _need(
        received.monotonic_ns < state["challenge_expiry_monotonic_ns"],
        "native_live_human_challenge_expired",
    )
    state["approval_received"] = received
    state["approval_future"].set_result(
        (challenge_id, arm_phrase, clear_stop_phrase, controlled_exit_phrase)
    )


def revoke_live_operator_session_v1(session):
    """Synchronous local negative authority; call before awaiting a DB stop."""
    state = _OWNED.get(session)
    _need(type(session) is OwnedLiveOperatorSessionV1 and type(state) is dict)
    state["active"] = False
    state["inhibit_code"] = "native_live_operator_stop"
    _CHALLENGES.pop(state["challenge_id"], None)
    if not state["approval_future"].done():
        state["approval_future"].cancel()
    for record in tuple(_QUALIFIED.values()):
        if record["session"] is session:
            record["revoked"] = True


async def _refresh(session, barrier):
    state = _session(session)
    owner = await source.refresh_owned_live_source_v1(
        state["source_owner"],
        publication_barrier=barrier,
        invocation_id=secrets.token_hex(16),
    )
    state["source_owner"] = owner
    view = source._replay_owned_live_source_v1(owner, key=source._SOURCE_KEY)
    _need(
        (
            view["uid"],
            view["main_uid"],
            view["credential_session_sha256"],
            view["account_config"],
        )
        == (
            state["identity"]["uid"],
            state["identity"]["main_uid"],
            state["identity"]["credential_session_sha256"],
            state["identity"]["account_config"],
        )
    )
    state["identity"] = view
    return await _financial(session)


def _risk_inputs(state, facts, instrument, public_value, local):
    keys, stamp, active = local
    return adapter.live_finite_inputs_data_v1(
        facts=facts,
        instrument=instrument,
        policy=state["policy"],
        control_row=state["control"],
        evaluated_at=public_value.evaluated_at,
        deadline=state["deadline"],
        local_stamp=stamp,
        local_active_count=active,
    ), keys


def _candidate(state, calculation):
    operands = decode(calculation.risk_operands_json, maximum=16384)
    return canonical(
        {
            "environment": "live",
            "instrument_id": state["instrument_id"],
            "direction": state["direction"],
            "strategy": state["strategy"],
            "entry": format(calculation.intent.candidate_entry, "f"),
            "stop_loss": format(calculation.result.stop_loss, "f"),
            "take_profit": format(calculation.result.take_profit, "f"),
            "event_key": operands["event_key"],
            "policy_sha256": state["policy_sha256"],
            "expires_at": operands["expires_at"],
            "original_geometry_sha256": calculation.candidate_geometry_sha256,
            "original_evaluation_sha256": calculation.evaluation_sha256,
        }
    )


def _intent(state, calculation, instrument):
    original = decode(state["original_candidate_json"], maximum=32768)
    operands = decode(calculation.risk_operands_json, maximum=16384)
    entries = tuple(
        Decimal(operands[name]) for name in ("candidate_entry", "executable_reference")
    )
    samples = tuple(
        math._sample_risk(
            calculation.risk_inputs,
            intent=calculation.intent,
            policy=calculation.policy,
            entry=entry,
            stop=calculation.result.stop_loss,
            cost=Decimal(operands["round_trip_cost_per_base"]),
            now=calculation.source.evaluated_at,
        )
        for entry in entries
    )
    _need(all(item.passed for item in samples))
    with localcontext(Context(prec=100)):
        risks = tuple(
            item.max_loss_amount.quantize(Decimal("1e-20"), rounding=ROUND_CEILING)
            for item in samples
        )
        notional = max(item.notional for item in samples).quantize(
            Decimal("1e-20"), rounding=ROUND_CEILING
        )
        margin = max(item.required_margin for item in samples).quantize(
            Decimal("1e-20"), rounding=ROUND_CEILING
        )
    expiry = min(
        state["deadline"],
        datetime.fromisoformat(original["expires_at"]),
        datetime.fromisoformat(operands["expires_at"]),
        # The ledger requires the durable intent itself to expire inside the
        # original account observation's five-second window. Floor to the
        # supported datetime precision so rounding cannot extend that bound.
        datetime(1970, 1, 1, tzinfo=UTC)
        + timedelta(
            microseconds=(
                source._SOURCES[state["source_owner"]]["current_completed"].utc_ns
                + source._SOURCES[state["source_owner"]]["plan"].max_current_age_ns
            )
            // 1000
        ),
    )
    return LiveIntentDataV1(
        account_uid=state["identity"]["uid"],
        main_uid=state["identity"]["main_uid"],
        credential_session_sha256=state["identity"]["credential_session_sha256"],
        source_revision_sha256=state["identity"]["source_revision_sha256"],
        process_sha256=state["process_sha256"],
        policy_sha256=state["policy_sha256"],
        arm_id=state["arm_id"],
        expected_control_revision=state["control"]["revision"],
        event_key=original["event_key"],
        candidate_sha256=sha(state["original_candidate_json"]),
        original_candidate_json=state["original_candidate_json"].decode("ascii"),
        report_sha256=state["publication"].receipt.report_sha256,
        evidence_sha256=sha(state["publication_json"]),
        recheck_sha256=calculation.evaluation_sha256,
        instrument_id=state["instrument_id"],
        direction=state["direction"],
        entry=calculation.intent.candidate_entry,
        stop_loss=calculation.result.stop_loss,
        take_profit=calculation.result.take_profit,
        executable_price_bound=entries[1],
        contracts=instrument.contracts,
        leverage=instrument.leverage,
        position_side=instrument.position_side,
        client_order_id="CTCCL" + secrets.token_hex(13),
        protection_client_id="CTCCA" + secrets.token_hex(13),
        risk_samples=risks,
        margin_amount=margin,
        notional_amount=notional,
        deadline=expiry,
    )


async def run_owned_live_operator_once_v1(session):
    """Original worker only; exactly one approval and at most one physical POST."""
    state = _session(session)
    _need(not state["run_started"])
    state["run_started"] = True
    try:
        while not state["approval_future"].done():
            remaining = (
                state["challenge_expiry_monotonic_ns"] - _stamp().monotonic_ns
            ) / 1e9
            _need(remaining > 0, "native_live_human_challenge_expired")
            await asyncio.wait(
                (state["approval_future"],), timeout=min(30.0, remaining)
            )
            # Native clock continuity is sampled while waiting; a human may
            # take120s without stretching a single60s clock-evidence batch.
            _session(session)
        response = state["approval_future"].result()
        _session(session)
        state["approval_used"] = True  # Invalid answers burn this challenge too.
        _need(
            response
            == (
                state["challenge_id"],
                ARM_PHRASE + ":" + state["nonce"],
                CLEAR_STOP_PHRASE + ":" + state["nonce"],
                EXIT_PHRASE + ":" + state["nonce"] if state["exit_requested"] else "",
            )
        )
        state["exit_authorized"] = state["exit_requested"]
        _need(_stamp().monotonic_ns < state["challenge_expiry_monotonic_ns"])
        state["approval_sha256"] = sha(
            canonical(
                [
                    "native-live-human-response-v1",
                    state["challenge_json"].decode("ascii"),
                    *response,
                ]
            )
        )
        if state["exit_authorized"]:
            received = state["approval_received"]
            _need(type(received) is ClockStamp)
            state["close_consent_received"] = received
            state["close_expires_at"] = utc_from_ns(received.utc_ns) + timedelta(
                seconds=CONTROLLED_EXIT_SECONDS
            )
            state["close_expiry_monotonic_ns"] = (
                received.monotonic_ns + CONTROLLED_EXIT_SECONDS * 1_000_000_000
            )
            state["close_consent_sha256"] = sha(
                canonical(
                    [
                        "ctcc-native-live-human-controlled-close-v1",
                        state["approval_sha256"],
                        received.model_dump(),
                        CONTROLLED_EXIT_SECONDS,
                        state["challenge_id"],
                        state["identity"]["uid"],
                        state["identity"]["main_uid"],
                        state["process_sha256"],
                    ]
                )
            )
        facts = await _refresh(session, _stamp())
        view = state["identity"]
        current_instrument = adapter.live_instrument_data_v1(
            view,
            instrument_id=state["instrument_id"],
            direction=state["direction"],
            settings=get_settings(),
        )
        current_policy = adapter.fixed_live_qualification_policy_v1(
            current_instrument,
            epoch_started_at=facts.epoch_started_at,
            settings=get_settings(),
        )
        _need(
            current_policy == state["policy"]
            and _instrument_identity(current_instrument)
            == _instrument_identity(state["instrument"]),
            "native_live_challenge_scope_changed",
        )
        await _control(session, "prepare")
        await _control(session, "clear_stop")
        now = _stamp()
        state["deadline"] = utc_from_ns(now.utc_ns) + timedelta(seconds=ARM_SECONDS)
        state["expiry_monotonic_ns"] = now.monotonic_ns + ARM_SECONDS * 1_000_000_000
        state["arm_id"] = sha(
            canonical(
                [state["approval_sha256"], now.model_dump(), secrets.token_hex(32)]
            )
        )
        await _control(session, "arm")
        state["active"] = True
        initial_local = await _local(session)
        _initial_owner, initial_public = await _public(session, "initial")
        state["initial_public_owner"] = _initial_owner
        inputs, keys = _risk_inputs(
            state, facts, current_instrument, initial_public, initial_local
        )
        entry = (
            initial_public.quote.ticker.ask
            if state["direction"] == "long"
            else initial_public.quote.ticker.bid
        )
        intent = QualificationIntent(
            report_id=state["report_id"],
            instrument_id=state["instrument_id"],
            strategy=state["strategy"],
            direction=state["direction"],
            candidate_entry=entry,
            created_at=initial_public.evaluated_at,
            expires_at=state["deadline"],
        )
        initial = math.evaluate_live_qualification_math_v1(
            initial_public,
            intent=intent,
            policy=state["policy"],
            consumed_event_keys=keys,
            risk_inputs=inputs,
        )
        _need(
            initial.pre_evidence_complete,
            "native_live_initial_qualification_wait_or_fail",
        )
        state["initial_math"] = initial
        state["original_candidate_json"] = _candidate(state, initial)
        entry_original.prepare_original_math_v2(
            session, key=_ENTRY_ORIGINAL_MATH_KEY_V2
        )
        market, analysis = _market_analysis(initial)
        protection = math._selection(
            initial.detection,
            market,
            analysis,
            initial.policy,
            entry,
            initial_public.evaluated_at,
        )
        snapshot = evidence.prepare_live_evidence_v1(
            market,
            analysis,
            qualification=initial.result,
            detection=initial.detection,
            quote=initial_public.quote,
            protection=protection,
            prepared_at=initial_public.evaluated_at,
            zone_tick_size=current_instrument.tick_size,
        )
        state["snapshot"] = snapshot
        cfg = runtime._replay_live_runtime_v1(state["runtime"], key=runtime._KEY)
        publication = evidence.publish_live_evidence_v1(cfg["evidence_root"], snapshot)
        state["publication"] = publication
        state["publication_json"] = canonical(
            publication.receipt.model_dump(mode="json")
        )
        _publication(state)
        entry_original.bind_original_publication_v2(
            session, key=_ENTRY_ORIGINAL_MATH_KEY_V2
        )
        _session(session, current=True)
        async with _post_g12_public_stage_v2(session) as (stage, ohlc):
            fresh_facts = await _refresh(session, ohlc.completed)
            fresh_instrument = adapter.live_instrument_data_v1(
                state["identity"],
                instrument_id=state["instrument_id"],
                direction=state["direction"],
                settings=get_settings(),
            )
            _need(
                _instrument_identity(fresh_instrument)
                == _instrument_identity(current_instrument)
            )
            await _control(session, "refresh")
            current_local = await _local(session)
            current_owner = await public.complete_owned_live_post_g12_public_v2(
                stage, key=_PUBLIC_SOURCE_KEY_V1
            )
            state["public_owners"].append(current_owner)
            current_public = public._replay_owned_live_public_v1(
                current_owner, key=_PUBLIC_SOURCE_KEY_V1
            )
        current_inputs, current_keys = _risk_inputs(
            state, fresh_facts, fresh_instrument, current_public, current_local
        )
        current = entry_original.recheck_original_math_v2(
            session,
            current_public,
            consumed_event_keys=current_keys,
            risk_inputs=current_inputs,
            key=_ENTRY_ORIGINAL_MATH_KEY_V2,
        )
        _need(current.pre_evidence_complete, "native_live_post_g12_cancelled")
        state.update(
            current_math=current,
            current_public=current_public,
            current_public_owner=current_owner,
            current_local=current_local,
            current_instrument=fresh_instrument,
            current_financial_facts=fresh_facts,
        )
        value = _intent(state, current, fresh_instrument)
        state["entry_native_intent"] = value
        entry_proof.prepare_pending_entry_v1(session, key=_ENTRY_PROOF_KEY_V1)
        repository = ledger.NativeLiveLedgerDataRepositoryV1(
            cfg["writer_factory"], cfg["writer_pins"]
        )
        readback = await repository.reserve_and_commit_intent_data_v1(
            value, owned_source=state["source_owner"]
        )
        reservation, raw, digest = exact_live_intent_document_v1(value)
        _need(
            readback["reservation_id"] == reservation
            and readback["intent_sha256"] == digest
            and readback["state"] == "reserved"
        )
        doc = decode(raw, maximum=65536)
        qualified = object.__new__(OwnedLiveQualificationV1)
        final_facts = LiveQualifiedExecutionFactsV1(
            value,
            canonical(readback),
            reservation,
            doc["request_json"].encode("ascii"),
            doc["request_sha256"],
            cfg["writer_factory"],
            cfg["writer_pins"],
            state["source_owner"],
            current_owner,
            value.deadline,
            state["expiry_monotonic_ns"]
            - (source._utc_ns(state["deadline"]) - source._utc_ns(value.deadline)),
            value.credential_session_sha256,
            value.account_uid,
            value.main_uid,
            value.arm_id,
        )
        _QUALIFIED[qualified] = {
            "session": session,
            "facts": final_facts,
            "spent": False,
            "revoked": False,
            "postsubmit_used": False,
            "close_permission_used": False,
            "close_permission_facts": None,
            "close_public_used": False,
        }
        state["qualified_owner"] = qualified
        state["entry_native_readback"] = readback
        entry_proof.complete_native_entry_v1(
            session, qualified, key=_ENTRY_PROOF_KEY_V1
        )
        _qualified(qualified, spent=False)
        try:
            handle = await physical.submit_owned_live_fok_v1(qualified)
        except Exception as failure:
            handle = getattr(failure, "_owned_live_response_v1", None)
            if handle is None:
                raise
            state["active"] = False
        state["physical_handle"] = handle
        # No result is declared protected/reconciled by this handoff. The
        # separate owned lifecycle must read actual exchange state and resolve.
        return qualified, handle
    except BaseException:
        entry_original.retire_original_math_v2(session, key=_ENTRY_ORIGINAL_MATH_KEY_V2)
        entry_proof.retire_pending_entry_v1(session, key=_ENTRY_PROOF_KEY_V1)
        state["active"] = False
        state["inhibit_code"] = "native_live_invocation_failed_do_not_retry"
        raise
    finally:
        _CHALLENGES.pop(state["challenge_id"], None)
        state["approval_used"] = True


def _market_analysis(calculation):
    from app.domain.analysis import MultiTimeframeAnalysis
    from app.domain.market import MarketSnapshot

    doc = decode(calculation.source_json.encode("ascii"), maximum=8 * 1024 * 1024)
    return (
        MarketSnapshot.model_validate_json(canonical(doc["market"]), strict=True),
        MultiTimeframeAnalysis.model_validate_json(
            canonical(doc["analysis"]), strict=True
        ),
    )


def _instrument_identity(value):
    # Receipt/timing changes are expected after refresh; economic source facts
    # and selected existing leverage must remain exactly fixed.
    return canonical(
        {
            "spec": {
                k: v
                for k, v in value.spec.model_dump(mode="json").items()
                if k not in ("source_sha256", "observed_at", "received_at")
            },
            "tick_size": str(value.tick_size),
            "contracts": str(value.contracts),
            "leverage": value.leverage,
            "position_side": value.position_side,
        }
    )


@asynccontextmanager
async def _open_live_operator_body_v1(
    runtime_owner,
    *,
    instrument_id,
    strategy,
    direction,
    controlled_exit_authorized=False,
):
    """Keep this context and run_once in ONE background worker task.

    The UI receives only session.challenge_data, then signals its exact nonce
    response using signal_live_operator_approval_v1 in its authenticated route.
    No actual invocation is made by importing this module or installing a role.
    """
    _NATIVE_GUARD()  # Populated by the source-owned import-time guard.
    cfg = runtime._replay_live_runtime_v1(runtime_owner, key=runtime._KEY)
    _need(cfg["region"] == "global", "native_live_public_region_not_supported")
    _need(
        len(_OWNED) < _MAX_SESSIONS
        and type(strategy) is str
        and strategy in _STRATEGIES
        and type(direction) is str
        and direction in ("long", "short")
        and type(instrument_id) is str
        and instrument_id in public.journal.GLOBAL_INSTRUMENTS
        and type(controlled_exit_authorized) is bool,
        "native_live_scope_or_exit_authorization_unavailable",
    )
    from types import FunctionType

    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    preflight = lifecycle.preflight_owned_live_lifecycle_runtime_v1
    _need(
        type(preflight) is FunctionType
        and preflight.__module__ == lifecycle.__name__
        and Path(lifecycle.__file__).resolve()
        == Path(__file__).with_name("owned_lifecycle_v1.py")
    )
    _source_code(
        preflight, Path(lifecycle.__file__).read_bytes(), preflight.__code__.co_filename
    )
    await preflight(runtime_owner)
    await ledger.NativeLiveLedgerDataRepositoryV1(
        cfg["writer_factory"], cfg["writer_pins"]
    ).preflight_data_v1()
    await financial.preflight_live_financial_collector_v1(
        factory=cfg["collector_factory"], role_pins=cfg["collector_pins"]
    )
    now = _stamp()
    end = utc_from_ns(now.utc_ns)
    plan = source.LiveSourcePlanV1(
        secrets.token_hex(16), cfg["region"], end - timedelta(days=8), end, now
    )
    session = None
    async with source.acquire_owned_live_source_v1(
        plan, private_root=cfg["source_root"]
    ) as source_owner:
        view = source._replay_owned_live_source_v1(source_owner, key=source._SOURCE_KEY)
        settings = get_settings()
        _need(
            view["account_config"].position_mode == "net_mode",
            "native_live_net_position_mode_required",
        )
        _need(instrument_id in settings.okx_live_allowed_symbols.split(","))
        session = object.__new__(OwnedLiveOperatorSessionV1)
        stamp = _stamp()
        process_sha = sha(
            canonical(
                [
                    "ctcc-live-process-v1",
                    _PROCESS_NONCE,
                    os.getpid(),
                    str(Path(__file__).resolve()),
                ]
            )
        )
        challenge_id, nonce = secrets.token_hex(32), secrets.token_hex(16)
        state = {
            "runtime": runtime_owner,
            "settings_json": _settings_pin_v1(settings),
            "task": asyncio.current_task(),
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": get_ident(),
            "closed": False,
            "last": stamp,
            "source_owner": source_owner,
            "identity": view,
            "active": False,
            "approval_used": False,
            "run_started": False,
            "approval_future": asyncio.get_running_loop().create_future(),
            "process_sha256": process_sha,
            "instrument_id": instrument_id,
            "strategy": strategy,
            "direction": direction,
            "invocation": object(),
            "report_id": "CTCCL" + secrets.token_hex(16),
            "caps": _caps(view),
            "public_owners": [],
            "challenge_id": challenge_id,
            "nonce": nonce,
            "exit_authorized": False,
            "exit_requested": controlled_exit_authorized,
            "approval_received": None,
            "close_consent_received": None,
            "close_expires_at": None,
            "close_expiry_monotonic_ns": None,
            "close_consent_sha256": None,
            "lease_until": utc_from_ns(stamp.utc_ns)
            + timedelta(seconds=CHALLENGE_SECONDS + ARM_SECONDS + 30),
            "challenge_expiry_monotonic_ns": stamp.monotonic_ns
            + CHALLENGE_SECONDS * 1_000_000_000,
            "policy_sha256": sha(
                canonical(
                    ["ctcc-native-live-stopped-preparation-v1", public.POLICY_SHA256]
                )
            ),
            "challenge_json": b"",
            "control": None,
        }
        _OWNED[session] = state
        try:
            existing = await controls.NativeLiveOperatorControlRepositoryV1(
                cfg["operator_factory"], cfg["operator_pins"]
            ).read_control_data_v1(view["uid"])
            if existing is not None:
                state["control"] = _decode_control(existing)
                state["caps"] = tuple(
                    min(a, state["control"][name])
                    for a, name in zip(
                        state["caps"],
                        (
                            "per_trade_risk_cap",
                            "portfolio_risk_cap",
                            "margin_cap",
                            "micro_notional_cap",
                        ),
                        strict=True,
                    )
                )
                # Restart never inherits an Arm or a cleared process latch.
                await _control(session, "stop")
            await _control(session, "prepare")
            facts = await _financial(session)
            instrument = adapter.live_instrument_data_v1(
                view,
                instrument_id=instrument_id,
                direction=direction,
                settings=settings,
            )
            policy = adapter.fixed_live_qualification_policy_v1(
                instrument, epoch_started_at=facts.epoch_started_at, settings=settings
            )
            state.update(
                instrument=instrument, policy=policy, policy_sha256=policy_hash(policy)
            )
            await _control(session, "prepare")
            state["challenge_json"] = canonical(
                {
                    "schema": "ctcc.native_live.operator_challenge.v1",
                    "challenge_id": challenge_id,
                    "nonce": nonce,
                    "instrument_id": instrument_id,
                    "strategy": strategy,
                    "direction": direction,
                    "policy_sha256": state["policy_sha256"],
                    "account_identity_sha256": sha(
                        canonical(["live", view["uid"], view["main_uid"]])
                    ),
                    "account_label": "…" + view["uid"][-4:],
                    "credential_session_sha256": view["credential_session_sha256"],
                    "process_sha256": process_sha,
                    "arm_phrase": ARM_PHRASE + ":" + nonce,
                    "clear_stop_phrase": CLEAR_STOP_PHRASE + ":" + nonce,
                    "caps": [str(v) for v in state["caps"]],
                    "existing_leverage": instrument.leverage,
                    "candidate_created": False,
                    "controlled_exit_authorized": False,
                    "controlled_exit_requested": controlled_exit_authorized,
                    "controlled_exit_seconds": CONTROLLED_EXIT_SECONDS
                    if controlled_exit_authorized
                    else 0,
                    "controlled_exit_phrase": EXIT_PHRASE + ":" + nonce
                    if controlled_exit_authorized
                    else "",
                    "challenge_expires_at": (
                        utc_from_ns(stamp.utc_ns) + timedelta(seconds=CHALLENGE_SECONDS)
                    ).isoformat(),
                    "arm_seconds": ARM_SECONDS,
                    "maximum_submissions": 1,
                    "execution_authority": False,
                }
            )
            _CHALLENGES[challenge_id] = state
            yield session
        finally:
            state["active"] = False
            entry_original.retire_original_math_v2(
                session, key=_ENTRY_ORIGINAL_MATH_KEY_V2
            )
            _CHALLENGES.pop(challenge_id, None)
            if not state["approval_future"].done():
                state["approval_future"].cancel()
            for owner in state["public_owners"]:
                owner.close()
            # Durable stop is necessary even when qualification never reached
            # POST. A stop-write failure is retained, never treated as cleared.
            try:
                if state["control"] is not None:
                    await _control(session, "stop")
            finally:
                state["closed"] = True
                _OWNED.pop(session, None)


@asynccontextmanager
async def open_live_operator_session_v1(
    runtime_owner,
    *,
    instrument_id,
    strategy,
    direction,
    controlled_exit_authorized=False,
):
    """Own account admission before any await; retain one original worker."""
    _NATIVE_GUARD()
    runtime._replay_live_runtime_v1(runtime_owner, key=runtime._KEY)
    uid = get_settings().okx_live_expected_uid.get_secret_value()
    _need(
        uid not in _OPENING
        and len(_OPENING) + len(_OWNED) < _MAX_SESSIONS
        and not any(
            state["identity"]["uid"] == uid and not state["closed"]
            for state in _OWNED.values()
        ),
        "native_live_account_worker_already_owned",
    )
    task = asyncio.current_task()
    _need(task is not None)
    _OPENING[uid] = task
    try:
        await runtime.inhibit_configured_live_runtime_v1(runtime_owner)
        async with _open_live_operator_body_v1(
            runtime_owner,
            instrument_id=instrument_id,
            strategy=strategy,
            direction=direction,
            controlled_exit_authorized=controlled_exit_authorized,
        ) as session:
            yield session
    finally:
        if _OPENING.get(uid) is task:
            _OPENING.pop(uid)


# Pin actual async-generator source bodies, not only contextlib's decorator
# closure identity. Changing an inner.__code__ cannot create an owned source.
_OPEN_BODY_SOURCE_V1 = _open_live_operator_body_v1.__wrapped__
_OPEN_ENTRY_SOURCE_V1 = open_live_operator_session_v1.__wrapped__
_RUNTIME_BODY_SOURCE_V1 = runtime.configured_live_runtime_v1.__wrapped__
_SOURCE_BODY_SOURCE_V1 = source.acquire_owned_live_source_v1.__wrapped__
_ENTRY_RUN_BODY_SOURCE_V1 = run_owned_live_operator_once_v1
_PUBLIC_STAGE_BODY_SOURCE_V2 = _post_g12_public_stage_v2.__wrapped__
# These fixed context-manager bodies are actual source callbacks. Their
# decorator closures pin the inner function identity, while these aliases also
# pin its code/defaults/namespace before any capture or readback can execute it.
_PUBLIC_OWNED_STAGE_BODY_SOURCE_V2 = (
    public.begin_owned_live_post_g12_public_v2.__wrapped__
)
_PUBLIC_WIRE_STAGE_BODY_SOURCE_V2 = (
    public_staged_source.begin_global_public_ohlc_v2.__wrapped__
)
_PUBLIC_JOURNAL_ATTEMPT_BODY_SOURCE_V2 = public.journal._runtime_attempt.__wrapped__
_PUBLIC_POSIX_ROOT_BODY_SOURCE_V2 = evidence.storage._posix_root.__wrapped__
_PUBLIC_WINDOWS_ROOT_BODY_SOURCE_V2 = evidence.storage._windows_root.__wrapped__


from app.trade_qualification.native_continuation_guard_v1 import (
    install_native_guards_v1 as _install,
)

_install(
    sys.modules[__name__],
    ("run_owned_live_operator_once_v1",),
    dependencies=(
        runtime,
        source_primitives,
        intent_source,
        adapter,
        final_recheck,
        math,
        controls,
        ledger,
        ledger.structural_catalog,
        ledger.function_specs,
        ledger.function_specs.ancestor,
        ledger.function_specs.lifecycle,
        financial,
        source,
        # The physical namespace has a fixed-identity API cache cell. Its
        # legitimate contents may populate, while helpers, methods, constants,
        # source guard aliases and native transport callbacks remain pinned.
        physical,
        physical.transport,
        public,
        # Closed Global public read/capture graph: the sole reader, canonical
        # wire/packet validators, projection and their native clock/root
        # definers. Imported module identity alone does not pin its callbacks.
        public.production,
        public.projection,
        public_staged_source,
        public.transport,
        public.journal,
        public.candles,
        public.auxiliary,
        public.collectors,
        public.quote_wire,
        public.websocket,
        public_ws_reference_source,
        public_parser_source,
        public_symbol_source,
        public_clock_source,
        public_receipt_contracts,
        public_receipt_source,
        public_clock_primitives,
        evidence,
        evidence.shared,
        evidence.service,
        evidence.storage,
        entry_proof,
        entry_original,
        entry_proof.inputs,
        entry_proof.inputs.dag,
        entry_proof.inputs.custody,
        entry_proof.inputs.current,
        entry_proof.inputs.history,
        entry_math_closure,
        *entry_math_closure.original_entry_math_modules_v1(),
    ),
)
