"""Synthetic V7 gateway coverage; every result remains a read-only DENY."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_gateway_causality as gateway
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_source_v7 import (
    current_plan_v7,
    empty_v7_pages,
    recorded_v7,
)
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, wire
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_account_v4 import script as account_script


def audit_v7(chain, **changes):
    return gateway.audit_recorded_demo_account_gateway_causality(
        **{
            "chain": chain,
            "reference": observed.source_reference(chain),
            "scope": SCOPE,
            "validated_at": NOW + timedelta(seconds=4),
            "expected_policy_sha256": gateway.V7_POLICY_SHA256,
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_v7_gateway_audit_covers_all_eight_algo_chains_and_missing_time(
    monkeypatch,
):
    chain, harness = await recorded_v7(monkeypatch)
    receipt = json.loads(audit_v7(chain).receipt_json)
    assert receipt["schema_version"] == "ctcc.demo_account_gateway_causality_audit.v2"
    assert receipt["policy_sha256"] == gateway.V7_POLICY_SHA256
    assert len(receipt["pages"]) == len(harness.requests)
    assert tuple(dict.fromkeys(page["stream"] for page in receipt["pages"])) == (
        capture.V7_CURRENT_STREAMS
    )
    assert [
        page["stream"]
        for page in receipt["pages"]
        if page["stream"].startswith("algo_")
    ] == [f"algo_{kind}" for kind in capture.CURRENT_ALGO_ORDER_TYPES_V7]
    assert receipt["gateway_causal_coverage_complete"] is False
    assert receipt["missing_gateway_time_request_indices"] == list(
        range(len(receipt["pages"]))
    )
    assert "exchange_gateway_time_missing" in receipt["blocking_reasons"]
    assert "algo_type_coverage_incomplete" not in receipt["blocking_reasons"]
    assert receipt["snapshot"] is None
    assert receipt["account_complete"] is False
    assert receipt["source_authenticity_verified"] is False
    assert receipt["execution_authority"] is False
    assert receipt["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v7_gateway_audit_exact_policy_and_page_inventory_are_sealed(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    with pytest.raises(gateway.AccountGatewayCausalityError):
        audit_v7(chain, expected_policy_sha256=gateway.POLICY_SHA256)
    with pytest.raises(gateway.AccountGatewayCausalityError):
        audit_v7(chain[:-1], reference=observed.source_reference(chain))
    original = json.loads(audit_v7(chain).receipt_json)
    with pytest.raises(
        gateway.AccountGatewayCausalityError,
        match="account_gateway_receipt_invalid",
    ):
        gateway.AccountGatewayCausalityAudit(
            gateway.canonical(
                {
                    **original,
                    "pages": [
                        page
                        for page in original["pages"]
                        if page["stream"] != "algo_smart_iceberg"
                    ],
                }
            ),
            _issuer=gateway._ISSUER,
        )


@pytest.mark.asyncio
async def test_v7_measured_gateway_time_all_pages_never_grants_authority(monkeypatch):
    holder = {}

    def with_gateway_window(_stream, _index, rows):
        request = holder["harness"].requests[-1]
        started = datetime.fromisoformat(request.headers["OK-ACCESS-TIMESTAMP"])
        delta = started - datetime(1970, 1, 1, tzinfo=UTC)
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return wire(rows, inTime=str(micros + 100), outTime=str(micros + 200))

    session, harness, _, arguments, events = setup(
        monkeypatch, change=with_gateway_window
    )
    holder["harness"] = harness
    selected = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
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
    receipt = json.loads(audit_v7(tuple(events)).receipt_json)
    assert receipt["gateway_causal_coverage_complete"] is True
    assert receipt["missing_gateway_time_request_indices"] == []
    assert all(page["gateway_causal_window_verified"] for page in receipt["pages"])
    assert receipt["blocking_reasons"] == [
        "account_revision_unverified",
        "source_authenticity_unverified",
    ]
    assert receipt["account_complete"] is receipt["execution_authority"] is False
    assert receipt["admission"] == "DENY"
