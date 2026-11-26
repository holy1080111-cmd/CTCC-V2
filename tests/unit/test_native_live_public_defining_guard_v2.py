"""Genuine source closure before any sole public-reader callback can run."""

import pytest

from app.okx_live import owned_qualification_v1 as q
from app.trade_qualification.native_continuation_guard_v1 import (
    NativeSourceClosureError,
)


def test_original_closed_public_graph_has_no_runtime_or_order_authority():
    before = (len(q._OWNED), len(q._OPENING), len(q._QUALIFIED), len(q.physical._GATES))
    assert q._NATIVE_GUARD() is None
    after = (len(q._OWNED), len(q._OPENING), len(q._QUALIFIED), len(q.physical._GATES))
    assert after == before


@pytest.mark.parametrize(
    "module,name",
    [
        (q.public.production, "_read_global_public_capture_audit_v2"),
        (q.public.production, "_read_global_raw_bank_v2"),
        (q.public.production, "_replay_packet"),
        (q.public.production, "_checked_packet"),
        (q.public_staged_source, "_take_prepared_candles"),
        (q.public.transport, "replay_public_runtime"),
        (q.public.transport, "_replay_captured_packet"),
        (q.public.transport, "_reopen_runtime_journal"),
        (q.public.journal, "replay_runtime_attempt"),
        (q.public.journal, "_replay_semantics"),
        (q.public.journal, "_packet_join"),
        (q.public.projection, "project_global_public_market_snapshot_v2"),
        (q.public.projection, "_project_global_market_v2"),
        (q.public, "_audit"),
        (q.public, "_copy_locally_replayed_candle_series"),
        (q.public, "_project"),
        (q.math, "_source"),
        (q.public.candles, "validate_collected_candles"),
        (q.public.auxiliary, "validate_collected_market_aux"),
        (q.public.websocket, "validate_collected_ws_reference"),
        (q.public.quote_wire, "_policy_preflight"),
        (q.public_ws_reference_source, "parse_ws_ticker_frame"),
        (q.public_parser_source, "parse_swap_ticker_v2"),
        (q.public_clock_source, "replay_clock_observation"),
        (q.public_receipt_source, "_root_context"),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_replaced_public_source_callback_is_denied_before_invocation(
    monkeypatch, module, name
):
    q._NATIVE_GUARD()
    called = []

    def foreign(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("foreign public callback executed")

    monkeypatch.setattr(module, name, foreign)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()
    assert not called


@pytest.mark.parametrize(
    "cls,name",
    [
        (q.public.production.GlobalPublicComponentPacketV1, "replay"),
        (q.public.production.GlobalPublicComponentPacketV1, "_replay_validated_fields"),
        (
            q.public.production.GlobalPublicComponentPacketV1,
            "_replay_component_bindings",
        ),
        (q.public.production.GlobalQuoteEvidenceV1, "replay"),
        (q.public.production._FrozenGlobalRawV2, "read"),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_changed_source_class_method_is_not_blessed_by_exact_dto_type(
    monkeypatch, cls, name
):
    q._NATIVE_GUARD()
    called = []

    def foreign(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("foreign source model callback executed")

    monkeypatch.setattr(cls, name, foreign)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()
    assert not called


@pytest.mark.parametrize(
    "body_name",
    [
        "_PUBLIC_OWNED_STAGE_BODY_SOURCE_V2",
        "_PUBLIC_WIRE_STAGE_BODY_SOURCE_V2",
        "_PUBLIC_JOURNAL_ATTEMPT_BODY_SOURCE_V2",
        "_PUBLIC_POSIX_ROOT_BODY_SOURCE_V2",
        "_PUBLIC_WINDOWS_ROOT_BODY_SOURCE_V2",
    ],
)
def test_hidden_context_body_code_change_is_denied_before_original_read(
    monkeypatch, body_name
):
    q._NATIVE_GUARD()
    original = getattr(q, body_name)

    def foreign(*_args, **_kwargs):
        raise AssertionError("foreign context body executed")

    assert not original.__closure__ and not foreign.__closure__
    monkeypatch.setattr(original, "__code__", foreign.__code__)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()


def test_prepared_candle_consumer_code_change_is_denied(monkeypatch):
    q._NATIVE_GUARD()
    original = q.public_staged_source._take_prepared_candles

    def foreign(_state):
        raise AssertionError("foreign prepared candle consumer executed")

    monkeypatch.setattr(original, "__code__", foreign.__code__)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()


@pytest.mark.parametrize(
    "module,name",
    [
        (q.public.production.GlobalPublicComponentPacketV1, "_replay_validated_fields"),
        (q.public.projection, "_project_global_market_v2"),
        (q.public, "_copy_locally_replayed_candle_series"),
        (q.math, "_source"),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_exact_replay_projection_code_change_is_denied(monkeypatch, module, name):
    q._NATIVE_GUARD()
    original = getattr(module, name)

    def foreign(*_args, **_kwargs):
        raise AssertionError("foreign raw replay projection executed")

    monkeypatch.setattr(original, "__code__", foreign.__code__)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()


@pytest.mark.parametrize(
    "cls",
    [
        q.public.production.GlobalPublicComponentPacketV1,
        q.public.candles.CollectedCandles,
        q.public.candles.FrameCapture,
        q.public.candles.CandlePage,
    ],
    ids=lambda value: value.__name__,
)
def test_changed_compiled_candle_validator_is_denied(monkeypatch, cls):
    q._NATIVE_GUARD()
    monkeypatch.setattr(cls, "__pydantic_validator__", object())
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()
