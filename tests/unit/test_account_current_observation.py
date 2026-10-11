"""V7 current-source index derivation over synthetic original B1 journals."""

import json
from datetime import timedelta

import pytest

from app.database.repositories import account_current_observation as index
from app.trade_qualification import account_current_source_verifier as source
from app.trade_qualification import account_observation_index as observed
from tests.durable_migration_fixtures import load_migration
from tests.unit.test_account_current_history_join import recorded_current
from tests.unit.test_account_current_source_v7 import recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE, recorded
from tests.unit.test_qualification_account_capture import NOW


def test_db0038_pins_current_source_and_diagnostic_policy_hashes():
    migration = load_migration("0038")
    assert migration._POLICY_SHA256 == index.POLICY_SHA256
    assert migration._SOURCE_POLICY_SHA256 == source.V7_POLICY_SHA256


@pytest.mark.asyncio
async def test_v7_eight_algo_original_chain_derives_only_a_diagnostic(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    reference = observed.source_reference(chain)
    document, receipt = index._derive(
        chain,
        reference,
        SCOPE,
        sequence=1,
        previous_sha256=None,
        validated_at=NOW + timedelta(seconds=4),
    )
    saved, source = json.loads(document), json.loads(receipt)
    assert saved["policy_sha256"] == index.POLICY_SHA256
    assert saved["current_source_policy_sha256"] == source["policy_sha256"]
    assert saved["source_reference"] == source["source_reference"]
    assert source["algo_type_coverage"]["all_eight_terminal_chains_replayed"]
    assert len(source["algo_type_coverage"]["terminal_receipt_sha256s"]) == 8
    assert saved["receipt_sha256"] == index.journal.digest(receipt)
    assert saved["account_complete"] is saved["execution_authority"] is False
    assert saved["flat_start_permission"] is False
    assert source["admission"] == "DENY"


@pytest.mark.asyncio
async def test_sealed_v5_chain_cannot_be_relabelled_as_v7(monkeypatch):
    chain = await recorded(monkeypatch)
    with pytest.raises(ValueError):
        index._derive(
            chain,
            observed.source_reference(chain),
            SCOPE,
            sequence=1,
            previous_sha256=None,
            validated_at=NOW + timedelta(seconds=4),
        )


@pytest.mark.asyncio
async def test_sealed_v6_four_algo_chain_cannot_be_relabelled_as_v7(monkeypatch):
    chain, _ = await recorded_current(monkeypatch)
    with pytest.raises(ValueError):
        index._derive(
            chain,
            observed.source_reference(chain),
            SCOPE,
            sequence=1,
            previous_sha256=None,
            validated_at=NOW + timedelta(seconds=4),
        )


@pytest.mark.asyncio
async def test_v7_missing_original_terminal_or_wrong_reference_is_denied(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    reference = observed.source_reference(chain)
    for changed, pinned in ((chain[:-1], reference), (chain, None)):
        with pytest.raises(ValueError):
            index._derive(
                changed,
                reference
                if pinned is not None
                else observed.CaptureReference(
                    reference.capture_id,
                    "0" * 64,
                    reference.plan_sha256,
                    reference.packet_sha256,
                    reference.session_binding_sha256,
                ),
                SCOPE,
                sequence=1,
                previous_sha256=None,
                validated_at=NOW + timedelta(seconds=4),
            )


@pytest.mark.asyncio
async def test_v7_sequence_and_clock_bypass_are_denied(monkeypatch):
    chain, _ = await recorded_v7(monkeypatch)
    reference = observed.source_reference(chain)
    for sequence, previous, at in (
        (2, None, NOW + timedelta(seconds=4)),
        (1, "a" * 64, NOW + timedelta(seconds=4)),
        (1, None, NOW - timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError):
            index._derive(
                chain,
                reference,
                SCOPE,
                sequence=sequence,
                previous_sha256=previous,
                validated_at=at,
            )
