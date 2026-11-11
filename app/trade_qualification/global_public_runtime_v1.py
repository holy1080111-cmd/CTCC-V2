"""Owned initial-only anonymous Global market evidence."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import get_ident
from typing import Literal
from uuid import uuid4
from weakref import WeakKeyDictionary

from pydantic import Field, field_validator, model_validator

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import market_aux_collector as aux
from app.trade_qualification import public_market_collector as public
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import quote_collector as quotes
from app.trade_qualification import ws_collector as ws
from app.trade_qualification.event_models import Digest
from app.trade_qualification.models import QualificationModel, ReportId

_ISSUER = object()
_SCOPES = WeakKeyDictionary()


class GlobalPublicCaptureError(ValueError):
    """Fixed public evidence refusal."""


class _Scope:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise GlobalPublicCaptureError("global_public_owner_required")

    def __reduce_ex__(self, protocol):
        raise GlobalPublicCaptureError("global_public_owner_not_transferable")


def _take_scope(scope):
    if type(scope) is not _Scope:
        raise GlobalPublicCaptureError("global_public_owner_required")
    state = _SCOPES.pop(scope, None)
    task = asyncio.current_task()
    if (
        state is None
        or task is None
        or task.cancelling()
        or state["parent"] is not task
        or state["loop"] is not asyncio.get_running_loop()
        or state["pid"] != os.getpid()
        or state["thread"] != get_ident()
    ):
        raise GlobalPublicCaptureError("global_public_owner_required")
    return state


class GlobalQuoteEvidenceV1(QualificationModel):
    provenance: tuple[quotes.EndpointObservation, ...] = Field(
        min_length=3, max_length=3
    )
    policy: quotes.QuoteCollectionPolicy
    completed_at: datetime

    _time = field_validator("completed_at")(quotes._utc)

    @model_validator(mode="after")
    def replay(self):
        quotes._policy_preflight(self.policy)
        if tuple(item.role for item in self.provenance) != (
            "ticker",
            "mark",
            "funding",
        ):
            raise GlobalPublicCaptureError("global_public_quote_order_invalid")
        previous = None
        for item in self.provenance:
            if (
                item.origin != journal.GLOBAL_REST_ORIGIN
                or (previous is not None and item.request_started_at < previous)
                or item.completed_at > self.completed_at
                or item.body_size_bytes > self.policy.max_response_bytes
                or item.completed_at - item.request_started_at
                > timedelta(seconds=self.policy.request_timeout_seconds)
                or self.completed_at - item.source_time
                > timedelta(seconds=self.policy.max_age_seconds)
            ):
                raise GlobalPublicCaptureError(
                    "global_public_quote_observation_invalid"
                )
            previous = item.completed_at
        if self.completed_at - self.provenance[0].request_started_at > timedelta(
            seconds=self.policy.batch_timeout_seconds
        ):
            raise GlobalPublicCaptureError("global_public_quote_deadline_exceeded")
        return self


class GlobalPublicComponentPacketV1(QualificationModel):
    schema_version: Literal["ctcc.public.global_component_packet.v1"] = (
        journal.GLOBAL_PACKET
    )
    environment: Literal["anonymous_global_public"] = journal.GLOBAL_ENVIRONMENT
    rest_origin: Literal["https://openapi.okx.com"] = journal.GLOBAL_REST_ORIGIN
    ws_origin: Literal["wss://ws.okx.com:443/ws/v5/public"] = journal.GLOBAL_WS_ORIGIN
    global_route_sha256: Digest = journal.GLOBAL_ROUTE_SHA256
    report_id: ReportId
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    started_at: datetime
    completed_at: datetime
    policy: public.PublicMarketCollectionPolicy
    policy_sha256: Digest
    quote: GlobalQuoteEvidenceV1
    candles: candles.CollectedCandles
    market_aux: aux.CollectedMarketAux
    ws: ws.CollectedWSReference
    barrier_completed_at: None = None
    source_authenticity_verified: Literal[False] = False
    execution_authority: Literal[False] = False
    qualification_performed: Literal[False] = False

    _times = field_validator("started_at", "completed_at")(quotes._utc)

    @field_validator(
        "source_authenticity_verified",
        "execution_authority",
        "qualification_performed",
        mode="before",
    )
    @classmethod
    def exact_no_authority(cls, value):
        if value is not False:
            raise GlobalPublicCaptureError("global_public_authority_forbidden")
        return value

    @model_validator(mode="after")
    def replay(self):
        if (
            self.global_route_sha256 != journal.GLOBAL_ROUTE_SHA256
            or self.policy_sha256 != public._digest(self.policy)
            or not timedelta(0)
            <= self.completed_at - self.started_at
            <= timedelta(seconds=self.policy.total_timeout_seconds)
        ):
            raise GlobalPublicCaptureError("global_public_packet_scope_invalid")
        candles.validate_collected_candles(self.candles)
        aux.validate_collected_market_aux(self.market_aux)
        ws.validate_collected_ws_reference(self.ws)
        if (
            any(
                item.instrument_id != self.instrument_id
                for item in self.quote.provenance
            )
            or any(
                page.origin != self.rest_origin
                for frame in self.candles.frames
                for page in frame.pages
            )
            or any(
                item.origin != self.rest_origin for item in self.market_aux.provenance
            )
            or self.ws.endpoint != self.ws_origin
        ):
            raise GlobalPublicCaptureError("global_public_component_origin_invalid")
        for name, start in (
            ("quote", self.quote.provenance[0].request_started_at),
            ("candles", self.candles.frames[0].pages[0].request_started_at),
            ("market_aux", self.market_aux.provenance[0].request_started_at),
            ("ws", self.ws.connection_started_at),
        ):
            component = getattr(self, name)
            if (
                component.policy != getattr(self.policy, name)
                or not self.started_at
                <= start
                <= component.completed_at
                <= self.completed_at
                or (
                    name != "quote"
                    and (
                        component.report_id != self.report_id
                        or component.instrument_id != self.instrument_id
                        or component.barrier_completed_at is not None
                    )
                )
            ):
                raise GlobalPublicCaptureError(
                    "global_public_component_binding_invalid"
                )
        for name in ("quote", "market_aux"):
            if any(
                self.completed_at - item.source_time
                > timedelta(seconds=getattr(self.policy, name).max_age_seconds)
                for item in getattr(self, name).provenance
            ):
                raise GlobalPublicCaptureError("global_public_component_stale")
        if self.completed_at - self.ws.ticker.source_time > timedelta(
            seconds=self.policy.ws.max_age_seconds
        ) or any(
            frame.verified_through != candles._floor(self.completed_at, frame.timeframe)
            for frame in self.candles.frames
        ):
            raise GlobalPublicCaptureError("global_public_component_stale")
        return self

    @property
    def packet_json(self):
        return canonical(self.model_dump(mode="json", round_trip=True))

    @property
    def bundle_sha256(self):
        return sha(self.packet_json)


def _checked_packet(packet):
    if type(packet) is not GlobalPublicComponentPacketV1:
        raise GlobalPublicCaptureError("global_public_packet_required")
    return GlobalPublicComponentPacketV1.model_validate(packet)


def _replay_packet(plan_raw, plan, summary, packet_raw):
    if (
        type(packet_raw) is not bytes
        or not 0 < len(packet_raw) <= journal.MAX_RAW
        or summary["packet_sha256"] != sha(packet_raw)
        or summary["plan_sha256"] != sha(plan_raw)
    ):
        raise GlobalPublicCaptureError("global_public_packet_readback_invalid")
    journal._validate_global_initial_plan(plan)
    packet = GlobalPublicComponentPacketV1.model_validate_json(packet_raw)
    if (
        packet.packet_json != packet_raw
        or packet.report_id != plan["report_id"]
        or packet.instrument_id != plan["instrument_id"]
        or packet.policy_sha256 != plan["policy_sha256"]
        or not utc_from_ns(plan["invocation_started"]["utc_ns"])
        < packet.started_at
        <= packet.completed_at
        <= utc_from_ns(summary["completed"]["utc_ns"])
    ):
        raise GlobalPublicCaptureError("global_public_packet_readback_invalid")
    return packet


async def _collect_global_quote(
    *, client, clock, report_id, instrument_id, policy, barrier_completed_at, _source
):
    runtime._check_inputs(
        _source,
        clock=clock,
        report_id=report_id,
        instrument_id=instrument_id,
        barrier_completed_at=barrier_completed_at,
    )
    provenance = []
    async with asyncio.timeout(policy.batch_timeout_seconds):
        for role, path in quotes.ENDPOINTS:
            provenance.append(
                await quotes._collect_one(
                    client,
                    clock,
                    role,
                    path,
                    instrument_id,
                    policy,
                    barrier_completed_at,
                    provenance[-1].completed_at if provenance else None,
                    _source=_source,
                )
            )
    return GlobalQuoteEvidenceV1(
        provenance=tuple(provenance), policy=policy, completed_at=clock()
    )


async def _collect_owned_global_components(source):
    state = runtime._state(source)
    journal._validate_global_initial_plan(state["plan"])
    policy = state["policy"]
    clock = state["clock"]
    started = clock()
    common = {
        "clock": clock,
        "report_id": state["report_id"],
        "instrument_id": state["instrument_id"],
        "barrier_completed_at": None,
    }
    tasks = {}
    try:
        for name, collector in (
            ("quote", _collect_global_quote),
            ("candles", candles.collect_candles),
            ("market_aux", aux.collect_market_aux),
        ):
            tasks[name] = asyncio.create_task(
                public._http_capture(
                    collector,
                    policy.client_close_timeout_seconds,
                    _source=source,
                    _role=name,
                    policy=getattr(policy, name),
                    **common,
                )
            )
        tasks["ws"] = asyncio.create_task(
            ws.collect_ws_reference(policy=policy.ws, _source=source, **common)
        )
    except asyncio.CancelledError:
        await public._join_component_tasks(
            tasks, _source=source, already_failed=True, interrupted=True
        )
        raise
    except Exception:
        await public._join_component_tasks(tasks, _source=source, already_failed=True)
        raise
    async with asyncio.timeout_at(state["deadline"]):
        components = await public._join_component_tasks(tasks, _source=source)
    return GlobalPublicComponentPacketV1(
        report_id=state["report_id"],
        instrument_id=state["instrument_id"],
        started_at=started,
        completed_at=clock(),
        policy=policy,
        policy_sha256=state["plan"]["policy_sha256"],
        **components,
    )


@dataclass(frozen=True, slots=True)
class GlobalPublicCaptureV1:
    code: str
    plan_sha256: str | None = None
    journal_sha256: str | None = None
    packet: GlobalPublicComponentPacketV1 | None = field(default=None, repr=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    account_requests: Literal[0] = field(default=0, init=False)
    order_writes: Literal[0] = field(default=0, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    qualification_performed: Literal[False] = field(default=False, init=False)


async def capture_initial_global_public_market_v1(
    root: Path, *, instrument_id, market_policy
):
    """One anonymous native attempt, fixed origins, no caller time or transport."""
    if (
        type(root) is not type(Path())
        or not root.is_absolute()
        or type(instrument_id) is not str
        or instrument_id not in journal.GLOBAL_INSTRUMENTS
    ):
        raise GlobalPublicCaptureError("global_public_inputs_invalid")
    policy = public._policy_copy(market_policy)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    scope = _Scope(_ISSUER)
    try:
        started = native_stamp()
        expires = utc_from_ns(started["utc_ns"]) + timedelta(
            seconds=policy.total_timeout_seconds
        )
        invocation_id = uuid4().hex
        report = "initial-" + invocation_id
        plan = {
            "stage": "initial_public",
            "invocation_id": invocation_id,
            "environment": journal.GLOBAL_ENVIRONMENT,
            "report_id": report,
            "instrument_id": instrument_id,
            "invocation_started": started,
            "expires_at": expires.isoformat(),
            "rest_origin": journal.GLOBAL_REST_ORIGIN,
            "ws_origin": journal.GLOBAL_WS_ORIGIN,
            "global_route_sha256": journal.GLOBAL_ROUTE_SHA256,
            "policy_sha256": public._digest(policy),
        }
        full_plan = {"schema_version": journal.GLOBAL_INITIAL_PLAN, **plan}
        journal._validate_global_initial_plan(full_plan)
        invocation = object()
        _SCOPES[scope] = {
            "invocation": invocation,
            "parent": task,
            "pid": os.getpid(),
            "thread": get_ident(),
            "loop": asyncio.get_running_loop(),
            "plan": plan,
            "report_id": report,
            "instrument_id": instrument_id,
            "invocation_started": started,
            "publication_completed_at": None,
            "expires_at": expires,
        }
        carrier = await runtime._capture_initial_global_public(scope, policy, root)
        packet, journal_pin, _ = runtime._consume_initial_global_public(
            carrier, invocation
        )
        return GlobalPublicCaptureV1(
            "initial_global_public_captured",
            sha(canonical(full_plan)),
            journal_pin,
            packet,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- bounded public error, no transport or paths.
        if task.cancelling():
            raise asyncio.CancelledError from None
        return GlobalPublicCaptureV1("initial_global_public_denied")
    finally:
        _SCOPES.pop(scope, None)


def read_global_public_capture_v1(
    root, *, expected_plan_sha256, expected_journal_sha256
):
    """Reopen exact original bytes; audit data cannot issue another capture."""
    if (
        type(root) is not type(Path())
        or not root.is_absolute()
        or any(
            type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
            for pin in (expected_plan_sha256, expected_journal_sha256)
        )
    ):
        raise GlobalPublicCaptureError("global_public_readback_unavailable")
    try:
        identity = runtime._native_recheck_root_identity(root)
        with runtime._reopen_runtime_journal(root) as directory:
            summary = directory.read("summary.json", journal.MAX_RAW)
            if sha(summary) != expected_journal_sha256:
                raise GlobalPublicCaptureError("global_public_journal_pin_changed")
            result, _, packet = runtime.replay_public_runtime(
                directory, expected_plan_sha256=expected_plan_sha256
            )
            if (
                result["disposition"] != "captured"
                or type(packet) is not GlobalPublicComponentPacketV1
                or directory.read("summary.json", journal.MAX_RAW) != summary
                or runtime._native_recheck_root_identity(root) != identity
            ):
                raise GlobalPublicCaptureError("global_public_journal_readback_changed")
        return GlobalPublicCaptureV1(
            "initial_global_public_replayed",
            expected_plan_sha256,
            expected_journal_sha256,
            packet,
        )
    except Exception:  # noqa: BLE001 -- no private root or raw content escapes.
        raise GlobalPublicCaptureError("global_public_readback_unavailable") from None
