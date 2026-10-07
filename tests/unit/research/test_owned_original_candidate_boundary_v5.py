"""Owned original preflight remains DENY before source-derived G1--G11/G12."""

import asyncio

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import native_original_g1_policy_v1 as g1_policy
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_candidate_boundary_v5 as boundary
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification import public_source_runtime
from tests.unit.research.test_owned_original_source_coordinator_v2 import session
from tests.unit.research.test_owned_public_runtime_v2 import policy


def _forbidden(*_args, **_kwargs):
    pytest.fail("original precursor must never reach G12 or network write")


def _inspected_precursor_receipt(plan_pin, *, with_intent=True):
    document = {
        "schema_version": "ctcc.original_owned_precursor_diagnostic.v4",
        "code": "original_precursor_inspected",
        "account_plan_sha256": plan_pin,
        "declared_demo_route_policy_sha256": "b" * 64,
        "public_report_id": "initial-synthetic-0000",
        "public_packet_sha256": "c" * 64,
        "public_journal_sha256": "d" * 64,
        "account_receipt_sha256": "e" * 64,
        "account_packet_sha256": "f" * 64,
        "observed_at": "2026-09-12T10:00:01+00:00",
        "g1_policy_record_sha256": g1_policy.POLICY_RECORD_SHA256,
        "g1_policy_sha256": g1_policy.DATA_POLICY_SHA256,
        "g1_source_sha256": "c" * 64,
        "g1_result_sha256": "1" * 64,
        "g1_result_code": "passed",
        "g1_evaluated_at": "2026-09-12T10:00:00+00:00",
        "g1_evaluated": True,
        "g1_passed": True,
        "precursor_policy_sha256": precursor.POLICY_SHA256,
        "precursor_receipt_sha256": "2" * 64,
        "precursor_intent_sha256": "3" * 64 if with_intent else None,
        "precursor_action": "WAIT",
        "precursor_code": "precursor_derived_remaining_dependencies_unverified",
        "precursor_intent_derived": with_intent,
        "candidate_created": False,
        "g1_g11_complete": False,
        "g12_published": False,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_recheck_performed": False,
        "atomic_risk_reserved": False,
        "execution_authority": False,
        "order_submitted": False,
        "admission": "DENY",
    }
    return original.InitialOwnedPrecursorDiagnosticV4(canonical(document))


@pytest.mark.asyncio
async def test_real_native_origin_refusal_stops_before_g12_and_source_io(
    tmp_path, monkeypatch
):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    monkeypatch.setattr(public_source_runtime, "_new_owned_client", _forbidden)
    monkeypatch.setattr(
        public_runtime, "publish_qualification_evidence_liquidity_v2", _forbidden
    )
    result = await boundary.preflight_owned_publish_and_recheck_v5(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "native_precursor_unavailable"
    assert receipt["public_packet_sha256"] is None
    assert receipt["account_packet_sha256"] is None
    assert receipt["precursor_intent_sha256"] is None
    assert receipt["original_diagnostic_sha256"] is not None
    assert result.receipt_sha256 == sha(result.receipt_json)
    assert result.admission == "DENY" and result.execution_authority is False
    assert all(receipt[name] is False for name in boundary._FALSE_FIELDS)
    assert controlled._used
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / "account").exists()
    forged = dict(receipt, g12_published=True)
    with pytest.raises(boundary.OwnedOriginalCandidateBoundaryError):
        boundary.OwnedOriginalCandidateBoundaryV5(canonical(forged))
    forged = dict(receipt, precursor_intent_sha256="3" * 64)
    with pytest.raises(boundary.OwnedOriginalCandidateBoundaryError):
        boundary.OwnedOriginalCandidateBoundaryV5(canonical(forged))


@pytest.mark.asyncio
async def test_even_inspected_precursor_stops_at_missing_g1_g11_without_g12(
    tmp_path, monkeypatch
):
    controlled = session()
    inspected = _inspected_precursor_receipt(controlled._pin)
    parent = asyncio.current_task()
    calls = []

    async def native_v4(*args, **kwargs):
        assert asyncio.current_task() is parent
        assert args == (tmp_path / "public", tmp_path / "account")
        assert kwargs["account_session"] is controlled
        controlled._used = True
        calls.append(1)
        return inspected

    monkeypatch.setattr(original, "capture_owned_original_precursor_v4", native_v4)
    monkeypatch.setattr(
        public_runtime, "publish_qualification_evidence_liquidity_v2", _forbidden
    )
    result = await boundary.preflight_owned_publish_and_recheck_v5(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert calls == [1]
    assert receipt["code"] == "g1_g11_source_inputs_unavailable"
    assert receipt["original_diagnostic_sha256"] == inspected.receipt_sha256
    assert receipt["precursor_intent_sha256"] == "3" * 64
    assert receipt["g12_published"] is False
    assert result.execution_authority is False
    assert not (tmp_path / "public").exists()


@pytest.mark.asyncio
async def test_inspected_precursor_without_intent_has_distinct_denial(
    tmp_path, monkeypatch
):
    controlled = session()

    async def native_v4(*_args, **_kwargs):
        controlled._used = True
        return _inspected_precursor_receipt(controlled._pin, with_intent=False)

    monkeypatch.setattr(original, "capture_owned_original_precursor_v4", native_v4)
    monkeypatch.setattr(
        public_runtime, "publish_qualification_evidence_liquidity_v2", _forbidden
    )
    result = await boundary.preflight_owned_publish_and_recheck_v5(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "precursor_no_intent"
    assert receipt["precursor_receipt_sha256"] is not None
    assert receipt["precursor_intent_sha256"] is None
    assert receipt["g12_published"] is False
    forged = dict(receipt, precursor_intent_sha256="3" * 64)
    with pytest.raises(boundary.OwnedOriginalCandidateBoundaryError):
        boundary.OwnedOriginalCandidateBoundaryV5(canonical(forged))


@pytest.mark.asyncio
async def test_unconsumed_v4_result_cannot_cross_owned_boundary(tmp_path, monkeypatch):
    controlled = session()

    async def borrowed_v4(*_args, **_kwargs):
        return _inspected_precursor_receipt(controlled._pin)

    monkeypatch.setattr(original, "capture_owned_original_precursor_v4", borrowed_v4)
    with pytest.raises(
        boundary.OwnedOriginalCandidateBoundaryError,
        match="owned_original_session_mismatch",
    ):
        await boundary.preflight_owned_publish_and_recheck_v5(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )
    assert not controlled._used


@pytest.mark.asyncio
async def test_foreign_plan_v4_result_cannot_cross_owned_boundary(
    tmp_path, monkeypatch
):
    controlled = session()

    async def foreign_v4(*_args, **_kwargs):
        controlled._used = True
        return _inspected_precursor_receipt("a" * 64)

    monkeypatch.setattr(original, "capture_owned_original_precursor_v4", foreign_v4)
    with pytest.raises(
        boundary.OwnedOriginalCandidateBoundaryError,
        match="owned_original_session_mismatch",
    ):
        await boundary.preflight_owned_publish_and_recheck_v5(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra", ["run", "candidate", "receipt", "v4_receipt", "passed", "barrier", "clock"]
)
async def test_caller_cannot_import_run_or_old_precursor_to_owned_boundary(
    tmp_path, extra
):
    controlled = session()
    with pytest.raises(TypeError):
        await boundary.preflight_owned_publish_and_recheck_v5(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
            **{extra: _inspected_precursor_receipt(controlled._pin)},
        )


@pytest.mark.asyncio
async def test_consumed_session_cannot_reenter_from_another_task(tmp_path, monkeypatch):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    monkeypatch.setattr(public_source_runtime, "_new_owned_client", _forbidden)
    args = {
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "fvg_return",
        "market_policy": policy(),
        "account_session": controlled,
        "session_factory": object(),
    }
    await boundary.preflight_owned_publish_and_recheck_v5(
        tmp_path / "public", tmp_path / "account", **args
    )
    with pytest.raises(original.OriginalSourceCoordinatorError):
        await asyncio.create_task(
            boundary.preflight_owned_publish_and_recheck_v5(
                tmp_path / "public2", tmp_path / "account2", **args
            )
        )
    assert controlled._used


@pytest.mark.asyncio
async def test_early_v4_validation_error_does_not_claim_session_consumed(
    tmp_path, monkeypatch
):
    controlled = session()
    monkeypatch.setattr(
        public_runtime, "publish_qualification_evidence_liquidity_v2", _forbidden
    )
    with pytest.raises(
        original.OriginalSourceCoordinatorError,
        match="original_precursor_strategy_invalid",
    ):
        await boundary.preflight_owned_publish_and_recheck_v5(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="not_a_strategy",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )
    assert not controlled._used
    assert not (tmp_path / "public").exists()
