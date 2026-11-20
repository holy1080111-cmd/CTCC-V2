"""Stopped orchestration with synthetic SQL/source handoffs; no venue credit."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.okx_live import lifecycle_key_v1 as keys
from app.okx_live import native_runtime_v1 as runtime
from app.okx_live import owned_lifecycle_v1 as lifecycle
from app.okx_live import owned_recovery_v1 as subject
from tests import native_live_postsubmit_fixtures_v1 as fixture

STOPPED = {"armed": False, "disarmed": True, "emergency_stop": True, "revision": 10}


def harness(monkeypatch, *, control=None, terminal=False, missing_time=False):
    calls, runtime_owner, key_owner, settings = [], object(), object(), object()
    intent = fixture.intent()
    rid, raw, digest = exact_live_intent_document_v1(intent)
    journal = SimpleNamespace(
        reservation_id=rid,
        reservation_state="reconciled" if terminal else "reserved",
        intent=intent,
        intent_json=raw,
        intent_sha256=digest,
        intent_recorded_at=None if missing_time else utc_from_ns(fixture.BASE),
        proofs=(canonical({"outcome": "ZERO_FILL_FLAT", "report_id": None}),)
        if terminal
        else (),
    )
    cfg = {
        "source_root": "declared_private_source",
        "evidence_root": "declared_private_evidence",
        "region": "global",
    }

    @asynccontextmanager
    async def configured():
        calls.append("runtime_open")
        try:
            yield runtime_owner
        finally:
            calls.append("runtime_closed")

    class Repository:
        async def preflight(self, key):
            assert key is key_owner
            calls.append("sql_preflight")

        async def inhibit_for_recovery_v1(self, key):
            assert key is key_owner
            calls.append("sql_inhibit_committed")

        async def read_control_v1(self, key):
            assert key is key_owner
            calls.append("sql_control_independent")
            return dict(STOPPED if control is None else control)

        async def read_journal_v1(self, key, reservation_id):
            assert key is key_owner and reservation_id == rid
            calls.append("sql_intent_independent")
            return journal

    repository = Repository()

    async def declared_no_controlled_records(*args):
        # This harness is an explicitly synthetic SQL handoff, never a real
        # absence proof. The actual repository is covered in its own PG lane.
        return None

    monkeypatch.setattr(
        subject, "_read_controlled_close_records_v1", declared_no_controlled_records
    )
    monkeypatch.setattr(runtime, "configured_live_runtime_v1", configured)
    monkeypatch.setattr(
        lifecycle, "_runtime_repository", lambda owner: (cfg, key_owner, repository)
    )
    monkeypatch.setattr(runtime, "_replay_live_runtime_v1", lambda owner, *, key: cfg)
    monkeypatch.setattr(subject, "_settings", lambda region, value: settings)
    monkeypatch.setattr(
        keys,
        "lifecycle_key_metadata_v1",
        lambda owner: {
            "environment": "live",
            "account_uid": intent.account_uid,
            "main_uid": intent.main_uid,
            "origin": fixture.ORIGIN,
        },
    )
    return calls, rid, journal, settings


@pytest.mark.asyncio
async def test_fresh_scope_starts_only_after_committed_stop_and_independent_readbacks(
    monkeypatch,
):
    calls, rid, journal, settings = harness(monkeypatch)
    captured = []

    async def source(owner):
        calls.append("fresh_get_scope")
        state, scope = subject._scope(owner)
        captured.append(owner)
        assert scope.settings is settings and state["journal"] is journal
        assert (
            state["source_owner"]
            is state["handoff"]
            is state["submission_started"]
            is None
        )
        assert (
            state["submission_attempted"] is False
            and state["submission_sha256"] is None
        )
        assert (
            state["recovery_history"].intent_recorded_at == journal.intent_recorded_at
        )
        assert state["recovery_history"].submission_started_utc is None
        return "synthetic_scoped_source_result"

    monkeypatch.setattr(subject, "_recover_with_scope", source)
    assert (
        await subject.reconcile_pending_owned_live_intent_v1(rid)
        == "synthetic_scoped_source_result"
    )
    assert calls == [
        "runtime_open",
        "sql_preflight",
        "sql_inhibit_committed",
        "sql_control_independent",
        "sql_intent_independent",
        "fresh_get_scope",
        "runtime_closed",
    ]
    assert captured[0] not in subject._SCOPES and captured[0] not in lifecycle._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["armed", "stop_clear", "arm_not_cleared", "missing_recorded"]
)
async def test_unverified_stop_or_original_intent_never_starts_http(
    monkeypatch, change
):
    control = dict(STOPPED)
    if change == "armed":
        control["armed"] = True
    elif change == "stop_clear":
        control["emergency_stop"] = False
    elif change == "arm_not_cleared":
        control["disarmed"] = False
    calls, rid, _, _ = harness(
        monkeypatch, control=control, missing_time=change == "missing_recorded"
    )

    async def forbidden(owner):
        pytest.fail("HTTP_scope_must_not_start")

    monkeypatch.setattr(subject, "_recover_with_scope", forbidden)
    with pytest.raises(subject.LiveRecoveryError):
        await subject.reconcile_pending_owned_live_intent_v1(rid)
    assert "sql_inhibit_committed" in calls and calls[-1] == "runtime_closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "timeout", "foreign_task"])
async def test_failure_clears_read_scope_without_arm_or_resubmit(monkeypatch, failure):
    calls, rid, _, _ = harness(monkeypatch)
    captured = []

    async def source(owner):
        captured.append(owner)
        if failure == "foreign_task":

            async def foreign():
                subject._scope(owner)

            await asyncio.create_task(foreign())
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise TimeoutError("DECLARED_SYNTHETIC_NO_VENUE")

    monkeypatch.setattr(subject, "_recover_with_scope", source)
    expected = (
        asyncio.CancelledError if failure == "cancel" else subject.LiveRecoveryError
    )
    with pytest.raises(expected):
        await subject.reconcile_pending_owned_live_intent_v1(rid)
    assert captured[0] not in subject._SCOPES and captured[0] not in lifecycle._OWNERS
    assert calls[-1] == "runtime_closed" and "sql_inhibit_committed" in calls


@pytest.mark.asyncio
async def test_existing_terminal_receipt_does_not_open_any_source_or_claim_current_protection(
    monkeypatch,
):
    calls, rid, _, _ = harness(monkeypatch, terminal=True)

    async def forbidden(owner):
        pytest.fail("terminal_record_needs_no_new_source")

    monkeypatch.setattr(subject, "_recover_with_scope", forbidden)
    receipt = await subject.reconcile_pending_owned_live_intent_v1(rid)
    public = lifecycle.live_lifecycle_public_projection_v1(receipt)
    assert (
        public["status"] == "ZERO_FILL_FLAT" and public["risk_hold_retained"] is False
    )
    assert (
        public["protection_verified"] is None and public["execution_authority"] is False
    )
    assert calls[-1] == "runtime_closed"


@pytest.mark.asyncio
async def test_no_private_scope_can_be_minted_by_a_saved_journal_or_constructor():
    owner = lifecycle._new_owner()
    try:
        with pytest.raises(subject.LiveRecoveryError):
            subject._scope(owner)
    finally:
        lifecycle._OWNERS.pop(owner, None)


def test_recovery_submission_observation_does_not_invent_original_http_response():
    intent = fixture.intent()
    state = {
        "intent": intent,
        "recovery_history": subject._historical_times(
            SimpleNamespace(
                intent=intent, intent_recorded_at=utc_from_ns(fixture.BASE), proofs=()
            )
        ),
    }
    document = decode(
        subject._submission_observation(
            state,
            SimpleNamespace(
                order=SimpleNamespace(receipt_sha256="1" * 64, raw_sha256="2" * 64)
            ),
            SimpleNamespace(order_id="123"),
        )
    )
    assert (
        document["semantics"]
        == "fresh_exact_exchange_order_not_original_http_submission"
    )
    assert (
        document["original_submission_started"] is None
        and document["execution_authority"] is False
    )
    assert not {
        "raw_headers",
        "raw_body",
        "started_at",
        "request_attempted",
    }.intersection(document)


@pytest.mark.asyncio
async def test_pure_history_or_constructed_owner_cannot_reach_signing_without_private_scope():
    key = object()
    intent = fixture.intent()
    owner = lifecycle._new_owner(
        key_owner=key,
        recovery_history=subject._historical_times(
            SimpleNamespace(
                intent=intent, intent_recorded_at=utc_from_ns(fixture.BASE), proofs=()
            )
        ),
    )
    try:
        lifecycle._OWNERS[owner]["phase"] = "source_verified"
        with pytest.raises(subject.LiveRecoveryError):
            lifecycle._verify_source_for_signing_v1(owner, key_owner=key)
    finally:
        lifecycle._OWNERS.pop(owner, None)


def test_report_id_preserves_original_signed_lineage_or_unknown():
    assert subject._retained_report_id(SimpleNamespace(proofs=())) is None
    assert (
        subject._retained_report_id(
            SimpleNamespace(
                proofs=(
                    canonical({"report_id": "original_report"}),
                    canonical({"report_id": None}),
                )
            )
        )
        == "original_report"
    )
    with pytest.raises(subject.LiveRecoveryError):
        subject._retained_report_id(
            SimpleNamespace(
                proofs=tuple(
                    canonical({"report_id": value})
                    for value in ("original_report", "changed_report")
                )
            )
        )


@pytest.mark.asyncio
async def test_existing_http_reconcile_action_calls_the_real_new_recovery_delegate(
    monkeypatch,
):
    import httpx

    from app.api import native_live_manual_v1 as api
    from app.api import security

    calls, rid, journal, _ = harness(monkeypatch)
    token = "declared-synthetic-recovery-http-token-000000000"
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_token_is_safe=True, api_token_value=token),
    )
    # Only unused entry/coordinator import is inert. The original Live worker,
    # lifecycle delegate and new recovery function execute their actual code.
    monkeypatch.setattr(api, "_modules", lambda: (runtime, None, lifecycle))

    async def source(owner):
        state, _ = subject._scope(owner)
        assert state["journal"] is journal
        calls.append("synthetic_fresh_get_handoff")
        observed = SimpleNamespace(
            reservation_id=rid,
            reservation_state="reserved",
            proofs=(canonical({"outcome": "UNCERTAIN", "report_id": None}),),
        )
        return lifecycle._new_receipt(observed, dict(STOPPED))

    monkeypatch.setattr(subject, "_recover_with_scope", source)
    worker = api.LiveToolWorker()
    app = api._build_app(worker)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.post(
            "/api/live-tool/reconcile",
            headers={"X-CTCC-Token": token},
            json={"reservation_id": rid},
        )
        assert response.status_code == 202
        await worker.task
        view = worker.snapshot()["job"]
        assert view["status"] == "UNCERTAIN"
        assert view["result"]["risk_hold_retained"] is True
        assert view["result"]["protection_verified"] is None
        assert view["result"]["execution_authority"] is False
    assert calls == [
        "runtime_open",
        "sql_preflight",
        "sql_inhibit_committed",
        "sql_control_independent",
        "sql_intent_independent",
        "synthetic_fresh_get_handoff",
        "runtime_closed",
    ]
