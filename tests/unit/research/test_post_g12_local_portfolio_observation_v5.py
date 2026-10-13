"""V5 observes stable local storage after a newly published V4, always DENY."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.qualification_ledger import (
    PortfolioLocalCheckpoint,
    QualificationLedgerRepository,
)
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import post_g12_local_portfolio_observation_v5 as bridge
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.reservations import LedgerScope, reservation_id
from tests.unit.research.test_post_g12_account_event_observation_v4 import _matched


def _roots(tmp_path):
    roots = [
        tmp_path / name
        for name in ("g12", "public", "account", "recheck", "join", "event", "local")
    ]
    for root in roots:
        root.mkdir()
    return roots


def _checkpoint(scope, at, *, reasons=(), account_id=None):
    raw = canonical(
        {
            "schema_version": "ctcc.portfolio_local_checkpoint.v2",
            "environment": scope.environment,
            "account_id": scope.account_id if account_id is None else account_id,
            "requested_settlement_currency": scope.settlement_currency,
            "currency_states": [{"settlement_currency": scope.settlement_currency}],
            "unresolved_intents": [],
            "legacy": {},
            "blocking_reasons": list(reasons),
            "future_writer_exclusion": False,
            "execution_authority": False,
        }
    )
    return PortfolioLocalCheckpoint(raw, at, at + timedelta(microseconds=10))


def _v4(scope, deadline, *, received=None, event_recorded=False):
    key = "a" * 64
    received = deadline - timedelta(milliseconds=500) if received is None else received
    observed = LedgerEventObservation(
        contract_version=VERSION,
        scope=scope,
        original_event_key=key,
        account_revision=1 if event_recorded else 0,
        ledger_revision=2 if event_recorded else 0,
        matched=_matched(scope, key, {"deadline": deadline})
        if event_recorded
        else None,
        request_started_at=received - timedelta(milliseconds=100),
        observed_at=received - timedelta(milliseconds=50),
        received_at=received,
        monotonic_started_ns=100,
        monotonic_received_ns=150,
    )
    pins = {name: "b" * 64 for name in prior._SOURCE_PINS}
    receipt = prior.PostG12AccountEventObservationV4(
        code=(
            "original_event_already_recorded"
            if event_recorded
            else "trusted_execution_inputs_missing"
        ),
        report_id="synthetic-v5",
        scope_sha256=prior.digest(
            canonical([scope.environment, scope.account_id, scope.settlement_currency])
        ),
        account_uid_sha256=sha(scope.account_id.encode("ascii")),
        candidate_sha256="c" * 64,
        account_plan_sha256="d" * 64,
        original_event_key=key,
        reservation_id=reservation_id(scope, key),
        candidate_deadline_at=deadline.isoformat(),
        v3_observed_at=(received - timedelta(milliseconds=200)).isoformat(),
        account_db_readback_sha256="e" * 64,
        ledger_observation_sha256=observed.sha256,
        ledger_event_state="reserved" if event_recorded else "absent_at_read",
        **pins,
    )
    return receipt, observed


def _invoke_kwargs(roots, ledger, scope):
    return {
        "run": object(),
        "original_inputs": object(),
        "market_policy": object(),
        "account_session": object(),
        "session_factory": ledger.session_factory,
        "history_capture_id": "h" * 32,
        "ledger": ledger,
        "scope": scope,
    }


def _setup(
    monkeypatch,
    tmp_path,
    *,
    reasons=(),
    changed=False,
    wrong_uid=False,
    second_error=False,
    clock_late=False,
    v4_sidecar_tamper=False,
    v4_incomplete=False,
    event_recorded=False,
):
    roots = _roots(tmp_path)
    scope = LedgerScope(account_id="123456789012345678901", settlement_currency="USDT")
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    ledger = QualificationLedgerRepository(None, clock=lambda: deadline)
    calls = []
    receipt, observed = _v4(scope, deadline, event_recorded=event_recorded)
    if v4_incomplete:
        receipt = prior.PostG12AccountEventObservationV4(
            code="v3_binding_invalid",
            report_id=receipt.report_id,
            scope_sha256=receipt.scope_sha256,
            account_uid_sha256=receipt.account_uid_sha256,
            candidate_sha256=receipt.candidate_sha256,
            account_plan_sha256=receipt.account_plan_sha256,
            original_event_key=receipt.original_event_key,
            reservation_id=receipt.reservation_id,
            candidate_deadline_at=receipt.candidate_deadline_at,
        )

    async def issue(*args, **kwargs):
        calls.append("new_v4")
        assert args[5] == roots[5]
        assert kwargs["scope"] == scope
        prior._publish_receipt(
            roots[5],
            receipt,
            expected_root_identity=bridge.source_runtime._native_recheck_root_identity(
                roots[5]
            ),
            observation=None if v4_incomplete else observed,
        )
        if v4_sidecar_tamper:
            (roots[5] / "observation.json").write_bytes(b"invalid prior sidecar")
        return receipt

    async def checkpoint(read_scope):
        assert read_scope == scope
        calls.append("local_read")
        count = calls.count("local_read")
        if second_error and count == 2:
            raise RuntimeError("private SQL detail must not escape")
        return _checkpoint(
            scope,
            deadline + timedelta(milliseconds=1)
            if clock_late and count == 1
            else deadline - timedelta(milliseconds=300 if count == 1 else 150),
            reasons=("legacy_reconciliation_incomplete_or_exposed",)
            if changed and count == 2
            else reasons,
            account_id="999" if wrong_uid else None,
        )

    monkeypatch.setattr(prior, "publish_capture_inspect_history_event_v4", issue)
    monkeypatch.setattr(ledger, "read_portfolio_checkpoint", checkpoint)
    return roots, scope, ledger, calls, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reasons,expected",
    (
        ((), "local_state_stable_not_authority"),
        (("legacy_reconciliation_incomplete_or_exposed",), "local_state_blocked"),
    ),
)
async def test_v5_same_invocation_v4_readback_then_two_local_reads(
    tmp_path, monkeypatch, reasons, expected
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path, reasons=reasons)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    assert calls == ["new_v4", "local_read", "local_read"]
    record = decode(result.receipt_json)
    assert record["code"] == expected
    assert record["v4_receipt_sha256"] == v4.receipt_sha256
    assert record["first_checkpoint_sha256"] == record["second_checkpoint_sha256"]
    assert record["local_blocking_reasons"] == list(reasons)
    assert record["local_flat_at_reads"] is (not reasons)
    assert result.admission == "DENY" and result.execution_authority is False
    assert all(record[field] is False for field in bridge._FALSE_FIELDS)
    assert scope.account_id.encode("ascii") not in result.receipt_json
    identity = bridge.source_runtime._native_recheck_root_identity(roots[6])
    assert (
        bridge.read_post_g12_local_portfolio_observation_v5(
            roots[6],
            expected_sha256=result.receipt_sha256,
            expected_root_identity=identity,
        ).receipt_json
        == result.receipt_json
    )
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault,code",
    (
        ("changed", "v5_local_state_changed"),
        ("wrong_uid", "v5_local_checkpoint_invalid"),
        ("second_error", "v5_local_checkpoint_unavailable"),
        ("clock_late", "v5_local_checkpoint_clock_invalid"),
    ),
)
async def test_v5_local_uncertainty_retains_v4_and_never_publishes_v5(
    tmp_path, monkeypatch, fault, code
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path, **{fault: True})
    with pytest.raises(bridge.PostG12LocalPortfolioObservationError, match=code):
        await bridge.publish_capture_inspect_local_portfolio_v5(
            *roots, object(), **_invoke_kwargs(roots, ledger, scope)
        )
    assert calls == ["new_v4", "local_read", "local_read"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert not list(roots[6].iterdir())


@pytest.mark.asyncio
async def test_v5_prior_sidecar_tamper_stops_before_local_reads(tmp_path, monkeypatch):
    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch, tmp_path, v4_sidecar_tamper=True
    )
    with pytest.raises(
        prior.PostG12AccountEventObservationError, match="v4_readback_failed"
    ):
        await bridge.publish_capture_inspect_local_portfolio_v5(
            *roots, object(), **_invoke_kwargs(roots, ledger, scope)
        )
    assert calls == ["new_v4"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert not list(roots[6].iterdir())


@pytest.mark.asyncio
async def test_v5_incomplete_v4_does_not_read_local_or_claim_flat(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path, v4_incomplete=True)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    assert calls == ["new_v4"]
    record = decode(result.receipt_json)
    assert record["code"] == "v4_incomplete"
    assert record["first_checkpoint_sha256"] is None
    assert record["local_flat_at_reads"] is False
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json


@pytest.mark.asyncio
async def test_v5_local_flat_cannot_requalify_recorded_original_event(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path, event_recorded=True)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert record["code"] == "original_event_already_recorded"
    assert record["v4_receipt_sha256"] == v4.receipt_sha256
    assert record["local_flat_at_reads"] is True
    assert result.execution_authority is False


@pytest.mark.asyncio
async def test_v5_old_local_receipt_rejected_before_new_v4(tmp_path, monkeypatch):
    roots, scope, ledger, calls, _ = _setup(monkeypatch, tmp_path)
    (roots[6] / "receipt.json").write_bytes(b"accepted old V5 receipt")
    with pytest.raises(
        bridge.PostG12LocalPortfolioObservationError, match="v5_local_root_unavailable"
    ):
        await bridge.publish_capture_inspect_local_portfolio_v5(
            *roots, object(), **_invoke_kwargs(roots, ledger, scope)
        )
    assert calls == []
    assert (roots[6] / "receipt.json").read_bytes() == b"accepted old V5 receipt"


@pytest.mark.asyncio
async def test_v5_late_publish_failure_keeps_both_journals(tmp_path, monkeypatch):
    roots, scope, ledger, _, v4 = _setup(monkeypatch, tmp_path)
    original = bridge.read_post_g12_local_portfolio_observation_v5

    def fail_readback(*_args, **_kwargs):
        raise ValueError("synthetic late readback")

    monkeypatch.setattr(
        bridge, "read_post_g12_local_portfolio_observation_v5", fail_readback
    )
    with pytest.raises(
        bridge.PostG12LocalPortfolioObservationError, match="v5_publish_failed"
    ):
        await bridge.publish_capture_inspect_local_portfolio_v5(
            *roots, object(), **_invoke_kwargs(roots, ledger, scope)
        )
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    retained = (roots[6] / "receipt.json").read_bytes()
    assert retained
    monkeypatch.setattr(
        bridge, "read_post_g12_local_portfolio_observation_v5", original
    )
    assert (
        bridge.read_post_g12_local_portfolio_observation_v5(
            roots[6],
            expected_sha256=sha(retained),
            expected_root_identity=bridge.source_runtime._native_recheck_root_identity(
                roots[6]
            ),
        ).receipt_json
        == retained
    )


def test_v5_readback_rejects_tampered_authority_and_flat_claim(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    identity = bridge.source_runtime._native_recheck_root_identity(root)
    value = {
        "schema_version": bridge._SCHEMA,
        "code": "v4_incomplete",
        "v4_code": "v3_incomplete",
        "v4_receipt_sha256": "a" * 64,
        "scope_sha256": "b" * 64,
        "account_uid_sha256": "c" * 64,
        "candidate_sha256": "d" * 64,
        "original_event_key": "e" * 64,
        "candidate_deadline_at": "2026-10-13T00:00:00+00:00",
        "v4_event_read_received_at": None,
        "first_checkpoint_sha256": None,
        "second_checkpoint_sha256": None,
        "first_observed_at": None,
        "first_received_at": None,
        "second_observed_at": None,
        "second_received_at": None,
        "local_blocking_reasons": [],
        "local_flat_at_reads": False,
        "admission": "DENY",
        **{name: False for name in bridge._FALSE_FIELDS},
    }
    original = bridge.PostG12LocalPortfolioObservationV5(canonical(value))
    bridge._publish_receipt(root, original, expected_root_identity=identity)
    for change in ({"execution_authority": True}, {"local_flat_at_reads": True}):
        forged = canonical({**value, **change})
        (root / "receipt.json").write_bytes(forged)
        with pytest.raises(
            bridge.PostG12LocalPortfolioObservationError, match="v5_readback_failed"
        ):
            bridge.read_post_g12_local_portfolio_observation_v5(
                root,
                expected_sha256=sha(forged),
                expected_root_identity=identity,
            )
    (root / "receipt.json").write_bytes(original.receipt_json)


@pytest.mark.asyncio
async def test_v5_cancellation_after_first_local_read_keeps_v4(tmp_path, monkeypatch):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    second_started = asyncio.Event()
    release = asyncio.Event()
    original = ledger.read_portfolio_checkpoint

    async def stalled(read_scope):
        if calls.count("local_read") == 1:
            second_started.set()
            await release.wait()
        return await original(read_scope)

    monkeypatch.setattr(ledger, "read_portfolio_checkpoint", stalled)
    task = asyncio.create_task(
        bridge.publish_capture_inspect_local_portfolio_v5(
            *roots, object(), **_invoke_kwargs(roots, ledger, scope)
        )
    )
    await asyncio.wait_for(second_started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert not list(roots[6].iterdir())
