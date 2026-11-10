"""Bounded Dev/Val prefix replay tests with synthetic filesystem seams."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.mie.validation import development_validation_source_collection_v6 as finite
from app.mie.validation.development_validation_source_plan_v1 import (
    DevelopmentValidationSourceCoordinateV1,
    DevelopmentValidationSourcePlanV1,
)
from app.mie.validation.prospective_development_validation_plan import (
    ProspectiveDevelopmentValidationPlanV1,
)


def _plan():
    start = datetime(2030, 1, 1, tzinfo=UTC)
    grid = ProspectiveDevelopmentValidationPlanV1(
        plan_id="synthetic-prefix-replay",
        planned_at=start - timedelta(days=1),
        origin="https://openapi.okx.com",
        development_start_at=start,
        development_end_at=start + timedelta(minutes=2),
        validation_start_at=start + timedelta(hours=4),
        validation_end_at=start + timedelta(hours=4, minutes=1),
        expected_rows=6,
    )
    coordinates = []
    for ordinal in range(3):
        capture = grid.capture_plan(ordinal)
        coordinates.append(
            DevelopmentValidationSourceCoordinateV1(
                ordinal=ordinal,
                partition="development",
                purpose="prospective_collection",
                capture_plan=capture,
                capture_plan_sha256=capture.canonical_sha256(),
            )
        )
    return DevelopmentValidationSourcePlanV1(
        collection_id=grid.plan_id,
        origin=grid.origin,
        planned_at=grid.planned_at,
        coordinates=tuple(coordinates),
        expected_rows=3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "changed_inventory", "missing_receipt_pin"])
async def test_owned_prefix_replays_each_pinned_slot_and_rechecks_inventory(
    tmp_path, monkeypatch, defect
):
    plan = _plan()
    identity = finite.CollectionIdentityV6(
        plan_sha256=plan.canonical_sha256(),
        journal_genesis_sha256="a" * 64,
        journal_id="b" * 32,
        root_device=1,
        root_inode=1,
    )
    reader = finite.CollectionReadOnlyV6(tmp_path, plan, identity)
    owner = finite.DevelopmentValidationAvailabilityOwnerV6(finite._ISSUER)
    state = finite._State(reader, None, None, None, finite._context(), None)
    finite._OWNERS[owner] = state
    pins = ("c" * 64, None if defect == "missing_receipt_pin" else "d" * 64)
    calls = []
    plan_checks = []
    original_check = finite.checked_development_validation_source_plan

    def check_plan(supplied, *, expected_sha256):
        plan_checks.append(expected_sha256)
        return original_check(supplied, expected_sha256=expected_sha256)

    def inventory(directory):
        assert directory is root
        again = ("inventory",) in calls
        calls.append(("inventory",))
        return (0, 1, 2) if defect == "changed_inventory" and again else (0, 1)

    def slot(directory, claim, *, expected_receipt_sha256):
        assert directory is root
        assert claim.identity_sha256 == identity.canonical_sha256()
        assert (
            claim.capture_plan_sha256
            == plan.coordinates[claim.ordinal].capture_plan_sha256
        )
        calls.append(("slot", claim.ordinal, expected_receipt_sha256))
        return SimpleNamespace(status="NATIVE_DURABLE_AVAILABLE")

    def mkdir(name):
        calls.append(("mkdir", name))
        raise finite.SourceCollectionError("synthetic_stop_before_native_io")

    @contextmanager
    def root_context(path):
        assert path == tmp_path
        yield root

    root = SimpleNamespace(mkdir=mkdir)
    monkeypatch.setattr(finite, "_root_context", root_context)
    monkeypatch.setattr(
        finite, "checked_development_validation_source_plan", check_plan
    )
    monkeypatch.setattr(reader, "inventory", inventory)
    monkeypatch.setattr(reader, "_read_claimed_slot", slot)

    with pytest.raises(
        finite.SourceCollectionError, match="availability_attempt_unresolved"
    ):
        await owner.capture_once(ordinal=2, expected_previous_receipt_sha256s=pins)

    assert plan_checks == [identity.plan_sha256]
    if defect == "missing_receipt_pin":
        assert not calls
    else:
        assert calls[:4] == [
            ("inventory",),
            ("slot", 0, pins[0]),
            ("slot", 1, pins[1]),
            ("inventory",),
        ]
        assert calls[4:] == ([] if defect else [("mkdir", "batch-00000002")])
    assert state.stopped and not state.busy
