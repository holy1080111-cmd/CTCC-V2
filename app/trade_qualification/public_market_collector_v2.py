"""Full v2 public raw packet and collection through the existing native owner.

Offline packets are replay data. Only public_source_runtime can own IO and issue
a one-use same-invocation handoff; this module never creates a source registry.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import demo_public_origin as demo_origin
from app.trade_qualification import demo_public_origin_policy_v2 as demo_policy
from app.trade_qualification import market_aux_collector as aux
from app.trade_qualification import public_market_collector as legacy
from app.trade_qualification import quote_collector as wire
from app.trade_qualification import quote_collector_v2 as quotes
from app.trade_qualification import ws_collector as ws
from app.trade_qualification.executable_quote_v2 import (
    ExecutableQuoteV2,
    RestMarkObservationV2,
    RestTickerObservationV2,
    inspect_executable_quote_v2,
)
from app.trade_qualification.funding_observation_v2 import parse_funding_observation_v2

SCHEMA_VERSION = "ctcc.collected_public_market.v2"
POLICY_VERSION = "ctcc.public_market_collection_policy.v2"
ROUTED_POLICY_VERSION = "ctcc.public_market_collection_policy.v3"
MAX_PACKET_BYTES = 16 * 1024 * 1024
_POLICY_PARTS = (
    ("candles", candles.CandleCollectionPolicy),
    ("market_aux", aux.MarketAuxCollectionPolicy),
    ("ws", ws.WSCollectionPolicy),
)
_FLAGS = (
    "source_authenticity_verified",
    "original_source_verified",
    "account_complete",
    "qualification_performed",
    "execution_recheck_performed",
    "execution_authority",
)


class PublicMarketV2Error(ValueError):
    """Fixed public diagnostic codes only."""


@dataclass(frozen=True, slots=True)
class PublicMarketCollectionPolicyV2:
    candles: candles.CandleCollectionPolicy
    market_aux: aux.MarketAuxCollectionPolicy
    ws: ws.WSCollectionPolicy
    total_timeout_seconds: int = 30
    client_close_timeout_seconds: int = 1


@dataclass(frozen=True, slots=True)
class CollectedPublicMarketV2:
    packet_json: bytes

    @property
    def bundle_sha256(self):
        return sha(self.packet_json)

    @property
    def admission(self):
        return "DENY"

    @property
    def execution_authority(self):
        return False


def _deny(code):
    raise PublicMarketV2Error(code)


def _policy_copy(value):
    if type(value) is not PublicMarketCollectionPolicyV2:
        _deny("public_v2_exact_policy_required")
    copied = {}
    for name, cls in _POLICY_PARTS:
        part = getattr(value, name)
        if type(part) is not cls:
            _deny("public_v2_component_policy_invalid")
        legacy._guard(part)
        copied[name] = cls.model_validate_json(
            canonical(part.model_dump(mode="json", round_trip=True)), strict=True
        )
    for name, maximum in (
        ("total_timeout_seconds", 60),
        ("client_close_timeout_seconds", 3),
    ):
        number = getattr(value, name)
        if type(number) is not int or not 1 <= number <= maximum:
            _deny("public_v2_policy_bound_invalid")
        copied[name] = number
    return PublicMarketCollectionPolicyV2(**copied)


def _plan_pins(route=None):
    return {
        "public_packet_schema": SCHEMA_VERSION,
        "quote_collector_schema": quotes.SCHEMA_VERSION,
        "quote_profile_sha256": quotes.POLICY_SHA256,
        "quote_transport_policy_sha256": quotes._transport_policy_sha256(route),
    }


def _policy_document(value, route=None):
    selected = _policy_copy(value)
    document = {
        "schema_version": POLICY_VERSION if route is None else ROUTED_POLICY_VERSION,
        "schedule": "candles_then_parallel_quote_aux_ws",
        **_plan_pins(route),
        **{
            name: getattr(selected, name).model_dump(mode="json", round_trip=True)
            for name, _ in _POLICY_PARTS
        },
        "total_timeout_seconds": selected.total_timeout_seconds,
        "client_close_timeout_seconds": selected.client_close_timeout_seconds,
    }
    if route is not None:
        demo_origin._checked_route(route)
        document.update(
            registration_region=route.registration_region,
            registration_region_authenticated=False,
            rest_origin=route.rest_origin,
            ws_origin=route.ws_origin,
            demo_public_origin_policy_sha256=sha(
                demo_policy.freeze_demo_public_origin_policy_v2(
                    route.registration_region
                )
            ),
        )
    return document


def _policy_digest(value, route=None):
    return sha(canonical(_policy_document(value, route)))


def _policy_from_document(value):
    if type(value) is not dict:
        _deny("public_v2_policy_document_invalid")
    route = (
        demo_origin.reviewed_demo_public_route(value["registration_region"])
        if "registration_region" in value
        else None
    )
    selected = PublicMarketCollectionPolicyV2(
        **{
            name: cls.model_validate_json(canonical(value[name]), strict=True)
            for name, cls in _POLICY_PARTS
        },
        total_timeout_seconds=value["total_timeout_seconds"],
        client_close_timeout_seconds=value["client_close_timeout_seconds"],
    )
    if canonical(_policy_document(selected, route)) != canonical(value):
        _deny("public_v2_policy_document_changed")
    return _policy_copy(selected), route


def _quote_parts(packet):
    if (
        type(packet) is not quotes.CollectedQuoteV2
        or type(packet.packet_json) is not bytes
        or not 0 < len(packet.packet_json) <= quotes.MAX_PACKET_BYTES
    ):
        _deny("public_v2_quote_type_invalid")
    checked = quotes.replay_quote_packet_v2(
        packet.packet_json, expected_sha256=packet.bundle_sha256
    )
    document = decode(checked.packet_json, quotes.MAX_PACKET_BYTES)
    observations = tuple(
        wire.EndpointObservation.model_validate_json(canonical(item), strict=True)
        for item in document["provenance"]
    )
    funding, mark, ticker = observations
    mark_row = wire._row(wire._parse(mark.response_body)[0], mark.instrument_id)
    ticker_row = wire._row(wire._parse(ticker.response_body)[0], ticker.instrument_id)
    bid, ask, bid_size, ask_size = wire._values("ticker", ticker_row)

    def stamps(item):
        return {
            "instrument_id": item.instrument_id,
            "request_started_at": item.request_started_at,
            "headers_received_at": item.received_at,
            "body_completed_at": item.completed_at,
            "raw_body_sha256": item.body_sha256,
        }

    value = ExecutableQuoteV2(
        report_id=document["report_id"],
        instrument_id=document["instrument_id"],
        environment=document["environment"],
        capture_started_at=_time(document["capture_started_at"]),
        capture_completed_at=_time(document["capture_completed_at"]),
        ticker=RestTickerObservationV2(
            bid=bid,
            ask=ask,
            bid_size_contracts=bid_size,
            ask_size_contracts=ask_size,
            source_generated_at=ticker.source_time,
            **stamps(ticker),
        ),
        mark=RestMarkObservationV2(
            price=wire._values("mark", mark_row)[0],
            exchange_data_return_at=mark.source_time,
            **stamps(mark),
        ),
        funding=parse_funding_observation_v2(
            funding.response_body,
            source_kind="rest",
            instrument_id=funding.instrument_id,
            acquisition_started_at=funding.request_started_at,
            received_at=funding.received_at,
            completed_at=funding.completed_at,
        ),
    )
    return document, value, observations


def _time(value):
    if type(value) is not str or len(value) > 40:
        _deny("public_v2_time_invalid")
    return wire._utc(datetime.fromisoformat(value))


_SCOPE_KEYS = frozenset(
    {
        "invocation_id",
        "stage",
        "environment",
        "report_id",
        "instrument_id",
        "barrier_completed_at",
    }
)
_ROUTE_SCOPE_KEYS = frozenset(
    {
        "registration_region",
        "registration_region_authenticated",
        "account_plan_sha256",
        "demo_public_origin_policy_sha256",
        "rest_origin",
        "ws_origin",
    }
)


def _scope_route(scope):
    if type(scope) is not dict or set(scope) not in (
        _SCOPE_KEYS,
        _SCOPE_KEYS | _ROUTE_SCOPE_KEYS,
    ):
        _deny("public_v2_scope_invalid")
    if set(scope) == _SCOPE_KEYS:
        return None
    route = demo_origin.reviewed_demo_public_route(scope["registration_region"])
    if (
        scope["registration_region_authenticated"] is not False
        or type(scope["account_plan_sha256"]) is not str
        or re.fullmatch("[a-f0-9]{64}", scope["account_plan_sha256"]) is None
        or scope["rest_origin"] != route.rest_origin
        or scope["ws_origin"] != route.ws_origin
        or scope["demo_public_origin_policy_sha256"]
        != sha(
            demo_policy.freeze_demo_public_origin_policy_v2(route.registration_region)
        )
    ):
        _deny("public_v2_route_scope_invalid")
    return route


def _build(
    *, scope, policy, quote, candle_packet, market_aux, reference, started, completed
):
    selected = _policy_copy(policy)
    route = _scope_route(scope)
    canonical(scope)  # reject opaque/subclass values before comparisons
    if (
        type(scope["invocation_id"]) is not str
        or re.fullmatch("[a-f0-9]{32}", scope["invocation_id"]) is None
        or type(scope["report_id"]) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", scope["report_id"])
        is None
        or type(scope["instrument_id"]) is not str
        or scope["instrument_id"] not in REVIEWED_DEMO_INSTRUMENT_IDS
        or scope["environment"] != "demo"
        or scope["stage"] not in ("initial_public", "post_publication")
        or (scope["stage"] == "initial_public")
        != (scope["barrier_completed_at"] is None)
    ):
        _deny("public_v2_scope_invalid")
    start, finish = map(wire._utc, (started, completed))
    barrier = (
        None
        if scope["barrier_completed_at"] is None
        else _time(scope["barrier_completed_at"])
    )
    if (
        finish < start
        or finish - start > timedelta(seconds=selected.total_timeout_seconds)
        or (barrier is not None and start <= barrier)
    ):
        _deny("public_v2_capture_interval_invalid")
    candle_packet = candles.validate_collected_candles(candle_packet)
    market_aux = aux.validate_collected_market_aux(market_aux)
    reference = ws.validate_collected_ws_reference(reference)
    document, current_quote, quote_observations = _quote_parts(quote)
    rest_origin = wire.BASE_URL if route is None else route.rest_origin
    ws_origins = (
        {ws.PUBLIC_WS_URL, ws.LEGACY_PUBLIC_WS_URL}
        if route is None
        else {route.ws_origin}
    )
    if (
        any(item.origin != rest_origin for item in quote_observations)
        or any(
            page.origin != rest_origin
            for frame in candle_packet.frames
            for page in frame.pages
        )
        or any(item.origin != rest_origin for item in market_aux.provenance)
        or reference.endpoint not in ws_origins
        or document["transport_policy_sha256"] != quotes._transport_policy_sha256(route)
    ):
        _deny("public_v2_component_route_mismatch")
    if route is not None and any(
        document.get(name) != scope[name]
        for name in (
            "registration_region",
            "registration_region_authenticated",
            "rest_origin",
            "demo_public_origin_policy_sha256",
        )
    ):
        _deny("public_v2_quote_route_mismatch")
    if (
        document["report_id"] != scope["report_id"]
        or document["instrument_id"] != scope["instrument_id"]
        or document["environment"] != scope["environment"]
        or document["barrier_completed_at"] != scope["barrier_completed_at"]
        or current_quote.capture_started_at < start
        or current_quote.capture_completed_at > finish
    ):
        _deny("public_v2_quote_scope_mismatch")
    for name, component, first in (
        ("candles", candle_packet, candle_packet.frames[0].pages[0].request_started_at),
        ("market_aux", market_aux, market_aux.provenance[0].request_started_at),
        ("ws", reference, reference.connection_started_at),
    ):
        if (
            component.report_id != scope["report_id"]
            or component.instrument_id != scope["instrument_id"]
            or component.policy != getattr(selected, name)
            or component.barrier_completed_at != barrier
            or first < start
            or component.completed_at > finish
        ):
            _deny("public_v2_component_scope_mismatch")
        if name != "candles" and first < candle_packet.completed_at:
            _deny("public_v2_acquisition_schedule_mismatch")
    if current_quote.capture_started_at < candle_packet.completed_at:
        _deny("public_v2_acquisition_schedule_mismatch")
    inspection = inspect_executable_quote_v2(current_quote, current_time=finish)
    receipt = decode(inspection.receipt_json, quotes.MAX_PACKET_BYTES)
    if receipt["profile_satisfied"] is not True:
        _deny("public_v2_quote_stale_at_completion")
    for item in market_aux.provenance:
        if finish - item.source_time > timedelta(
            seconds=selected.market_aux.max_age_seconds
        ):
            _deny("public_v2_aux_stale_at_completion")
    if finish - reference.ticker.source_time > timedelta(
        seconds=selected.ws.max_age_seconds
    ):
        _deny("public_v2_ws_stale_at_completion")
    for frame in candle_packet.frames:
        if frame.verified_through != candles._floor(finish, frame.timeframe):
            _deny("public_v2_candle_tail_missing_at_completion")
    value = {
        "schema_version": SCHEMA_VERSION,
        **scope,
        **_plan_pins(route),
        "policy": _policy_document(selected, route),
        "policy_sha256": _policy_digest(selected, route),
        "started_at": start.isoformat(),
        "completed_at": finish.isoformat(),
        "quote": document,
        "quote_sha256": quote.bundle_sha256,
        "candles": candle_packet.model_dump(mode="json", round_trip=True),
        "market_aux": market_aux.model_dump(mode="json", round_trip=True),
        "ws": reference.model_dump(mode="json", round_trip=True),
        "completion_quote_inspection": receipt,
        "completion_quote_inspection_sha256": inspection.receipt_sha256,
        **{name: False for name in _FLAGS},
        "admission": "DENY",
    }
    raw = canonical(value)
    if len(raw) > MAX_PACKET_BYTES:
        _deny("public_v2_packet_size_limit")
    return CollectedPublicMarketV2(raw), (
        selected,
        current_quote,
        quote_observations,
        candle_packet,
        market_aux,
        reference,
    )


def _replay(raw):
    value = decode(raw, MAX_PACKET_BYTES)
    selected, route = _policy_from_document(value["policy"])
    scope_keys = _SCOPE_KEYS | (_ROUTE_SCOPE_KEYS if route is not None else frozenset())
    packet, parts = _build(
        scope={key: value[key] for key in scope_keys},
        policy=selected,
        quote=quotes.CollectedQuoteV2(quotes._canonical(value["quote"])),
        candle_packet=candles.CollectedCandles.model_validate_json(
            canonical(value["candles"]), strict=True
        ),
        market_aux=aux.CollectedMarketAux.model_validate_json(
            canonical(value["market_aux"]), strict=True
        ),
        reference=ws.CollectedWSReference.model_validate_json(
            canonical(value["ws"]), strict=True
        ),
        started=_time(value["started_at"]),
        completed=_time(value["completed_at"]),
    )
    if packet.packet_json != raw:
        _deny("public_v2_packet_replay_mismatch")
    return packet, parts


def replay_collected_public_market_v2(raw, *, expected_sha256):
    """Complete raw semantic replay; this never restores a native handoff."""
    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= MAX_PACKET_BYTES
            or type(expected_sha256) is not str
            or re.fullmatch("[a-f0-9]{64}", expected_sha256) is None
            or sha(raw) != expected_sha256
        ):
            _deny("public_v2_packet_pin_invalid")
        return _replay(raw)[0]
    except PublicMarketV2Error:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ArithmeticError,
        RecursionError,
    ):
        raise PublicMarketV2Error("public_v2_packet_invalid") from None


def _parts(packet):
    if (
        type(packet) is not CollectedPublicMarketV2
        or type(packet.packet_json) is not bytes
        or not 0 < len(packet.packet_json) <= MAX_PACKET_BYTES
    ):
        _deny("public_v2_exact_packet_required")
    return _replay(packet.packet_json)


async def _collect_owned_public_market_v2(source):
    from app.trade_qualification import public_source_runtime as runtime

    state = runtime._state(source)
    selected = _policy_copy(state["policy"])
    plan = state["plan"]
    if type(plan) is dict and plan.get("environment") == "demo":
        # Even a malformed caller-built V2 route cannot reach a clock or IO.
        runtime._require_trusted_v2_demo_origin_profile(plan)
    route = (
        demo_origin.reviewed_demo_public_route(plan["registration_region"])
        if type(plan) is dict and "registration_region" in plan
        else None
    )
    if (
        type(plan) is not dict
        or any(plan.get(key) != value for key, value in _plan_pins(route).items())
        or plan.get("policy_sha256") != _policy_digest(selected, route)
    ):
        _deny("public_v2_owned_plan_required")
    # The collector is also an internal entry point. A future caller must not
    # bypass the runtime's capture-level origin check and leave a V2 Demo
    # journal after sampling a clock or opening a transport. Reviewed route
    # strings alone are still no account-region or credential-session proof.
    if "public_v2_attempted" in state:
        _deny("public_v2_invocation_already_used")
    state["public_v2_attempted"] = True
    started = state["clock"]()
    common = {
        "clock": state["clock"],
        "report_id": state["report_id"],
        "instrument_id": state["instrument_id"],
        "barrier_completed_at": state["publication_completed_at"],
    }
    runtime._check_inputs(source, **common)
    async with asyncio.timeout(selected.total_timeout_seconds):
        try:
            candle_packet = await legacy._http_capture(
                candles.collect_candles,
                selected.client_close_timeout_seconds,
                _source=source,
                _role="candles",
                policy=selected.candles,
                **common,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            runtime._event(
                source,
                "component_failed",
                {"role": "candles", "code": "component_failed"},
            )
            raise
        tasks = {}
        try:
            tasks["quote"] = asyncio.create_task(quotes._collect_owned_quote_v2(source))
            tasks["market_aux"] = asyncio.create_task(
                legacy._http_capture(
                    aux.collect_market_aux,
                    selected.client_close_timeout_seconds,
                    _source=source,
                    _role="market_aux",
                    policy=selected.market_aux,
                    **common,
                )
            )
            tasks["ws"] = asyncio.create_task(
                ws.collect_ws_reference(policy=selected.ws, _source=source, **common)
            )
        except asyncio.CancelledError:
            await legacy._join_component_tasks(
                tasks, _source=source, already_failed=True, interrupted=True
            )
            raise
        except Exception:
            await legacy._join_component_tasks(
                tasks, _source=source, already_failed=True
            )
            raise
        outcomes = await legacy._join_component_tasks(tasks, _source=source)
    completed = state["clock"]()
    packet, _ = _build(
        scope={
            "invocation_id": plan["invocation_id"],
            "stage": plan["stage"],
            "environment": plan["environment"],
            "report_id": state["report_id"],
            "instrument_id": state["instrument_id"],
            "barrier_completed_at": None
            if state["publication_completed_at"] is None
            else state["publication_completed_at"].isoformat(),
            **(
                {name: plan[name] for name in _ROUTE_SCOPE_KEYS}
                if route is not None
                else {}
            ),
        },
        policy=selected,
        quote=outcomes["quote"],
        candle_packet=candle_packet,
        market_aux=outcomes["market_aux"],
        reference=outcomes["ws"],
        started=started,
        completed=completed,
    )
    return packet
