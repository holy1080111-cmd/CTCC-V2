"""0033 DDL, clock and authority boundaries without a PostgreSQL fixture."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.database.models  # noqa: F401 - register migration metadata
from app.database.base import Base
from app.database.repositories.public_receipt_publication_ack import (
    PublicReceiptPublicationAckReadback,
    _bracketed_server_time,
    _checked_identity,
)
from tests.durable_migration_fixtures import RecordedDowngrade, load_migration


class Recorder:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, statement):
        self.sql.append(str(statement))


def test_0033_migration_matches_model_and_grants_no_role():
    migration = load_migration("0033")
    assert migration.revision == "0033"
    assert migration.down_revision == "0032"
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()
    ddl = "\n".join(recorder.sql)
    table = Base.metadata.tables["public_receipt_publication_acks"]
    assert "CREATE TABLE public.public_receipt_publication_acks" in ddl
    for constraint in table.constraints:
        assert constraint.name in ddl
    assert "CREATE ROLE" not in ddl
    assert "GRANT EXECUTE" not in ddl
    assert "REVOKE ALL ON TABLE public.public_receipt_publication_acks" in ddl
    assert "session_user" in ddl
    assert "public.public_receipt_witness_append(jsonb)" in ddl
    assert "public.public_receipt_witness_read(text)" in ddl
    assert "FOR KEY SHARE NOWAIT" in ddl
    assert "pg_catalog.clock_timestamp()" in ddl
    assert "pinned.transition IS DISTINCT FROM 'append_capture'" in ddl
    assert "public_receipt_ack_role_denied" in ddl


def test_0033_downgrade_requires_no_acks_and_never_drops_witness():
    commands = RecordedDowngrade("0033").commands
    ddl = "\n".join(value for kind, value in commands if kind == "execute")
    assert "public_receipt_ack_downgrade_requires_empty" in ddl
    assert ("drop", "public_receipt_publication_acks") in commands
    assert ("drop", "public_receipt_witness_revisions") not in commands


def test_host_and_database_clock_must_be_causally_bracketed():
    start = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    before = (start, 1_000_000_000)
    after = (start + timedelta(milliseconds=10), 1_010_000_000)
    assert _bracketed_server_time(
        before=before, after=after, server_time=start + timedelta(milliseconds=5)
    )
    assert not _bracketed_server_time(
        before=before, after=after, server_time=start - timedelta(microseconds=1)
    )
    assert not _bracketed_server_time(
        before=before, after=after, server_time=start + timedelta(milliseconds=11)
    )
    assert not _bracketed_server_time(
        before=before,
        after=(start + timedelta(milliseconds=20), 1_010_000_000),
        server_time=start + timedelta(milliseconds=5),
    )


def test_no_caller_boolean_can_promote_publication_ack():
    _checked_identity("a" * 64, 3)
    value = PublicReceiptPublicationAckReadback(
        journal_key="a" * 64,
        witness_revision=3,
        witness_record_sha256="b" * 64,
        checkpoint_sha256="c" * 64,
        capture_sequence=1,
        capture_head_sha256="d" * 64,
        witness_recorded_at=datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        acknowledged_at=datetime(2026, 10, 8, 12, 1, tzinfo=UTC),
        database_readback_at=datetime(2026, 10, 8, 12, 2, tzinfo=UTC),
        host_readback_clock_bracket_verified=True,
        host_ack_clock_bracket_verified=True,
    )
    assert not value.trusted_clock_verified
    # The ACK's timestamp precedes its own commit; its separate-session
    # readback timestamp is only in memory, not a historical availability pin.
    assert not value.historical_decision_availability_verified
    assert not value.independently_protected
    assert not value.predictive_oos_eligible
    assert not value.execution_authority
    with pytest.raises(TypeError):
        PublicReceiptPublicationAckReadback(
            **{
                field: getattr(value, field)
                for field in (
                    "journal_key",
                    "witness_revision",
                    "witness_record_sha256",
                    "checkpoint_sha256",
                    "capture_sequence",
                    "capture_head_sha256",
                    "witness_recorded_at",
                    "acknowledged_at",
                    "database_readback_at",
                    "host_readback_clock_bracket_verified",
                )
            },
            execution_authority=True,
        )
    with pytest.raises(TypeError):
        replace(value, predictive_oos_eligible=True)
    with pytest.raises((AttributeError, TypeError)):
        value.execution_authority = True
