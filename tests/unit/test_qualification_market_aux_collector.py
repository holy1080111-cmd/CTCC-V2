"""Synthetic HTTPX transports only; these records do not attest real market IO."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import httpx
import pytest
from pydantic import ValidationError

import app.trade_qualification.market_aux_collector as aux

NOW = datetime(2026, 9, 12, 1, tzinfo=UTC)
INSTRUMENT = "BTC-USDT-SWAP"
POLICY = aux.MarketAuxCollectionPolicy(max_age_seconds=5)


def milliseconds(value=NOW):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


def payload(role):
    if role == "books":
        row = {
            "asks": [[str(101 + n), f"{n + 1}.125", "0", str(n + 1)] for n in range(5)],
            "bids": [[str(99 - n), f"{n + 1}.25", "0", str(n + 2)] for n in range(5)],
            "ts": milliseconds(),
            "seqId": 123456,
        }
    else:
        row = {
            "instType": "SWAP",
            "instId": INSTRUMENT,
            "oi": "5000.25",
            "oiCcy": "55.0025",
            "oiUsd": "5555.25",
            "ts": milliseconds(),
        }
    return {"code": "0", "msg": "", "data": [row]}


def wire(value):
    return json.dumps(value, ensure_ascii=False).encode()


class Clock:
    def __init__(self, moments=None):
        self.moments = moments or tuple(
            NOW + timedelta(milliseconds=10 * n) for n in range(7)
        )
        self.calls = 0

    def __call__(self):
        result = self.moments[self.calls]
        self.calls += 1
        return result


class Stream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.closed = False
        self.close_count = 0
        self.yielded = False

    async def __aiter__(self):
        self.yielded = True
        yield self.body[:11]
        yield self.body[11:]

    async def aclose(self):
        self.closed = True
        self.close_count += 1


async def capture(
    *,
    change=None,
    clock=None,
    policy=POLICY,
    barrier=None,
    client_options=None,
    instrument=INSTRUMENT,
    report="synthetic-market-aux",
    response_change=None,
    requests=None,
    streams=None,
):
    requests = [] if requests is None else requests
    streams = [] if streams is None else streams

    def handler(request):
        requests.append(request)
        role = "books" if request.url.path.endswith("/books") else "open_interest"
        value = payload(role)
        if change is not None:
            changed = change(role, value)
            if changed is not None:
                value = changed
        body = value if type(value) is bytes else wire(value)
        stream = Stream(body)
        streams.append(stream)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        status = 200
        if response_change is not None:
            status, headers = response_change(role, status, headers)
        return httpx.Response(status, headers=headers, stream=stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        **(client_options or {}),
    ) as client:
        try:
            result = await aux.collect_market_aux(
                client=client,
                clock=clock or Clock(),
                report_id=report,
                instrument_id=instrument,
                policy=policy,
                barrier_completed_at=barrier,
            )
            assert not client.is_closed
        finally:
            assert all(item.closed and item.close_count == 1 for item in streams)
    return result, requests


@pytest.mark.asyncio
async def test_exact_two_public_gets_and_preserved_units_and_source_semantics():
    result, requests = await capture(barrier=NOW - timedelta(microseconds=1))
    assert [(r.method, str(r.url)) for r in requests] == [
        ("GET", f"https://www.okx.com/api/v5/market/books?instId={INSTRUMENT}&sz=5"),
        (
            "GET",
            f"https://www.okx.com/api/v5/public/open-interest?instId={INSTRUMENT}&instType=SWAP",
        ),
    ]
    assert result.book.bids[0].price == Decimal(99)
    assert result.book.bids[0].size_contracts == Decimal("1.25")
    assert result.book.bids[0].order_count == 2
    assert result.book.bids[0].deprecated_liquidated_orders == 0
    assert result.book.seq_id == 123456
    assert result.book.identity_binding == "request"
    assert result.book.size_unit == "contracts"
    assert result.open_interest.contracts == Decimal("5000.25")
    assert result.open_interest.currency == Decimal("55.0025")
    assert result.open_interest.usd == Decimal("5555.25")
    assert result.open_interest.identity_binding == "request_and_response"
    assert result.book.generation_at == result.open_interest.returned_at == NOW
    assert [x.timestamp_semantics for x in result.provenance] == [
        "book_generation",
        "exchange_data_return",
    ]
    assert not result.execution_authority and not result.source_authenticity_verified
    for index, observation in enumerate(result.provenance):
        assert observation.request_started_at == NOW + timedelta(
            milliseconds=index * 30
        )
        assert observation.received_at == NOW + timedelta(milliseconds=index * 30 + 10)
        assert observation.completed_at == NOW + timedelta(milliseconds=index * 30 + 20)
        assert observation.request_started_at > result.barrier_completed_at
        assert (
            observation.body_sha256
            == hashlib.sha256(observation.response_body).hexdigest()
        )
        assert (
            observation.canonical_sha256
            == hashlib.sha256(observation.canonical_json.encode()).hexdigest()
        )
        assert observation.body_size_bytes == len(observation.response_body)
    # Book identity was not invented in its raw response.
    assert "instId" not in json.loads(result.provenance[0].response_body)["data"][0]


@pytest.mark.asyncio
async def test_roundtrip_determinism_and_mutable_domain_views_are_isolated():
    first, _ = await capture()
    second, _ = await capture()
    assert first == second
    assert first.bundle_sha256 == second.bundle_sha256
    rebuilt = aux.CollectedMarketAux.model_validate_json(
        first.model_dump_json(round_trip=True), strict=True
    )
    assert aux.validate_collected_market_aux(rebuilt) == first
    book = first.order_book
    book.bids[0].price = Decimal(1)
    book.asks.clear()
    book.instrument_id = "ETH-USDT-SWAP"
    assert first.order_book.bids[0].price == Decimal(99)
    assert len(first.order_book.asks) == 5
    assert first.order_book.instrument_id == INSTRUMENT
    with pytest.raises(ValidationError):
        first.book.bids[0].price = Decimal(1)
    assert isinstance(first.book.asks, tuple)


@pytest.mark.asyncio
async def test_optional_fields_remain_unknown_and_zero_interest_is_not_missing():
    def change(role, data):
        row = data["data"][0]
        if role == "books":
            del row["seqId"]
            row["bids"] = row["bids"][:1]
            row["asks"] = row["asks"][:1]
        else:
            del row["oiUsd"]
            row["oi"] = row["oiCcy"] = "0"

    result, _ = await capture(change=change)
    assert result.book.seq_id is None
    assert result.open_interest.usd is None
    assert result.open_interest.contracts == result.open_interest.currency == Decimal(0)


@pytest.mark.asyncio
async def test_optional_matching_book_response_identity_is_preserved_not_required():
    def change(role, data):
        if role == "books":
            data["data"][0].update(instId=INSTRUMENT, instType="SWAP")

    result, _ = await capture(change=change)
    assert result.book.identity_binding == "request"
    assert (
        json.loads(result.provenance[0].response_body)["data"][0]["instId"]
        == INSTRUMENT
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("precision", [6, 28, 80])
async def test_tiny_prices_are_not_rounded_by_ambient_decimal_context(precision):
    def change(role, data):
        if role == "books":
            row = data["data"][0]
            row["bids"] = [
                ["0.00000000000000000001", "0.00000000000000000001", "0", "1"]
            ]
            row["asks"] = [
                ["0.00000000000000000002", "1.12345678901234567890", "0", "1"]
            ]

    with localcontext() as context:
        context.prec = precision
        result, _ = await capture(change=change)
    assert result.book.asks[0].price == Decimal("0.00000000000000000002")
    assert result.book.asks[0].size_contracts == Decimal("1.12345678901234567890")
    assert aux.validate_collected_market_aux(result) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["price", "size"])
@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        1,
        1.0,
        "",
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "1e2",
        "+1",
        "01",
        " 1",
        "0",
        "-1",
        "1." + "1" * 21,
        "1" * 21,
    ],
)
async def test_book_numeric_lexemes_fail_closed(field, bad):
    def change(role, data):
        if role == "books":
            data["data"][0]["bids"][0][0 if field == "price" else 1] = bad

    requests = []
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change, requests=requests)
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["oi", "oiCcy", "oiUsd"])
@pytest.mark.parametrize(
    "bad", [None, True, 1.0, "", "NaN", "Infinity", "-1", "1e2", "01"]
)
async def test_interest_never_fills_invalid_fields(field, bad):
    def change(role, data):
        if role == "open_interest":
            data["data"][0][field] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["oi", "oiCcy", "instId", "instType", "ts"])
async def test_required_interest_fields_cannot_be_omitted(field):
    def change(role, data):
        if role == "open_interest":
            del data["data"][0][field]

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["books", "open_interest"])
@pytest.mark.parametrize(
    "field,bad", [("instId", "ETH-USDT-SWAP"), ("instType", "SPOT"), ("instId", None)]
)
async def test_conflicting_response_identity_rejected(role, field, bad):
    def change(current, data):
        if role == current:
            data["data"][0][field] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        [["1", "1", "0"]],
        [["1", "1", "0", "1", "extra"]],
        [["1", "1", "0", "1"]] * 6,
    ],
)
async def test_books_must_be_nonempty_four_column_bounded_lists(bad):
    def change(role, data):
        if role == "books":
            data["data"][0]["bids"] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, False, None, "1", "0.0", "", " 0"])
async def test_deprecated_column_cannot_become_rpi_quantity(bad):
    def change(role, data):
        if role == "books":
            data["data"][0]["bids"][0][2] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [0, 1, True, None, "0", "1.0", "-1", "01", "1e2", "1" * 20]
)
async def test_order_count_is_positive_bounded_integer_string(bad):
    def change(role, data):
        if role == "books":
            data["data"][0]["bids"][0][3] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, True, "1", 1.0, -1, 2**63])
async def test_supplied_sequence_id_must_be_exact_bounded_integer(bad):
    def change(role, data):
        if role == "books":
            data["data"][0]["seqId"] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "bids_reverse",
        "asks_reverse",
        "bids_duplicate",
        "asks_duplicate",
        "crossed",
        "locked",
    ],
)
async def test_book_ordering_and_geometry(case):
    def change(role, data):
        if role != "books":
            return
        row = data["data"][0]
        if case.endswith("reverse"):
            row[case.split("_")[0]].reverse()
        elif case.endswith("duplicate"):
            side = row[case.split("_")[0]]
            side[1][0] = side[0][0]
        else:
            row["bids"][0][0] = "102" if case == "crossed" else "101"

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"not-json",
        b'{"code":"0","code":"0"}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"\xff",
        b"[" * 20 + b"0" + b"]" * 20,
    ],
)
async def test_malformed_duplicate_nonfinite_and_deep_raw_json_rejected(bad):
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=lambda role, data: bad if role == "books" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "code",
        "msg",
        "zero_rows",
        "two_rows",
        "nonlist",
        "too_many_items",
        "long_string",
        "deep",
        "wide",
    ],
)
async def test_envelope_and_unknown_fields_still_bounded(case):
    def change(role, data):
        if role != "books":
            return
        if case == "code":
            data["code"] = "500"
        elif case == "msg":
            data["msg"] = None
        elif case == "zero_rows":
            data["data"] = []
        elif case == "two_rows":
            data["data"] *= 2
        elif case == "nonlist":
            data["data"] = {}
        elif case == "too_many_items":
            data["extra"] = [0] * 129
        elif case == "long_string":
            data["extra"] = "x" * 4097
        elif case == "deep":
            data["extra"] = [[[[[[[[[0]]]]]]]]]
        else:
            data["extra"] = {str(n): 0 for n in range(129)}

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["books", "open_interest"])
@pytest.mark.parametrize(
    "bad", [None, True, 1, "0", "1.5", "-1", " 1", "1e2", "9" * 16]
)
async def test_source_timestamp_lexeme(role, bad):
    def change(current, data):
        if role == current:
            data["data"][0]["ts"] = bad

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["books", "open_interest"])
@pytest.mark.parametrize("delta", [-6, 1])
async def test_component_stale_or_future_rejected_per_endpoint(role, delta):
    def change(current, data):
        if role == current:
            data["data"][0]["ts"] = milliseconds(NOW + timedelta(seconds=delta))

    requests = []
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(change=change, requests=requests)
    assert len(requests) == (1 if role == "books" else 2)


@pytest.mark.asyncio
async def test_stale_at_final_batch_clock_even_if_fresh_at_each_completion():
    moments = [NOW + timedelta(milliseconds=10 * n) for n in range(6)] + [
        NOW + timedelta(seconds=5, milliseconds=1)
    ]
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(
            clock=Clock(moments),
            policy=POLICY.model_copy(update={"batch_timeout_seconds": 10}),
        )


@pytest.mark.asyncio
async def test_age_boundary_inclusive_and_offset_equivalent_clock_normalizes_utc():
    moments = [NOW + timedelta(milliseconds=10 * n) for n in range(6)] + [
        NOW + timedelta(seconds=5)
    ]
    offset = timezone(timedelta(hours=8))
    result, _ = await capture(
        clock=Clock([x.astimezone(offset) for x in moments]),
        policy=POLICY.model_copy(update={"batch_timeout_seconds": 10}),
        barrier=(NOW - timedelta(microseconds=1)).astimezone(offset),
    )
    assert result.completed_at == NOW + timedelta(seconds=5)
    assert result.completed_at.tzinfo is UTC
    assert all(x.request_started_at.tzinfo is UTC for x in result.provenance)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 1])
async def test_barrier_not_crossed_before_first_request(offset):
    requests = []
    with pytest.raises(aux.MarketAuxCollectionError, match="publication_barrier"):
        await capture(barrier=NOW + timedelta(microseconds=offset), requests=requests)
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("index", range(7))
async def test_naive_clock_fails_and_closes_any_inflight_response(index):
    moments = [NOW + timedelta(milliseconds=10 * n) for n in range(7)]
    moments[index] = moments[index].replace(tzinfo=None)
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(clock=Clock(moments))


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [1, 2, 3, 4, 5, 6])
async def test_reversed_component_and_batch_clocks(index):
    moments = [NOW + timedelta(milliseconds=10 * n) for n in range(7)]
    moments[index] = NOW - timedelta(milliseconds=1)
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(clock=Clock(moments), barrier=NOW - timedelta(microseconds=1))


def resign(record):
    return record.model_copy(
        update={
            "bundle_sha256": aux._bundle_sha(
                record.report_id,
                record.instrument_id,
                record.book,
                record.open_interest,
                record.provenance,
                record.policy,
                record.barrier_completed_at,
                record.completed_at,
            )
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "/api/v5/public/open-interest"),
        ("role", "open_interest"),
        ("method", "POST"),
        ("origin", "https://example.invalid"),
        ("instrument_id", "ETH-USDT-SWAP"),
        ("timestamp_semantics", "exchange_data_return"),
        ("identity_binding", "request_and_response"),
        ("ts_raw", "1"),
        ("source_time", NOW - timedelta(seconds=1)),
        ("body_sha256", "0" * 64),
        ("canonical_sha256", "0" * 64),
        ("body_size_bytes", 1),
        ("canonical_json", "{}"),
        ("parameters", (("instId", INSTRUMENT), ("sz", "50"))),
    ],
)
async def test_raw_metadata_tampering_not_repaired_by_self_signed_bundle(field, value):
    result, _ = await capture()
    observation = result.provenance[0].model_copy(update={field: value})
    forged = resign(
        result.model_copy(update={"provenance": (observation, result.provenance[1])})
    )
    with pytest.raises(aux.MarketAuxCollectionError):
        aux.validate_collected_market_aux(forged)


@pytest.mark.asyncio
async def test_raw_wire_pin_and_derived_values_all_rebuilt_not_just_outer_hash():
    result, _ = await capture()
    original = result.provenance[0]
    raw = json.loads(original.response_body)
    raw["data"][0]["bids"][0][1] = "999"
    body = wire(raw)
    canonical = aux._canonical(raw)
    changed = original.model_copy(
        update={
            "response_body": body,
            "body_size_bytes": len(body),
            "body_sha256": aux._sha(body),
            "canonical_json": canonical,
            "canonical_sha256": aux._sha(canonical.encode()),
        }
    )
    forged = resign(
        result.model_copy(update={"provenance": (changed, result.provenance[1])})
    )
    with pytest.raises(aux.MarketAuxCollectionError):
        aux.validate_collected_market_aux(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_authority", True),
        ("source_authenticity_verified", True),
        ("execution_authority", 0),
        ("source_authenticity_verified", "false"),
        ("bundle_sha256", "0" * 64),
        ("instrument_id", "ETH-USDT-SWAP"),
    ],
)
async def test_authority_identity_and_digest_mutation_fails(field, value):
    result, _ = await capture()
    with pytest.raises(aux.MarketAuxCollectionError):
        aux.validate_collected_market_aux(result.model_copy(update={field: value}))


@pytest.mark.asyncio
async def test_both_request_barriers_revalidated_not_just_first_or_batch_time():
    result, _ = await capture(barrier=NOW - timedelta(microseconds=1))
    for index in (0, 1):
        items = list(result.provenance)
        items[index] = items[index].model_copy(
            update={"request_started_at": result.barrier_completed_at}
        )
        forged = resign(result.model_copy(update={"provenance": tuple(items)}))
        with pytest.raises(aux.MarketAuxCollectionError):
            aux.validate_collected_market_aux(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "nested_authority",
        "book_extra",
        "unknown_model",
        "list",
        "generator",
        "oversize_tuple",
        "oversize_raw",
        "invalid_price",
    ],
)
async def test_nested_dirty_copies_rejected_before_serialization(case):
    result, _ = await capture()
    if case == "nested_authority":
        forged = result.model_copy(
            update={
                "policy": result.policy.model_copy(update={"execution_authority": True})
            }
        )
    elif case == "book_extra":
        forged = result.model_copy(
            update={"book": result.book.model_copy(update={"extra": "bad"})}
        )
    elif case == "unknown_model":

        class StrangeBook(aux.CapturedOrderBook):
            pass

        forged = result.model_copy(
            update={"book": StrangeBook(**result.book.model_dump(mode="python"))}
        )
    elif case in ("list", "generator", "oversize_tuple"):
        values = list(result.provenance)
        values = (
            iter(values)
            if case == "generator"
            else tuple(values * 10)
            if case == "oversize_tuple"
            else values
        )
        forged = result.model_copy(update={"provenance": values})
    elif case == "oversize_raw":
        item = result.provenance[0].model_copy(update={"response_body": b"x" * 65537})
        forged = result.model_copy(update={"provenance": (item, result.provenance[1])})
    else:
        level = result.book.bids[0].model_copy(update={"price": Decimal("sNaN")})
        forged = result.model_copy(
            update={"book": result.book.model_copy(update={"bids": (level,)})}
        )
    with pytest.raises(aux.MarketAuxCollectionError):
        aux.validate_collected_market_aux(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["book", "open_interest", "policy", "provenance"])
async def test_json_unknown_nested_fields_cannot_claim_authority(field):
    result, _ = await capture()
    data = json.loads(result.model_dump_json())
    target = data[field][0] if field == "provenance" else data[field]
    target["execution_authority"] = True
    with pytest.raises(ValidationError):
        aux.CollectedMarketAux.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 400, 401, 429, 500])
async def test_no_redirect_or_retry_after_status_failure(status):
    requests = []
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(
            response_change=lambda role, old, headers: (
                status,
                {**headers, "Location": "https://example.invalid"},
            ),
            requests=requests,
        )
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,value",
    [
        ("content-type", "text/html"),
        ("content-encoding", "gzip"),
        ("content-length", "999999"),
        ("content-length", "-1"),
        ("content-length", "1.0"),
        ("content-length", "1"),
    ],
)
async def test_bad_response_headers_fail_and_close(header, value):
    def change(role, status, headers):
        return status, {
            key: item for key, item in headers.items() if key.lower() != header
        } | {header: value}

    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(response_change=change)


@pytest.mark.asyncio
async def test_streaming_limit_applies_without_declared_content_length():
    def change(role, status, headers):
        return status, {"Content-Type": "application/json"}

    with pytest.raises(aux.MarketAuxCollectionError, match="response_too_large"):
        await capture(
            change=lambda role, data: b"x" * 1025,
            policy=POLICY.model_copy(update={"max_response_bytes": 1024}),
            response_change=change,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"auth": ("not-a-real-user", "not-a-real-secret")},
        {"cookies": {"sid": "fictional"}},
        {"headers": {"OK-ACCESS-KEY": "fictional"}},
        {"headers": {"Authorization": "Bearer fictional"}},
        {"params": {"instId": "ETH-USDT-SWAP"}},
        {"event_hooks": {"request": [lambda request: None]}},
    ],
)
async def test_nonisolated_client_rejected_before_io(options):
    requests = []
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(client_options=options, requests=requests)
    assert requests == []


@pytest.mark.asyncio
async def test_received_cookies_are_not_resent_and_headers_are_explicit():
    result, requests = await capture(
        response_change=lambda role, status, headers: (
            status,
            {**headers, "Set-Cookie": "sid=fictional; Path=/; Secure"},
        )
    )
    assert result
    for request in requests:
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        assert "ok-access-key" not in request.headers
        assert request.headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["environment", "explicit_proxy", "transport_proxy", "mounted"]
)
async def test_known_proxy_and_transport_routing_rejected_without_network(case):
    calls = []
    mock = httpx.MockTransport(lambda request: calls.append(request))
    options = {"trust_env": False, "transport": mock}
    if case == "environment":
        options["trust_env"] = True
    elif case == "explicit_proxy":
        options["proxy"] = "http://example.invalid:8080"
    elif case == "transport_proxy":
        options["transport"] = httpx.AsyncHTTPTransport(
            proxy="http://example.invalid:8080"
        )
    else:
        options["mounts"] = {"https://": mock}
    async with httpx.AsyncClient(**options) as client:
        with pytest.raises(aux.MarketAuxCollectionError):
            await aux.collect_market_aux(
                client=client,
                clock=Clock(),
                report_id="synthetic",
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instrument",
    ["BTC-USDT", "BTC-USDT-SWAP?x=1", "../BTC", "btc-usdt-swap", "", None, True],
)
async def test_instrument_validation_precedes_io(instrument):
    requests = []
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(instrument=instrument, requests=requests)
    assert requests == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_age_seconds", 0),
        ("max_age_seconds", 61),
        ("max_age_seconds", True),
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", 6),
        ("batch_timeout_seconds", 16),
        ("max_response_bytes", 1023),
        ("max_response_bytes", 65537),
    ],
)
def test_policy_is_strict_and_bounded(field, value):
    with pytest.raises(ValidationError):
        aux.MarketAuxCollectionPolicy(**({"max_age_seconds": 5} | {field: value}))


class BlockingStream(Stream):
    def __init__(self, *, fail_close=False, wait_close=False):
        super().__init__(b"")
        self.entered = asyncio.Event()
        self.closing = asyncio.Event()
        self.release_close = asyncio.Event()
        self.fail_close = fail_close
        self.wait_close = wait_close

    async def __aiter__(self):
        self.entered.set()
        await asyncio.Event().wait()
        yield b"unreachable"

    async def aclose(self):
        self.close_count += 1
        self.closing.set()
        if self.wait_close:
            await self.release_close.wait()
        self.closed = True
        if self.fail_close:
            raise RuntimeError("synthetic cleanup failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_close", [False, True])
async def test_cancellation_closes_once_and_preserves_cancellation_even_if_close_fails(
    fail_close,
):
    stream = BlockingStream(fail_close=fail_close)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=stream
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        task = asyncio.create_task(
            aux.collect_market_aux(
                client=client,
                clock=Clock(),
                report_id="synthetic",
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
        )
        await asyncio.wait_for(stream.entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed and stream.close_count == 1
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_cancellation_waits_for_single_shielded_cleanup():
    stream = BlockingStream(wait_close=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/json"}, stream=stream
            )
        ),
        trust_env=False,
    ) as client:
        task = asyncio.create_task(
            aux.collect_market_aux(
                client=client,
                clock=Clock(),
                report_id="synthetic",
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
        )
        await asyncio.wait_for(stream.entered.wait(), 1)
        task.cancel()
        await asyncio.wait_for(stream.closing.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        stream.release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed and stream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["request", "batch"])
async def test_deadline_cleanup_has_no_second_request(kind):
    stream = BlockingStream()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=stream
        )

    policy = aux.MarketAuxCollectionPolicy(
        max_age_seconds=5,
        request_timeout_seconds=1 if kind == "request" else 2,
        batch_timeout_seconds=1 if kind == "batch" else 4,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        with pytest.raises(aux.MarketAuxCollectionError, match="transport_failed"):
            await aux.collect_market_aux(
                client=client,
                clock=Clock(),
                report_id="synthetic",
                instrument_id=INSTRUMENT,
                policy=policy,
            )
    assert stream.closed and stream.close_count == 1 and len(requests) == 1


@pytest.mark.asyncio
async def test_transport_exception_redacted_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadError("sensitive synthetic response detail")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        with pytest.raises(
            aux.MarketAuxCollectionError, match="^public_market_aux_transport_failed$"
        ):
            await aux.collect_market_aux(
                client=client,
                clock=Clock(),
                report_id="synthetic",
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_injected_http_transport_retries_rejected_before_connect():
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(retries=2), trust_env=False
    ) as client:
        with pytest.raises(aux.MarketAuxCollectionError, match="retries_forbidden"):
            await aux.collect_market_aux(
                client=client,
                clock=Clock(),
                report_id="synthetic",
                instrument_id=INSTRUMENT,
                policy=POLICY,
            )


@pytest.mark.asyncio
async def test_plain_http_transport_preflight_only_no_network():
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(retries=0), trust_env=False
    ) as client:
        aux._isolated_client(client)


@pytest.mark.asyncio
async def test_canonical_equal_bodies_still_require_wire_pin():
    result, _ = await capture()
    original = result.provenance[0]
    changed = original.model_copy(
        update={"response_body": b" " + original.response_body}
    )
    forged = resign(
        result.model_copy(update={"provenance": (changed, result.provenance[1])})
    )
    assert aux._parse(changed.response_body)[1] == original.canonical_json
    with pytest.raises(aux.MarketAuxCollectionError):
        aux.validate_collected_market_aux(forged)


@pytest.mark.asyncio
async def test_unknown_bounded_response_metadata_is_preserved_in_canonical_audit():
    def change(role, data):
        data["data"][0]["futureMetadata"] = {
            "unicode": "原始",
            "observations": [1, "2", None],
        }

    result, _ = await capture(change=change)
    for observation in result.provenance:
        assert (
            json.loads(observation.canonical_json)["data"][0]["futureMetadata"][
                "unicode"
            ]
            == "原始"
        )
    assert (
        aux.CollectedMarketAux.model_validate_json(
            result.model_dump_json(), strict=True
        )
        == result
    )


@pytest.mark.asyncio
async def test_request_policy_elapsed_deadline_and_final_clock_bound():
    moments = [
        NOW,
        NOW + timedelta(milliseconds=1),
        NOW + timedelta(seconds=2, microseconds=1),
    ]
    requests = []
    with pytest.raises(aux.MarketAuxCollectionError, match="request_deadline"):
        await capture(clock=Clock(moments), requests=requests)
    assert len(requests) == 1
    moments = [NOW + timedelta(milliseconds=10 * n) for n in range(6)] + [
        NOW + timedelta(seconds=4, microseconds=1)
    ]
    with pytest.raises(aux.MarketAuxCollectionError):
        await capture(clock=Clock(moments))


@pytest.mark.asyncio
async def test_component_order_and_count_are_rechecked():
    result, _ = await capture()
    for observations in (
        result.provenance[::-1],
        result.provenance[:1],
        (result.provenance[0],) * 2,
    ):
        forged = resign(result.model_copy(update={"provenance": observations}))
        with pytest.raises(aux.MarketAuxCollectionError):
            aux.validate_collected_market_aux(forged)


@pytest.mark.asyncio
async def test_stored_body_limit_cannot_be_lowered_after_collection():
    def change(role, data):
        data["extra"] = "x" * 1024

    result, _ = await capture(change=change)
    forged = resign(
        result.model_copy(
            update={
                "policy": result.policy.model_copy(update={"max_response_bytes": 1024})
            }
        )
    )
    with pytest.raises(aux.MarketAuxCollectionError):
        aux.validate_collected_market_aux(forged)
