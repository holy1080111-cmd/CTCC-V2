"""Synthetic same-task raw continuity; never Demo or G12 acceptance."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_candidate_boundary_v5 as boundary
from app.trade_qualification import post_g12_public_runtime as public_runtime
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


def _forbid_g12(*_args, **_kwargs):
    pytest.fail("raw continuity is not G12 permission")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["intact", "wrong_public_pin", "wrong_account_pin", "expired"]
)
async def test_v5_keeps_exact_native_raw_inputs_only_in_its_private_frame(
    source, tmp_path, monkeypatch, mode
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
    clock, _, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(original, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    monkeypatch.setattr(
        public_runtime, "publish_qualification_evidence_liquidity_v2", _forbid_g12
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
    state = {"account": None, "consumed": False, "continuity": 0, "diagnostic": None}

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
        state["account"] = diagnostic
        return diagnostic

    def consume(diagnostic, received):
        assert asyncio.current_task() is owner
        assert diagnostic is state["account"] and received is controlled
        assert state["consumed"] is False
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

    replay = original._replay_owned_original_raw_continuity

    def inspect(public_packet, raw_account, precursor, **pins):
        assert asyncio.current_task() is owner
        assert raw_account is account_packet
        assert type(public_packet.packet_json) is bytes
        assert pins["public_pin"] == sha(public_packet.packet_json)
        assert pins["account_pin"] == frozen.sha256
        assert pins["plan_pin"] == controlled._pin
        state["continuity"] += 1
        if mode == "wrong_public_pin":
            pins = dict(pins, public_pin="0" * 64)
        if mode == "wrong_account_pin":
            pins = dict(pins, account_pin="0" * 64)
        replay(public_packet, raw_account, precursor, **pins)
        if mode == "expired":
            for _ in range(11_000):
                clock.stamp()

    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    monkeypatch.setattr(account_native, "_consume_native_demo_raw_packet", consume)
    monkeypatch.setattr(original, "_replay_owned_original_raw_continuity", inspect)
    private_capture = original._capture_owned_original_precursor_for_boundary_v5

    async def capture_diagnostic(*args, **kwargs):
        diagnostic = await private_capture(*args, **kwargs)
        state["diagnostic"] = decode(diagnostic.receipt_json)
        return diagnostic

    monkeypatch.setattr(
        original,
        "_capture_owned_original_precursor_for_boundary_v5",
        capture_diagnostic,
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
    assert state["continuity"] == 1 and state["consumed"]
    assert controlled._used and result.admission == "DENY"
    assert receipt["g12_published"] is False
    assert receipt["execution_authority"] is False
    if mode == "intact":
        assert state["diagnostic"]["observed_at"] == clock.last.isoformat()
        assert receipt["code"] in {
            "precursor_no_intent",
            "g1_g11_source_inputs_unavailable",
        }
        assert receipt["precursor_receipt_sha256"] is not None
    else:
        assert receipt["code"] == "native_precursor_unavailable"
        assert receipt["precursor_receipt_sha256"] is None
    assert set(receipt) == boundary._FIELDS
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()
    with pytest.raises(original.OriginalSourceCoordinatorError):
        await boundary.preflight_owned_publish_and_recheck_v5(
            tmp_path / "second-public",
            tmp_path / "second-account",
            instrument_id="BTC-USDT-SWAP",
            strategy="fvg_return",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )
    assert not (tmp_path / "second-public").exists()
