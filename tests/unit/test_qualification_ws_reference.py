"""Pure synthetic WS wire contracts; no socket, HTTP, account or live data.

Ticker source instants are milliseconds, not funding settlement or host receipt.
These bytes prove neither a real WS connection nor source authenticity. Parsing
old but causal records is allowed; freshness belongs to the enclosing evaluator.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError, create_model

from app.trade_qualification.ws_reference import (
    WSCaptureError,
    WSSubscriptionAck,
    WSTickerFrame,
    parse_ws_subscription_ack,
    parse_ws_ticker_frame,
    validate_ws_subscription_ack,
    validate_ws_ticker_frame,
)

D = Decimal
NOW = datetime(2026, 9, 12, 1, 10, tzinfo=UTC)
REPORT = "synthetic-ws-reference"
INSTRUMENT = "BTC-USDT-SWAP"
SUBSCRIPTION = "syntheticSub01"
CONNECTION = "syntheticConn01"
MAX_FRAME = 65536
FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "socket_binding_verified",
)
OPTIONAL_FIELDS = (
    "last",
    "lastSz",
    "open24h",
    "high24h",
    "low24h",
    "volCcy24h",
    "vol24h",
    "sodUtc0",
    "sodUtc8",
)
REQUIRED_ROW_FIELDS = ("instType", "instId", "bidPx", "askPx", "bidSz", "askSz", "ts")


def milliseconds(value):
    difference = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        difference.days * 86400000
        + difference.seconds * 1000
        + difference.microseconds // 1000
    )


def ticker_payload(instrument=INSTRUMENT):
    return {
        "arg": {"channel": "tickers", "instId": instrument},
        "data": [
            {
                "instType": "SWAP",
                "instId": instrument,
                "bidPx": "100.00",
                "askPx": "100.01",
                "bidSz": "2",
                "askSz": "3",
                "ts": milliseconds(NOW - timedelta(seconds=1)),
            }
        ],
    }


def ack_payload(instrument=INSTRUMENT):
    return {
        "id": SUBSCRIPTION,
        "event": "subscribe",
        "connId": CONNECTION,
        "arg": {"channel": "tickers", "instId": instrument},
    }


def payload(kind):
    return ticker_payload() if kind == "ticker" else ack_payload()


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse(kind, raw, **updates):
    arguments = {"instrument_id": INSTRUMENT, "received_at": NOW}
    if kind == "ticker":
        return parse_ws_ticker_frame(
            raw, **(arguments | {"report_id": REPORT} | updates)
        )
    return parse_ws_subscription_ack(
        raw, **(arguments | {"subscription_id": SUBSCRIPTION} | updates)
    )


def validate(kind, record):
    return (
        validate_ws_ticker_frame(record)
        if kind == "ticker"
        else validate_ws_subscription_ack(record)
    )


def reject(kind, raw, **updates):
    with pytest.raises(WSCaptureError) as raised:
        parse(kind, raw, **updates)
    assert raised.value.code == str(raised.value)
    assert re.fullmatch(r"ws_[a-z0-9_]{1,90}", raised.value.code)
    return raised.value.code


def extended(record):
    subclass = create_model(
        f"Untrusted{type(record).__name__}",
        __base__=type(record),
        hidden_declared=(bool, True),
    )
    return subclass.model_construct(**record.__dict__, hidden_declared=True)


@pytest.fixture(params=("ticker", "ack"))
def kind(request):
    return request.param


def assert_pins(record, raw):
    canonical = json.dumps(
        json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert record.raw_frame == raw
    assert record.frame_size_bytes == len(raw)
    assert record.frame_sha256 == hashlib.sha256(raw).hexdigest()
    assert record.canonical_json == canonical
    assert record.canonical_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert record.received_at.tzinfo is UTC
    assert all(getattr(record, flag) is False for flag in FLAGS)


def test_valid_wire_receipt_is_exact_frozen_json_roundtrippable_without_authority(kind):
    raw = wire(payload(kind))
    record = parse(kind, raw)
    assert_pins(record, raw)
    assert validate(kind, record) == record
    restored = type(record).model_validate_json(
        record.model_dump_json(round_trip=True), strict=True
    )
    assert restored == record
    assert validate(kind, restored) == record
    with pytest.raises(ValidationError):
        record.execution_authority = True
    if kind == "ticker":
        assert record.report_id == REPORT
        assert (record.bid, record.ask, record.bid_size, record.ask_size) == (
            D(100),
            D("100.01"),
            D(2),
            D(3),
        )
        assert record.size_unit == "contracts"
        assert record.source_time == NOW - timedelta(seconds=1)
        assert record.ts_raw == milliseconds(record.source_time)
        reference = record.reference
        assert reference.report_id == REPORT and reference.instrument_id == INSTRUMENT
        assert (
            reference.source == "ws"
            and reference.channel == "tickers"
            and reference.venue == "OKX"
        )
        assert (
            reference.bid,
            reference.ask,
            reference.source_time,
            reference.received_at,
        ) == (
            record.bid,
            record.ask,
            record.source_time,
            record.received_at,
        )
    else:
        assert record.subscription_id == SUBSCRIPTION and record.conn_id == CONNECTION


def test_semantic_json_order_and_whitespace_change_only_raw_pins(kind):
    value = payload(kind)
    first = parse(kind, wire(value))
    second_raw = json.dumps(value, sort_keys=True, indent=2).encode()
    second = parse(kind, second_raw)
    assert first.frame_sha256 != second.frame_sha256
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.canonical_json == second.canonical_json
    assert_pins(second, second_raw)


@pytest.mark.parametrize(
    "instrument",
    (
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
        "SOL-USDC-SWAP",
        "1000PEPE-USDT-SWAP",
        "A" * 16 + "-" + "B" * 16 + "-SWAP",
    ),
)
def test_instrument_is_a_strict_swap_identifier_not_a_two_coin_whitelist(
    kind, instrument
):
    value = ticker_payload(instrument) if kind == "ticker" else ack_payload(instrument)
    record = parse(kind, wire(value), instrument_id=instrument)
    assert record.instrument_id == instrument
    assert validate(kind, record) == record


@pytest.mark.parametrize(
    "instrument",
    (
        None,
        True,
        123,
        "",
        " btc-USDT-SWAP",
        "BTC-USDT-SWAP ",
        "BTC-usdt-SWAP",
        "比特幣-USDT-SWAP",
        "BTC-USDT",
        "BTC/USDT:USDT",
        "BTC-USDT-SPOT",
        "A" * 17 + "-USDT-SWAP",
        "BTC-" + "B" * 17 + "-SWAP",
        "ＢＴＣ-USDT-SWAP",
    ),
)
def test_invalid_or_normalized_instrument_input_is_not_accepted(kind, instrument):
    reject(kind, wire(payload(kind)), instrument_id=instrument)


@pytest.mark.parametrize("place", ("arg", "row"))
def test_each_ticker_instrument_identity_must_match_requested_identity(place):
    value = ticker_payload()
    target = value["arg"] if place == "arg" else value["data"][0]
    target["instId"] = "ETH-USDT-SWAP"
    reject("ticker", wire(value))


@pytest.mark.parametrize("field", REQUIRED_ROW_FIELDS)
def test_every_required_ticker_row_field_is_mandatory(field):
    value = ticker_payload()
    del value["data"][0][field]
    reject("ticker", wire(value))


@pytest.mark.parametrize("field", ("bidPx", "askPx", "bidSz", "askSz"))
@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        1,
        1.0,
        "",
        "0",
        "-0",
        "-1",
        "NaN",
        "Infinity",
        "1e2",
        "+1",
        " 1",
        "1 ",
        "01",
        ".1",
        "1.",
        "１",
        "١",
        "0.000000000000000000001",
        "100000000000000000000",
    ),
)
def test_required_decimal_wire_fields_are_exact_bounded_positive_ascii_strings(
    field, value
):
    body = ticker_payload()
    body["data"][0][field] = value
    reject("ticker", wire(body))


@pytest.mark.parametrize("prices", (("100", "100"), ("100.01", "100")))
def test_locked_or_crossed_ticker_book_is_not_a_ws_reference(prices):
    value = ticker_payload()
    value["data"][0].update(bidPx=prices[0], askPx=prices[1])
    reject("ticker", wire(value))


@pytest.mark.parametrize("field", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value", ("0", "1.2345"))
def test_known_optional_ticker_fields_preserve_valid_bounded_source_values(
    field, value
):
    body = ticker_payload()
    body["data"][0][field] = value
    raw = wire(body)
    frame = parse("ticker", raw)
    assert_pins(frame, raw)
    assert json.loads(frame.canonical_json)["data"][0][field] == value
    assert frame.reference.bid == D(100)


@pytest.mark.parametrize("field", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value", (None, "", "NaN", "1e2", "-1", 1))
def test_known_optional_fields_are_not_a_loose_metadata_escape(field, value):
    body = ticker_payload()
    body["data"][0][field] = value
    reject("ticker", wire(body))


@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        123,
        "",
        "0",
        "-1",
        "+1",
        "1.0",
        "1e12",
        "01",
        "１２３",
        "١٢٣",
        " 123",
        "123 ",
        "999999999999999",
        "1000000000000000",
    ),
)
def test_source_timestamp_is_an_exact_positive_ascii_millisecond_string(value):
    body = ticker_payload()
    body["data"][0]["ts"] = value
    reject("ticker", wire(body))


def test_milliseconds_are_decoded_exactly_and_old_causal_frames_are_not_freshness_approved():
    body = ticker_payload()
    body["data"][0]["ts"] = "1001"
    frame = parse("ticker", wire(body))
    assert frame.source_time == datetime(1970, 1, 1, 0, 0, 1, 1000, tzinfo=UTC)
    assert frame.received_at == NOW
    assert frame.socket_binding_verified is frame.source_authenticity_verified is False


@pytest.mark.parametrize("milliseconds_after", (0, 1))
def test_source_time_may_equal_but_never_follow_receipt(milliseconds_after):
    value = ticker_payload()
    value["data"][0]["ts"] = milliseconds(
        NOW + timedelta(milliseconds=milliseconds_after)
    )
    if milliseconds_after:
        reject("ticker", wire(value))
    else:
        assert parse("ticker", wire(value)).source_time == NOW


@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        "2026-09-12",
        123,
        datetime(2026, 9, 12, tzinfo=UTC).replace(tzinfo=None),
    ),
)
def test_receipt_requires_an_exact_timezone_aware_datetime(kind, value):
    reject(kind, wire(payload(kind)), received_at=value)


def test_equivalent_offset_and_hostile_decimal_context_do_not_change_the_receipt(kind):
    expected = parse(kind, wire(payload(kind)))
    context = Context(prec=2, Emin=-2, Emax=2)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    with localcontext(context):
        actual = parse(
            kind,
            wire(payload(kind)),
            received_at=NOW.astimezone(timezone(timedelta(hours=8))),
        )
        assert actual == expected
        assert validate(kind, actual) == expected


def test_dst_fold_is_compared_by_actual_instant_not_equal_local_clock():
    class SyntheticRepeatedHour(tzinfo):
        """Two explicit offsets exercise fold without external timezone data."""

        def utcoffset(self, value):
            return timedelta(hours=-4 if value is None or value.fold == 0 else -5)

        def dst(self, _value):
            return timedelta(0)

        def tzname(self, _value):
            return "synthetic-repeated-hour"

    zone = SyntheticRepeatedHour()
    earlier = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    later = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    value = ticker_payload()
    value["data"][0]["ts"] = milliseconds(later)
    reject("ticker", wire(value), received_at=earlier)
    value["data"][0]["ts"] = milliseconds(earlier)
    frame = parse("ticker", wire(value), received_at=later)
    assert frame.received_at - frame.source_time == timedelta(hours=1)


@pytest.mark.parametrize(
    "raw",
    (
        None,
        True,
        "{}",
        bytearray(b"{}"),
        memoryview(b"{}"),
        b"",
        b"ping",
        b"pong",
        b"null",
        b"[]",
        b"true",
        b"1",
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{",
        b"{}{}",
    ),
)
def test_raw_input_requires_one_exact_bounded_utf8_json_object(kind, raw):
    reject(kind, raw)


def test_raw_frame_limit_is_inclusive_and_precedes_json_parsing(kind):
    valid = wire(payload(kind))
    exact = valid + b" " * (MAX_FRAME - len(valid))
    record = parse(kind, exact)
    assert record.frame_size_bytes == MAX_FRAME
    assert record.raw_frame == exact
    reject(kind, exact + b" ")


@pytest.mark.parametrize(
    "raw",
    (
        b'{"arg":{},"arg":{}}',
        b'{"arg":{"channel":"tickers","channel":"tickers"}}',
        b'{"data":[{"bidPx":"1","bidPx":"1"}]}',
        b'{"id":"one","id":"two"}',
        b'{"data":NaN}',
        b'{"data":Infinity}',
        b'{"data":1e999}',
        b"[" * 2000 + b"0" + b"]" * 2000,
    ),
)
def test_duplicate_nonfinite_or_deep_json_never_escapes_as_an_untyped_error(kind, raw):
    reject(kind, raw)


@pytest.mark.parametrize("key", ("code", "msg", "connId", "event", "unknown"))
def test_ticker_top_level_does_not_accept_rest_envelopes_or_ack_fields(key):
    value = ticker_payload()
    value[key] = "0"
    reject("ticker", wire(value))


@pytest.mark.parametrize("value", (None, {}, [], [None], [1], [{}, {}]))
def test_ticker_data_is_exactly_one_object_not_empty_or_multiple(value):
    body = ticker_payload()
    body["data"] = value
    reject("ticker", wire(body))


@pytest.mark.parametrize("location", ("top", "arg", "row"))
def test_undeclared_fields_cannot_hide_inside_ticker_layers(location):
    value = ticker_payload()
    target = (
        value
        if location == "top"
        else value["arg"]
        if location == "arg"
        else value["data"][0]
    )
    target["socket_binding_verified"] = True
    reject("ticker", wire(value))


@pytest.mark.parametrize("value", ("SPOT", "swap", "SWAP ", "FUTURES", None, True))
def test_ticker_instrument_type_must_be_exact_swap(value):
    body = ticker_payload()
    body["data"][0]["instType"] = value
    reject("ticker", wire(body))


@pytest.mark.parametrize(
    "value", ("ticker", "mark-price", "funding-rate", "tickers ", None, True)
)
def test_subscription_channel_identity_is_exact(kind, value):
    body = payload(kind)
    body["arg"]["channel"] = value
    reject(kind, wire(body))


@pytest.mark.parametrize("field", ("id", "event", "arg", "connId"))
def test_each_ack_field_is_mandatory(field):
    body = ack_payload()
    del body[field]
    reject("ack", wire(body))


@pytest.mark.parametrize(
    "field", ("data", "code", "msg", "report_id", "execution_authority")
)
def test_ack_cannot_mix_push_rest_or_authority_fields(field):
    body = ack_payload()
    body[field] = "SYNTHETIC_NOT_AN_ACK_FIELD"
    reject("ack", wire(body))


@pytest.mark.parametrize(
    "value", ("unsubscribe", "error", "notice", "login", "subscribe ", None, True)
)
def test_ack_event_is_only_exact_subscription_success(value):
    body = ack_payload()
    body["event"] = value
    reject("ack", wire(body))


@pytest.mark.parametrize("field", ("id", "connId"))
@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        1,
        "",
        "has space",
        " leading",
        "trailing ",
        "dash-id",
        "under_score",
        "中文",
        "ＡＢＣ",
        "abc\n",
    ),
)
def test_ack_identifiers_are_exact_nonempty_ascii_alphanumeric(field, value):
    body = ack_payload()
    body[field] = value
    updates = {"subscription_id": value} if field == "id" else {}
    reject("ack", wire(body), **updates)


@pytest.mark.parametrize("field,maximum", (("id", 32), ("connId", 64)))
def test_ack_identifier_length_boundary_is_inclusive(field, maximum):
    body = ack_payload()
    body[field] = "A" * maximum
    arguments = {"subscription_id": body[field]} if field == "id" else {}
    record = parse("ack", wire(body), **arguments)
    assert (
        getattr(record, "subscription_id" if field == "id" else "conn_id")
        == body[field]
    )
    body[field] += "A"
    arguments = {"subscription_id": body[field]} if field == "id" else {}
    reject("ack", wire(body), **arguments)


def test_ack_subscription_and_instrument_must_match_the_actual_request():
    reject("ack", wire(ack_payload()), subscription_id="otherRequest")
    body = ack_payload("ETH-USDT-SWAP")
    reject("ack", wire(body))


@pytest.mark.parametrize(
    "value", (None, True, 42, "", "../report", "報告", " leading", "trailing ")
)
def test_report_identity_cannot_be_coerced_or_normalized(value):
    reject("ticker", wire(ticker_payload()), report_id=value)


@pytest.mark.parametrize(
    "field",
    (
        "raw_frame",
        "frame_size_bytes",
        "frame_sha256",
        "canonical_json",
        "canonical_sha256",
    ),
)
def test_every_wire_and_canonical_pin_is_recomputed(kind, field):
    raw = wire(payload(kind))
    record = parse(kind, raw)
    changed = {
        "raw_frame": raw + b" ",
        "frame_size_bytes": len(raw) + 1,
        "frame_sha256": "0" * 64,
        "canonical_json": record.canonical_json + " ",
        "canonical_sha256": "0" * 64,
    }[field]
    dirty = record.model_copy(update={field: changed})
    with pytest.raises(WSCaptureError):
        validate(kind, dirty)
    if kind == "ticker":
        with pytest.raises(WSCaptureError):
            _ = dirty.reference


def test_recomputed_hashes_do_not_hide_mismatch_between_wire_and_typed_fields(kind):
    record = parse(kind, wire(payload(kind)))
    changed = payload(kind)
    if kind == "ticker":
        changed["data"][0]["bidPx"] = "99.99"
    else:
        changed["connId"] = "DifferentConnection"
    raw = wire(changed)
    canonical = json.dumps(changed, sort_keys=True, separators=(",", ":"))
    dirty = record.model_copy(
        update={
            "raw_frame": raw,
            "frame_size_bytes": len(raw),
            "frame_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_json": canonical,
            "canonical_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        }
    )
    with pytest.raises(WSCaptureError):
        validate(kind, dirty)


@pytest.mark.parametrize(
    "field,value",
    (
        ("bid", D("99.99")),
        ("ask", D("100.02")),
        ("bid_size", D(4)),
        ("ask_size", D(4)),
        ("source_time", NOW),
        ("ts_raw", "1001"),
        ("size_unit", "base_currency"),
    ),
)
def test_reference_property_revalidates_each_derived_ticker_operand(field, value):
    frame = parse("ticker", wire(ticker_payload()))
    dirty = frame.model_copy(update={field: value})
    with pytest.raises(WSCaptureError):
        validate_ws_ticker_frame(dirty)
    with pytest.raises(WSCaptureError):
        _ = dirty.reference


@pytest.mark.parametrize(
    "field,value",
    (
        ("subscription_id", "anotherSubscription"),
        ("conn_id", "anotherConnection"),
        ("instrument_id", "ETH-USDT-SWAP"),
    ),
)
def test_ack_revalidation_binds_all_request_and_connection_identifiers(field, value):
    ack = parse("ack", wire(ack_payload()))
    with pytest.raises(WSCaptureError):
        validate_ws_subscription_ack(ack.model_copy(update={field: value}))


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("value", (True, 0, "false"))
def test_receipts_cannot_grant_or_coerce_any_authority_flag(kind, flag, value):
    record = parse(kind, wire(payload(kind)))
    dirty = record.model_copy(update={flag: value})
    with pytest.raises(WSCaptureError):
        validate(kind, dirty)
    python = record.model_dump(mode="python", round_trip=True)
    python[flag] = value
    with pytest.raises(ValidationError):
        type(record).model_validate(python, strict=True)


@pytest.mark.parametrize("change", ("hidden", "subclass", "raw_object"))
def test_dirty_receipt_is_rejected_before_any_untrusted_serialization(kind, change):
    class Unreadable:
        def __iter__(self):
            pytest.fail("Dirty receipt was iterated")

        def model_dump(self, *_args, **_kwargs):
            pytest.fail("Dirty receipt was serialized")

    record = parse(kind, wire(payload(kind)))
    if change == "subclass":
        dirty = extended(record)
    else:
        dirty = record.model_copy(
            update={"hidden" if change == "hidden" else "raw_frame": Unreadable()}
        )
    with pytest.raises(WSCaptureError):
        validate(kind, dirty)
    if kind == "ticker":
        with pytest.raises(WSCaptureError):
            _ = dirty.reference


@pytest.mark.parametrize("field", ("received_at", "raw_frame", "frame_size_bytes"))
def test_strict_json_conversion_does_not_enable_python_scalar_coercion(kind, field):
    record = parse(kind, wire(payload(kind)))
    python = record.model_dump(mode="python", round_trip=True)
    python[field] = {
        "received_at": record.received_at.isoformat(),
        "raw_frame": record.raw_frame.decode(),
        "frame_size_bytes": True,
    }[field]
    with pytest.raises(ValidationError):
        type(record).model_validate(python, strict=True)
    with pytest.raises(WSCaptureError):
        validate(kind, record.model_copy(update={field: python[field]}))


@pytest.mark.parametrize("field", ("bid", "ask", "bid_size", "ask_size"))
@pytest.mark.parametrize("value", (True, "1", D("NaN"), D("1e-21"), D("1e20")))
def test_ticker_derived_prices_and_contract_sizes_reject_model_copy_scalar_tamper(
    field, value
):
    frame = parse("ticker", wire(ticker_payload()))
    with pytest.raises(WSCaptureError):
        validate_ws_ticker_frame(frame.model_copy(update={field: value}))


def test_validation_functions_do_not_accept_each_others_receipt_shape():
    frame = parse("ticker", wire(ticker_payload()))
    ack = parse("ack", wire(ack_payload()))
    with pytest.raises(WSCaptureError):
        validate_ws_ticker_frame(ack)
    with pytest.raises(WSCaptureError):
        validate_ws_subscription_ack(frame)
    assert type(frame) is WSTickerFrame and type(ack) is WSSubscriptionAck


def test_error_codes_never_expose_opaque_frame_content(kind):
    body = payload(kind)
    body["synthetic_sensitive_marker"] = "SYNTHETIC_OPAQUE_VALUE_DO_NOT_ECHO"
    code = reject(kind, wire(body))
    assert "SYNTHETIC" not in code


@pytest.mark.parametrize(
    "field,value",
    (("source", "rest"), ("venue", "OtherVenue"), ("channel", "mark-price")),
)
def test_fixed_source_metadata_cannot_be_relabelled(kind, field, value):
    record = parse(kind, wire(payload(kind)))
    with pytest.raises(WSCaptureError):
        validate(kind, record.model_copy(update={field: value}))


@pytest.mark.parametrize(
    "field,value",
    (("inst_type", "SPOT"), ("timestamp_semantics", "host_receipt")),
)
def test_ticker_timestamp_and_instrument_semantics_cannot_be_relabelled(field, value):
    frame = parse("ticker", wire(ticker_payload()))
    with pytest.raises(WSCaptureError):
        _ = frame.model_copy(update={field: value}).reference


def test_ack_event_metadata_cannot_be_changed_after_parse():
    ack = parse("ack", wire(ack_payload()))
    with pytest.raises(WSCaptureError):
        validate_ws_subscription_ack(ack.model_copy(update={"event": "login"}))


@pytest.mark.parametrize(
    "bid,ask,size",
    (
        ("0.00000000000000000001", "0.00000000000000000002", "0.00000000000000000001"),
        (
            "99999999999999999998.99999999999999999999",
            "99999999999999999999.99999999999999999999",
            "99999999999999999999.99999999999999999999",
        ),
    ),
)
def test_supported_decimal_precision_boundaries_preserve_native_contract_sizes(
    bid, ask, size
):
    body = ticker_payload()
    body["data"][0].update(bidPx=bid, askPx=ask, bidSz=size, askSz=size)
    frame = parse("ticker", wire(body))
    assert frame.bid == D(bid) and frame.ask == D(ask)
    assert frame.bid_size == frame.ask_size == D(size)
    assert frame.size_unit == "contracts"
    assert validate_ws_ticker_frame(frame) == frame


def test_equal_numeric_value_does_not_erase_source_decimal_representation():
    frame = parse("ticker", wire(ticker_payload()))
    assert frame.bid == D(100)
    assert frame.bid.as_tuple() != D(100).as_tuple()
    with pytest.raises(WSCaptureError, match="ws_record_source_mismatch"):
        _ = frame.model_copy(update={"bid": D(100)}).reference


def test_bytes_and_datetime_subclasses_are_not_silently_normalized(kind):
    class DerivedBytes(bytes):
        pass

    class DerivedDatetime(datetime):
        pass

    raw = wire(payload(kind))
    reject(kind, DerivedBytes(raw))
    received = DerivedDatetime(2026, 9, 12, 1, 10, tzinfo=UTC)
    reject(kind, raw, received_at=received)
    record = parse(kind, raw)
    for field, value in (("raw_frame", DerivedBytes(raw)), ("received_at", received)):
        with pytest.raises(WSCaptureError):
            validate(kind, record.model_copy(update={field: value}))


@pytest.mark.parametrize("field", ("frame_sha256", "canonical_sha256"))
@pytest.mark.parametrize("side", ("leading", "trailing"))
def test_hashes_with_whitespace_cannot_be_repaired_by_model_json_or_helper(
    kind, field, side
):
    record = parse(kind, wire(payload(kind)))
    original = getattr(record, field)
    changed = " " + original if side == "leading" else original + " "
    dirty = record.model_copy(update={field: changed})
    with pytest.raises(WSCaptureError):
        validate(kind, dirty)
    with pytest.raises(ValidationError):
        type(record).model_validate(
            dirty.model_dump(mode="python", round_trip=True), strict=True
        )
    with pytest.raises(ValidationError):
        type(record).model_validate_json(
            dirty.model_dump_json(round_trip=True), strict=True
        )
