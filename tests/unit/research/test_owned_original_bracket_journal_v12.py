"""Synthetic same-frame V11 bracket / V10 journal join; never order authority."""

import asyncio
import inspect
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import qualification_ledger as ledger_repo
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as original
from app.trade_qualification import owned_original_bracket_journal_v12 as boundary
from app.trade_qualification import owned_original_bracket_v11 as bracket_v11
from app.trade_qualification import owned_original_event_journal_v10 as journal_v10
from app.trade_qualification import qualification_runtime as initial
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
from tests.unit.research.test_owned_original_event_ledger_v8 import _journal
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def owned_source():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        return replace(
            v2_engine_source("long"),
            evaluated_at=original_fixture.NOW + timedelta(milliseconds=10),
        )


@pytest.mark.asyncio
async def test_v12_owns_one_raw_frame_through_bracket_and_locked_journal(
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
    monkeypatch.setattr(
        initial, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="a" * 32))
    )
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
    state = {"diagnostic": None, "consumed": False, "g5": 0, "bracket": 0, "db": 0}

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
    evaluate_event = original._evaluate_owned_base_event_v7
    evaluate_bracket = original._evaluate_owned_bracket_v11

    def event(public_packet, derived, **pins):
        assert asyncio.current_task() is owner and state["consumed"]
        state["g5"] += 1
        if state["g5"] == 1:
            state["public"] = public_packet
            state["precursor"] = derived
        else:
            assert public_packet is state["public"]
            assert derived is state["precursor"]
        if state["g5"] == 5:
            assert state["db"] == 2 and state["bracket"] == 2
        return evaluate_event(public_packet, derived, **pins)

    def bracket(public_packet, packet, derived, **pins):
        assert asyncio.current_task() is owner and state["consumed"]
        state["bracket"] += 1
        assert public_packet is state["public"]
        assert packet is account_packet
        assert derived is state["precursor"]
        if state["bracket"] == 3:
            assert state["db"] == 2 and state["g5"] == 5
        return evaluate_bracket(public_packet, packet, derived, **pins)

    async def inspect_journal(self, scope):
        assert asyncio.current_task() is owner and controlled._used
        assert state["g5"] == 4 and state["bracket"] == 2
        assert scope.account_id == controlled._plan.expected_uid
        state["db"] += 1
        return _journal(scope, (), self.clock)

    monkeypatch.setattr(original, "_evaluate_owned_base_event_v7", event)
    monkeypatch.setattr(original, "_evaluate_owned_bracket_v11", bracket)
    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        inspect_journal,
    )
    result = await boundary.inspect_owned_original_bracket_journal_v12(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    bracket_record = decode(record["bracket_receipt_json"].encode("ascii"))
    journal_record = decode(record["journal_receipt_json"].encode("ascii"))
    assert state["g5"] == 6 and state["bracket"] == 3 and state["db"] == 2
    assert record["code"] in {"joined_inspected_unqualified", "bracket_unavailable"}
    assert bracket_record["bracket_inspected"] is True
    assert journal_record["code"] == "event_absent_from_controlled_journal_only"
    assert (
        bracket_record["base_event_receipt_sha256"]
        == journal_record["event_receipt_sha256"]
    )
    assert (
        bracket_v11.OwnedOriginalBracketDiagnosticV11(
            record["bracket_receipt_json"].encode("ascii")
        ).receipt_sha256
        == record["bracket_receipt_sha256"]
    )
    assert (
        journal_v10.OwnedOriginalEventJournalDiagnosticV10(
            record["journal_receipt_json"].encode("ascii")
        ).receipt_sha256
        == record["journal_receipt_sha256"]
    )
    assert all(record[name] is False for name in boundary._FALSE_FIELDS)
    assert result.admission == "DENY" and result.execution_authority is False
    assert controlled._plan.expected_uid.encode() not in result.receipt_json
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_v12_unproved_region_denies_before_io_and_journal(tmp_path, monkeypatch):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    async def forbidden(*args, **kwargs):
        pytest.fail("region refusal must precede journal access")

    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        forbidden,
    )
    result = await boundary.inspect_owned_original_bracket_journal_v12(
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
    assert (
        decode(record["bracket_receipt_json"].encode("ascii"))["bracket_inspected"]
        is False
    )
    assert (
        decode(record["journal_receipt_json"].encode("ascii"))["g1_g5_replayed"]
        is False
    )
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / "account").exists()


def test_v12_rejects_changed_nested_digest_shared_lineage_and_authority():
    bracket = {
        "schema_version": bracket_v11._SCHEMA,
        "code": "source_unavailable",
        **{name: None for name in bracket_v11._DIGEST_FIELDS},
        "original_diagnostic_sha256": "a" * 64,
        "account_plan_sha256": "b" * 64,
        "bracket_policy_sha256": bracket_v11.policy.POLICY_RECORD_SHA256,
        "report_id": None,
        "instrument_id": None,
        "direction": None,
        "original_entry": None,
        "fixed_stop_loss": None,
        "fixed_take_profit": None,
        "bracket_inspected": False,
        "bracket_available": False,
        "g1_g5_replayed": False,
        "admission": "DENY",
        **{name: False for name in bracket_v11._FALSE_FIELDS},
    }
    journal = {
        "schema_version": journal_v10._SCHEMA,
        "code": "g5_unavailable",
        **{name: None for name in journal_v10._DIGEST_FIELDS},
        "original_diagnostic_sha256": "a" * 64,
        "account_plan_sha256": "b" * 64,
        "scope_sha256": "c" * 64,
        "session_binding_sha256": "d" * 64,
        "ledger_revision": None,
        "ledger_observed_at": None,
        "event_seen_in_controlled_journal": None,
        "g1_g5_replayed": False,
        "admission": "DENY",
        **{name: False for name in journal_v10._FALSE_FIELDS},
    }
    first, second = canonical(bracket), canonical(journal)
    record = {
        "schema_version": boundary._SCHEMA,
        "code": "source_unavailable",
        "bracket_receipt_json": first.decode("ascii"),
        "bracket_receipt_sha256": sha(first),
        "journal_receipt_json": second.decode("ascii"),
        "journal_receipt_sha256": sha(second),
        "admission": "DENY",
        **{name: False for name in boundary._FALSE_FIELDS},
    }
    checked = boundary.OwnedOriginalBracketJournalDiagnosticV12(canonical(record))
    assert checked.execution_authority is False
    for changed in (
        dict(record, execution_authority=True),
        dict(record, journal_receipt_sha256="0" * 64),
        dict(record, code="joined_inspected_unqualified"),
        dict(
            record,
            journal_receipt_json=canonical(
                dict(journal, account_plan_sha256="e" * 64)
            ).decode("ascii"),
            journal_receipt_sha256=sha(
                canonical(dict(journal, account_plan_sha256="e" * 64))
            ),
        ),
    ):
        with pytest.raises(boundary.OwnedOriginalBracketJournalError):
            boundary.OwnedOriginalBracketJournalDiagnosticV12(canonical(changed))


def test_v12_public_signature_accepts_no_caller_gate_or_bracket():
    signature = inspect.signature(boundary.inspect_owned_original_bracket_journal_v12)
    assert not {
        "candidate",
        "run",
        "event",
        "receipt",
        "entry",
        "stop_loss",
        "take_profit",
        "passed",
        "consumed_event_keys",
        "clock",
        "callback",
    }.intersection(signature.parameters)
    assert all(
        item.kind is not inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )


@pytest.mark.asyncio
async def test_v12_non_base_strategy_refuses_before_io(tmp_path):
    with pytest.raises(original.OriginalSourceCoordinatorError, match="unsupported"):
        await boundary.inspect_owned_original_bracket_journal_v12(
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
