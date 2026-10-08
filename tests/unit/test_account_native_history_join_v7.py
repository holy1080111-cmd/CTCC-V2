"""V7 coordinator mechanism with synthetic carrier and locked receipt only.

No account HTTP, database transaction, native proof issuance or execution is
performed. The fixture tests coordinator binding and fail-closed behavior,
not real source acceptance.
"""

import asyncio
import json
import os
from datetime import timedelta
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
from app.trade_qualification import account_current_history_join as source_join
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_current_source_verifier import recorded
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_collector import credentials

HISTORY_ID = "a" * 32
CURRENT_ID = "b" * 32


async def _synthetic_v7_join(
    monkeypatch,
    *,
    changed_source=False,
    expired=False,
    locked_overrides=None,
    native_overrides=None,
    wrong_invocation=False,
    pre_read_failure=False,
    first_http_reversed=False,
    pre_read_gate=None,
    pre_read_entered=None,
    progress=None,
):
    plan = current_plan_v7()
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
        session_binding_sha256=reference.session_binding_sha256,
    )
    marker = {
        "late": False,
        "calls": [],
        "issued": None,
        "carrier": None,
        "invocation": None,
        "handle": None,
    }
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
        lambda: pytest.fail("account HTTP must not run in synthetic join test"),
    )

    async def read_before_http(stage, repo, scope, capture_id, selected):
        marker["calls"].append("pre_read")
        if progress is not None:
            progress.append("pre_read")
        if pre_read_entered is not None:
            pre_read_entered.set()
        if pre_read_gate is not None:
            await pre_read_gate.wait()
        assert capture_id == HISTORY_ID and selected == session._plan
        assert repo.clock is not None and scope.account_id == plan.expected_uid
        if pre_read_failure:
            raise RuntimeError("simulated uncommitted history")
        started = native._sample(stage)
        completed = native._sample(stage)
        verified = native._sample(stage)
        return runtime._CommittedHistoryPreRead(
            history_reference,
            "4" * 64,
            "5" * 64,
            NOW - timedelta(seconds=1),
            started,
            completed,
            verified,
        )

    async def capture_current(stage, received_session, factory, root):
        assert received_session is session and factory is None and root == Path.cwd()
        marker["calls"].append("native_current")
        if progress is not None:
            progress.append("native_current")
        state = native._state(stage)
        issued = native._sample(stage)
        marker["issued"] = issued
        state["first_http_request_start"] = (
            state["required_pre_history_readback"] if first_http_reversed else issued
        )
        state["current_deadline"] = issued["monotonic_ns"] + 30_000_000_000
        native_receipt = {
            "schema_version": "ctcc.initial_native_account_diagnostic.v3",
            "policy_sha256": proof.V7_FLAT_POLICY_SHA256,
            "source_reference": observed.reference_document(reference),
            "snapshot": None,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        if native_overrides is not None:
            native_receipt.update(native_overrides)
        receipt = canonical(native_receipt)
        handle = object.__new__(boundary._AccountClockBoundary)
        marker["handle"] = handle
        marker["invocation"] = state["invocation"]
        boundary._BOUNDARIES[handle] = boundary._NativeClockRegistration(
            issuer=boundary._NATIVE_ISSUER,
            invocation=state["invocation"],
            parent_task=asyncio.current_task(),
            loop=asyncio.get_running_loop(),
            pid=os.getpid(),
            thread=get_ident(),
            receipt_sha256=sha(receipt),
            proof_schema=boundary.V7_FLAT_PROOF_SCHEMA,
            durable_clock_proof_sha256="f" * 64,
            native_issue_stamp_json=canonical(issued),
            expires_at=utc_from_ns(issued["utc_ns"] + 30_000_000_000),
            monotonic_deadline_ns=state["current_deadline"],
        )
        carrier = object.__new__(runtime._CapturedCurrentNativeAccount)
        marker["carrier"] = carrier
        runtime._CAPTURES[carrier] = runtime._CurrentNativeHandoff(
            None,
            reference,
            receipt,
            handle,
            object() if wrong_invocation else state["invocation"],
        )
        return carrier

    async def locked_join(
        repo,
        scope,
        *,
        history_capture_id,
        current_capture_id,
        expected_policy_sha256,
    ):
        marker["calls"].append("locked_join")
        assert repo.clock is not None
        assert (
            scope.environment,
            scope.account_id,
            scope.settlement_currency,
        ) == ("demo", plan.expected_uid, "USDT")
        assert (history_capture_id, current_capture_id) == (HISTORY_ID, CURRENT_ID)
        assert expected_policy_sha256 == source_join.V7_ORDERED_POLICY_SHA256
        marker["late"] = expired
        current_reference = observed.reference_document(reference)
        if changed_source:
            current_reference["packet_sha256"] = "0" * 64
        locked_receipt = {
            "schema_version": "ctcc.demo_account_locked_source_join.v4",
            "join_receipt_sha256": "a" * 64,
            "join_policy_sha256": source_join.V7_ORDERED_POLICY_SHA256,
            "current_source_policy_sha256": current.V7_POLICY_SHA256,
            "history_original_db_chain_sha256": "4" * 64,
            "history_journal_terminal_db_recorded_at": (
                NOW - timedelta(seconds=1)
            ).isoformat(),
            "current_capture_started_at": (NOW + timedelta(seconds=1)).isoformat(),
            "history_terminal_db_timestamp_before_current_request": True,
            "history_commit_before_current_request": False,
            "current_source_reference": current_reference,
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
            "recorded_pre_lock_blocking_reasons": [
                "current_local_revision_readback_required",
                "history_tail_not_atomically_closed",
            ],
            "locked_readback_blocking_reasons": ["history_tail_not_atomically_closed"],
            "snapshot": None,
            "account_complete": False,
            "flat_start_permission": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        if locked_overrides is not None:
            locked_receipt.update(locked_overrides)
        return LockedAccountSourceJoinReadback(canonical(locked_receipt))

    monkeypatch.setattr(
        runtime, "_read_committed_history_before_http", read_before_http
    )
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
    assert session._used is True
    assert not runtime._CAPTURES and not boundary._BOUNDARIES
    return result, marker


@pytest.mark.asyncio
async def test_v7_same_invocation_joins_current_and_history_without_authority(
    monkeypatch,
):
    result, marker = await _synthetic_v7_join(monkeypatch)
    value = json.loads(result.receipt_json)
    assert marker["calls"] == ["pre_read", "native_current", "locked_join"]
    assert value["schema_version"] == "ctcc.native_current_history_join_diagnostic.v3"
    assert value["join_policy_sha256"] == source_join.V7_ORDERED_POLICY_SHA256
    assert value["current_source_policy_sha256"] == current.V7_POLICY_SHA256
    assert value["committed_history_readback_before_first_http"] is True
    assert value["pre_http_history_db_chain_sha256"] == "4" * 64
    assert value["pre_http_history_source_reference"]["capture_id"] == HISTORY_ID
    assert value["native_current_source_observed"] is True
    assert value["historical_native_source_observed"] is False
    assert value["history_tail_closed"] is False
    assert value["flat_start_permission"] is False
    assert value["locked_readback_blocking_reasons"] == [
        "history_tail_not_atomically_closed"
    ]
    assert result.owner is None and result.snapshot is None
    assert result.account_complete is False and result.execution_authority is False
    assert value["admission"] == "DENY"
    with pytest.raises(proof.NativeAccountProofError, match="unavailable"):
        runtime._consume_current_native_capture(
            marker["carrier"],
            invocation=marker["invocation"],
            expected_receipt_sha256=value["native_current_receipt_sha256"],
        )


@pytest.mark.asyncio
async def test_v7_pre_read_failure_prevents_any_current_http_or_locked_join(
    monkeypatch,
):
    result, marker = await _synthetic_v7_join(monkeypatch, pre_read_failure=True)
    assert marker["calls"] == ["pre_read"]
    value = json.loads(result.receipt_json)
    assert value["code"] == "native_account_history_join_denied"
    assert value["execution_authority"] is False


@pytest.mark.asyncio
async def test_v7_uncommitted_history_wait_expires_without_http(monkeypatch):
    monkeypatch.setattr(runtime, "_PRE_HISTORY_READBACK_TIMEOUT_SECONDS", 0.01)
    result, marker = await _synthetic_v7_join(
        monkeypatch, pre_read_gate=asyncio.Event()
    )
    assert marker["calls"] == ["pre_read"]
    assert (
        json.loads(result.receipt_json)["code"] == "native_account_history_join_denied"
    )


@pytest.mark.asyncio
async def test_v7_current_capture_waits_for_pre_read_transaction_to_finish(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    progress = []
    task = asyncio.create_task(
        _synthetic_v7_join(
            monkeypatch,
            pre_read_gate=release,
            pre_read_entered=entered,
            progress=progress,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert progress == ["pre_read"]
    assert not task.done()
    release.set()
    result, marker = await asyncio.wait_for(task, timeout=5)
    assert marker["calls"] == ["pre_read", "native_current", "locked_join"]
    assert (
        json.loads(result.receipt_json)["committed_history_readback_before_first_http"]
        is True
    )


@pytest.mark.asyncio
async def test_v7_first_http_at_readback_time_is_denied(monkeypatch):
    result, marker = await _synthetic_v7_join(monkeypatch, first_http_reversed=True)
    assert marker["calls"] == ["pre_read", "native_current", "locked_join"]
    value = json.loads(result.receipt_json)
    assert value["code"] == "native_account_history_join_denied"
    assert value["account_complete"] is value["execution_authority"] is False


@pytest.mark.asyncio
async def test_first_public_time_request_requires_completed_pre_readback(monkeypatch):
    plan = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    counter = 0

    def stamp():
        nonlocal counter
        counter += 1
        return {
            "utc_ns": int(NOW.timestamp() * 1_000_000_000) + counter * 1_000_000,
            "monotonic_ns": counter * 1_000_000,
        }

    monkeypatch.setattr(native.clock, "native_stamp", stamp)
    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin,
            scope_sha256=proof.scope_sha256(
                runtime.LedgerScope(
                    account_id=plan.expected_uid,
                    settlement_currency="USDT",
                )
            ),
            _claimed_session=session,
        ) as stage,
    ):
        state = native._state(stage)
        state["clock_results"]["before"] = "accepted"
        readback = native._sample(stage)
        state["required_pre_history_readback"] = readback
        trace = runtime._TimeProbeTrace(
            stage, proof._exchange_plan(state["started"]), "before"
        )
        with pytest.raises(
            proof.NativeAccountProofError,
            match="native_account_history_readback_not_before_http",
        ):
            trace.begin_request(
                endpoint="/api/v5/public/time", query=(), stamp=readback
            )
        later = native._sample(stage)
        trace.begin_request(endpoint="/api/v5/public/time", query=(), stamp=later)
        assert state["first_http_request_start"] == later


@pytest.mark.asyncio
async def test_pre_http_history_readback_replays_exact_v5_chain(monkeypatch):
    historical = await recorded(monkeypatch)
    selected = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    counter = 0

    def stamp():
        nonlocal counter
        counter += 1
        return {
            "utc_ns": int((NOW + timedelta(seconds=2)).timestamp() * 1_000_000_000)
            + counter * 1_000_000,
            "monotonic_ns": counter * 1_000_000,
        }

    monkeypatch.setattr(native.clock, "native_stamp", stamp)
    history_reference = observed.source_reference(historical)

    class ReadOnlyRepo:
        async def read_chain(self, scope, capture_id):
            assert scope.account_id == selected.expected_uid
            return historical

    scope = runtime.LedgerScope(
        account_id=selected.expected_uid, settlement_currency="USDT"
    )
    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin,
            scope_sha256=proof.scope_sha256(scope),
            _claimed_session=session,
        ) as stage,
    ):
        result = await runtime._read_committed_history_before_http(
            stage,
            ReadOnlyRepo(),
            scope,
            history_reference.capture_id,
            selected,
        )
        assert result.reference == history_reference
        assert result.db_chain_sha256 == source_join._recorded_db_chain_sha256(
            historical
        )
        assert result.history_query_receipt_sha256
        assert result.completed["monotonic_ns"] < result.verified["monotonic_ns"]
        with pytest.raises(
            proof.NativeAccountProofError,
            match="native_account_pre_http_history_readback_invalid",
        ):
            await runtime._read_committed_history_before_http(
                stage,
                ReadOnlyRepo(),
                scope,
                "0" * 32,
                selected,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["changed_source", "expired", "wrong_invocation"],
)
async def test_v7_changed_source_lease_or_invocation_denied(monkeypatch, case):
    result, marker = await _synthetic_v7_join(
        monkeypatch,
        changed_source=case == "changed_source",
        expired=case == "expired",
        wrong_invocation=case == "wrong_invocation",
    )
    value = json.loads(result.receipt_json)
    assert marker["calls"] == (
        ["pre_read", "native_current"]
        if case == "wrong_invocation"
        else ["pre_read", "native_current", "locked_join"]
    )
    assert value["schema_version"] == "ctcc.native_current_history_join_diagnostic.v3"
    assert value["code"] == "native_account_history_join_denied"
    assert value["flat_start_permission"] is False
    assert value["snapshot"] is None and value["admission"] == "DENY"
    assert result.account_complete is False and result.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "locked_overrides",
    [
        {"schema_version": "ctcc.demo_account_locked_source_join.v3"},
        {"join_receipt_sha256": None},
        {"join_receipt_sha256": "invalid"},
        {"join_policy_sha256": source_join.POLICY_SHA256},
        {"join_policy_sha256": source_join.V7_POLICY_SHA256},
        {"current_source_policy_sha256": current.V6_POLICY_SHA256},
        {"history_terminal_db_timestamp_before_current_request": False},
        {"history_commit_before_current_request": True},
        {"history_original_db_chain_sha256": "0" * 64},
        {"history_journal_terminal_db_recorded_at": NOW.isoformat()},
        {"current_capture_started_at": (NOW - timedelta(seconds=2)).isoformat()},
        {"history_journal_terminal_db_recorded_at": "invalid"},
        {"flat_start_permission": True},
        {"recorded_pre_lock_blocking_reasons": []},
        {"recorded_pre_lock_blocking_reasons": ["unrelated_blocker"]},
        {"scope_sha256": "0" * 64},
        {"session_binding_sha256": "0" * 64},
        {"recorded_local_checkpoint_sha256": "0" * 64},
        {"db_local_state_sha256": "0" * 64},
        {"history_source_reference": {"capture_id": "0" * 32}},
        {"history_tail_closed": True},
        {"account_complete": True},
        {"execution_authority": True},
    ],
)
async def test_v7_locked_source_schema_policy_session_and_checkpoint_pins(
    monkeypatch, locked_overrides
):
    result, marker = await _synthetic_v7_join(
        monkeypatch, locked_overrides=locked_overrides
    )
    value = json.loads(result.receipt_json)
    assert marker["calls"] == ["pre_read", "native_current", "locked_join"]
    assert value["code"] == "native_account_history_join_denied"
    assert value["flat_start_permission"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "native_overrides",
    [
        {"schema_version": "ctcc.initial_native_account_diagnostic.v2"},
        {"policy_sha256": proof.V3_POLICY_SHA256},
        {"source_reference": {"capture_id": "0" * 32}},
        {"account_complete": True},
        {"execution_authority": True},
    ],
)
async def test_v7_native_diagnostic_relabel_cannot_claim_locked_join(
    monkeypatch, native_overrides
):
    result, marker = await _synthetic_v7_join(
        monkeypatch, native_overrides=native_overrides
    )
    value = json.loads(result.receipt_json)
    assert marker["calls"] == ["pre_read", "native_current", "locked_join"]
    assert value["code"] == "native_account_history_join_denied"
    assert value["flat_start_permission"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v7_invalid_history_locator_rejected_before_synthetic_source(monkeypatch):
    plan = current_plan_v7()
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
    assert session._used is False and not runtime._CAPTURES
