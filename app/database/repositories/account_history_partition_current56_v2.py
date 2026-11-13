"""Original restricted DB0056 writer for the held controlled partition append."""

from sqlalchemy import text

from app.database.repositories import account_history_partition_current55_v2 as previous
from app.database.repositories import (
    account_history_partition_current56_source_v1 as source,
)
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint

_DURABLE = text("SET LOCAL synchronous_commit='on'")
_CLOCK = checkpoint.locked._CLOCK


class Db0056ControlledPartitionRepositoryV2(previous.AccountHistoryPartitionRepository):
    def __init__(self, parent):
        from app.trade_qualification import (
            native_controlled_account_source_v6 as native,
        )

        self._parent = parent
        self._replay = native._replay_owned_partition_repository_v6
        factories, _, _, _, _, _ = self._replay(parent, self, opening=True)
        super().__init__(factories.writer_factory, clock=self._clock)

    def _clock(self):
        return self._replay(self._parent, self)[4]

    async def _transaction(self, *, append=False):
        selected, roles, _, expected, _, expiry = self._replay(
            self._parent, self, append=append
        )
        factory = selected.writer_factory
        if self.session_factory is not factory:
            previous.policy.deny("account_history_partition_original_writer_required")
        async with factory() as session, session.begin():
            engine = factory.kw["bind"]
            transaction = session.sync_session.get_transaction()
            source._require_session(session, engine, transaction)
            if any(
                session.get_bind(clause=statement) is not engine.sync_engine
                for statement in (previous._CONTROLLED_APPEND, _DURABLE, _CLOCK)
            ):
                previous.policy.deny(
                    "account_history_partition_original_writer_required"
                )
            await session.execute(_DURABLE)
            self._replay(self._parent, self, append=append)
            async with self._held_checkpoint(
                selected, session, expected.state.scope, roles=roles
            ) as held:
                actual = held.control.control
                if (
                    actual.state != expected.state
                    or actual.event_sha256 != expected.event_sha256
                    or not expected.observed_at <= actual.observed_at < expiry
                    or not expected.state.arm_intent_current(actual.observed_at)
                ):
                    previous.policy.deny(
                        "account_history_partition_original_arm_changed"
                    )
                self._replay(self._parent, self, append=append)
                yield session
                if await held.recheck_control() != (
                    expected.state,
                    expected.event_sha256,
                ):
                    previous.policy.deny(
                        "account_history_partition_original_arm_changed"
                    )
                finished = await session.scalar(_CLOCK)
                held.check()
                if (
                    not actual.observed_at <= finished < expiry
                    or not expected.state.arm_intent_current(finished)
                ):
                    previous.policy.deny(
                        "account_history_partition_expired_before_commit"
                    )
            # Source checks follow the last catalog/Control await. PostgreSQL
            # still owns the same XID and all locks until the outer context commits.
            source._require_session(session, engine, transaction)
            self._replay(self._parent, self, append=append)

    @staticmethod
    def _held_checkpoint(
        selected,
        session,
        scope,
        *,
        roles,
        original=checkpoint.held_qualification_checkpoint_0056_v1,
    ):
        if checkpoint.held_qualification_checkpoint_0056_v1 is not original:
            previous.policy.deny("account_history_partition_original_writer_required")
        return original(selected, session, scope, roles=roles)

    async def _read_in_session(self, session, scope):
        selected, _, original_scope, _, _, _ = self._replay(self._parent, self)
        if scope != original_scope:
            previous.policy.deny("account_history_partition_original_scope_required")
        engine = selected.writer_factory.kw["bind"]
        transaction = session.sync_session.get_transaction()
        source._require_session(session, engine, transaction)
        actual = await super()._read_in_session(session, scope)
        db_tx = tuple((await session.execute(source._DB_TX)).one())
        source._require_session(session, engine, transaction)
        if db_tx != (actual.backend_pid, actual.transaction_id):
            previous.policy.deny("account_history_partition_transaction")
        self._replay(self._parent, self)
        return actual
