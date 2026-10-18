"""Offline DB0044 computation tests; no PostgreSQL or account authentication."""

import json
from types import SimpleNamespace

import pytest

from app.database.repositories.account_measured_hwm_v2 import (
    AccountMeasuredHWMError,
    AccountMeasuredHWMRepository,
    MeasuredHWMReadback,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_measured_hwm_continuation as hwm
from app.trade_qualification import account_observation_index as observed
from tests.durable_migration_fixtures import RecordedDowngrade, load_migration
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_portfolio_components import measurement


def _row(sequence, projection, *, peak_sequence):
    return SimpleNamespace(
        sequence=sequence,
        event_sha256=f"{sequence:064x}",
        projection_sha256=projection.receipt_sha256,
        projection_json=projection.receipt_json.decode(),
        genesis_sequence=1,
        peak_sequence=peak_sequence,
    )


def _batch(sequence):
    return SimpleNamespace(
        sequence=sequence,
        previous_sha256=None if sequence == 1 else f"{sequence - 1:064x}",
        event_sha256=f"{sequence:064x}",
    )


@pytest.mark.asyncio
async def test_repository_computation_uses_only_current_genesis_and_selected_peak(
    monkeypatch,
):
    samples = [
        (await measurement(monkeypatch, equity=value))[1]
        for value in ("1000", "1200", "900", "1200")
    ]
    repository = object.__new__(AccountMeasuredHWMRepository)
    witness_reads = []
    current = None
    witnesses = {}

    async def original(_session, _scope, _batch):
        data = json.loads(current.source_json)
        return observed.CaptureReference(**data["source_reference"]), current

    async def saved(_session, _scope, sequence):
        witness_reads.append(sequence)
        return SimpleNamespace(), witnesses[sequence]

    monkeypatch.setattr(repository, "_original", original)
    monkeypatch.setattr(repository, "_saved_witness", saved)
    predecessor = None
    peak_sequences = []
    for sequence, current in enumerate(samples, 1):
        projected, actual, peak_sequence = await repository._compute(
            None, SCOPE, _batch(sequence), predecessor
        )
        assert actual is current
        assert projected.execution_authority is False
        assert json.loads(projected.receipt_json)["admission"] == "DENY"
        peak_sequences.append(peak_sequence)
        witnesses[sequence] = current
        predecessor = _row(sequence, projected, peak_sequence=peak_sequence)
    assert peak_sequences == [1, 2, 2, 2]
    assert witness_reads == [1, 1, 1, 2, 1, 2]


@pytest.mark.asyncio
async def test_repository_computation_rejects_prefix_gap_before_witness_read(
    monkeypatch,
):
    _, sample = await measurement(monkeypatch, equity="1000")
    repository = object.__new__(AccountMeasuredHWMRepository)

    async def original(_session, _scope, _batch):
        data = json.loads(sample.source_json)
        return observed.CaptureReference(**data["source_reference"]), sample

    async def never(*_args):
        pytest.fail("gap must fail before genesis/peak witness read")

    monkeypatch.setattr(repository, "_original", original)
    monkeypatch.setattr(repository, "_saved_witness", never)
    first = hwm.advance_measured_hwm(
        SCOPE,
        sequence=1,
        previous_event_sha256=None,
        event_sha256=f"{1:064x}",
        measurement=sample,
        expected_reference=observed.CaptureReference(
            **json.loads(sample.source_json)["source_reference"]
        ),
    )
    with pytest.raises(AccountMeasuredHWMError, match="predecessor_mismatch"):
        await repository._compute(
            None, SCOPE, _batch(3), _row(1, first, peak_sequence=1)
        )


def test_db0044_contract_remains_denied_and_downgrade_refuses_evidence():
    migration = load_migration("0044")
    sql = migration._guard()
    assert migration.down_revision == "0043"
    assert hwm.POLICY_SHA256 in sql
    assert "pg_advisory_xact_lock" in sql
    assert "account_measured_hwm_v2_membership_denied" in sql
    assert "account_complete" in sql and "execution_authority" in sql
    commands = RecordedDowngrade("0044").commands
    assert commands[0][0] == "execute" and "NOWAIT" in commands[0][1]
    assert "account_measured_hwm_v2_downgrade_requires_empty" in commands[1][1]
    assert commands[2] == ("drop", "demo_account_measured_hwm_v2")
    result = MeasuredHWMReadback(b"{}", journal.digest(b"{}"), 1, "0" * 64)
    assert result.durable_membership_verified is False
    assert result.native_clock_verified is False
    assert result.account_complete is False
    assert result.execution_authority is False
    assert result.admission == "DENY"
