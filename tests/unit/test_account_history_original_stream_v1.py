"""Prepared synthetic raw B1/B3/partition replay; no native/TLS/PG acceptance."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
    _membership,
)
from app.domain import account_history_partition_v1 as partition
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_history_source_v1 as native_source
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_history_partition_v1 import original_linkage
from tests.unit.test_account_history_query_verifier_contracts import rewrite
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_account_native_proof import synthetic_tls_chain
from tests.unit.test_qualification_account_capture import NOW, UID, ms, row
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_v4 import script

SCOPE = partition.HistoryPartitionScopeV1("demo", UID, "USDT")
SCOPE_DOC = ["demo", UID, "USDT"]
LEDGER_SCOPE = LedgerScope(account_id=UID, settlement_currency="USDT")
WINDOW = observed.ExecutionWindow(NOW - timedelta(days=7), NOW - timedelta(seconds=600))


async def original_chain(monkeypatch, *, fee="-0.1"):
    session, harness, _, arguments, events = setup(monkeypatch, v4=True)
    pages = source_pages()
    sample = row(
        "fills_history",
        fillTime=ms(NOW - timedelta(seconds=900)),
        fillPnl="-3",
        fee=fee,
    )
    pages["fills_history_swap"] = [[sample], []]
    harness.script = script(pages=pages, source=pages)
    await bootstrap.collect_bootstrap_recorded(session, **arguments)
    harness.assert_closed()
    # Explicit wire-level synthetic TLS annotation, never native acceptance.
    return synthetic_tls_chain(tuple(events))


def capture_copy(chain, number):
    def change(record, raw, packet):
        record["capture_id"] = f"{number:032x}"

    return tuple(rewrite(chain, change))


def originals(chain, count=33, *, variant=None, witness_at=None, native_at=()):
    """Construct original B3 wire receipts through actual single/witness replay.

    Copies are labelled synthetic source chains with distinct original capture
    IDs. Their packet/raw bytes are parsed by the real B1/query verifiers.
    """
    frames, documents, checkpoints = [], [], []
    cumulative = {"source": 0, "coverage": 0, "finding": 0}
    previous = anchor_sha = None
    for number in range(1, count + 1):
        selected = variant if number == count and variant is not None else chain
        current = capture_copy(selected, number)
        reference = observed.source_reference(current)
        single = observed.replay_observation_index(
            ((current, reference),), scope=LEDGER_SCOPE, window=WINDOW
        )
        facts, coverage = _membership(single)
        witness_numbers = () if witness_at is None else witness_at.get(number, ())
        witnesses = tuple(
            stream.OriginalHistoryWitnessV1(prior, frames[prior - 1].chain)
            for prior in witness_numbers
        )
        prior_sources = tuple(
            (witness.chain, observed.source_reference(witness.chain))
            for witness in witnesses
        )
        receipt = json.loads(
            observed._replay(
                (*prior_sources, (current, reference)),
                LEDGER_SCOPE,
                WINDOW,
                observed.POLICY_SHA256,
                adjacent=False,
            ).receipt_json
        )
        memberships = {
            "source": facts,
            "coverage": coverage,
            "finding": receipt["findings"],
        }
        for name, values in memberships.items():
            cumulative[name] += len(values)
        anchor = {
            "schema_version": "ctcc.account_observation_continuation.v1",
            "policy_sha256": observed.POLICY_SHA256,
            "scope_sha256": journal.digest(journal.canonical(SCOPE_DOC)),
            "through_sequence": number,
            "previous_sequence": number - 1,
            "previous_event_sha256": previous,
            "previous_anchor_sha256": anchor_sha,
            "source_facts_through_sequence": cumulative["source"],
            "coverage_through_sequence": cumulative["coverage"],
            "unresolved_findings_through_sequence": cumulative["finding"],
            "current_source_membership_sha256": journal.digest(
                journal.canonical(facts)
            ),
            "current_coverage_membership_sha256": journal.digest(
                journal.canonical(coverage)
            ),
            "verified_witnesses": [
                {
                    "sequence": prior,
                    "event_sha256": frames[prior - 1].batch.event_sha256,
                    "source_membership_sha256": json.loads(documents[prior - 1])[
                        "source_membership_sha256"
                    ],
                    "coverage_membership_sha256": json.loads(documents[prior - 1])[
                        "coverage_membership_sha256"
                    ],
                }
                for prior in witness_numbers
            ],
            "required_proof_budget_exceeded": False,
        }
        receipt["continuation_anchor"] = anchor
        raw_receipt = journal.canonical(receipt)
        document = {
            "schema_version": "ctcc.account_observation_batch.v1",
            "scope_sha256": anchor["scope_sha256"],
            "sequence": number,
            "previous_sha256": previous,
            "reference": observed.reference_document(reference),
            "window": observed.window_document(WINDOW),
            "policy_sha256": observed.POLICY_SHA256,
            "receipt_sha256": journal.digest(raw_receipt),
            "anchor_json": journal.canonical(anchor).decode(),
            "anchor_sha256": journal.digest(journal.canonical(anchor)),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        for name, values in memberships.items():
            document[name + "_membership_sha256"] = journal.digest(
                journal.canonical(values)
            )
            document[name + "_membership_count"] = len(values)
        raw = journal.canonical(document)
        batch = ObservationBatchReadback(
            number, journal.digest(raw), raw, observed.ObservationReplay(raw_receipt)
        )
        registration = None
        if number in native_at:
            linkage = original_linkage(raw)
            linkage["scope"] = SCOPE_DOC
            registration = partition.derive_native_member_registration_v1(
                scope=SCOPE,
                b3_document_raw=raw,
                native_linkage_raw=journal.canonical(linkage),
                expected_b3_policy_sha256=observed.POLICY_SHA256,
                expected_b3_event_sha256=batch.event_sha256,
            ).record_json
        frame = stream.OriginalHistoryFrameV1(current, batch, witnesses, registration)
        frames.append(frame)
        documents.append(raw)
        if number % 32 == 0:
            checked = partition.build_partition_checkpoint_v1(
                scope=SCOPE,
                b3_documents=tuple(documents[-32:]),
                native_registrations=tuple(
                    item.native_registration for item in frames[-32:]
                ),
                previous_checkpoints=tuple(checkpoints),
                expected_b3_policy_sha256=observed.POLICY_SHA256,
                expected_b3_head_sha256=batch.event_sha256,
            )
            checkpoints.append(checked.record_json)
            frames[-1] = replace(frame, checkpoint=checked.record_json)
        previous, anchor_sha = batch.event_sha256, document["anchor_sha256"]
    return tuple(frames)


def pages(frames, width=8):
    previous = None
    for offset in range(0, len(frames), width):
        page = stream.OriginalHistoryPageV1(
            offset // width, previous, frames[offset : offset + width]
        )
        previous = stream.original_history_page_sha256_v1(page)
        yield page


def replay(frames, *, supplied_pages=None, **changes):
    last_checkpoint = next(
        (
            frame.checkpoint
            for frame in reversed(frames)
            if frame.checkpoint is not None
        ),
        None,
    )
    arguments = {
        "scope": SCOPE,
        "expected_through_sequence": len(frames),
        "expected_b3_head_sha256": frames[-1].batch.event_sha256,
        "expected_checkpoint_sha256": None
        if last_checkpoint is None
        else journal.digest(last_checkpoint),
        **changes,
    }
    return stream.replay_original_history_stream_v1(
        pages(frames) if supplied_pages is None else supplied_pages, **arguments
    )


async def test_single_source_membership_and_receipt_share_one_exact_replay(
    monkeypatch,
):
    frames = originals(await original_chain(monkeypatch), count=2, witness_at={2: (1,)})
    original_replay = observed._replay
    chain = frames[0].chain
    reference = observed.source_reference(chain)
    arguments = (((chain, reference),), LEDGER_SCOPE, WINDOW, observed.POLICY_SHA256)
    assert (
        original_replay(*arguments).receipt_json
        == original_replay(*arguments, adjacent=False).receipt_json
    )

    calls = []

    def count_sources(sources, *args, **kwargs):
        calls.append(len(sources))
        return original_replay(sources, *args, **kwargs)

    monkeypatch.setattr(observed, "_replay", count_sources)
    result = replay(frames)
    assert calls == [1, 1, 2]
    assert json.loads(result.receipt_json)["full_original_genesis_sources_replayed"]


@pytest.mark.asyncio
async def test_full_original32_then33_replays_every_raw_source_and_preserves_genesis(
    monkeypatch,
):
    chain = await original_chain(monkeypatch)
    frames = originals(chain, native_at=(32, 33))
    original_bytes = tuple(frame.batch.document_json for frame in frames)
    called = []
    real_source = stream._source

    def count_source(actual, scope):
        called.append(observed.source_reference(actual).capture_id)
        return real_source(actual, scope)

    monkeypatch.setattr(stream, "_source", count_source)
    result = replay(frames)
    value = json.loads(result.receipt_json)
    assert called == [f"{number:032x}" for number in range(1, 34)]
    assert value["through_sequence"] == 33
    assert value["original_partition_count"] == 1
    assert value["active_partition_member_count"] == 1
    assert value["native_registration_missing_count"] == 31
    assert value["full_original_genesis_sources_replayed"] is True
    assert value["observed_duplicate_row_count"] >= 32
    assert tuple(frame.batch.document_json for frame in frames) == original_bytes
    # Existing V1 tuple/proof contracts do not gain capture33 eligibility.
    with pytest.raises(
        lifecycle.AccountLifecycleHistoryError, match="population_bound"
    ):
        lifecycle.source_set_sha256(
            tuple(
                (frame.chain, observed.source_reference(frame.chain))
                for frame in frames
            )
        )
    assert json.loads(replay(frames).receipt_json) == value
    assert value["admission"] == "DENY"
    assert value["current_history_tail_finality"] is value["daily_net_pnl"] is None
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["all_observation_comparisons_complete"] is False
    with pytest.raises(native_source.NativeHistorySourceError):
        native_source.consume_owned_native_history_source_facts_v1(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["missing32", "reorder32", "start2", "extra34", "wrong_head", "wrong_checkpoint"],
)
async def test_no_missing_reordered_new_genesis_or_changed_head_is_admitted(
    monkeypatch, change
):
    frames = originals(await original_chain(monkeypatch), count=34)
    selected, kwargs = frames[:33], {}
    if change == "missing32":
        selected = (*selected[:31], selected[32])
    elif change == "reorder32":
        selected = (*selected[:31], selected[32], selected[31])
    elif change == "start2":
        selected = selected[1:]
    elif change == "extra34":
        kwargs["supplied_pages"] = pages(frames)
    elif change == "wrong_head":
        kwargs["expected_b3_head_sha256"] = "f" * 64
    else:
        kwargs["expected_checkpoint_sha256"] = "f" * 64
    with pytest.raises(stream.OriginalHistoryStreamError):
        replay(selected, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "different", "misplaced"])
async def test_original32_checkpoint_is_exact_and_never_substitutes_raw_sources(
    monkeypatch, change
):
    frames = list(originals(await original_chain(monkeypatch)))
    original = frames[31].checkpoint
    if change == "missing":
        frames[31] = replace(frames[31], checkpoint=None)
    elif change == "different":
        value = json.loads(original)
        value["missing_native_registrations_through_sequence"] = 0
        frames[31] = replace(frames[31], checkpoint=journal.canonical(value))
    else:
        frames[32] = replace(frames[32], checkpoint=original)
    with pytest.raises(stream.OriginalHistoryStreamError):
        replay(tuple(frames))


@pytest.mark.asyncio
async def test_original_witness_reopens_before_boundary_and_cross_capture_conflicts_remain_unknown(
    monkeypatch,
):
    before = await original_chain(monkeypatch)
    changed = await original_chain(monkeypatch, fee="-0.2")
    frames = originals(before, variant=changed, witness_at={33: (1,)})
    value = json.loads(replay(frames).receipt_json)
    assert value["observed_conflicting_variant_count"] >= 1
    assert "original_cross_capture_row_conflict" in value["blocking_reasons"]
    assert (
        value["cumulative_original_membership_counts"][
            "unresolved_findings_through_sequence"
        ]
        > 0
    )
    assert value["daily_net_pnl"] is value["weekly_net_pnl"] is None
    last = frames[-1]
    with pytest.raises(stream.OriginalHistoryStreamError, match="witness"):
        replay((*frames[:-1], replace(last, witnesses=())))
    forward = stream.OriginalHistoryWitnessV1(33, last.chain)
    with pytest.raises(stream.OriginalHistoryStreamError, match="witness"):
        replay((*frames[:-1], replace(last, witnesses=(forward,))))


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [1, 33])
async def test_original_raw_mutation_is_refused_despite_checkpoint_or_recomputed_page_hash(
    monkeypatch, number
):
    frames = originals(await original_chain(monkeypatch))
    selected = frames[number - 1]
    points = list(selected.chain)
    offset = next(index for index, point in enumerate(points) if point.event.raw_body)
    point = points[offset]
    # Deliberately bypass construction to model later mutation of a previously
    # valid immutable wrapper. Replay must revalidate rather than trust type.
    event = object.__new__(journal._JournalEvent)
    object.__setattr__(event, "event_json", point.event.event_json)
    object.__setattr__(event, "raw_body", point.event.raw_body + b" ")
    object.__setattr__(event, "packet_payload", point.event.packet_payload)
    corrupted = object.__new__(journal.JournalReadback)
    object.__setattr__(corrupted, "event", event)
    object.__setattr__(corrupted, "db_recorded_at", point.db_recorded_at)
    object.__setattr__(corrupted, "readback_at", point.readback_at)
    points[offset] = corrupted
    changed = (
        *frames[: number - 1],
        replace(selected, chain=tuple(points)),
        *frames[number:],
    )
    with pytest.raises(stream.OriginalHistoryStreamError):
        replay(changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["repeat", "missing", "previous"])
async def test_page_chain_cannot_hide_repeated_or_missing_original_pages(
    monkeypatch, change
):
    frames = originals(await original_chain(monkeypatch))
    supplied = list(pages(frames))
    if change == "repeat":
        supplied.insert(1, supplied[0])
    elif change == "missing":
        supplied.pop(1)
    else:
        supplied[1] = replace(supplied[1], previous_page_sha256="f" * 64)
    with pytest.raises(stream.OriginalHistoryStreamError, match="page"):
        replay(frames, supplied_pages=iter(supplied))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bound", ["MAX_FRAME_BYTES", "MAX_PAGE_BYTES", "MAX_INDEX_BYTES"]
)
async def test_each_byte_budget_denies_instead_of_truncating(monkeypatch, bound):
    frames = originals(await original_chain(monkeypatch), count=2)
    monkeypatch.setattr(stream, bound, 1)
    with pytest.raises(stream.OriginalHistoryStreamError, match="byte_bound"):
        replay(frames)


@pytest.mark.asyncio
async def test_stream_failure_after_first_page_returns_no_partial_or_private_error(
    monkeypatch,
):
    frames = originals(await original_chain(monkeypatch), count=9)
    first = next(pages(frames))

    def interrupted():
        yield first
        raise RuntimeError("declared-synthetic-private-source-error")

    with pytest.raises(stream.OriginalHistoryStreamError) as result:
        replay(frames, supplied_pages=interrupted())
    assert str(result.value) == "stream_original_replay_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        ["declared_synthetic_private_value"],
        ["x" * 129],
        [f"synthetic_code_{number:03d}" for number in range(257)],
    ],
)
async def test_rehashed_original_blockers_cannot_bypass_retained_state_budget(
    monkeypatch, extra
):
    frame = originals(await original_chain(monkeypatch), count=1)[0]
    receipt = json.loads(frame.batch.replay.receipt_json)
    receipt["blocking_reasons"] = sorted(set(receipt["blocking_reasons"]) | set(extra))
    raw_receipt = journal.canonical(receipt)
    document = json.loads(frame.batch.document_json)
    document["receipt_sha256"] = journal.digest(raw_receipt)
    raw_document = journal.canonical(document)
    batch = ObservationBatchReadback(
        1,
        journal.digest(raw_document),
        raw_document,
        observed.ObservationReplay(raw_receipt),
    )
    with pytest.raises(stream.OriginalHistoryStreamError, match="blocker"):
        replay((replace(frame, batch=batch),))


@pytest.mark.asyncio
async def test_native_linkage_identity_cannot_be_reused_across_original_partition_boundary(
    monkeypatch,
):
    frames = originals(await original_chain(monkeypatch), native_at=(32, 33))
    first = json.loads(
        json.loads(frames[31].native_registration)["native_linkage_json"]
    )
    linkage = json.loads(
        json.loads(frames[32].native_registration)["native_linkage_json"]
    )
    linkage["custody_bundle_id"] = first["custody_bundle_id"]
    registration = partition.derive_native_member_registration_v1(
        scope=SCOPE,
        b3_document_raw=frames[32].batch.document_json,
        native_linkage_raw=journal.canonical(linkage),
        expected_b3_policy_sha256=observed.POLICY_SHA256,
        expected_b3_event_sha256=frames[32].batch.event_sha256,
    ).record_json
    with pytest.raises(
        stream.OriginalHistoryStreamError, match="native_identity_conflict"
    ):
        replay((*frames[:32], replace(frames[32], native_registration=registration)))
