"""Synthetic same-frame G5/DB join; no Demo source or G6 authority."""

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
from app.trade_qualification import owned_original_event_journal_v10 as boundary
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
from tests.unit.research.test_owned_original_source_coordinator_v2 import (
    clock as source_clock,
)
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
async def test_v10_keeps_raw_g5_frame_across_locked_journal_reads(
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
    state = {"diagnostic": None, "consumed": False, "g5_calls": 0, "db_calls": 0}

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
        state["g5_calls"] += 1
        if state["g5_calls"] == 1:
            state["raw_packet"] = public_packet
            state["precursor"] = derived
        else:
            assert public_packet is state["raw_packet"]
            assert derived is state["precursor"]
        if state["g5_calls"] == 3:
            assert state["db_calls"] == 2
        result = original_event(public_packet, derived, **pins)
        state["event_key"] = decode(result)["event_key_sha256"]
        return result

    async def inspect_journal(self, scope):
        assert asyncio.current_task() is owner
        assert controlled._used and state["g5_calls"] == 2
        assert self.session_factory is state["factory"]
        assert scope.account_id == controlled._plan.expected_uid
        state["db_calls"] += 1
        return _journal(scope, (), self.clock)

    monkeypatch.setattr(original, "_evaluate_owned_base_event_v7", evaluate)
    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        inspect_journal,
    )
    state["factory"] = object()
    result = await boundary.inspect_owned_original_event_journal_v10(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=state["factory"],
    )
    record = decode(result.receipt_json)
    assert state["g5_calls"] == 3 and state["db_calls"] == 2
    assert record["code"] == "event_absent_from_controlled_journal_only"
    assert record["g1_g5_replayed"] is True
    assert all(record[name] is False for name in boundary._FALSE_FIELDS)
    assert result.admission == "DENY" and result.execution_authority is False
    assert controlled._plan.expected_uid.encode() not in result.receipt_json
    assert not publications
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
    empty_registries()
    # Pin the complete readback after checking its source and DENY semantics.
    # The owned raw fixture now rolls quote turnover up from child candles.
    assert sha(result.receipt_json) == (
        "2bc60ab9f2a4bf355c71fdc0622ca2a73e502441f7574cecfa401fef3c270f2c"
    )


def test_v10_receipt_cannot_claim_g6_or_execution():
    record = {
        "schema_version": boundary._SCHEMA,
        "code": "g5_unavailable",
        **{name: None for name in boundary._DIGEST_FIELDS},
        "original_diagnostic_sha256": "a" * 64,
        "account_plan_sha256": "b" * 64,
        "scope_sha256": "c" * 64,
        "session_binding_sha256": "d" * 64,
        "ledger_revision": None,
        "ledger_observed_at": None,
        "event_seen_in_controlled_journal": None,
        "g1_g5_replayed": False,
        "admission": "DENY",
        **{name: False for name in boundary._FALSE_FIELDS},
    }
    diagnostic = boundary.OwnedOriginalEventJournalDiagnosticV10(canonical(record))
    assert diagnostic.execution_authority is False
    for name in ("g6_evaluated", "event_ledger_authenticated", "execution_authority"):
        changed = dict(record, **{name: True})
        with pytest.raises(boundary.OwnedOriginalEventJournalError):
            boundary.OwnedOriginalEventJournalDiagnosticV10(canonical(changed))


def test_v10_public_signature_accepts_no_caller_gates_or_ledger():
    signature = inspect.signature(boundary.inspect_owned_original_event_journal_v10)
    assert not {
        "candidate",
        "gate",
        "event",
        "passed",
        "consumed_event_keys",
        "receipt",
        "clock",
        "callback",
    }.intersection(signature.parameters)
    assert all(
        item.kind is not inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_keys", "second_keys", "event_state", "expected"),
    [
        ((), (), "reserved", "event_absent_from_controlled_journal_only"),
        (("a" * 64,), ("a" * 64,), "reserved", "event_seen_in_controlled_journal"),
        (
            ("a" * 64,),
            ("a" * 64,),
            "reconciled_flat",
            "event_seen_in_controlled_journal",
        ),
        ((), ("a" * 64,), "reserved", "ledger_changed"),
    ],
)
async def test_v10_journal_helper_is_bounded_and_never_claims_completeness(
    monkeypatch, first_keys, second_keys, event_state, expected
):
    controlled = session()
    controlled._used = True
    monkeypatch.setattr(original, "native_stamp", source_clock())
    identity = original._session_identity(controlled, used=True)
    calls = []

    async def inspect_journal(self, scope):
        assert asyncio.current_task() is owner
        assert scope == identity[1]
        calls.append(scope)
        keys = first_keys if len(calls) == 1 else second_keys
        return _journal(scope, keys, self.clock, event_state=event_state)

    owner = asyncio.current_task()
    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        inspect_journal,
    )
    result = await original._inspect_owned_event_journal_in_frame_v10(
        account_session=controlled,
        session_factory=object(),
        identity=identity,
        event_key="a" * 64,
    )
    assert len(calls) == 2
    assert result["code"] == expected
    assert result["ledger_first_sha256"] is not None
    assert result["ledger_second_sha256"] is not None
    assert "db_journal_complete" not in result


@pytest.mark.asyncio
async def test_v10_journal_read_failure_is_not_empty_history(monkeypatch):
    controlled = session()
    controlled._used = True
    monkeypatch.setattr(original, "native_stamp", source_clock())
    identity = original._session_identity(controlled, used=True)

    async def unavailable(_self, _scope):
        raise RuntimeError("PRIVATE_DATABASE_DETAIL")

    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        unavailable,
    )
    result = await original._inspect_owned_event_journal_in_frame_v10(
        account_session=controlled,
        session_factory=object(),
        identity=identity,
        event_key="a" * 64,
    )
    assert result["code"] == "ledger_unavailable"
    assert result["ledger_first_sha256"] is None
    assert "PRIVATE_DATABASE_DETAIL" not in str(result)


@pytest.mark.asyncio
async def test_v10_cancelled_journal_read_propagates(monkeypatch):
    controlled = session()
    controlled._used = True
    monkeypatch.setattr(original, "native_stamp", source_clock())
    identity = original._session_identity(controlled, used=True)

    async def cancelled(_self, _scope):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        cancelled,
    )
    with pytest.raises(asyncio.CancelledError):
        await original._inspect_owned_event_journal_in_frame_v10(
            account_session=controlled,
            session_factory=object(),
            identity=identity,
            event_key="a" * 64,
        )


@pytest.mark.asyncio
async def test_v10_native_region_denial_precedes_db_read(tmp_path, monkeypatch):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    async def forbidden(_self, _scope):
        pytest.fail("region denial must precede journal access")

    monkeypatch.setattr(
        ledger_repo.QualificationLedgerRepository,
        "inspect_consumed_event_journal",
        forbidden,
    )
    result = await boundary.inspect_owned_original_event_journal_v10(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        strategy="fvg_return",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    record = decode(result.receipt_json)
    assert record["code"] == "g5_unavailable"
    assert record["event_key_sha256"] is None
    assert record["g6_evaluated"] is False
    assert result.execution_authority is False
