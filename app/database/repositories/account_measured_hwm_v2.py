"""Persist the B5 sampled-HWM continuation as DENY-only DB evidence.

Every append starts from DB0021's exact accepted sequence and replays original
DB0020 B1 captures for the new, genesis and selected-peak measurements. This
repository never returns a current account owner, account revision or risk
snapshot. A committed row survives a later readback failure.
"""

import json
from dataclasses import dataclass
from fractions import Fraction

from sqlalchemy import select

from app.database.models.account_measured_hwm_v2 import (
    DemoAccountMeasuredHWMV2 as HWMRow,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationBatch as Batch,
)
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_measured_hwm_continuation as hwm
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap


class AccountMeasuredHWMError(ValueError):
    """Private-source-safe static failure codes only."""


MAX_CATCH_UP_ROWS = 4


def _deny(code: str) -> None:
    raise AccountMeasuredHWMError(code)


def _keys(scope: LedgerScope) -> dict[str, str]:
    checked_bootstrap(scope, LedgerScope)
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }


def _sequence(value: int) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        _deny("account_measured_hwm_sequence_invalid")
    return value


def _page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_CATCH_UP_ROWS:
        _deny("account_measured_hwm_catch_up_limit_invalid")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class MeasuredHWMReadback:
    projection_json: bytes
    projection_sha256: str
    sequence: int
    head_sha256: str

    @property
    def durable_membership_verified(self) -> bool:
        return False

    @property
    def native_clock_verified(self) -> bool:
        return False

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


@dataclass(frozen=True, slots=True, repr=False)
class MeasuredHWMHistoryReadback:
    """A checked B3 sample prefix, never a complete account/HWM claim."""

    projection_json: bytes
    projection_sha256: str
    indexed_through_sequence: int
    indexed_head_sha256: str
    observation_head_sequence: int
    observation_head_sha256: str
    verified_rows: int

    @property
    def remaining_observation_rows(self) -> int:
        return self.observation_head_sequence - self.indexed_through_sequence

    @property
    def durable_membership_verified(self) -> bool:
        return False

    @property
    def native_clock_verified(self) -> bool:
        return False

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


class AccountMeasuredHWMRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)
        self.observations = AccountObservationIndexRepository(
            session_factory, clock=clock
        )

    async def _original(self, session, scope, batch):
        data = self.observations._checked_document(batch, scope)
        # Older B3 policy epochs cannot be reinterpreted under the current
        # replay policy. Their immutable rows remain evidence but do not enter
        # this V2 continuation without a separately versioned migration.
        if data["policy_sha256"] != observed.POLICY_SHA256:
            _deny("account_measured_hwm_observation_policy_epoch_unknown")
        reference = observed.CaptureReference(**data["reference"])
        chain = await self.journal._read_chain_locked(
            session, scope, reference.capture_id
        )
        if (
            batch.capture_id != reference.capture_id
            # The B3 batch event is the observation-document digest; the B1
            # reference head is the capture-journal terminal-event digest.
            # _checked_document verifies the former, and source_reference
            # verifies the latter against the original chain below.
            or len(chain) != batch.source_sequence
            or observed.source_reference(chain) != reference
            or capture._utc(batch.db_recorded_at)
            < capture._utc(chain[-1].db_recorded_at)
        ):
            _deny("account_measured_hwm_original_source_mismatch")
        return reference, components.observe_balance(
            chain, reference=reference, scope=scope
        )

    async def _saved_witness(self, session, scope, sequence):
        row = await session.scalar(
            select(HWMRow).filter_by(**_keys(scope), sequence=sequence)
        )
        batch = await session.scalar(
            select(Batch).filter_by(**_keys(scope), sequence=sequence)
        )
        if row is None or batch is None or row.event_sha256 != batch.event_sha256:
            _deny("account_measured_hwm_original_witness_missing")
        reference, measurement = await self._original(session, scope, batch)
        if (
            row.capture_id != reference.capture_id
            or row.source_sequence != batch.source_sequence
            or row.measurement_sha256 != journal.digest(measurement.source_json)
            or row.measurement_json.encode() != measurement.source_json
            or row.projection_sha256 != journal.digest(row.projection_json.encode())
            or row.account_complete
            or row.execution_authority
            or row.admission != "DENY"
        ):
            _deny("account_measured_hwm_original_witness_mismatch")
        return row, measurement

    async def _compute(self, session, scope, batch, predecessor):
        reference, measurement = await self._original(session, scope, batch)
        if predecessor is None:
            if batch.sequence != 1 or batch.previous_sha256 is not None:
                _deny("account_measured_hwm_genesis_missing")
            genesis = peak = None
        else:
            if (
                predecessor.sequence != batch.sequence - 1
                or predecessor.event_sha256 != batch.previous_sha256
                or predecessor.projection_sha256
                != journal.digest(predecessor.projection_json.encode())
                or predecessor.genesis_sequence != 1
                or not 1 <= predecessor.peak_sequence <= predecessor.sequence
            ):
                _deny("account_measured_hwm_predecessor_mismatch")
            _, genesis = await self._saved_witness(session, scope, 1)
            _, peak = await self._saved_witness(
                session, scope, predecessor.peak_sequence
            )
        projected = hwm.advance_measured_hwm(
            scope,
            sequence=batch.sequence,
            previous_event_sha256=batch.previous_sha256,
            event_sha256=batch.event_sha256,
            measurement=measurement,
            expected_reference=reference,
            predecessor=None
            if predecessor is None
            else hwm.HWMContinuation(predecessor.projection_json.encode()),
            expected_predecessor_sha256=None
            if predecessor is None
            else predecessor.projection_sha256,
            genesis_witness=genesis,
            selected_peak_witness=peak,
        )
        document = json.loads(projected.receipt_json)
        selected_sequence = batch.sequence
        if predecessor is not None:
            current = json.loads(measurement.source_json)["equity"]
            old_peak = json.loads(predecessor.projection_json)["peak_equity"]
            if Fraction(
                int(current["numerator"]), int(current["denominator"])
            ) <= Fraction(int(old_peak["numerator"]), int(old_peak["denominator"])):
                selected_sequence = predecessor.peak_sequence
        if document["peak_measurement_sha256"] != (
            journal.digest(measurement.source_json)
            if selected_sequence == batch.sequence
            else journal.digest(peak.source_json)
        ):
            _deny("account_measured_hwm_selected_peak_mismatch")
        return projected, measurement, selected_sequence

    @staticmethod
    def _new_row(scope, batch, predecessor, projected, measurement, peak_sequence):
        return HWMRow(
            **_keys(scope),
            sequence=batch.sequence,
            capture_id=batch.capture_id,
            source_sequence=batch.source_sequence,
            event_sha256=batch.event_sha256,
            previous_event_sha256=batch.previous_sha256,
            measurement_sha256=journal.digest(measurement.source_json),
            measurement_json=measurement.source_json.decode(),
            genesis_sequence=1,
            peak_sequence=peak_sequence,
            previous_projection_sha256=None
            if predecessor is None
            else predecessor.projection_sha256,
            projection_sha256=projected.receipt_sha256,
            projection_json=projected.receipt_json.decode(),
            account_complete=False,
            execution_authority=False,
            admission="DENY",
        )

    async def _verified_row_locked(self, session, scope, batch, row):
        """Replay one immutable B5 row against its original B3/B1 sources."""
        if row is None or batch is None or row.sequence != batch.sequence:
            _deny("account_measured_hwm_history_row_missing")
        predecessor = None
        if row.sequence > 1:
            predecessor = await session.scalar(
                select(HWMRow).filter_by(**_keys(scope), sequence=row.sequence - 1)
            )
            if predecessor is None:
                _deny("account_measured_hwm_history_predecessor_missing")
        projected, measurement, selected = await self._compute(
            session, scope, batch, predecessor
        )
        if (
            row.capture_id != batch.capture_id
            or row.source_sequence != batch.source_sequence
            or row.event_sha256 != batch.event_sha256
            or row.previous_event_sha256 != batch.previous_sha256
            or row.measurement_sha256 != journal.digest(measurement.source_json)
            or row.measurement_json.encode() != measurement.source_json
            or row.genesis_sequence != 1
            or row.peak_sequence != selected
            or row.previous_projection_sha256
            != (None if predecessor is None else predecessor.projection_sha256)
            or row.projection_sha256 != projected.receipt_sha256
            or row.projection_json.encode() != projected.receipt_json
            or capture._utc(row.db_recorded_at) < capture._utc(batch.db_recorded_at)
            or row.account_complete
            or row.execution_authority
            or row.admission != "DENY"
        ):
            _deny("account_measured_hwm_history_row_mismatch")
        return row

    async def append_next(
        self, scope: LedgerScope, *, expected_sequence: int, expected_head_sha256: str
    ) -> MeasuredHWMReadback:
        """Append one tail only; historical backlog is never replayed here."""
        try:
            expected_sequence = _sequence(expected_sequence)
            journal._sha(expected_head_sha256)
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                latest = await session.scalar(
                    select(Batch)
                    .filter_by(**_keys(scope))
                    .order_by(Batch.sequence.desc())
                    .limit(1)
                )
                if (
                    latest is None
                    or latest.sequence != expected_sequence
                    or latest.event_sha256 != expected_head_sha256
                ):
                    _deny("account_measured_hwm_observation_head_changed")
                existing = await session.scalar(
                    select(HWMRow).filter_by(**_keys(scope), sequence=expected_sequence)
                )
                if existing is None:
                    predecessor = None
                    if expected_sequence > 1:
                        predecessor = await session.scalar(
                            select(HWMRow).filter_by(
                                **_keys(scope), sequence=expected_sequence - 1
                            )
                        )
                        if predecessor is None:
                            _deny("account_measured_hwm_predecessor_missing")
                    projected, measurement, peak_sequence = await self._compute(
                        session, scope, latest, predecessor
                    )
                    session.add(
                        self._new_row(
                            scope,
                            latest,
                            predecessor,
                            projected,
                            measurement,
                            peak_sequence,
                        )
                    )
                    await session.flush()
            return await self.readback(
                scope,
                expected_sequence=expected_sequence,
                expected_head_sha256=expected_head_sha256,
            )
        except Exception:  # noqa: BLE001 -- no original account/SQL bytes in errors
            raise AccountMeasuredHWMError(
                "account_measured_hwm_append_unknown"
            ) from None

    async def catch_up_history(
        self,
        scope: LedgerScope,
        *,
        expected_observation_sequence: int,
        expected_observation_head_sha256: str,
        max_rows: int = MAX_CATCH_UP_ROWS,
    ) -> MeasuredHWMHistoryReadback:
        """Append at most four missing B5 rows from B3 sequence one onward.

        The pinned B3 head must be the current database head. A historical
        suffix, later genesis, caller balance or caller-selected predecessor is
        never accepted. This method has no B5 runtime or trading authority.
        """
        try:
            expected_observation_sequence = _sequence(expected_observation_sequence)
            journal._sha(expected_observation_head_sha256)
            max_rows = _page_limit(max_rows)
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                head = await session.scalar(
                    select(Batch)
                    .filter_by(**_keys(scope))
                    .order_by(Batch.sequence.desc())
                    .limit(1)
                )
                if (
                    head is None
                    or head.sequence != expected_observation_sequence
                    or head.event_sha256 != expected_observation_head_sha256
                ):
                    _deny("account_measured_hwm_catch_up_observation_head_changed")
                tip = await session.scalar(
                    select(HWMRow)
                    .filter_by(**_keys(scope))
                    .order_by(HWMRow.sequence.desc())
                    .limit(1)
                )
                before = 0 if tip is None else tip.sequence
                if before > head.sequence:
                    _deny("account_measured_hwm_catch_up_prefix_ahead")
                if tip is not None:
                    tip_batch = await session.scalar(
                        select(Batch).filter_by(**_keys(scope), sequence=before)
                    )
                    await self._verified_row_locked(session, scope, tip_batch, tip)
                count = min(max_rows, head.sequence - before)
                batches = (
                    await session.scalars(
                        select(Batch)
                        .filter_by(**_keys(scope))
                        .where(
                            Batch.sequence > before,
                            Batch.sequence <= head.sequence,
                        )
                        .order_by(Batch.sequence)
                        .limit(count)
                    )
                ).all()
                if len(batches) != count:
                    _deny("account_measured_hwm_catch_up_observation_gap")
                previous_head = None if tip is None else tip.event_sha256
                for sequence, batch in enumerate(batches, before + 1):
                    if (
                        batch.sequence != sequence
                        or batch.previous_sha256 != previous_head
                    ):
                        _deny("account_measured_hwm_catch_up_observation_gap")
                    projected, measurement, peak_sequence = await self._compute(
                        session, scope, batch, tip
                    )
                    row = self._new_row(
                        scope, batch, tip, projected, measurement, peak_sequence
                    )
                    session.add(row)
                    await session.flush()
                    tip = row
                    previous_head = batch.event_sha256
                if tip is None:
                    _deny("account_measured_hwm_catch_up_empty")
                through = tip.sequence
                projection_sha256 = tip.projection_sha256
            return await self.readback_history(
                scope,
                expected_observation_sequence=expected_observation_sequence,
                expected_observation_head_sha256=expected_observation_head_sha256,
                first_sequence=before + 1 if count else through,
                through_sequence=through,
                expected_projection_sha256=projection_sha256,
            )
        except Exception:  # noqa: BLE001 -- durable append survives late readback failure
            raise AccountMeasuredHWMError(
                "account_measured_hwm_catch_up_unknown"
            ) from None

    async def readback_history(
        self,
        scope: LedgerScope,
        *,
        expected_observation_sequence: int,
        expected_observation_head_sha256: str,
        first_sequence: int,
        through_sequence: int,
        expected_projection_sha256: str,
    ) -> MeasuredHWMHistoryReadback:
        """Independent-session replay of one bounded, possibly nonlatest page."""
        try:
            expected_observation_sequence = _sequence(expected_observation_sequence)
            first_sequence = _sequence(first_sequence)
            through_sequence = _sequence(through_sequence)
            journal._sha(expected_observation_head_sha256)
            journal._sha(expected_projection_sha256)
            if (
                first_sequence > through_sequence
                or through_sequence > expected_observation_sequence
                or through_sequence - first_sequence + 1 > MAX_CATCH_UP_ROWS
            ):
                _deny("account_measured_hwm_history_range_invalid")
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                head = await session.scalar(
                    select(Batch)
                    .filter_by(**_keys(scope))
                    .order_by(Batch.sequence.desc())
                    .limit(1)
                )
                if (
                    head is None
                    or head.sequence != expected_observation_sequence
                    or head.event_sha256 != expected_observation_head_sha256
                ):
                    _deny("account_measured_hwm_history_observation_head_changed")
                tip = await session.scalar(
                    select(HWMRow)
                    .filter_by(**_keys(scope))
                    .order_by(HWMRow.sequence.desc())
                    .limit(1)
                )
                if (
                    tip is None
                    or tip.sequence != through_sequence
                    or tip.projection_sha256 != expected_projection_sha256
                ):
                    _deny("account_measured_hwm_history_tip_changed")
                rows = (
                    await session.scalars(
                        select(HWMRow)
                        .filter_by(**_keys(scope))
                        .where(
                            HWMRow.sequence >= first_sequence,
                            HWMRow.sequence <= through_sequence,
                        )
                        .order_by(HWMRow.sequence)
                        .limit(MAX_CATCH_UP_ROWS + 1)
                    )
                ).all()
                if len(rows) != through_sequence - first_sequence + 1:
                    _deny("account_measured_hwm_history_page_missing")
                previous = None
                if first_sequence > 1:
                    previous = await session.scalar(
                        select(HWMRow).filter_by(
                            **_keys(scope), sequence=first_sequence - 1
                        )
                    )
                    previous_batch = await session.scalar(
                        select(Batch).filter_by(
                            **_keys(scope), sequence=first_sequence - 1
                        )
                    )
                    await self._verified_row_locked(
                        session, scope, previous_batch, previous
                    )
                for sequence, row in enumerate(rows, first_sequence):
                    batch = await session.scalar(
                        select(Batch).filter_by(**_keys(scope), sequence=sequence)
                    )
                    if (
                        row.sequence != sequence
                        or batch is None
                        or batch.previous_sha256
                        != (None if previous is None else previous.event_sha256)
                    ):
                        _deny("account_measured_hwm_history_page_gap")
                    await self._verified_row_locked(session, scope, batch, row)
                    previous = row
                return MeasuredHWMHistoryReadback(
                    tip.projection_json.encode(),
                    tip.projection_sha256,
                    tip.sequence,
                    tip.event_sha256,
                    head.sequence,
                    head.event_sha256,
                    len(rows),
                )
        except Exception:  # noqa: BLE001 -- no private source/SQL bytes in errors
            raise AccountMeasuredHWMError(
                "account_measured_hwm_history_readback_unknown"
            ) from None

    async def readback(
        self, scope: LedgerScope, *, expected_sequence: int, expected_head_sha256: str
    ) -> MeasuredHWMReadback:
        """Independent-session source replay; no authority even after success."""
        try:
            expected_sequence = _sequence(expected_sequence)
            journal._sha(expected_head_sha256)
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                latest = await session.scalar(
                    select(Batch)
                    .filter_by(**_keys(scope))
                    .order_by(Batch.sequence.desc())
                    .limit(1)
                )
                if (
                    latest is None
                    or latest.sequence != expected_sequence
                    or latest.event_sha256 != expected_head_sha256
                ):
                    _deny("account_measured_hwm_readback_head_changed")
                row = await session.scalar(
                    select(HWMRow).filter_by(**_keys(scope), sequence=expected_sequence)
                )
                predecessor = None
                if expected_sequence > 1:
                    predecessor = await session.scalar(
                        select(HWMRow).filter_by(
                            **_keys(scope), sequence=expected_sequence - 1
                        )
                    )
                if row is None or (expected_sequence > 1 and predecessor is None):
                    _deny("account_measured_hwm_readback_missing")
                projected, measurement, selected = await self._compute(
                    session, scope, latest, predecessor
                )
                if (
                    row.capture_id != latest.capture_id
                    or row.source_sequence != latest.source_sequence
                    or row.event_sha256 != latest.event_sha256
                    or row.previous_event_sha256 != latest.previous_sha256
                    or row.measurement_sha256 != journal.digest(measurement.source_json)
                    or row.measurement_json.encode() != measurement.source_json
                    or row.genesis_sequence != 1
                    or row.peak_sequence != selected
                    or row.previous_projection_sha256
                    != (None if predecessor is None else predecessor.projection_sha256)
                    or row.projection_sha256 != projected.receipt_sha256
                    or row.projection_json.encode() != projected.receipt_json
                    or row.account_complete
                    or row.execution_authority
                    or row.admission != "DENY"
                ):
                    _deny("account_measured_hwm_readback_mismatch")
                return MeasuredHWMReadback(
                    row.projection_json.encode(),
                    row.projection_sha256,
                    row.sequence,
                    row.event_sha256,
                )
        except Exception:  # noqa: BLE001 -- static denial omits private source
            raise AccountMeasuredHWMError(
                "account_measured_hwm_readback_unknown"
            ) from None
