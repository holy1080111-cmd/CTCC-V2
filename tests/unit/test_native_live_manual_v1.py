"""Actual local HTTP/worker tests with explicit inert core seams; no Live Arm.

Fake sessions model one original worker and retain a synthetic journal across
late cleanup failure. They are never accepted by the genuine source boundary.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api import native_live_manual_v1 as subject
from app.api import security
from app.dashboard.native_live_manual_v1 import render_live_manual_dashboard_v1

TOKEN = "synthetic-live-http-only-access-000000000000"
RESERVATION = "a" * 64
CHALLENGE = "b" * 64


def _data():
    return {
        "schema": "ctcc.native_live.lifecycle_receipt.v1",
        "status": "RECONCILED_FLAT",
        "reservation_id": RESERVATION,
        "report_id": None,
        "risk_hold_retained": False,
        "protection_verified": None,
        "emergency_stop": True,
        "disarmed": True,
        "execution_authority": False,
    }


def _challenge():
    return {
        "schema": "ctcc.native_live.operator_challenge.v1",
        "challenge_id": CHALLENGE,
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "direction": "long",
        "account_label": "…0001",
        "arm_phrase": "ARM_OKX_LIVE_REAL_MONEY:cccccccccccccccccccccccccccccccc",
        "clear_stop_phrase": "CLEAR_OKX_LIVE_STOP:cccccccccccccccccccccccccccccccc",
        "controlled_exit_phrase": "",
        "caps": [
            "0.25000000000000000000",
            "0.25000000000000000000",
            "25.00000000000000000000",
            "25.00000000000000000000",
        ],
        "existing_leverage": 3,
        "controlled_exit_requested": False,
        "challenge_expires_at": "2026-11-20T00:00:00+00:00",
        "arm_seconds": 30,
        "maximum_submissions": 1,
        "candidate_created": False,
        "controlled_exit_authorized": False,
        "execution_authority": False,
        "credential_session_sha256": "DO_NOT_EXPOSE_SESSION",
        "account_uid": "DO_NOT_EXPOSE_UID",
    }


def _request():
    return subject.PrepareRequest(
        instrument_id="BTC-USDT-SWAP",
        strategy="trend_pullback",
        direction="long",
        controlled_exit_requested=False,
    )


def _approval(worker):
    return subject.ApprovalRequest(
        operation_id=worker.job["operation_id"],
        challenge_id=CHALLENGE,
        arm_phrase="ARM_OKX_LIVE_REAL_MONEY:cccccccccccccccccccccccccccccccc",
        clear_stop_phrase="CLEAR_OKX_LIVE_STOP:cccccccccccccccccccccccccccccccc",
        controlled_exit_phrase="",
    )


class SyntheticQualificationError(ValueError):
    pass


class FakeCore:
    def __init__(self, *, late_failure=False, pause_open=False):
        self.events = []
        self.journal = []
        self.session = None
        self.task = None
        self.future = None
        self.late_failure = late_failure
        self.entered = asyncio.Event()
        self.open_gate = asyncio.Event()
        if not pause_open:
            self.open_gate.set()
        self.runtime = SimpleNamespace(
            configured_live_runtime_v1=self.runtime_context,
            inhibit_configured_live_runtime_v1=self.inhibit,
        )
        self.qualification = SimpleNamespace(
            open_live_operator_session_v1=self.source_context,
            run_owned_live_operator_once_v1=self.run,
            signal_live_operator_approval_v1=self.approve,
            revoke_live_operator_session_v1=self.revoke,
            OwnedLiveQualificationError=SyntheticQualificationError,
        )
        self.lifecycle = SimpleNamespace(
            reconcile_owned_live_postsubmit_v1=self.reconcile,
            live_lifecycle_public_projection_v1=lambda receipt: receipt,
            observe_live_lifecycle_tasks_v1=self.observe,
            reconcile_pending_owned_live_intent_v1=self.recover,
        )

    @asynccontextmanager
    async def runtime_context(self):
        self.events.append("runtime_open")
        try:
            yield object()
        finally:
            self.events.append("runtime_closed")

    async def inhibit(self, _owner):
        self.events.append("startup_inhibit")
        return {
            "schema": "ctcc.native_live.startup_inhibit_data.v1",
            "armed": False,
            "emergency_stop": True,
            "control_present": False,
            "readback_sha256": None,
            "account_complete": False,
            "exchange_flat_verified": False,
            "execution_authority": False,
        }

    @asynccontextmanager
    async def source_context(self, _owner, **_arguments):
        self.task = asyncio.current_task()
        self.entered.set()
        await self.open_gate.wait()
        self.future = asyncio.get_running_loop().create_future()
        self.session = SimpleNamespace(challenge_data=json.dumps(_challenge()).encode())
        try:
            self.events.append("source_open")
            yield self.session
        finally:
            self.events.append("source_stopped")
            if not self.future.done():
                self.future.cancel()
            if self.late_failure:
                raise OSError("DO_NOT_EXPOSE_PRIVATE_PATH")

    async def run(self, session):
        assert session is self.session and asyncio.current_task() is self.task
        if await self.future is not True:
            raise ValueError("synthetic_revoke")
        self.events.append("synthetic_submit_once")
        return object(), object()

    def approve(self, **body):
        assert asyncio.current_task() is not self.task
        if (
            body["arm_phrase"]
            != "ARM_OKX_LIVE_REAL_MONEY:cccccccccccccccccccccccccccccccc"
        ):
            raise ValueError("DO_NOT_EXPOSE_PRIVATE_KEY")
        self.future.set_result(True)

    def revoke(self, session):
        assert session is self.session
        self.events.append("synchronous_revoke")
        if not self.future.done():
            self.future.cancel()

    async def reconcile(self, _qualified, _physical):
        assert asyncio.current_task() is self.task
        self.journal.append(_data())
        self.events.append("synthetic_journal_committed")
        return _data()

    async def observe(self):
        return tuple(self.journal)

    async def recover(self, reservation_id):
        assert reservation_id == RESERVATION
        self.events.append("read_only_recovery")
        return _data()


def _install(monkeypatch, core):
    monkeypatch.setattr(
        subject, "_modules", lambda: (core.runtime, core.qualification, core.lifecycle)
    )
    monkeypatch.setattr(subject, "_runtime", lambda: core.runtime)
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(
            api_token_is_safe=True,
            api_token_value=TOKEN,
        ),
    )


@pytest.mark.asyncio
async def test_actual_http_default_surface_authentication_and_secret_redaction(
    monkeypatch,
):
    core = FakeCore()
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    application = subject._build_app(worker)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://127.0.0.1",
        ) as client,
    ):
        page = await client.get("/")
        assert page.status_code == 200 and "Live 人工操作" in page.text
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert page.headers["cache-control"] == "no-store"
        assert (await client.get("/api/live-tool/status")).status_code == 401
        headers = {"X-CTCC-Token": TOKEN}
        status = await client.get("/api/live-tool/status", headers=headers)
        assert status.json()["startup_inhibit_confirmed"] is True
        assert status.json()["micro_live_acceptance"] == "NOT_PERFORMED"
        response = await client.post(
            "/api/live-tool/prepare",
            headers=headers,
            json={**_request().model_dump(), "secret": "DO_NOT_ECHO_TOKEN"},
        )
        assert response.status_code == 422 and "DO_NOT_ECHO" not in response.text
        for path in (
            "/docs",
            "/openapi.json",
            "/api/okx-live/order",
            "/api/live-tool/arm",
        ):
            assert (await client.post(path, headers=headers)).status_code == 404
    assert core.events == ["runtime_open", "startup_inhibit", "runtime_closed"]


@pytest.mark.asyncio
async def test_same_worker_retains_source_and_http_only_signals_once(monkeypatch):
    core = FakeCore()
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.start(_request())
    await asyncio.sleep(0)
    assert worker.job["status"] == "AWAITING_HUMAN_ARM"
    assert worker.ready is False and "synthetic_submit_once" not in core.events
    raw = json.dumps(worker.snapshot())
    assert "DO_NOT_EXPOSE" not in raw
    with pytest.raises(HTTPException):
        worker.start(_request())
    worker.approve(_approval(worker))
    with pytest.raises(HTTPException):
        worker.approve(_approval(worker))
    await worker.task
    assert worker.job["status"] == "RECONCILED_FLAT"
    assert core.events[-4:] == [
        "synthetic_submit_once",
        "synthetic_journal_committed",
        "source_stopped",
        "runtime_closed",
    ]
    assert worker.job["challenge"] is None


@pytest.mark.asyncio
async def test_stop_revokes_before_cleanup_and_never_submits(monkeypatch):
    core = FakeCore()
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.start(_request())
    await asyncio.sleep(0)
    worker.stop(worker.job["operation_id"])
    assert core.events[-1] == "synchronous_revoke"
    await worker.task
    assert "synthetic_submit_once" not in core.events
    assert worker.job["status"] == "STOPPED_BEFORE_ARM"


@pytest.mark.asyncio
async def test_stop_during_source_open_revokes_before_challenge_or_dispatch(
    monkeypatch,
):
    core = FakeCore(pause_open=True)
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.start(_request())
    await core.entered.wait()
    worker.stop(worker.job["operation_id"])
    core.open_gate.set()
    await worker.task
    assert "synchronous_revoke" in core.events
    assert "synthetic_submit_once" not in core.events
    assert worker.job["challenge"] is None


@pytest.mark.asyncio
async def test_late_context_failure_keeps_committed_journal_and_denies_success(
    monkeypatch,
):
    core = FakeCore(late_failure=True)
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.start(_request())
    await asyncio.sleep(0)
    worker.approve(_approval(worker))
    await worker.task
    assert core.journal == [_data()]
    assert worker.job["status"] == "UNCERTAIN" and worker.job["result"] is None
    assert "DO_NOT_EXPOSE" not in json.dumps(worker.snapshot())
    assert (await worker.journal())["items"] == [_data()]


@pytest.mark.asyncio
async def test_restart_is_stopped_and_reconciliation_never_enters_submit(monkeypatch):
    core = FakeCore()
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.reconcile(subject.ReconcileRequest(reservation_id=RESERVATION))
    await worker.task
    assert worker.job["status"] == "RECONCILED_FLAT"
    assert "read_only_recovery" in core.events
    assert (
        "source_open" not in core.events and "synthetic_submit_once" not in core.events
    )
    restarted = subject.LiveToolWorker()
    await restarted.initialize()
    assert (
        restarted.job is None
        and restarted.snapshot()["micro_live_acceptance"] == "NOT_PERFORMED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [None, [], tuple(_data() for _ in range(101)), ({**_data(), "private": "secret"},)],
)
async def test_unknown_or_oversized_journal_never_becomes_empty_success(
    monkeypatch, value
):
    core = FakeCore()

    async def observe():
        return value

    core.lifecycle.observe_live_lifecycle_tasks_v1 = observe
    _install(monkeypatch, core)
    with pytest.raises(HTTPException, match="live_journal_unconfirmed"):
        await subject.LiveToolWorker().journal()


def test_static_dashboard_has_no_automatic_requests_or_saved_credentials():
    first, second = render_live_manual_dashboard_v1(), render_live_manual_dashboard_v1()
    html = first.body.decode()
    assert (
        first.headers["content-security-policy"]
        != second.headers["content-security-policy"]
    )
    assert "__NONCE__" not in html
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "innerHTML",
        "setInterval",
        "location.search",
    ):
        assert forbidden not in html
    assert 'value="ARM_' not in html
    assert "credentials:'omit'" in html and "redirect:'error'" in html
    assert "只在此視窗暫時使用" in html


@pytest.mark.parametrize(
    "bad",
    [
        "NaN",
        "Infinity",
        "-1",
        "0",
        "0.25000000000000000001",
        "1E-21",
        "1E+99",
        "9" * 100,
        "1/4",
        0.25,
        True,
    ],
)
def test_source_decimal_caps_reject_unknown_nonfinite_oversized_or_weakened_values(bad):
    value = _challenge()
    value["caps"][0] = bad
    with pytest.raises(HTTPException):
        subject._challenge(json.dumps(value).encode())


def test_source_twenty_decimal_caps_and_tiny_exponent_are_displayed_exactly():
    value = _challenge()
    assert subject._challenge(json.dumps(value).encode())["caps"] == [
        "0.25",
        "0.25",
        "25",
        "25",
    ]
    value["caps"][0] = "1E-20"
    projected = subject._challenge(json.dumps(value).encode())
    assert projected["caps"][0] == "0.00000000000000000001"
    assert value["caps"][0] == "1E-20"  # Original source DATA is unchanged.


@pytest.mark.asyncio
async def test_actual_asgi_prepare_approve_stop_journal_and_reconcile_all_use_one_worker(
    monkeypatch,
):
    core = FakeCore()
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    application = subject._build_app(worker)
    headers = {"X-CTCC-Token": TOKEN}
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://127.0.0.1"
        ) as client,
    ):
        prepared = await client.post(
            "/api/live-tool/prepare", headers=headers, json=_request().model_dump()
        )
        assert prepared.status_code == 202
        await asyncio.sleep(0)
        current = (await client.get("/api/live-tool/status", headers=headers)).json()
        assert (
            current["job"]["status"] == "AWAITING_HUMAN_ARM"
            and "synthetic_submit_once" not in core.events
        )
        approved = await client.post(
            "/api/live-tool/approve",
            headers=headers,
            json=_approval(worker).model_dump(),
        )
        assert approved.status_code == 200
        await worker.task
        assert worker.job["status"] == "RECONCILED_FLAT"
        journal = await client.get("/api/live-tool/journal", headers=headers)
        assert journal.json()["items"] == [_data()]
        recovered = await client.post(
            "/api/live-tool/reconcile",
            headers=headers,
            json={"reservation_id": RESERVATION},
        )
        assert recovered.status_code == 202
        await worker.task
        assert (
            core.events.count("synthetic_submit_once") == 1
            and "read_only_recovery" in core.events
        )
        await client.post("/api/live-tool/check-setup", headers=headers)
        await client.post(
            "/api/live-tool/prepare", headers=headers, json=_request().model_dump()
        )
        await asyncio.sleep(0)
        stopped = await client.post(
            "/api/live-tool/stop",
            headers=headers,
            json={"operation_id": worker.job["operation_id"]},
        )
        assert stopped.status_code == 200
        await worker.task
        assert (
            worker.job["status"] == "STOPPED_BEFORE_ARM"
            and core.events.count("synthetic_submit_once") == 1
        )


@pytest.mark.asyncio
async def test_actual_remote_peer_cannot_use_loopback_host_header(monkeypatch):
    core = FakeCore()
    _install(monkeypatch, core)
    application = subject._build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=("203.0.113.10", 12000)),
        base_url="http://127.0.0.1",
    ) as client:
        response = await client.get(
            "/api/live-tool/status", headers={"X-CTCC-Token": TOKEN}
        )
    assert response.status_code == 403 and response.json() == {
        "detail": "live_tool_loopback_required"
    }
    assert core.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status",
    [
        ("native_live_initial_qualification_wait_or_fail", "WAIT"),
        ("native_live_post_g12_cancelled", "CANCELLED"),
        ("native_live_human_challenge_expired", "EXPIRED"),
    ],
)
async def test_known_pre_intent_rejection_has_truthful_status_after_successful_cleanup(
    monkeypatch, code, status
):
    core = FakeCore()

    async def fail(_session):
        raise SyntheticQualificationError(code)

    core.qualification.run_owned_live_operator_once_v1 = fail
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.start(_request())
    await worker.task
    assert worker.job["status"] == status and worker.job["error"] is None
    assert "synthetic_submit_once" not in core.events and core.events[-2:] == [
        "source_stopped",
        "runtime_closed",
    ]


@pytest.mark.asyncio
async def test_late_cleanup_failure_does_not_relabel_wait_as_a_confirmed_safe_stop(
    monkeypatch,
):
    core = FakeCore(late_failure=True)

    async def fail(_session):
        raise SyntheticQualificationError(
            "native_live_initial_qualification_wait_or_fail"
        )

    core.qualification.run_owned_live_operator_once_v1 = fail
    _install(monkeypatch, core)
    worker = subject.LiveToolWorker()
    await worker.initialize()
    worker.start(_request())
    await worker.task
    assert worker.job["status"] == "UNCERTAIN" and worker.job["result"] is None
