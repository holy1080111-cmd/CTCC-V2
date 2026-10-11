"""Synthetic service wiring; original journal and V3 readers tested separately."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

import app.mie.validation.post_publication_availability_v3 as v3_module
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
)
from app.mie.validation.blind_window_capture import bind_blind_window_minute
from app.mie.validation.public_checkpoint_service import (
    _ISSUER,
    PublicCheckpointCaptureService,
    WitnessedPublicCapture,
)
from tests.unit.mie.test_gate3_blind_window_capture import fixture_inputs

pytestmark = pytest.mark.asyncio


@pytest.fixture
def case(monkeypatch):
    source = fixture_inputs(monkeypatch)
    journal = source["journal"]
    repository = PublicReceiptWitnessRepository(None)
    observation = PublicReceiptPostReadRepository(None, repository)
    service = PublicCheckpointCaptureService(
        _ISSUER,
        journal,
        repository,
        "b" * 32,
        SimpleNamespace(journal_key=journal.checkpoint.genesis_sha256),
    )
    blind = bind_blind_window_minute(**source)
    witnessed = WitnessedPublicCapture(
        capture_id=source["capture_id"],
        receipt_sha256=source["expected_capture_receipt_sha256"],
        witness_revision=3,
        witness_record_sha256="f" * 64,
        checkpoint_sha256=source["expected_journal_checkpoint_sha256"],
    )
    state = {"capture": 0, "append": 0, "read": 0, "verified": None}

    async def capture(_self, **_kwargs):
        state["capture"] += 1
        return witnessed

    checked = SimpleNamespace(
        v2_capture=SimpleNamespace(
            capture_id=witnessed.capture_id,
            receipt_sha256=witnessed.receipt_sha256,
            witness_record_sha256=witnessed.witness_record_sha256,
            capture_sequence=blind.journal_sequence,
            rows=(SimpleNamespace(source_row_sha256=blind.source_row_sha256),),
        ),
        available_at=blind.retained_at + timedelta(seconds=1),
    )
    state["verified"] = checked

    async def freeze(**_kwargs):
        state["append"] += 1
        return SimpleNamespace(payload=b"synthetic", sha256="a" * 64, contract=checked)

    async def verify(*_args, **_kwargs):
        state["read"] += 1
        return state["verified"]

    monkeypatch.setattr(PublicCheckpointCaptureService, "capture", capture)
    monkeypatch.setattr(v3_module, "freeze_post_publication_capture_v3", freeze)
    monkeypatch.setattr(v3_module, "verify_post_publication_capture_v3", verify)
    kwargs = {
        "plan": journal.read_all()[0][0][0],
        "expected_plan_sha256": source["expected_capture_plan_sha256"],
        "preregistration": source["preregistration"],
        "expected_preregistration_sha256": source["expected_preregistration_sha256"],
        "observation_repository": observation,
    }
    return service, kwargs, blind, state


async def test_owned_service_links_source_to_independent_v3_readback(case):
    service, kwargs, blind, state = case
    result = await service.capture_with_post_read_v3(**kwargs)
    assert result.witnessed.capture_id == blind.capture_id
    assert result.blind_capture_sha256 == blind.canonical_sha256()
    assert result.post_read_v3_sha256 == "a" * 64
    assert not result.predictive_oos_eligible
    assert not result.execution_authority
    assert state["capture"] == state["append"] == state["read"] == 1


async def test_changed_independent_readback_is_unresolved_and_not_retried(case):
    service, kwargs, _, state = case
    state["verified"] = SimpleNamespace(
        v2_capture=state["verified"].v2_capture,
        available_at=state["verified"].available_at + timedelta(seconds=1),
    )
    with pytest.raises(
        PublicWitnessError, match="scheduled_capture_post_read_mismatch"
    ):
        await service.capture_with_post_read_v3(**kwargs)
    assert state["capture"] == state["append"] == state["read"] == 1
