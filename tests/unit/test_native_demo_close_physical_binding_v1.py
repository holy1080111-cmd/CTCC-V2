"""Real request-byte binding primitives, NOT native Source/DB permissions.

The extracted helper intentionally has foreign globals and cannot pass the
actual native guard. It exercises the SAME HMAC/h11 request contract without
ever opening a socket, invoking an SDK write or manufacturing a genuine cap.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.domain.source_primitives import canonical
from app.exchange.okx import native_demo_write_v2 as write
from app.exchange.okx import private_rest
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_controlled_close_v1 as close
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def primitive(side, position_side):
    at = datetime(2026, 11, 3, tzinfo=UTC)
    payload = {
        "instId": "BTC-USDT-SWAP",
        "tdMode": "isolated",
        "ordType": "market",
        "side": side,
        "posSide": position_side,
        "sz": "1",
        "clOrdId": "CTC" + "a" * 29,
    }
    if position_side == "net":
        payload["reduceOnly"] = True
    raw = canonical(payload)
    client = SimpleNamespace(
        _credentials=lambda: ("unit-key", "unit-secret", "unit-pass"),
        _user_agent=lambda: "ctcc-unit",
    )
    context = SimpleNamespace(origin="https://www.okx.com", client=client)
    facts = SimpleNamespace(
        exact_order_json=raw, exact_order_sha256=write.hashlib.sha256(raw).hexdigest()
    )
    function = foreign_primitive_v1(
        write._bind_request,
        {
            "_live_facts": lambda record: (
                facts,
                context,
                at + timedelta(milliseconds=2),
            )
        },
    )
    record = {"opened_at": at, "phase": "locked", "request": None}
    return function, record, payload, raw


def prepared(payload):
    raw = canonical(payload)
    timestamp = private_rest.utc_iso_timestamp(datetime(2026, 11, 3, tzinfo=UTC))
    signature = private_rest.build_signature(
        timestamp=timestamp,
        method="POST",
        request_path="/api/v5/trade/order",
        body=raw.decode(),
        secret="unit-secret",
    )
    return httpx.Request(
        "POST",
        "https://www.okx.com/api/v5/trade/order",
        content=raw,
        headers={
            "connection": "close",
            "content-type": "application/json",
            "accept": "application/json",
            "accept-encoding": "identity",
            "user-agent": "ctcc-unit",
            "ok-access-key": "unit-key",
            "ok-access-sign": signature,
            "ok-access-passphrase": "unit-pass",
            "ok-access-timestamp": timestamp,
            "x-simulated-trading": "1",
        },
    )


@pytest.mark.parametrize(
    "side,position_side",
    (("sell", "net"), ("buy", "net"), ("sell", "long"), ("buy", "short")),
)
def test_exact_fixed_close_bytes_bind_and_are_not_a_native_permit(side, position_side):
    function, record, payload, raw = primitive(side, position_side)
    request = prepared(payload)
    function(record, request)
    assert record["request"] is request and record["phase"] == "prepared"
    assert record["wire"].endswith(raw)
    assert record["actual_post_signature"] == request.headers["ok-access-sign"]
    with pytest.raises(ValueError):
        guard._function_pin(function, {})


@pytest.mark.parametrize(
    "field,value",
    (
        ("sz", "2"),
        ("side", "buy"),
        ("posSide", "long"),
        ("ordType", "fok"),
        ("clOrdId", "another"),
        ("reduceOnly", False),
    ),
)
def test_resigned_changed_close_payload_still_fails_exact_intent_binding(field, value):
    function, record, payload, _ = primitive("sell", "net")
    altered = {**payload, field: value}
    with pytest.raises(write.NativeDemoWriteError):
        function(record, prepared(altered))
    assert record["phase"] == "locked" and record["request"] is None


def test_fake_or_copied_intent_cannot_enter_actual_close_boundary():
    fake = object.__new__(close.OwnedNativeCloseIntentV1)
    for supplied in (fake, {"close_request_sha256": "a" * 64}, object()):
        with pytest.raises(ValueError):
            write.submit_owned_native_demo_close_v1(supplied)


def test_native_close_intent_is_not_constructible():
    with pytest.raises(close.NativeControlledCloseError):
        close.OwnedNativeCloseIntentV1()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("bind", "response", "late_lock", "cancel"))
async def test_real_close_attempt_body_retains_unknown_and_stops_after_transaction_exit(
    failure,
):
    """Exercise real orchestration with explicit foreign primitive dependencies.

    No source, SQL session, cap or physical-write permission is fabricated.
    The exact production body must never retry its one SDK invocation, must
    exit its transaction before awaiting stop, and must retain received bytes.
    """
    events, state = [], {}
    journal = write._native_transport._ResponseDataJournalV2()
    gate = object()
    record = {
        "received": journal,
        "phase": "locked",
        "client": None,
        "original_transport_facts": SimpleNamespace(exact_order_json=b'{"sz":"1"}'),
    }

    class Transaction:
        async def __aenter__(self):
            events.append("transaction_enter")
            return self

        async def __aexit__(self, *arguments):
            events.append("transaction_exit")

    class Session:
        async def __aenter__(self):
            events.append("session_enter")
            return self

        async def __aexit__(self, *arguments):
            events.append("session_exit")

        def begin(self):
            return Transaction()

    class Client:
        async def place_order_observed(self, payload):
            events.append("post")
            assert payload == {"sz": "1"}
            await asyncio.sleep(0)
            journal.retain_headers(200, ((b"content-type", b"application/json"),))
            journal.retain_body(b'{"code":"0","data":[]}')
            journal.retain_eof()
            record["phase"] = "completed"
            if failure == "response":
                raise ValueError("private response text must not escape")
            if failure == "cancel":
                raise asyncio.CancelledError()
            return object()

    record["client"] = Client()
    facts = SimpleNamespace(session_factory=Session)

    def bind(actual, *, key):
        assert actual is state
        events.append("bind")
        if failure == "bind":
            raise ValueError("private source text must not escape")
        return facts

    async def lock(actual, *, session, key):
        events.append("lock")
        await asyncio.sleep(0)

    def discard(actual, *, key):
        assert actual is state
        state["unknown"] = (record, journal)
        events.append("discard")

    def inhibit(actual, *, key):
        assert actual is state and "unknown" in state
        events.append("local_inhibit")

        async def persist():
            assert "transaction_enter" not in events or "transaction_exit" in events
            events.append("persistent_stop")
            await asyncio.sleep(0)

        return persist()

    functions = {
        "_bind_owned_native_close_transport_v1": bind,
        "_lock_owned_native_close_transport_v1": lock,
        "_discard_owned_native_close_intent_for_root_v1": discard,
        "_inhibit_owned_native_close_transport_v1": inhibit,
    }

    def require_lock(actual):
        events.append("late_lock")
        if failure == "late_lock":
            raise ValueError("private lock text must not escape")

    overrides = {
        "_close_function_v1": lambda holder, name: functions[name],
        "_create_close_locked_gate_v1": lambda actual, session: gate,
        "_GATES": {gate: record},
        "_require_record_lock_v1": require_lock,
        "_retire_gate": lambda actual: events.append("retire"),
    }
    body = foreign_primitive_v1(write._submit_close_v1, overrides)
    expected = (
        asyncio.CancelledError if failure == "cancel" else write.NativeDemoWriteError
    )
    with pytest.raises(expected) as error:
        await body(state, (), asyncio.get_running_loop().time() + 1)
    assert "private" not in str(error.value)
    assert events.count("post") == (0 if failure == "bind" else 1)
    assert events.count("persistent_stop") == 1
    assert events.index("discard") < events.index("persistent_stop")
    assert state["unknown"] == (record, journal)
    if failure != "bind":
        assert events.index("transaction_exit") < events.index("persistent_stop")
        assert journal.body_raw == b'{"code":"0","data":[]}' and journal.eof is True
    with pytest.raises(ValueError):
        guard._function_pin(body, {})
