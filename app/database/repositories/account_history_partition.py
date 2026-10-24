"""PostgreSQL immutable Slice A storage. Portable records never attest native IO.

Actual original-custody factory revalidation belongs to the held native producer
before this write and after its distinct-session readback. There is no callback,
distributed transaction, retry, replacement owner, or account revision mutation.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.domain import account_history_partition_catalog_v1 as policy
from app.domain import account_history_partition_v1 as records

AccountHistoryPartitionError = policy.AccountHistoryPartitionError
expected_account_history_partition_schema = (
    policy.expected_account_history_partition_schema
)
verify_account_history_partition_catalog = (
    policy.verify_account_history_partition_catalog
)
canonical = policy.canonical
digest = policy.digest

_APPEND_SQL = "SELECT public.account_history_partition_v1_append(:environment,:account_id,:currency,:head,:previous,:registration,:checkpoint,:control_revision,:account_revision,:ledger_revision,:expires)"
_READ_SQL = "SELECT public.account_history_partition_v1_read(:environment,:account_id,:currency)"


async def read_account_history_partition_catalog(session):
    """Capture real JSON before strict admission; do not coerce OIDs/ACLs."""
    if isinstance(session, AsyncConnection):
        dialect = session.dialect
    elif isinstance(session, AsyncSession):
        dialect = session.get_bind().dialect
    else:
        policy.deny("account_history_partition_postgresql_required")
    if dialect.name != "postgresql":
        policy.deny("account_history_partition_postgresql_required")
    value = await session.scalar(text(policy.CATALOG_SQL))
    if type(value) is not dict or len(canonical(value)) > records.MAX_RECORD_BYTES:
        policy.deny("account_history_partition_catalog_shape")
    return value


async def read_account_history_partition_retention(session):
    """Full private rows, never a truncated summary presented as completeness."""
    rows, counts = {}, {}
    for table in policy.TABLES:
        key = "b3_sequence" if table == policy.TABLES[0] else "partition_number"
        result = await session.execute(
            text(
                f"SELECT row_to_json(t) FROM public.{table} t ORDER BY environment,account_id,settlement_currency,{key} LIMIT 8193"
            )
        )
        actual = list(result.scalars())
        if len(actual) > 8192 or any(type(row) is not dict for row in actual):
            policy.deny("account_history_partition_retention_bound")
        rows[table] = actual
        counts[table] = len(actual)
    raw = canonical(rows)
    if len(raw) > records.MAX_CHAIN_BYTES:
        policy.deny("account_history_partition_retention_bound")
    return {"rows": rows, "counts": counts, "rows_sha256": digest(raw)}


@dataclass(frozen=True, slots=True, repr=False)
class HistoryNativeMemberReadbackV1:
    b3_sequence: int
    record_json: bytes
    record_sha256: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class HistoryCheckpointReadbackV1:
    partition_number: int
    record_json: bytes
    record_sha256: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class HistoryPartitionReadbackV1:
    scope: records.HistoryPartitionScopeV1
    members: tuple
    checkpoints: tuple
    b3_documents: tuple
    head_sequence: int
    head_sha256: str
    control_revision: int
    account_revision: int
    ledger_revision: int
    observed_at: datetime
    backend_pid: int
    transaction_id: str

    @property
    def execution_authority(self):
        return False

    @property
    def account_complete(self):
        return False


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        policy.deny("account_history_partition_clock")
    return value


def _readback(raw, scope, checked_at):
    if (
        type(raw) is not dict
        or set(raw)
        != {
            "scope",
            "head_sequence",
            "head_sha256",
            "control_revision",
            "account_revision",
            "ledger_revision",
            "backend_pid",
            "transaction_id",
            "members",
            "checkpoints",
            "b3_documents",
        }
        or raw["scope"] != records._scope(scope)
        or len(canonical(raw)) > records.MAX_CHAIN_BYTES
    ):
        policy.deny("account_history_partition_readback")
    for key in (
        "head_sequence",
        "control_revision",
        "account_revision",
        "ledger_revision",
        "backend_pid",
    ):
        records._number(raw[key], zero=key in {"account_revision", "ledger_revision"})
    if type(raw["transaction_id"]) is not str or not raw["transaction_id"].isdigit():
        policy.deny("account_history_partition_transaction")
    records._hash(raw["head_sha256"])
    if any(
        type(raw[key]) is not list for key in ("members", "checkpoints", "b3_documents")
    ):
        policy.deny("account_history_partition_readback")
    documents = tuple(records._ascii(value) for value in raw["b3_documents"])
    if len(documents) != raw["head_sequence"]:
        policy.deny("account_history_partition_source_gap")
    previous = anchor = None
    source_policy = None
    captures = set()
    prior_counts = (0, 0, 0)
    for number, original in enumerate(documents, 1):
        value = records._json(original)
        selected_policy = records._hash(value["policy_sha256"])
        source_policy = selected_policy if source_policy is None else source_policy
        value, continuation = records._b3(
            original, scope=records._scope(scope), source_policy=source_policy
        )
        if (
            value["sequence"] != number
            or value["previous_sha256"] != previous
            or continuation["previous_anchor_sha256"] != anchor
            or value["reference"]["capture_id"] in captures
        ):
            policy.deny("account_history_partition_source_gap")
        counts = tuple(continuation[key] for key in records._COUNTS)
        increments = tuple(
            value[key + "_membership_count"]
            for key in ("source", "coverage", "finding")
        )
        if counts != tuple(
            a + b for a, b in zip(prior_counts, increments, strict=True)
        ):
            policy.deny("account_history_partition_source_counts")
        prior_counts = counts
        captures.add(value["reference"]["capture_id"])
        previous, anchor = digest(original), value["anchor_sha256"]
    if previous != raw["head_sha256"]:
        policy.deny("account_history_partition_head_changed")
    members, checkpoints = [], []
    for category, target, kind, sequence_key in (
        ("members", members, HistoryNativeMemberReadbackV1, "b3_sequence"),
        ("checkpoints", checkpoints, HistoryCheckpointReadbackV1, "partition_number"),
    ):
        previous_sequence = 0
        for row in raw[category]:
            if type(row) is not dict or set(row) != {
                "sequence",
                "record_json",
                "record_sha256",
                "recorded_at",
            }:
                policy.deny("account_history_partition_readback")
            sequence = records._number(row["sequence"])
            if sequence <= previous_sequence:
                policy.deny("account_history_partition_readback_order")
            previous_sequence = sequence
            original = records._ascii(row["record_json"])
            if digest(original) != records._hash(row["record_sha256"]):
                policy.deny("account_history_partition_readback_hash")
            document = records._json(original)
            if document[sequence_key] != sequence or document[
                "scope"
            ] != records._scope(scope):
                policy.deny("account_history_partition_readback_scope")
            try:
                recorded = datetime.fromisoformat(row["recorded_at"])
            except (ValueError, TypeError):
                policy.deny("account_history_partition_readback_time")
            if (
                recorded.utcoffset() is None
                or recorded.utcoffset().total_seconds() != 0
                or recorded > checked_at
            ):
                policy.deny("account_history_partition_readback_time")
            if category == "members":
                if sequence > len(documents):
                    policy.deny("account_history_partition_readback_scope")
                derived = records.derive_native_member_registration_v1(
                    scope=scope,
                    b3_document_raw=documents[sequence - 1],
                    native_linkage_raw=records._ascii(document["native_linkage_json"]),
                    expected_b3_policy_sha256=source_policy,
                    expected_b3_event_sha256=digest(documents[sequence - 1]),
                )
                if derived.record_json != original:
                    policy.deny("account_history_partition_readback_changed")
            target.append(kind(sequence, original, row["record_sha256"], recorded))
    if checkpoints:
        records.replay_partition_checkpoints_v1(
            scope=scope,
            checkpoints=tuple(row.record_json for row in checkpoints),
            expected_checkpoint_sha256=checkpoints[-1].record_sha256,
            expected_b3_policy_sha256=source_policy,
            expected_b3_head_sha256=digest(documents[32 * len(checkpoints) - 1]),
        )
        known = {row.b3_sequence: row.record_json for row in members}
        previous_checkpoints = []
        for row in checkpoints:
            first = (row.partition_number - 1) * records.PARTITION_SPAN
            expected = records.build_partition_checkpoint_v1(
                scope=scope,
                b3_documents=documents[first : first + records.PARTITION_SPAN],
                native_registrations=tuple(
                    known.get(number)
                    for number in range(first + 1, first + records.PARTITION_SPAN + 1)
                ),
                previous_checkpoints=tuple(previous_checkpoints),
                expected_b3_policy_sha256=source_policy,
                expected_b3_head_sha256=digest(
                    documents[first + records.PARTITION_SPAN - 1]
                ),
            )
            if expected.record_json != row.record_json:
                policy.deny("account_history_partition_checkpoint_source_changed")
            previous_checkpoints.append(row.record_json)
    return HistoryPartitionReadbackV1(
        scope,
        tuple(members),
        tuple(checkpoints),
        documents,
        raw["head_sequence"],
        raw["head_sha256"],
        raw["control_revision"],
        raw["account_revision"],
        raw["ledger_revision"],
        checked_at,
        raw["backend_pid"],
        raw["transaction_id"],
    )


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
        policy.verify_runtime_role(captured)

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
                    text(_APPEND_SQL),
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
