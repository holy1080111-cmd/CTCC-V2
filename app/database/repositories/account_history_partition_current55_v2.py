"""Exact existing partition transaction/readback with NEW current55 append RPC.

The original repository/module and stopped RPC are untouched. Readback types,
JSON/genesis/checkpoint/CAS, one-attempt burn, commit and distinct-session
checks below are copied exactly; actual current55 catalog is additionally
checked before each owned session can read or append.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import account_history_partition as original
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as current55,
)
from app.domain import controlled_history_current55_schema_v2 as controlled

policy = original.policy
records = original.records
_utc = original._utc
_readback = original._readback
_READ_SQL = original._READ_SQL
digest = original.digest
read_account_history_partition_catalog = original.read_account_history_partition_catalog
verify_account_history_partition_catalog = (
    original.verify_account_history_partition_catalog
)
expected_account_history_partition_schema = (
    original.expected_account_history_partition_schema
)
HistoryPartitionReadbackV1 = original.HistoryPartitionReadbackV1


class AccountHistoryPartitionRepository:
    def __init__(self, session_factory, *, clock):
        if (
            type(session_factory) is not async_sessionmaker
            or session_factory.class_ is not AsyncSession
        ):
            policy.deny("account_history_partition_factory")
        self.session_factory = session_factory
        self.clock = clock
        self._poisoned = False

    async def _settings(self, session):
        if (
            type(session) is not AsyncSession
            or session.get_bind().dialect.name != "postgresql"
        ):
            policy.deny("account_history_partition_postgresql_required")
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        await session.execute(
            text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
        )
        await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        await session.execute(text("SET LOCAL lock_timeout='1000ms'"))
        await session.execute(text("SET LOCAL statement_timeout='10000ms'"))
        captured = await read_account_history_partition_catalog(session)
        verify_account_history_partition_catalog(captured)
        _verify_current55_runtime_role_v2(captured)
        await current55.verify_actual_control_catalog_v2(session)

    async def verify_deployment(self):
        async with self.session_factory() as session, session.begin():
            await self._settings(session)
        return expected_account_history_partition_schema()

    async def _read_in_session(self, session, scope):
        value = await session.scalar(
            text(_READ_SQL),
            {
                "environment": scope.environment,
                "account_id": scope.account_id,
                "currency": scope.settlement_currency,
            },
        )
        return _readback(value, scope, _utc(self.clock()))

    async def read(self, scope):
        records._scope(scope)
        async with self.session_factory() as session, session.begin():
            await self._settings(session)
            return await self._read_in_session(session, scope)

    def append_member_and_checkpoint(
        self,
        scope,
        *,
        b3_document_raw,
        native_linkage_raw,
        expected_b3_policy_sha256,
        expected_b3_event_sha256,
        expected_previous_checkpoint_sha256,
        expected_control_revision,
        expected_account_revision,
        expected_ledger_revision,
        expires_at,
        checkpoint_raw=None,
    ):
        """One attempt per repository, burned synchronously even if never awaited."""
        if self._poisoned:
            policy.deny("account_history_partition_attempt_burned")
        self._poisoned = True
        records._scope(scope)
        record = records.derive_native_member_registration_v1(
            scope=scope,
            b3_document_raw=b3_document_raw,
            native_linkage_raw=native_linkage_raw,
            expected_b3_policy_sha256=expected_b3_policy_sha256,
            expected_b3_event_sha256=expected_b3_event_sha256,
        )
        if expected_previous_checkpoint_sha256 is not None:
            records._hash(expected_previous_checkpoint_sha256)
        for value, zero in (
            (expected_control_revision, False),
            (expected_account_revision, True),
            (expected_ledger_revision, True),
        ):
            records._number(value, zero=zero)
        if _utc(expires_at) <= _utc(self.clock()):
            policy.deny("account_history_partition_expired")
        if checkpoint_raw is not None:
            records._json(checkpoint_raw)
        return self._append(
            scope,
            b3_document_raw,
            expected_b3_policy_sha256,
            expected_b3_event_sha256,
            expected_previous_checkpoint_sha256,
            expected_control_revision,
            expected_account_revision,
            expected_ledger_revision,
            expires_at,
            record.record_json,
            checkpoint_raw,
        )

    async def _append(
        self,
        scope,
        document,
        source_policy,
        head,
        previous,
        control_revision,
        account_revision,
        ledger_revision,
        expires,
        registration,
        checkpoint,
    ):
        if _utc(self.clock()) >= expires:
            policy.deny("account_history_partition_expired")
        try:
            async with self.session_factory() as first, first.begin():
                await self._settings(first)
                before = await self._read_in_session(first, scope)
                if (
                    before.head_sha256 != head
                    or before.b3_documents[-1] != document
                    or before.control_revision != control_revision
                    or before.account_revision != account_revision
                    or before.ledger_revision != ledger_revision
                    or (
                        before.checkpoints[-1].record_sha256
                        if before.checkpoints
                        else None
                    )
                    != previous
                ):
                    policy.deny("account_history_partition_fence_changed")
                if checkpoint is not None:
                    if before.head_sequence % 32:
                        policy.deny("account_history_partition_exact32")
                    known = {row.b3_sequence: row.record_json for row in before.members}
                    known[before.head_sequence] = registration
                    first_sequence = before.head_sequence - 31
                    expected = records.build_partition_checkpoint_v1(
                        scope=scope,
                        b3_documents=before.b3_documents[-32:],
                        native_registrations=tuple(
                            known.get(number)
                            for number in range(
                                first_sequence, before.head_sequence + 1
                            )
                        ),
                        previous_checkpoints=tuple(
                            row.record_json for row in before.checkpoints
                        ),
                        expected_b3_policy_sha256=source_policy,
                        expected_b3_head_sha256=head,
                    )
                    if expected.record_json != checkpoint:
                        policy.deny("account_history_partition_checkpoint_changed")
                acknowledged = await first.scalar(
                    text(controlled.APPEND_SQL),
                    {
                        "environment": scope.environment,
                        "account_id": scope.account_id,
                        "currency": scope.settlement_currency,
                        "head": head,
                        "previous": previous,
                        "registration": registration.decode("ascii"),
                        "checkpoint": None
                        if checkpoint is None
                        else checkpoint.decode("ascii"),
                        "control_revision": control_revision,
                        "account_revision": account_revision,
                        "ledger_revision": ledger_revision,
                        "expires": expires,
                    },
                )
                if acknowledged != digest(registration):
                    policy.deny("account_history_partition_acknowledgement_changed")
                if _utc(self.clock()) >= expires:
                    policy.deny("account_history_partition_expired_before_commit")
            # first commit AND its session close precede opening the second one.
            async with self.session_factory() as second, second.begin():
                if second is first:
                    policy.deny("account_history_partition_distinct_session_required")
                await self._settings(second)
                after = await self._read_in_session(second, scope)
                if (
                    after.transaction_id == before.transaction_id
                    or after.head_sha256 != head
                    or after.control_revision != control_revision
                    or after.account_revision != account_revision
                    or after.ledger_revision != ledger_revision
                    or _utc(self.clock()) >= expires
                ):
                    policy.deny("account_history_partition_postcommit_fence_changed")
                matched = [
                    row
                    for row in after.members
                    if row.b3_sequence == before.head_sequence
                ]
                if (
                    len(matched) != 1
                    or matched[0].record_json != registration
                    or (
                        checkpoint is not None
                        and (
                            not after.checkpoints
                            or after.checkpoints[-1].record_json != checkpoint
                        )
                    )
                ):
                    policy.deny("account_history_partition_committed_readback_changed")
                return after
        except (policy.AccountHistoryPartitionError, records.HistoryPartitionError):
            self._poisoned = True
            raise
        except Exception:  # noqa: BLE001 - Any uncertain commit/readback must burn the attempt and hide private adapter errors.
            self._poisoned = True
            policy.deny("account_history_partition_commit_or_readback_uncertain")
        except BaseException:
            # No rollback/delete compensation or retry is performed after the
            # main commit may have succeeded. The native owner must also burn.
            self._poisoned = True
            raise


def _verify_current55_runtime_role_v2(catalog):
    """Mandatory actual session gate; no public flag may bypass it."""
    try:
        r, s = catalog["role"], catalog["server"]
        if (
            s["current_user"] != s["session_user"]
            or r["name"] != s["session_user"]
            or s["role_setting"] != "none"
            or r["can_login"] is not True
            or any(
                r[key] is not False
                for key in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "other_membership",
                    "can_create",
                )
            )
        ):
            policy.deny("account_history_partition_role_denied")
        if (
            s["timezone"] != "UTC"
            or s["search_path"] != "pg_catalog, public, pg_temp"
            or s["isolation"] != "read committed"
            or s["replication_role"] != "origin"
            or s["in_recovery"] is not False
            or any(
                s[key] != "on"
                for key in ("synchronous_commit", "fsync", "full_page_writes")
            )
        ):
            policy.deny("account_history_partition_role_denied")
        if any(
            row["owner_oid"] == r["oid"] or row["table_access"] is not False
            for row in catalog["tables"]
        ):
            policy.deny("account_history_partition_role_denied")
        for row in catalog["functions"]:
            if row["name"] == "account_history_partition_v1_append":
                # The genuine old privilege remains observed, not projected.
                # Only the separate strict55 catalog admits the NEW append.
                if type(row["can_execute"]) is not bool or row["owner_oid"] == r["oid"]:
                    policy.deny("account_history_partition_role_denied")
                continue
            expected = row["name"] == "account_history_partition_v1_read"
            if row["can_execute"] is not expected or row["owner_oid"] == r["oid"]:
                policy.deny("account_history_partition_role_denied")
    except policy.AccountHistoryPartitionError:
        raise
    except (KeyError, TypeError, AttributeError):
        policy.deny("account_history_partition_role_denied")
