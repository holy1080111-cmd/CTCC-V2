"""Synthetic coordinator mechanism only; no account HTTP, DB or proof issuance."""

import asyncio
import json
import os
from pathlib import Path
from threading import get_ident

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
    LockedAccountSourceJoinReadback,
)
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_collector import credentials

HISTORY_ID = "a" * 32
CURRENT_ID = "b" * 32


async def _synthetic_join(
    monkeypatch, *, changed_source=False, expired=False, locked_overrides=None
):
    plan = current_plan()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    reference = observed.CaptureReference(
        capture_id=CURRENT_ID,
        head_sha256="c" * 64,
        plan_sha256=session._pin,
        packet_sha256="d" * 64,
        session_binding_sha256="e" * 64,
    )
    history_reference = observed.CaptureReference(
        capture_id=HISTORY_ID,
        head_sha256="1" * 64,
        plan_sha256="2" * 64,
        packet_sha256="3" * 64,
        session_binding_sha256="e" * 64,
    )
    marker = {"late": False, "calls": [], "issued": None, "handle": None}
    counter = 0

    def stamp():
        nonlocal counter
        counter += 1
        if marker["late"]:
            return {
                "utc_ns": marker["issued"]["utc_ns"] + 30_000_000_000,
                "monotonic_ns": marker["issued"]["monotonic_ns"] + 30_000_000_000,
            }
        return {
            "utc_ns": int(NOW.timestamp() * 1_000_000_000) + counter * 1_000_000,
            "monotonic_ns": 1_000_000_000 + counter * 1_000_000,
        }

    monkeypatch.setattr(native.clock, "native_stamp", stamp)
    monkeypatch.setattr(boundary, "native_stamp", stamp)
    monkeypatch.setattr(runtime, "_configured_factory", lambda _: True)
    monkeypatch.setattr(
        runtime.collector,
        "_new_client",
        lambda: pytest.fail("account HTTP must not run in this mechanism test"),
    )

    async def capture_current(stage, received_session, factory, root):
        assert received_session is session and factory is None and root == Path.cwd()
        marker["calls"].append("native_current")
        state = native._state(stage)
        issued = native._sample(stage)
        marker["issued"] = issued
        state["current_deadline"] = issued["monotonic_ns"] + 30_000_000_000
        receipt = canonical(
            {
                "schema_version": "ctcc.initial_native_account_diagnostic.v2",
                "source_reference": observed.reference_document(reference),
                "snapshot": None,
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        handle = object.__new__(boundary._AccountClockBoundary)
        marker["handle"] = handle
        boundary._BOUNDARIES[handle] = boundary._NativeClockRegistration(
            issuer=boundary._NATIVE_ISSUER,
            invocation=state["invocation"],
            parent_task=asyncio.current_task(),
            loop=asyncio.get_running_loop(),
            pid=os.getpid(),
            thread=get_ident(),
            receipt_sha256=sha(receipt),
            proof_schema=boundary.V3_PROOF_SCHEMA,
            durable_clock_proof_sha256="f" * 64,
            native_issue_stamp_json=canonical(issued),
            expires_at=utc_from_ns(issued["utc_ns"] + 30_000_000_000),
            monotonic_deadline_ns=state["current_deadline"],
        )
        carrier = object.__new__(runtime._CapturedCurrentNativeAccount)
        runtime._CAPTURES[carrier] = runtime._CurrentNativeHandoff(
            None, reference, receipt, handle, state["invocation"]
        )
        return carrier

    async def locked_join(repo, scope, *, history_capture_id, current_capture_id):
        marker["calls"].append("locked_join")
        assert repo.clock is not None
        assert (
            scope.environment,
            scope.account_id,
            scope.settlement_currency,
        ) == ("demo", plan.expected_uid, "USDT")
        assert (history_capture_id, current_capture_id) == (HISTORY_ID, CURRENT_ID)
        marker["late"] = expired
        current = observed.reference_document(reference)
        if changed_source:
            current["packet_sha256"] = "0" * 64
        receipt = {
            "schema_version": "ctcc.demo_account_locked_source_join.v2",
            "current_source_reference": current,
            "history_source_reference": observed.reference_document(history_reference),
            "scope_sha256": proof.scope_sha256(scope),
            "session_binding_sha256": reference.session_binding_sha256,
            "recorded_local_checkpoint_sha256": "f" * 64,
            "db_local_state_sha256": "f" * 64,
            "db_account_revision": 0,
            "db_ledger_revision": 0,
            "db_active_hold_count": 0,
            "local_revision_readback_verified": True,
            "exchange_atomic_revision_verified": False,
            "history_tail_closed": False,
            "account_revision_published": False,
            "locked_readback_blocking_reasons": ["history_tail_not_atomically_closed"],
            "snapshot": None,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        if locked_overrides is not None:
            receipt.update(locked_overrides)
        return LockedAccountSourceJoinReadback(canonical(receipt))

    monkeypatch.setattr(runtime, "_capture_initial_current", capture_current)
    monkeypatch.setattr(
        AccountCaptureJournalRepository,
        "read_locked_current_history_join",
        locked_join,
    )
    result = await runtime.capture_native_current_history_join(
        session,
        session_factory=None,
        proof_root=Path.cwd(),
        history_capture_id=HISTORY_ID,
    )
    assert session._used
    assert not runtime._CAPTURES and not boundary._BOUNDARIES
    return result, marker


@pytest.mark.asyncio
async def test_same_invocation_native_current_and_locked_history_remain_diagnostic(
    monkeypatch,
):
    result, marker = await _synthetic_join(monkeypatch)
    value = json.loads(result.receipt_json)
    assert marker["calls"] == ["native_current", "locked_join"]
    assert value["schema_version"] == "ctcc.native_current_history_join_diagnostic.v1"
    assert value["native_current_source_observed"] is True
    assert value["historical_native_source_observed"] is False
    assert value["history_tail_closed"] is False
    assert value["locked_readback_blocking_reasons"] == [
        "history_tail_not_atomically_closed"
    ]
    assert result.owner is None and result.snapshot is None
    assert result.account_complete is False and result.execution_authority is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_source,expired", [(True, False), (False, True)])
async def test_join_mismatch_or_original_native_lease_expiry_burns_carrier(
    monkeypatch, changed_source, expired
):
    result, marker = await _synthetic_join(
        monkeypatch, changed_source=changed_source, expired=expired
    )
    value = json.loads(result.receipt_json)
    assert marker["calls"] == ["native_current", "locked_join"]
    assert value["code"] == "native_account_history_join_denied"
    assert value["snapshot"] is None and value["admission"] == "DENY"
    assert result.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "locked_overrides",
    [
        {"scope_sha256": "0" * 64},
        {"session_binding_sha256": "0" * 64},
        {"recorded_local_checkpoint_sha256": "0" * 64},
        {"db_local_state_sha256": "0" * 64},
        {"db_account_revision": -1},
        {"db_ledger_revision": -1},
        {"db_ledger_revision": True},
        {"db_active_hold_count": -1},
        {"local_revision_readback_verified": False},
        {"exchange_atomic_revision_verified": True},
        {"history_tail_closed": True},
        {"account_revision_published": True},
        {"locked_readback_blocking_reasons": []},
        {
            "locked_readback_blocking_reasons": [
                "history_tail_not_atomically_closed",
                "history_tail_not_atomically_closed",
            ]
        },
        {"history_source_reference": {"capture_id": "0" * 32}},
    ],
)
async def test_forged_locked_readback_cannot_claim_native_history_join(
    monkeypatch, locked_overrides
):
    result, marker = await _synthetic_join(
        monkeypatch, locked_overrides=locked_overrides
    )
    value = json.loads(result.receipt_json)
    assert marker["calls"] == ["native_current", "locked_join"]
    assert value["code"] == "native_account_history_join_denied"
    assert value["snapshot"] is None and value["admission"] == "DENY"
    assert result.execution_authority is False


@pytest.mark.asyncio
async def test_history_locator_is_exact_and_rejected_before_capture(monkeypatch):
    plan = current_plan()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    monkeypatch.setattr(runtime, "_configured_factory", lambda _: True)
    with pytest.raises(proof.NativeAccountProofError, match="inputs_invalid"):
        await runtime.capture_native_current_history_join(
            session,
            session_factory=None,
            proof_root=Path.cwd(),
            history_capture_id="not-a-capture-id",
        )
    assert not session._used and not runtime._CAPTURES
