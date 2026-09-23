"""Memory-only crash-probe wiring. No process kill, PostgreSQL or real source."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification.account_capture_journal import (
    JournalReadback,
    checked_event,
)
from app.trade_qualification.reservations import LedgerScope
from scripts import account_capture_durability_probe as probe


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        self.now += timedelta(milliseconds=1)
        return self.now


def setup(monkeypatch):
    clock, rows = Clock(), {}
    scope = LedgerScope(
        environment="demo",
        account_id="987654321000000000004",
        settlement_currency="USDT",
    )
    checkpoint = SimpleNamespace(state=("synthetic_unknown",), state_sha256="a" * 64)

    async def read_checkpoint(self, scope):
        return checkpoint

    async def append(self, scope, event):
        record = checked_event(event)
        chain = rows.setdefault(record["capture_id"], [])
        assert record["sequence"] == len(chain) + 1
        assert record["previous_sha256"] == (
            probe.digest(chain[-1].event.event_json) if chain else None
        )
        receipt = JournalReadback(event, clock(), clock())
        chain.append(receipt)
        return receipt

    async def read_chain(self, scope, capture_id):
        return tuple(rows[capture_id])

    monkeypatch.setattr(
        QualificationLedgerRepository, "read_bootstrap_checkpoint", read_checkpoint
    )
    monkeypatch.setattr(AccountCaptureJournalRepository, "_append", append)
    monkeypatch.setattr(AccountCaptureJournalRepository, "read_chain", read_chain)
    return scope, clock, rows


@pytest.mark.asyncio
async def test_seed_keeps_partial_buffer_alive_and_recovery_only_records_unknown(
    monkeypatch,
):
    scope, clock, rows = setup(monkeypatch)
    seeded = await probe.seed_account_captures(None, scope=scope, clock=clock)
    probe.confirm_ready_account_captures(seeded)
    prefix_id = seeded.records[1]["capture_id"]
    before = tuple(rows[prefix_id])
    assert bytes(seeded.owners[1].current["body"]) == probe._PARTIAL_RAW
    assert not any(item.event.raw_body is not None for item in before)
    clock.now += timedelta(seconds=30)  # Explicit synthetic test clock only.
    await probe.verify_account_captures(None, seeded.records, scope=scope, clock=clock)
    assert tuple(rows[prefix_id][:-1]) == before
    recovered = checked_event(rows[prefix_id][-1].event)
    assert recovered["outcome"] == "interrupted_owner_unknown"
    assert recovered["data"]["missing_raw"][0]["additional_unobserved_bytes"] is None
    with pytest.raises(RuntimeError, match="durable_prefix_changed"):
        await probe.verify_account_captures(
            None, seeded.records, scope=scope, clock=clock
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "missing_event",
        "changed_db_time",
        "wrong_marker_hash",
        "wrong_count",
        "duplicate_capture",
    ],
)
async def test_restart_rejects_changed_or_missing_evidence(monkeypatch, damage):
    scope, clock, rows = setup(monkeypatch)
    seeded = await probe.seed_account_captures(None, scope=scope, clock=clock)
    target = seeded.records[0]
    if damage == "missing_event":
        rows[target["capture_id"]].pop()
    elif damage == "changed_db_time":
        last = rows[target["capture_id"]][-1]
        rows[target["capture_id"]][-1] = JournalReadback(
            last.event, last.db_recorded_at + timedelta(microseconds=1), clock()
        )
    elif damage == "wrong_marker_hash":
        target["chain_sha256"] = "f" * 64
    elif damage == "wrong_count":
        target["events"] += 1
    else:
        seeded.records[1]["capture_id"] = target["capture_id"]
    with pytest.raises(RuntimeError):
        await probe.verify_account_captures(
            None, seeded.records, scope=scope, clock=clock
        )


@pytest.mark.asyncio
async def test_marker_cannot_claim_a_buffer_that_is_no_longer_owned(monkeypatch):
    scope, clock, _ = setup(monkeypatch)
    seeded = await probe.seed_account_captures(None, scope=scope, clock=clock)
    seeded.owners[1].current["body"].clear()
    with pytest.raises(RuntimeError, match="owner_changed_before_publish"):
        probe.confirm_ready_account_captures(seeded)


@pytest.mark.asyncio
async def test_failed_capture_raw_must_survive_even_when_prefix_is_intact(monkeypatch):
    scope, clock, rows = setup(monkeypatch)
    seeded = await probe.seed_account_captures(None, scope=scope, clock=clock)
    target = seeded.records[0]
    chain = rows[target["capture_id"]]
    raw = next(item for item in chain if item.event.raw_body is not None)
    object.__setattr__(raw.event, "raw_body", b"changed bytes")
    with pytest.raises(RuntimeError, match="durable_prefix_changed"):
        await probe.verify_account_captures(
            None, seeded.records, scope=scope, clock=clock
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["unknown_tail", "db_recorded_at"])
async def test_final_independent_readback_must_match_new_recovery_event(
    monkeypatch, damage
):
    scope, clock, rows = setup(monkeypatch)
    seeded = await probe.seed_account_captures(None, scope=scope, clock=clock)
    clock.now += timedelta(seconds=30)

    async def read_chain(self, scope, capture_id):
        chain = tuple(rows[capture_id])
        last = chain[-1]
        document = checked_event(last.event)
        if document["kind"] == "recovery":
            if damage == "unknown_tail":
                document["data"]["missing_raw"][0]["additional_unobserved_bytes"] = 123
                changed = journal._JournalEvent(
                    journal._ISSUER, journal.canonical(document)
                )
                replacement = JournalReadback(changed, last.db_recorded_at, clock())
            else:
                replacement = JournalReadback(
                    last.event, last.db_recorded_at + timedelta(microseconds=1), clock()
                )
            return (*chain[:-1], replacement)
        return chain

    monkeypatch.setattr(AccountCaptureJournalRepository, "read_chain", read_chain)
    with pytest.raises(RuntimeError, match="recovery_readback_changed"):
        await probe.verify_account_captures(
            None, seeded.records, scope=scope, clock=clock
        )
