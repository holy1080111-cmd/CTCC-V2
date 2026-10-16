"""Synthetic-only owned Demo read lease; never account or order authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from contextlib import contextmanager

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.database.repositories.qualification_ledger import (
    LedgerBootstrapCheckpoint,
    LedgerCaptureCheckpoint,
    QualificationLedgerRepository,
)
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_runtime as runtime
from app.trade_qualification import reservations
from tests.unit.test_qualification_account_capture import BARRIER, NOW
from tests.unit.test_qualification_account_collector import (
    API_KEY,
    API_SECRET,
    PASSPHRASE,
    Clock,
    Harness,
    Stream,
)
from tests.unit.test_qualification_account_materializer import (
    ledger_evidence,
    snapshot_inputs,
    source_pages,
)
from tests.unit.test_qualification_account_runtime import regional
from tests.unit.test_qualification_bootstrap_runtime import unknown_state


@contextmanager
def _pinned():
    settings = Settings(
        _env_file=None,
        environment="test",
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_api_key=API_KEY,
        okx_demo_api_secret=API_SECRET,
        okx_demo_api_passphrase=PASSPHRASE,
        okx_demo_read_max_retries=0,
    )
    private = OkxDemoPrivateRestClient(settings=settings)
    with private.pinned_read_session() as lease:
        yield lease, settings


def _setup(monkeypatch, lease, **harness_options):
    selected = regional(session_binding_id=lease.binding_id)
    harness = Harness(monkeypatch, pages=source_pages(), **harness_options)
    state = ledger_evidence().state

    async def read(self, scope):
        observed = harness.clock()
        received = harness.clock()
        return LedgerCaptureCheckpoint(
            state=state,
            observed_at=observed,
            received_at=received,
            state_sha256=reservations.digest(state),
        )

    monkeypatch.setattr(QualificationLedgerRepository, "read_capture_checkpoint", read)
    repository = QualificationLedgerRepository(None, clock=harness.clock)
    inputs = snapshot_inputs(ledger=None)
    session = runtime.ControlledDemoAccountSession.from_pinned_read_session(
        read_session=lease,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    arguments = {
        "repository": repository,
        "clock": harness.clock,
        "barrier_completed_at": BARRIER,
        "inputs": inputs,
        "expected_inputs_sha256": mapping.materialization_inputs_sha256(inputs),
    }
    return session, harness, arguments


@pytest.mark.asyncio
async def test_all_signed_gets_use_runtime_lease_but_remain_diagnostic(monkeypatch):
    with _pinned() as (lease, _):
        session, harness, arguments = _setup(monkeypatch, lease)
        result = await session.collect_and_materialize(**arguments)
        assert harness.requests
        assert len(harness.requests) == len(result.packet.observations)
        assert all(request.method == "GET" for request in harness.requests)
        assert all(
            request.url.host == "openapi.okx.com" for request in harness.requests
        )
        assert all(
            request.headers["ok-access-key"] == API_KEY
            and request.headers["x-simulated-trading"] == "1"
            for request in harness.requests
        )
        assert result.packet.plan.session_binding_id == lease.binding_id
        assert result.transport_provenance == "synthetic_transport"
        assert result.admission == "DENY"
        assert result.execution_authority is False
        assert result.materialization.account_complete is False
        assert result.materialization.source_authenticity_verified is False
        receipt = json.loads(result.receipt_json)
        assert receipt["account_complete"] is False
        assert receipt["execution_authority"] is False
        assert lease.binding_id not in result.receipt_json.decode()
        assert hashlib.sha256(result.receipt_json).hexdigest() == result.receipt_sha256
        harness.assert_closed()


def test_closed_foreign_and_wrong_plan_lease_deny_before_http(monkeypatch):
    with _pinned() as (lease, _):
        wrong = regional(session_binding_id="synthetic-private-session")
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            runtime.ControlledDemoAccountSession.from_pinned_read_session(
                read_session=lease,
                plan=wrong,
                expected_plan_sha256=capture.plan_sha256(wrong),
            )
        selected = regional(session_binding_id=lease.binding_id)
    with pytest.raises(runtime.AccountRuntimeError, match="account_runtime_invalid"):
        runtime.ControlledDemoAccountSession.from_pinned_read_session(
            read_session=lease,
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
        )


@pytest.mark.asyncio
async def test_changed_credentials_before_first_get_denies_without_request(monkeypatch):
    with _pinned() as (lease, settings):
        session, harness, arguments = _setup(monkeypatch, lease)
        settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            await session.collect_and_materialize(**arguments)
        assert harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change_kind", ("credential", "origin"))
async def test_changed_lease_inside_first_response_stops_page_chain(
    monkeypatch, change_kind
):
    with _pinned() as (lease, settings):

        def change(stream, index, rows):
            if index == 0:
                if change_kind == "credential":
                    settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
                else:
                    settings.okx_demo_rest_base_url = "https://us.okx.com"

        session, harness, arguments = _setup(monkeypatch, lease, change=change)
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ) as error:
            await session.collect_and_materialize(**arguments)
        assert len(harness.requests) == 1
        assert all(
            secret not in "".join(traceback.format_exception(error.value))
            for secret in (API_KEY, API_SECRET, PASSPHRASE)
        )
        harness.assert_closed()


@pytest.mark.asyncio
async def test_lease_change_during_response_cleanup_stops_page_chain(monkeypatch):
    with _pinned() as (lease, settings):
        original_close = Stream.aclose
        closes = 0

        async def close(stream):
            nonlocal closes
            await original_close(stream)
            closes += 1
            if closes == 1:
                settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")

        monkeypatch.setattr(Stream, "aclose", close)
        session, harness, arguments = _setup(monkeypatch, lease)
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            await session.collect_and_materialize(**arguments)
        assert closes >= 1
        assert len(harness.requests) == 1
        harness.assert_closed()


@pytest.mark.asyncio
async def test_mutated_session_handle_cannot_downgrade_to_legacy(monkeypatch):
    with _pinned() as (lease, _):
        session, harness, arguments = _setup(monkeypatch, lease)
        session._read_session = None
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            await session.collect_and_materialize(**arguments)
        assert harness.requests == []


@pytest.mark.asyncio
async def test_closed_lease_during_first_response_stops_page_chain(monkeypatch):
    context = _pinned()
    lease, _ = context.__enter__()
    closed = False
    try:

        def change(stream, index, rows):
            nonlocal closed
            if index == 0:
                context.__exit__(None, None, None)
                closed = True

        session, harness, arguments = _setup(monkeypatch, lease, change=change)
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            await session.collect_and_materialize(**arguments)
        assert len(harness.requests) == 1
        harness.assert_closed()
    finally:
        if not closed:
            context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_closed_lease_during_stream_await_stops_page_chain(monkeypatch):
    context = _pinned()
    lease, _ = context.__enter__()
    closed = False
    original_iter = Stream.__aiter__
    try:

        async def chunks(stream):
            nonlocal closed
            async for chunk in original_iter(stream):
                await asyncio.sleep(0)
                if not closed:
                    context.__exit__(None, None, None)
                    closed = True
                yield chunk

        monkeypatch.setattr(Stream, "__aiter__", chunks)
        session, harness, arguments = _setup(monkeypatch, lease)
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            await session.collect_and_materialize(**arguments)
        assert len(harness.requests) == 1
        harness.assert_closed()
    finally:
        if not closed:
            context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_lease_change_during_client_cleanup_denies_completed_chain(monkeypatch):
    with _pinned() as (lease, settings):

        def configure_client(client):
            original_close = client.aclose

            async def close():
                await original_close()
                settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")

            client.aclose = close

        session, harness, arguments = _setup(
            monkeypatch, lease, configure_client=configure_client
        )
        with pytest.raises(
            runtime.AccountRuntimeError, match="account_runtime_invalid"
        ):
            await session.collect_and_materialize(**arguments)
        assert len(harness.requests) == len(harness.script)
        harness.assert_closed()


@pytest.mark.asyncio
async def test_lease_change_during_journal_await_denies_before_http(monkeypatch):
    with _pinned() as (lease, settings):
        selected = regional(session_binding_id=lease.binding_id)
        credential = collector.DemoAccountCredentials(
            API_KEY, API_SECRET, PASSPHRASE, lease.binding_id
        )
        requests = []

        def handler(request):
            requests.append(request)
            raise AssertionError("GET must not start after changed lease")

        class Journal:
            async def request(self, **_):
                await asyncio.sleep(0)
                settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with pytest.raises(
                collector.AccountCollectionError, match="pinned_read_session_invalid"
            ):
                await collector._page(
                    client,
                    credential,
                    Clock(),
                    selected,
                    capture.plan_sha256(selected),
                    BARRIER,
                    stream="config_before",
                    page_index=0,
                    after=None,
                    previous_sha=None,
                    identity=None,
                    previous_time=NOW,
                    batch_started=NOW,
                    remaining_bytes=selected.max_total_bytes,
                    secret_tokens=[API_KEY, API_SECRET, PASSPHRASE, lease.binding_id],
                    wall_deadline=asyncio.get_running_loop().time() + 5,
                    capture_proofs=[],
                    _journal=Journal(),
                    _read_session=lease,
                )
        assert requests == []


def test_same_binding_label_with_other_credentials_is_not_a_lease():
    with _pinned() as (lease, _):
        credential = collector.DemoAccountCredentials(
            "synthetic-other-key", API_SECRET, PASSPHRASE, lease.binding_id
        )
        with pytest.raises(
            collector.AccountCollectionError, match="pinned_read_session_invalid"
        ):
            collector._check_pinned_read_session(
                lease, credential, origin="https://openapi.okx.com"
            )


@pytest.mark.asyncio
async def test_pinned_cold_bootstrap_remains_uninitialized_and_deny(monkeypatch):
    with _pinned() as (lease, _):
        session, harness, arguments = _setup(monkeypatch, lease)
        state = unknown_state()

        async def read(self, scope):
            return LedgerBootstrapCheckpoint(
                state,
                harness.clock(),
                harness.clock(),
                reservations.digest(state),
            )

        monkeypatch.setattr(
            QualificationLedgerRepository, "initialize_capture_scope", read
        )
        monkeypatch.setattr(
            QualificationLedgerRepository, "read_bootstrap_checkpoint", read
        )
        result = await session.collect_bootstrap(
            repository=arguments["repository"],
            clock=harness.clock,
            barrier_completed_at=BARRIER,
        )
        assert harness.requests
        assert len(harness.requests) == len(result.packet.observations)
        assert result.ledger_checkpoint.account_initialized is False
        assert result.account_complete is False
        assert result.execution_authority is False
        assert result.admission == "DENY"
        assert json.loads(result.receipt_json)["account_revision_published"] is False
        harness.assert_closed()
