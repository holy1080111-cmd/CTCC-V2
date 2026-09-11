"""Contract consistency only; event truth is tested against OHLC separately."""

from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.models import EntryTrigger

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def event_payload():
    return {
        "report_id": "test-event",
        "symbol": "BTC/USDT:USDT",
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "direction": "long",
        "observed_at": NOW,
        "source_sha256": "a" * 64,
        "source_timeframe": "5m",
        "setup_time": NOW - timedelta(minutes=5),
        "setup_type": "ema_pullback",
        "invalidation_price": Decimal(98),
        "setup_basis": (("level", "99"),),
        "trigger": EntryTrigger(
            report_id="test-event",
            trigger_type="momentum_resume",
            trigger_time=NOW,
            trigger_price=Decimal(100),
            expires_at=NOW + timedelta(minutes=10),
        ),
    }


def test_source_bound_event_contract_is_frozen_and_round_trips():
    event = TriggerDetection(**event_payload())
    assert (
        TriggerDetection.model_validate_json(event.model_dump_json(round_trip=True))
        == event
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        event.source_sha256 = "b" * 64


@pytest.mark.parametrize("fault", ["reversed_setup", "reversed_expiry"])
def test_clock_fold_cannot_reverse_actual_event_causality(fault):
    class FoldOffset(tzinfo):
        def utcoffset(self, value):
            return timedelta(hours=-5 if value.fold else -4)

        def dst(self, value):
            return timedelta(0)

    zone = FoldOffset()
    later = datetime(2026, 11, 1, 1, 10, tzinfo=zone, fold=1)
    earlier = datetime(2026, 11, 1, 1, 50, tzinfo=zone)
    payload = event_payload()
    trigger = payload["trigger"].model_dump(round_trip=True)
    if fault == "reversed_expiry":
        trigger.update(trigger_time=later, expires_at=earlier)
        with pytest.raises(ValidationError):
            EntryTrigger(**trigger)
    else:
        trigger.update(trigger_time=earlier, expires_at=later)
        payload.update(
            setup_time=later,
            trigger=EntryTrigger(**trigger),
            observed_at=datetime(2026, 11, 1, 2, tzinfo=zone, fold=1),
        )
        with pytest.raises(ValidationError):
            TriggerDetection(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("report_id", "../escape"),
        ("source_sha256", "A" * 64),
        ("source_sha256", "a" * 65),
        ("source_sha256", None),
        ("source_timeframe", "1m"),
        ("strategy", "unknown"),
        ("direction", "neutral"),
        ("setup_time", None),
        ("setup_time", NOW + timedelta(seconds=1)),
        ("observed_at", NOW.replace(tzinfo=None)),
        ("setup_time", NOW.replace(tzinfo=None)),
        ("invalidation_price", None),
        ("invalidation_price", Decimal("NaN")),
        ("invalidation_price", Decimal("Infinity")),
        ("invalidation_price", Decimal(0)),
        ("setup_basis", (("x", "1"), ("x", "2"))),
        ("setup_basis", tuple((f"x{i}", "1") for i in range(33))),
    ],
)
def test_invalid_event_identity_chronology_or_geometry_cannot_enter_contract(
    field, value
):
    payload = event_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        TriggerDetection(**payload)


@pytest.mark.parametrize("fault", ["report", "future", "before_setup"])
def test_trigger_must_match_report_and_known_setup(fault):
    payload = event_payload()
    trigger = payload["trigger"].model_dump()
    if fault == "report":
        trigger["report_id"] = "different"
    elif fault == "future":
        trigger["trigger_time"] = NOW + timedelta(seconds=1)
    else:
        trigger["trigger_time"] = NOW - timedelta(minutes=6)
    payload["trigger"] = EntryTrigger(**trigger)
    with pytest.raises(ValidationError):
        TriggerDetection(**payload)


def test_absent_trigger_evidence_stays_absent_instead_of_fabricated():
    payload = event_payload()
    payload.update(
        trigger=None,
        setup_time=None,
        setup_type=None,
        source_sha256=None,
        invalidation_price=None,
        setup_basis=(),
        fail_codes=("trigger_missing",),
    )
    event = TriggerDetection(**payload)
    assert event.trigger is None and event.source_sha256 is None
    assert event.fail_codes == ("trigger_missing",)


def test_callers_cannot_supply_execution_permission_to_event_record():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TriggerDetection(**event_payload(), execution_authority=True)
