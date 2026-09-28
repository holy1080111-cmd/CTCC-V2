"""Actual evaluators and raw synthetic HTTP/WS, never real account/order IO."""

import asyncio
import hashlib
import json
import os
from datetime import timedelta, tzinfo
from pathlib import Path

import httpx
import pytest

from app.trade_evidence import gates
from app.trade_evidence.storage import FILE_NAMES, PublicationReceipt, PublishedFile
from app.trade_qualification import one_shot as module
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.test_qualification_account_capture import MAIN_UID, UID, plan
from tests.unit.test_qualification_account_collector import Stream, credentials
from tests.unit.test_qualification_account_materializer import instrument
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_public_market_collector import policy
from tests.unit.test_qualification_ws_collector import Socket

CALLBACKS = []


class Opaque:
    @property
    def __class__(self):
        CALLBACKS.append("class")
        raise module.OneShotInputError("synthetic-secret-do-not-serialize")


class OpaqueTimezone(tzinfo):
    def utcoffset(self, dt):
        CALLBACKS.append("timezone")
        raise module.OneShotInputError("synthetic-secret-do-not-serialize")


@pytest.fixture(autouse=True)
def no_foreign_callbacks():
    CALLBACKS.clear()
    yield
    assert CALLBACKS == []


def ms(value):
    return str(int(value.timestamp() * 1000))


class Clock:
    def __init__(self, start):
        self.start = self.last = start
        self.calls = 0
        self.overrides = {}

    def __call__(self):
        self.calls += 1
        self.last = self.overrides.get(
            self.calls, self.start + timedelta(milliseconds=self.calls)
        )
        return self.last


@pytest.fixture(params=("long", "short"))
def inputs(request):
    source = v2_engine_source(request.param)
    values = engine_inputs(source)
    risk = values["risk_inputs"]
    account = risk.account.model_copy(
        update={
            "account_id": UID,
            **{
                name: getattr(risk.account, name).model_copy(update={"account_id": UID})
                for name in (
                    "balance_stamp",
                    "positions_stamp",
                    "history_stamp",
                    "reservations_stamp",
                )
            },
        }
    )
    authority = risk.authority.model_copy(
        update={"stamp": risk.authority.stamp.model_copy(update={"account_id": UID})}
    )
    values["risk_inputs"] = risk.model_copy(
        update={"account": account, "authority": authority}
    )
    run = evaluate_pre_evidence(source.market, **values)
    assert run.pre_evidence_complete, run.result.fail_codes
    return source, values, run


def arguments(inputs, root, *, clock=None):
    source, values, run = inputs
    at = source.evaluated_at.replace(microsecond=0)
    selected = plan(created_at=at, history_start=at - timedelta(days=7), history_end=at)
    return {
        "root": root,
        "original_market": source.market,
        "run": run,
        "original_inputs": values,
        "market_policy": policy(counts=(240, 240, 240, 240)),
        "account_plan": selected,
        "expected_account_plan_sha256": module.accounts.plan_sha256(selected),
        "credentials": credentials(),
        "purpose": "synthetic_test",
        "clock": Clock(source.evaluated_at) if clock is None else clock,
    }


def synthetic_publisher(monkeypatch):
    """Windows-independent publisher contract only, never a native disk claim."""
    packets = []

    def publish(root, *, report_id, files, clock):
        clock()
        records = tuple(
            PublishedFile(
                name=name,
                sha256=hashlib.sha256(files[name]).hexdigest(),
                size_bytes=len(files[name]),
            )
            for name in FILE_NAMES
        )
        packets.append((report_id, dict(files)))
        return PublicationReceipt(
            status="written",
            report_id=report_id,
            report_directory=str(root / report_id),
            report_sha256=records[-1].sha256,
            files=records,
            completed_at=clock(),
        )

    monkeypatch.setattr(gates, "publish_evidence", publish)
    return packets


class CaptureHarness:
    def __init__(self, monkeypatch, source, clock, *, before_request=None):
        self.requests = []
        self.clients = []
        self.streams = []
        self.connections = []
        self.clock = clock
        self.source = source
        self.before_request = before_request
        self.socket = Socket()
        self.private_error = None
        self.public_error = None
        self.public_hold = None
        self.started = asyncio.Event()

        async def handler(request):
            if self.before_request:
                self.before_request()
            self.requests.append(request)
            self.started.set()
            await asyncio.sleep(0)
            private = request.url.path.startswith(
                ("/api/v5/account/", "/api/v5/trade/")
            )
            if private and self.private_error:
                raise self.private_error
            if not private and self.public_error:
                raise self.public_error
            if not private and self.public_hold is not None:
                await self.public_hold.wait()
            data = self.account(request) if private else self.public(request)
            body = json.dumps({"code": "0", "msg": "", "data": data}).encode()
            stream = Stream(body)
            self.streams.append(stream)
            return httpx.Response(
                200,
                stream=stream,
                headers={
                    "content-type": "application/json",
                    "content-length": str(len(body)),
                },
            )

        def factory():
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler), trust_env=False
            )
            self.clients.append(client)
            return client

        async def connector(uri, **kwargs):
            if self.before_request:
                self.before_request()
            self.connections.append(uri)
            return self.socket

        original_recv = self.socket.recv

        async def recv():
            self.socket.ticker = json.dumps(
                {
                    "arg": {
                        "channel": "tickers",
                        "instId": source.market.instrument_id,
                    },
                    "data": [self.ticker()],
                }
            )
            return await original_recv()

        self.socket.recv = recv
        monkeypatch.setattr(module.public_capture, "_new_client", factory)
        monkeypatch.setattr(module.private_capture, "_new_client", factory)
        monkeypatch.setattr(module.public_capture.ws, "_NoRedirectConnect", connector)

    def ticker(self):
        ticker = self.source.market.ticker
        return {
            "instType": "SWAP",
            "instId": self.source.market.instrument_id,
            "last": str(ticker.last),
            "bidPx": str(ticker.bid),
            "askPx": str(ticker.ask),
            "bidSz": "20",
            "askSz": "20",
            "open24h": str(ticker.open_24h),
            "high24h": str(ticker.high_24h),
            "low24h": str(ticker.low_24h),
            "vol24h": str(ticker.volume_contracts_24h),
            "volCcy24h": str(ticker.volume_currency_24h),
            "ts": ms(self.clock.last),
        }

    def public(self, request):
        source = self.source.market
        path = request.url.path
        if path.endswith("/candles"):
            params = request.url.params
            bars = list(reversed(source.candles[params["bar"]]))
            if params.get("after"):
                bars = [
                    bar for bar in bars if int(ms(bar.timestamp)) < int(params["after"])
                ]
            return [
                [
                    ms(bar.timestamp),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume_contracts),
                    str(bar.volume_currency),
                    str(bar.volume_quote),
                    "1",
                ]
                for bar in bars[: int(params["limit"])]
            ]
        base = {
            "instType": "SWAP",
            "instId": source.instrument_id,
            "ts": ms(self.clock.last),
        }
        if path.endswith("/ticker"):
            return [self.ticker()]
        if path.endswith("/mark-price"):
            return [{**base, "markPx": str(source.mark_price)}]
        if path.endswith("/funding-rate"):
            return [
                {
                    **base,
                    "fundingRate": str(source.funding_rate),
                    "nextFundingTime": ms(source.next_funding_time),
                }
            ]
        if path.endswith("/books"):
            return [
                {
                    **base,
                    "bids": [
                        [str(v.price), str(v.size), "0", "1"]
                        for v in source.order_book.bids
                    ],
                    "asks": [
                        [str(v.price), str(v.size), "0", "1"]
                        for v in source.order_book.asks
                    ],
                }
            ]
        if path.endswith("/open-interest"):
            return [
                {
                    **base,
                    "instType": "SWAP",
                    "oi": str(source.open_interest_contracts),
                    "oiCcy": str(source.open_interest_currency),
                    "oiUsd": "100000",
                }
            ]
        raise AssertionError("unexpected synthetic public endpoint")

    def account(self, request):
        path = request.url.path
        if path.endswith("/config"):
            return [
                {"uid": UID, "mainUid": MAIN_UID, "acctLv": "2", "posMode": "net_mode"}
            ]
        if path.endswith("/account-position-risk"):
            return [{"ts": ms(self.clock.last), "balData": [], "posData": []}]
        if path.endswith("/balance"):
            return [
                {
                    "uTime": ms(self.clock.last),
                    "totalEq": "10000",
                    "availEq": "9000",
                    "details": [{"ccy": "USDT", "eq": "10000", "availEq": "9000"}],
                }
            ]
        return []

    def assert_closed(self):
        assert all(client.is_closed for client in self.clients)
        assert all(stream.close_count == 1 for stream in self.streams)
        assert not self.connections or self.socket.closed


@pytest.mark.asyncio
async def test_g12_then_raw_collectors_then_real_recheck_without_order_authority(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    published = synthetic_publisher(monkeypatch)

    def published_first():
        assert len(published) == 1
        assert set(published[0][1]) == set(FILE_NAMES)

    harness = CaptureHarness(
        monkeypatch, inputs[0], args["clock"], before_request=published_first
    )
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_account_materialization_required", result
    assert result.evidence.result.evidence_complete
    assert len(result.evidence.result.gates) == 12
    assert result.recheck.checks[-1].step == "current_risk"
    assert all(check.passed for check in result.recheck.checks[:-1])
    assert not result.recheck.computational_checks_passed
    assert result.account_packet.account_complete is False
    assert len(result.account_packet.observations) == 23
    assert result.account_payload_sha256
    assert all(
        request.method == "GET" and request.url.host == "www.okx.com"
        for request in harness.requests
    )
    assert all(
        page.request_started_at > result.evidence.receipt.completed_at
        for page in result.account_packet.observations
    )
    assert (
        result.market_packet.barrier_completed_at
        == result.evidence.receipt.completed_at
    )
    assert (
        not result.execution_authority
        and not result.atomic_risk_reserved
        and not result.order_submitted
    )
    assert not result.evidence.result.qualified
    assert "synthetic-only-account-api" not in repr(result)
    assert UID not in repr(result)
    harness.assert_closed()


def supplements(inputs):
    at = inputs[0].evaluated_at
    return module.materializer.AccountMaterializationInputs(
        account_id=UID,
        settlement_currency="USDT",
        correlation_version="synthetic-one-shot-v1",
        instruments=(instrument(observed_at=at, received_at=at),),
        correlations=(
            module.materializer.CorrelationEntry(
                instrument_id=inputs[0].market.instrument_id, group="synthetic-crypto"
            ),
        ),
    )


@pytest.mark.asyncio
async def test_pinned_metadata_is_copied_before_await_and_mapped_without_authority(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    supplied = supplements(inputs)
    pin = module.materializer.materialization_inputs_sha256(supplied)
    args.update(
        materialization_inputs=supplied, expected_materialization_inputs_sha256=pin
    )

    def mutate_caller():
        object.__setattr__(supplied, "account_id", "999")

    harness = CaptureHarness(
        monkeypatch, inputs[0], args["clock"], before_request=mutate_caller
    )
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_account_materialization_incomplete", result
    mapped = result.account_materialization
    assert mapped.account_id == UID and mapped.inputs_sha256 == pin
    assert len(mapped.instruments) == 1
    assert mapped.instruments[0].instrument_id == inputs[0].market.instrument_id
    assert mapped.incomplete_reasons and not mapped.account_complete
    assert not result.account_complete and not result.execution_authority
    assert result.recheck.checks[-1].step == "current_risk"
    assert not result.recheck.computational_checks_passed
    assert mapped.snapshot is None or not mapped.snapshot.balance_stamp.complete
    assert UID not in repr(result)
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing_pin", "wrong_pin", "pin_without_inputs", "other_account", "dirty"]
)
async def test_materialization_pin_and_shape_rejected_before_g12(
    inputs, tmp_path, monkeypatch, case
):
    args = arguments(inputs, tmp_path)
    supplied = supplements(inputs)
    if case == "other_account":
        supplied = supplied.model_copy(update={"account_id": "999"})
    pin = module.materializer.materialization_inputs_sha256(supplied)
    if case == "dirty":
        object.__setattr__(supplied, "authority", True)
    args.update(
        materialization_inputs=None if case == "pin_without_inputs" else supplied,
        expected_materialization_inputs_sha256=None
        if case == "missing_pin"
        else "0" * 64
        if case == "wrong_pin"
        else pin,
    )

    def forbidden(*a, **kw):
        pytest.fail("invalid materialization crossed the publication boundary")

    args["clock"] = forbidden
    monkeypatch.setattr(module, "publish_qualification_evidence", forbidden)
    with pytest.raises(module.OneShotInputError):
        await module.publish_capture_recheck(**args)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "intent",
        "nested_risk",
        "nested_market",
        "private",
        "fields_set",
        "extra",
        "timezone",
        "supplied_error",
    ],
)
async def test_raw_preflight_never_invokes_opaque_callbacks_or_echoes_error(
    inputs, tmp_path, monkeypatch, case
):
    args = arguments(inputs, tmp_path)
    if case == "intent":
        args["original_inputs"]["intent"] = Opaque()
    elif case == "nested_risk":
        object.__setattr__(args["original_inputs"]["risk_inputs"], "account", Opaque())
    elif case == "nested_market":
        object.__setattr__(args["original_market"].ticker, "bid", Opaque())
    elif case in {"private", "fields_set", "extra"}:
        name = {
            "private": "__pydantic_private__",
            "fields_set": "__pydantic_fields_set__",
            "extra": "__pydantic_extra__",
        }[case]
        object.__setattr__(args["run"], name, {"hidden": Opaque()})
    elif case == "timezone":
        object.__setattr__(
            args["original_market"].ticker,
            "timestamp",
            inputs[0].evaluated_at.replace(tzinfo=OpaqueTimezone()),
        )
    else:

        def foreign_error(*a, **kw):
            raise module.OneShotInputError("synthetic-secret-do-not-serialize")

        monkeypatch.setattr(module.public_capture, "_policy_copy", foreign_error)

    def forbidden(*a, **kw):
        pytest.fail("unsafe preflight crossed publication boundary")

    args["clock"] = forbidden
    monkeypatch.setattr(module, "publish_qualification_evidence", forbidden)
    with pytest.raises(module.OneShotInputError) as caught:
        await module.publish_capture_recheck(**args)
    assert "synthetic-secret" not in str(caught.value)
    assert CALLBACKS == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_expiry_after_successful_g12_stops_before_new_capture(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    packets = synthetic_publisher(monkeypatch)
    args["clock"].overrides[5] = inputs[1]["intent"].expires_at
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_candidate_expired"
    assert len(packets) == 1 and result.evidence.result.evidence_complete
    assert result.recheck is None and harness.requests == []
    harness.assert_closed()


@pytest.mark.asyncio
async def test_timeout_closes_real_owned_clients_and_never_rechecks(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    args["total_timeout_seconds"] = 1
    synthetic_publisher(monkeypatch)
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    harness.public_hold = asyncio.Event()
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_deadline_exceeded", result
    assert result.recheck is None and not result.order_submitted
    harness.assert_closed()


@pytest.mark.asyncio
async def test_external_cancel_during_failed_sibling_cleanup_is_not_swallowed(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    started, closing, release, closed = (asyncio.Event() for _ in range(4))

    async def public(**kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            closing.set()
            # Model the real bounded owner cleanup: repeated cancellation cannot
            # discard ownership before its close has joined.
            waiter = asyncio.create_task(release.wait())
            while not waiter.done():
                try:
                    await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    pass
            closed.set()

    async def private(**kwargs):
        await started.wait()
        raise RuntimeError("synthetic-private-failure")

    monkeypatch.setattr(module.public_capture, "collect_public_market", public)
    monkeypatch.setattr(module.private_capture, "collect_demo_account_records", private)
    task = asyncio.create_task(module.publish_capture_recheck(**args))
    try:
        # Let synchronous G12 replay/render finish before timing the asynchronous
        # cleanup handshake. A busy renderer is not a stalled capture owner.
        await asyncio.sleep(0)
        await asyncio.wait_for(closing.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert closed.is_set()
    finally:
        # Failed assertions/timeouts must also release this test-owned blocker;
        # otherwise asyncio fixture teardown can wait forever for its cleanup.
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)


@pytest.mark.asyncio
async def test_real_earlier_gate_failure_has_no_clock_files_or_clients(
    inputs, tmp_path, monkeypatch
):
    source, values, _ = inputs
    values = {**values, "quote": None}
    run = evaluate_pre_evidence(source.market, **values)
    args = arguments((source, values, run), tmp_path)

    def forbidden(*a, **kw):
        pytest.fail("earlier rejection crossed a side-effect boundary")

    args["clock"] = forbidden
    monkeypatch.setattr(module, "publish_qualification_evidence", forbidden)
    monkeypatch.setattr(module.public_capture, "_new_client", forbidden)
    monkeypatch.setattr(module.private_capture, "_new_client", forbidden)
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_pre_evidence_rejected"
    assert result.evidence is result.account_packet is result.market_packet is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "old_receipt",
        "extra",
        "relative_root",
        "purpose",
        "timeout_bool",
        "plan_pin",
        "account_scope",
        "session",
    ],
)
async def test_invalid_input_rejected_before_any_publication(
    inputs, tmp_path, monkeypatch, change
):
    args = arguments(inputs, tmp_path)
    if change == "old_receipt":
        args["run"] = object()
    elif change == "extra":
        args["original_inputs"] = {**args["original_inputs"], "passed": True}
    elif change == "relative_root":
        args["root"] = Path("relative-root")
    elif change == "purpose":
        args["purpose"] = "live"
    elif change == "timeout_bool":
        args["total_timeout_seconds"] = True
    elif change == "plan_pin":
        args["expected_account_plan_sha256"] = "0" * 64
    elif change == "account_scope":
        args["account_plan"] = args["account_plan"].model_copy(
            update={"expected_uid": "999"}
        )
        args["expected_account_plan_sha256"] = module.accounts.plan_sha256(
            args["account_plan"]
        )
    elif change == "session":
        args["credentials"] = credentials(session_binding_id="other-session")

    def forbidden(*a, **kw):
        pytest.fail("invalid preflight caused I/O")

    monkeypatch.setattr(module, "publish_qualification_evidence", forbidden)
    with pytest.raises(module.OneShotInputError):
        await module.publish_capture_recheck(**args)


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_case", ["expired", "reversed", "bad_type"])
async def test_invalid_clock_never_opens_fresh_clients(
    inputs, tmp_path, monkeypatch, clock_case
):
    args = arguments(inputs, tmp_path)
    args["clock"].overrides[1] = {
        "expired": inputs[1]["intent"].expires_at,
        "reversed": inputs[0].evaluated_at - timedelta(milliseconds=1),
        "bad_type": "private-secret-value",
    }[clock_case]
    result = await module.publish_capture_recheck(**args)
    assert result.code in {
        "one_shot_candidate_expired",
        "one_shot_clock_reversed",
        "one_shot_clock_invalid",
    }
    assert result.evidence is None
    assert "private-secret-value" not in repr(result)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["private", "public"])
async def test_capture_failure_cancels_and_closes_siblings_without_recheck(
    inputs, tmp_path, monkeypatch, side
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    setattr(harness, side + "_error", RuntimeError("never-serialize-this-secret"))
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_capture_or_replay_rejected"
    assert result.recheck is None
    assert "never-serialize" not in repr(result)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_caller_cancellation_is_preserved_and_clients_joined(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    harness.public_hold = asyncio.Event()
    task = asyncio.create_task(module.publish_capture_recheck(**args))
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(harness.started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        harness.assert_closed()
    finally:
        harness.public_hold.set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)


@pytest.mark.skipif(
    os.name == "nt",
    reason="Actual POSIX six-file publication; Windows success not inferred",
)
@pytest.mark.asyncio
async def test_native_g12_disk_readback_before_owned_capture(
    inputs, tmp_path, monkeypatch
):
    root = tmp_path / "owned-one-shot"
    root.mkdir()
    args = arguments(inputs, root)
    report_path = root / inputs[0].report_id

    def disk_first():
        assert {p.name for p in report_path.iterdir()} == set(FILE_NAMES)
        for p in report_path.iterdir():
            assert p.stat().st_size > 0

    harness = CaptureHarness(
        monkeypatch, inputs[0], args["clock"], before_request=disk_first
    )
    result = await module.publish_capture_recheck(**args)
    assert result.code == "one_shot_account_materialization_required", result
    for item in result.evidence.receipt.files:
        raw = (report_path / item.name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item.sha256
    harness.assert_closed()
