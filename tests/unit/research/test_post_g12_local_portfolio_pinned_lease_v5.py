"""Opt-in V5 Demo read lease continuity; synthetic, with no HTTP or orders."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from app.domain.source_primitives import decode, sha
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import post_g12_local_portfolio_observation_v5 as bridge
from tests.unit.research.test_post_g12_account_event_pinned_lease_v4 import _pinned
from tests.unit.research.test_post_g12_local_portfolio_observation_v5 import (
    _invoke_kwargs,
    _setup,
)


def _bind_used_v4(monkeypatch, session):
    original = prior.publish_capture_inspect_history_event_v4

    async def issue(*args, **kwargs):
        result = await original(*args, **kwargs)
        session._used = True
        return result

    monkeypatch.setattr(prior, "publish_capture_inspect_history_event_v4", issue)


@pytest.mark.asyncio
async def test_pinned_v5_valid_lease_remains_deny_and_reads_both_receipts(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    with _pinned() as (session, _, _):
        _bind_used_v4(monkeypatch, session)
        result = await bridge.publish_capture_inspect_local_portfolio_v5_pinned(
            *roots,
            object(),
            **{**_invoke_kwargs(roots, ledger, scope), "account_session": session},
        )
    assert calls == ["new_v4", "local_read", "local_read"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert (roots[6] / "receipt.json").read_bytes() == result.receipt_json
    assert decode(result.receipt_json)["code"] == "local_state_stable_not_authority"
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("lookalike", "credential", "origin", "closed"))
async def test_pinned_v5_invalid_lease_rejects_before_delegate(monkeypatch, change):
    called = []

    async def forbidden(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("V5 must not start with an invalid read lease")

    monkeypatch.setattr(bridge, "publish_capture_inspect_local_portfolio_v5", forbidden)

    async def require_denial(candidate):
        with pytest.raises(
            bridge.PostG12LocalPortfolioObservationError,
            match="^v5_pinned_account_session_invalid$",
        ):
            await bridge.publish_capture_inspect_local_portfolio_v5_pinned(
                account_session=candidate
            )

    with _pinned() as (session, lease, settings):
        if change == "lookalike":

            class Lookalike:
                _used = False
                _read_session_required = True
                _plan = session._plan
                _pin = session._pin

                def _checked_read_session(self, _plan):
                    return lease

            candidate = Lookalike()
        else:
            candidate = session
            if change == "credential":
                settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
            elif change == "origin":
                settings.okx_demo_rest_base_url = "https://us.okx.com"
        if change != "closed":
            await require_denial(candidate)
    if change == "closed":
        await require_denial(candidate)
    assert called == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint,change",
    ((1, "revoked"), (2, "credential"), (2, "origin")),
)
async def test_pinned_v5_late_lease_change_rejects_and_retains_both_receipts(
    tmp_path, monkeypatch, checkpoint, change
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    original = ledger.read_portfolio_checkpoint
    attempts = 0

    async def stalled(read_scope):
        nonlocal attempts
        attempts += 1
        if attempts == checkpoint:
            started.set()
            await release.wait()
        return await original(read_scope)

    monkeypatch.setattr(ledger, "read_portfolio_checkpoint", stalled)
    with _pinned() as (session, lease, settings):
        _bind_used_v4(monkeypatch, session)
        task = asyncio.create_task(
            bridge.publish_capture_inspect_local_portfolio_v5_pinned(
                *roots,
                object(),
                **{
                    **_invoke_kwargs(roots, ledger, scope),
                    "account_session": session,
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        if change == "revoked":
            lease._lease.active = False
        elif change == "credential":
            settings.okx_demo_api_key = SecretStr("synthetic-rotated-key")
        else:
            settings.okx_demo_rest_base_url = "https://us.okx.com"
        release.set()
        with pytest.raises(
            bridge.PostG12LocalPortfolioObservationError,
            match="^v5_pinned_account_session_invalid$",
        ):
            await task
    assert calls == ["new_v4", "local_read", "local_read"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    retained = (roots[6] / "receipt.json").read_bytes()
    identity = bridge.source_runtime._native_recheck_root_identity(roots[6])
    assert (
        bridge.read_post_g12_local_portfolio_observation_v5(
            roots[6], expected_sha256=sha(retained), expected_root_identity=identity
        ).receipt_json
        == retained
    )
    assert decode(retained)["code"] == "local_state_stable_not_authority"


@pytest.mark.asyncio
async def test_pinned_v5_cancellation_during_second_checkpoint_propagates(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    original = ledger.read_portfolio_checkpoint
    attempts = 0

    async def stalled(read_scope):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            started.set()
            await release.wait()
        return await original(read_scope)

    monkeypatch.setattr(ledger, "read_portfolio_checkpoint", stalled)
    with _pinned() as (session, _, _):
        _bind_used_v4(monkeypatch, session)
        task = asyncio.create_task(
            bridge.publish_capture_inspect_local_portfolio_v5_pinned(
                *roots,
                object(),
                **{
                    **_invoke_kwargs(roots, ledger, scope),
                    "account_session": session,
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == ["new_v4", "local_read"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert not list(roots[6].iterdir())


@pytest.mark.asyncio
async def test_pinned_v5_rejects_v4_phase_lease_revocation_and_retains_receipts(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    original = prior.publish_capture_inspect_history_event_v4
    with _pinned() as (session, lease, _):

        async def revoked_during_v4(*args, **kwargs):
            result = await original(*args, **kwargs)
            session._used = True
            lease._lease.active = False
            return result

        monkeypatch.setattr(
            prior, "publish_capture_inspect_history_event_v4", revoked_during_v4
        )
        with pytest.raises(
            bridge.PostG12LocalPortfolioObservationError,
            match="^v5_pinned_account_session_invalid$",
        ):
            await bridge.publish_capture_inspect_local_portfolio_v5_pinned(
                *roots,
                object(),
                **{
                    **_invoke_kwargs(roots, ledger, scope),
                    "account_session": session,
                },
            )
    assert calls == ["new_v4", "local_read", "local_read"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    retained = (roots[6] / "receipt.json").read_bytes()
    assert decode(retained)["admission"] == "DENY"


@pytest.mark.asyncio
async def test_pinned_v5_rejects_delegate_that_did_not_consume_session(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    with (
        _pinned() as (session, _, _),
        pytest.raises(
            bridge.PostG12LocalPortfolioObservationError,
            match="^v5_pinned_account_session_invalid$",
        ),
    ):
        await bridge.publish_capture_inspect_local_portfolio_v5_pinned(
            *roots,
            object(),
            **{
                **_invoke_kwargs(roots, ledger, scope),
                "account_session": session,
            },
        )
    assert calls == ["new_v4", "local_read", "local_read"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert decode((roots[6] / "receipt.json").read_bytes())["admission"] == "DENY"


@pytest.mark.asyncio
async def test_pinned_v5_requires_exact_delegate_result(monkeypatch):
    with _pinned() as (session, _, _):

        async def wrong_result(*args, **kwargs):
            assert kwargs["account_session"] is session
            session._used = True
            return object()

        monkeypatch.setattr(
            bridge, "publish_capture_inspect_local_portfolio_v5", wrong_result
        )
        with pytest.raises(
            bridge.PostG12LocalPortfolioObservationError,
            match="^v5_pinned_result_invalid$",
        ):
            await bridge.publish_capture_inspect_local_portfolio_v5_pinned(
                account_session=session
            )
