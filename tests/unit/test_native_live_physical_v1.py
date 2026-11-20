"""Synthetic physical boundary contracts; zero venue calls, keys or Live Arm."""

import asyncio
import copy
import gc
import sys
import weakref
from decimal import Decimal
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from app.domain.source_primitives import canonical, sha
from app.exchange.okx import native_live_transport_v1 as transport
from app.exchange.okx import native_live_write_v1 as subject
from app.okx_live import trusted_source_bridge_v1 as source
from tests.unit.test_native_live_source_ledger_v1 import _value


def _request_record():
    from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
    from app.domain.source_primitives import decode

    value = _value()
    _, raw, _ = exact_live_intent_document_v1(value)
    body = decode(raw, maximum=65536)["request_json"].encode("ascii")
    headers = {
        "Host": "openapi.okx.com",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "User-Agent": "synthetic",
        "OK-ACCESS-KEY": "synthetic-key",
        "OK-ACCESS-SIGN": "synthetic-signature",
        "OK-ACCESS-TIMESTAMP": "2026-11-20T00:00:00.000Z",
        "OK-ACCESS-PASSPHRASE": "synthetic-passphrase",
        "expTime": "1795132800000",
    }
    facts = SimpleNamespace(request_json=body, request_sha256=sha(body))
    record = {"facts": facts, "origin": "https://openapi.okx.com", "wire": b""}
    return record, httpx.Request(
        "POST", record["origin"] + subject._ORDER_PATH, headers=headers, content=body
    )


def test_live_exact_wire_has_fok_bound_and_never_simulated_trading_header():
    record, request = _request_record()
    subject._bind_request(record, request)
    assert record["wire"].startswith(b"POST /api/v5/trade/order HTTP/1.1\r\n")
    assert record["wire"].endswith(record["facts"].request_json)
    assert b"simulated" not in record["wire"]
    assert (
        b'"ordType":"fok"' in record["wire"]
        and b'"tdMode":"isolated"' in record["wire"]
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "demo_header",
        "extra_header",
        "duplicate_header",
        "body",
        "host",
        "path",
        "method",
    ),
)
def test_live_wire_refuses_header_body_route_or_method_substitution(mutation):
    record, request = _request_record()
    headers = list(request.headers.raw)
    body = request.content
    url = str(request.url)
    method = "POST"
    if mutation == "demo_header":
        headers.append((b"x-simulated-trading", b"1"))
    elif mutation == "extra_header":
        headers.append((b"proxy-authorization", b"synthetic"))
    elif mutation == "duplicate_header":
        headers.append((b"OK-ACCESS-KEY", b"other"))
    elif mutation == "body":
        body = b"{}"
    elif mutation == "host":
        url = "https://www.okx.com" + subject._ORDER_PATH
    elif mutation == "path":
        url = "https://openapi.okx.com/api/v5/trade/close-position"
    elif mutation == "method":
        method = "GET"
    changed = httpx.Request(method, url, headers=headers, content=body)
    with pytest.raises(subject.NativeLiveWriteError):
        subject._bind_request(record, changed)


@pytest.mark.asyncio
async def test_missing_original_coordinator_refuses_before_any_client_database_or_dns(
    monkeypatch,
):
    monkeypatch.setitem(sys.modules, subject._COORDINATOR, None)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: pytest.fail("HTTP client created")
    )
    monkeypatch.setattr(
        subject.ledger, "_factory", lambda *a: pytest.fail("database selected")
    )
    with pytest.raises(
        subject.NativeLiveWriteError,
        match="^native_live_physical_attempt_unresolved_do_not_retry$",
    ):
        await subject.submit_owned_live_fok_v1(SimpleNamespace(passed=True))


@pytest.mark.asyncio
async def test_saved_facts_class_or_callback_flag_is_not_an_issuer(monkeypatch):
    fake = ModuleType(subject._COORDINATOR)
    fake.LiveQualifiedExecutionFactsV1 = type("LiveQualifiedExecutionFactsV1", (), {})
    for name in subject._API:
        setattr(fake, name, lambda *a, **k: True)
    monkeypatch.setitem(sys.modules, subject._COORDINATOR, fake)
    with pytest.raises(subject.NativeLiveWriteError):
        await subject.submit_owned_live_fok_v1(object())


def _micro_view(**changes):
    spec = {
        "instId": "ETH-USDT-SWAP",
        "instType": "SWAP",
        "ctType": "linear",
        "settleCcy": "USDT",
        "state": "live",
        "ctVal": "0.1",
        "ctValCcy": "ETH",
        "lotSz": "0.01",
        "minSz": "0.01",
        "maxLmtSz": "100",
        "tickSz": "0.1",
    }
    spec.update(changes)
    leverage = {
        "instId": "ETH-USDT-SWAP",
        "mgnMode": "isolated",
        "posSide": "net",
        "lever": "1",
    }
    return {
        "current_pages": (
            SimpleNamespace(
                selector=source.current.select_live_account_raw_page_v1(
                    "account_instruments", instrument_id="ETH-USDT-SWAP"
                ),
                raw_body=canonical({"code": "0", "data": [spec]}),
            ),
            SimpleNamespace(
                selector=source.current.select_live_account_raw_page_v1(
                    "leverage_isolated", leverage_instrument_ids=("ETH-USDT-SWAP",)
                ),
                raw_body=canonical({"code": "0", "data": [leverage]}),
            ),
        )
    }


def test_micro_caps_recalculate_source_contract_notional_lot_tick_stop_and_exact_leverage():
    value = _value(notional_amount=Decimal("10.01"), margin_amount=Decimal("10.01"))
    subject._hard_micro(value, _micro_view())
    assert subject.MICRO_NOTIONAL_HARD_CAP_USDT == 25
    assert subject.MICRO_STOP_RISK_HARD_CAP_USDT == Decimal("0.25")


@pytest.mark.parametrize(
    "changes",
    (
        {"notional_amount": Decimal(26)},
        {"risk_samples": (Decimal("0.251"),)},
        {"margin_amount": Decimal(1)},
        {"contracts": Decimal("0.015")},
        {"executable_price_bound": Decimal("100.01")},
        {"notional_amount": Decimal(10)},
        {"leverage": 2},
    ),
)
def test_micro_caps_never_increase_budget_or_repair_changed_execution(changes):
    value = _value(notional_amount=Decimal("10.01"), margin_amount=Decimal("10.01"))
    value = value.model_copy(update=changes)
    with pytest.raises(subject.NativeLiveWriteError):
        subject._hard_micro(value, _micro_view())


@pytest.mark.parametrize(
    "field,changed",
    (
        ("ctValCcy", "UNKNOWN"),
        ("ctType", "inverse"),
        ("settleCcy", "BTC"),
        ("maxLmtSz", ""),
        ("lotSz", "0"),
        ("ctVal", "NaN"),
        ("state", "suspend"),
    ),
)
def test_unknown_or_changed_instrument_metadata_cannot_become_a_micro_trade(
    field, changed
):
    with pytest.raises(subject.NativeLiveWriteError):
        subject._hard_micro(
            _value(notional_amount=Decimal("10.01"), margin_amount=Decimal("10.01")),
            _micro_view(**{field: changed}),
        )


def test_physical_gate_cannot_be_constructed_copied_or_replayed_as_a_dto():
    with pytest.raises(subject.NativeLiveWriteError):
        subject._PhysicalLiveWriteGateV1(key=object())
    forged = object.__new__(subject._PhysicalLiveWriteGateV1)
    with pytest.raises(subject.NativeLiveWriteError):
        copy.copy(forged)


@pytest.mark.asyncio
async def test_even_private_constructor_key_without_original_held_session_cannot_open_transport():
    gate = subject._PhysicalLiveWriteGateV1(key=subject._GATE_KEY)
    with pytest.raises(subject.NativeLiveWriteError):
        transport._create_transport_v1(gate, key=subject._INTENT_KEY)
    with pytest.raises(subject.NativeLiveWriteError):
        subject._require_live_source_access_v1(
            object(), key=subject._CREDENTIAL_SOURCE_KEY_V1
        )
    with pytest.raises(source.LiveSourceBridgeError):
        source._owned_live_credential_material_v1(object(), key=object())


def test_transport_vendor_mutation_is_detected_without_sending(monkeypatch):
    subject._require_vendor_state_v1()
    original = transport.AnyIOBackend.connect_tcp

    async def fake(self, *a, **k):
        raise AssertionError("must not run")

    monkeypatch.setattr(transport.AnyIOBackend, "connect_tcp", fake)
    with pytest.raises(
        subject.NativeLiveWriteError, match="native_live_transport_vendor_changed"
    ):
        subject._require_vendor_state_v1()
    monkeypatch.setattr(transport.AnyIOBackend, "connect_tcp", original)
    subject._require_vendor_state_v1()


def test_inplace_vendor_bytecode_mutation_is_also_detected(monkeypatch):
    original = next(
        row[0]
        for row in transport._VENDOR_CODE_BINDINGS_V1
        if not row[0].__code__.co_freevars
    )

    async def fake(self, seconds):
        return None

    monkeypatch.setattr(original, "__code__", fake.__code__)
    with pytest.raises(
        subject.NativeLiveWriteError, match="native_live_transport_vendor_changed"
    ):
        subject._require_vendor_state_v1()


def test_late_failure_keeps_completed_received_journal_and_has_no_order_authority():
    journal = transport._ResponseDataJournalV1(body_limit=65536)
    raw = canonical(
        {
            "code": "0",
            "data": [{"ordId": "123", "clOrdId": "CTCCLsynthetic", "sCode": "0"}],
        }
    )
    journal.retain_headers(
        200,
        (
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ),
    )
    journal.retain_body(raw)
    journal.retain_eof()
    from app.domain.source_primitives import ClockStamp

    stamps = [
        ClockStamp.model_validate(
            {"utc_ns": 1_795_132_800_000_000_000 + i, "monotonic_ns": 100 + i}
        )
        for i in range(3)
    ]
    record = {
        "received": journal,
        "attempted": True,
        "accepted": 1,
        "wire": b"x",
        "tls_certificate_sha256": "a" * 64,
        "started_at": stamps[0],
        "headers_at": stamps[1],
        "completed_at": stamps[2],
        "private_tokens": ("synthetic-private-key-not-in-response",),
    }
    subject._validate_known_response_data(record)
    journal.retain_fault("close")
    assert (
        record["response_checked"] is True
        and journal.body_raw == raw
        and journal.body_complete is True
    )
    assert journal.faults == ["close"]


def test_response_without_actual_native_write_cannot_be_attested_as_exchange_data():
    journal = transport._ResponseDataJournalV1(body_limit=65536)
    journal.retain_headers(200, ((b"content-type", b"application/json"),))
    journal.retain_body(b'{"code":"0","data":[]}')
    journal.retain_eof()
    with pytest.raises(subject.NativeLiveWriteError):
        subject._validate_known_response_data({"received": journal, "attempted": False})


@pytest.mark.asyncio
async def test_cancellation_is_preserved_and_never_converted_into_retryable_error(
    monkeypatch,
):
    cancelled = asyncio.CancelledError()

    def cancelled_source():
        raise cancelled

    monkeypatch.setattr(subject, "_coordinator_api", cancelled_source)
    with pytest.raises(asyncio.CancelledError) as caught:
        await subject.submit_owned_live_fok_v1(object())
    assert caught.value is cancelled
    assert caught.value._owned_live_response_v1 is None


@pytest.mark.asyncio
async def test_cancelled_negative_control_stays_tracked_until_independent_readback(
    monkeypatch,
):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def actual_negative(facts, *, stop):
        assert facts is marker and stop is True
        calls.append("negative_only")
        started.set()
        await finish.wait()
        calls.append("independent_readback")

    marker, record = object(), {}
    monkeypatch.setattr(subject, "_negative_control", actual_negative)
    caller = asyncio.create_task(
        subject._settle_negative_control(marker, record, stop=True)
    )
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert len(subject._NEGATIVE_TASKS) == 1
    assert record["negative_control_pending"] is True
    finish.set()
    await asyncio.gather(*tuple(subject._NEGATIVE_TASKS))
    await asyncio.sleep(0)
    assert not subject._NEGATIVE_TASKS
    assert record["negative_control_pending"] is False
    assert calls == ["negative_only", "independent_readback"]


@pytest.mark.asyncio
async def test_failed_negative_control_remains_unconfirmed_without_retry(monkeypatch):
    calls = []

    async def refused(facts, *, stop):
        calls.append(stop)
        raise RuntimeError("synthetic_readback_failure")

    monkeypatch.setattr(subject, "_negative_control", refused)
    record = {}
    with pytest.raises(RuntimeError, match="synthetic_readback_failure"):
        await subject._settle_negative_control(object(), record, stop=True)
    assert calls == [True] and not subject._NEGATIVE_TASKS
    assert record["durable_inhibit_unconfirmed"] is True
    assert record["negative_control_pending"] is False


def test_retained_response_registry_does_not_keep_dead_handles_or_credentials_alive():
    gate = object.__new__(subject._PhysicalLiveWriteGateV1)
    observed = weakref.ref(gate)
    subject._GATES[gate] = {"gate": weakref.ref(gate)}
    before = len(subject._GATES)
    del gate
    gc.collect()
    assert observed() is None and len(subject._GATES) == before - 1
