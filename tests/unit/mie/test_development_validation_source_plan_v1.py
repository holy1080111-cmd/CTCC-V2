"""Synthetic source-plan contracts; no capture, clock, DB, or network evidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.mie.validation.development_validation_source_plan_v1 import (
    MAX_SOURCE_BATCH_COORDINATES,
    DevelopmentValidationSourceCoordinateV1,
    DevelopmentValidationSourcePlanV1,
    checked_development_validation_source_plan,
)
from app.mie.validation.prospective_capture_schedule import MAX_SCHEDULE_ROWS
from app.public_market_source.public_attempt_journal import MAX_ATTEMPTS
from app.public_market_source.public_market_receipts import (
    MINUTE_NS,
    PublicMinuteCapturePlanV1,
)
from app.public_market_source.public_receipt_storage import MAX_CAPTURES

_START = datetime(2026, 10, 1, tzinfo=UTC)
_PLANNED = datetime(2026, 10, 22, 1, 2, 3, 4000, tzinfo=UTC)
_ORIGIN = "https://openapi.okx.com"


def _ns(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1000


def _coordinate(
    ordinal=0,
    *,
    start=_START,
    rows=1,
    instrument="BTC-USDT-SWAP",
    partition="development",
    purpose="historical_warmup",
    planned=_PLANNED,
    origin=_ORIGIN,
):
    capture = PublicMinuteCapturePlanV1(
        origin=origin,
        instrument_id=instrument,
        start_ns=_ns(start),
        end_ns=_ns(start) + (rows if type(rows) is int else 1) * MINUTE_NS,
        expected_rows=rows,
        created_ns=_ns(planned),
    )
    return DevelopmentValidationSourceCoordinateV1(
        ordinal=ordinal,
        partition=partition,
        purpose=purpose,
        capture_plan=capture,
        capture_plan_sha256=capture.canonical_sha256(),
    )


def _plan(coordinates=None, **updates):
    if coordinates is None:
        coordinates = (_coordinate(),)
    payload = {
        "collection_id": "devval:source:test:v1",
        "origin": _ORIGIN,
        "planned_at": _PLANNED,
        "coordinates": coordinates,
        "expected_rows": sum(c.capture_plan.expected_rows for c in coordinates),
    }
    payload.update(updates)
    return DevelopmentValidationSourcePlanV1(**payload)


def _changed_capture(coordinate, **updates):
    capture = coordinate.capture_plan.model_copy(update=updates)
    return coordinate.model_copy(
        update={
            "capture_plan": capture,
            "capture_plan_sha256": capture.canonical_sha256(),
        }
    )


def test_exact_84h_two_symbol_warmup_is_four_bounded_batches():
    coordinates = tuple(
        _coordinate(
            ordinal,
            instrument=instrument,
            start=_START + timedelta(minutes=offset),
            rows=rows,
            partition=partition,
        )
        for ordinal, (instrument, offset, rows, partition) in enumerate(
            (
                ("BTC-USDT-SWAP", 0, 2880, "development"),
                ("BTC-USDT-SWAP", 2880, 2160, "development"),
                ("ETH-USDT-SWAP", 0, 2880, "validation"),
                ("ETH-USDT-SWAP", 2880, 2160, "validation"),
            )
        )
    )
    plan = _plan(coordinates)
    assert plan.expected_rows == 10_080 > MAX_SCHEDULE_ROWS
    assert tuple(plan.capture_plan(i).expected_rows for i in range(4)) == (
        2880,
        2160,
        2880,
        2160,
    )
    assert all(c.capture_plan.end_ns < _ns(plan.planned_at) for c in coordinates)
    assert MAX_SCHEDULE_ROWS == 4096
    assert MAX_SOURCE_BATCH_COORDINATES == min(MAX_CAPTURES, MAX_ATTEMPTS) == 1024


def test_canonical_roundtrip_is_deterministic_and_has_no_availability_field():
    plan = _plan()
    replayed = DevelopmentValidationSourcePlanV1.model_validate_json(
        plan.canonical_json_bytes()
    )
    assert replayed == plan
    assert (
        replayed.canonical_sha256()
        == hashlib.sha256(plan.canonical_json_bytes()).hexdigest()
    )
    assert (
        checked_development_validation_source_plan(
            replayed, expected_sha256=plan.canonical_sha256()
        )
        == plan
    )
    assert plan.coordinate(0) == plan.coordinates[0]
    assert type(plan.capture_plan(0)) is PublicMinuteCapturePlanV1
    assert plan.capture_plan(0).created_ns == _ns(_PLANNED)
    assert "available_at" not in type(plan).model_fields
    assert "clock_trusted" not in type(plan).model_fields
    assert not plan.native_capture_authority
    assert not plan.predictive_oos_eligible
    assert not plan.candidate_seal_authority
    assert not plan.holdout_access_authority
    assert not plan.execution_authority
    assert plan.runtime_consumers == 0


def test_prospective_coordinates_can_be_frozen_without_candidate_or_holdout():
    plan = _plan(
        (
            _coordinate(
                start=datetime(2026, 10, 23, tzinfo=UTC),
                rows=3000,
                partition="validation",
                purpose="prospective_collection",
            ),
        )
    )
    assert plan.capture_plan(0).created_ns < plan.capture_plan(0).start_ns
    assert plan.expected_rows == 3000


@pytest.mark.parametrize("ordinal", (-1, 1, 1.0, True, False, "0", None))
def test_access_requires_an_existing_exact_integer_ordinal(ordinal):
    with pytest.raises(ValueError, match="ordinal invalid"):
        _plan().capture_plan(ordinal)


@pytest.mark.parametrize("ordinal", (-1, 1024, 0.0, True, "0"))
def test_coordinate_ordinal_is_strict_and_bounded(ordinal):
    with pytest.raises(ValidationError):
        _coordinate(ordinal)


@pytest.mark.parametrize("ordinals", ((1,), (0, 0), (0, 2), (1, 0)))
def test_missing_duplicate_or_reordered_ordinals_are_not_repaired(ordinals):
    coordinates = tuple(
        _coordinate(ordinal, start=_START + timedelta(minutes=i))
        for i, ordinal in enumerate(ordinals)
    )
    with pytest.raises(ValidationError, match="coordinate identity differs"):
        _plan(coordinates)


@pytest.mark.parametrize(
    ("partition", "purpose"),
    (
        ("retrospective_holdout", "historical_warmup"),
        ("holdout", "historical_warmup"),
        ("test", "historical_warmup"),
        ("development", "predictive_oos"),
        ("development", "execution"),
    ),
)
def test_only_development_validation_and_explicit_source_purposes(partition, purpose):
    with pytest.raises(ValidationError):
        _coordinate(partition=partition, purpose=purpose)


@pytest.mark.parametrize("partition", ("development", "validation"))
def test_overlap_cannot_hide_behind_a_different_partition(partition):
    coordinates = (
        _coordinate(rows=2),
        _coordinate(1, start=_START + timedelta(minutes=1), partition=partition),
    )
    with pytest.raises(ValidationError, match="windows overlap"):
        _plan(coordinates)


def test_unordered_nonoverlapping_windows_remain_in_fixed_caller_order():
    coordinates = (
        _coordinate(start=_START + timedelta(minutes=2)),
        _coordinate(1, start=_START),
    )
    plan = _plan(coordinates)
    assert plan.capture_plan(0).start_ns > plan.capture_plan(1).start_ns


@pytest.mark.parametrize(
    "updates",
    (
        {"origin": "https://us.okx.com"},
        {"created_ns": _ns(_PLANNED) + 1000},
        {"source_parser": "caller.parser"},
        {"clock_policy": "caller.clock.trusted"},
        {"volume_unit": "USD"},
        {"max_pages": 0},
        {"expected_rows": True},
        {"page_size": 101},
        {"end_ns": _ns(_START) + MINUTE_NS + 1},
    ),
)
def test_nested_plan_change_or_forgery_is_revalidated(updates):
    coordinate = _changed_capture(_coordinate(), **updates)
    with pytest.raises(ValueError):
        _plan((coordinate,))


def test_capture_digest_must_match_the_exact_nested_contract():
    coordinate = _coordinate().model_copy(update={"capture_plan_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="digest differs"):
        _plan((coordinate,))


@pytest.mark.parametrize("rows", (0, 3001, True, 1.0, "1"))
def test_existing_capture_row_bound_is_not_relaxed(rows):
    with pytest.raises(ValidationError):
        _coordinate(rows=rows)


@pytest.mark.parametrize("total", (0, 2, True, 1.0, "1"))
def test_expected_total_is_strict_and_matches_every_batch(total):
    with pytest.raises(ValidationError):
        _plan(expected_rows=total)


@pytest.mark.parametrize(
    ("purpose", "start", "planned"),
    (
        ("historical_warmup", datetime(2026, 10, 23, tzinfo=UTC), _PLANNED),
        ("prospective_collection", _START, _PLANNED),
        ("prospective_collection", _START, _START),
    ),
)
def test_purpose_must_agree_with_declared_planning_metadata(purpose, start, planned):
    coordinate = _coordinate(purpose=purpose, start=start, planned=planned)
    with pytest.raises(ValidationError, match="purpose contradicts"):
        _plan((coordinate,), planned_at=planned)


@pytest.mark.parametrize(
    "planned_at",
    (
        _PLANNED.replace(tzinfo=None),
        _PLANNED.astimezone(timezone(timedelta(hours=8))),
        _PLANNED.isoformat(),
    ),
)
def test_planning_time_is_utc_metadata_not_coerced_or_trusted(planned_at):
    with pytest.raises(ValidationError):
        _plan(planned_at=planned_at)


@pytest.mark.parametrize(
    "name",
    (
        "predictive_oos_eligible",
        "native_capture_authority",
        "candidate_seal_authority",
        "holdout_access_authority",
        "execution_authority",
    ),
)
@pytest.mark.parametrize("value", (True, 0, "false"))
def test_authority_cannot_be_granted_or_coerced(name, value):
    with pytest.raises(ValidationError, match="authority forbidden"):
        _plan(**{name: value})


@pytest.mark.parametrize("value", (False, True, "0", 1))
def test_runtime_consumer_count_cannot_be_coerced(value):
    with pytest.raises(ValidationError, match="authority forbidden"):
        _plan(runtime_consumers=value)


@pytest.mark.parametrize(
    "name", ("holdout_id", "candidate_sha256", "seal_sha256", "available_at")
)
def test_unrelated_authority_or_availability_fields_are_forbidden(name):
    with pytest.raises(ValidationError, match="Extra inputs"):
        _plan(**{name: "0" * 64})


@pytest.mark.parametrize("digest", ("0" * 64, "A" * 64, "x", b"0" * 64, True))
def test_retained_digest_is_exact(digest):
    with pytest.raises(ValueError, match="retained digest differs"):
        checked_development_validation_source_plan(_plan(), expected_sha256=digest)


def test_tampered_instances_are_replayed_at_public_access_boundary():
    plan = _plan()
    changed = plan.model_copy(update={"expected_rows": 2})
    with pytest.raises(ValidationError, match="row total differs"):
        changed.coordinate(0)
    changed_coordinate = plan.coordinates[0].model_copy(update={"ordinal": True})
    changed = plan.model_copy(update={"coordinates": (changed_coordinate,)})
    with pytest.raises(ValidationError):
        checked_development_validation_source_plan(changed)
    with pytest.raises(ValidationError, match="frozen"):
        plan.origin = "https://us.okx.com"
    with pytest.raises(ValidationError, match="frozen"):
        plan.coordinates[0].ordinal = 1


def test_subclasses_cannot_replace_exact_source_contracts():
    class CaptureSubclass(PublicMinuteCapturePlanV1):
        pass

    class CoordinateSubclass(DevelopmentValidationSourceCoordinateV1):
        pass

    class PlanSubclass(DevelopmentValidationSourcePlanV1):
        pass

    coordinate = _coordinate()
    capture = CaptureSubclass(**coordinate.capture_plan.model_dump(mode="python"))
    with pytest.raises(ValidationError, match="exact_contract_required"):
        DevelopmentValidationSourceCoordinateV1(
            ordinal=0,
            partition="development",
            purpose="historical_warmup",
            capture_plan=capture,
            capture_plan_sha256=capture.canonical_sha256(),
        )
    subclass_coordinate = CoordinateSubclass(**coordinate.model_dump(mode="python"))
    with pytest.raises(ValidationError, match="exact source coordinate"):
        _plan((subclass_coordinate,))
    subclass_plan = PlanSubclass(**_plan().model_dump(mode="python"))
    with pytest.raises(ValueError, match="exact development validation"):
        checked_development_validation_source_plan(subclass_plan)


def test_coordinate_collection_is_finite_exact_tuple_without_refill():
    with pytest.raises(ValidationError):
        _plan(())
    with pytest.raises(ValidationError):
        _plan([_coordinate()])
    coordinates = tuple(
        _coordinate(i, start=_START + timedelta(minutes=i))
        for i in range(MAX_SOURCE_BATCH_COORDINATES)
    )
    plan = _plan(coordinates)
    assert len(plan.coordinates) == MAX_SOURCE_BATCH_COORDINATES
    assert plan.capture_plan(1023).expected_rows == 1
    with pytest.raises(ValidationError):
        _plan(coordinates + (coordinates[-1],))
    with pytest.raises(ValueError, match="ordinal invalid"):
        plan.coordinate(1024)


def test_changed_plan_has_new_identity_and_cannot_match_retained_identity():
    original = _plan()
    changed = _plan(collection_id="devval:source:different:v1")
    assert changed.canonical_sha256() != original.canonical_sha256()
    with pytest.raises(ValueError, match="retained digest differs"):
        checked_development_validation_source_plan(
            changed, expected_sha256=original.canonical_sha256()
        )
