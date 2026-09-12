"""Synthetic socket I/O only; no external WS, account, credentials or orders."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError, create_model, model_serializer

from app.trade_qualification import ws_collector as module
from app.trade_qualification.ws_collector import (
    CollectedWSReference,
    WSCollectionError,
    WSCollectionPolicy,
    collect_ws_reference,
    validate_collected_ws_reference,
)

NOW = datetime(2026, 9, 12, 6, tzinfo=UTC)
REPORT = "synthetic-ws-collector"
INSTRUMENT = "BTC-USDT-SWAP"


def millis(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return str(
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )


def ticker(source=NOW):
    return json.dumps(
        {
            "arg": {"channel": "tickers", "instId": INSTRUMENT},
            "data": [
                {
                    "instType": "SWAP",
                    "instId": INSTRUMENT,
                    "bidPx": "100",
                    "askPx": "100.01",
                    "bidSz": "2",
                    "askSz": "3",
                    "ts": millis(source),
                }
            ],
        }
    )


class Clock:
    def __init__(self, values=None):
        self.values = iter(
            values
            if values is not None
            else [NOW + timedelta(milliseconds=50 * i) for i in range(1, 9)]
        )

    def __call__(self):
        return next(self.values)


class Transport:
    aborted = False

    def abort(self):
        self.aborted = True


class Socket:
    def __init__(self):
        self.sent = []
        self.receives = 0
        self.closes = 0
        self.closed = False
        self.ack_override = None
        self.ticker = ticker()
        self.transport = Transport()
        self.recv_exception = None
        self.close_exception = None
        self.send_exception = None
        self.block_receive = False
        self.block_close = False
        self.receiving = asyncio.Event()
        self.closing = asyncio.Event()
        self.release_close = asyncio.Event()

    async def send(self, message):
        self.sent.append(message)
        if self.send_exception:
            raise self.send_exception

    async def recv(self):
        self.receives += 1
        self.receiving.set()
        if self.block_receive:
            await asyncio.Event().wait()
        if self.recv_exception:
            raise self.recv_exception
        if self.receives == 1:
            if self.ack_override is not None:
                return self.ack_override
            subscription = json.loads(self.sent[0])
            return json.dumps(
                {
                    "id": subscription["id"],
                    "event": "subscribe",
                    "arg": subscription["args"][0],
                    "connId": "syntheticConnection01",
                }
            )
        return self.ticker

    async def close(self):
        self.closes += 1
        self.closing.set()
        if self.block_close:
            await self.release_close.wait()
        if self.close_exception:
            raise self.close_exception
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    socket = Socket()
    calls = []

    async def connector(uri, **kwargs):
        calls.append((uri, kwargs))
        return socket

    monkeypatch.setattr(module, "_NoRedirectConnect", connector)
    return socket, calls


async def capture(fake, **kwargs):
    return await collect_ws_reference(
        clock=kwargs.pop("clock", Clock()),
        report_id=kwargs.pop("report_id", REPORT),
        instrument_id=kwargs.pop("instrument_id", INSTRUMENT),
        policy=kwargs.pop("policy", WSCollectionPolicy(max_age_seconds=5)),
        barrier_completed_at=kwargs.pop(
            "barrier_completed_at", NOW - timedelta(seconds=1)
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_single_fixed_public_subscription_capture_and_fresh_copy(fake):
    socket, calls = fake
    result = await capture(fake)
    assert len(calls) == 1
    assert calls[0] == (
        module.PUBLIC_WS_URL,
        {
            "proxy": None,
            "compression": None,
            "additional_headers": None,
            "user_agent_header": "CTCC-source-ws/1",
            "open_timeout": 3,
            "ping_interval": None,
            "close_timeout": 1,
            "max_size": 32768,
            "max_queue": 1,
        },
    )
    assert len(socket.sent) == 1
    assert socket.receives == 2
    assert socket.closes == 1 and socket.closed
    assert json.loads(socket.sent[0])["op"] == "subscribe"
    assert result.subscription_body == socket.sent[0].encode()
    assert (
        result.subscription_sha256
        == hashlib.sha256(result.subscription_body).hexdigest()
    )
    assert result.reference.bid == Decimal(100)
    assert result.reference is not result.reference
    assert result.ticker.source_time == NOW
    assert not result.execution_authority
    assert not result.socket_binding_verified
    assert not result.source_authenticity_verified
    assert validate_collected_ws_reference(result) == result
    assert (
        CollectedWSReference.model_validate_json(
            result.model_dump_json(round_trip=True), strict=True
        )
        == result
    )


def test_redirect_hook_never_returns_new_url():
    exc = RuntimeError("synthetic redirect")
    assert module._NoRedirectConnect.process_redirect(None, exc) is exc


@pytest.mark.parametrize(
    "key,value",
    [
        ("report_id", "bad id"),
        ("report_id", ""),
        ("report_id", 1),
        ("instrument_id", "BTC-USDT"),
        ("instrument_id", "BTC-USDT-SWAP "),
        ("instrument_id", "../private"),
        ("instrument_id", 1),
        ("clock", None),
        ("clock", lambda: NOW.replace(tzinfo=None)),
        ("barrier_completed_at", NOW + timedelta(seconds=1)),
        (
            "policy",
            WSCollectionPolicy(max_age_seconds=5).model_copy(
                update={"max_age_seconds": True}
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_inputs_cannot_open_socket(fake, key, value):
    with pytest.raises(WSCollectionError):
        await capture(fake, **{key: value})
    assert not fake[1]


@pytest.mark.parametrize(
    "frame",
    [
        "pong",
        "{}",
        '{"event":"error","msg":"PRIVATE SENTINEL"}',
        ticker(),
        b"{}",
        "x" * 32769,
        "界" * 12000,
        (
            '{"id":"wrong","event":"subscribe","arg":{"channel":"tickers",'
            '"instId":"BTC-USDT-SWAP"},"connId":"synthetic"}'
        ),
    ],
    ids=[
        "pong",
        "empty",
        "error",
        "ticker-before-ack",
        "binary",
        "large-ascii",
        "large-utf8",
        "wrong-id",
    ],
)
@pytest.mark.asyncio
async def test_first_non_ack_aborts_without_search_or_retry(fake, frame):
    socket, calls = fake
    socket.ack_override = frame
    with pytest.raises(WSCollectionError) as error:
        await capture(fake)
    assert "PRIVATE" not in str(error.value)
    assert socket.receives == 1
    assert len(calls) == socket.closes == 1
    assert socket.closed


@pytest.mark.parametrize(
    "frame",
    [
        "{}",
        "pong",
        b"{}",
        ticker(NOW + timedelta(seconds=1)),
        ticker(NOW - timedelta(seconds=1)),
        ticker(NOW - timedelta(seconds=10)),
        ticker().replace('"100.01"', '"99"'),
    ],
)
@pytest.mark.asyncio
async def test_bad_stale_pre_barrier_or_future_ticker_never_passes(fake, frame):
    socket, calls = fake
    socket.ticker = frame
    with pytest.raises(WSCollectionError):
        await capture(fake)
    assert socket.receives == 2 and socket.closes == 1 and socket.closed
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field", ["send_exception", "recv_exception", "close_exception"]
)
@pytest.mark.asyncio
async def test_remote_exceptions_are_redacted_and_cleanup_always_attempted(fake, field):
    socket, _ = fake
    setattr(socket, field, RuntimeError("PRIVATE SENTINEL"))
    with pytest.raises(WSCollectionError) as error:
        await capture(fake)
    assert "PRIVATE" not in str(error.value)
    assert socket.closes == 1
    assert socket.transport.aborted == (field == "close_exception")


@pytest.mark.asyncio
async def test_receive_timeout_closes_socket_once(fake):
    socket, calls = fake
    socket.block_receive = True
    with pytest.raises(WSCollectionError, match="ws_transport_timeout"):
        await capture(
            fake,
            policy=WSCollectionPolicy(max_age_seconds=5, receive_timeout_seconds=1),
        )
    assert socket.closes == 1 and socket.closed and len(calls) == 1


@pytest.mark.asyncio
async def test_cleanup_timeout_aborts_and_cannot_produce_record(fake):
    socket, _ = fake
    socket.block_close = True
    with pytest.raises(WSCollectionError, match="ws_cleanup_failed"):
        await capture(fake)
    assert socket.closes == 1 and socket.transport.aborted


@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.asyncio
async def test_cancellation_survives_cleanup_failure(fake, close_fails):
    socket, _ = fake
    socket.block_receive = True
    if close_fails:
        socket.close_exception = RuntimeError("PRIVATE SENTINEL")
    task = asyncio.create_task(capture(fake))
    await socket.receiving.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert socket.closes == 1
    assert socket.transport.aborted == close_fails


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_interrupt_single_cleanup(fake):
    socket, _ = fake
    socket.block_receive = socket.block_close = True
    task = asyncio.create_task(capture(fake))
    await socket.receiving.wait()
    task.cancel()
    await socket.closing.wait()
    task.cancel()
    socket.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert socket.closed and socket.closes == 1


@pytest.mark.parametrize("index", range(1, 8))
@pytest.mark.asyncio
async def test_any_backward_clock_rejects_and_closes(fake, index):
    values = [NOW + timedelta(milliseconds=50 * i) for i in range(1, 9)]
    values[index] = NOW
    with pytest.raises(WSCollectionError):
        await capture(fake, clock=Clock(values))
    assert fake[0].closes == 1


@pytest.mark.asyncio
async def test_final_age_includes_cleanup(fake):
    values = [NOW + timedelta(milliseconds=50 * i) for i in range(1, 9)]
    values[-1] = NOW + timedelta(seconds=6)
    with pytest.raises(WSCollectionError):
        await capture(fake, clock=Clock(values))
    assert fake[0].closed


@pytest.mark.parametrize(
    "key,value",
    [
        ("execution_authority", True),
        ("execution_authority", 0),
        ("source_authenticity_verified", True),
        ("socket_binding_verified", True),
        ("instrument_id", "ETH-USDT-SWAP"),
        ("report_id", "different-report"),
        ("endpoint", "wss://wrong.example"),
        ("subscription_body", b"{}"),
        ("subscription_sha256", "0" * 64),
        ("bundle_sha256", "0" * 64),
        ("hidden", True),
        ("subscription_body", iter((b"{}",))),
        ("subscription_id", "x" * 100000),
        ("completed_at", "2026-09-12"),
    ],
    ids=lambda value: (
        "bounded-oversize" if isinstance(value, str) and len(value) > 100 else None
    ),
)
@pytest.mark.asyncio
async def test_copied_record_cannot_bypass_revalidation(fake, key, value):
    record = await capture(fake)
    with pytest.raises(WSCollectionError, match="ws_record_invalid"):
        validate_collected_ws_reference(record.model_copy(update={key: value}))


@pytest.mark.parametrize("component", ["policy", "ack", "ticker"])
@pytest.mark.asyncio
async def test_hidden_nested_models_are_rejected_before_serialization(fake, component):
    record = await capture(fake)
    nested = getattr(record, component).model_copy(update={"hidden": True})
    with pytest.raises(WSCollectionError, match="ws_record_invalid"):
        validate_collected_ws_reference(record.model_copy(update={component: nested}))


@pytest.mark.asyncio
async def test_subclasses_are_not_authorized_records(fake):
    record = await capture(fake)
    derived = create_model("DerivedCapture", __base__=CollectedWSReference)
    child = derived.model_validate(record.model_dump(round_trip=True))
    with pytest.raises(WSCollectionError):
        validate_collected_ws_reference(child)


@pytest.mark.parametrize(
    "field",
    [
        "max_age_seconds",
        "open_timeout_seconds",
        "receive_timeout_seconds",
        "batch_timeout_seconds",
        "close_timeout_seconds",
        "max_message_bytes",
    ],
)
@pytest.mark.parametrize("value", [True, 0, -1, 100000, "2"])
def test_policy_rejects_unsafe_values(field, value):
    with pytest.raises(ValidationError):
        WSCollectionPolicy(**{"max_age_seconds": 5, field: value})


@pytest.mark.asyncio
async def test_injected_policy_serializer_is_never_called_or_allowed_to_open(fake):
    class Trap(BaseModel):
        @model_serializer
        def serialize(self):
            pytest.fail("Untrusted policy serializer executed")

    policy = WSCollectionPolicy(max_age_seconds=5).model_copy(
        update={"max_age_seconds": Trap()}
    )
    with pytest.raises(WSCollectionError):
        await capture(fake, policy=policy)
    assert not fake[1]
    record = await capture(fake)
    with pytest.raises(WSCollectionError):
        validate_collected_ws_reference(record.model_copy(update={"policy": policy}))


@pytest.mark.parametrize(
    "field",
    [
        "report_id",
        "instrument_id",
        "subscription_id",
        "subscription_sha256",
        "bundle_sha256",
    ],
)
@pytest.mark.asyncio
async def test_identifier_and_digest_whitespace_is_not_repaired(fake, field):
    record = await capture(fake)
    raw = record.model_dump(mode="python", round_trip=True)
    raw[field] = " " + raw[field] + " "
    with pytest.raises(ValidationError):
        CollectedWSReference.model_validate(raw, strict=True)
    with pytest.raises(WSCollectionError):
        validate_collected_ws_reference(record.model_copy(update={field: raw[field]}))
