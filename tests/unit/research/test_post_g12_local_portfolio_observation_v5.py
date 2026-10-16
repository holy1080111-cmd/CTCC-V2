"""V5 observes stable local storage after a newly published V4, always DENY."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.database.repositories.qualification_ledger import (
    PortfolioLocalCheckpoint,
    QualificationLedgerRepository,
)
from app.domain.source_primitives import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
    utc_from_ns,
)
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import post_g12_local_portfolio_observation_v5 as bridge
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.reservations import (
    LedgerScope,
    LedgerScopeState,
    reservation_id,
)
from tests.unit.research import test_post_g12_account_event_observation_v4 as v4_fixture
from tests.unit.research.test_post_g12_account_event_observation_v4 import _matched


@pytest.fixture(scope="module")
def source_inputs_fixture():
    return v4_fixture.source_inputs_fixture.__wrapped__()


@pytest.fixture(scope="module")
async def captured_public_journal(source_inputs_fixture, tmp_path_factory):
    return await v4_fixture.captured_public_journal.__wrapped__(
        source_inputs_fixture, tmp_path_factory
    )


@pytest.fixture(scope="module")
async def captured_fresh_public_journal(source_inputs_fixture, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        clock, directory, harness, publications = v4_fixture.public_fixture.setup(
            patch, source_inputs_fixture[0]
        )
        v4_fixture.public_fixture.fresh_funding_return(harness, clock)
        result = await v4_fixture.public_fixture.invoke(
            tmp_path_factory.mktemp("v5-public-fresh-funding")
        )
        assert result.packet is not None and result.journal_sha256 is not None
        harness.assert_closed()
        assert not publications
        v4_fixture.public_fixture.empty_registries()
        return result, dict(directory.content)


def _roots(tmp_path):
    roots = [
        tmp_path / name
        for name in ("g12", "public", "account", "recheck", "join", "event", "local")
    ]
    for root in roots:
        root.mkdir()
    return roots


def _checkpoint(scope, at, *, reasons=(), account_id=None):
    fixed = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    state = LedgerScopeState(
        scope=scope,
        account_revision=0,
        ledger_revision=0,
        claims_sha256=None,
        active=(),
    )
    raw = canonical(
        {
            "schema_version": "ctcc.portfolio_local_checkpoint.v2",
            "environment": scope.environment,
            "account_id": scope.account_id if account_id is None else account_id,
            "requested_settlement_currency": scope.settlement_currency,
            "currency_states": [state.model_dump(mode="json")],
            "unresolved_intents": [],
            "legacy": {
                "automation": {
                    "armed": False,
                    "emergency_stop": False,
                    "locked": False,
                    "lock_reasons": [],
                    "active_instrument_id": None,
                    "active_client_order_id": None,
                    "active_start_equity": None,
                    "active_started_at": None,
                    "active_trades": {},
                    "last_started_at": None,
                    "last_completed_at": None,
                    "last_error": None,
                    "updated_at": fixed,
                },
                "sync_checkpoint": {
                    "status": "reconciled",
                    "order_count": 0,
                    "position_count": 0,
                    "algo_order_count": 0,
                    "last_error": None,
                    "reconciled_at": fixed,
                    "updated_at": fixed,
                    "exact_uid_bound": True,
                    "credential_session_binding_id": "c" * 64,
                },
                "positions": [],
                "orders": [],
                "algos": [],
            },
            "blocking_reasons": list(reasons),
            "future_writer_exclusion": False,
            "execution_authority": False,
        }
    )
    return PortfolioLocalCheckpoint(raw, at, at + timedelta(microseconds=10))


def _v4(scope, deadline, *, received=None, event_recorded=False, public_pins=None):
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
    if public_pins is not None:
        pins["public_packet_sha256"] = public_pins[0].packet.bundle_sha256
        pins["public_journal_sha256"] = public_pins[0].journal_sha256
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


@pytest.mark.parametrize(
    "wall_elapsed_ns,monotonic_elapsed_ns",
    ((-1, 1), (60_000_000_001, 60_000_000_001)),
)
def test_v5_public_capture_pin_rejects_clock_rollback_or_expired_lease(
    wall_elapsed_ns, monotonic_elapsed_ns
):
    start_ns = 1_700_000_000_000_000_000
    public_stamp = {"utc_ns": start_ns, "monotonic_ns": 1_000_000_000}
    final_stamp = {
        "utc_ns": start_ns + wall_elapsed_ns,
        "monotonic_ns": public_stamp["monotonic_ns"] + monotonic_elapsed_ns,
    }
    with pytest.raises(PublicReceiptError):
        prior.joined.prior._monotonic_final_freshness_at(public_stamp, final_stamp)


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
    late_public_g1=False,
    late_transport_profile_stale=False,
    late_economics_quote_stale=False,
    late_monotonic_quote_stale=False,
    late_public_journal_error=False,
    final_clock_skew_ns=0,
    g1_checks=None,
    economics_checks=None,
    deadline_override=None,
    v4_received=None,
    first_checkpoint_at=None,
    second_checkpoint_at=None,
    validation_at=None,
    public_journal=None,
):
    roots = _roots(tmp_path)
    if public_journal is not None:
        for name, payload in public_journal[1].items():
            (roots[1] / name).write_bytes(payload)
    scope = LedgerScope(account_id="123456789012345678901", settlement_currency="USDT")
    deadline = deadline_override or datetime.now(UTC) + timedelta(seconds=30)
    validation_at = validation_at or deadline - timedelta(milliseconds=100)
    public_stamp = (
        decode(public_journal[1]["summary.json"])["completed"]
        if public_journal is not None
        else {
            "utc_ns": int((validation_at - timedelta(seconds=1)).timestamp())
            * 1_000_000_000
            + validation_at.microsecond * 1000,
            "monotonic_ns": 1_000_000_000,
        }
    )
    elapsed = validation_at - utc_from_ns(public_stamp["utc_ns"])
    elapsed_ns = (
        (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    ) * 1000
    monkeypatch.setattr(
        bridge,
        "native_stamp",
        lambda: {
            "utc_ns": int(validation_at.timestamp()) * 1_000_000_000
            + validation_at.microsecond * 1_000,
            "monotonic_ns": public_stamp["monotonic_ns"]
            + elapsed_ns
            + final_clock_skew_ns,
        },
    )
    ledger = QualificationLedgerRepository(None, clock=lambda: deadline)
    calls = []
    receipt, observed = _v4(
        scope,
        deadline,
        received=v4_received,
        event_recorded=event_recorded,
        public_pins=public_journal,
    )
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

    def frozen_original(*_args):
        assert not calls
        return (
            object(),
            SimpleNamespace(policy=SimpleNamespace(economics=object())),
            object(),
        )

    def frozen_binding(_pre, _inputs, _session, _scope, _capture_id):
        assert not calls and _scope == scope
        return {
            "account_scope_sha256": receipt.scope_sha256,
            "candidate_sha256": receipt.candidate_sha256,
            "original_event_key": receipt.original_event_key,
            "report_id": receipt.report_id,
            "account_plan_sha256": receipt.account_plan_sha256,
            "deadline": deadline,
        }

    def pinned_public(_root, value, *, root_identity, with_stamp=False):
        if late_public_journal_error:
            raise prior.PostG12AccountEventObservationError(
                "v4_public_journal_readback_failed"
            )
        assert _root == roots[1]
        assert root_identity == bridge.source_runtime._native_recheck_root_identity(
            roots[1]
        )
        assert value["public_packet_sha256"] == receipt.public_packet_sha256
        assert value["public_journal_sha256"] == receipt.public_journal_sha256
        packet = SimpleNamespace(bundle_sha256=receipt.public_packet_sha256)
        return (packet, public_stamp) if with_stamp else packet

    def final_public_g1(_packet, _pre, observed_at):
        if g1_checks is not None:
            g1_checks.append(observed_at)
        if late_public_g1 and observed_at > deadline - timedelta(milliseconds=200):
            raise prior.joined.prior.PostG12AccountJoinError(
                "post_g12_final_original_g1_rejected"
            )

    monkeypatch.setattr(prior.joined, "_original", frozen_original)
    monkeypatch.setattr(prior, "_checked_original_binding", frozen_binding)
    monkeypatch.setattr(prior, "_read_bound_public_journal", pinned_public)
    monkeypatch.setattr(
        prior.joined.prior, "_require_final_original_g1", final_public_g1
    )
    transport_checks = []

    def final_transport_profile(packet, *, expected_bundle_sha256, evaluated_at):
        transport_checks.append(evaluated_at)
        assert packet.bundle_sha256 == expected_bundle_sha256
        if late_transport_profile_stale:
            raise ValueError("synthetic late transport rejection")

    monkeypatch.setattr(
        bridge.current_economics,
        "public_market_context_v2",
        final_transport_profile,
    )
    observed_economics_checks = []

    def final_economics_age(_packet, _policy, observed_at):
        observed_economics_checks.append(observed_at)
        if economics_checks is not None:
            economics_checks.append(observed_at)
        if late_monotonic_quote_stale:
            return observed_at < validation_at + timedelta(milliseconds=2)
        return not late_economics_quote_stale

    monkeypatch.setattr(
        bridge.current_economics,
        "quote_within_economics_policy_age_v2",
        final_economics_age,
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
            else (first_checkpoint_at or deadline - timedelta(milliseconds=300))
            if count == 1
            else (second_checkpoint_at or deadline - timedelta(milliseconds=150)),
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
    assert record["schema_version"] == bridge._CLOCK_SCHEMA
    proof = record["final_clock_proof"]
    assert proof["public_journal_sha256"] == record["public_journal_sha256"]
    cutoff = bridge.final_clock_proof.replay_proof(
        proof,
        expected_public_journal_sha256=record["public_journal_sha256"],
    )
    assert (
        bridge._utc_text(record["second_received_at"])
        <= cutoff
        < bridge._utc_text(record["candidate_deadline_at"])
    )
    with pytest.raises(bridge.PostG12LocalPortfolioObservationError):
        bridge.PostG12LocalPortfolioObservationV5(
            canonical({**record, "candidate_deadline_at": cutoff.isoformat()})
        )
    forged = {
        **record,
        "final_clock_proof": {
            **proof,
            "effective_cutoff_at": "1970-01-01T00:00:00+00:00",
        },
    }
    with pytest.raises(bridge.PostG12LocalPortfolioObservationError):
        bridge.PostG12LocalPortfolioObservationV5(canonical(forged))
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
    assert record["local_blocking_reasons"] == []
    assert result.execution_authority is False


@pytest.mark.asyncio
async def test_v5_later_checkpoint_rejects_stale_original_public_g1(
    tmp_path, monkeypatch
):
    g1_checks = []
    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch, tmp_path, late_public_g1=True, g1_checks=g1_checks
    )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("stale public data reached a ledger write")

    monkeypatch.setattr(ledger, "reserve", forbidden)
    monkeypatch.setattr(ledger, "reserve_control_bound", forbidden)
    monkeypatch.setattr(ledger, "consume_with_submission_intent", forbidden)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert v4.code == "trusted_execution_inputs_missing"
    assert g1_checks == [
        datetime.fromisoformat(record["candidate_deadline_at"])
        - timedelta(milliseconds=100)
    ]
    assert g1_checks[0] < datetime.fromisoformat(record["candidate_deadline_at"])
    assert record["code"] == "public_g1_rejected_at_local_readback"
    assert record["admission"] == "DENY" and result.execution_authority is False
    assert record["atomic_risk_reserved"] is False
    assert record["durable_intent_created"] is False
    assert record["order_submitted"] is False


@pytest.mark.asyncio
async def test_v5_later_checkpoint_rejects_original_cost_quote_age(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch, tmp_path, late_economics_quote_stale=True
    )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("stale cost quote reached a ledger write")

    monkeypatch.setattr(ledger, "reserve", forbidden)
    monkeypatch.setattr(ledger, "reserve_control_bound", forbidden)
    monkeypatch.setattr(ledger, "consume_with_submission_intent", forbidden)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert record["code"] == "public_economics_quote_stale_at_local_readback"
    assert record["v4_receipt_sha256"] == v4.receipt_sha256
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert all(record[field] is False for field in bridge._FALSE_FIELDS)
    assert result.admission == "DENY"


@pytest.mark.asyncio
async def test_v5_monotonic_floor_prevents_late_cost_quote_resurrection(
    tmp_path, monkeypatch
):
    economics_checks = []
    roots, scope, ledger, calls, _ = _setup(
        monkeypatch,
        tmp_path,
        late_monotonic_quote_stale=True,
        final_clock_skew_ns=4_000_000,
        economics_checks=economics_checks,
    )
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    deadline = datetime.fromisoformat(record["candidate_deadline_at"])
    assert calls == ["new_v4", "local_read", "local_read"]
    assert economics_checks == [deadline - timedelta(milliseconds=96)]
    assert record["code"] == "public_economics_quote_stale_at_local_readback"
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.asyncio
async def test_v5_invalid_final_clock_pair_denies_before_public_policy(
    tmp_path, monkeypatch
):
    g1_checks = []
    roots, scope, ledger, calls, _ = _setup(
        monkeypatch,
        tmp_path,
        final_clock_skew_ns=6_000_000,
        g1_checks=g1_checks,
    )
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert g1_checks == []
    assert record["code"] == "public_validation_clock_invalid_at_local_readback"
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.asyncio
async def test_v5_later_checkpoint_rejects_public_transport_profile(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch, tmp_path, late_transport_profile_stale=True
    )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("stale public transport reached a ledger write")

    monkeypatch.setattr(ledger, "reserve", forbidden)
    monkeypatch.setattr(ledger, "reserve_control_bound", forbidden)
    monkeypatch.setattr(ledger, "consume_with_submission_intent", forbidden)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert record["code"] == "public_transport_profile_rejected_at_local_readback"
    assert record["v4_receipt_sha256"] == v4.receipt_sha256
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert all(record[field] is False for field in bridge._FALSE_FIELDS)
    assert result.admission == "DENY"


@pytest.mark.asyncio
async def test_v5_late_public_journal_unreadable_is_distinct_denial(
    tmp_path, monkeypatch
):
    g1_checks = []
    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch,
        tmp_path,
        late_public_journal_error=True,
        g1_checks=g1_checks,
    )
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert g1_checks == []
    assert record["code"] == "public_journal_readback_invalid_at_local_readback"
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert (roots[5] / "observation.json").exists()
    assert all(record[field] is False for field in bridge._FALSE_FIELDS)
    assert result.admission == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_fault", ("deadline_elapsed", "stamp_unavailable"))
async def test_v5_late_validation_clock_failure_precedes_original_g1(
    tmp_path, monkeypatch, clock_fault
):
    g1_checks = []
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path, g1_checks=g1_checks)
    deadline = datetime.fromisoformat(v4.candidate_deadline_at)

    def validation_stamp():
        if clock_fault == "stamp_unavailable":
            raise RuntimeError("private native clock detail")
        expired = deadline + timedelta(milliseconds=1)
        return {
            "utc_ns": int(expired.timestamp()) * 1_000_000_000
            + expired.microsecond * 1_000
        }

    async def forbidden(*_args, **_kwargs):
        pytest.fail("invalid validation time reached a ledger write")

    monkeypatch.setattr(bridge, "native_stamp", validation_stamp)
    monkeypatch.setattr(ledger, "reserve", forbidden)
    monkeypatch.setattr(ledger, "reserve_control_bound", forbidden)
    monkeypatch.setattr(ledger, "consume_with_submission_intent", forbidden)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots, object(), **_invoke_kwargs(roots, ledger, scope)
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert g1_checks == []
    assert record["code"] == "public_validation_clock_invalid_at_local_readback"
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert all(record[field] is False for field in bridge._FALSE_FIELDS)
    assert result.admission == "DENY"


@pytest.mark.asyncio
async def test_v5_real_pinned_public_packet_expires_before_candidate(
    tmp_path, monkeypatch, source_inputs_fixture, captured_public_journal
):
    source, values, run = source_inputs_fixture
    packet = captured_public_journal[0].packet
    completed = datetime.fromisoformat(decode(packet.packet_json)["completed_at"])
    t1 = completed + timedelta(seconds=1)
    t2 = completed + timedelta(seconds=11)
    original = prior.joined._original
    read_journal = prior._read_bound_public_journal
    require_g1 = prior.joined.prior._require_final_original_g1
    _, pre, _ = original(source.market, run, values)
    require_g1(packet, pre, t1)
    deadline = min(
        pre.prefix.intent.expires_at,
        pre.prefix.detection.trigger.expires_at,
        pre.result.entry_zone.expires_at,
        pre.prefix.timing.latest_valid_entry_time,
    )
    assert t1 < t2 < deadline
    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch,
        tmp_path,
        deadline_override=deadline,
        v4_received=completed + timedelta(seconds=3),
        first_checkpoint_at=completed + timedelta(seconds=5),
        second_checkpoint_at=completed + timedelta(seconds=10),
        validation_at=t2,
        public_journal=captured_public_journal,
    )
    g1_times = []

    def observed_g1(packet_value, pre_value, observed_at):
        g1_times.append(observed_at)
        return require_g1(packet_value, pre_value, observed_at)

    monkeypatch.setattr(prior.joined, "_original", original)
    monkeypatch.setattr(prior, "_read_bound_public_journal", read_journal)
    monkeypatch.setattr(prior.joined.prior, "_require_final_original_g1", observed_g1)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots,
        source.market,
        **{
            **_invoke_kwargs(roots, ledger, scope),
            "run": run,
            "original_inputs": values,
        },
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    assert g1_times == [t2]
    assert record["code"] == "public_g1_rejected_at_local_readback"
    assert record["candidate_deadline_at"] == deadline.isoformat()
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert all(record[field] is False for field in bridge._FALSE_FIELDS)
    assert result.admission == "DENY"


@pytest.mark.asyncio
async def test_v5_real_pinned_transport_and_economics_reach_local_denial(
    tmp_path, monkeypatch, source_inputs_fixture, captured_fresh_public_journal
):
    source, values, run = source_inputs_fixture
    packet = captured_fresh_public_journal[0].packet
    completed = datetime.fromisoformat(decode(packet.packet_json)["completed_at"])
    validation_at = completed + timedelta(seconds=1)
    original = prior.joined._original
    read_journal = prior._read_bound_public_journal
    require_g1 = prior.joined.prior._require_final_original_g1
    real_profile = bridge.current_economics.public_market_context_v2
    real_quote_age = bridge.current_economics.quote_within_economics_policy_age_v2
    _, pre, _ = original(source.market, run, values)
    deadline = min(
        pre.prefix.intent.expires_at,
        pre.prefix.detection.trigger.expires_at,
        pre.result.entry_zone.expires_at,
        pre.prefix.timing.latest_valid_entry_time,
    )
    assert validation_at < deadline
    require_g1(packet, pre, validation_at)
    context = real_profile(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        evaluated_at=validation_at,
    )
    assert context.packet_sha256 == packet.bundle_sha256
    assert real_quote_age(packet, pre.policy.economics, validation_at)

    roots, scope, ledger, calls, v4 = _setup(
        monkeypatch,
        tmp_path,
        reasons=("legacy_reconciliation_incomplete_or_exposed",),
        deadline_override=deadline,
        v4_received=completed + timedelta(milliseconds=200),
        first_checkpoint_at=completed + timedelta(milliseconds=400),
        second_checkpoint_at=completed + timedelta(milliseconds=600),
        validation_at=validation_at,
        public_journal=captured_fresh_public_journal,
    )
    profile_spy = Mock(wraps=real_profile)
    quote_age_spy = Mock(wraps=real_quote_age)
    monkeypatch.setattr(prior.joined, "_original", original)
    monkeypatch.setattr(prior, "_read_bound_public_journal", read_journal)
    monkeypatch.setattr(prior.joined.prior, "_require_final_original_g1", require_g1)
    monkeypatch.setattr(
        bridge.current_economics, "public_market_context_v2", profile_spy
    )
    monkeypatch.setattr(
        bridge.current_economics, "quote_within_economics_policy_age_v2", quote_age_spy
    )
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots,
        source.market,
        **{
            **_invoke_kwargs(roots, ledger, scope),
            "run": run,
            "original_inputs": values,
        },
    )
    record = decode(result.receipt_json)
    assert calls == ["new_v4", "local_read", "local_read"]
    profile_spy.assert_called_once_with(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        evaluated_at=validation_at,
    )
    quote_age_spy.assert_called_once_with(packet, pre.policy.economics, validation_at)
    assert (
        record["public_journal_sha256"]
        == captured_fresh_public_journal[0].journal_sha256
    )
    assert record["code"] == "local_state_blocked"
    assert record["local_flat_at_reads"] is False
    assert result.admission == "DENY" and result.execution_authority is False
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json


@pytest.mark.asyncio
async def test_v5_changed_original_candidate_pin_stops_before_local_reads(
    tmp_path, monkeypatch
):
    roots, scope, ledger, calls, v4 = _setup(monkeypatch, tmp_path)
    frozen_binding = prior._checked_original_binding

    def changed_binding(*args):
        return {**frozen_binding(*args), "candidate_sha256": "f" * 64}

    monkeypatch.setattr(prior, "_checked_original_binding", changed_binding)
    with pytest.raises(
        bridge.PostG12LocalPortfolioObservationError, match="v5_v4_scope_mismatch"
    ):
        await bridge.publish_capture_inspect_local_portfolio_v5(
            *roots, object(), **_invoke_kwargs(roots, ledger, scope)
        )
    assert calls == ["new_v4"]
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
    assert not list(roots[6].iterdir())


@pytest.mark.parametrize(
    "fault",
    (
        "active_without_reason",
        "intent_without_reason",
        "legacy_missing",
        "legacy_exposure_without_reason",
        "legacy_inflight_chronology_without_reason",
        "binding_missing_without_reason",
        "binding_malformed_without_reason",
        "currency_fields_missing",
    ),
)
def test_v5_rejects_nested_checkpoint_contradiction_or_omission(fault):
    scope = LedgerScope(account_id="123456789012345678901", settlement_currency="USDT")
    original = _checkpoint(scope, datetime.now(UTC))
    record = decode(original.document_json)
    if fault == "active_without_reason":
        receipt = _matched(
            scope,
            "a" * 64,
            {"deadline": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=30)},
        )
        record["currency_states"][0]["active"] = [receipt.model_dump(mode="json")]
        record["currency_states"][0]["account_revision"] = 1
        record["currency_states"][0]["ledger_revision"] = 2
    elif fault == "intent_without_reason":
        record["unresolved_intents"] = [["b" * 64, "USDT", "consumed", 2, "submitted"]]
    elif fault == "legacy_missing":
        record["legacy"] = {}
    elif fault == "legacy_exposure_without_reason":
        record["legacy"]["positions"] = ["position-1"]
    elif fault == "legacy_inflight_chronology_without_reason":
        record["legacy"]["automation"]["last_started_at"] = "2026-01-01T00:00:02+00:00"
        record["legacy"]["automation"]["last_completed_at"] = (
            "2026-01-01T00:00:01+00:00"
        )
    elif fault == "binding_missing_without_reason":
        record["legacy"]["sync_checkpoint"]["credential_session_binding_id"] = None
    elif fault == "binding_malformed_without_reason":
        record["legacy"]["sync_checkpoint"]["credential_session_binding_id"] = (
            "not-a-binding"
        )
    else:
        record["currency_states"] = [{"scope": scope.model_dump(mode="json")}]
    forged = PortfolioLocalCheckpoint(
        canonical(record), original.observed_at, original.received_at
    )
    with pytest.raises(
        bridge.PostG12LocalPortfolioObservationError,
        match="v5_local_checkpoint_invalid",
    ):
        bridge._checked_checkpoint(forged, scope)


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
