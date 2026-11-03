"""Actual primary54 nonempty rollback; declared synthetic B1/B3 mechanics only."""

import hashlib
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import account_history_partition_v1 as records
from app.domain import collector_current_0054_catalog_v1 as current54
from scripts import current_contract_source_scopes_v1 as source_scopes
from tests.integration.test_account_history_partition_repository import (
    _ROOT,
    _append,
    _member,
    _migrate,
    _receipt_side,
    _restricted_role,
    _settings,
    _snapshot,
    _sources,
)
from tests.integration.test_account_history_partition_repository import (
    partition_primary as partition_primary,  # noqa: PLC0414 -- explicit pytest fixture binding
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _actual_owner_graph(admin):
    async with admin.connect() as connection:
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET TRANSACTION READ ONLY",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
        ):
            await connection.execute(text(statement))
        scope = source_scopes.current0054_source_scope(_ROOT)
        await connection.run_sync(
            lambda actual: source_scopes.verify_database_scope(actual, _ROOT, scope)
        )
        value = await connection.scalar(text(current54.OWNER_CATALOG_SQL))
        current54.observe_actual_owner_catalog(value)
        return value


async def test_current54_partition_nonempty_downgrade_retains_whole54_catalog_and_rows(
    monkeypatch, partition_primary
):
    # Retain actual primary54 mechanics for the current54 harness. The old52
    # experiment remains separate in its genuine fresh historical52 database.
    scope, documents, revisions, lease = await _sources(monkeypatch, partition_primary)
    member, _ = _member(scope, documents[-1])
    async with _restricted_role(partition_primary) as restricted:
        async with restricted.begin() as connection:
            await _settings(connection)
            await _append(connection, scope, documents[-1], member, revisions, lease)
        before = await _snapshot(partition_primary)
        owner_before = await _actual_owner_graph(partition_primary)
        assert before[0] == "0054" and sum(before[2]["counts"].values()) > 0
        with pytest.raises(
            DBAPIError, match="account_history_partition_downgrade_requires_empty"
        ):
            async with partition_primary.begin() as connection:
                await connection.run_sync(_migrate, "0049", "downgrade")
        after = await _snapshot(partition_primary)
        owner_after = await _actual_owner_graph(partition_primary)
        assert before == after and records.canonical(owner_before) == records.canonical(
            owner_after
        )
        directory = os.environ.get("CTCC_COVERAGE_RECEIPT_DIR")
        if directory is not None:
            output = (
                Path(directory) / "account-history-partition-current0054-migration.json"
            )
            result = {
                "schema_version": "ctcc.account_history_partition_migration_acceptance.v4",
                "manifest_sha256": hashlib.sha256(
                    (_ROOT / "MANIFEST.sha256").read_bytes()
                ).hexdigest(),
                "repository_sha256": hashlib.sha256(
                    (
                        _ROOT / "app/database/repositories/account_history_partition.py"
                    ).read_bytes()
                ).hexdigest(),
                "migration_sha256": hashlib.sha256(
                    (
                        _ROOT / "migrations/versions/0050_account_history_partition.py"
                    ).read_bytes()
                ).hexdigest(),
                "server_major": 17,
                "source_head": "0054",
                "owner_graph_before_sha256": records.digest(
                    records.canonical(owner_before)
                ),
                "owner_graph_after_sha256": records.digest(
                    records.canonical(owner_after)
                ),
                "before": _receipt_side(before),
                "after": _receipt_side(after),
                "refusal_code": "account_history_partition_downgrade_requires_empty",
                "downgrade_target": "0049",
                "native_baseline_admission": False,
                "execution_authority": False,
            }
            owner_artifact = (
                Path(directory)
                / "account-history-partition-current0054-owner-catalog.json"
            )
            owner_raw = records.canonical(
                {
                    "schema": "ctcc.current54.nonempty_rollback_owner_catalog.v1",
                    "source_head": "0054",
                    "database_head": "0054",
                    "before": owner_before,
                    "after": owner_after,
                    "native_baseline_admission": False,
                    "account_complete": False,
                    "execution_authority": False,
                }
            )
            with owner_artifact.open("xb") as stream:
                assert stream.write(owner_raw) == len(owner_raw)
                stream.flush()
                os.fsync(stream.fileno())
            assert owner_artifact.read_bytes() == owner_raw
            result["owner_artifact_sha256"] = records.digest(owner_raw)
            raw = records.canonical(result)
            with output.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            assert output.read_bytes() == raw
