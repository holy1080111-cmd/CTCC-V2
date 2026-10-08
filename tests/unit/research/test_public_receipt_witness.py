"""Witness replay rejects rollback even when individual row hashes match."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
    WitnessRevision,
    verify_witness_chain,
)
from app.mie.validation.public_checkpoint_service import (
    _ISSUER,
    ControlledPublicCheckpointWitness,
)
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_receipt_storage import PublicJournalCheckpointV1
from tests.unit.research.public_receipt_fixtures import (
    SyntheticCapture,
    storage_fixture_capture,
)

GENESIS = "a" * 64
JOURNAL_ID = "b" * 32
PLAN = "c" * 64
OPERATION = "d" * 32


def row(revision, transition, state, checkpoint, previous=None, outcome=None):
    checkpoint_json = checkpoint.canonical_bytes().decode("utf-8")
    fields = (
        GENESIS,
        str(revision),
        JOURNAL_ID,
        "1",
        "2",
        transition,
        state,
        "" if revision == 0 else OPERATION,
        "" if revision == 0 else PLAN,
        outcome or "",
        previous or "",
        hashlib.sha256(checkpoint_json.encode()).hexdigest(),
        checkpoint_json,
    )
    record_sha = hashlib.sha256("\x1f".join(fields).encode()).hexdigest()
    return SimpleNamespace(
        journal_key=GENESIS,
        revision=revision,
        journal_id=JOURNAL_ID,
        root_device=1,
        root_inode=2,
        transition=transition,
        state=state,
        operation_id=None if revision == 0 else OPERATION,
        plan_sha256=None if revision == 0 else PLAN,
        attempt_outcome=outcome,
        previous_record_sha256=previous,
        checkpoint_json=checkpoint_json,
        checkpoint_sha256=fields[-2],
        record_sha256=record_sha,
    )


def chain():
    initial = PublicJournalCheckpointV1(
        genesis_sha256=GENESIS,
        root_device=1,
        root_inode=2,
        sequence=0,
        head_sha256=GENESIS,
        attempt_sequence=0,
        attempt_head_sha256=GENESIS,
    )
    anchored_attempt = initial.model_copy(
        update={"attempt_sequence": 1, "attempt_head_sha256": "e" * 64}
    )
    anchored_capture = anchored_attempt.model_copy(
        update={"sequence": 1, "head_sha256": "f" * 64}
    )
    genesis = row(0, "initialize", "idle", initial)
    opened = row(1, "open_attempt", "open", initial, genesis.record_sha256)
    attempt = row(
        2,
        "append_attempt",
        "attempt_anchored",
        anchored_attempt,
        opened.record_sha256,
        "completed_collection",
    )
    capture = row(
        3,
        "append_capture",
        "idle",
        anchored_capture,
        attempt.record_sha256,
        "completed_collection",
    )
    return genesis, opened, attempt, capture


def test_complete_witness_chain_binds_every_phase():
    items = chain()
    latest = verify_witness_chain(items)
    assert latest.revision == 3
    assert latest.checkpoint.sequence == 1
    assert latest.checkpoint.attempt_sequence == 1
    assert latest.execution_authority is False


def test_committed_observation_samples_clock_after_full_chain_read(monkeypatch):
    steps = []
    sample = datetime(2026, 10, 9, tzinfo=UTC)

    class Result:
        def __init__(self, value):
            self.value = value

        def all(self):
            return self.value

        def scalar_one(self):
            return self.value

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, query, _parameters=None):
            statement = str(query)
            if statement == "SET TRANSACTION READ ONLY":
                steps.append("read_only")
                return Result(None)
            if "public_receipt_witness_read" in statement:
                steps.append("read_chain")
                return Result(chain())
            if statement == "SELECT clock_timestamp()":
                steps.append("sample_clock")
                return Result(sample)
            raise AssertionError("unexpected SQL")

    async def guard(_session):
        steps.append("role_guard")

    monkeypatch.setattr(
        PublicReceiptWitnessRepository, "_role_guard", staticmethod(guard)
    )
    repository = PublicReceiptWitnessRepository(lambda: Session())
    observed = asyncio.run(repository.observe_committed_revision(GENESIS, 3))
    assert observed.revision == verify_witness_chain(chain())
    assert observed.observed_at == sample
    assert steps == ["read_only", "role_guard", "read_chain", "sample_clock"]


@pytest.mark.parametrize("missing", (0, 1, 2))
def test_missing_or_reordered_witness_revision_denied(missing):
    items = chain()
    with pytest.raises(PublicWitnessError):
        verify_witness_chain(items[:missing] + items[missing + 1 :])


def test_coordinated_checkpoint_json_edit_without_record_rehash_denied():
    items = chain()
    changed = replace_dataclass(items[-1], checkpoint_json="{}")
    with pytest.raises(PublicWitnessError):
        verify_witness_chain((*items[:-1], changed))


def test_identical_record_hash_cannot_mask_illegal_stage():
    items = chain()
    # A standalone record hash may be recomputed, but the state transition
    # must still match the prior attempt and capture counters.
    forged = row(
        3,
        "append_capture",
        "idle",
        PublicJournalCheckpointV1.model_validate_json(items[2].checkpoint_json),
        items[2].record_sha256,
        "completed_collection",
    )
    with pytest.raises(PublicWitnessError, match="witness_transition_invalid"):
        verify_witness_chain((*items[:-1], forged))


def replace_dataclass(value, **changes):
    return SimpleNamespace(**{**vars(value), **changes})


@dataclass
class FakeResult:
    role: object

    def one_or_none(self):
        return self.role


class FakeSession:
    async def execute(self, _query):
        return FakeResult(
            SimpleNamespace(
                direct_login=True,
                privileged=True,
                owner_member=False,
                can_read=True,
                can_append=True,
                direct_table_access=False,
                can_create=False,
            )
        )


def test_superuser_witness_connection_denied_before_any_write():
    with pytest.raises(PublicWitnessError, match="restricted_witness_role_required"):
        asyncio.run(PublicReceiptWitnessRepository._role_guard(FakeSession()))


def test_duplicate_capture_does_not_advance_witness(monkeypatch, tmp_path):
    fixture = SyntheticCapture(monkeypatch, rows=2)
    packet, files = fixture.collect()
    owned = storage_fixture_capture(packet, files)
    monkeypatch.setattr(storage, "native_stamp", fixture.stamp)
    root = tmp_path / "public-journal"
    root.mkdir()
    journal = storage.initialize_public_receipt_journal(root)
    journal._publish_owned(owned)
    before = journal.checkpoint
    duplicate = journal._publish_owned(owned)
    assert duplicate.checkpoint == before
    pending = WitnessRevision(
        journal_key=before.genesis_sha256,
        revision=2,
        journal_id=JOURNAL_ID,
        root_device=before.root_device,
        root_inode=before.root_inode,
        transition="append_attempt",
        state="attempt_anchored",
        operation_id=OPERATION,
        plan_sha256=PLAN,
        attempt_outcome="completed_collection",
        previous_record_sha256="f" * 64,
        checkpoint=before,
        checkpoint_sha256=before.canonical_sha256(),
        record_sha256="e" * 64,
    )
    witness = ControlledPublicCheckpointWitness(
        _ISSUER,
        journal,
        PublicReceiptWitnessRepository(None),
        JOURNAL_ID,
        pending,
    )
    with pytest.raises(PublicWitnessError, match="witness_capture_state_invalid"):
        asyncio.run(witness.after_capture(journal))
    assert witness.before_capture_sequence == before.sequence
    assert journal.checkpoint == before


def test_unknown_witness_commit_preserves_published_capture(monkeypatch, tmp_path):
    fixture = SyntheticCapture(monkeypatch, rows=2)
    packet, files = fixture.collect()
    owned = storage_fixture_capture(packet, files)
    monkeypatch.setattr(storage, "native_stamp", fixture.stamp)
    root = tmp_path / "public-journal"
    root.mkdir()
    journal = storage.initialize_public_receipt_journal(root)
    before = journal.checkpoint
    published = journal._publish_owned(owned)
    pending = WitnessRevision(
        journal_key=before.genesis_sha256,
        revision=2,
        journal_id=JOURNAL_ID,
        root_device=before.root_device,
        root_inode=before.root_inode,
        transition="append_attempt",
        state="attempt_anchored",
        operation_id=OPERATION,
        plan_sha256=PLAN,
        attempt_outcome="completed_collection",
        previous_record_sha256="f" * 64,
        checkpoint=before,
        checkpoint_sha256=before.canonical_sha256(),
        record_sha256="e" * 64,
    )
    repository = PublicReceiptWitnessRepository(None)

    async def unknown_commit(*_args, **_kwargs):
        raise PublicWitnessError("witness_commit_uncertain")

    monkeypatch.setattr(PublicReceiptWitnessRepository, "append", unknown_commit)
    witness = ControlledPublicCheckpointWitness(
        _ISSUER, journal, repository, JOURNAL_ID, pending
    )
    with pytest.raises(PublicWitnessError, match="witness_commit_uncertain"):
        asyncio.run(witness.after_capture(journal))
    assert journal.checkpoint == published.checkpoint
    assert witness.before_capture_sequence == before.sequence
    assert (root / "capture-00000001" / "entry.json").is_file()
    assert (root / "capture-00000001" / "page-000.raw").read_bytes() == files[
        "page-000.raw"
    ]
