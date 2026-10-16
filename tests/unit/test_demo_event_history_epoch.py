"""No unverified local epoch can become G6/account/execution authority."""

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.repositories.demo_event_history_epoch import (
    _INSERT_GUARD_PROSRC_SHA256,
    DemoEventHistoryEpochRepository,
    EventEpochBinding,
    EventHistoryEpochError,
    _digest,
    _read_rows,
    _uid_lock_key,
    _wire,
)
from app.database.repositories.qualification_ledger import _account_uid_lock_key
from app.trade_qualification.reservations import LedgerScope
from tests.durable_migration_fixtures import load_migration


def binding(**changes):
    values = {
        "environment": "demo",
        "account_id": "12345678901234567890",
        "credential_session_binding_sha256": "a" * 64,
        "claimed_source_sha256": "b" * 64,
        "policy_sha256": "c" * 64,
    }
    values.update(changes)
    return EventEpochBinding(**values)


def row(bound, sequence=1, previous=None, **changes):
    kind = "anchor_unverified" if sequence == 1 else "invalidated"
    reason = "source_history_unverified" if sequence == 1 else "source_gap"
    evidence = "d" * 64
    wire = _wire(
        bound,
        sequence=sequence,
        kind=kind,
        reason_code=reason,
        evidence_sha256=evidence,
        previous=previous,
    )
    values = {
        "epoch_id": bound.epoch_id,
        "sequence": sequence,
        "environment": bound.environment,
        "account_id": bound.account_id,
        "credential_session_binding_sha256": bound.credential_session_binding_sha256,
        "claimed_source_sha256": bound.claimed_source_sha256,
        "policy_sha256": bound.policy_sha256,
        "kind": kind,
        "reason_code": reason,
        "evidence_sha256": evidence,
        "previous_sha256": previous,
        "event_json": wire,
        "event_sha256": _digest(wire),
        "db_recorded_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_exact_uid_session_source_policy_define_epoch_without_raw_credential():
    base = binding()
    alternatives = (
        binding(account_id="12345678901234567891"),
        binding(credential_session_binding_sha256="f" * 64),
        binding(claimed_source_sha256="f" * 64),
        binding(policy_sha256="f" * 64),
    )
    assert len({base.epoch_id, *(item.epoch_id for item in alternatives)}) == 5
    ledger_scope = LedgerScope(account_id=base.account_id, settlement_currency="USDT")
    assert _uid_lock_key(base) == _account_uid_lock_key(ledger_scope)
    assert repr(base).find(base.account_id) == -1


@pytest.mark.parametrize(
    "change",
    (
        {"environment": "live"},
        {"account_id": "123-not-exact"},
        {"credential_session_binding_sha256": "A" * 64},
        {"claimed_source_sha256": "x" * 64},
        {"policy_sha256": ""},
    ),
)
def test_invalid_binding_is_rejected(change):
    with pytest.raises(EventHistoryEpochError, match="event_epoch_binding_invalid"):
        binding(**change)


def test_valid_chain_remains_deny_even_after_anchor_and_invalidation():
    bound = binding()
    first = row(bound)
    second = row(bound, 2, first.event_sha256)
    for rows in ([], [first], [first, second]):
        result = _read_rows(bound, rows)
        assert result.admission == "DENY"
        assert result.history_complete is False
        assert result.source_authenticity_verified is False
        assert result.execution_authority is False
        assert result.anchor_observed is bool(rows)
        assert result.invalidated is (len(rows) == 2)
    assert DemoEventHistoryEpochRepository.__doc__.startswith("Local audit only")


@pytest.mark.parametrize(
    "change",
    (
        {"sequence": 2},
        {"previous_sha256": "f" * 64},
        {"event_sha256": "f" * 64},
        {"event_json": "{}"},
        {"kind": "complete"},
        {"reason_code": "complete"},
        {"claimed_source_sha256": "f" * 64},
    ),
)
def test_readback_rejects_tampered_anchor_or_positive_claim(change):
    with pytest.raises(EventHistoryEpochError):
        _read_rows(binding(), [row(binding(), **change)])


def test_readback_requires_contiguous_sequence_link_and_database_clock():
    bound = binding()
    first = row(bound)
    second = row(
        bound,
        2,
        first.event_sha256,
        db_recorded_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(EventHistoryEpochError, match="event_epoch_chain_invalid"):
        _read_rows(bound, [first, second])


def test_db0041_sql_requires_deny_only_guard_and_retention():
    migration = load_migration("0041")
    assert migration.down_revision == "0040"
    commands = []
    migration.op = type(
        "Recorder", (), {"execute": lambda self, sql: commands.append(sql)}
    )()
    migration.upgrade()
    sql = "\n".join(commands)
    assert "environment,account_id,credential_session_binding_sha256" in sql
    assert "kind='anchor_unverified'" in sql
    assert "kind='invalidated'" in sql
    for denied in (
        "history_complete",
        "source_authenticity_verified",
        "execution_authority",
    ):
        assert f"j->'{denied}' IS DISTINCT FROM 'false'::jsonb" in sql
    assert "admission' IS DISTINCT FROM 'DENY'" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "BEFORE TRUNCATE" in sql
    assert "qualification_ledger_immutable()" in sql
    assert "pg_advisory_xact_lock(lock_key)" in sql
    guard = next(
        command
        for command in commands
        if "CREATE FUNCTION public.demo_event_history_epoch_insert_guard()" in command
    )
    body = guard.split("AS $$", 1)[1].split("$$ LANGUAGE", 1)[0]
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        _INSERT_GUARD_PROSRC_SHA256
    )
