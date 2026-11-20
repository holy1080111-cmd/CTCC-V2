"""Owned Live consumption of unchanged anonymous production public evidence.

The original raw packet keeps its source environment, origins and report ID.
Only a genuine one-use coordinator request can bind that newly captured source
to a Live invocation. No Demo route lease, account claim or submit permit is
accepted or issued here.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import get_ident
from types import FunctionType
from uuid import uuid4
from weakref import WeakKeyDictionary

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import executable_quote_live_v1 as live_quote
from app.trade_qualification import global_public_runtime_v1 as production
from app.trade_qualification import global_public_runtime_v2 as projection
from app.trade_qualification import market_aux_collector as auxiliary
from app.trade_qualification import public_market_collector as collectors
from app.trade_qualification import public_source_runtime as transport
from app.trade_qualification import quote_collector as quote_wire
from app.trade_qualification import ws_collector as websocket
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.funding_observation_v2 import parse_funding_observation_v2

_COORDINATOR = "app.okx_live.owned_qualification_v1"
_COORDINATOR_REQUEST_KEY_V1 = object()
_ENTRY_BANK_KEY_V1 = object()
_CONTROLLED_QUOTE_PURPOSE_V1 = object()
_ENTRY_QUOTE_PURPOSE_V1 = object()
_OWNERS = WeakKeyDictionary()
_INFLIGHT = set()
_MAX_OWNERS = 16
_FILE = "live-public.json"
_SCHEMA = "ctcc.native_live.production_public_envelope.v1"
_HEX = re.compile(r"[a-f0-9]{64}\Z")


class LivePublicSourceError(ValueError):
    """Fixed refusal without filesystem, transport or coordinator contents."""


def _need(value, code="native_live_public_source_unavailable"):
    if value is not True:
        raise LivePublicSourceError(code) from None


def _market_policy():
    return collectors.PublicMarketCollectionPolicy(
        quote=quote_wire.QuoteCollectionPolicy(max_age_seconds=5),
        candles=candles.CandleCollectionPolicy(
            requests=tuple(
                candles.FrameRequest(timeframe=bar, requested_confirmed_bars=240)
                for bar in candles.TIMEFRAMES
            )
        ),
        market_aux=auxiliary.MarketAuxCollectionPolicy(max_age_seconds=5),
        ws=websocket.WSCollectionPolicy(max_age_seconds=5),
        total_timeout_seconds=30,
        client_close_timeout_seconds=1,
    )


_WIRE_POLICY_SHA256 = collectors._digest(_market_policy())
POLICY_BYTES = canonical(
    {
        "schema_version": "ctcc.native_live.production_public_policy.v1",
        "target_environment": "live",
        "source_environment": journal.GLOBAL_ENVIRONMENT,
        "source_route_sha256": journal.GLOBAL_ROUTE_SHA256,
        "rest_origin": journal.GLOBAL_REST_ORIGIN,
        "ws_origin": journal.GLOBAL_WS_ORIGIN,
        "instruments": journal.GLOBAL_INSTRUMENTS,
        "market_policy_sha256": _WIRE_POLICY_SHA256,
        "live_quote_policy_sha256": live_quote.POLICY_SHA256,
        "requests_after_original_barrier": True,
        "retries": 0,
        "source_substitution": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
CLOSE_POLICY_BYTES = canonical(
    {
        "schema_version": "ctcc.native_live.controlled_exit_public_policy.v1",
        "ancestor_policy_sha256": POLICY_SHA256,
        "purpose": "controlled_exit",
        "original_publication_preserved": True,
        "new_request_barrier_required": True,
        "original_candidate_preserved": True,
        "execution_authority": False,
    }
)
CLOSE_POLICY_SHA256 = sha(CLOSE_POLICY_BYTES)


@dataclass(frozen=True, slots=True, repr=False)
class LivePublicCaptureParametersV1:
    """Inert DATA returned only by the fixed coordinator's original registry."""

    root: Path
    report_id: str
    instrument_id: str
    invocation: object
    stage: str
    expires_at: datetime
    expires_monotonic_ns: int
    candidate_sha256: str | None
    publication_barrier: ClockStamp | None
    publication_receipt_sha256: str | None
    public_policy_sha256: str
    request_not_before: ClockStamp | None = None


class OwnedLivePublicSourceV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False, "native_live_public_not_constructible")

    def __copy__(self):
        _need(False, "native_live_public_not_transferable")

    def __deepcopy__(self, memo):
        _need(False, "native_live_public_not_transferable")

    def __reduce_ex__(self, protocol):
        _need(False, "native_live_public_not_transferable")

    def close(self):
        _OWNERS.pop(self, None)


@dataclass(frozen=True, slots=True, repr=False)
class LivePublicRawFileV1:
    name: str
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class LivePublicEvaluationV1:
    """Replayed projection DATA; only the private owner can be revalidated."""

    invocation: object
    report_id: str
    instrument_id: str
    stage: str
    candidate_sha256: str | None
    publication_receipt_sha256: str | None
    publication_barrier: ClockStamp | None
    market: object
    quote: live_quote.LiveExecutableQuoteV1
    reference: WSReferenceObservation
    packet: production.GlobalPublicComponentPacketV1
    raw_files: tuple[LivePublicRawFileV1, ...]
    receipt_json: bytes
    plan_sha256: str
    journal_sha256: str
    raw_inventory_sha256: str
    evaluated_at: datetime
    expires_at: datetime
    environment: str = field(default="live", init=False)
    source_environment: str = field(default=journal.GLOBAL_ENVIRONMENT, init=False)
    execution_authority: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    historical_availability_verified: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


@dataclass(frozen=True, slots=True, repr=False)
class LiveControlledQuoteReplayV1:
    """Exit-only DATA. No MarketSnapshot, entry replay or qualification claim."""

    quote: live_quote.LiveExecutableQuoteV1
    reference: WSReferenceObservation
    raw_inventory_sha256: str
    original_packet_sha256: str
    original_publication_sha256: str
    evaluated_at: datetime
    schema: str = field(
        default="ctcc.native_live.controlled_exit_raw_quote_replay.v1", init=False
    )
    execution_authority: bool = field(default=False, init=False)
    qualification_performed: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class LiveEntryQuoteReplayV1:
    """Fresh raw DATA under one already-owned full-R7 exact-input proof."""

    quote: live_quote.LiveExecutableQuoteV1
    reference: WSReferenceObservation
    raw_inventory_sha256: str
    original_packet_sha256: str
    original_publication_sha256: str
    evaluated_at: datetime
    schema: str = field(
        default="ctcc.native_live.entry_raw_quote_replay.v1", init=False
    )
    execution_authority: bool = field(default=False, init=False)
    qualification_performed: bool = field(default=False, init=False)


def _coordinator(key):
    module = sys.modules.get(_COORDINATOR)
    _need(module is not None and getattr(module, "__name__", None) == _COORDINATOR)
    _need(key is getattr(module, "_PUBLIC_SOURCE_KEY_V1", None) and key is not None)
    consumer = getattr(module, "_consume_live_public_request_v1", None)
    _need(type(consumer) is FunctionType and consumer.__module__ == _COORDINATOR)
    return consumer


def _stamp(value):
    _need(type(value) is dict and set(value) == {"utc_ns", "monotonic_ns"})
    result = ClockStamp.model_validate(dict(value))
    return {"utc_ns": result.utc_ns, "monotonic_ns": result.monotonic_ns}


def _barrier(value):
    if value is None:
        return None
    _need(
        type(value) is ClockStamp and set(value.__dict__) == {"utc_ns", "monotonic_ns"}
    )
    return _stamp({"utc_ns": value.utc_ns, "monotonic_ns": value.monotonic_ns})


def _parameters(value):
    _need(type(value) is LivePublicCaptureParametersV1)
    _need(type(value.root) is type(Path()) and value.root.is_absolute())
    _need(
        type(value.report_id) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value.report_id) is not None
    )
    _need(
        type(value.instrument_id) is str
        and value.instrument_id in journal.GLOBAL_INSTRUMENTS
    )
    _need(
        value.invocation is not None
        and type(value.stage) is str
        and value.stage in {"initial", "post_g12", "controlled_exit"}
    )
    _need(type(value.expires_at) is datetime and value.expires_at.tzinfo is UTC)
    _need(type(value.expires_monotonic_ns) is int and value.expires_monotonic_ns > 0)
    expected_policy = (
        CLOSE_POLICY_SHA256 if value.stage == "controlled_exit" else POLICY_SHA256
    )
    _need(
        value.public_policy_sha256 == expected_policy
        and sha(POLICY_BYTES) == POLICY_SHA256
        and sha(CLOSE_POLICY_BYTES) == CLOSE_POLICY_SHA256
    )
    _need(
        value.candidate_sha256 is None
        or type(value.candidate_sha256) is str
        and _HEX.fullmatch(value.candidate_sha256) is not None
    )
    barrier = _barrier(value.publication_barrier)
    if value.stage in ("post_g12", "controlled_exit"):
        _need(
            barrier is not None
            and value.candidate_sha256 is not None
            and type(value.publication_receipt_sha256) is str
            and _HEX.fullmatch(value.publication_receipt_sha256) is not None
        )
    else:
        _need(barrier is None and value.publication_receipt_sha256 is None)
    request_barrier = _barrier(value.request_not_before)
    if value.stage == "controlled_exit":
        _need(
            request_barrier is not None
            and barrier is not None
            and request_barrier["utc_ns"] > barrier["utc_ns"]
            and request_barrier["monotonic_ns"] > barrier["monotonic_ns"]
        )
    else:
        _need(request_barrier is None)
    return replace(
        value,
        publication_barrier=None
        if barrier is None
        else ClockStamp.model_validate(barrier),
        request_not_before=None
        if request_barrier is None
        else ClockStamp.model_validate(request_barrier),
    )


def _owned(state):
    task = asyncio.current_task()
    _need(
        task is not None
        and task is state["task"]
        and not task.cancelling()
        and asyncio.get_running_loop() is state["loop"]
        and os.getpid() == state["pid"]
        and get_ident() == state["thread"]
    )


def _current(state):
    _owned(state)
    stamp = _stamp(native_stamp())
    validate_stamps((state["last"], stamp))
    _need(
        utc_from_ns(stamp["utc_ns"]) < state["expires_at"]
        and stamp["monotonic_ns"] < state["expires_monotonic_ns"],
        "native_live_public_expired",
    )
    state["last"] = stamp
    return stamp


def _after(stamp, before):
    validate_stamps((before, stamp))
    _need(
        stamp["utc_ns"] > before["utc_ns"]
        and stamp["monotonic_ns"] > before["monotonic_ns"],
        "native_live_public_barrier_not_crossed",
    )


def _inventory(directory):
    names = sorted(directory.names())
    _need(2 <= len(names) <= 2 * journal.MAX_EVENTS + 2)
    files, total = [], 0
    digest = hashlib.sha256(b"ctcc.native_live.public_raw_inventory.v1\n")
    for name in names:
        _need(
            type(name) is str
            and (
                name in {"plan.json", "summary.json"}
                or re.fullmatch(r"event-[0-9]{4}\.(json|raw)", name) is not None
            )
        )
        raw = directory.read(name, journal.MAX_RAW)
        _need(type(raw) is bytes)
        total += len(raw)
        _need(total <= journal.MAX_TOTAL)
        pin = sha(raw)
        digest.update(canonical([name, len(raw), pin]) + b"\n")
        files.append(LivePublicRawFileV1(name, raw, pin))
    return tuple(files), digest.hexdigest(), total


def _audit(raw_root, *, root_identity, plan_sha256, journal_sha256, params, not_before):
    replayed = production.read_global_public_capture_v1(
        raw_root,
        expected_plan_sha256=plan_sha256,
        expected_journal_sha256=journal_sha256,
        expected_root_identity=root_identity,
    )
    _need(
        type(replayed) is production.GlobalPublicCaptureV1
        and replayed.code == "initial_global_public_replayed"
    )
    with transport._reopen_runtime_journal(raw_root) as directory:
        plan_raw, summary_raw = (
            directory.read("plan.json", journal.MAX_RAW),
            directory.read("summary.json", journal.MAX_RAW),
        )
        _need(sha(plan_raw) == plan_sha256 and sha(summary_raw) == journal_sha256)
        summary, events, packet = transport.replay_public_runtime(
            directory, expected_plan_sha256=plan_sha256
        )
        plan = decode(plan_raw, journal.MAX_RAW)
        _need(
            plan["schema_version"] == journal.GLOBAL_INITIAL_PLAN
            and plan["environment"] == journal.GLOBAL_ENVIRONMENT
            and plan["instrument_id"] == params.instrument_id
            and plan["policy_sha256"] == _WIRE_POLICY_SHA256
            and plan["global_route_sha256"] == journal.GLOBAL_ROUTE_SHA256
        )
        _after(plan["invocation_started"], not_before)
        starts = []
        for event in events:
            if event["kind"] in {"request", "ws_connect"}:
                stamp = _stamp(event["metadata"]["started"])
                _after(stamp, not_before)
                starts.append((event["kind"], stamp))
        _need(
            any(kind == "request" for kind, _ in starts)
            and sum(kind == "ws_connect" for kind, _ in starts) == 1
        )
        _need(
            type(packet) is production.GlobalPublicComponentPacketV1
            and packet.packet_json == replayed.packet.packet_json
        )
        _need(
            packet.environment == journal.GLOBAL_ENVIRONMENT
            and packet.rest_origin == journal.GLOBAL_REST_ORIGIN
            and packet.ws_origin == journal.GLOBAL_WS_ORIGIN
            and packet.barrier_completed_at is None
        )
        _need(
            tuple(frame.timeframe for frame in packet.candles.frames)
            == candles.TIMEFRAMES
        )
        files, inventory_sha256, size = _inventory(directory)
        _need(
            directory.read("plan.json", journal.MAX_RAW) == plan_raw
            and directory.read("summary.json", journal.MAX_RAW) == summary_raw
        )
    _need(transport._native_recheck_root_identity(raw_root) == root_identity)
    return replayed, plan, summary, packet, files, inventory_sha256, size, tuple(starts)


def _project(packet, *, report_id, at):
    market, _following_funding = projection.project_global_public_market_snapshot_v2(
        packet, evaluated_at=at
    )
    _need(
        projection.matches_global_public_market_identity_v2(
            market, packet.instrument_id
        )
    )
    quote, reference = _project_quote_reference(
        packet.quote,
        packet.ws,
        instrument_id=packet.instrument_id,
        report_id=report_id,
        at=at,
    )
    return market, quote, reference


def _project_quote_reference(
    quote_component, ws_component, *, instrument_id, report_id, at
):
    ticker, mark, funding = quote_component.provenance
    ticker_row = quote_wire._row(
        quote_wire._parse(ticker.response_body)[0], instrument_id
    )
    bid, ask, bid_size, ask_size = quote_wire._values("ticker", ticker_row)

    def stamps(item):
        return {
            "instrument_id": item.instrument_id,
            "request_started_at": item.request_started_at,
            "headers_received_at": item.received_at,
            "body_completed_at": item.completed_at,
            "raw_body_sha256": item.body_sha256,
        }

    quote = live_quote.LiveExecutableQuoteV1(
        report_id=report_id,
        instrument_id=instrument_id,
        environment="live",
        capture_started_at=ticker.request_started_at,
        capture_completed_at=quote_component.completed_at,
        ticker=live_quote.shared.RestTickerObservationV2(
            bid=bid,
            ask=ask,
            bid_size_contracts=bid_size,
            ask_size_contracts=ask_size,
            source_generated_at=ticker.source_time,
            **stamps(ticker),
        ),
        mark=live_quote.shared.RestMarkObservationV2(
            price=quote_wire._values(
                "mark",
                quote_wire._row(
                    quote_wire._parse(mark.response_body)[0], instrument_id
                ),
            )[0],
            exchange_data_return_at=mark.source_time,
            **stamps(mark),
        ),
        funding=parse_funding_observation_v2(
            funding.response_body,
            source_kind="rest",
            instrument_id=instrument_id,
            acquisition_started_at=funding.request_started_at,
            received_at=funding.received_at,
            completed_at=funding.completed_at,
        ),
    )
    inspection = decode(
        live_quote.inspect_live_executable_quote_v1(quote, current_time=at).receipt_json
    )
    _need(
        inspection["profile_satisfied"] is True, "native_live_public_quote_incomplete"
    )
    ws = ws_component.ticker
    _need(
        0 <= (at - ws.received_at).total_seconds() <= 5
        and 0 <= (at - ws.source_time).total_seconds() <= 5
    )
    reference = WSReferenceObservation(
        report_id=report_id,
        instrument_id=instrument_id,
        bid=ws.bid,
        ask=ws.ask,
        source_time=ws.source_time,
        received_at=ws.received_at,
    )
    return quote, reference


async def capture_owned_live_public_v1(request, *, key):
    """Consume the fixed coordinator request, capture once and retain all raw IO."""
    try:
        consumer = _coordinator(key)
        params = _parameters(consumer(request, key=_COORDINATOR_REQUEST_KEY_V1))
    except LivePublicSourceError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed boundary cannot expose coordinator data
        raise LivePublicSourceError("native_live_public_request_unavailable") from None
    _need(len(_OWNERS) + len(_INFLIGHT) < _MAX_OWNERS)
    task = asyncio.current_task()
    _need(task is not None and not task.cancelling())
    try:
        first_stamp = _stamp(native_stamp())
    except Exception:  # noqa: BLE001 -- no clock or platform details may escape
        raise LivePublicSourceError("native_live_public_clock_unavailable") from None
    token = object()
    state = {
        "params": params,
        "task": task,
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "last": first_stamp,
        "expires_at": params.expires_at,
        "expires_monotonic_ns": params.expires_monotonic_ns,
    }
    _INFLIGHT.add(token)
    try:
        started = _current(state)
        barrier = _barrier(params.publication_barrier)
        request_barrier = _barrier(params.request_not_before)
        # The original G12 barrier is immutable history for an exit. Clock
        # continuity remains bounded around this NEW measured request barrier.
        effective_barrier = (
            request_barrier if params.stage == "controlled_exit" else barrier
        )
        if effective_barrier is not None:
            _after(started, effective_barrier)
        not_before = started
        policy = _market_policy()
        _need(collectors._digest(policy) == _WIRE_POLICY_SHA256)
        raw_root = params.root / "raw"
        with transport._reopen_runtime_journal(params.root) as directory:
            _need(not directory.names(), "native_live_public_root_not_empty")
            state["root_identity"] = transport._native_recheck_root_identity(
                params.root
            )
            directory.mkdir("raw")
            raw_identity = transport._native_recheck_root_identity(raw_root)
            _need(raw_identity != state["root_identity"])
            remaining = min(
                30,
                (params.expires_monotonic_ns - started["monotonic_ns"]) / 1_000_000_000,
            )
            _need(remaining > 0)
            async with asyncio.timeout(remaining):
                captured = await production.capture_initial_global_public_market_v1(
                    raw_root, instrument_id=params.instrument_id, market_policy=policy
                )
            _need(
                type(captured) is production.GlobalPublicCaptureV1
                and captured.code == "initial_global_public_captured"
            )
            _current(state)
            audit = _audit(
                raw_root,
                root_identity=raw_identity,
                plan_sha256=captured.plan_sha256,
                journal_sha256=captured.journal_sha256,
                params=params,
                not_before=not_before,
            )
            replayed, plan, _summary, packet, files, inventory_pin, size, starts = audit
            _need(packet.packet_json == captured.packet.packet_json)
            state["expires_at"] = min(params.expires_at, replayed.invocation_expires_at)
            state["expires_monotonic_ns"] = min(
                params.expires_monotonic_ns,
                plan["invocation_started"]["monotonic_ns"] + 30_000_000_000,
            )
            finished = _current(state)
            _project(
                packet, report_id=params.report_id, at=utc_from_ns(finished["utc_ns"])
            )
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_live.production_public_close_envelope.v1"
                    if params.stage == "controlled_exit"
                    else _SCHEMA,
                    "capture_id": uuid4().hex,
                    "environment": "live",
                    "source_environment": packet.environment,
                    "source_report_id": packet.report_id,
                    "report_id": params.report_id,
                    "instrument_id": params.instrument_id,
                    "stage": params.stage,
                    "candidate_sha256": params.candidate_sha256,
                    "publication_receipt_sha256": params.publication_receipt_sha256,
                    "publication_barrier": barrier,
                    "policy_sha256": params.public_policy_sha256,
                    **(
                        {"request_not_before": request_barrier}
                        if params.stage == "controlled_exit"
                        else {}
                    ),
                    "plan_sha256": captured.plan_sha256,
                    "journal_sha256": captured.journal_sha256,
                    "packet_sha256": packet.bundle_sha256,
                    "raw_inventory_sha256": inventory_pin,
                    "raw_file_count": len(files),
                    "raw_total_bytes": size,
                    "root_identity": state["root_identity"],
                    "raw_root_identity": raw_identity,
                    "capture_started": started,
                    "readback_completed": finished,
                    "expires_at": state["expires_at"].isoformat(),
                    "expires_monotonic_ns": state["expires_monotonic_ns"],
                    "http_request_count": sum(kind == "request" for kind, _ in starts),
                    "ws_connection_count": 1,
                    "raw_packet_relabelled": False,
                    "account_complete": False,
                    "historical_availability_verified": False,
                    "execution_authority": False,
                }
            )
            directory.publish(_FILE, receipt)
            _need(
                directory.read(_FILE, 16384) == receipt
                and set(directory.names()) == {"raw", _FILE}
            )
            _need(
                transport._native_recheck_root_identity(params.root)
                == state["root_identity"]
            )
            state.update(
                raw_root=raw_root,
                raw_identity=raw_identity,
                plan_sha256=captured.plan_sha256,
                journal_sha256=captured.journal_sha256,
                packet_sha256=packet.bundle_sha256,
                inventory_sha256=inventory_pin,
                not_before=not_before,
                receipt=receipt,
            )
        _current(state)
        owner = object.__new__(OwnedLivePublicSourceV1)
        _OWNERS[owner] = state
        return owner
    except asyncio.CancelledError:
        raise
    except LivePublicSourceError:
        raise
    except Exception:  # noqa: BLE001 -- fail closed with a fixed public diagnostic
        raise LivePublicSourceError("native_live_public_capture_failed") from None
    finally:
        _INFLIGHT.discard(token)


def _replay_owned_live_public_v1(owner, *, key):
    """Freshly reopen the same retained source; saved files never mint an owner."""
    _coordinator(key)
    state = _OWNERS.get(owner) if type(owner) is OwnedLivePublicSourceV1 else None
    _need(state is not None)
    try:
        params = state["params"]
        _current(state)
        with transport._reopen_runtime_journal(params.root) as directory:
            _need(
                set(directory.names()) == {"raw", _FILE}
                and directory.read(_FILE, 16384) == state["receipt"]
            )
            _need(
                transport._native_recheck_root_identity(params.root)
                == state["root_identity"]
            )
            audit = _audit(
                state["raw_root"],
                root_identity=state["raw_identity"],
                plan_sha256=state["plan_sha256"],
                journal_sha256=state["journal_sha256"],
                params=params,
                not_before=state["not_before"],
            )
            _, _, _, packet, files, inventory_pin, _, _ = audit
            _need(
                packet.bundle_sha256 == state["packet_sha256"]
                and inventory_pin == state["inventory_sha256"]
            )
            stamp = _current(state)
            at = utc_from_ns(stamp["utc_ns"])
            market, quote, reference = _project(
                packet, report_id=params.report_id, at=at
            )
        return LivePublicEvaluationV1(
            params.invocation,
            params.report_id,
            params.instrument_id,
            params.stage,
            params.candidate_sha256,
            params.publication_receipt_sha256,
            None
            if params.publication_barrier is None
            else ClockStamp.model_validate(_barrier(params.publication_barrier)),
            market,
            quote,
            reference,
            packet,
            files,
            state["receipt"],
            state["plan_sha256"],
            state["journal_sha256"],
            inventory_pin,
            at,
            state["expires_at"],
        )
    except asyncio.CancelledError:
        _OWNERS.pop(owner, None)
        raise
    except LivePublicSourceError:
        _OWNERS.pop(owner, None)
        raise
    except Exception:  # noqa: BLE001 -- burn the owner without leaking raw IO details
        _OWNERS.pop(owner, None)
        raise LivePublicSourceError("native_live_public_replay_failed") from None
    except BaseException:
        _OWNERS.pop(owner, None)
        raise


def _replay_owned_live_controlled_quote_v1(owner, *, key):
    """Re-read all original bytes, then freshly parse exit quote/WS components.

    The complete source was audited before its original opaque owner existed.
    Every call reopens its entire immutable inventory and requires that exact
    same digest, packet, plan, receipt and native root identities. OHLC bytes
    remain bound to that original audit; this purpose emits no candle replay,
    MarketSnapshot or entry qualification. No quote/model/clock is cached.
    """
    return _replay_owned_live_raw_quote_v1(
        owner, key=key, qualification_owner=None, purpose=_CONTROLLED_QUOTE_PURPOSE_V1
    )


def _replay_owned_live_entry_quote_v1(owner, qualification_owner, *, key):
    """ENTRY-only raw replay requires a genuine private same-invocation seal."""
    return _replay_owned_live_raw_quote_v1(
        owner,
        key=key,
        qualification_owner=qualification_owner,
        purpose=_ENTRY_QUOTE_PURPOSE_V1,
    )


def _require_entry_bank_v1(qualification_owner, owner):
    module = sys.modules.get(_COORDINATOR)
    _need(module is not None)
    checker = getattr(module, "_require_native_entry_public_owner_v1", None)
    _need(type(checker) is FunctionType and checker.__module__ == _COORDINATOR)
    checker(qualification_owner, owner, key=_ENTRY_BANK_KEY_V1)


def _replay_owned_live_raw_quote_v1(owner, *, key, qualification_owner, purpose):
    _coordinator(key)
    _need(purpose is _CONTROLLED_QUOTE_PURPOSE_V1 or purpose is _ENTRY_QUOTE_PURPOSE_V1)
    if purpose is _ENTRY_QUOTE_PURPOSE_V1:
        _require_entry_bank_v1(qualification_owner, owner)
    else:
        _need(qualification_owner is None)
    state = _OWNERS.get(owner) if type(owner) is OwnedLivePublicSourceV1 else None
    _need(state is not None)
    try:
        params = state["params"]
        _need(
            params.stage
            == ("post_g12" if purpose is _ENTRY_QUOTE_PURPOSE_V1 else "controlled_exit")
            and params.public_policy_sha256
            == (
                POLICY_SHA256
                if purpose is _ENTRY_QUOTE_PURPOSE_V1
                else CLOSE_POLICY_SHA256
            )
        )
        _current(state)
        with transport._reopen_runtime_journal(params.root) as parent:
            _need(
                set(parent.names()) == {"raw", _FILE}
                and parent.read(_FILE, 16384) == state["receipt"]
            )
            _need(
                transport._native_recheck_root_identity(params.root)
                == state["root_identity"]
            )
            with transport._reopen_runtime_journal(state["raw_root"]) as directory:
                _need(
                    transport._native_recheck_root_identity(state["raw_root"])
                    == state["raw_identity"]
                )
                files, inventory, _size = _inventory(directory)
                _need(inventory == state["inventory_sha256"])
                bank = {item.name: item.raw for item in files}
                plan_raw, summary_raw = bank["plan.json"], bank["summary.json"]
                _need(
                    sha(plan_raw) == state["plan_sha256"]
                    and sha(summary_raw) == state["journal_sha256"]
                )
                plan, summary = (
                    decode(plan_raw, journal.MAX_RAW),
                    decode(summary_raw, journal.MAX_RAW),
                )
                _need(
                    plan["schema_version"] == journal.GLOBAL_INITIAL_PLAN
                    and plan["instrument_id"] == params.instrument_id
                    and summary["disposition"] == "captured"
                )
                count = summary["event_count"]
                _need(type(count) is int and 0 < count <= journal.MAX_EVENTS)
                event = decode(bank[f"event-{count - 1:04d}.json"], journal.MAX_RAW)
                packet_raw = bank[f"event-{count - 1:04d}.raw"]
                _need(
                    event["kind"] == "packet"
                    and event["raw_sha256"]
                    == summary["packet_sha256"]
                    == state["packet_sha256"]
                    == sha(packet_raw)
                )
                document = decode(packet_raw, journal.MAX_RAW)
                _need(
                    canonical(document) == packet_raw
                    and set(document)
                    == set(production.GlobalPublicComponentPacketV1.model_fields)
                )
                _need(
                    document["schema_version"] == journal.GLOBAL_PACKET
                    and document["environment"] == journal.GLOBAL_ENVIRONMENT
                    and document["rest_origin"] == journal.GLOBAL_REST_ORIGIN
                    and document["ws_origin"] == journal.GLOBAL_WS_ORIGIN
                    and document["global_route_sha256"] == journal.GLOBAL_ROUTE_SHA256
                    and document["instrument_id"] == params.instrument_id
                    and document["report_id"] == plan["report_id"]
                    and document["policy_sha256"] == plan["policy_sha256"]
                    and document["barrier_completed_at"] is None
                )
                _need(
                    all(
                        document[name] is False
                        for name in (
                            "source_authenticity_verified",
                            "execution_authority",
                            "qualification_performed",
                        )
                    )
                )
                quote_component = production.GlobalQuoteEvidenceV1.model_validate_json(
                    canonical(document["quote"])
                )
                ws_component = websocket.CollectedWSReference.model_validate_json(
                    canonical(document["ws"]), strict=True
                )
                _need(
                    tuple(item.instrument_id for item in quote_component.provenance)
                    == (params.instrument_id,) * 3
                )
                _need(
                    ws_component.instrument_id == params.instrument_id
                    and ws_component.report_id == plan["report_id"]
                    and ws_component.endpoint == journal.GLOBAL_WS_ORIGIN
                )
                stamp = _current(state)
                at = utc_from_ns(stamp["utc_ns"])
                quote, reference = _project_quote_reference(
                    quote_component,
                    ws_component,
                    instrument_id=params.instrument_id,
                    report_id=params.report_id,
                    at=at,
                )
                _need(_inventory(directory)[1] == inventory)
            _need(
                transport._native_recheck_root_identity(state["raw_root"])
                == state["raw_identity"]
            )
        _need(
            transport._native_recheck_root_identity(params.root)
            == state["root_identity"]
        )
        final = _current(state)
        final_at = utc_from_ns(final["utc_ns"])
        # Recheck age after inventory readback; no earlier sampled clock can
        # authorize a native write that resumed later.
        _need(
            decode(
                live_quote.inspect_live_executable_quote_v1(
                    quote, current_time=final_at
                ).receipt_json
            )["profile_satisfied"]
            is True
        )
        _need(
            0 <= (final_at - reference.received_at).total_seconds() <= 5
            and 0 <= (final_at - reference.source_time).total_seconds() <= 5
        )
        kind = (
            LiveEntryQuoteReplayV1
            if purpose is _ENTRY_QUOTE_PURPOSE_V1
            else LiveControlledQuoteReplayV1
        )
        return kind(
            quote,
            reference,
            inventory,
            state["packet_sha256"],
            params.publication_receipt_sha256,
            final_at,
        )
    except asyncio.CancelledError:
        _OWNERS.pop(owner, None)
        raise
    except LivePublicSourceError:
        _OWNERS.pop(owner, None)
        raise
    except Exception:  # noqa: BLE001 -- a damaged source burns this owner and never retries.
        _OWNERS.pop(owner, None)
        raise LivePublicSourceError(
            "native_live_controlled_quote_replay_failed"
        ) from None
    except BaseException:
        _OWNERS.pop(owner, None)
        raise


def _recheck_owned_initial_entry_bytes_v1(owner, qualification_owner, *, key):
    """Reopen old initial evidence bytes; never relabel its quote as current."""
    _coordinator(key)
    _require_entry_bank_v1(qualification_owner, owner)
    state = _OWNERS.get(owner) if type(owner) is OwnedLivePublicSourceV1 else None
    _need(state is not None)
    try:
        params = state["params"]
        _need(
            params.stage == "initial" and params.public_policy_sha256 == POLICY_SHA256
        )
        _current(state)
        with transport._reopen_runtime_journal(params.root) as parent:
            _need(
                set(parent.names()) == {"raw", _FILE}
                and parent.read(_FILE, 16384) == state["receipt"]
            )
            _need(
                transport._native_recheck_root_identity(params.root)
                == state["root_identity"]
            )
            with transport._reopen_runtime_journal(state["raw_root"]) as directory:
                _need(
                    transport._native_recheck_root_identity(state["raw_root"])
                    == state["raw_identity"]
                )
                inventory = _inventory(directory)[1]
                _need(inventory == state["inventory_sha256"])
                _need(_inventory(directory)[1] == inventory)
            _need(
                transport._native_recheck_root_identity(state["raw_root"])
                == state["raw_identity"]
            )
        _need(
            transport._native_recheck_root_identity(params.root)
            == state["root_identity"]
        )
        _current(state)
        return (
            inventory,
            state["packet_sha256"],
            state["plan_sha256"],
            state["journal_sha256"],
            sha(state["receipt"]),
        )
    except BaseException:
        _OWNERS.pop(owner, None)
        raise
