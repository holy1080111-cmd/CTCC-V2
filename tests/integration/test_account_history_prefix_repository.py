"""Real PostgreSQL B3-prefix replay; synthetic account HTTP is not admission."""

import json
import os
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

import pytest

from app.database.repositories.account_history_prefix import (
    POLICY_SHA256,
    AccountHistoryPrefixError,
    AccountHistoryPrefixRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_account_history_query_verifier_repository import now
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    stored,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_full_prefix_replays_committed_b3_and_preserves_denial(
    monkeypatch, database
):
    _, scope, reference, _, batch = await stored(monkeypatch, database)
    receipt = await AccountHistoryPrefixRepository(
        database[1], clock=now
    ).read_full_prefix(scope)
    value = json.loads(receipt.receipt_json)
    assert value["through_sequence"] == batch.sequence == 1
    assert value["head_sha256"] == batch.event_sha256
    assert value["last_source_reference"] == observed.reference_document(reference)
    assert value["entire_accepted_b3_index_prefix_replayed"] is True
    assert value["current_predecessor_sequence"] == 0
    assert value["current_predecessor_sha256"] is None
    assert value["account_complete"] is receipt.account_complete is False
    assert value["execution_authority"] is receipt.execution_authority is False
    assert value["admission"] == receipt.admission == "DENY"
    assert scope.account_id.encode() not in receipt.receipt_json


async def test_new_committed_b3_batch_during_prefix_replay_is_rejected(
    monkeypatch, database
):
    history, scope, _, _, _ = await stored(monkeypatch, database)
    prefix = AccountHistoryPrefixRepository(database[1], clock=now)
    original_read = prefix.history.read

    async def append_after_page(_scope, *, after, limit):
        page = await original_read(_scope, after=after, limit=limit)
        if after == 0:
            reference, window = await additional_capture(
                monkeypatch, history, scope, []
            )
            await history.append(scope, reference, window, expected_revision=1)
        return page

    monkeypatch.setattr(prefix.history, "read", append_after_page)
    with pytest.raises(AccountHistoryPrefixError, match="history_prefix_read_failed"):
        await prefix.read_full_prefix(scope)
    assert len(await history.read(scope)) == 2


@pytest.mark.skipif(
    os.environ.get("CTCC_RUN_B3_PREFIX_33") != "1",
    reason="opt-in 33-batch PostgreSQL scale probe; no private HTTP",
)
async def test_cross_page_33_prefix_scale_and_fail_closed(
    monkeypatch, database, tmp_path
):
    """Replay every original B1 source across the B3 page boundary.

    This deliberately builds 33 synthetic captures, so ordinary CI skips it.
    The optional receipt contains only digests, counts, and elapsed milliseconds.
    """
    scope = LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}",
        settlement_currency="USDT",
    )
    history = AccountObservationIndexRepository(database[1], clock=now)
    await history.journal.ledger.initialize_capture_scope(scope)
    accepted = []
    for revision in range(33):
        reference, window = await additional_capture(monkeypatch, history, scope, [])
        accepted.append(
            await history.append(scope, reference, window, expected_revision=revision)
        )

    prefix = AccountHistoryPrefixRepository(database[1], clock=now)
    page_reads = source_chain_reads = 0
    original_page = prefix.history.read
    original_chain = prefix.history.journal.read_chain

    async def counted_page(*args, **kwargs):
        nonlocal page_reads
        page_reads += 1
        return await original_page(*args, **kwargs)

    async def counted_chain(*args, **kwargs):
        nonlocal source_chain_reads
        source_chain_reads += 1
        return await original_chain(*args, **kwargs)

    monkeypatch.setattr(prefix.history, "read", counted_page)
    monkeypatch.setattr(prefix.history.journal, "read_chain", counted_chain)
    started = perf_counter_ns()
    receipt = await prefix.read_full_prefix(scope)
    elapsed_ms = (perf_counter_ns() - started) // 1_000_000
    value = json.loads(receipt.receipt_json)

    scope_sha = journal.digest(
        journal.canonical(
            [scope.environment, scope.account_id, scope.settlement_currency]
        )
    )
    membership = journal.digest(
        journal.canonical([POLICY_SHA256, scope_sha, "accepted_B3_prefix"])
    )
    for sequence, batch in enumerate(accepted, 1):
        document = json.loads(batch.document_json)
        membership = journal.digest(
            journal.canonical(
                [
                    membership,
                    sequence,
                    batch.event_sha256,
                    document["anchor_sha256"],
                    batch.replay.receipt_sha256,
                ]
            )
        )
    terminal = json.loads(accepted[-1].document_json)
    anchor = json.loads(terminal["anchor_json"])
    checkpoint = await history.journal.ledger.read_bootstrap_checkpoint(scope)
    expected_receipt = {
        "schema_version": "ctcc.account_accepted_b3_prefix.v1",
        "policy_sha256": POLICY_SHA256,
        "scope_sha256": scope_sha,
        "through_sequence": 33,
        "head_sha256": accepted[-1].event_sha256,
        "head_anchor_sha256": terminal["anchor_sha256"],
        "ordered_membership_sha256": membership,
        "last_source_reference": terminal["reference"],
        "current_predecessor_sequence": 0,
        "current_predecessor_sha256": None,
        "local_checkpoint_sha256": checkpoint.state_sha256,
        "unresolved_findings_through_sequence": anchor[
            "unresolved_findings_through_sequence"
        ],
        "entire_accepted_b3_index_prefix_replayed": True,
        "exchange_history_finality_verified": False,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    measurement = {
        "schema_version": "ctcc.b3_prefix_scale_measurement.v1",
        "synthetic_capture_count": 33,
        "b3_page_reads": page_reads,
        "original_b1_chain_reads": source_chain_reads,
        "replay_elapsed_ms": elapsed_ms,
        "prefix_receipt_sha256": receipt.receipt_sha256,
        "expected_receipt_sha256": journal.digest(journal.canonical(expected_receipt)),
    }
    receipt_dir = Path(os.environ.get("CTCC_B3_SCALE_RECEIPT_DIR", str(tmp_path)))
    receipt_dir.mkdir(parents=True, exist_ok=True)
    with (receipt_dir / "b3-prefix-33-measurement.json").open("xb") as output:
        output.write(journal.canonical(measurement))
    print(journal.canonical(measurement).decode())
    assert receipt.receipt_json == journal.canonical(expected_receipt)
    assert receipt.receipt_sha256 == measurement["expected_receipt_sha256"]
    assert value["through_sequence"] == 33 and page_reads == 2
    assert source_chain_reads >= 33
    assert elapsed_ms < 30_000  # necessary, not sufficient for the full pre-HTTP path

    missing = AccountHistoryPrefixRepository(database[1], clock=now)
    real_read = missing.history.read

    async def omit_second_page(*args, **kwargs):
        if kwargs["after"] == 32:
            return ()
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(missing.history, "read", omit_second_page)
    with pytest.raises(AccountHistoryPrefixError, match="history_prefix_read_failed"):
        await missing.read_full_prefix(scope)

    advancing = AccountHistoryPrefixRepository(database[1], clock=now)
    real_read = advancing.history.read

    async def append_after_first_page(*args, **kwargs):
        page = await real_read(*args, **kwargs)
        if kwargs["after"] == 0:
            reference, window = await additional_capture(
                monkeypatch, history, scope, []
            )
            await history.append(scope, reference, window, expected_revision=33)
        return page

    monkeypatch.setattr(advancing.history, "read", append_after_first_page)
    with pytest.raises(AccountHistoryPrefixError, match="history_prefix_read_failed"):
        await advancing.read_full_prefix(scope)
