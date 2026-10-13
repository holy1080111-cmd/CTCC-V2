"""Synthetic V7 row/gateway chronology; no account or order authority."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_row_gateway_causality as row_gateway
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_source_v7 import current_plan_v7, empty_v7_pages
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, ms, wire
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_account_v4 import script as account_script


async def recorded_with_gateway(monkeypatch, *, conflict=False, omit=False):
    holder = {}

    def respond(stream, _index, rows):
        request = holder["harness"].requests[-1]
        started = datetime.fromisoformat(request.headers["OK-ACCESS-TIMESTAMP"])
        if stream == "balance" and conflict:
            # This exchange row time is after outTime but no later than the
            # synthetic host headers receipt. The older parser accepts it.
            rows[0]["uTime"] = ms(started + timedelta(milliseconds=1))
        delta = started - datetime(1970, 1, 1, tzinfo=UTC)
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        if stream == "balance" and omit:
            return wire(rows)
        return wire(rows, inTime=str(micros + 100), outTime=str(micros + 200))

    _, harness, _, arguments, events = setup(monkeypatch, change=respond)
    holder["harness"] = harness
    plan = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    harness.script = account_script(
        source=empty_v7_pages(), streams=capture.V7_CURRENT_STREAMS
    )
    harness.clock.overrides.update(
        {
            index: NOW + timedelta(seconds=1, milliseconds=index + 1)
            for index in range(2000)
        }
    )
    await bootstrap.collect_bootstrap_recorded(session, **arguments)
    harness.assert_closed()
    return tuple(events)


def audit(chain, **changes):
    return row_gateway.audit_v7_account_row_gateway_causality(
        **{
            "chain": chain,
            "reference": observed.source_reference(chain),
            "scope": SCOPE,
            "validated_at": NOW + timedelta(seconds=4),
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_source_after_gateway_outtime_is_retained_as_blocking_conflict(
    monkeypatch,
):
    chain = await recorded_with_gateway(monkeypatch, conflict=True)
    result = audit(chain)
    receipt = json.loads(result.receipt_json)
    conflicts = receipt["source_timestamp_after_gateway_response_request_indices"]
    assert len(conflicts) == 1
    assert receipt["pages"][conflicts[0]]["status"] == "source_after_gateway"
    assert receipt["pages"][conflicts[0]]["source_after_gateway_count"] == 1
    assert "source_timestamp_after_gateway_response" in receipt["blocking_reasons"]
    assert receipt["account_complete"] is result.account_complete is False
    assert receipt["execution_authority"] is result.execution_authority is False
    assert receipt["admission"] == "DENY"
    assert (
        row_gateway.verify_v7_account_row_gateway_causality(
            result,
            chain=chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
            expected_receipt_sha256=result.receipt_sha256,
        ).receipt_json
        == result.receipt_json
    )


@pytest.mark.asyncio
async def test_missing_gateway_time_stays_unknown_not_host_completion(monkeypatch):
    chain = await recorded_with_gateway(monkeypatch, omit=True)
    receipt = json.loads(audit(chain).receipt_json)
    balance = next(
        page for page in receipt["pages"] if page["status"] == "gateway_missing"
    )
    assert balance["known_source_timestamp_count"] > 0
    assert balance["source_after_gateway_count"] == 0
    assert receipt["source_timestamp_after_gateway_response_request_indices"] == []
    assert "exchange_gateway_time_missing" in receipt["blocking_reasons"]
    assert receipt["source_authenticity_verified"] is False


@pytest.mark.asyncio
async def test_no_recorded_clock_conflict_does_not_mint_authority(monkeypatch):
    chain = await recorded_with_gateway(monkeypatch)
    result = audit(chain)
    receipt = json.loads(result.receipt_json)
    assert all(page["status"] == "checked" for page in receipt["pages"])
    assert receipt["source_timestamp_after_gateway_response_request_indices"] == []
    assert receipt["blocking_reasons"] == [
        "account_revision_unverified",
        "source_authenticity_unverified",
    ]
    assert receipt["snapshot"] is None
    assert receipt["execution_authority"] is False
    with pytest.raises(
        row_gateway.AccountRowGatewayCausalityError,
        match="account_row_gateway_readback_mismatch",
    ):
        row_gateway.verify_v7_account_row_gateway_causality(
            result,
            chain=chain,
            reference=observed.source_reference(chain),
            scope=SCOPE,
            validated_at=NOW + timedelta(seconds=4),
            expected_receipt_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_old_gateway_policy_and_caller_receipt_cannot_upgrade(monkeypatch):
    chain = await recorded_with_gateway(monkeypatch)
    with pytest.raises(row_gateway.AccountRowGatewayCausalityError):
        audit(chain, expected_policy_sha256="0" * 64)
    result = audit(chain)
    with pytest.raises(
        row_gateway.AccountRowGatewayCausalityError,
        match="account_row_gateway_receipt_invalid",
    ):
        row_gateway.AccountRowGatewayCausalityAudit(result.receipt_json)
