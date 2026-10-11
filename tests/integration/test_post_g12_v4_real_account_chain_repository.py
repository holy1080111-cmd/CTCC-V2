"""Real PostgreSQL B1 chain readback at the V4 account boundary.

The account HTTP fixture, V3 timing pins, and native proof bytes are synthetic.
Only an explicitly supplied, migrated, isolated DATABASE_URL is used. This
does not establish exchange authenticity, account completeness, or authority.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_native_proof as native_proof
from app.trade_qualification import account_native_proof_storage as native_storage
from app.trade_qualification import post_g12_account_event_observation_v4 as bridge
from app.trade_qualification.account_capture_journal import digest
from tests.integration.test_account_locked_current_history_join_v7_repository import (
    _captured_v7_pair,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated PostgreSQL fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _utc(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def _stamp(instant, monotonic_ns):
    return {
        "utc_ns": int(instant.timestamp() * 1_000_000_000),
        "monotonic_ns": monotonic_ns,
    }


def _native_companion(root, reference, scope_sha256, first_http, deadline):
    """Synthetic V3 companion, pinned to the *real* current B1 reference."""
    parts = {name: b"{}" for name in native_proof.FILES}
    part_hashes = {name: sha(raw) for name, raw in parts.items()}
    persist_at = datetime.now(UTC) + timedelta(milliseconds=1)
    complete_at = persist_at + timedelta(milliseconds=1)
    assert first_http < persist_at < complete_at < deadline
    proof = canonical(
        {
            "schema_version": native_proof.V7_FLAT_SCHEMA,
            "policy_sha256": native_proof.V7_FLAT_POLICY_SHA256,
            "scope_sha256": scope_sha256,
            "source_reference": reference,
            "clock_file_sha256": part_hashes,
            "proof_persist_start": _stamp(persist_at, 100_000_000),
            "expires_at": deadline.isoformat(),
            "historical_hwm_clock_verified": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    identity = native_storage._root_identity(root)
    readback = canonical(
        {
            "schema_version": native_storage.V7_FLAT_READBACK_SCHEMA,
            "proof_sha256": sha(proof),
            "clock_file_sha256": part_hashes,
            "root_device": identity[0],
            "root_inode": identity[1],
            "readback_complete": _stamp(complete_at, 110_000_000),
            "execution_authority": False,
        }
    )
    for name, raw in (
        *parts.items(),
        ("proof.json", proof),
        ("readback.json", readback),
    ):
        (root / name).write_bytes(raw)
    return sha(proof), sha(readback), complete_at


async def test_v4_reads_real_v5_v7_b1_chains_before_separate_event_observation(
    monkeypatch, database, tmp_path
):
    scope, repo, (history_id, current_id) = await _captured_v7_pair(
        monkeypatch, database
    )
    assert type(repo) is AccountCaptureJournalRepository
    ledger = QualificationLedgerRepository(database[1], clock=lambda: datetime.now(UTC))
    ordered = await repo.read_locked_current_history_join(
        scope,
        history_capture_id=history_id,
        current_capture_id=current_id,
        expected_policy_sha256=bridge.account_join.V7_ORDERED_POLICY_SHA256,
    )
    record = bridge.decode(ordered.receipt_json)
    terminal = _utc(record["history_journal_terminal_db_recorded_at"])
    current_started = _utc(record["current_capture_started_at"])
    assert terminal < current_started
    gap = current_started - terminal
    history_read_start = terminal + gap / 3
    first_http = terminal + gap * 2 / 3
    assert terminal < history_read_start < first_http < current_started
    deadline = datetime.now(UTC) + timedelta(seconds=60)
    scope_sha256 = digest(
        canonical([scope.environment, scope.account_id, scope.settlement_currency])
    )
    account_root = tmp_path / "account"
    account_root.mkdir()
    current_reference = record["current_source_reference"]
    proof_pin, readback_pin, complete_at = _native_companion(
        account_root, current_reference, scope_sha256, first_http, deadline
    )
    v3_observed = complete_at + timedelta(milliseconds=1)
    expected = {
        "account_scope_sha256": scope_sha256,
        "account_plan_sha256": current_reference["plan_sha256"],
        "history_capture_id_sha256": sha(history_id.encode("ascii")),
        "deadline": deadline,
    }
    value = {
        "native_account_proof_sha256": proof_pin,
        "native_account_readback_sha256": readback_pin,
        "current_reference_sha256": sha(canonical(current_reference)),
        "history_db_chain_sha256": record["history_original_db_chain_sha256"],
        "history_reference_sha256": sha(canonical(record["history_source_reference"])),
        "account_history_readback_started_at": history_read_start.isoformat(),
        "account_session_first_http_started_at": first_http.isoformat(),
        "observed_at": v3_observed.isoformat(),
    }
    await asyncio.sleep(
        max(0, (v3_observed - datetime.now(UTC)).total_seconds()) + 0.002
    )
    root_identity = native_storage._root_identity(account_root)
    readback_sha256, received_at = await bridge._read_bound_account_sources(
        account_root,
        value,
        expected,
        root_identity=root_identity,
        session_factory=database[1],
        ledger=ledger,
        scope=scope,
        history_capture_id=history_id,
    )
    assert len(readback_sha256) == 64
    assert v3_observed < received_at < deadline
    observation = await ledger.read_event_observation(scope, "f" * 64)
    assert observation.request_started_at >= received_at
    assert observation.matched is None

    changed = {**value, "history_db_chain_sha256": "0" * 64}
    assert changed["history_db_chain_sha256"] != value["history_db_chain_sha256"]
    with pytest.raises(
        bridge.PostG12AccountEventObservationError, match="v4_account_readback_failed"
    ):
        await bridge._read_bound_account_sources(
            account_root,
            changed,
            expected,
            root_identity=root_identity,
            session_factory=database[1],
            ledger=ledger,
            scope=scope,
            history_capture_id=history_id,
        )
