"""DENY-only same-frame original bracket inspection using synthetic raw packets."""

import asyncio
import inspect
from dataclasses import replace

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import native_original_bracket_policy_v1 as policy_v11
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_bracket_v11 as boundary
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.account_observation_index import CaptureReference
from tests.unit.research import test_owned_original_base_event_v7 as base_event
from tests.unit.research.test_owned_original_base_event_v7 import (
    _base_receipt,
    _event_receipt,
)
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
from tests.unit.test_original_candidate_precursor_v2 import derive, inputs


@pytest.fixture(name="captured", scope="module", params=["long", "short"])
def captured_fixture(request, tmp_path_factory):
    return base_event.captured.__wrapped__(request, tmp_path_factory)


@pytest.fixture(name="owned_source", scope="module")
def owned_source_fixture():
    return base_event.owned_source.__wrapped__()


def _inspect(diagnostic, account, derived, prefix, event, *, packet=None, **changes):
    source = inputs(diagnostic, account)
    return original._evaluate_owned_bracket_v11(
        diagnostic.packet if packet is None else packet,
        account,
        derived,
        **{
            "public_pin": source["expected_public_bundle_sha256"],
            "account_pin": source["expected_account_packet_sha256"],
            "plan_pin": source["expected_account_plan_sha256"],
            "data_policy": source["data_policy"],
            "created_at": source["created_at"],
            "prefix_receipt_json": prefix,
            "event_receipt_json": event,
            **changes,
        },
    )


def test_same_raw_event_produces_deterministic_fixed_geometry_only(captured):
    diagnostic, account = captured
    derived = derive(diagnostic, account)
    prefix = _base_receipt(diagnostic, account, derived)
    event = _event_receipt(diagnostic, account, derived, prefix)
    first = _inspect(diagnostic, account, derived, prefix, event)
    assert first == _inspect(diagnostic, account, derived, prefix, event)
    assert first["original_entry"] == format(derived.intent.candidate_entry, "f")
    assert first["selection_sha256"] is not None
    assert first["direction"] == derived.intent.direction
    if first["bracket_available"]:
        assert first["fixed_stop_loss"] is not None
        assert first["fixed_take_profit"] is not None
    else:
        assert first["fixed_stop_loss"] is None
        assert first["fixed_take_profit"] is None


def test_changed_source_event_entry_or_policy_refuses_inspection(captured, monkeypatch):
    diagnostic, account = captured
    derived = derive(diagnostic, account)
    prefix = _base_receipt(diagnostic, account, derived)
    event = _event_receipt(diagnostic, account, derived, prefix)
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _inspect(diagnostic, account, derived, prefix, event, public_pin="0" * 64)
    changed_event = decode(event)
    changed_event["event_key_sha256"] = "0" * 64
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _inspect(diagnostic, account, derived, prefix, canonical(changed_event))
    changed_packet = decode(diagnostic.packet.packet_json, public_v2.MAX_PACKET_BYTES)
    changed_packet["candles"]["frames"][0]["verified_through"] = (
        "2026-01-01T00:00:00+00:00"
    )
    altered = public_v2.CollectedPublicMarketV2(canonical(changed_packet))
    with pytest.raises(ValueError):
        _inspect(diagnostic, account, derived, prefix, event, packet=altered)
    changed_entry = replace(
        derived,
        intent=derived.intent.model_copy(
            update={"candidate_entry": derived.intent.candidate_entry + 1}
        ),
    )
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _inspect(diagnostic, account, changed_entry, prefix, event)
    expired = replace(
        derived,
        intent=derived.intent.model_copy(
            update={"expires_at": inputs(diagnostic, account)["created_at"]}
        ),
    )
    with pytest.raises(original.OriginalSourceCoordinatorError):
        _inspect(diagnostic, account, expired, prefix, event)
    monkeypatch.setattr(policy_v11, "POLICY_RECORD", b"{}")
    with pytest.raises(ValueError, match="native_original_bracket_policy_changed"):
        _inspect(diagnostic, account, derived, prefix, event)


def test_unavailable_bracket_preserves_unknown_geometry(captured, monkeypatch):
    diagnostic, account = captured
    derived = derive(diagnostic, account)
    prefix = _base_receipt(diagnostic, account, derived)
    event = _event_receipt(diagnostic, account, derived, prefix)
    select = original.select_structural_protection

    def unavailable(*args, **kwargs):
        observed = select(*args, **kwargs)
        return replace(
            observed,
            selected=None,
            fail_codes=("no_valid_structural_bracket",),
        )

    monkeypatch.setattr(original, "select_structural_protection", unavailable)
    state = _inspect(diagnostic, account, derived, prefix, event)
    assert state["bracket_available"] is False
    assert state["fixed_stop_loss"] is None
    assert state["fixed_take_profit"] is None


@pytest.mark.asyncio
async def test_v11_owns_raw_packet_across_replay_and_returns_typed_deny(
    owned_source, tmp_path, monkeypatch
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
    clock, _, harness, publications = setup(
        monkeypatch,
        owned_source,
        rest_hostname="openapi.okx.com",
        ws_hostname="wspap.okx.com",
    )
    monkeypatch.setattr(original, "native_stamp", clock.stamp)
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
    state = {"diagnostic": None, "consumed": False, "replays": 0}

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
    evaluate = original._evaluate_owned_bracket_v11

    def same_frame(public_packet, packet, derived, **pins):
        assert asyncio.current_task() is owner and state["consumed"]
        state["replays"] += 1
        if state["replays"] == 1:
            state["public"] = public_packet
            state["account"] = packet
            state["precursor"] = derived
        else:
            assert public_packet is state["public"]
            assert packet is state["account"]
            assert derived is state["precursor"]
        return evaluate(public_packet, packet, derived, **pins)

    monkeypatch.setattr(original, "_evaluate_owned_bracket_v11", same_frame)
    result = await boundary.inspect_owned_original_bracket_v11(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    assert state["replays"] == 2
    assert record["code"] in {
        "fixed_bracket_observed_unqualified",
        "bracket_unavailable",
    }
    assert record["bracket_inspected"] is True
    assert record["bracket_selection_sha256"] is not None
    assert all(record[name] is False for name in boundary._FALSE_FIELDS)
    assert result.execution_authority is False
    forged = dict(record, g8_evaluated=True)
    with pytest.raises(boundary.OwnedOriginalBracketError):
        boundary.OwnedOriginalBracketDiagnosticV11(canonical(forged))
    forged = dict(
        record,
        fixed_stop_loss=(
            record["fixed_take_profit"] if record["bracket_available"] else "1"
        ),
    )
    with pytest.raises(boundary.OwnedOriginalBracketError):
        boundary.OwnedOriginalBracketDiagnosticV11(canonical(forged))
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_v11_unproved_demo_region_denies_before_source_io(tmp_path, monkeypatch):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    result = await boundary.inspect_owned_original_bracket_v11(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    assert record["code"] == "source_unavailable"
    assert record["bracket_inspected"] is False
    assert result.admission == "DENY" and result.execution_authority is False
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / "account").exists()
    assert (
        boundary.OwnedOriginalBracketDiagnosticV11(result.receipt_json).receipt_json
        == result.receipt_json
    )
    for name in ("g8_evaluated", "g9_evaluated", "execution_authority"):
        forged = dict(record, **{name: True})
        with pytest.raises(boundary.OwnedOriginalBracketError):
            boundary.OwnedOriginalBracketDiagnosticV11(canonical(forged))


def test_v11_accepts_no_caller_original_or_mutable_geometry():
    signature = inspect.signature(boundary.inspect_owned_original_bracket_v11)
    assert not {
        "candidate",
        "run",
        "event",
        "receipt",
        "entry",
        "stop_loss",
        "take_profit",
        "policy",
        "passed",
        "clock",
        "callback",
    }.intersection(signature.parameters)
    assert all(
        item.kind is not inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )


@pytest.mark.asyncio
async def test_v11_non_base_strategy_refuses_before_io(tmp_path):
    with pytest.raises(original.OriginalSourceCoordinatorError, match="unsupported"):
        await boundary.inspect_owned_original_bracket_v11(
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
