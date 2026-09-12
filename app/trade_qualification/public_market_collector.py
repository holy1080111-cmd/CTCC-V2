"""One bounded concurrent public capture, not a legacy snapshot or entry gate.

Three fresh HTTP clients and one owned public socket are closed before return.
Wire records remain separate: in particular no SWAP currency volume is relabelled
as quote volume. Clock, transport and serialized provenance are dependencies and
claims, not source authentication. No account, order, retry or runtime wiring.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.trade_qualification import candle_collector as candles
from app.trade_qualification import market_aux_collector as aux
from app.trade_qualification import quote_collector as quotes
from app.trade_qualification import ws_collector as ws
from app.trade_qualification.event_models import Digest
from app.trade_qualification.location import ExecutableQuote
from app.trade_qualification.models import QualificationModel, ReportId
from app.trade_qualification.ws_reference import WSSubscriptionAck, WSTickerFrame

_FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "socket_binding_verified",
    "complete_path_verified",
    "execution_recheck_performed",
    "qualification_performed",
)


# Static local vocabulary reviewed against the four collector modules. No runtime
# source inspection, and valid ASCII syntax alone never makes a code admissible.
_HTTP_CODES = frozenset(
    [
        "clock_invalid",
        "instrument_id_invalid",
        "public_client_invalid",
        "public_client_not_isolated",
        "public_client_transport_not_isolated",
        "public_client_transport_retries_forbidden",
        "response_cleanup_failed",
        "source_timestamp_invalid",
        "source_decimal_invalid",
        "source_decimal_nonpositive",
        "response_nesting_exceeded",
        "response_object_too_large",
        "response_array_too_large",
        "response_string_too_large",
        "response_number_invalid",
        "response_too_large",
        "duplicate_response_key",
        "nonfinite_response_number",
        "canonical_response_too_large",
        "response_json_invalid",
        "batch_clock_reversed",
        "publication_barrier_not_crossed",
        "request_clock_reversed",
        "http_response_rejected",
        "response_media_type_invalid",
        "response_encoding_rejected",
        "response_length_mismatch",
        "response_status_invalid",
        "response_cardinality_invalid",
        "response_identity_mismatch",
    ]
)
_QUOTE_CODES = _HTTP_CODES | frozenset(
    [
        "crossed_executable_quote",
        "collected_quote_invalid",
        "provenance_cardinality_invalid",
        "parameters_shape_invalid",
        "observation_body_shape_invalid",
        "observation_text_shape_invalid",
        "public_quote_transport_failed",
        "public_quote_capture_invalid",
        "future_component_timestamp",
        "component_stale",
        "request_deadline_exceeded",
        "batch_deadline_exceeded",
    ]
)
_CANDLE_CODES = _HTTP_CODES | frozenset(
    [
        "capture_traversal_limit",
        "capture_model_type_invalid",
        "capture_model_fields_invalid",
        "capture_raw_pin_invalid",
        "capture_sequence_invalid",
        "capture_parameters_invalid",
        "capture_mapping_invalid",
        "capture_string_invalid",
        "capture_decimal_invalid",
        "capture_value_invalid",
        "capture_bundle_too_large",
        "response_schema_invalid",
        "candle_page_cardinality_invalid",
        "candle_row_schema_invalid",
        "candle_off_grid",
        "candle_confirmation_invalid",
        "future_candle",
        "unconfirmed_candle_not_current",
        "candle_page_order_or_gap",
        "candle_page_budget_exceeded",
        "candle_page_chain_invalid",
        "page_clock_reversed",
        "page_capture_policy_exceeded",
        "candle_cross_page_gap_or_overlap",
        "unconfirmed_candle_not_first",
        "confirmed_candle_exceeds_capture_cutoff",
        "confirmed_coverage_incomplete",
        "required_closed_tail_missing",
        "collected_candles_invalid",
        "report_id_invalid",
        "public_candle_transport_failed",
        "public_candle_capture_invalid",
    ]
)
_AUX_CODES = _HTTP_CODES | frozenset(
    [
        "book_level_count_invalid",
        "book_level_shape_invalid",
        "deprecated_book_field_invalid",
        "book_order_count_invalid",
        "book_sequence_invalid",
        "open_interest_negative",
        "record_nesting_invalid",
        "record_model_invalid",
        "record_value_invalid",
        "record_type_invalid",
        "collected_market_aux_invalid",
        "request_deadline_exceeded",
        "component_stale",
        "public_market_aux_transport_failed",
        "public_market_aux_capture_invalid",
    ]
)
_WS_CODES = frozenset(
    [
        "ws_clock_invalid",
        "ws_policy_invalid",
        "ws_record_invalid",
        "ws_cleanup_failed",
        "ws_message_type_or_size_invalid",
        "ws_message_encoding_invalid",
        "ws_frame_too_large",
        "ws_instrument_id_invalid",
        "ws_publication_barrier_not_crossed",
        "ws_capture_clock_reversed",
        "ws_wire_record_rejected",
        "ws_transport_timeout",
        "ws_public_capture_failed",
        "ws_reference_stale",
    ]
)


class PublicMarketCollectionError(ValueError):
    """Stable local codes only, never transport errors or remote response bodies."""

    __slots__ = ("_component_failures",)

    def __init__(self, code, *, component_failures=()):
        if type(component_failures) is not tuple or len(component_failures) > 4:
            raise ValueError("public_failure_metadata_invalid")
        for pair in component_failures:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or pair[0] not in ("quote", "candles", "market_aux", "ws")
                or type(pair[1]) is not str
                or re.fullmatch(r"[a-z0-9_]{1,80}", pair[1]) is None
            ):
                raise ValueError("public_failure_metadata_invalid")
        object.__setattr__(self, "_component_failures", component_failures)
        super().__init__(code)

    @property
    def component_failures(self):
        return self._component_failures

    def __setattr__(self, name, value):
        if name in ("component_failures", "_component_failures"):
            raise AttributeError("public failure metadata is immutable")
        super().__setattr__(name, value)


def _raw_pins(values):
    for name, value in values.items():
        pattern = None
        if name.endswith("_sha256"):
            pattern = r"[a-f0-9]{64}"
        elif name == "report_id":
            pattern = r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}"
        elif name == "instrument_id":
            pattern = r"[A-Z0-9]{1,16}-[A-Z0-9]{1,16}-SWAP"
        if pattern is not None and (
            type(value) is not str or re.fullmatch(pattern, value) is None
        ):
            raise PublicMarketCollectionError("public_raw_pin_invalid")


def _edges(cls, value):
    values = value.__dict__ if isinstance(value, BaseModel) else value
    if type(values) is not dict:
        return
    expected = {
        PublicMarketCollectionPolicy: {
            "quote": quotes.QuoteCollectionPolicy,
            "candles": candles.CandleCollectionPolicy,
            "market_aux": aux.MarketAuxCollectionPolicy,
            "ws": ws.WSCollectionPolicy,
        },
        CollectedPublicMarket: {
            "policy": PublicMarketCollectionPolicy,
            "quote": quotes.CollectedQuote,
            "candles": candles.CollectedCandles,
            "market_aux": aux.CollectedMarketAux,
            "ws": ws.CollectedWSReference,
        },
        quotes.CollectedQuote: {
            "policy": quotes.QuoteCollectionPolicy,
            "quote": ExecutableQuote,
        },
        candles.CollectedCandles: {"policy": candles.CandleCollectionPolicy},
        candles.FrameCapture: {"policy": candles.CandleCollectionPolicy},
        aux.CollectedMarketAux: {
            "policy": aux.MarketAuxCollectionPolicy,
            "book": aux.CapturedOrderBook,
            "open_interest": aux.CapturedOpenInterest,
        },
        ws.CollectedWSReference: {
            "policy": ws.WSCollectionPolicy,
            "ack": WSSubscriptionAck,
            "ticker": WSTickerFrame,
        },
    }.get(cls, {})
    for name, expected_type in expected.items():
        if name in values and type(values[name]) not in (dict, expected_type):
            raise PublicMarketCollectionError("public_component_model_type_invalid")


def _validate_components(values):
    for name, validator in (
        ("quote", quotes.validate_collected_quote),
        ("candles", candles.validate_collected_candles),
        ("market_aux", aux.validate_collected_market_aux),
        ("ws", ws.validate_collected_ws_reference),
    ):
        if name in values and isinstance(values[name], BaseModel):
            validator(values[name])


def _guard(value, depth=0, budget=None, *, json_input=False):
    """Inspect raw exact types BEFORE nested serializers can hide dirty inputs."""
    if budget is None:
        budget = [160000, 32 * 1024 * 1024]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise PublicMarketCollectionError("public_record_traversal_limit")
    if isinstance(value, BaseModel):
        allowed = {
            PublicMarketCollectionPolicy,
            CollectedPublicMarket,
            quotes.QuoteCollectionPolicy,
            quotes.CollectedQuote,
            quotes.EndpointObservation,
            ExecutableQuote,
            candles.CandleCollectionPolicy,
            candles.FrameRequest,
            candles.CollectedCandles,
            candles.FrameCapture,
            candles.CandlePage,
            candles.CapturedCandle,
            aux.MarketAuxCollectionPolicy,
            aux.CollectedMarketAux,
            aux.AuxEndpointObservation,
            aux.CapturedOrderBook,
            aux.CapturedBookLevel,
            aux.CapturedOpenInterest,
            ws.WSCollectionPolicy,
            ws.CollectedWSReference,
            WSSubscriptionAck,
            WSTickerFrame,
        }
        if type(value) not in allowed:
            raise PublicMarketCollectionError("public_record_model_type_invalid")
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise PublicMarketCollectionError("public_record_fields_invalid")
        _raw_pins(value.__dict__)
        _edges(type(value), value)
        bounds = {
            candles.CandleCollectionPolicy: (("requests", 4, 4),),
            candles.CollectedCandles: (("frames", 4, 4), ("volume_units", 3, 3)),
            candles.FrameCapture: (("pages", 1, 12), ("confirmed", 200, 1024)),
            candles.CandlePage: (("parameters", 3, 4),),
            quotes.CollectedQuote: (("provenance", 3, 3),),
            quotes.EndpointObservation: (("parameters", 1, 2),),
            aux.CollectedMarketAux: (("provenance", 2, 2),),
            aux.AuxEndpointObservation: (("parameters", 2, 2),),
            aux.CapturedOrderBook: (("bids", 1, 5), ("asks", 1, 5)),
        }
        for name, minimum, maximum in bounds.get(type(value), ()):
            part = value.__dict__[name]
            if type(part) is not tuple or not minimum <= len(part) <= maximum:
                raise PublicMarketCollectionError("public_record_sequence_invalid")
        if type(value) in (
            candles.CandlePage,
            quotes.EndpointObservation,
            aux.AuxEndpointObservation,
        ):
            for pair in value.parameters:
                if (
                    type(pair) is not tuple
                    or len(pair) != 2
                    or any(type(item) is not str or len(item) > 64 for item in pair)
                ):
                    raise PublicMarketCollectionError(
                        "public_record_parameters_invalid"
                    )
        for part in value.__dict__.values():
            _guard(part, depth + 1, budget, json_input=json_input)
    elif type(value) is dict:
        if len(value) > 64 or any(
            type(key) is not str or len(key) > 64 for key in value
        ):
            raise PublicMarketCollectionError("public_record_mapping_invalid")
        _raw_pins(value)
        for part in value.values():
            _guard(part, depth + 1, budget, json_input=json_input)
    elif type(value) is tuple or (json_input and type(value) is list):
        if len(value) > 1024:
            raise PublicMarketCollectionError("public_record_sequence_invalid")
        for part in value:
            _guard(part, depth + 1, budget, json_input=json_input)
    elif type(value) in (str, bytes):
        budget[1] -= len(value)
        if len(value) > candles.MAX_RESPONSE_BYTES or budget[1] < 0:
            raise PublicMarketCollectionError("public_record_size_limit")
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 40
            or abs(value.as_tuple().exponent) > 20
        ):
            raise PublicMarketCollectionError("public_record_decimal_invalid")
    elif type(value) is datetime:
        quotes._utc(value)
    elif (
        value is None
        or type(value) is bool
        or (type(value) is int and abs(value) <= 10**19)
    ):
        pass
    else:
        raise PublicMarketCollectionError("public_record_value_invalid")


def _plain(value):
    if isinstance(value, BaseModel):
        return {key: _plain(item) for key, item in value.__dict__.items()}
    if type(value) is dict:
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    return value


def _digest(value):
    _guard(value)

    def serial(item):
        if isinstance(item, BaseModel):
            return {key: serial(part) for key, part in item.__dict__.items()}
        if type(item) is dict:
            return {key: serial(part) for key, part in item.items()}
        if type(item) is tuple:
            return [serial(part) for part in item]
        if type(item) is datetime:
            return quotes._utc(item).isoformat()
        if type(item) is Decimal:
            return str(item)
        if type(item) is bytes:
            return {"wire_bytes_hex": item.hex()}
        return item

    raw = json.dumps(
        serial(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(raw) > 32 * 1024 * 1024:
        raise PublicMarketCollectionError("public_record_size_limit")
    return hashlib.sha256(raw).hexdigest()


def _json_fields(cls, value):
    # A model-level Python boundary must not make strict JSON decimals, clocks
    # and tuples invalid. Decode each declared field through its real schema,
    # then re-enter strict Python validation. Python strings/lists remain denied.
    _guard(value, json_input=True)
    if type(value) is not dict:
        raise PublicMarketCollectionError("public_record_mapping_invalid")
    decoded = {}
    for name, item in value.items():
        field = cls.model_fields.get(name)
        decoded[name] = (
            item
            if field is None
            else TypeAdapter(field.rebuild_annotation()).validate_json(
                json.dumps(item, allow_nan=False), strict=True
            )
        )
    return cls.model_validate(_plain(decoded), strict=True)


class PublicMarketCollectionPolicy(QualificationModel):
    quote: quotes.QuoteCollectionPolicy
    candles: candles.CandleCollectionPolicy
    market_aux: aux.MarketAuxCollectionPolicy
    ws: ws.WSCollectionPolicy
    total_timeout_seconds: int = Field(default=30, ge=1, le=60)
    client_close_timeout_seconds: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="wrap")
    @classmethod
    def safe_python(cls, value, handler, info):
        if info.mode == "json":
            return _json_fields(cls, value)
        _guard(value)
        _edges(cls, value)
        return handler(value)


def _policy_copy(value):
    if type(value) is not PublicMarketCollectionPolicy:
        raise PublicMarketCollectionError("public_policy_invalid")
    _guard(value)
    return PublicMarketCollectionPolicy.model_validate(_plain(value), strict=True)


class CollectedPublicMarket(QualificationModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    report_id: ReportId
    instrument_id: candles.Instrument
    policy: PublicMarketCollectionPolicy
    quote: quotes.CollectedQuote
    candles: candles.CollectedCandles
    market_aux: aux.CollectedMarketAux
    ws: ws.CollectedWSReference
    started_at: datetime
    completed_at: datetime
    barrier_completed_at: datetime | None
    bundle_sha256: Digest
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    socket_binding_verified: Literal[False] = False
    complete_path_verified: Literal[False] = False
    execution_recheck_performed: Literal[False] = False
    qualification_performed: Literal[False] = False

    _times = field_validator("started_at", "completed_at")(quotes._utc)

    @field_validator("barrier_completed_at")
    @classmethod
    def barrier_time(cls, value):
        return None if value is None else quotes._utc(value)

    @field_validator(*_FLAGS, mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("public_capture_cannot_grant_authority")
        return value

    @model_validator(mode="wrap")
    @classmethod
    def safe_python(cls, value, handler, info):
        if info.mode == "json":
            return _json_fields(cls, value)
        _guard(value)
        _edges(cls, value)
        _validate_components(value.__dict__ if isinstance(value, BaseModel) else value)
        return handler(value)

    @model_validator(mode="after")
    def consistent(self):
        _guard(self)
        quotes.validate_collected_quote(self.quote)
        candles.validate_collected_candles(self.candles)
        aux.validate_collected_market_aux(self.market_aux)
        ws.validate_collected_ws_reference(self.ws)
        if self.started_at > self.completed_at:
            raise ValueError("public_batch_clock_reversed")
        if self.completed_at - self.started_at > timedelta(
            seconds=self.policy.total_timeout_seconds
        ):
            raise ValueError("public_batch_deadline_exceeded")
        if (
            self.barrier_completed_at is not None
            and self.started_at <= self.barrier_completed_at
        ):
            raise ValueError("public_publication_barrier_not_crossed")
        starts = (
            self.quote.provenance[0].request_started_at,
            self.candles.frames[0].pages[0].request_started_at,
            self.market_aux.provenance[0].request_started_at,
            self.ws.connection_started_at,
        )
        for name, start in zip(
            ("quote", "candles", "market_aux", "ws"), starts, strict=True
        ):
            component = getattr(self, name)
            identity = component.quote if name == "quote" else component
            if (
                identity.report_id != self.report_id
                or identity.instrument_id != self.instrument_id
            ):
                raise ValueError("public_component_identity_mismatch")
            if component.policy != getattr(self.policy, name):
                raise ValueError("public_component_policy_mismatch")
            if component.barrier_completed_at != self.barrier_completed_at:
                raise ValueError("public_component_barrier_mismatch")
            if start < self.started_at or component.completed_at > self.completed_at:
                raise ValueError("public_component_clock_outside_batch")
        for name in ("quote", "market_aux"):
            for item in getattr(self, name).provenance:
                if self.completed_at - item.source_time > timedelta(
                    seconds=getattr(self.policy, name).max_age_seconds
                ):
                    raise ValueError(f"public_{name}_stale_at_completion")
        if self.completed_at - self.ws.ticker.source_time > timedelta(
            seconds=self.policy.ws.max_age_seconds
        ):
            raise ValueError("public_ws_stale_at_completion")
        for frame in self.candles.frames:
            if frame.verified_through != candles._floor(
                self.completed_at, frame.timeframe
            ):
                raise ValueError("public_candle_tail_missing_at_completion")
        if self.bundle_sha256 != _digest(
            {key: item for key, item in self.__dict__.items() if key != "bundle_sha256"}
        ):
            raise ValueError("public_bundle_digest_mismatch")
        return self


def validate_collected_public_market(value) -> CollectedPublicMarket:
    """Replay all four byte records and cross-pins; hashes do not attest a socket."""
    try:
        if type(value) is not CollectedPublicMarket:
            raise PublicMarketCollectionError("public_record_model_type_invalid")
        _guard(value)
        _validate_components(value.__dict__)
        return CollectedPublicMarket.model_validate(_plain(value), strict=True)
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        raise PublicMarketCollectionError("public_record_invalid") from None


def _new_client():
    # This private factory is replaceable by MockTransport in unit tests only.
    return httpx.AsyncClient(trust_env=False, follow_redirects=False, auth=None)


async def _close_client(client, seconds, *, pending_cancel=False):
    async def close_once():
        async with asyncio.timeout(seconds):
            await client.aclose()

    task = asyncio.create_task(close_once())
    interrupted = pending_cancel
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:  # noqa: BLE001 -- cleanup details must not escape
            break
    failed = False
    try:
        task.result()
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- cleanup only
        failed = True
    if interrupted:
        raise asyncio.CancelledError
    if failed:
        raise PublicMarketCollectionError("public_client_cleanup_failed") from None


async def _http_capture(collector, close_seconds, **kwargs):
    client = _new_client()
    pending_cancel = False
    try:
        return await collector(client=client, **kwargs)
    except asyncio.CancelledError:
        pending_cancel = True
        raise
    finally:
        await _close_client(client, close_seconds, pending_cancel=pending_cancel)


def _component_failure(role, error):
    expected_type, allowed = {
        "quote": (quotes.QuoteCollectionError, _QUOTE_CODES),
        "candles": (candles.CandleCollectionError, _CANDLE_CODES),
        "market_aux": (aux.MarketAuxCollectionError, _AUX_CODES),
        "ws": (ws.WSCollectionError, _WS_CODES),
    }[role]
    if (
        type(error) is expected_type
        and type(error.args) is tuple
        and len(error.args) == 1
    ):
        candidate = error.args[0]
        if type(candidate) is str and candidate in allowed:
            return role, candidate
    return role, "component_failed"


async def _collect_components(policy, common):
    # Explicit ownership avoids TaskGroup's internal parent cancellation being
    # confused with caller cancellation during failure cleanup (Python 3.12).
    tasks = {}
    interrupted = False
    failed = False
    try:
        for name, collector in (
            ("quote", quotes.collect_executable_quote),
            ("candles", candles.collect_candles),
            ("market_aux", aux.collect_market_aux),
        ):
            tasks[name] = asyncio.create_task(
                _http_capture(
                    collector,
                    policy.client_close_timeout_seconds,
                    policy=getattr(policy, name),
                    **common,
                )
            )
        tasks["ws"] = asyncio.create_task(
            ws.collect_ws_reference(policy=policy.ws, **common)
        )
        pending = set(tasks.values())
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            if any(task.cancelled() or task.exception() is not None for task in done):
                failed = True
                break
    except asyncio.CancelledError:
        interrupted = True
        failed = True
    except Exception:  # noqa: BLE001 -- no source exception detail escapes
        failed = True
    finally:
        if failed:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
        joined = asyncio.gather(*tasks.values(), return_exceptions=True)
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                interrupted = True
                for task in tasks.values():
                    if not task.done():
                        task.cancel()
        outcomes = joined.result()
    if interrupted:
        raise asyncio.CancelledError
    if failed or any(isinstance(item, BaseException) for item in outcomes):
        failures = []
        for role, item in zip(tasks, outcomes, strict=True):
            if not isinstance(item, BaseException) or isinstance(
                item, asyncio.CancelledError
            ):
                continue
            # Never stringify unknown exceptions, frames, models or arbitrary
            # args. A cancelled sibling is cleanup, not another source failure.
            failures.append(_component_failure(role, item))
        raise PublicMarketCollectionError(
            "public_component_capture_failed",
            component_failures=tuple(failures),
        ) from None
    return dict(zip(tasks, outcomes, strict=True))


async def collect_public_market(
    *,
    clock: Callable[[], datetime],
    report_id: str,
    instrument_id: str,
    policy: PublicMarketCollectionPolicy,
    barrier_completed_at: datetime | None = None,
) -> CollectedPublicMarket:
    """One attempt; component failure cancels and joins siblings before returning.

    Cleanup has its own small bounded allowance after a total deadline. No packet
    is emitted if capture or cleanup fails, or if cleanup crosses the total limit.
    An injected clock is not independent evidence that these operations occurred.
    """
    try:
        policy = _policy_copy(policy)
        _raw_pins({"report_id": report_id, "instrument_id": instrument_id})
        TypeAdapter(ReportId).validate_python(report_id, strict=True)
        TypeAdapter(candles.Instrument).validate_python(instrument_id, strict=True)
        if not callable(clock):
            raise PublicMarketCollectionError("public_clock_invalid")
        barrier = (
            None if barrier_completed_at is None else quotes._utc(barrier_completed_at)
        )
        started = quotes._utc(clock())
        if barrier is not None and started <= barrier:
            raise PublicMarketCollectionError("public_publication_barrier_not_crossed")
        common = {
            "clock": clock,
            "report_id": report_id,
            "instrument_id": instrument_id,
            "barrier_completed_at": barrier,
        }
        async with asyncio.timeout(policy.total_timeout_seconds):
            components = await _collect_components(policy, common)
        values = dict(
            report_id=report_id,
            instrument_id=instrument_id,
            policy=policy,
            started_at=started,
            completed_at=quotes._utc(clock()),
            barrier_completed_at=barrier,
            **components,
            **dict.fromkeys(_FLAGS, False),
        )
        _guard(values)
        _validate_components(values)
        values["bundle_sha256"] = _digest(values)
        return validate_collected_public_market(
            CollectedPublicMarket.model_validate(_plain(values), strict=True)
        )
    except PublicMarketCollectionError:
        raise
    except TimeoutError:
        raise PublicMarketCollectionError("public_batch_timeout") from None
    except Exception:  # noqa: BLE001 -- safe error code, never transport payloads
        raise PublicMarketCollectionError("public_component_capture_failed") from None
