"""Synthetic DB/file provenance diagnostics; no Notion or order transport."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories.submission_reporting import (
    SubmissionReportingRepository,
)
from app.trade_evidence import outbox
from app.trade_evidence.submission_reporting import SubmissionReportingError
from tests.unit.test_trade_evidence_outbox import (
    REPORT,
    Clock,
    MemoryBackend,
    payload,
)

QUEUE = "ctcc-synthetic-diagnostic"


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows, receipt=None, *, fails=False):
        self.rows, self.receipt, self.fails = rows, receipt, fails
        self.queries = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def scalars(self, _statement):
        self.queries += 1
        if self.fails:
            raise RuntimeError("synthetic private DB failure")
        return FakeRows(self.rows)

    async def get(self, _model, _identity):
        self.queries += 1
        return self.receipt


def scenario(monkeypatch, *, rows=True, receipt=True, eligible=False):
    backend, clock = MemoryBackend(), Clock()
    monkeypatch.setattr(outbox, "_root_context", backend.context)
    local = outbox.enqueue(backend.root, payload(), clock=clock)
    data = local.envelope.payload
    spool = SimpleNamespace(
        spool_id="d" * 64,
        report_sha256=data.submission_receipt_sha256,
        policy_sha256="e" * 64,
        queue_namespace=QUEUE,
        eligible=eligible,
        created_at=clock.now,
    )
    stored_receipt = SimpleNamespace(
        envelope_sha256=local.envelope.envelope_sha256,
        report_sha256=spool.report_sha256,
        policy_sha256=spool.policy_sha256,
        queue_namespace=QUEUE,
        verified_at=clock.now + timedelta(seconds=1),
    )
    session = FakeSession((spool,) if rows else (), stored_receipt if receipt else None)
    repository = SubmissionReportingRepository(lambda: session, clock=clock)
    report = SimpleNamespace(
        report_id=data.report_id,
        candidate=SimpleNamespace(
            instrument_id=data.instrument_id,
            direction=data.direction,
            original_source_sha256=data.source_sha256,
            original_evidence_sha256=data.evidence_report_sha256,
        ),
        strategy=data.strategy,
        client_order_id=data.order_reference,
        completed_at=data.submitted_at,
        evidence_completed_at=data.evidence_completed_at,
        status="acknowledged",
    )

    async def verified_spool(_session, _spool):
        assert _session is session and _spool is spool
        return SimpleNamespace(status="acknowledged"), report, local.envelope.policy

    monkeypatch.setattr(repository, "_verify_spool", verified_spool)
    return backend, local, repository, session, spool, stored_receipt, report


async def inspect(backend, local, repository, *, expected=None):
    return await repository.inspect_notion_delivery_source(
        local.envelope.payload.report_id,
        root=backend.root,
        queue_namespace=QUEUE,
        expected_envelope_sha256=(
            local.envelope.envelope_sha256 if expected is None else expected
        ),
    )


@pytest.mark.asyncio
async def test_local_envelope_pin_conflict_denies_before_db(monkeypatch):
    backend, local, repository, session, *_ = scenario(monkeypatch)
    finding = await inspect(backend, local, repository, expected="f" * 64)
    assert finding.reason_code == "notion_delivery_envelope_identity_mismatch"
    assert session.queries == 0
    assert finding.local_pair_verified is False
    assert finding.delivery_authority is False


@pytest.mark.asyncio
async def test_synthetic_late_marker_failure_preserves_journal_and_denies(monkeypatch):
    backend, local, repository, session, *_ = scenario(monkeypatch)
    journal = (f"{REPORT}.state", "00000001.json")
    saved_journal = backend.files[journal]
    backend.files.pop((f"{REPORT}.json",))  # In-memory late publication failure.

    finding = await inspect(backend, local, repository)
    assert finding.reason_code == "notion_delivery_local_pair_unavailable"
    assert finding.delivery_authority is False
    assert session.queries == 0
    assert backend.files[journal] == saved_journal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("no_spool", "notion_delivery_spool_not_unique"),
        ("missing_receipt", "notion_delivery_projection_receipt_missing"),
        ("wrong_receipt", "notion_delivery_projection_receipt_mismatch"),
        ("ineligible", "notion_delivery_source_eligibility_unverified"),
        ("forged_eligible", "notion_delivery_owned_issuer_unavailable"),
        ("changed_payload", "notion_delivery_spool_pair_mismatch"),
        ("bad_lineage", "notion_delivery_committed_lineage_invalid"),
        ("db_failure", "notion_delivery_db_readback_unavailable"),
    ],
)
async def test_synthetic_missing_or_forged_db_source_never_authorizes(
    monkeypatch, change, reason
):
    backend, local, repository, session, spool, receipt, report = scenario(
        monkeypatch,
        rows=change != "no_spool",
        receipt=change != "missing_receipt",
        eligible=change == "forged_eligible",
    )
    before = dict(backend.files)
    if change == "wrong_receipt":
        receipt.envelope_sha256 = "f" * 64
    elif change == "changed_payload":
        report.client_order_id = "changed-order-reference"
    elif change == "bad_lineage":

        async def broken_lineage(*_args):
            raise SubmissionReportingError("synthetic_private_lineage_failure")

        monkeypatch.setattr(repository, "_verify_spool", broken_lineage)
    elif change == "db_failure":
        session.fails = True

    finding = await inspect(backend, local, repository)
    assert finding.reason_code == reason
    assert finding.delivery_authority is False
    assert finding.execution_authority is False
    assert finding.owned_transport_issuer_verified is False
    assert backend.files == before  # This verifier never repairs or publishes.
    assert spool.spool_id == "d" * 64  # No DB mutation from the read view.


@pytest.mark.asyncio
async def test_invalid_pin_is_static_and_does_not_read_private_source(monkeypatch):
    backend, local, repository, session, *_ = scenario(monkeypatch)
    with pytest.raises(
        SubmissionReportingError, match="notion_delivery_source_input_invalid"
    ):
        await inspect(backend, local, repository, expected="invalid")
    assert session.queries == 0
