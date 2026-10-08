"""Synthetic original B1 journals only; no authenticated OKX or order access."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_gateway_causality as gateway
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_history_join import current_plan, recorded_current
from tests.unit.test_account_current_source_verifier import SCOPE, flat_pages
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_qualification_account_capture import NOW, UID, wire
from tests.unit.test_qualification_account_collector import SECRETS, credentials
from tests.unit.test_qualification_account_v4 import script as account_script


def audit(chain, **changes):
    return gateway.audit_recorded_demo_account_gateway_causality(
        **{
            "chain": chain,
            "reference": observed.source_reference(chain),
            "scope": SCOPE,
            "validated_at": NOW + timedelta(seconds=2),
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_missing_gateway_window_is_explicitly_incomplete(monkeypatch):
    chain, _ = await recorded_current(monkeypatch)
    result = audit(chain)
    receipt = json.loads(result.receipt_json)
    assert receipt["gateway_causal_coverage_complete"] is False
    assert receipt["missing_gateway_time_request_indices"] == list(
        range(len(receipt["pages"]))
    )
    assert "exchange_gateway_time_missing" in receipt["blocking_reasons"]
    assert all(
        page["gateway_in_time_raw"] is None
        and page["gateway_out_time_raw"] is None
        and page["gateway_causal_window_verified"] is False
        for page in receipt["pages"]
    )
    assert receipt["source_reference"] == observed.reference_document(
        observed.source_reference(chain)
    )
    assert result.receipt_json == gateway.canonical(receipt)
    assert result.snapshot is None
    assert result.account_complete is result.execution_authority is False
    assert receipt["admission"] == "DENY"
    assert UID.encode() not in result.receipt_json
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)


@pytest.mark.asyncio
async def test_measured_gateway_window_covers_every_original_page(monkeypatch):
    holder = {}

    def gateway_envelope(_stream, _index, rows):
        request = holder["harness"].requests[-1]
        started = datetime.fromisoformat(request.headers["OK-ACCESS-TIMESTAMP"])
        delta = started - datetime(1970, 1, 1, tzinfo=UTC)
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return wire(rows, inTime=str(micros + 100), outTime=str(micros + 200))

    session, harness, _, arguments, events = setup(monkeypatch, change=gateway_envelope)
    holder["harness"] = harness
    selected = current_plan()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    harness.script = account_script(
        source=flat_pages(), streams=capture.V6_CURRENT_STREAMS
    )
    harness.clock.overrides.update(
        {
            index: NOW + timedelta(seconds=1, milliseconds=index + 1)
            for index in range(300)
        }
    )
    await bootstrap.collect_bootstrap_recorded(session, **arguments)
    harness.assert_closed()
    result = audit(tuple(events))
    receipt = json.loads(result.receipt_json)
    assert len(receipt["pages"]) == len(harness.requests)
    assert receipt["gateway_causal_coverage_complete"] is True
    assert receipt["missing_gateway_time_request_indices"] == []
    assert "exchange_gateway_time_missing" not in receipt["blocking_reasons"]
    assert all(
        page["gateway_causal_window_verified"] is True for page in receipt["pages"]
    )
    assert receipt["blocking_reasons"] == [
        "account_revision_unverified",
        "algo_type_coverage_incomplete",
        "source_authenticity_unverified",
    ]
    assert receipt["snapshot"] is None
    assert receipt["account_complete"] is False
    assert receipt["source_authenticity_verified"] is False
    assert receipt["execution_authority"] is False
    assert receipt["admission"] == "DENY"


@pytest.mark.asyncio
async def test_missing_original_terminal_or_changed_pin_cannot_be_audited(monkeypatch):
    chain, _ = await recorded_current(monkeypatch)
    with pytest.raises(gateway.AccountGatewayCausalityError):
        audit(chain[:-1], reference=observed.source_reference(chain))
    with pytest.raises(gateway.AccountGatewayCausalityError):
        audit(
            chain,
            reference=replace(observed.source_reference(chain), head_sha256="0" * 64),
        )
    with pytest.raises(
        gateway.AccountGatewayCausalityError,
        match="account_gateway_audit_inputs_invalid",
    ):
        audit(chain, expected_policy_sha256="0" * 64)


@pytest.mark.asyncio
async def test_direct_or_internally_inconsistent_receipt_cannot_mint_audit(monkeypatch):
    chain, _ = await recorded_current(monkeypatch)
    original = audit(chain).receipt_json
    with pytest.raises(
        gateway.AccountGatewayCausalityError,
        match="account_gateway_receipt_invalid",
    ):
        gateway.AccountGatewayCausalityAudit(original)
    with pytest.raises(gateway.AccountGatewayCausalityError):
        gateway.AccountGatewayCausalityAudit(
            b"x" * (gateway.MAX_RECEIPT_BYTES + 1), _issuer=gateway._ISSUER
        )
    with pytest.raises(gateway.AccountGatewayCausalityError):
        gateway.AccountGatewayCausalityAudit(b" " + original, _issuer=gateway._ISSUER)

    def forged(mutator):
        value = json.loads(original)
        mutator(value)
        with pytest.raises(
            gateway.AccountGatewayCausalityError,
            match="account_gateway_receipt_invalid",
        ):
            gateway.AccountGatewayCausalityAudit(
                gateway.canonical(value), _issuer=gateway._ISSUER
            )

    forged(lambda value: value.update(gateway_causal_coverage_complete=True))
    forged(lambda value: value.update(missing_gateway_time_request_indices=[]))
    forged(lambda value: value["pages"][0].update(request_index=True))
    forged(lambda value: value["pages"][1].update(page_index=1))
    forged(lambda value: value["pages"][0].update(gateway_causal_window_verified=True))
    forged(lambda value: value["source_reference"].update(head_sha256="0"))
    forged(lambda value: value.update(account_complete=True))
    forged(lambda value: value.update(source_authenticity_verified=True))
    forged(lambda value: value.update(execution_authority=True))
    forged(lambda value: value.update(admission="PASS"))
    forged(lambda value: value.update(blocking_reasons=[]))
