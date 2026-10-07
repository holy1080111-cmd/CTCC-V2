"""Synthetic native handoff wiring; no Demo source or trading acceptance."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import original_source_coordinator_v2 as coordinator
from app.trade_qualification.account_observation_index import CaptureReference
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_original_candidate_policy as original_fixture
from tests.unit.research.test_owned_original_g1_diagnostic_v3 import (
    _native_account_receipt,
)
from tests.unit.research.test_owned_original_source_coordinator_v2 import session
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    policy,
    setup,
)
from tests.unit.test_account_current_history_join import current_plan, recorded_current
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_qualification_account_capture import row
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def source():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        return replace(
            v2_engine_source("long"),
            evaluated_at=original_fixture.NOW + timedelta(milliseconds=10),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("expire_during_replay", [False, True])
async def test_same_task_raw_sources_recompute_diagnostic_precursor_only(
    source, tmp_path, monkeypatch, expire_during_replay
):
    pages = flat_pages()
    pages["account_instruments"] = [[row("account_instruments", tickSz="0.01")]]
    chain, account_harness = await recorded_current(
        monkeypatch, offset_seconds=600, pages=pages
    )
    payload = next(
        item.event.packet_payload for item in chain if item.event.packet_payload
    )
    account_packet = capture.verify_demo_account_packet(
        payload,
        expected_sha256=sha(payload),
        expected_plan_sha256=capture.plan_sha256(current_plan()),
    )
    account_harness.assert_closed()
    clock, directory, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(coordinator, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    controlled = session()
    frozen = capture.freeze_demo_account_packet(
        account_packet, expected_plan_sha256=controlled._pin
    )
    reference = CaptureReference(
        capture_id="b" * 32,
        head_sha256="c" * 64,
        plan_sha256=controlled._pin,
        packet_sha256=frozen.sha256,
        session_binding_sha256="e" * 64,
    )
    owner = asyncio.current_task()
    state = {"account": None, "consumed": False}

    async def account_source(received, *, session_factory, proof_root):
        assert asyncio.current_task() is owner
        assert received is controlled and proof_root == tmp_path / "account"
        received._used = True
        # Simulate the time taken by the private source. Its separately
        # constructed receipt must finish after the public observation.
        for _ in range(130):
            clock.stamp()
        at = utc_from_ns(clock.stamp()["utc_ns"])
        diagnostic = _native_account_receipt(received, at)
        document = decode(diagnostic.receipt_json)
        document["source_reference"]["packet_sha256"] = frozen.sha256
        diagnostic = account_native.InitialNativeAccountDiagnostic(canonical(document))
        state["account"] = diagnostic
        return diagnostic

    def consume(diagnostic, received):
        assert asyncio.current_task() is owner
        assert diagnostic is state["account"] and received is controlled
        assert state["consumed"] is False
        state["consumed"] = True
        account_receipt = decode(diagnostic.receipt_json)
        return account_native._ObservedNativeDemoAccountRawPacket(
            packet=account_packet,
            reference=reference,
            receipt_sha256=diagnostic.receipt_sha256,
            proof_sha256="1" * 64,
            readback_sha256="2" * 64,
            observed_at=coordinator._utc_text(account_receipt["observed_at"]),
            expires_at=coordinator._utc_text(account_receipt["expires_at"]),
        )

    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    monkeypatch.setattr(account_native, "_consume_native_demo_raw_packet", consume)
    if expire_during_replay:
        original_verify = precursor.verify_original_candidate_precursor_v2

        def delayed_verify(*args, **kwargs):
            checked = original_verify(*args, **kwargs)
            for _ in range(11_000):
                clock.stamp()
            return checked

        monkeypatch.setattr(
            precursor, "verify_original_candidate_precursor_v2", delayed_verify
        )
    result = await coordinator.capture_owned_original_precursor_v4(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == (
        "original_precursor_unavailable"
        if expire_during_replay
        else "original_precursor_inspected"
    )
    assert receipt["precursor_policy_sha256"] == precursor.POLICY_SHA256
    assert (receipt["precursor_receipt_sha256"] is None) is expire_during_replay
    if not expire_during_replay:
        assert receipt["precursor_action"] in {"WAIT", "CANCEL", "NO_TRADE"}
    assert receipt["public_packet_sha256"] is not None
    assert receipt["account_packet_sha256"] == frozen.sha256
    assert receipt["g1_passed"] is True
    assert receipt["candidate_created"] is False
    assert receipt["g1_g11_complete"] is False
    assert receipt["g12_published"] is False
    assert receipt["execution_authority"] is False
    assert receipt["order_submitted"] is False
    assert result.admission == "DENY" and result.execution_authority is False
    assert state["consumed"] and controlled._used
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    assert sha(result.receipt_json) == result.receipt_sha256
    assert directory.content["plan.json"]
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_missing_native_account_lease_stops_before_precursor(
    source, tmp_path, monkeypatch
):
    clock, _, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(coordinator, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    controlled = session()

    async def account_source(received, *, session_factory, proof_root):
        received._used = True
        return _native_account_receipt(received, utc_from_ns(clock.stamp()["utc_ns"]))

    def forbidden(*_args, **_kwargs):
        pytest.fail("a forged account diagnostic cannot start precursor math")

    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    monkeypatch.setattr(precursor, "derive_original_candidate_precursor_v2", forbidden)
    result = await coordinator.capture_owned_original_precursor_v4(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "original_raw_account_unavailable"
    assert receipt["precursor_receipt_sha256"] is None
    assert receipt["execution_authority"] is False
    assert not publications and controlled._used
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_foreign_task_public_carrier_is_burned_before_account_or_precursor(
    source, tmp_path, monkeypatch
):
    clock, _, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(coordinator, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    controlled = session()
    original_capture = coordinator.initial._capture_initial_lineage_v2

    async def foreign_capture(*args, **kwargs):
        return await asyncio.create_task(original_capture(*args, **kwargs))

    def forbidden(*_args, **_kwargs):
        pytest.fail("foreign-task source cannot reach account or precursor")

    monkeypatch.setattr(
        coordinator.initial, "_capture_initial_lineage_v2", foreign_capture
    )
    monkeypatch.setattr(account_native, "capture_initial_native_account", forbidden)
    monkeypatch.setattr(precursor, "derive_original_candidate_precursor_v2", forbidden)
    result = await coordinator.capture_owned_original_precursor_v4(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "original_source_public_unavailable"
    assert receipt["precursor_receipt_sha256"] is None
    assert result.execution_authority is False and controlled._used
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_consumed_account_session_cannot_repeat_source_handoff(
    tmp_path, monkeypatch
):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    first = await coordinator.capture_owned_original_precursor_v4(
        tmp_path / "first-public",
        tmp_path / "first-account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    assert decode(first.receipt_json)["code"] == "original_source_public_unavailable"
    assert controlled._used
    with pytest.raises(
        coordinator.OriginalSourceCoordinatorError,
        match="original_source_inputs_invalid",
    ):
        await coordinator.capture_owned_original_precursor_v4(
            tmp_path / "second-public",
            tmp_path / "second-account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )
    assert not (tmp_path / "second-public").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden", ["candidate", "intent", "packet", "receipt", "g1", "passed", "barrier"]
)
async def test_v4_caller_cannot_supply_candidate_or_old_receipt(tmp_path, forbidden):
    with pytest.raises(TypeError):
        await coordinator.capture_owned_original_precursor_v4(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
            **{forbidden: object()},
        )


def test_v4_receipt_rejects_forged_authority_and_precursor_claim():
    base = {
        "schema_version": "ctcc.original_owned_precursor_diagnostic.v4",
        "code": "original_source_public_unavailable",
        "account_plan_sha256": "a" * 64,
        "declared_demo_route_policy_sha256": "b" * 64,
        "public_report_id": None,
        "public_packet_sha256": None,
        "public_journal_sha256": None,
        "account_receipt_sha256": None,
        "account_packet_sha256": None,
        "observed_at": None,
        "g1_policy_record_sha256": coordinator.native_g1.POLICY_RECORD_SHA256,
        "g1_policy_sha256": coordinator.native_g1.DATA_POLICY_SHA256,
        "g1_source_sha256": None,
        "g1_result_sha256": None,
        "g1_result_code": None,
        "g1_evaluated_at": None,
        "g1_evaluated": False,
        "g1_passed": False,
        "precursor_policy_sha256": precursor.POLICY_SHA256,
        "precursor_receipt_sha256": None,
        "precursor_intent_sha256": None,
        "precursor_action": None,
        "precursor_code": None,
        "precursor_intent_derived": False,
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
    coordinator.InitialOwnedPrecursorDiagnosticV4(canonical(base))
    for changed in (
        {"precursor_receipt_sha256": "f" * 64},
        {"precursor_intent_derived": True},
        {"candidate_created": True},
        {"g12_published": True},
        {"execution_authority": True},
    ):
        with pytest.raises(coordinator.OriginalSourceCoordinatorError):
            coordinator.InitialOwnedPrecursorDiagnosticV4(canonical(base | changed))
