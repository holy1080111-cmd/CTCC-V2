"""The independent observer validates original seal bytes before any ACK."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3CommittedSealError,
    Gate3PreregistrationSealObserver,
)
from tests.unit.mie.test_gate3_capture_schedule import valid_schedule

pytestmark = pytest.mark.asyncio


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, source):
        self.source = source

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return self

    async def scalar(self, _):
        return True

    async def execute(self, statement, _parameters):
        query = str(statement)
        if "gate3_prereg_seal_read" in query:
            return FakeResult((self.source.row,))
        if "gate3_prereg_seal_ack_read" in query:
            return FakeResult((self.source.ack_row,) if self.source.ack_row else ())
        if "gate3_prereg_seal_ack_append" in query:
            self.source.ack_appends += 1
            return FakeResult(())
        raise AssertionError("unexpected database operation")


class FakeSessionFactory:
    def __init__(self, row, ack_row=None):
        self.row = row
        self.ack_row = ack_row
        self.ack_appends = 0

    def __call__(self):
        return FakeSession(self)


async def test_malformed_canonical_nested_seal_is_rejected_before_ack():
    seal, schedule = valid_schedule()
    coordinate = schedule.coordinate_plan
    payload = json.loads(seal.canonical_json())
    payload["candidate"] = {}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    observed_at = datetime.now(UTC)
    source = FakeSessionFactory(
        SimpleNamespace(
            seal_sha256=sha,
            preregistration_id=seal.preregistration_id,
            coordinate_plan_sha256=coordinate.canonical_sha256(),
            window_key=coordinate.window_key(),
            holdout_id=coordinate.holdout_id,
            window_start=coordinate.start_at,
            window_end=coordinate.end_at,
            created_at=seal.created_at,
            recorded_at=observed_at,
            database_readback_at=observed_at,
            seal_json=raw,
        )
    )
    observer = Gate3PreregistrationSealObserver(source)
    with pytest.raises(Gate3CommittedSealError, match="prereg_seal_read_unavailable"):
        await observer.acknowledge(
            expected_seal_sha256=sha,
            expected_coordinate_plan_sha256=coordinate.canonical_sha256(),
            coordinate_plan=coordinate,
        )
    assert source.ack_appends == 0


async def test_historical_ack_replays_after_window_but_new_ack_is_denied():
    seal, schedule = valid_schedule()
    coordinate = schedule.coordinate_plan
    seal_recorded_at = datetime.now(UTC)
    acknowledged_at = seal_recorded_at + timedelta(seconds=1)
    late_readback = coordinate.start_at + timedelta(minutes=1)
    source = FakeSessionFactory(
        SimpleNamespace(
            seal_sha256=seal.canonical_sha256(),
            preregistration_id=seal.preregistration_id,
            coordinate_plan_sha256=coordinate.canonical_sha256(),
            window_key=coordinate.window_key(),
            holdout_id=coordinate.holdout_id,
            window_start=coordinate.start_at,
            window_end=coordinate.end_at,
            created_at=seal.created_at,
            recorded_at=seal_recorded_at,
            database_readback_at=late_readback,
            seal_json=seal.canonical_json(),
        ),
        SimpleNamespace(
            seal_sha256=seal.canonical_sha256(),
            preregistration_id=seal.preregistration_id,
            coordinate_plan_sha256=coordinate.canonical_sha256(),
            window_key=coordinate.window_key(),
            holdout_id=coordinate.holdout_id,
            window_start=coordinate.start_at,
            window_end=coordinate.end_at,
            seal_recorded_at=seal_recorded_at,
            acknowledged_at=acknowledged_at,
            database_readback_at=late_readback,
        ),
    )
    observer = Gate3PreregistrationSealObserver(source)
    historical = await observer.read_ack(
        expected_seal_sha256=seal.canonical_sha256(),
        expected_coordinate_plan_sha256=coordinate.canonical_sha256(),
        coordinate_plan=coordinate,
    )
    assert historical.acknowledged_at == acknowledged_at
    assert historical.predictive_oos_eligible is False
    with pytest.raises(
        Gate3CommittedSealError, match="prereg_seal_publication_readback_late"
    ):
        await observer.acknowledge(
            expected_seal_sha256=seal.canonical_sha256(),
            expected_coordinate_plan_sha256=coordinate.canonical_sha256(),
            coordinate_plan=coordinate,
        )
    assert source.ack_appends == 0
