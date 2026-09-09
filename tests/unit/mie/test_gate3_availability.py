import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation.availability import (
    ArchiveObservationReceipt,
    AvailabilityBasis,
    AvailabilityProvenance,
)
from app.mie.validation.contracts import DatasetPartition

START = datetime(2024, 1, 1, tzinfo=UTC)
CLOSE = START + timedelta(minutes=1)
OBSERVED = START + timedelta(days=2)
RETRIEVED = OBSERVED + timedelta(minutes=1)


def observation(**updates) -> ArchiveObservationReceipt:
    return ArchiveObservationReceipt(
        **{
            "archive_sha256": "a" * 64,
            "archive_byte_size": 100,
            "symbol": "BTCUSDT",
            "instrument_id": "BTC-USDT-SWAP",
            "partition": DatasetPartition.DEVELOPMENT,
            "day_start_at": START,
            "member_name": "BTCUSDT-1m-2024-01-01.csv",
            "observed_at": OBSERVED,
            "retrieved_at": RETRIEVED,
            **updates,
        }
    )


def availability(**updates) -> AvailabilityProvenance:
    return AvailabilityProvenance(
        **{
            "basis": AvailabilityBasis.ARCHIVE_OBSERVATION,
            "source_row_sha256": "b" * 64,
            "receipt_sha256": "a" * 64,
            "bar_closed_at": CLOSE,
            "observed_at": OBSERVED,
            "retrieved_at": RETRIEVED,
            "available_at": RETRIEVED,
            **updates,
        }
    )


@pytest.mark.parametrize(
    "basis,time,assumption",
    [
        (AvailabilityBasis.ARCHIVE_OBSERVATION, RETRIEVED, "none"),
        (AvailabilityBasis.MEASURED_ROW_RECEIPT, OBSERVED, "none"),
        (AvailabilityBasis.ASSUMED_BAR_CLOSE, CLOSE, "confirmed_at_bar_close"),
    ],
)
def test_timing_bases_are_explicit_and_never_promote(basis, time, assumption) -> None:
    result = availability(basis=basis, available_at=time, assumption=assumption)
    assert result.available_at == time
    assert result.predictive_oos_eligible is False
    assert result.current_claim.value == "computational"
    assert result.execution_authority is False
    assert AvailabilityProvenance.model_validate_json(result.canonical_json()) == result


@pytest.mark.parametrize(
    "updates",
    [
        {"available_at": CLOSE},
        {"basis": AvailabilityBasis.ASSUMED_BAR_CLOSE, "available_at": CLOSE},
        {"basis": AvailabilityBasis.MEASURED_ROW_RECEIPT},
        {"assumption": "confirmed_at_bar_close"},
        {"observed_at": CLOSE - timedelta(microseconds=1)},
        {"retrieved_at": OBSERVED - timedelta(microseconds=1)},
        {"observed_at": OBSERVED.replace(tzinfo=None)},
        {"predictive_oos_eligible": True},
        {"current_claim": "predictive_oos"},
        {"source_row_sha256": "not-a-hash"},
    ],
)
def test_availability_rejects_false_timing_or_eligibility(updates) -> None:
    with pytest.raises(ValidationError):
        availability(**updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"partition": DatasetPartition.RETROSPECTIVE_HOLDOUT},
        {"partition": "prospective_holdout"},
        {"symbol": "SOLUSDT"},
        {"instrument_id": "ETH-USDT-SWAP"},
        {"member_name": "../BTCUSDT-1m-2024-01-01.csv"},
        {"member_name": "BTCUSDT-1m-2024-01-02.csv"},
        {"day_start_at": START + timedelta(microseconds=1)},
        {"observed_at": START + timedelta(days=1) - timedelta(microseconds=1)},
        {"retrieved_at": OBSERVED - timedelta(microseconds=1)},
        {"day_start_at": START.replace(tzinfo=None)},
        {"archive_byte_size": True},
        {"archive_byte_size": 1.0},
        {"archive_byte_size": 1024 * 1024 + 1},
        {"archive_sha256": "A" * 64},
        {"runtime_consumers": 1},
        {"execution_authority": True},
    ],
)
def test_observation_rejects_unscoped_or_unproven_inputs(updates) -> None:
    with pytest.raises(ValidationError):
        observation(**updates)


def test_receipt_is_immutable_canonical_and_hash_binds_times() -> None:
    result = observation()
    payload = result.canonical_json_bytes()
    assert result.canonical_sha256() == hashlib.sha256(payload).hexdigest()
    assert ArchiveObservationReceipt.model_validate_json(payload) == result
    assert (
        observation(retrieved_at=RETRIEVED + timedelta(seconds=1)).canonical_sha256()
        != result.canonical_sha256()
    )
    with pytest.raises(ValidationError):
        result.archive_byte_size = 101


def test_archive_day_end_overflow_is_a_contract_validation_error() -> None:
    with pytest.raises(ValidationError, match="supported datetime range"):
        observation(
            day_start_at=datetime(9999, 12, 31, tzinfo=UTC),
            member_name="BTCUSDT-1m-9999-12-31.csv",
        )
