"""Synthetic V7 pure native proof; no owned TLS, account, or order authority."""

import json

import pytest

from app.domain.source_primitives import canonical
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_proof as proof
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_native_proof import companion_fixture, replay


@pytest.mark.asyncio
async def test_v7_eight_algo_pure_native_clock_proof_is_versioned_and_denied(
    monkeypatch,
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch, current_v7=True)
    checked = replay(raw, files, chain, scope)
    value = json.loads(raw)
    source = json.loads(checked.current_source_receipt_json)
    assert value["schema_version"] == proof.V7_FLAT_SCHEMA
    assert value["policy_sha256"] == proof.V7_FLAT_POLICY_SHA256
    assert source["schema_version"] == "ctcc.current_account_source_observation.v6"
    assert source["policy_sha256"] == current.V7_POLICY_SHA256
    assert source["observed_flat"] is True
    assert set(source["inventory_row_counts"]) == set(current.V7_INVENTORY_STREAMS)
    assert all(count == 0 for count in source["inventory_row_counts"].values())
    assert tuple(
        dict.fromkeys(page.request.stream for page in checked.packet.observations)
    ) == (capture.V7_CURRENT_STREAMS)
    assert checked.account_complete is checked.execution_authority is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v7_pure_proof_rejects_policy_relabel_old_capture_and_missing_algo_chain(
    monkeypatch,
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch, current_v7=True)
    value = json.loads(raw)
    for schema, policy in (
        (proof.V7_FLAT_SCHEMA, proof.V3_POLICY_SHA256),
        (proof.V3_SCHEMA, proof.V3_POLICY_SHA256),
        (proof.EXPOSED_V4_SCHEMA, proof.EXPOSED_V4_POLICY_SHA256),
    ):
        with pytest.raises(proof.NativeAccountProofError):
            replay(
                canonical({**value, "schema_version": schema, "policy_sha256": policy}),
                files,
                chain,
                scope,
            )
    missing_chase = tuple(
        item
        for item in chain
        if journal.checked_event(item.event)["data"].get("stream") != "algo_chase"
    )
    assert len(missing_chase) < len(chain)
    with pytest.raises(proof.NativeAccountProofError):
        replay(raw, files, missing_chase, scope)


@pytest.mark.asyncio
async def test_v6_four_algo_flat_source_stays_revoked_under_v3_or_v7_proof(monkeypatch):
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, current_only=True, current_v7=False, verify_baseline=False
    )
    value = json.loads(raw)
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_current_sources_incomplete_or_exposed",
    ):
        replay(raw, files, chain, scope)
    with pytest.raises(proof.NativeAccountProofError):
        replay(
            canonical(
                {
                    **value,
                    "schema_version": proof.V7_FLAT_SCHEMA,
                    "policy_sha256": proof.V7_FLAT_POLICY_SHA256,
                }
            ),
            files,
            chain,
            scope,
        )


def test_v7_proof_policy_pins_exact_eight_algo_inventory_and_no_authority():
    assert proof.POLICY_SHA256 == (
        "4dccdd41b32b287f5d45c4ef32285732f63ff126f682b6a45afc9d7e30f436b5"
    )
    assert proof.V3_POLICY_SHA256 == (
        "e65bd1ce979f78adede31406b1f934a245a0feae366865403965b6848ca8592e"
    )
    assert proof.EXPOSED_V4_POLICY_SHA256 == (
        "af20645d7170285165ff65c5006392a73684889d4446a8f273e471afcde0dd75"
    )
    plan = current_plan_v7()
    assert proof.contract_for_plan(plan) == (
        proof.V7_FLAT_SCHEMA,
        proof.V7_FLAT_POLICY_SHA256,
    )
    value = json.loads(proof.V7_FLAT_POLICY_BYTES)
    assert value["account_current_policy_sha256"] == current.V7_POLICY_SHA256
    assert value["current_streams"] == list(capture.V7_CURRENT_STREAMS)
    assert value["current_algo_types"] == list(capture.CURRENT_ALGO_ORDER_TYPES_V7)
    assert value["historical_hwm_clock_verified"] is False
    assert value["account_complete"] is value["execution_authority"] is False
