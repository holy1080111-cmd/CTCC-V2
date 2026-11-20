"""Closed arithmetic time boundaries, never an owner or an Arm fixture."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Data

import pytest

from app.domain.source_primitives import ClockStamp
from app.okx_live import entry_owned_seal_v1 as subject
from app.okx_live import owned_qualification_v1 as q

NOW = datetime(2026, 11, 20, 16, 2, tzinfo=UTC)


def _stamp(at):
    return ClockStamp(
        utc_ns=q.source._utc_ns(at),
        monotonic_ns=100_000_000_000 + q.source._utc_ns(at) - q.source._utc_ns(NOW),
    )


def _data(monkeypatch):
    observed = Data(
        observed_at=NOW - timedelta(seconds=1), received_at=NOW - timedelta(seconds=1)
    )
    account = Data(
        **{
            name: observed
            for name in (
                "balance_stamp",
                "positions_stamp",
                "history_stamp",
                "reservations_stamp",
            )
        }
    )
    risk = Data(
        instrument=observed,
        claims=Data(
            account=account,
            authority=Data(stamp=observed),
            risk_policy=Data(max_age_seconds=5),
        ),
    )
    market = Data(
        received_at=NOW, ticker=Data(timestamp=NOW), order_book=Data(timestamp=NOW)
    )
    frames = tuple(
        Data(timeframe=bar, verified_through=q.public.candles._floor(NOW, bar))
        for bar in q.public.candles.TIMEFRAMES
    )
    packet = Data(
        candles=Data(frames=frames),
        market_aux=Data(provenance=(Data(source_time=NOW), Data(source_time=NOW))),
        policy=Data(market_aux=Data(max_age_seconds=5)),
    )
    current = Data(
        risk_inputs=risk,
        source=Data(evaluated_at=NOW, market=market, packet=packet),
        policy=Data(
            prefix=Data(
                data=Data(maximum_snapshot_age_seconds=5, maximum_quote_age_seconds=5)
            )
        ),
    )
    owner = object.__new__(q.source.OwnedLiveSourceV1)
    q.source._SOURCES[owner] = {
        "current_completed": _stamp(NOW),
        "plan": Data(max_current_age_ns=5_000_000_000),
    }
    facts = Data(history_start=NOW - timedelta(days=8))
    monkeypatch.setattr(subject, "_financial", lambda _: (facts, None))
    state = {
        "current_math": current,
        "source_owner": owner,
        "expiry_monotonic_ns": 130_000_000_000,
        "deadline": NOW + timedelta(seconds=30),
    }
    proof = {
        "expires_at": NOW + timedelta(seconds=20),
        "cash_cutoff": NOW + timedelta(hours=1),
    }
    return state, proof, facts, owner


def test_clock_advance_with_unchanged_exact_inputs_stays_valid_inside_all_boundaries(
    monkeypatch,
):
    state, proof, _, owner = _data(monkeypatch)
    try:
        subject._time(state, proof, _stamp(NOW + timedelta(seconds=1)))
        assert state["current_math"].source.evaluated_at == NOW
        assert state["current_math"].source.market.received_at == NOW
    finally:
        q.source._SOURCES.pop(owner)


@pytest.mark.parametrize(
    "change",
    (
        "book_age",
        "aux_age",
        "risk_age",
        "account_age",
        "event_expiry",
        "cash_window",
        "history_window",
        "bar_rollover",
        "arm_expiry",
    ),
)
def test_any_changed_time_dependent_input_cancels_without_retiming_or_recomputing(
    monkeypatch, change
):
    state, proof, facts, owner = _data(monkeypatch)
    at = NOW + timedelta(seconds=1)
    try:
        if change == "book_age":
            state["current_math"].source.market.order_book.timestamp -= timedelta(
                seconds=5
            )
        elif change == "aux_age":
            state["current_math"].source.packet.market_aux.provenance[
                0
            ].source_time -= timedelta(seconds=5)
        elif change == "risk_age":
            state["current_math"].risk_inputs.instrument.observed_at -= timedelta(
                seconds=4
            )
        elif change == "account_age":
            q.source._SOURCES[owner]["current_completed"] = _stamp(
                NOW - timedelta(seconds=5)
            )
        elif change == "event_expiry":
            proof["expires_at"] = at
        elif change == "cash_window":
            proof["cash_cutoff"] = at
        elif change == "history_window":
            facts.history_start = NOW - timedelta(days=7) + timedelta(seconds=2)
        elif change == "bar_rollover":
            at = NOW + timedelta(minutes=3)
        else:
            state["expiry_monotonic_ns"] = _stamp(at).monotonic_ns
        with pytest.raises(ValueError):
            subject._time(state, proof, _stamp(at))
    finally:
        q.source._SOURCES.pop(owner)


def test_cash_window_cutoff_includes_positive_rows_and_exact_utc_midnight():
    row = Data(at=NOW - timedelta(days=7) + timedelta(microseconds=12), net=999)
    facts = Data(cashflows=(row,))
    assert q.final_recheck.next_cash_window_change_v1(
        facts, evaluated_at=NOW
    ) == NOW + timedelta(microseconds=13)
    assert q.final_recheck.next_cash_window_change_v1(
        Data(cashflows=()), evaluated_at=NOW
    ) == datetime(2026, 11, 21, tzinfo=UTC)
