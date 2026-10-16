"""Opt-in V7 pinned Demo lease boundary; synthetic, with no HTTP or orders."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_runtime as runtime
from app.trade_qualification import post_g12_account_event_observation_v4 as bridge
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_capture_v7 import v7_plan


@contextmanager
def _pinned():
    settings = Settings(
        _env_file=None,
        environment="test",
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_api_key="synthetic-demo-key",
        okx_demo_api_secret="synthetic-demo-secret",
        okx_demo_api_passphrase="synthetic-demo-passphrase",
        okx_demo_read_max_retries=0,
    )
    with OkxDemoPrivateRestClient(settings=settings).pinned_read_session() as lease:
        plan = v7_plan(session_binding_id=lease.binding_id)
        session = runtime.ControlledDemoAccountSession.from_pinned_read_session(
            read_session=lease,
            plan=plan,
            expected_plan_sha256=capture.plan_sha256(plan),
        )
        yield session, lease, settings


def _result():
    return bridge.PostG12AccountEventObservationV4(
        code="v3_binding_invalid",
        report_id="synthetic-report",
        scope_sha256="a" * 64,
        account_uid_sha256="b" * 64,
        candidate_sha256="c" * 64,
        account_plan_sha256="d" * 64,
        original_event_key="e" * 64,
        reservation_id="f" * 64,
        candidate_deadline_at=NOW.isoformat(),
    )


@pytest.mark.asyncio
async def test_pinned_entry_delegates_existing_v7_pipeline_without_authority(
    monkeypatch,
):
    with _pinned() as (session, _, _):
        expected = _result()
        seen = []

        async def original(*args, **kwargs):
            seen.append((args, kwargs))
            session._used = True
            return expected

        monkeypatch.setattr(
            bridge, "publish_capture_inspect_history_event_v4", original
        )
        actual = await bridge.publish_capture_inspect_history_event_v4_pinned(
            "synthetic-argument", account_session=session, marker="synthetic-marker"
        )
        assert actual is expected
        assert seen == [
            (
                ("synthetic-argument",),
                {
                    "account_session": session,
                    "marker": "synthetic-marker",
                },
            )
        ]
        assert actual.admission == "DENY"
        assert actual.source_authenticity_verified is False
        assert actual.account_complete is False
        assert actual.execution_authority is False
        assert actual.atomic_risk_reserved is False
        assert actual.order_submitted is False


@pytest.mark.asyncio
async def test_legacy_and_lookalike_sessions_reject_before_delegate(monkeypatch):
    invoked = []

    async def forbidden(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("existing V4 flow must not start")

    monkeypatch.setattr(bridge, "publish_capture_inspect_history_event_v4", forbidden)
    with _pinned() as (session, lease, _):
        plan = v7_plan()
        legacy = runtime.ControlledDemoAccountSession(
            credentials=runtime.collector.DemoAccountCredentials(
                "synthetic-demo-key",
                "synthetic-demo-secret",
                "synthetic-demo-passphrase",
                plan.session_binding_id,
            ),
            plan=plan,
            expected_plan_sha256=capture.plan_sha256(plan),
        )

        class Lookalike:
            _used = False
            _read_session_required = True
            _plan = session._plan
            _pin = session._pin

            def _checked_read_session(self, _plan):
                return lease

        detached = object.__new__(runtime.ControlledDemoAccountSession)
        detached._used = False
        detached._read_session_required = True
        detached._read_session = lease
        detached._credentials = session._credentials
        detached._plan = session._plan
        detached._pin = session._pin
        for candidate in (legacy, Lookalike(), detached):
            with pytest.raises(
                bridge.PostG12AccountEventObservationError,
                match="^v4_pinned_account_session_invalid$",
            ):
                await bridge.publish_capture_inspect_history_event_v4_pinned(
                    account_session=candidate
                )
    assert invoked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("credential", "origin", "used"))
async def test_changed_pinned_lease_rejects_before_delegate(monkeypatch, change):
    invoked = []

    async def forbidden(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("existing V4 flow must not start")

    monkeypatch.setattr(bridge, "publish_capture_inspect_history_event_v4", forbidden)
    with _pinned() as (session, _, settings):
        if change == "credential":
            settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
        elif change == "origin":
            settings.okx_demo_rest_base_url = "https://us.okx.com"
        else:
            session._used = True
        with pytest.raises(
            bridge.PostG12AccountEventObservationError,
            match="^v4_pinned_account_session_invalid$",
        ):
            await bridge.publish_capture_inspect_history_event_v4_pinned(
                account_session=session
            )
    assert invoked == []


@pytest.mark.asyncio
async def test_closed_lease_rejects_before_delegate(monkeypatch):
    invoked = []

    async def forbidden(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("existing V4 flow must not start")

    monkeypatch.setattr(bridge, "publish_capture_inspect_history_event_v4", forbidden)
    with _pinned() as (session, _, _):
        pass
    with pytest.raises(
        bridge.PostG12AccountEventObservationError,
        match="^v4_pinned_account_session_invalid$",
    ):
        await bridge.publish_capture_inspect_history_event_v4_pinned(
            account_session=session
        )
    assert invoked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("credential", "origin", "unconsumed"))
async def test_late_lease_change_rejects_completed_delegate(monkeypatch, change):
    with _pinned() as (session, _, settings):
        seen = []

        async def original(*args, **kwargs):
            seen.append("v4_completed")
            if change != "unconsumed":
                session._used = True
            if change == "credential":
                settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
            elif change == "origin":
                settings.okx_demo_rest_base_url = "https://us.okx.com"
            return _result()

        monkeypatch.setattr(
            bridge, "publish_capture_inspect_history_event_v4", original
        )
        with pytest.raises(
            bridge.PostG12AccountEventObservationError,
            match="^v4_pinned_account_session_invalid$",
        ):
            await bridge.publish_capture_inspect_history_event_v4_pinned(
                account_session=session
            )
        assert seen == ["v4_completed"]


@pytest.mark.asyncio
async def test_delegate_return_must_be_exact_v4_result(monkeypatch):
    with _pinned() as (session, _, _):

        async def original(*args, **kwargs):
            session._used = True
            return object()

        monkeypatch.setattr(
            bridge, "publish_capture_inspect_history_event_v4", original
        )
        with pytest.raises(
            bridge.PostG12AccountEventObservationError,
            match="^v4_pinned_result_invalid$",
        ):
            await bridge.publish_capture_inspect_history_event_v4_pinned(
                account_session=session
            )
