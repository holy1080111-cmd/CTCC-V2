"""Synthetic source arithmetic only; no real Demo capture or order authority."""

import asyncio
import inspect
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_base_prefix_v6 as boundary
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification.account_observation_index import CaptureReference
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_original_candidate_policy as original_fixture
from tests.unit.research.test_owned_original_candidate_boundary_v5 import (
    _inspected_precursor_receipt,
)
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
from tests.unit.test_original_candidate_precursor_v2 import (
    capture_raw_source,
    derive,
    inputs,
)
from tests.unit.test_qualification_account_capture import row
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module", params=["long", "short"])
def captured(request, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        source = v2_engine_source(request.param)
    source = replace(
        source, evaluated_at=original_fixture.NOW + timedelta(milliseconds=10)
    )
    diagnostic = capture_raw_source(source, tmp_path_factory.mktemp("base-prefix-v6"))
    return request.param, diagnostic, original_fixture.account_source()


@pytest.fixture(scope="module")
def owned_source():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        return replace(
            v2_engine_source("long"),
            evaluated_at=original_fixture.NOW + timedelta(milliseconds=10),
        )


def test_base_prefix_replays_source_derived_g1_g4_without_authority(captured):
    _, diagnostic, account = captured
    derived = derive(diagnostic, account)
    assert derived.intent is not None
    args = inputs(diagnostic, account)
    first = original._evaluate_owned_base_prefix_v6(
        diagnostic.packet,
        account,
        derived,
        public_pin=args["expected_public_bundle_sha256"],
        account_pin=args["expected_account_packet_sha256"],
        plan_pin=args["expected_account_plan_sha256"],
        data_policy=args["data_policy"],
        created_at=args["created_at"],
    )
    second = original._evaluate_owned_base_prefix_v6(
        diagnostic.packet,
        account,
        derived,
        public_pin=args["expected_public_bundle_sha256"],
        account_pin=args["expected_account_packet_sha256"],
        plan_pin=args["expected_account_plan_sha256"],
        data_policy=args["data_policy"],
        created_at=args["created_at"],
    )
    assert first == second
    record = decode(first)
    assert [gate["gate"] for gate in record["gates"]] == ["G1", "G2", "G3", "G4"]
    assert all(gate["code"] == "passed" and gate["passed"] for gate in record["gates"])
    assert record["precursor_receipt_sha256"] == derived.receipt_sha256
    assert record["public_packet_sha256"] == diagnostic.packet.bundle_sha256
    assert record["calibrated_for_trading"] is False
    assert record["admission"] == "DENY"
    assert record["candidate_created"] is False
    assert record["execution_authority"] is False


def test_changed_source_pin_fails_before_prefix_result(captured):
    _, diagnostic, account = captured
    derived = derive(diagnostic, account)
    args = inputs(diagnostic, account)
    with pytest.raises(original.OriginalSourceCoordinatorError):
        original._evaluate_owned_base_prefix_v6(
            diagnostic.packet,
            account,
            derived,
            public_pin="0" * 64,
            account_pin=args["expected_account_packet_sha256"],
            plan_pin=args["expected_account_plan_sha256"],
            data_policy=args["data_policy"],
            created_at=args["created_at"],
        )


def test_boundary_accepts_no_caller_gate_or_pass_override():
    signature = inspect.signature(boundary.preflight_owned_base_prefix_v6)
    forbidden = {
        "run",
        "candidate",
        "market_packet",
        "account_packet",
        "g1",
        "g2",
        "g3",
        "g4",
        "passed",
        "clock",
        "callback",
        "prior_receipt",
        "publication_barrier",
    }
    assert not forbidden.intersection(signature.parameters)
    assert all(
        item.kind is not inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )
    denied = boundary.OwnedOriginalBasePrefixDiagnosticV6(
        canonical(
            {
                "schema_version": boundary._SCHEMA,
                "code": "native_precursor_unavailable",
                "original_diagnostic_sha256": "a" * 64,
                "public_packet_sha256": None,
                "account_packet_sha256": None,
                "precursor_receipt_sha256": None,
                "precursor_intent_sha256": None,
                "base_prefix_receipt_sha256": None,
                "base_prefix_result_sha256": None,
                "base_prefix_policy_sha256": None,
                "base_g1_g4_evaluated": False,
                "admission": "DENY",
                **{name: False for name in boundary._FALSE_FIELDS},
            }
        )
    )
    assert denied.admission == "DENY" and denied.execution_authority is False
    forged = decode(denied.receipt_json)
    forged["execution_authority"] = True
    with pytest.raises(boundary.OwnedOriginalBasePrefixError):
        boundary.OwnedOriginalBasePrefixDiagnosticV6(canonical(forged))
    forged = decode(denied.receipt_json)
    forged["base_g1_g4_evaluated"] = True
    with pytest.raises(boundary.OwnedOriginalBasePrefixError):
        boundary.OwnedOriginalBasePrefixDiagnosticV6(canonical(forged))
    assert sha(denied.receipt_json) == denied.receipt_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_source", [False, True])
async def test_owned_v6_native_packets_evaluate_or_deny_without_g12(
    owned_source, tmp_path, monkeypatch, changed_source
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
    clock, _, harness, publications = setup(monkeypatch, owned_source)
    monkeypatch.setattr(original, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    monkeypatch.setattr(
        public_runtime,
        "publish_qualification_evidence_liquidity_v2",
        lambda *_a, **_k: pytest.fail("G12 is not available from this diagnostic"),
    )
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
    state = {"diagnostic": None, "consumed": False, "prefix_calls": 0}

    async def account_source(received, *, session_factory, proof_root):
        assert asyncio.current_task() is owner
        assert received is controlled and proof_root == tmp_path / "account"
        received._used = True
        for _ in range(130):
            clock.stamp()
        diagnostic = _native_account_receipt(
            received, utc_from_ns(clock.stamp()["utc_ns"])
        )
        document = decode(diagnostic.receipt_json)
        document["source_reference"]["packet_sha256"] = frozen.sha256
        diagnostic = account_native.InitialNativeAccountDiagnostic(canonical(document))
        state["diagnostic"] = diagnostic
        return diagnostic

    def consume(diagnostic, received):
        assert asyncio.current_task() is owner
        assert diagnostic is state["diagnostic"] and received is controlled
        assert not state["consumed"]
        state["consumed"] = True
        receipt = decode(diagnostic.receipt_json)
        return account_native._ObservedNativeDemoAccountRawPacket(
            packet=account_packet,
            reference=reference,
            receipt_sha256=diagnostic.receipt_sha256,
            proof_sha256="1" * 64,
            readback_sha256="2" * 64,
            observed_at=original._utc_text(receipt["observed_at"]),
            expires_at=original._utc_text(receipt["expires_at"]),
        )

    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    monkeypatch.setattr(account_native, "_consume_native_demo_raw_packet", consume)
    original_continuity = original._replay_owned_original_raw_continuity

    def continuity(public_packet, raw_account, derived, **pins):
        assert asyncio.current_task() is owner
        assert raw_account is account_packet
        if changed_source:
            pins = dict(pins, public_pin="0" * 64)
        return original_continuity(public_packet, raw_account, derived, **pins)

    monkeypatch.setattr(original, "_replay_owned_original_raw_continuity", continuity)
    original_prefix = original._evaluate_owned_base_prefix_v6

    def evaluate(public_packet, raw_account, derived, **pins):
        assert asyncio.current_task() is owner
        assert raw_account is account_packet
        state["prefix_calls"] += 1
        return original_prefix(public_packet, raw_account, derived, **pins)

    monkeypatch.setattr(original, "_evaluate_owned_base_prefix_v6", evaluate)
    result = await boundary.preflight_owned_base_prefix_v6(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    assert state["consumed"] and controlled._used
    assert result.admission == "DENY" and result.execution_authority is False
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()
    if changed_source:
        assert record["code"] == "native_precursor_unavailable"
        assert record["base_prefix_receipt_sha256"] is None
        assert state["prefix_calls"] == 0
    else:
        assert record["code"] == "base_g1_g4_inspected"
        assert record["base_g1_g4_evaluated"] is True
        assert record["base_prefix_receipt_sha256"] is not None
        assert state["prefix_calls"] == 2


@pytest.mark.asyncio
async def test_non_base_strategy_denied_before_source_io(tmp_path):
    with pytest.raises(boundary.OwnedOriginalBasePrefixError, match="unsupported"):
        await boundary.preflight_owned_base_prefix_v6(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="liquidity_sweep_reversal",
            market_policy=None,
            account_session=None,
            session_factory=None,
        )
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / "account").exists()


@pytest.mark.asyncio
async def test_extra_prefix_field_rejected_after_private_handoff(tmp_path, monkeypatch):
    controlled = session()
    diagnostic = _inspected_precursor_receipt(controlled._pin)
    source = decode(diagnostic.receipt_json)
    forged = {
        "schema_version": boundary._PREFIX_SCHEMA,
        "precursor_receipt_sha256": source["precursor_receipt_sha256"],
        "precursor_intent_sha256": source["precursor_intent_sha256"],
        "public_packet_sha256": source["public_packet_sha256"],
        "account_packet_sha256": source["account_packet_sha256"],
        "instrument_rules_sha256": "a" * 64,
        "g1_result_sha256": "b" * 64,
        "g1_source_sha256": "c" * 64,
        "prefix_policy_sha256": "d" * 64,
        "result_sha256": "e" * 64,
        "gates": [
            {"gate": f"G{number}", "code": "passed", "passed": True}
            for number in range(1, 5)
        ],
        "calibrated_for_trading": False,
        "admission": "DENY",
        **{name: False for name in boundary._FALSE_FIELDS},
        "unexpected": True,
    }

    async def private_capture(*_args, **_kwargs):
        controlled._used = True
        return original._OwnedBasePrefixHandoffV6(diagnostic, canonical(forged))

    monkeypatch.setattr(
        original, "_capture_owned_original_base_prefix_for_boundary_v6", private_capture
    )
    with pytest.raises(
        boundary.OwnedOriginalBasePrefixError, match="inspection_invalid"
    ):
        await boundary.preflight_owned_base_prefix_v6(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=None,
            account_session=controlled,
            session_factory=None,
        )
