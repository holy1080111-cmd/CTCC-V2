"""Owned-source-only v2 quote seam and pure raw-packet replay.

The native seam reuses the unchanged v1 per-endpoint GET transport, not its
ExecutableQuote or common-age validation. Existing runtime plans do not opt in.
Packets remain diagnostic data; ownership stays in the existing private source
registry and is never reconstructed from packet bytes or a caller digest.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.trade_qualification import demo_public_origin as demo_origin
from app.trade_qualification import demo_public_origin_policy_v2 as demo_policy
from app.trade_qualification import quote_collector as wire
from app.trade_qualification.executable_quote_v2 import (
    POLICY_SHA256,
    ExecutableQuoteV2,
    RestMarkObservationV2,
    RestTickerObservationV2,
    inspect_executable_quote_v2,
)
from app.trade_qualification.funding_observation_v2 import parse_funding_observation_v2

SCHEMA_VERSION = "ctcc.collected_executable_quote.v2"
ORDER = (
    ("funding", "/api/v5/public/funding-rate"),
    ("mark", "/api/v5/public/mark-price"),
    ("ticker", "/api/v5/market/ticker"),
)
MAX_PACKET_BYTES = 512 * 1024


def _canonical(value):
    return wire._canonical(value).encode("utf-8")


TRANSPORT_POLICY_BYTES = _canonical(
    {
        "version": "ctcc.raw_quote_transport.v2",
        "origin": wire.BASE_URL,
        "method": "GET",
        "request_order": ORDER,
        "request_timeout_seconds": 2,
        "batch_timeout_seconds": 6,
        "max_response_bytes": 32768,
        "retries": 0,
        "redirects": False,
        "proxy": False,
        "native_journal": "existing_owned_source_fetch_http",
    }
)
TRANSPORT_POLICY_SHA256 = wire._sha(TRANSPORT_POLICY_BYTES)


def _transport_policy_sha256(route=None):
    """Retain the historical hash; route-bound Demo captures use V3 identity."""
    if route is None:
        return TRANSPORT_POLICY_SHA256
    demo_origin._checked_route(route)
    document = json.loads(TRANSPORT_POLICY_BYTES)
    document.update(
        version="ctcc.raw_quote_transport.v3",
        origin=route.rest_origin,
        registration_region=route.registration_region,
        request_headers=demo_origin.demo_public_headers(route, "quote"),
        demo_public_origin_policy_sha256=wire._sha(
            demo_policy.freeze_demo_public_origin_policy_v2(route.registration_region)
        ),
    )
    return wire._sha(_canonical(document))


class QuoteCollectionV2Error(ValueError):
    """Fixed diagnostic codes; no private or transport exception text."""


@dataclass(frozen=True, slots=True)
class CollectedQuoteV2:
    packet_json: bytes

    @property
    def bundle_sha256(self):
        return wire._sha(self.packet_json)

    @property
    def execution_authority(self):
        return False

    @property
    def source_authenticity_verified(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _deny(code):
    raise QuoteCollectionV2Error(code)


def _packet_tree(value, depth=0):
    if depth > 12:
        _deny("quote_v2_packet_depth_invalid")
    if type(value) is dict:
        if len(value) > 128 or any(
            type(key) is not str or len(key) > 128 for key in value
        ):
            _deny("quote_v2_packet_mapping_invalid")
        for item in value.values():
            _packet_tree(item, depth + 1)
    elif type(value) is list:
        if len(value) > 128:
            _deny("quote_v2_packet_array_invalid")
        for item in value:
            _packet_tree(item, depth + 1)
    elif type(value) is str and len(value) > wire.MAX_RESPONSE_BYTES:
        _deny("quote_v2_packet_text_invalid")


def _transport_policy():
    # The age field is unused by _collect_one. No v1 CollectedQuote is built.
    return wire.QuoteCollectionPolicy(
        max_age_seconds=5,
        request_timeout_seconds=2,
        batch_timeout_seconds=6,
        max_response_bytes=32768,
    )


def _checked_observations(observations):
    if type(observations) is not tuple or len(observations) != 3:
        _deny("quote_v2_exact_observations_required")
    checked = []
    for observation, (role, endpoint) in zip(observations, ORDER, strict=True):
        wire._observation_preflight(observation)
        replay = wire.EndpointObservation.model_validate(
            dict(observation.__dict__), strict=True
        )
        if (
            replay.role != role
            or replay.endpoint != endpoint
            or len(replay.response_body) > 32768
            or (checked and replay.request_started_at < checked[-1].completed_at)
        ):
            _deny("quote_v2_observation_scope_or_order_invalid")
        # completed_at is sampled after body consumption AND response close.
        # Native cleanup may finish after its IO timeout, but that never adds
        # time to this stricter successful-observation admission budget.
        if replay.completed_at - replay.request_started_at > timedelta(seconds=2):
            _deny("quote_v2_request_elapsed_limit")
        checked.append(replay)
    return tuple(checked)


def build_diagnostic_quote_packet_v2(
    *,
    report_id,
    instrument_id,
    environment,
    observations,
    capture_started_at,
    capture_completed_at,
    barrier_completed_at,
    route=None,
) -> CollectedQuoteV2:
    """Replay raw observation bytes; caller input cannot establish ownership."""
    try:
        return _build(
            report_id,
            instrument_id,
            environment,
            observations,
            capture_started_at,
            capture_completed_at,
            barrier_completed_at,
            route,
        )
    except QuoteCollectionV2Error:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError):
        raise QuoteCollectionV2Error("quote_v2_raw_replay_failed") from None


def _build(
    report,
    instrument,
    environment,
    observations,
    started,
    completed,
    barrier,
    route=None,
):
    checked = _checked_observations(observations)
    if route is None:
        if any(item.origin != wire.BASE_URL for item in checked):
            _deny("quote_v2_origin_mismatch")
    else:
        demo_origin._checked_route(route)
        if environment != "demo" or any(
            item.origin != route.rest_origin for item in checked
        ):
            _deny("quote_v2_origin_mismatch")
    started, completed = map(wire._utc, (started, completed))
    barrier = None if barrier is None else wire._utc(barrier)
    if (
        completed < started
        or any(item.request_started_at < started for item in checked)
        or any(item.completed_at > completed for item in checked)
    ):
        _deny("quote_v2_observation_outside_capture")
    if completed - started > timedelta(seconds=6):
        _deny("quote_v2_capture_elapsed_limit")
    if barrier is not None and (
        started <= barrier
        or any(item.request_started_at <= barrier for item in checked)
    ):
        _deny("quote_v2_publication_barrier_not_crossed")
    if any(item.instrument_id != instrument for item in checked):
        _deny("quote_v2_instrument_mismatch")
    funding_wire, mark_wire, ticker_wire = checked
    funding = parse_funding_observation_v2(
        funding_wire.response_body,
        source_kind="rest",
        instrument_id=instrument,
        acquisition_started_at=funding_wire.request_started_at,
        received_at=funding_wire.received_at,
        completed_at=funding_wire.completed_at,
    )
    rows = {
        item.role: wire._row(wire._parse(item.response_body)[0], instrument)
        for item in checked
    }
    bid, ask, bid_size, ask_size = wire._values("ticker", rows["ticker"])
    (mark_price,) = wire._values("mark", rows["mark"])

    def stamps(item):
        return {
            "instrument_id": instrument,
            "request_started_at": item.request_started_at,
            "headers_received_at": item.received_at,
            "body_completed_at": item.completed_at,
            "raw_body_sha256": item.body_sha256,
        }

    quote = ExecutableQuoteV2(
        report_id=report,
        instrument_id=instrument,
        environment=environment,
        capture_started_at=started,
        capture_completed_at=completed,
        ticker=RestTickerObservationV2(
            bid=bid,
            ask=ask,
            bid_size_contracts=bid_size,
            ask_size_contracts=ask_size,
            source_generated_at=ticker_wire.source_time,
            **stamps(ticker_wire),
        ),
        mark=RestMarkObservationV2(
            price=mark_price,
            exchange_data_return_at=mark_wire.source_time,
            **stamps(mark_wire),
        ),
        funding=funding,
    )
    inspection = inspect_executable_quote_v2(quote, current_time=completed)
    receipt = json.loads(inspection.receipt_json)
    if receipt["profile_satisfied"] is not True:
        _deny("quote_v2_profile_rejected")
    packet = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report,
        "instrument_id": instrument,
        "environment": environment,
        "quote_profile_sha256": POLICY_SHA256,
        "transport_policy_sha256": _transport_policy_sha256(route),
        "capture_started_at": started.isoformat(),
        "capture_completed_at": completed.isoformat(),
        "barrier_completed_at": None if barrier is None else barrier.isoformat(),
        "provenance": [
            item.model_dump(mode="json", round_trip=True) for item in checked
        ],
        "inspection": receipt,
        "inspection_sha256": inspection.receipt_sha256,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    if route is not None:
        packet.update(
            registration_region=route.registration_region,
            registration_region_authenticated=False,
            rest_origin=route.rest_origin,
            demo_public_origin_policy_sha256=wire._sha(
                demo_policy.freeze_demo_public_origin_policy_v2(
                    route.registration_region
                )
            ),
        )
    encoded = _canonical(packet)
    if len(encoded) > MAX_PACKET_BYTES:
        _deny("quote_v2_packet_size_limit")
    return CollectedQuoteV2(encoded)


def replay_quote_packet_v2(raw, *, expected_sha256) -> CollectedQuoteV2:
    """Offline integrity/semantics only; a matching digest never issues a scope."""
    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= MAX_PACKET_BYTES
            or type(expected_sha256) is not str
            or wire._sha(raw) != expected_sha256
        ):
            _deny("quote_v2_packet_identity_invalid")

        # Embedded raw response strings can exceed the per-field bound used
        # inside an exchange response. Bound the packet separately, then replay
        # every original response through the unchanged endpoint validator.
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    _deny("quote_v2_duplicate_packet_key")
                value[key] = item
            return value

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: _deny("quote_v2_packet_nonfinite"),
        )
        _packet_tree(value)
        if (
            type(value) is not dict
            or type(value.get("provenance")) is not list
            or len(value["provenance"]) != 3
        ):
            _deny("quote_v2_packet_shape_invalid")
        observations = tuple(
            wire.EndpointObservation.model_validate_json(_canonical(item), strict=True)
            for item in value["provenance"]
        )
        route = (
            demo_origin.reviewed_demo_public_route(value["registration_region"])
            if "registration_region" in value
            else None
        )

        def instant(name):
            text = value[name]
            if type(text) is not str or len(text) > 40:
                _deny("quote_v2_packet_timestamp_invalid")
            return wire._utc(datetime.fromisoformat(text))

        replay = _build(
            value["report_id"],
            value["instrument_id"],
            value["environment"],
            observations,
            instant("capture_started_at"),
            instant("capture_completed_at"),
            None
            if value["barrier_completed_at"] is None
            else instant("barrier_completed_at"),
            route,
        )
        if replay.packet_json != raw:
            _deny("quote_v2_packet_replay_mismatch")
        return replay
    except QuoteCollectionV2Error:
        raise
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ):
        raise QuoteCollectionV2Error("quote_v2_packet_replay_failed") from None


async def _collect_owned_quote_v2(source) -> CollectedQuoteV2:
    """Internal future-owner seam. Legacy scopes lack the pinned v2 profile."""
    from app.trade_qualification import public_source_runtime as runtime
    from app.trade_qualification.public_market_collector import _close_client

    state = runtime._state(source)
    plan = state["plan"]
    if type(plan) is dict and plan.get("environment") == "demo":
        # Direct internal calls cannot bypass the public owner into a clock or
        # three REST requests with a caller-declared registration region.
        runtime._require_trusted_v2_demo_origin_profile(plan)
    route = (
        demo_origin.reviewed_demo_public_route(plan["registration_region"])
        if type(plan) is dict and "registration_region" in plan
        else None
    )
    if type(plan) is not dict or any(
        plan.get(key) != expected
        for key, expected in (
            ("quote_collector_schema", SCHEMA_VERSION),
            ("quote_profile_sha256", POLICY_SHA256),
            ("quote_transport_policy_sha256", _transport_policy_sha256(route)),
        )
    ):
        _deny("quote_v2_owned_profile_required")
    if (
        type(plan.get("environment")) is not str
        or plan["environment"] not in ("analysis_only", "demo")
        or plan.get("rest_origin")
        != (wire.BASE_URL if route is None else route.rest_origin)
        or plan.get("report_id") != state["report_id"]
        or plan.get("instrument_id") != state["instrument_id"]
        or plan.get("stage") not in ("initial_public", "post_publication")
        or (plan["stage"] == "initial_public")
        != (state["publication_completed_at"] is None)
    ):
        _deny("quote_v2_owned_scope_invalid")
    if "quote_v2_attempted" in state:
        _deny("quote_v2_invocation_already_used")
    state["quote_v2_attempted"] = True
    observations = []
    try:
        started = state["clock"]()
        runtime._check_inputs(
            source,
            clock=state["clock"],
            report_id=state["report_id"],
            instrument_id=state["instrument_id"],
            barrier_completed_at=state["publication_completed_at"],
        )
        policy = _transport_policy()
        client = runtime._new_owned_client(source, "quote")
        pending_cancel = False
        try:
            async with asyncio.timeout(6):
                for role, endpoint in ORDER:
                    observation = await wire._collect_one(
                        client,
                        state["clock"],
                        role,
                        endpoint,
                        state["instrument_id"],
                        policy,
                        state["publication_completed_at"],
                        observations[-1].completed_at if observations else started,
                        _source=source,
                    )
                    observations.append(observation)
        except asyncio.CancelledError:
            pending_cancel = True
            raise
        finally:
            # Close once with the same bounded, shielded cleanup as the full
            # public collector. Its time remains inside measured admission.
            await _close_client(client, 1, pending_cancel=pending_cancel)
        completed = state["clock"]()
        runtime._state(source)
        packet = build_diagnostic_quote_packet_v2(
            report_id=state["report_id"],
            instrument_id=state["instrument_id"],
            environment=plan["environment"],
            observations=tuple(observations),
            capture_started_at=started,
            capture_completed_at=completed,
            barrier_completed_at=state["publication_completed_at"],
            route=route,
        )
        for item in observations:
            expected = (
                item.endpoint,
                item.response_body,
                item.request_started_at,
                item.received_at,
                item.completed_at,
            )
            matches = [
                actual
                for actual in state["responses"]
                if (
                    (actual[0], *actual[2:]) == expected
                    and tuple(sorted(actual[1])) == item.parameters
                )
            ]
            if len(matches) != 1:
                _deny("quote_v2_owned_raw_membership_mismatch")
        return replay_quote_packet_v2(
            packet.packet_json, expected_sha256=packet.bundle_sha256
        )
    except asyncio.CancelledError:
        try:
            runtime._event(
                source,
                "component_failed",
                {"role": "quote", "code": "component_failed"},
            )
        except Exception:  # noqa: BLE001, S110 -- preserve cancellation and prior journal
            pass
        raise
    except Exception:  # noqa: BLE001 -- no transport or private state in outward errors
        try:
            runtime._event(
                source,
                "component_failed",
                {"role": "quote", "code": "component_failed"},
            )
        except Exception:  # noqa: BLE001, S110 -- existing raw journal is never removed
            pass
        raise QuoteCollectionV2Error("owned_quote_v2_capture_denied") from None
