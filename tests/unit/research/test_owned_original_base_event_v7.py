"""Synthetic G5 event replay; no real Demo source or order authority."""

import asyncio
import inspect
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_base_event_v7 as boundary
from app.trade_qualification import owned_original_base_prefix_v6 as base
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.account_observation_index import CaptureReference
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_original_candidate_policy as original_fixture
from tests.unit.research.test_owned_original_base_prefix_v6 import (
    _native_account_receipt,
    current_plan,
    empty_registries,
    flat_pages,
    policy,
    recorded_current,
    row,
    session,
    setup,
)
from tests.unit.research.test_owned_original_candidate_boundary_v5 import (
    _inspected_precursor_receipt,
)
from tests.unit.test_original_candidate_precursor_v2 import (
    capture_raw_source,
    derive,
    inputs,
)
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module", params=["long", "short"])
def captured(request, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        source = v2_engine_source(request.param)
    source = replace(
        source, evaluated_at=original_fixture.NOW + timedelta(milliseconds=10)
    )
    diagnostic = capture_raw_source(source, tmp_path_factory.mktemp("base-event-v7"))
    return diagnostic, original_fixture.account_source()


@pytest.fixture(scope="module")
def owned_source():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        return replace(
            v2_engine_source("long"),
            evaluated_at=original_fixture.NOW + timedelta(milliseconds=10),
        )


def _base_receipt(diagnostic, account, derived):
    args = inputs(diagnostic, account)
    return original._evaluate_owned_base_prefix_v6(
        diagnostic.packet,
        account,
        derived,
        public_pin=args["expected_public_bundle_sha256"],
        account_pin=args["expected_account_packet_sha256"],
        plan_pin=args["expected_account_plan_sha256"],
        data_policy=args["data_policy"],
        created_at=args["created_at"],
    )


def _event_receipt(diagnostic, account, derived, base_receipt, **changes):
    args = inputs(diagnostic, account)
    kwargs = {
        "public_pin": args["expected_public_bundle_sha256"],
        "data_policy": args["data_policy"],
        "created_at": args["created_at"],
        "prefix_receipt_json": base_receipt,
        **changes,
    }
    return original._evaluate_owned_base_event_v7(diagnostic.packet, derived, **kwargs)


def test_fixed_g5_event_replays_from_same_raw_packet_without_authority(captured):
    diagnostic, account = captured
    derived = derive(diagnostic, account)
    assert derived.intent is not None
    base_receipt = _base_receipt(diagnostic, account, derived)
    first = _event_receipt(diagnostic, account, derived, base_receipt)
    second = _event_receipt(diagnostic, account, derived, base_receipt)
    assert first == second
    event = decode(first)
    assert canonical(event) == first
    precursor = decode(derived.receipt_json)
    assert event["gate"] == {"gate": "G5", "code": "passed", "passed": True}
    assert event["event_key_sha256"] == precursor["event_key"]
    assert event["detection_sha256"] == sha(canonical(precursor["detection"]))
    assert event["timeline_sha256"] == precursor["timeline_sha256"]
    assert event["historical_first_availability_verified"] is False
    assert event["event_ledger_authenticated"] is False
    assert event["g6_evaluated"] is False
    assert event["g7_evaluated"] is False
    assert event["candidate_created"] is False
    assert event["execution_authority"] is False
    assert event["admission"] == "DENY"


def test_changed_source_or_event_cannot_replay_g5(captured, monkeypatch):
    diagnostic, account = captured
    derived = derive(diagnostic, account)
    base_receipt = _base_receipt(diagnostic, account, derived)
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _event_receipt(diagnostic, account, derived, base_receipt, public_pin="0" * 64)
    changed = decode(base_receipt)
    changed["g1_source_sha256"] = "0" * 64
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _event_receipt(diagnostic, account, derived, canonical(changed))
    monkeypatch.setattr(original, "event_identity", lambda _detection: "0" * 64)
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _event_receipt(diagnostic, account, derived, base_receipt)


def test_changed_expiry_or_raw_timeline_cannot_replay_g5(captured):
    diagnostic, account = captured
    derived = derive(diagnostic, account)
    base_receipt = _base_receipt(diagnostic, account, derived)
    shifted = replace(
        derived,
        intent=derived.intent.model_copy(
            update={"expires_at": derived.intent.expires_at + timedelta(seconds=1)}
        ),
    )
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _event_receipt(diagnostic, account, shifted, base_receipt)
    changed_packet = decode(diagnostic.packet.packet_json, public_v2.MAX_PACKET_BYTES)
    changed_packet["candles"]["frames"][0]["verified_through"] = (
        "2026-01-01T00:00:00+00:00"
    )
    changed = public_v2.CollectedPublicMarketV2(canonical(changed_packet))
    args = inputs(diagnostic, account)
    with pytest.raises(ValueError):
        original._evaluate_owned_base_event_v7(
            changed,
            derived,
            public_pin=args["expected_public_bundle_sha256"],
            data_policy=args["data_policy"],
            created_at=args["created_at"],
            prefix_receipt_json=base_receipt,
        )


def test_v7_accepts_no_caller_gate_event_or_pass_override():
    signature = inspect.signature(boundary.preflight_owned_base_event_v7)
    assert not {
        "run",
        "candidate",
        "market_packet",
        "account_packet",
        "gate",
        "event",
        "passed",
        "clock",
        "callback",
        "prior_receipt",
        "publication_barrier",
        "consumed_event_keys",
    }.intersection(signature.parameters)
    assert all(
        item.kind is not inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )
    denied = boundary.OwnedOriginalBaseEventDiagnosticV7(
        canonical(
            {
                "schema_version": boundary._SCHEMA,
                "code": "native_precursor_unavailable",
                "base_diagnostic_sha256": "a" * 64,
                "original_diagnostic_sha256": "b" * 64,
                "public_packet_sha256": None,
                "account_packet_sha256": None,
                "precursor_receipt_sha256": None,
                "precursor_intent_sha256": None,
                "base_prefix_receipt_sha256": None,
                "event_receipt_sha256": None,
                "event_key_sha256": None,
                "g1_g5_replayed": False,
                "admission": "DENY",
                **{name: False for name in boundary._FALSE_FIELDS},
            }
        )
    )
    assert denied.execution_authority is False
    assert (
        boundary.OwnedOriginalBaseEventDiagnosticV7(denied.receipt_json).receipt_json
        == denied.receipt_json
    )
    with pytest.raises(boundary.OwnedOriginalBaseEventError):
        boundary.OwnedOriginalBaseEventDiagnosticV7(b" " + denied.receipt_json)
    forged = decode(denied.receipt_json)
    forged["execution_authority"] = True
    with pytest.raises(boundary.OwnedOriginalBaseEventError):
        boundary.OwnedOriginalBaseEventDiagnosticV7(canonical(forged))
    forged = decode(denied.receipt_json)
    forged["g1_g5_replayed"] = True
    with pytest.raises(boundary.OwnedOriginalBaseEventError):
        boundary.OwnedOriginalBaseEventDiagnosticV7(canonical(forged))


@pytest.mark.asyncio
@pytest.mark.parametrize("event_fault", ["none", "changed_pin", "private_error"])
async def test_owned_v7_replays_in_one_task_or_denies_without_g12(
    owned_source, tmp_path, monkeypatch, event_fault
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
    state = {"diagnostic": None, "consumed": False, "event_calls": 0}

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
    original_event = original._evaluate_owned_base_event_v7

    def evaluate(public_packet, derived, **pins):
        assert asyncio.current_task() is owner
        assert state["consumed"]
        state["event_calls"] += 1
        if event_fault == "private_error":
            raise RuntimeError("PRIVATE_ACCOUNT_SECRET_MUST_NOT_LEAK")
        if event_fault == "changed_pin":
            pins = dict(pins, public_pin="0" * 64)
        return original_event(public_packet, derived, **pins)

    monkeypatch.setattr(original, "_evaluate_owned_base_event_v7", evaluate)
    result = await boundary.preflight_owned_base_event_v7(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    assert canonical(record) == result.receipt_json
    assert (
        boundary.OwnedOriginalBaseEventDiagnosticV7(result.receipt_json).receipt_json
        == result.receipt_json
    )
    assert state["consumed"] and controlled._used
    assert result.admission == "DENY" and result.execution_authority is False
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()
    if event_fault != "none":
        assert record["code"] == "native_precursor_unavailable"
        assert record["event_receipt_sha256"] is None
        assert b"PRIVATE_ACCOUNT_SECRET_MUST_NOT_LEAK" not in result.receipt_json
        assert state["event_calls"] == 1
    else:
        assert record["code"] == "base_g1_g5_inspected"
        assert record["g1_g5_replayed"] is True
        assert record["event_receipt_sha256"] is not None
        assert state["event_calls"] == 2


@pytest.mark.asyncio
async def test_non_base_strategy_denied_before_io(tmp_path):
    with pytest.raises(boundary.OwnedOriginalBaseEventError, match="unsupported"):
        await boundary.preflight_owned_base_event_v7(
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
async def test_base_prefix_without_intent_carries_no_event(tmp_path, monkeypatch):
    controlled = session()
    diagnostic = _inspected_precursor_receipt(controlled._pin, with_intent=False)

    async def private_capture(*_args, **_kwargs):
        controlled._used = True
        return original._OwnedBaseEventHandoffV7(
            original._OwnedBasePrefixHandoffV6(diagnostic, None),
            None,
        )

    monkeypatch.setattr(
        original, "_capture_owned_original_base_event_for_boundary_v7", private_capture
    )
    result = await boundary.preflight_owned_base_event_v7(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=None,
        account_session=controlled,
        session_factory=None,
    )
    record = decode(result.receipt_json)
    assert record["code"] == "precursor_no_intent"
    assert record["base_prefix_receipt_sha256"] is None
    assert record["event_receipt_sha256"] is None
    assert record["event_key_sha256"] is None
    assert record["g1_g5_replayed"] is False
    assert record["g6_evaluated"] is False
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.asyncio
async def test_extra_event_field_rejected_after_private_handoff(tmp_path, monkeypatch):
    controlled = session()
    diagnostic = _inspected_precursor_receipt(controlled._pin)
    source = decode(diagnostic.receipt_json)
    prefix_receipt = canonical(
        {
            "schema_version": base._PREFIX_SCHEMA,
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
            **{name: False for name in base._FALSE_FIELDS},
        }
    )
    event_receipt = canonical(
        {
            "schema_version": boundary._EVENT_SCHEMA,
            "precursor_receipt_sha256": source["precursor_receipt_sha256"],
            "precursor_intent_sha256": source["precursor_intent_sha256"],
            "public_packet_sha256": source["public_packet_sha256"],
            "base_prefix_receipt_sha256": sha(prefix_receipt),
            "g1_result_sha256": "b" * 64,
            "g1_source_sha256": "c" * 64,
            "detection_sha256": "f" * 64,
            "event_key_sha256": "1" * 64,
            "timeline_sha256": "2" * 64,
            "event_prefix_witness_sha256": "3" * 64,
            "trigger_expires_at": "2026-01-01T00:00:00+00:00",
            "gate": {"gate": "G5", "code": "passed", "passed": True},
            "admission": "DENY",
            **{name: False for name in boundary._EVENT_FALSE_FIELDS},
            "unexpected": True,
        }
    )

    async def private_capture(*_args, **_kwargs):
        controlled._used = True
        return original._OwnedBaseEventHandoffV7(
            original._OwnedBasePrefixHandoffV6(diagnostic, prefix_receipt),
            event_receipt,
        )

    monkeypatch.setattr(
        original, "_capture_owned_original_base_event_for_boundary_v7", private_capture
    )
    with pytest.raises(
        boundary.OwnedOriginalBaseEventError, match="inspection_invalid"
    ):
        await boundary.preflight_owned_base_event_v7(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=None,
            account_session=controlled,
            session_factory=None,
        )
