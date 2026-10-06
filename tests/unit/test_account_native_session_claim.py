"""Private session ownership only; no host, account HTTP, DB or order IO."""

import asyncio
import json
from pathlib import Path

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification.account_runtime import (
    AccountRuntimeError,
    ControlledDemoAccountSession,
)
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_qualification_account_collector import credentials


def _session():
    plan = current_plan()
    return ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )


def _entry(kind, session):
    arguments = {"session_factory": None, "proof_root": Path.cwd()}
    if kind == "history":
        arguments["history_capture_id"] = "a" * 32
        return runtime.capture_native_current_history_join(session, **arguments)
    return runtime.capture_initial_native_account(session, **arguments)


def _no_io(monkeypatch, session):
    samples = Samples()
    calls = []

    def sample():
        # This is the initial stage's first native dependency, not a late check.
        assert session._used is True
        calls.append("sample")
        return samples()

    monkeypatch.setattr(runtime, "_configured_factory", lambda _: True)
    monkeypatch.setattr(native.clock, "native_stamp", sample)
    monkeypatch.setattr(
        native.clock,
        "_native_observation_payload",
        lambda: pytest.fail("a real host clock probe must not run"),
    )
    monkeypatch.setattr(
        runtime.collector,
        "_new_client",
        lambda: pytest.fail("a real account client must not be allocated"),
    )
    return calls


def _denied(result):
    value = json.loads(result.receipt_json)
    assert value["snapshot"] is None and value["admission"] == "DENY"
    assert result.owner is None and result.account_complete is False
    assert result.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["initial", "history"])
async def test_concurrent_reuse_cannot_start_a_second_native_stage(monkeypatch, kind):
    session = _session()
    calls = _no_io(monkeypatch, session)
    entered, release = asyncio.Event(), asyncio.Event()

    async def capture_current(stage, received_session, factory, root):
        assert received_session is session
        state = native._state(stage)
        assert state["session_claim"] is native._session_claim(session)
        calls.append("capture")
        entered.set()
        await release.wait()
        raise proof.NativeAccountProofError("synthetic_capture_failed")

    monkeypatch.setattr(runtime, "_capture_initial_current", capture_current)
    first = asyncio.create_task(_entry(kind, session))
    await asyncio.wait_for(entered.wait(), timeout=2)
    try:
        with pytest.raises(proof.NativeAccountProofError, match="inputs_invalid"):
            await _entry(kind, session)
        assert calls == ["sample", "capture"]
    finally:
        release.set()
    _denied(await first)
    assert session._used is True
    assert not native._SESSION_CLAIMS and not native._STAGES


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["initial", "history"])
async def test_cancellation_keeps_session_burned_and_removes_private_claim(
    monkeypatch, kind
):
    session = _session()
    calls = _no_io(monkeypatch, session)
    entered = asyncio.Event()

    async def capture_current(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_capture_initial_current", capture_current)
    first = asyncio.create_task(_entry(kind, session))
    await asyncio.wait_for(entered.wait(), timeout=2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert session._used is True
    assert not native._SESSION_CLAIMS and not native._STAGES
    with pytest.raises(proof.NativeAccountProofError, match="inputs_invalid"):
        await _entry(kind, session)
    assert calls == ["sample"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["initial", "history"])
async def test_first_native_sample_failure_cannot_return_or_renew_session(
    monkeypatch, kind
):
    session = _session()
    calls = _no_io(monkeypatch, session)

    def failed_sample():
        assert session._used is True
        calls.append("failed_sample")
        raise RuntimeError("synthetic_native_dependency_failed")

    monkeypatch.setattr(native.clock, "native_stamp", failed_sample)
    _denied(await _entry(kind, session))
    assert session._used is True
    assert not native._SESSION_CLAIMS and not native._STAGES
    with pytest.raises(proof.NativeAccountProofError, match="inputs_invalid"):
        await _entry(kind, session)
    assert calls == ["failed_sample"]


@pytest.mark.asyncio
async def test_exact_parent_bootstrap_adopts_claim_once_without_resetting_used(
    monkeypatch,
):
    session = _session()
    _no_io(monkeypatch, session)
    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin, scope_sha256="b" * 64, _claimed_session=session
        ) as stage,
    ):
        observer = native._state(stage)["observer"]
        native._claim_collector_session(observer, session)
        assert session._used is True
        with pytest.raises(native.NativeAccountClockError, match="claim_invalid"):
            native._claim_collector_session(observer, session)
    assert not native._SESSION_CLAIMS and not native._STAGES


@pytest.mark.asyncio
async def test_foreign_task_or_other_session_cannot_adopt_claim(monkeypatch):
    session = _session()
    _no_io(monkeypatch, session)
    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin, scope_sha256="b" * 64, _claimed_session=session
        ) as stage,
    ):
        observer = native._state(stage)["observer"]

        async def foreign():
            with pytest.raises(native.NativeAccountClockError):
                native._claim_collector_session(observer, session)

        await asyncio.create_task(foreign())
        other = _session()
        with pytest.raises(native.NativeAccountClockError, match="claim_invalid"):
            native._claim_collector_session(observer, other)
        # Rejected foreign adoption does not consume the owner's only handoff.
        native._claim_collector_session(observer, session)
        assert other._used is False
    assert not native._SESSION_CLAIMS and not native._STAGES


@pytest.mark.asyncio
async def test_mutated_credentials_cannot_adopt_already_burned_session(monkeypatch):
    session = _session()
    _no_io(monkeypatch, session)
    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin, scope_sha256="b" * 64, _claimed_session=session
        ) as stage,
    ):
        original = session._credentials
        session._credentials = credentials(
            session_binding_id=session._plan.session_binding_id
        )
        with pytest.raises(native.NativeAccountClockError, match="claim_invalid"):
            native._claim_collector_session(native._state(stage)["observer"], session)
        session._credentials = original
        with pytest.raises(native.NativeAccountClockError, match="claim_invalid"):
            native._claim_collector_session(native._state(stage)["observer"], session)
    assert session._used is True and not native._SESSION_CLAIMS


@pytest.mark.asyncio
@pytest.mark.parametrize("observer", [None, {}, object()])
async def test_used_boolean_or_caller_observer_cannot_replace_private_claim(
    monkeypatch, observer
):
    session = _session()
    _no_io(monkeypatch, session)
    session._used = True
    with pytest.raises(AccountRuntimeError):
        await bootstrap._collect(
            session, None, lambda: None, None, _native_observer=observer
        )
    assert session._used is True and not native._SESSION_CLAIMS


@pytest.mark.asyncio
async def test_direct_acquisition_without_claim_is_denied_before_storage_or_host(
    monkeypatch,
):
    session = _session()
    _no_io(monkeypatch, session)
    session._used = True
    monkeypatch.setattr(
        runtime.storage,
        "_companion_attempt",
        lambda _: pytest.fail("unclaimed acquisition must not open storage"),
    )
    with (
        native._initial_stage(plan_sha256=session._pin, scope_sha256="b" * 64) as stage,
        pytest.raises(native.NativeAccountClockError, match="claim_invalid"),
    ):
        await runtime._capture_initial_current(stage, session, None, Path.cwd())
    assert not native._SESSION_CLAIMS and not native._STAGES


@pytest.mark.asyncio
async def test_changed_pin_cannot_run_foreign_comparison_or_renew_adoption(monkeypatch):
    session = _session()
    _no_io(monkeypatch, session)

    class ForeignPin:
        def __eq__(self, other):
            pytest.fail("foreign pin comparison must never run")

    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin, scope_sha256="b" * 64, _claimed_session=session
        ) as stage,
    ):
        pin = session._pin
        session._pin = ForeignPin()
        observer = native._state(stage)["observer"]
        with pytest.raises(native.NativeAccountClockError, match="claim_invalid"):
            native._claim_collector_session(observer, session)
        session._pin = pin
        with pytest.raises(native.NativeAccountClockError, match="claim_invalid"):
            native._claim_collector_session(observer, session)
    assert session._used is True and not native._SESSION_CLAIMS
