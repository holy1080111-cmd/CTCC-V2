"""Bounded original-genesis B1/B3/DB0050 replay, never native ownership.

Each frame reopens its original source and every witness used by its original
B3 receipt. Pages do not start a new genesis. The old 32-source replay and wire
contracts are unchanged; 32 remains the per-frame comparison/partition budget.
This is source population replay, not a full economic/window/finality reducer.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
    _membership,
)
from app.domain import account_history_partition_v1 as partition
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

MAX_PAGE_FRAMES = 8
MAX_FRAME_BYTES = observed.MAX_SOURCE_BYTES
MAX_PAGE_BYTES = observed.MAX_SOURCE_BYTES
MAX_INDEX_BYTES = observed.MAX_SOURCE_BYTES
MAX_BLOCKERS = 256
MAX_BLOCKER_BYTES = 128
ORIGINAL_BLOCKER_CODES = frozenset(
    {
        "generation_observation_tail_below_policy",
        "generation_query_cutoff_stale",
        "generation_window_gap",
        "source_observations_require_reconciliation",
        "execution_time_unknown",
        "instrument_metadata_observation_conflict",
        "cashflow_product_or_currency_unsupported",
        "cashflow_operand_missing",
        "persistent_source_invalidation_unresolved",
    }
)
LOCAL_BLOCKER_CODES = frozenset(
    {
        "original_cross_capture_row_conflict",
        "original_required_proof_budget_exceeded",
        "original_native_registration_missing",
        "native_acquisition_not_owned",
        "economic_stream_reducer_unimplemented",
        "history_tail_finality_unknown",
    }
)
SCHEMA = "ctcc.original_genesis_account_history_stream.v1"
POLICY_BYTES = journal.canonical(
    {
        "schema_version": SCHEMA,
        "original_b3_policy_sha256": observed.POLICY_SHA256,
        "partition_policy_sha256": partition.POLICY_SHA256,
        "genesis": "original_sequence_one_never_reset",
        "raw_sources": "replay_current_and_every_original_B3_witness",
        "partition_span": partition.PARTITION_SPAN,
        "maximum_page_frames": MAX_PAGE_FRAMES,
        "maximum_frame_bytes": MAX_FRAME_BYTES,
        "maximum_page_bytes": MAX_PAGE_BYTES,
        "maximum_compact_index_bytes": MAX_INDEX_BYTES,
        "maximum_retained_blockers": MAX_BLOCKERS,
        "maximum_blocker_bytes": MAX_BLOCKER_BYTES,
        "original_blocker_codes": sorted(ORIGINAL_BLOCKER_CODES),
        "local_blocker_codes": sorted(LOCAL_BLOCKER_CODES),
        "maximum_witnesses_per_frame": observed.MAX_CAPTURES - 1,
        "budget_exceeded": "deny_never_truncate_or_renew",
        "economics_and_history_tail": "unimplemented_unknown",
        "native_owner": "never_issued_by_pure_replay",
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class OriginalHistoryStreamError(ValueError):
    """Fixed rejection codes; no source/account/SQL exception text."""


def _deny(code):
    raise OriginalHistoryStreamError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryWitnessV1:
    sequence: int
    chain: tuple


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryFrameV1:
    chain: tuple
    batch: ObservationBatchReadback
    witnesses: tuple[OriginalHistoryWitnessV1, ...] = ()
    native_registration: bytes | None = None
    checkpoint: bytes | None = None


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryPageV1:
    index: int
    previous_page_sha256: str | None
    frames: tuple[OriginalHistoryFrameV1, ...]


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryStreamReplayV1:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def execution_authority(self):
        return False

    @property
    def account_complete(self):
        return False


def _chain_measure(chain):
    if type(chain) is not tuple or not 1 <= len(chain) <= journal.MAX_EVENTS:
        _deny("stream_original_chain_required")
    size, membership = 0, hashlib.sha256()
    for point in chain:
        if (
            type(point) is not journal.JournalReadback
            or type(point.event) is not journal._JournalEvent
        ):
            _deny("stream_original_readback_required")
        # Readback timestamps are validated by the original verifier. A new
        # readback can be later; immutable event bytes and DB recording time
        # must still match when this original capture is used as a witness.
        raw_pins = []
        for raw in (
            point.event.event_json,
            point.event.raw_body,
            point.event.packet_payload,
        ):
            if raw is not None and type(raw) is not bytes:
                _deny("stream_original_bytes_required")
            size += 0 if raw is None else len(raw)
            if size > MAX_FRAME_BYTES:
                _deny("stream_frame_byte_bound")
            raw_pins.append(None if raw is None else [len(raw), journal.digest(raw)])
        if type(point.db_recorded_at) is not datetime:
            _deny("stream_original_readback_required")
        membership.update(
            journal.canonical([raw_pins, point.db_recorded_at.isoformat()])
        )
        membership.update(b"\n")
    return size, membership.hexdigest()


def _frame_measure(frame):
    if (
        type(frame) is not OriginalHistoryFrameV1
        or type(frame.batch) is not ObservationBatchReadback
        or type(frame.batch.replay) is not observed.ObservationReplay
        or type(frame.witnesses) is not tuple
        or len(frame.witnesses) >= observed.MAX_CAPTURES
    ):
        _deny("stream_exact_frame_required")
    size, source_pin = _chain_measure(frame.chain)
    witness_pins = []
    for witness in frame.witnesses:
        if type(witness) is not OriginalHistoryWitnessV1:
            _deny("stream_exact_witness_required")
        original_size, original_pin = _chain_measure(witness.chain)
        size += original_size
        if size > MAX_FRAME_BYTES:
            _deny("stream_frame_byte_bound")
        witness_pins.append([witness.sequence, original_pin])
    optional = []
    for raw in (
        frame.batch.document_json,
        frame.batch.replay.receipt_json,
        frame.native_registration,
        frame.checkpoint,
    ):
        if raw is not None and type(raw) is not bytes:
            _deny("stream_original_bytes_required")
        size += 0 if raw is None else len(raw)
        if size > MAX_FRAME_BYTES:
            _deny("stream_frame_byte_bound")
        optional.append(None if raw is None else [len(raw), journal.digest(raw)])
    return size, journal.digest(journal.canonical([source_pin, witness_pins, optional]))


def original_history_page_sha256_v1(page):
    """Pure page identity; a matching hash grants no provenance/authority."""
    if (
        type(page) is not OriginalHistoryPageV1
        or type(page.index) is not int
        or page.index < 0
        or type(page.frames) is not tuple
        or not 1 <= len(page.frames) <= MAX_PAGE_FRAMES
    ):
        _deny("stream_exact_page_required")
    if page.previous_page_sha256 is not None:
        partition._hash(page.previous_page_sha256)
    size, pins = 0, []
    for frame in page.frames:
        frame_size, frame_pin = _frame_measure(frame)
        size += frame_size
        if size > MAX_PAGE_BYTES:
            _deny("stream_page_byte_bound")
        pins.append(frame_pin)
    return journal.digest(
        journal.canonical([page.index, page.previous_page_sha256, pins])
    )


def _source(chain, scope):
    return proof._original_source_reference(chain, scope)


def _blocker_codes(values, *, allowed=ORIGINAL_BLOCKER_CODES):
    if (
        type(values) is not list
        or len(values) > MAX_BLOCKERS
        or any(
            type(value) is not str
            or not 1 <= len(value) <= MAX_BLOCKER_BYTES
            or value[0] not in "abcdefghijklmnopqrstuvwxyz"
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                for character in value
            )
            or value not in allowed
            for value in values
        )
        or values != sorted(set(values))
    ):
        _deny("stream_original_blocker_bound_or_shape")
    return values


def _retain_blockers(blockers, values, index_bytes):
    values = _blocker_codes(
        sorted(set(values)), allowed=ORIGINAL_BLOCKER_CODES | LOCAL_BLOCKER_CODES
    )
    fresh = set(values) - blockers
    used = index_bytes + sum(len(reason) + 4 for reason in fresh)
    if len(blockers | fresh) > MAX_BLOCKERS or used > MAX_INDEX_BYTES:
        _deny("stream_retained_blocker_byte_bound")
    blockers.update(fresh)
    return used


def _original_receipt(frame, document, scope, index):
    receipt = lifecycle._json(frame.batch.replay.receipt_json)
    if (
        frame.batch.event_sha256 != journal.digest(frame.batch.document_json)
        or document["receipt_sha256"] != frame.batch.replay.receipt_sha256
        or receipt.get("scope_sha256") != document["scope_sha256"]
        or receipt.get("policy_sha256") != observed.POLICY_SHA256
        or journal.canonical(receipt.get("window"))
        != journal.canonical(document["window"])
        or receipt.get("account_complete") is not False
        or receipt.get("execution_authority") is not False
        or receipt.get("admission") != "DENY"
    ):
        _deny("stream_original_receipt_binding_invalid")
    anchor = receipt.get("continuation_anchor")
    if (
        type(anchor) is not dict
        or journal.canonical(anchor).decode() != document["anchor_json"]
        or type(anchor.get("verified_witnesses")) is not list
        or len(anchor["verified_witnesses"]) != len(frame.witnesses)
    ):
        _deny("stream_original_witness_binding_invalid")
    prior, last = [], 0
    for witness, expected in zip(
        frame.witnesses, anchor["verified_witnesses"], strict=True
    ):
        number = witness.sequence
        if type(number) is not int or not last < number < document["sequence"]:
            _deny("stream_original_witness_order_invalid")
        old = index.get(number)
        expected_witness = (
            None
            if old is None
            else {
                "sequence": number,
                "event_sha256": old["event_sha256"],
                "source_membership_sha256": old["source_membership_sha256"],
                "coverage_membership_sha256": old["coverage_membership_sha256"],
            }
        )
        if old is None or journal.canonical(expected) != journal.canonical(
            expected_witness
        ):
            _deny("stream_original_witness_pin_changed")
        reference = _source(witness.chain, scope)
        if (
            observed.reference_document(reference) != old["reference"]
            or _chain_measure(witness.chain)[1] != old["chain_sha256"]
        ):
            _deny("stream_original_witness_source_changed")
        prior.append((witness.chain, reference))
        last = number
    reference = _source(frame.chain, scope)
    if observed.reference_document(reference) != document["reference"]:
        _deny("stream_original_source_reference_changed")
    window = observed.ExecutionWindow(
        *(
            datetime.fromisoformat(document["window"][name])
            for name in ("started_at", "ended_at")
        )
    )
    single = observed.replay_observation_index(
        ((frame.chain, reference),), scope=scope, window=window
    )
    facts, coverage = _membership(single)
    # With one source there is no previous capture for the adjacency policy.
    # The same replay supplies membership and receipt comparisons in that case.
    combined = single
    if prior:
        combined = observed._replay(
            (*prior, (frame.chain, reference)),
            scope,
            window,
            observed.POLICY_SHA256,
            adjacent=False,
        )
    replayed = lifecycle._json(combined.receipt_json)
    for name in (
        "captures",
        "source_index",
        "coverage",
        "cashflows",
        "generation_observation_cutoff",
        "observed_completed_at",
    ):
        if journal.canonical(receipt.get(name)) != journal.canonical(replayed[name]):
            _deny("stream_original_row_query_replay_mismatch")
    findings = receipt.get("findings")
    blockers = _blocker_codes(receipt.get("blocking_reasons"))
    if (
        type(findings) is not list
        or journal.canonical(findings[: len(replayed["findings"])])
        != journal.canonical(replayed["findings"])
        or not set(replayed["blocking_reasons"]).issubset(blockers)
    ):
        _deny("stream_original_findings_or_blockers_removed")
    for name, values in (
        ("source", facts),
        ("coverage", coverage),
        ("finding", findings),
    ):
        if document[name + "_membership_count"] != len(values) or document[
            name + "_membership_sha256"
        ] != journal.digest(journal.canonical(values)):
            _deny("stream_original_membership_changed")
    return reference, facts, findings, blockers


def replay_original_history_stream_v1(
    pages,
    *,
    scope,
    expected_through_sequence,
    expected_b3_head_sha256,
    expected_checkpoint_sha256,
    expected_policy_sha256=POLICY_SHA256,
):
    """Consume the whole original prefix once, or deny without a partial result.

    The caller supplies an iterable of bounded pages, not a saved checkpoint as
    a replacement for original sources. No iterator/IO callback is treated as
    trusted; the runtime producer must separately own and fence acquisition.
    """
    try:
        return _replay(
            pages,
            scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            expected_policy_sha256,
        )
    except OriginalHistoryStreamError:
        raise
    except Exception:  # noqa: BLE001 -- source-bearing exceptions stay private
        _deny("stream_original_replay_failed")


async def replay_original_history_stream_async_v1(
    pages,
    *,
    scope,
    expected_through_sequence,
    expected_b3_head_sha256,
    expected_checkpoint_sha256,
    expected_policy_sha256=POLICY_SHA256,
):
    """Replay bounded async pages in this task; never issue native ownership.

    Awaiting the producer does not renew source times, reset original genesis,
    authenticate acquisition, or relax any page/frame/retained-state budget.
    Native callers must separately retain their original lease and final fence.
    """
    engine = _replay_engine(
        scope,
        expected_through_sequence,
        expected_b3_head_sha256,
        expected_checkpoint_sha256,
        expected_policy_sha256,
    )
    try:
        next(engine)
        async for page in pages:
            if type(page) is not OriginalHistoryPageV1:
                _deny("stream_exact_page_required")
            engine.send(page)
        return _finish_replay_engine(engine)
    except OriginalHistoryStreamError:
        raise
    except Exception:  # noqa: BLE001 -- source-bearing errors remain private
        _deny("stream_original_replay_failed")
    finally:
        # Cancellation leaves no resumable receipt, replacement owner or retry.
        engine.close()


def _finish_replay_engine(engine):
    try:
        engine.send(None)
    except StopIteration as completed:
        if type(completed.value) is not OriginalHistoryStreamReplayV1:
            _deny("stream_original_replay_failed")
        return completed.value
    _deny("stream_original_replay_failed")


def _replay(
    pages,
    scope,
    through,
    head,
    checkpoint_head,
    policy_pin,
    *,
    _financial=None,
    _financial_v2=None,
):
    engine = _replay_engine(
        scope,
        through,
        head,
        checkpoint_head,
        policy_pin,
        _financial=_financial,
        _financial_v2=_financial_v2,
    )
    try:
        next(engine)
        for page in pages:
            if type(page) is not OriginalHistoryPageV1:
                _deny("stream_exact_page_required")
            engine.send(page)
        return _finish_replay_engine(engine)
    finally:
        engine.close()


def _replay_engine(
    scope,
    through,
    head,
    checkpoint_head,
    policy_pin,
    *,
    _financial=None,
    _financial_v2=None,
    _raw_bank_v1=None,
    _api_population_v2=None,
):
    scope_doc = partition._scope(scope)
    if _financial is not None and _financial_v2 is not None:
        _deny("stream_financial_accumulator_scope_changed")
    if _financial is not None:
        from app.trade_qualification import (
            account_history_global_financial_v1 as financial,
        )

        if (
            type(_financial) is not financial.OriginalHistoryFinancialAccumulatorV1
            or _financial._scope_sha256 != journal.digest(journal.canonical(scope_doc))
        ):
            _deny("stream_financial_accumulator_scope_changed")
    if _financial_v2 is not None:
        from app.trade_qualification import (
            account_history_global_financial_v2 as financial_v2,
        )

        if (
            type(_financial_v2)
            is not financial_v2.OriginalHistoryFinancialAccumulatorV2
            or _financial_v2._scope_sha256
            != journal.digest(journal.canonical(scope_doc))
        ):
            _deny("stream_financial_accumulator_scope_changed")
    if _raw_bank_v1 is not None:
        from app.trade_qualification import (
            account_original_raw_proof_bank_v1 as raw_bank,
        )

        if (
            type(_raw_bank_v1) is not raw_bank.OriginalAccountRawProofBankAccumulatorV1
            or _raw_bank_v1._fold is not _financial_v2
            or _financial is not None
        ):
            _deny("stream_financial_accumulator_scope_changed")
    if _api_population_v2 is not None:
        from app.trade_qualification import (
            account_original_raw_proof_bank_v1 as raw_bank,
        )

        if (
            type(_api_population_v2)
            is not raw_bank.OriginalAccountApiPopulationAccumulatorV2
            or _api_population_v2._original is not _raw_bank_v1
            or _raw_bank_v1 is None
            or _raw_bank_v1._fold is not _financial_v2
        ):
            _deny("stream_financial_accumulator_scope_changed")
    partition._number(through)
    partition._hash(head)
    if policy_pin != POLICY_SHA256 or type(policy_pin) is not str:
        _deny("stream_policy_mismatch")
    if through < partition.PARTITION_SPAN:
        if checkpoint_head is not None:
            _deny("stream_unexpected_checkpoint_pin")
    else:
        partition._hash(checkpoint_head)
    ledger_scope = LedgerScope(
        account_id=scope.account_id, settlement_currency=scope.settlement_currency
    )
    sequence = page_count = index_bytes = duplicate_rows = conflict_variants = 0
    previous_event = previous_anchor = previous_page = checkpoint_raw = None
    previous_checkpoint = None
    counts = (0, 0, 0)
    index, captures, native_invocations, custody_ids, variants = (
        {},
        set(),
        set(),
        set(),
        {},
    )
    partition_documents, registrations = [], []
    blockers, finding_roots = set(), hashlib.sha256()
    membership = journal.digest(
        journal.canonical([POLICY_SHA256, scope_doc, "original_genesis"])
    )
    source_bytes = active_partition_bytes = 0
    native_missing = 0
    while True:
        page = yield
        if page is None:
            break
        page_pin = original_history_page_sha256_v1(page)
        if page.index != page_count or page.previous_page_sha256 != previous_page:
            _deny("stream_page_gap_or_reordered")
        for frame in page.frames:
            sequence += 1
            if sequence > through:
                _deny("stream_extra_source_after_pinned_head")
            document, anchor = partition._b3(
                frame.batch.document_json,
                scope=scope_doc,
                source_policy=observed.POLICY_SHA256,
            )
            if (
                type(frame.batch.sequence) is not int
                or frame.batch.sequence != sequence
                or document["sequence"] != sequence
                or document["previous_sha256"] != previous_event
                or anchor["previous_anchor_sha256"] != previous_anchor
            ):
                _deny("stream_original_genesis_or_chain_gap")
            increments = tuple(
                document[name + "_membership_count"]
                for name in ("source", "coverage", "finding")
            )
            counts = tuple(a + b for a, b in zip(counts, increments, strict=True))
            if counts != tuple(anchor[name] for name in partition._COUNTS):
                _deny("stream_original_cumulative_counts_changed")
            reference, facts, findings, original_blockers = _original_receipt(
                frame, document, ledger_scope, index
            )
            if reference.capture_id in captures:
                _deny("stream_duplicate_original_capture")
            captures.add(reference.capture_id)
            if frame.native_registration is not None:
                registered = partition._json(frame.native_registration)
                linkage_raw = partition._ascii(registered["native_linkage_json"])
                derived = partition.derive_native_member_registration_v1(
                    scope=scope,
                    b3_document_raw=frame.batch.document_json,
                    native_linkage_raw=linkage_raw,
                    expected_b3_policy_sha256=observed.POLICY_SHA256,
                    expected_b3_event_sha256=frame.batch.event_sha256,
                )
                if derived.record_json != frame.native_registration:
                    _deny("stream_native_registration_changed")
                linkage = partition._json(linkage_raw)
                if (
                    linkage["native_invocation_id"] in native_invocations
                    or linkage["custody_bundle_id"] in custody_ids
                ):
                    _deny("stream_cross_partition_native_identity_conflict")
                native_invocations.add(linkage["native_invocation_id"])
                custody_ids.add(linkage["custody_bundle_id"])
            else:
                native_missing += 1
            for fact in facts:
                identity, variant = fact["identity_sha256"], fact["row_sha256"]
                old_variants = variants.setdefault(identity, set())
                if variant in old_variants:
                    duplicate_rows += 1
                elif old_variants:
                    conflict_variants += 1
                    index_bytes = _retain_blockers(
                        blockers, ("original_cross_capture_row_conflict",), index_bytes
                    )
                old_variants.add(variant)
            index_bytes = _retain_blockers(blockers, original_blockers, index_bytes)
            if anchor["required_proof_budget_exceeded"]:
                index_bytes = _retain_blockers(
                    blockers, ("original_required_proof_budget_exceeded",), index_bytes
                )
            finding_roots.update(journal.canonical([sequence, findings]))
            finding_roots.update(b"\n")
            chain_size, chain_pin = _chain_measure(frame.chain)
            source_bytes += chain_size
            compact = {
                "reference": observed.reference_document(reference),
                "event_sha256": frame.batch.event_sha256,
                "source_membership_sha256": document["source_membership_sha256"],
                "coverage_membership_sha256": document["coverage_membership_sha256"],
                "chain_sha256": chain_pin,
            }
            # Bound canonical compact state, not a claim about Python RSS.
            # No raw chain/packet/receipt is retained in this prefix index.
            index_bytes += (
                len(journal.canonical(compact))
                + sum(
                    len(fact["identity_sha256"]) + len(fact["row_sha256"]) + 8
                    for fact in facts
                )
                + 160
            )
            if index_bytes > MAX_INDEX_BYTES:
                _deny("stream_compact_index_byte_bound")
            index[sequence] = compact
            active_partition_bytes += len(frame.batch.document_json) + (
                0
                if frame.native_registration is None
                else len(frame.native_registration)
            )
            # A valid DB0050 checkpoint embeds every original member byte, so
            # its pre-encoding member bytes cannot exceed its existing cap.
            if active_partition_bytes > partition.MAX_RECORD_BYTES:
                _deny("stream_active_partition_byte_bound")
            partition_documents.append(frame.batch.document_json)
            registrations.append(frame.native_registration)
            if sequence % partition.PARTITION_SPAN == 0:
                stored_checkpoint = partition._json(frame.checkpoint)
                if type(stored_checkpoint) is not dict or stored_checkpoint.get(
                    "members"
                ) != [
                    {
                        "b3_document_json": raw.decode("ascii"),
                        "native_registration_json": None
                        if registration is None
                        else registration.decode("ascii"),
                    }
                    for raw, registration in zip(
                        partition_documents, registrations, strict=True
                    )
                ]:
                    _deny("stream_original_checkpoint_members_changed")
                derived_checkpoint = partition._checkpoint(
                    scope_doc,
                    observed.POLICY_SHA256,
                    tuple(partition_documents),
                    tuple(registrations),
                    previous_checkpoint,
                )
                checkpoint_raw = journal.canonical(derived_checkpoint)
                partition._json(checkpoint_raw)
                if frame.checkpoint != checkpoint_raw:
                    _deny("stream_original_partition_checkpoint_missing_or_changed")
                previous_checkpoint = derived_checkpoint
                partition_documents.clear()
                registrations.clear()
                active_partition_bytes = 0
            elif frame.checkpoint is not None:
                _deny("stream_checkpoint_outside_original_boundary")
            if _financial is not None:
                # CURRENT only, after every original witness/checkpoint check.
                # Witnesses remain evidence and never become another cashflow.
                _financial.consume_current(
                    chain=frame.chain,
                    reference=reference,
                    batch=frame.batch,
                    witnesses=frame.witnesses,
                )
            if _financial_v2 is not None:
                _financial_v2.consume_current(
                    chain=frame.chain,
                    reference=reference,
                    batch=frame.batch,
                    witnesses=frame.witnesses,
                )
            if _raw_bank_v1 is not None:
                _raw_bank_v1._capture_verified_current_v1(
                    chain=frame.chain,
                    reference=reference,
                    batch=frame.batch,
                )
            if _api_population_v2 is not None:
                _api_population_v2._capture_after_verified_current_v2(
                    chain=frame.chain,
                    reference=reference,
                    batch=frame.batch,
                )
            membership = journal.digest(
                journal.canonical(
                    [
                        membership,
                        sequence,
                        compact,
                        frame.batch.replay.receipt_sha256,
                        None
                        if frame.native_registration is None
                        else journal.digest(frame.native_registration),
                        None
                        if frame.checkpoint is None
                        else journal.digest(frame.checkpoint),
                    ]
                )
            )
            previous_event, previous_anchor = (
                frame.batch.event_sha256,
                document["anchor_sha256"],
            )
        page_count += 1
        previous_page = page_pin
    if sequence != through or previous_event != head:
        _deny("stream_incomplete_or_changed_pinned_head")
    if (
        None if checkpoint_raw is None else journal.digest(checkpoint_raw)
    ) != checkpoint_head:
        _deny("stream_checkpoint_head_changed")
    if native_missing:
        index_bytes = _retain_blockers(
            blockers, ("original_native_registration_missing",), index_bytes
        )
    index_bytes = _retain_blockers(
        blockers,
        (
            "native_acquisition_not_owned",
            "economic_stream_reducer_unimplemented",
            "history_tail_finality_unknown",
        ),
        index_bytes,
    )
    receipt = {
        "schema_version": SCHEMA,
        "policy_sha256": POLICY_SHA256,
        "scope_sha256": journal.digest(journal.canonical(scope_doc)),
        "through_sequence": sequence,
        "original_b3_head_sha256": head,
        "checkpoint_sha256": checkpoint_head,
        "full_original_genesis_sources_replayed": True,
        "original_partition_count": sequence // partition.PARTITION_SPAN,
        "active_partition_member_count": len(partition_documents),
        "page_count": page_count,
        "last_page_sha256": previous_page,
        "ordered_original_membership_sha256": membership,
        "original_findings_membership_sha256": finding_roots.hexdigest(),
        "cumulative_original_membership_counts": dict(
            zip(partition._COUNTS, counts, strict=True)
        ),
        "original_source_bytes_replayed": source_bytes,
        "compact_index_canonical_byte_budget_used": index_bytes,
        "native_registration_missing_count": native_missing,
        "observed_duplicate_row_count": duplicate_rows,
        "observed_conflicting_variant_count": conflict_variants,
        "unique_observed_row_identity_count": len(variants),
        "blocking_reasons": sorted(blockers),
        "all_observation_comparisons_complete": False,
        "current_history_tail_finality": None,
        "funding_accrual": None,
        "daily_net_pnl": None,
        "weekly_net_pnl": None,
        "loss_streak_seed": None,
        "historical_native_hwm": None,
        "native_history_prefix_verified": False,
        "account_complete": False,
        "account_revision_published": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    encoded = journal.canonical(receipt)
    if len(encoded) > observed.MAX_RECEIPT_BYTES:
        _deny("stream_final_receipt_byte_bound")
    return OriginalHistoryStreamReplayV1(encoded)


FINANCIAL_STREAM_SCHEMA_V2 = "ctcc.original_genesis_financial_stream.v2"


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryFinancialStreamReplayV2:
    """Immutable component bytes and their exact join; never an account issuer."""

    receipt_json: bytes
    original_replay: OriginalHistoryStreamReplayV1
    financial_facts: object

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def execution_authority(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _financial_stream_policy_bytes_v2():
    # The core imports V1 replay helpers. Import only after module loading so
    # adding this API does not introduce a circular-import initialization path.
    from app.trade_qualification import account_history_global_financial_v1 as financial

    return journal.canonical(
        {
            "schema_version": FINANCIAL_STREAM_SCHEMA_V2,
            "original_stream_policy_sha256": POLICY_SHA256,
            "global_financial_policy_sha256": financial.POLICY_SHA256,
            "source": "every_verified_original_CURRENT_from_genesis_in_same_replay",
            "witnesses": "validate_original_bytes_never_book_as_CURRENT",
            "finalization": "original_pinned_head_and_checkpoint_then_global_finish",
            "component_receipts": "unchanged_bytes_bound_by_sha256_not_requalified",
            "checked_at": "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP",
            "missing_windows_seed_hwm_tail_and_native_owner": "unknown_unchanged",
            "maximum_composite_receipt_bytes": observed.MAX_RECEIPT_BYTES,
            "native_ownership_issued": False,
            "account_revision_published": False,
            "account_complete": False,
            "execution_authority": False,
        }
    )


def original_history_financial_stream_policy_sha256_v2():
    """Return the composite policy identity, with no source/financial authority."""
    return journal.digest(_financial_stream_policy_bytes_v2())


def _financial_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _financial_task_check(owner):
    current = _financial_task()
    if current is not owner:
        _deny("stream_financial_task_changed")
    if current is not None and current.cancelling():
        raise asyncio.CancelledError() from None


def _financial_setup(scope, checked_at, policy_pin):
    from app.trade_qualification import account_history_global_financial_v1 as financial

    scope_doc = partition._scope(scope)
    if type(checked_at) is not datetime or checked_at.tzinfo is not UTC:
        _deny("stream_financial_UTC_cutoff_required")
    policy_raw = _financial_stream_policy_bytes_v2()
    if type(policy_pin) is not str or policy_pin != journal.digest(policy_raw):
        _deny("stream_financial_policy_mismatch")
    # Take a new validated scope before consuming a caller-controlled iterable.
    # An external mutation cannot rebind this invocation to a different account.
    scope_snapshot = partition.HistoryPartitionScopeV1(*scope_doc)
    accumulator = financial.OriginalHistoryFinancialAccumulatorV1(
        scope=LedgerScope(
            environment=scope_snapshot.environment,
            account_id=scope_snapshot.account_id,
            settlement_currency=scope_snapshot.settlement_currency,
        ),
        expected_policy_sha256=financial.POLICY_SHA256,
    )
    return scope_snapshot, accumulator, policy_raw


def _finish_financial_stream_v2(
    replay,
    accumulator,
    scope,
    through,
    head,
    checkpoint_head,
    checked_at,
    position_source,
    policy_raw,
    owner,
):
    from app.trade_qualification import account_history_global_financial_v1 as financial

    _financial_task_check(owner)
    if type(replay) is not OriginalHistoryStreamReplayV1:
        _deny("stream_financial_original_replay_required")
    if policy_raw != _financial_stream_policy_bytes_v2():
        _deny("stream_financial_policy_changed")
    facts = accumulator.finish(
        expected_through=through,
        expected_head=head,
        checked_at=checked_at,
        position_source=position_source,
        expected_policy_sha256=financial.POLICY_SHA256,
    )
    if type(facts) is not financial.GlobalFinancialHistoryFactsV1:
        _deny("stream_financial_exact_facts_required")
    original = lifecycle._json(replay.receipt_json)
    operands = lifecycle._json(facts.receipt_json)
    scope_pin = journal.digest(journal.canonical(partition._scope(scope)))
    if (
        original.get("schema_version") != SCHEMA
        or original.get("policy_sha256") != POLICY_SHA256
        or original.get("scope_sha256") != scope_pin
        or original.get("through_sequence") != through
        or original.get("original_b3_head_sha256") != head
        or original.get("checkpoint_sha256") != checkpoint_head
        or original.get("full_original_genesis_sources_replayed") is not True
        or operands.get("schema_version") != "ctcc.global_original_financial_facts.v1"
        or operands.get("policy_sha256") != financial.POLICY_SHA256
        or operands.get("component_policy_sha256") != financial._component_policies()
        or operands.get("scope_sha256") != scope_pin
        or operands.get("through_sequence") != through
        or operands.get("index_head_sha256") != head
        or operands.get("checked_at") != checked_at.isoformat()
        or operands.get("checked_at_semantics")
        != "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP"
        or operands.get("original_stream_prefix_verified") is not False
        or any(
            value.get(name) is not False
            for value in (original, operands)
            for name in (
                "account_complete",
                "account_revision_published",
                "execution_authority",
            )
        )
        or any(value.get("admission") != "DENY" for value in (original, operands))
    ):
        _deny("stream_financial_component_binding_changed")
    missing = operands.get("missing_obligations")
    if (
        type(missing) is not list
        or len(missing) > MAX_BLOCKERS
        or any(
            type(value) is not str or not 1 <= len(value) <= MAX_BLOCKER_BYTES
            for value in missing
        )
        or len(set(missing)) != len(missing)
        or "original_stream_replay_not_bound" not in missing
        or "native_acquisition_not_owned" not in missing
    ):
        _deny("stream_financial_obligations_changed")
    source_available_at = operands.get("source_available_at")
    if type(source_available_at) is not str:
        _deny("stream_financial_source_availability_unknown")
    available = datetime.fromisoformat(source_available_at)
    if available.tzinfo is not UTC or available > checked_at:
        _deny("stream_financial_source_availability_unknown")
    output = {
        "schema_version": FINANCIAL_STREAM_SCHEMA_V2,
        "policy_sha256": journal.digest(policy_raw),
        "scope_sha256": scope_pin,
        "through_sequence": through,
        "original_b3_head_sha256": head,
        "checkpoint_sha256": checkpoint_head,
        "original_stream_policy_sha256": POLICY_SHA256,
        "original_stream_receipt_sha256": replay.receipt_sha256,
        "global_financial_policy_sha256": financial.POLICY_SHA256,
        "global_financial_receipt_sha256": journal.digest(facts.receipt_json),
        "native_position_archive_sha256": operands["native_position_archive_sha256"],
        "checked_at": checked_at.isoformat(),
        "checked_at_semantics": "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP",
        "source_available_at": source_available_at,
        "full_original_genesis_sources_replayed": True,
        "global_CURRENT_financial_facts_accumulated": True,
        "resolved_composite_obligations": ["original_stream_replay_not_bound"],
        "remaining_obligations": [
            value for value in missing if value != "original_stream_replay_not_bound"
        ],
        # Both component receipts remain byte-for-byte unchanged. In particular,
        # a partial financial reducer does not erase any older safety blocker.
        "original_stream_blocking_reasons": original["blocking_reasons"],
        "native_history_prefix_verified": False,
        "native_acquisition_owned": False,
        "account_complete": False,
        "account_revision_published": False,
        "portfolio_revision": None,
        "execution_authority": False,
        "admission": "DENY",
    }
    encoded = journal.canonical(output)
    if len(encoded) > observed.MAX_RECEIPT_BYTES:
        _deny("stream_financial_composite_receipt_byte_bound")
    _financial_task_check(owner)
    if policy_raw != _financial_stream_policy_bytes_v2():
        _deny("stream_financial_policy_changed")
    return OriginalHistoryFinancialStreamReplayV2(encoded, replay, facts)


def replay_original_history_financial_stream_v2(
    pages,
    *,
    scope,
    expected_through_sequence,
    expected_b3_head_sha256,
    expected_checkpoint_sha256,
    checked_at,
    expected_policy_sha256,
    position_source=None,
):
    """Join a real whole-prefix V1 replay with one global CURRENT accumulator.

    This pure API does not own acquisition, prove history tail/window finality,
    or publish an account revision. A standalone saved receipt is not an input.
    """
    accumulator = None
    owner = _financial_task()
    try:
        _financial_task_check(owner)
        actual_scope, accumulator, policy_raw = _financial_setup(
            scope, checked_at, expected_policy_sha256
        )
        replay = _replay(
            pages,
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            POLICY_SHA256,
            _financial=accumulator,
        )
        return _finish_financial_stream_v2(
            replay,
            accumulator,
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            checked_at,
            position_source,
            policy_raw,
            owner,
        )
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except OriginalHistoryStreamError:
        raise
    except Exception:  # noqa: BLE001 -- private raw-source errors stay private
        _deny("stream_financial_replay_failed")
    finally:
        if accumulator is not None:
            accumulator.cancel()


async def replay_original_history_financial_stream_async_v2(
    pages,
    *,
    scope,
    expected_through_sequence,
    expected_b3_head_sha256,
    expected_checkpoint_sha256,
    checked_at,
    expected_policy_sha256,
    position_source=None,
):
    """Same-task async variant; the native owner must still close its producer.

    The existing native wrapper's managed async-generator close and final
    catalog/session/lease checks remain necessary. No new lease is created.
    """
    accumulator = engine = None
    owner = _financial_task()
    try:
        _financial_task_check(owner)
        actual_scope, accumulator, policy_raw = _financial_setup(
            scope, checked_at, expected_policy_sha256
        )
        engine = _replay_engine(
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            POLICY_SHA256,
            _financial=accumulator,
        )
        next(engine)
        async for page in pages:
            _financial_task_check(owner)
            if type(page) is not OriginalHistoryPageV1:
                _deny("stream_exact_page_required")
            engine.send(page)
        replay = _finish_replay_engine(engine)
        return _finish_financial_stream_v2(
            replay,
            accumulator,
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            checked_at,
            position_source,
            policy_raw,
            owner,
        )
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except OriginalHistoryStreamError:
        raise
    except Exception:  # noqa: BLE001 -- private raw-source errors stay private
        _deny("stream_financial_replay_failed")
    finally:
        if accumulator is not None:
            accumulator.cancel()
        if engine is not None:
            engine.close()


# V3 is a separate policy/schema for original per-capture session lineage.
FINANCIAL_STREAM_SCHEMA_V3 = "ctcc.original_genesis_financial_stream.v3"


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryFinancialStreamReplayV3:
    """Immutable component bytes and their exact join; never an account issuer."""

    receipt_json: bytes
    original_replay: OriginalHistoryStreamReplayV1
    financial_facts: object

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def execution_authority(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _financial_stream_policy_bytes_v3():
    # The core imports V1 replay helpers. Import only after module loading so
    # adding this API does not introduce a circular-import initialization path.
    from app.trade_qualification import account_history_global_financial_v2 as financial

    return journal.canonical(
        {
            "schema_version": FINANCIAL_STREAM_SCHEMA_V3,
            "original_stream_policy_sha256": POLICY_SHA256,
            "global_financial_policy_sha256": financial.POLICY_SHA256,
            "source": "every_verified_original_CURRENT_from_genesis_in_same_replay",
            "witnesses": "validate_original_bytes_never_book_as_CURRENT",
            "finalization": "original_pinned_head_and_checkpoint_then_global_finish",
            "component_receipts": "unchanged_bytes_bound_by_sha256_not_requalified",
            "checked_at": "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP",
            "missing_windows_seed_hwm_tail_and_native_owner": "unknown_unchanged",
            "maximum_composite_receipt_bytes": observed.MAX_RECEIPT_BYTES,
            "native_ownership_issued": False,
            "account_revision_published": False,
            "account_complete": False,
            "execution_authority": False,
        }
    )


def original_history_financial_stream_policy_sha256_v3():
    """Return the composite policy identity, with no source/financial authority."""
    return journal.digest(_financial_stream_policy_bytes_v3())


def _financial_task_v3():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _financial_task_check_v3(owner):
    current = _financial_task_v3()
    if current is not owner:
        _deny("stream_financial_task_changed")
    if current is not None and current.cancelling():
        raise asyncio.CancelledError() from None


def _financial_setup_v3(scope, checked_at, policy_pin):
    from app.trade_qualification import account_history_global_financial_v2 as financial

    scope_doc = partition._scope(scope)
    if type(checked_at) is not datetime or checked_at.tzinfo is not UTC:
        _deny("stream_financial_UTC_cutoff_required")
    policy_raw = _financial_stream_policy_bytes_v3()
    if type(policy_pin) is not str or policy_pin != journal.digest(policy_raw):
        _deny("stream_financial_policy_mismatch")
    # Take a new validated scope before consuming a caller-controlled iterable.
    # An external mutation cannot rebind this invocation to a different account.
    scope_snapshot = partition.HistoryPartitionScopeV1(*scope_doc)
    accumulator = financial.OriginalHistoryFinancialAccumulatorV2(
        scope=LedgerScope(
            environment=scope_snapshot.environment,
            account_id=scope_snapshot.account_id,
            settlement_currency=scope_snapshot.settlement_currency,
        ),
        expected_policy_sha256=financial.POLICY_SHA256,
    )
    return scope_snapshot, accumulator, policy_raw


def _finish_financial_stream_v3(
    replay,
    accumulator,
    scope,
    through,
    head,
    checkpoint_head,
    checked_at,
    position_source,
    policy_raw,
    owner,
):
    from app.trade_qualification import account_history_global_financial_v2 as financial

    _financial_task_check_v3(owner)
    if type(replay) is not OriginalHistoryStreamReplayV1:
        _deny("stream_financial_original_replay_required")
    if policy_raw != _financial_stream_policy_bytes_v3():
        _deny("stream_financial_policy_changed")
    facts = accumulator.finish(
        expected_through=through,
        expected_head=head,
        checked_at=checked_at,
        position_source=position_source,
        expected_policy_sha256=financial.POLICY_SHA256,
    )
    if type(facts) is not financial.GlobalFinancialHistoryFactsV2:
        _deny("stream_financial_exact_facts_required")
    original = lifecycle._json(replay.receipt_json)
    operands = lifecycle._json(facts.receipt_json)
    scope_pin = journal.digest(journal.canonical(partition._scope(scope)))
    if (
        original.get("schema_version") != SCHEMA
        or original.get("policy_sha256") != POLICY_SHA256
        or original.get("scope_sha256") != scope_pin
        or original.get("through_sequence") != through
        or original.get("original_b3_head_sha256") != head
        or original.get("checkpoint_sha256") != checkpoint_head
        or original.get("full_original_genesis_sources_replayed") is not True
        or operands.get("schema_version") != "ctcc.global_original_financial_facts.v2"
        or operands.get("policy_sha256") != financial.POLICY_SHA256
        or operands.get("component_policy_sha256") != financial._component_policies()
        or operands.get("scope_sha256") != scope_pin
        or operands.get("through_sequence") != through
        or operands.get("index_head_sha256") != head
        or operands.get("checked_at") != checked_at.isoformat()
        or operands.get("checked_at_semantics")
        != "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP"
        or operands.get("original_stream_prefix_verified") is not False
        or any(
            value.get(name) is not False
            for value in (original, operands)
            for name in (
                "account_complete",
                "account_revision_published",
                "execution_authority",
            )
        )
        or any(value.get("admission") != "DENY" for value in (original, operands))
    ):
        _deny("stream_financial_component_binding_changed")
    missing = operands.get("missing_obligations")
    if (
        type(missing) is not list
        or len(missing) > MAX_BLOCKERS
        or any(
            type(value) is not str or not 1 <= len(value) <= MAX_BLOCKER_BYTES
            for value in missing
        )
        or len(set(missing)) != len(missing)
        or "original_stream_replay_not_bound" not in missing
        or "native_acquisition_not_owned" not in missing
    ):
        _deny("stream_financial_obligations_changed")
    source_available_at = operands.get("source_available_at")
    if type(source_available_at) is not str:
        _deny("stream_financial_source_availability_unknown")
    available = datetime.fromisoformat(source_available_at)
    if available.tzinfo is not UTC or available > checked_at:
        _deny("stream_financial_source_availability_unknown")
    output = {
        "schema_version": FINANCIAL_STREAM_SCHEMA_V3,
        "policy_sha256": journal.digest(policy_raw),
        "scope_sha256": scope_pin,
        "through_sequence": through,
        "original_b3_head_sha256": head,
        "checkpoint_sha256": checkpoint_head,
        "original_stream_policy_sha256": POLICY_SHA256,
        "original_stream_receipt_sha256": replay.receipt_sha256,
        "global_financial_policy_sha256": financial.POLICY_SHA256,
        "global_financial_receipt_sha256": journal.digest(facts.receipt_json),
        "native_position_archive_sha256": operands["native_position_archive_sha256"],
        "checked_at": checked_at.isoformat(),
        "checked_at_semantics": "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP",
        "source_available_at": source_available_at,
        "full_original_genesis_sources_replayed": True,
        "global_CURRENT_financial_facts_accumulated": True,
        "resolved_composite_obligations": ["original_stream_replay_not_bound"],
        "remaining_obligations": [
            value for value in missing if value != "original_stream_replay_not_bound"
        ],
        # Both component receipts remain byte-for-byte unchanged. In particular,
        # a partial financial reducer does not erase any older safety blocker.
        "original_stream_blocking_reasons": original["blocking_reasons"],
        "native_history_prefix_verified": False,
        "native_acquisition_owned": False,
        "account_complete": False,
        "account_revision_published": False,
        "portfolio_revision": None,
        "execution_authority": False,
        "admission": "DENY",
    }
    encoded = journal.canonical(output)
    if len(encoded) > observed.MAX_RECEIPT_BYTES:
        _deny("stream_financial_composite_receipt_byte_bound")
    _financial_task_check_v3(owner)
    if policy_raw != _financial_stream_policy_bytes_v3():
        _deny("stream_financial_policy_changed")
    return OriginalHistoryFinancialStreamReplayV3(encoded, replay, facts)


def replay_original_history_financial_stream_v3(
    pages,
    *,
    scope,
    expected_through_sequence,
    expected_b3_head_sha256,
    expected_checkpoint_sha256,
    checked_at,
    expected_policy_sha256,
    position_source=None,
):
    """Join a real whole-prefix V1 replay with one global CURRENT accumulator.

    This pure API does not own acquisition, prove history tail/window finality,
    or publish an account revision. A standalone saved receipt is not an input.
    """
    accumulator = None
    owner = _financial_task_v3()
    try:
        _financial_task_check_v3(owner)
        actual_scope, accumulator, policy_raw = _financial_setup_v3(
            scope, checked_at, expected_policy_sha256
        )
        replay = _replay(
            pages,
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            POLICY_SHA256,
            _financial_v2=accumulator,
        )
        return _finish_financial_stream_v3(
            replay,
            accumulator,
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            checked_at,
            position_source,
            policy_raw,
            owner,
        )
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except OriginalHistoryStreamError:
        raise
    except Exception:  # noqa: BLE001 -- private raw-source errors stay private
        _deny("stream_financial_replay_failed")
    finally:
        if accumulator is not None:
            accumulator.cancel()


async def replay_original_history_financial_stream_async_v3(
    pages,
    *,
    scope,
    expected_through_sequence,
    expected_b3_head_sha256,
    expected_checkpoint_sha256,
    checked_at,
    expected_policy_sha256,
    position_source=None,
):
    """Same-task async variant; the native owner must still close its producer.

    The existing native wrapper's managed async-generator close and final
    catalog/session/lease checks remain necessary. No new lease is created.
    """
    accumulator = engine = None
    owner = _financial_task_v3()
    try:
        _financial_task_check_v3(owner)
        actual_scope, accumulator, policy_raw = _financial_setup_v3(
            scope, checked_at, expected_policy_sha256
        )
        engine = _replay_engine(
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            POLICY_SHA256,
            _financial_v2=accumulator,
        )
        next(engine)
        async for page in pages:
            _financial_task_check_v3(owner)
            if type(page) is not OriginalHistoryPageV1:
                _deny("stream_exact_page_required")
            engine.send(page)
        replay = _finish_replay_engine(engine)
        return _finish_financial_stream_v3(
            replay,
            accumulator,
            actual_scope,
            expected_through_sequence,
            expected_b3_head_sha256,
            expected_checkpoint_sha256,
            checked_at,
            position_source,
            policy_raw,
            owner,
        )
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except OriginalHistoryStreamError:
        raise
    except Exception:  # noqa: BLE001 -- private raw-source errors stay private
        _deny("stream_financial_replay_failed")
    finally:
        if accumulator is not None:
            accumulator.cancel()
        if engine is not None:
            engine.close()
