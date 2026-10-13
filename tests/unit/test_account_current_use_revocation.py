"""Synthetic V7 current-use revocation; no account credential or order authority."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_current_use_revocation as revoked
from app.trade_qualification import account_native_clock as native_clock
from app.trade_qualification import account_native_proof as native_proof
from app.trade_qualification import account_native_runtime as native_runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_row_gateway_causality import recorded_with_gateway
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_collector import credentials


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing", "conflict", "reason", "gateway_complete", "row_ordered"),
    [
        (True, False, "exchange_gateway_time_missing", False, False),
        (
            False,
            True,
            "source_timestamp_after_gateway_response",
            True,
            False,
        ),
        (False, False, None, True, True),
    ],
)
async def test_original_v7_chain_has_only_a_denied_current_use_interpretation(
    monkeypatch, missing, conflict, reason, gateway_complete, row_ordered
):
    chain = await recorded_with_gateway(monkeypatch, omit=missing, conflict=conflict)
    reference = observed.source_reference(chain)
    at = NOW + timedelta(seconds=4)
    historical = current.verify_current_account_sources(
        chain,
        reference=reference,
        scope=SCOPE,
        validated_at=at,
        expected_policy_sha256=current.V7_POLICY_SHA256,
    )
    if not conflict:
        # Preserve the exact historical interpretation while withdrawing its
        # current-use significance, including when gateway clocks are absent.
        assert json.loads(historical.receipt_json)["observed_flat"] is True
    result = revoked.audit_v7_current_use_revocation(
        chain=chain, reference=reference, scope=SCOPE, validated_at=at
    )
    value = json.loads(result.receipt_json)
    assert value["historical_current_receipt_sha256"] == historical.receipt_sha256
    assert value["gateway_causal_coverage_complete"] is gateway_complete
    assert value["known_source_row_times_ordered"] is row_ordered
    assert "v7_current_flat_policy_revoked" in value["blocking_reasons"]
    if reason is not None:
        assert reason in value["blocking_reasons"]
    assert value["current_flat_qualified"] is result.current_flat_qualified is False
    assert value["flat_start_permission"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"
    assert (
        revoked.verify_v7_current_use_revocation(
            result,
            chain=chain,
            reference=reference,
            scope=SCOPE,
            validated_at=at,
            expected_receipt_sha256=result.receipt_sha256,
        ).receipt_json
        == result.receipt_json
    )
    with pytest.raises(
        revoked.AccountCurrentUseRevocationError,
        match="account_current_use_readback_mismatch",
    ):
        revoked.verify_v7_current_use_revocation(
            result,
            chain=chain,
            reference=reference,
            scope=SCOPE,
            validated_at=at,
            expected_receipt_sha256="0" * 64,
        )


def test_old_v7_native_contract_replays_but_current_issuance_is_revoked():
    plan = current_plan_v7()
    assert native_proof.contract_for_plan(plan) == (
        native_proof.V7_FLAT_SCHEMA,
        native_proof.V7_FLAT_POLICY_SHA256,
    )
    with pytest.raises(
        native_proof.NativeAccountProofError,
        match="native_account_v7_flat_current_use_revoked",
    ):
        native_proof.current_use_contract_for_plan(plan)
    assert json.loads(revoked.POLICY_BYTES)["current_flat_qualified"] is False


@pytest.mark.asyncio
async def test_new_v7_native_flat_issuer_rejects_before_any_account_get(monkeypatch):
    plan = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    samples = Samples()
    monkeypatch.setattr(native_clock.clock, "native_stamp", samples)
    monkeypatch.setattr(native_runtime, "_configured_factory", lambda _factory: True)
    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: pytest.fail("revoked V7 current use must not issue any account GET"),
    )
    seen = []
    original = native_proof.current_use_contract_for_plan

    def tracked(candidate):
        seen.append(candidate)
        return original(candidate)

    monkeypatch.setattr(native_proof, "current_use_contract_for_plan", tracked)
    result = await native_runtime.capture_initial_native_account(
        session, session_factory=object(), proof_root=Path.cwd()
    )
    value = json.loads(result.receipt_json)
    assert seen == [plan]
    assert value["current_native_source_observed"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"
    assert session._used is True


@pytest.mark.asyncio
async def test_v7_history_join_reaches_revocation_without_account_get(monkeypatch):
    plan = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    samples = Samples()
    monkeypatch.setattr(native_clock.clock, "native_stamp", samples)
    monkeypatch.setattr(native_runtime, "_configured_factory", lambda _factory: True)
    monkeypatch.setattr(
        collector,
        "_new_client",
        lambda: pytest.fail("revoked V7 history join must not issue account GET"),
    )
    seen = []
    original = native_proof.current_use_contract_for_plan

    def tracked(candidate):
        seen.append(candidate)
        return original(candidate)

    async def committed_history(*args, **kwargs):
        stage = args[0]
        stamp = native_clock._sample(stage)
        return native_runtime._CommittedHistoryPreRead(
            reference=None,
            db_chain_sha256="0" * 64,
            history_query_receipt_sha256="0" * 64,
            terminal_db_recorded_at=NOW,
            started=stamp,
            completed=stamp,
            verified=stamp,
        )

    monkeypatch.setattr(native_proof, "current_use_contract_for_plan", tracked)
    monkeypatch.setattr(
        native_runtime, "_read_committed_history_before_http", committed_history
    )
    result = await native_runtime.capture_native_current_history_join(
        session,
        session_factory=object(),
        proof_root=Path.cwd(),
        history_capture_id="a" * 32,
    )
    value = json.loads(result.receipt_json)
    assert seen == [plan]
    assert value["native_current_source_observed"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"


def test_caller_cannot_mint_a_current_use_receipt():
    with pytest.raises(
        revoked.AccountCurrentUseRevocationError,
        match="account_current_use_receipt_invalid",
    ):
        revoked.V7CurrentUseRevocation(b"{}")


@pytest.mark.asyncio
async def test_receipt_rejects_claimed_order_when_gateway_pair_is_missing(monkeypatch):
    chain = await recorded_with_gateway(monkeypatch, omit=True)
    result = revoked.audit_v7_current_use_revocation(
        chain=chain,
        reference=observed.source_reference(chain),
        scope=SCOPE,
        validated_at=NOW + timedelta(seconds=4),
    )
    original = json.loads(result.receipt_json)
    for changes in (
        {"known_source_row_times_ordered": True},
        {"gateway_causal_coverage_complete": True},
    ):
        with pytest.raises(
            revoked.AccountCurrentUseRevocationError,
            match="account_current_use_receipt_invalid",
        ):
            revoked.V7CurrentUseRevocation(
                canonical(original | changes), _issuer=revoked._ISSUER
            )
