"""Durable source facts with bounded, original-source-verified continuation.

Thirty-two is a proof budget/page size, never a lifetime sequence limit. SQL
selects relevant witnesses; only original B1/B2 replay establishes membership.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app.database.models.account_observation_index import (
    DemoAccountObservationBatch as Batch,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationCoverage as Coverage,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationFact as Fact,
)
from app.database.models.account_observation_index import (
    DemoAccountObservationFinding as Finding,
)
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed

PAGE_SIZE = 32
MAX_REVISION = 9223372036854775807


@dataclass(frozen=True, slots=True, repr=False)
class ObservationBatchReadback:
    sequence: int
    event_sha256: str
    document_json: bytes
    replay: observed.ObservationReplay

    @property
    def execution_authority(self):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class BalanceSourcePage:
    through_sequence: int
    head_sha256: str
    after: int
    # sequence, previous accepted batch hash, batch hash, original measurement
    items: tuple


def _keys(scope):
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "settlement_currency": scope.settlement_currency,
    }


def _scope_hash(scope):
    return journal.digest(journal.canonical(list(_keys(scope).values())))


def _window(value):
    return observed.ExecutionWindow(
        **{k: datetime.fromisoformat(v) for k, v in value.items()}
    )


def _membership(replay):
    value = json.loads(replay.receipt_json)
    coverage = [
        {
            "family": item["family"],
            "product": item["product"],
            "started_at": start,
            "ended_at": end,
        }
        for item in value["coverage"]
        for start, end in item["intervals"]
    ]
    return value["source_index"], coverage


def _bytes(chain):
    return sum(
        len(item.event.event_json)
        + len(item.event.raw_body or b"")
        + len(item.event.packet_payload or b"")
        for item in chain
    )


class AccountObservationIndexRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory, self.clock = session_factory, clock
        self.journal = AccountCaptureJournalRepository(session_factory, clock=clock)

    async def _batch(self, scope, sequence=None):
        async with self.session_factory() as session, session.begin():
            await self.journal._lock(session, scope)
            statement = select(Batch).filter_by(**_keys(scope))
            if sequence is not None:
                statement = statement.where(Batch.sequence == sequence)
            return await session.scalar(
                statement.order_by(Batch.sequence.desc()).limit(1)
            )

    async def _rows(self, scope, *, after=0, limit=PAGE_SIZE):
        if (
            type(after) is not int
            or not 0 <= after <= MAX_REVISION
            or type(limit) is not int
            or not 1 <= limit <= PAGE_SIZE
        ):
            observed.deny("observation_read_page_invalid")
        async with self.session_factory() as session, session.begin():
            await self.journal._lock(session, scope)
            return (
                await session.scalars(
                    select(Batch)
                    .filter_by(**_keys(scope))
                    .where(Batch.sequence > after)
                    .order_by(Batch.sequence)
                    .limit(limit)
                )
            ).all()

    async def _source(self, scope, reference, window):
        chain = await self.journal.read_chain(scope, reference.capture_id)
        replay = observed.replay_observation_index(
            ((chain, reference),), scope=scope, window=window
        )
        return chain, replay

    def _checked_document(self, row, scope):
        document = row.document_json.encode("utf-8")
        data, receipt = json.loads(document), json.loads(row.receipt_json)
        anchor = receipt["continuation_anchor"]
        if (
            type(data.get("sequence")) is not int
            or data["sequence"] != row.sequence
            or data["scope_sha256"] != _scope_hash(scope)
            or data["reference"]["capture_id"] != row.capture_id
            or data["previous_sha256"] != row.previous_sha256
            or journal.digest(document) != row.event_sha256
            or journal.digest(row.receipt_json.encode()) != data["receipt_sha256"]
            or journal.canonical(receipt["continuation_anchor"]).decode()
            != data["anchor_json"]
            or journal.digest(data["anchor_json"].encode()) != data["anchor_sha256"]
            or any(getattr(row, key) != value for key, value in _keys(scope).items())
            or type(anchor["through_sequence"]) is not int
            or anchor["through_sequence"] != row.sequence
            or type(anchor["previous_sequence"]) is not int
            or anchor["previous_sequence"] != row.sequence - 1
            or anchor["scope_sha256"] != data["scope_sha256"]
            or anchor["policy_sha256"] != data["policy_sha256"]
            or anchor["previous_event_sha256"] != row.previous_sha256
            or anchor["current_source_membership_sha256"]
            != data["source_membership_sha256"]
            or anchor["current_coverage_membership_sha256"]
            != data["coverage_membership_sha256"]
        ):
            observed.deny("observation_persisted_binding_mismatch")
        return data

    async def _stored_membership(self, scope, row, single):
        facts, coverage = _membership(single)
        async with self.session_factory() as session, session.begin():
            await self.journal._lock(session, scope)
            for model, field, expected in (
                (Fact, "fact_json", facts),
                (Coverage, "coverage_json", coverage),
            ):
                saved = (
                    await session.scalars(
                        select(model)
                        .filter_by(**_keys(scope), batch_sequence=row.sequence)
                        .order_by(model.ordinal)
                        .limit(observed.MAX_ROWS + 1)
                    )
                ).all()
                if [item.ordinal for item in saved] != list(
                    range(1, len(expected) + 1)
                ) or [getattr(item, field).encode() for item in saved] != [
                    journal.canonical(item) for item in expected
                ]:
                    observed.deny("observation_source_membership_mismatch")
                for item, raw in zip(saved, expected, strict=True):
                    fields = (
                        ("family", "product", "identity_sha256", "row_sha256")
                        if model is Fact
                        else ("family", "product")
                    )
                    if any(getattr(item, name) != raw[name] for name in fields):
                        observed.deny("observation_normalized_fact_mismatch")
                    if model is Fact:
                        if (
                            item.generation_at
                            != datetime.fromisoformat(raw["generation_at"])
                            or item.fill_at
                            != (
                                None
                                if raw["fill_at"] is None
                                else datetime.fromisoformat(raw["fill_at"])
                            )
                            or item.metadata_sha256
                            != raw["metadata_observations"][0]["row_sha256"]
                        ):
                            observed.deny("observation_normalized_fact_mismatch")
                    elif any(
                        getattr(item, name) != datetime.fromisoformat(raw[name])
                        for name in ("started_at", "ended_at")
                    ):
                        observed.deny("observation_normalized_coverage_mismatch")
        data = self._checked_document(row, scope)
        for name, values in (("source", facts), ("coverage", coverage)):
            if data[f"{name}_membership_count"] != len(values) or data[
                f"{name}_membership_sha256"
            ] != journal.digest(journal.canonical(values)):
                observed.deny("observation_source_membership_mismatch")

    async def _witnesses(self, scope, upto, single, window):
        """Indexed queries span all durable history. Absence is only a selector."""
        value = json.loads(single.receipt_json)
        facts, coverage = _membership(single)
        selected = {upto} if upto else set()
        overflow = False
        counts = {}
        if not upto:
            return selected, {"facts": 0, "coverage": 0, "findings": 0}, False
        start = datetime.fromisoformat(value["captures"][0]["requested_start"])
        end = datetime.fromisoformat(value["generation_observation_cutoff"])
        async with self.session_factory() as session, session.begin():
            await self.journal._lock(session, scope)
            related = or_(
                Fact.identity_sha256.in_([item["identity_sha256"] for item in facts]),
                and_(Fact.generation_at >= start, Fact.generation_at <= end),
                and_(
                    Fact.fill_at >= window.started_at, Fact.fill_at <= window.ended_at
                ),
                and_(Fact.family == "fills", Fact.fill_at.is_(None)),
            )
            rows = (
                await session.scalars(
                    select(func.min(Fact.batch_sequence))
                    .filter_by(**_keys(scope))
                    .where(Fact.batch_sequence <= upto, related)
                    .group_by(
                        Fact.identity_sha256, Fact.row_sha256, Fact.metadata_sha256
                    )
                    .limit(observed.MAX_ROWS + 1)
                )
            ).all()
            overflow |= len(rows) > observed.MAX_ROWS
            selected.update(rows)
            for item in facts:
                generation = datetime.fromisoformat(item["generation_at"])
                tests = [
                    and_(
                        Coverage.started_at <= generation,
                        Coverage.ended_at >= generation,
                    )
                ]
                if item["fill_at"] is not None:
                    filled = datetime.fromisoformat(item["fill_at"])
                    tests.append(
                        and_(
                            Coverage.started_at <= filled,
                            Coverage.ended_at >= filled + timedelta(seconds=300),
                        )
                    )
                exists = (
                    select(Fact.ordinal)
                    .where(
                        Fact.environment == Coverage.environment,
                        Fact.account_id == Coverage.account_id,
                        Fact.settlement_currency == Coverage.settlement_currency,
                        Fact.batch_sequence == Coverage.batch_sequence,
                        Fact.identity_sha256 == item["identity_sha256"],
                    )
                    .exists()
                )
                witness = await session.scalar(
                    select(Coverage.batch_sequence)
                    .filter_by(**_keys(scope))
                    .where(
                        Coverage.batch_sequence <= upto,
                        Coverage.family == item["family"],
                        or_(
                            Coverage.product == item["product"], Coverage.product == "*"
                        ),
                        or_(*tests),
                        ~exists,
                    )
                    .order_by(Coverage.batch_sequence)
                    .limit(1)
                )
                if witness is not None:
                    selected.add(witness)
            # Resolve generation coverage from indexed intervals, without loading
            # complete old captures merely to search for an interval.
            for domain in value["coverage"]:
                point = window.started_at
                for _ in range(observed.MAX_CAPTURES):
                    own = [
                        datetime.fromisoformat(item["ended_at"])
                        for item in coverage
                        if (item["family"], item["product"])
                        == (domain["family"], domain["product"])
                        and datetime.fromisoformat(item["started_at"])
                        <= point
                        <= datetime.fromisoformat(item["ended_at"])
                    ]
                    if own and max(own) >= end:
                        break
                    older = await session.scalar(
                        select(Coverage)
                        .filter_by(
                            **_keys(scope),
                            family=domain["family"],
                            product=domain["product"],
                        )
                        .where(
                            Coverage.batch_sequence <= upto,
                            Coverage.started_at <= point,
                            Coverage.ended_at > point,
                        )
                        .order_by(Coverage.ended_at.desc(), Coverage.batch_sequence)
                        .limit(1)
                    )
                    own_end = max(own, default=point)
                    if older is None or older.ended_at <= own_end:
                        if own_end <= point:
                            break
                        point = own_end
                    else:
                        selected.add(older.batch_sequence)
                        point = older.ended_at
                    if point >= end:
                        break
                else:
                    overflow = True
            for name, model in (
                ("facts", Fact),
                ("coverage", Coverage),
                ("findings", Finding),
            ):
                counts[name] = await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .filter_by(**_keys(scope))
                    .where(model.batch_sequence <= upto)
                )
        overflow |= len(selected) + 1 > observed.MAX_CAPTURES
        return sorted(selected) if not overflow else [], counts, overflow

    async def _derive(self, scope, reference, window, sequence):
        chain, single = await self._source(scope, reference, window)
        selected, counts, overflow = await self._witnesses(
            scope, sequence - 1, single, window
        )
        previous = await self._batch(scope, sequence - 1) if sequence > 1 else None
        if sequence > 1 and previous is None:
            observed.deny("observation_predecessor_missing")
        previous_data = self._checked_document(previous, scope) if previous else None
        if previous:
            prior_anchor = json.loads(previous.receipt_json)["continuation_anchor"]
            for count_name, anchor_name in (
                ("facts", "source_facts_through_sequence"),
                ("coverage", "coverage_through_sequence"),
                ("findings", "unresolved_findings_through_sequence"),
            ):
                if (
                    type(prior_anchor[anchor_name]) is not int
                    or prior_anchor[anchor_name] != counts[count_name]
                ):
                    observed.deny("observation_continuation_prefix_incomplete")
        sources, witnesses, byte_count = [], [], _bytes(chain)
        previous_source = None
        for number in selected:
            row = await self._batch(scope, number)
            if row is None:
                observed.deny("observation_witness_missing")
            data = self._checked_document(row, scope)
            ref = observed.CaptureReference(**data["reference"])
            original, checked = await self._source(scope, ref, _window(data["window"]))
            await self._stored_membership(scope, row, checked)
            if number == sequence - 1:
                previous_source = json.loads(checked.receipt_json)
            byte_count += _bytes(original)
            if byte_count > observed.MAX_SOURCE_BYTES:
                overflow = True
                break
            sources.append((original, ref))
            witnesses.append(
                {
                    "sequence": number,
                    "event_sha256": row.event_sha256,
                    "source_membership_sha256": data["source_membership_sha256"],
                    "coverage_membership_sha256": data["coverage_membership_sha256"],
                }
            )
        if overflow:
            sources, witnesses = [], []
        replay = observed._replay(
            (*sources, (chain, reference)),
            scope,
            window,
            observed.POLICY_SHA256,
            adjacent=False,
        )
        receipt = json.loads(replay.receipt_json)
        findings = receipt["findings"]
        if overflow:
            findings.append(
                {
                    "kind": "relevant_source_proof_budget_exceeded",
                    "capture_id": reference.capture_id,
                }
            )
        current = json.loads(single.receipt_json)
        # Persist source unknowns, so a later empty capture cannot wash them out.
        persistent = {
            "execution_time_unknown",
            "cashflow_product_or_currency_unsupported",
            "cashflow_operand_missing",
            "instrument_metadata_observation_conflict",
        }
        for reason in sorted(set(receipt["blocking_reasons"]) & persistent):
            findings.append({"kind": reason, "capture_id": reference.capture_id})
        if previous and not overflow:
            if previous_source is None:
                observed.deny("observation_predecessor_source_missing")
            prior = previous_source
            if datetime.fromisoformat(
                current["generation_observation_cutoff"]
            ) < datetime.fromisoformat(prior["generation_observation_cutoff"]):
                findings.append(
                    {
                        "kind": "generation_cutoff_regressed",
                        "capture_id": reference.capture_id,
                    }
                )
            gap = datetime.fromisoformat(
                current["observed_completed_at"]
            ) - datetime.fromisoformat(prior["observed_completed_at"])
            if gap < timedelta(0) or gap > timedelta(seconds=3600):
                findings.append(
                    {"kind": "measurement_gap", "capture_id": reference.capture_id}
                )
            if datetime.fromisoformat(
                current["captures"][0]["requested_start"]
            ) > datetime.fromisoformat(prior["generation_observation_cutoff"]):
                findings.append(
                    {
                        "kind": "generation_overlap_gap",
                        "capture_id": reference.capture_id,
                    }
                )
        if len(findings) > observed.MAX_ROWS:
            findings = [
                {
                    "kind": "invalidation_proof_budget_exceeded",
                    "capture_id": reference.capture_id,
                }
            ]
        facts, coverage = _membership(single)
        anchor = {
            "schema_version": "ctcc.account_observation_continuation.v1",
            "policy_sha256": observed.POLICY_SHA256,
            "scope_sha256": _scope_hash(scope),
            "through_sequence": sequence,
            "previous_sequence": sequence - 1,
            "previous_event_sha256": None
            if previous is None
            else previous.event_sha256,
            "previous_anchor_sha256": None
            if previous_data is None
            else previous_data["anchor_sha256"],
            "source_facts_through_sequence": counts["facts"] + len(facts),
            "coverage_through_sequence": counts["coverage"] + len(coverage),
            "unresolved_findings_through_sequence": counts["findings"] + len(findings),
            "current_source_membership_sha256": journal.digest(
                journal.canonical(facts)
            ),
            "current_coverage_membership_sha256": journal.digest(
                journal.canonical(coverage)
            ),
            "verified_witnesses": witnesses,
            "required_proof_budget_exceeded": overflow,
        }
        receipt["continuation_anchor"], receipt["findings"] = anchor, findings
        if counts["findings"] or findings or overflow:
            receipt["observed_fill_cashflow_total"] = None
            receipt["window_state"] = "incomplete_observation"
            receipt["blocking_reasons"] = sorted(
                set(receipt["blocking_reasons"])
                | {"persistent_source_invalidation_unresolved"}
            )
        encoded = journal.canonical(receipt)
        if len(encoded) > observed.MAX_RECEIPT_BYTES:
            observed.deny("observation_receipt_byte_bound")
        replay = observed.ObservationReplay(encoded)
        document = {
            "schema_version": "ctcc.account_observation_batch.v1",
            "scope_sha256": _scope_hash(scope),
            "sequence": sequence,
            "previous_sha256": None if previous is None else previous.event_sha256,
            "reference": observed.reference_document(reference),
            "window": observed.window_document(window),
            "policy_sha256": observed.POLICY_SHA256,
            "receipt_sha256": replay.receipt_sha256,
            "anchor_json": journal.canonical(anchor).decode(),
            "anchor_sha256": journal.digest(journal.canonical(anchor)),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        for name, values in (
            ("source", facts),
            ("coverage", coverage),
            ("finding", findings),
        ):
            document[f"{name}_membership_sha256"] = journal.digest(
                journal.canonical(values)
            )
            document[f"{name}_membership_count"] = len(values)
        return journal.canonical(document), replay, facts, coverage, findings, chain

    async def read(self, scope, *, after=0, limit=PAGE_SIZE):
        try:
            result = []
            for row in await self._rows(scope, after=after, limit=limit):
                data = self._checked_document(row, scope)
                reference, window = (
                    observed.CaptureReference(**data["reference"]),
                    _window(data["window"]),
                )
                document, replay, _, _, findings, chain = await self._derive(
                    scope, reference, window, row.sequence
                )
                if (
                    row.document_json.encode() != document
                    or row.receipt_json.encode() != replay.receipt_json
                    or row.source_sequence != len(chain)
                    or capture._utc(row.db_recorded_at)
                    < capture._utc(chain[-1].db_recorded_at)
                ):
                    observed.deny("observation_persisted_replay_mismatch")
                _, single = await self._source(scope, reference, window)
                await self._stored_membership(scope, row, single)
                async with self.session_factory() as session, session.begin():
                    saved = (
                        await session.scalars(
                            select(Finding.finding_json)
                            .filter_by(**_keys(scope), batch_sequence=row.sequence)
                            .order_by(Finding.ordinal)
                            .limit(observed.MAX_ROWS + 1)
                        )
                    ).all()
                if [item.encode() for item in saved] != [
                    journal.canonical(item) for item in findings
                ]:
                    observed.deny("observation_finding_membership_mismatch")
                result.append(
                    ObservationBatchReadback(
                        row.sequence, row.event_sha256, document, replay
                    )
                )
            return tuple(result)
        except Exception:  # noqa: BLE001 -- never expose private raw or SQL errors
            raise observed.AccountObservationError("observation_read_failed") from None

    async def read_balance_source_page(
        self, scope, *, through_sequence, expected_head_sha256, after=0, limit=PAGE_SIZE
    ):
        """B5 read-only raw measurement enumeration, not B3 cashflow replay.

        A fixed original head and explicit cursor make population membership
        reviewable. Every page starts at exactly after+1; short reads before the
        pinned end fail. Original B1 source is replayed once for each sample.
        Page size bounds memory/SQL output, not whole-history computation time.
        """
        from app.trade_qualification import account_portfolio_components as components
        from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

        try:
            checked_bootstrap(scope, LedgerScope)
            journal._sha(expected_head_sha256)
            if (
                type(through_sequence) is not int
                or not 1 <= through_sequence <= MAX_REVISION
                or type(after) is not int
                or not 0 <= after < through_sequence
                or type(limit) is not int
                or not 1 <= limit <= PAGE_SIZE
            ):
                components.deny("component_balance_page_bound")
            async with self.session_factory() as session, session.begin():
                await self.journal._lock(session, scope)
                head = await session.scalar(
                    select(Batch).filter_by(**_keys(scope), sequence=through_sequence)
                )
                if head is None or head.event_sha256 != expected_head_sha256:
                    components.deny("component_balance_head_mismatch")
                self._checked_document(head, scope)
                previous = (
                    None
                    if after == 0
                    else await session.scalar(
                        select(Batch).filter_by(**_keys(scope), sequence=after)
                    )
                )
                if after and previous is None:
                    components.deny("component_balance_predecessor_missing")
                if previous is not None:
                    self._checked_document(previous, scope)
                prior_sha = None if previous is None else previous.event_sha256
                rows = (
                    await session.scalars(
                        select(Batch)
                        .filter_by(**_keys(scope))
                        .where(
                            Batch.sequence > after, Batch.sequence <= through_sequence
                        )
                        .order_by(Batch.sequence)
                        .limit(limit)
                    )
                ).all()
            if len(rows) != min(limit, through_sequence - after):
                components.deny("component_balance_population_missing")
            items = []
            for number, row in enumerate(rows, after + 1):
                data = self._checked_document(row, scope)
                if row.sequence != number or row.previous_sha256 != prior_sha:
                    components.deny("component_balance_chain_missing")
                reference = observed.CaptureReference(**data["reference"])
                chain = await self.journal.read_chain(scope, reference.capture_id)
                if len(chain) != row.source_sequence or capture._utc(
                    row.db_recorded_at
                ) < capture._utc(chain[-1].db_recorded_at):
                    components.deny("component_balance_original_terminal_mismatch")
                measurement = components.observe_balance(
                    chain, reference=reference, scope=scope
                )
                items.append((number, prior_sha, row.event_sha256, measurement))
                prior_sha = row.event_sha256
            return BalanceSourcePage(
                through_sequence, expected_head_sha256, after, tuple(items)
            )
        except Exception:  # noqa: BLE001 -- no raw private/SQL contents
            raise observed.AccountObservationError(
                "component_balance_source_read_failed"
            ) from None

    async def append(self, scope, reference, window, *, expected_revision):
        try:
            return await self._append(scope, reference, window, expected_revision)
        except Exception:  # noqa: BLE001 -- committed evidence survives readback failure
            raise observed.AccountObservationError(
                "observation_append_failed"
            ) from None

    async def _append(self, scope, reference, window, expected_revision):
        observed.reference_document(reference)
        observed.window_document(window)
        if (
            type(expected_revision) is not int
            or not 0 <= expected_revision < MAX_REVISION
        ):
            observed.deny("observation_revision_invalid")
        async with self.session_factory() as session, session.begin():
            await self.journal._lock(session, scope)
            existing = await session.scalar(
                select(Batch).where(Batch.capture_id == reference.capture_id)
            )
        if existing:
            data = self._checked_document(existing, scope)
            if (
                data["reference"] != observed.reference_document(reference)
                or data["window"] != observed.window_document(window)
                or existing.sequence != expected_revision + 1
            ):
                observed.deny("observation_idempotency_conflict")
            return (await self.read(scope, after=existing.sequence - 1, limit=1))[0]
        latest = await self._batch(scope)
        if (0 if latest is None else latest.sequence) != expected_revision:
            observed.deny("observation_revision_conflict")
        sequence = expected_revision + 1
        document, replay, facts, coverage, findings, chain = await self._derive(
            scope, reference, window, sequence
        )
        previous = None if latest is None else latest.event_sha256
        async with self.session_factory() as session, session.begin():
            await self.journal._lock(session, scope)
            latest = await session.scalar(
                select(Batch)
                .filter_by(**_keys(scope))
                .order_by(Batch.sequence.desc())
                .limit(1)
            )
            if (0 if latest is None else latest.sequence) != expected_revision or (
                None if latest is None else latest.event_sha256
            ) != previous:
                observed.deny("observation_revision_conflict")
            session.add(
                Batch(
                    **_keys(scope),
                    sequence=sequence,
                    capture_id=reference.capture_id,
                    source_sequence=len(chain),
                    previous_sha256=previous,
                    event_sha256=journal.digest(document),
                    document_json=document.decode(),
                    receipt_json=replay.receipt_json.decode(),
                )
            )
            await session.flush()
            for number, item in enumerate(facts, 1):
                session.add(
                    Fact(
                        **_keys(scope),
                        batch_sequence=sequence,
                        ordinal=number,
                        **{
                            key: item[key]
                            for key in (
                                "family",
                                "product",
                                "identity_sha256",
                                "row_sha256",
                            )
                        },
                        metadata_sha256=item["metadata_observations"][0]["row_sha256"],
                        generation_at=datetime.fromisoformat(item["generation_at"]),
                        fill_at=None
                        if item["fill_at"] is None
                        else datetime.fromisoformat(item["fill_at"]),
                        fact_json=journal.canonical(item).decode(),
                    )
                )
            for number, item in enumerate(coverage, 1):
                session.add(
                    Coverage(
                        **_keys(scope),
                        batch_sequence=sequence,
                        ordinal=number,
                        family=item["family"],
                        product=item["product"],
                        started_at=datetime.fromisoformat(item["started_at"]),
                        ended_at=datetime.fromisoformat(item["ended_at"]),
                        coverage_json=journal.canonical(item).decode(),
                    )
                )
            for number, item in enumerate(findings, 1):
                session.add(
                    Finding(
                        **_keys(scope),
                        batch_sequence=sequence,
                        ordinal=number,
                        finding_json=journal.canonical(item).decode(),
                    )
                )
            await session.flush()
        committed = await self.read(scope, after=sequence - 1, limit=1)
        if len(committed) != 1 or committed[0].document_json != document:
            observed.deny("observation_commit_readback_unknown")
        return committed[0]
