"""Synthetic OHLC, actual evaluators; no transport or source acceptance."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import sweep_history_permission as module
from app.trade_qualification.regime_admission import evaluate_regime_admission
from tests.unit.test_qualification_regime_admission import OBSERVED, fixture

D = Decimal


def sweep_market(direction="long"):
    market = fixture("liquidity_sweep_reversal", "long")
    # Define source candles before policy evaluation. A recovery below the long
    # EMA has positive short-window derivatives while the EMA structure remains
    # neutral: latest close < EMA200, but EMA20 > EMA50. Constant closes would
    # produce zero derivative/state confidence, even with normal wick ATR.
    # This deterministic price history does not replace any computed indicator.
    for tf in ("4H", "1H"):
        rows = market.candles[tf]
        for index, row in enumerate(rows):
            if index < 80:
                close = D(110)
            elif index < 140:
                close = D(110) - D(index - 79) * D("0.26")
            elif index < 180:
                close = D("94.4")
            else:
                close = D("94.4") + D(index - 179) * D("0.05")
            rows[index] = row.model_copy(
                update={
                    "open": close,
                    "close": close,
                    "high": close + D("0.3"),
                    "low": close - D("0.3"),
                }
            )
    # Earlier actual pivot leaves structural resistance beyond the reclaim /
    # co-confirmed break, both before P and at current C. No level DTO is passed.
    row = market.candles["15m"][-20]
    market.candles["15m"][-20] = row.model_copy(update={"high": D(105)})
    if direction == "short":
        for tf, rows in market.candles.items():
            market.candles[tf] = [
                row.model_copy(
                    update={
                        "open": D(200) - row.open,
                        "close": D(200) - row.close,
                        "high": D(200) - row.low,
                        "low": D(200) - row.high,
                    }
                )
                for row in rows
            ]
        market.ticker = market.ticker.model_copy(
            update={
                "last": D(200) - market.ticker.last,
                "bid": D(200) - market.ticker.ask,
                "ask": D(200) - market.ticker.bid,
                "open_24h": D(200) - market.ticker.open_24h,
                "high_24h": D(200) - market.ticker.low_24h,
                "low_24h": D(200) - market.ticker.high_24h,
            }
        )
        old_bids, old_asks = market.order_book.bids, market.order_book.asks
        market.order_book.bids = [
            row.model_copy(update={"price": D(200) - row.price}) for row in old_asks
        ]
        market.order_book.asks = [
            row.model_copy(update={"price": D(200) - row.price}) for row in old_bids
        ]
        market.mark_price = D(200) - market.mark_price
    return market


def inputs(direction="long", **changes):
    return {
        "report_id": "synthetic-sweep-permission",
        "direction": direction,
        "observed_at": OBSERVED,
        "analysis_version": "synthetic-sweep-permission-v1",
        **changes,
    }


@pytest.mark.parametrize("direction", ("long", "short"))
def test_actual_ohlc_proves_opposed_15m_and_neutral_htf_intersection(direction):
    market = sweep_market(direction)
    before = market.model_dump_json(round_trip=True)
    result = module.evaluate_sweep_history_permission(market, **inputs(direction))
    value = json.loads(result.receipt_json)
    assert result.admitted, value
    assert result.code == "passed"
    context = value["range_context"]
    assert context["views"]["15m"]["structure"]["trend"] in (
        {"bearish", "strong_bearish"}
        if direction == "long"
        else {"bullish", "strong_bullish"}
    )
    assert all(
        context["views"][tf]["structure"]["trend"] == "neutral" for tf in ("4H", "1H")
    )
    assert D(context["mathematical_core"]["coverage"]) >= D("0.35")
    assert context["mathematical_core"]["status"] not in {
        "insufficient",
        "unstable",
    }
    assert all(
        D(context["views"][tf]["indicators"]["causal_state"]["confidence"]) > 0
        for tf in ("4H", "1H")
    )
    assert context["views"]["15m"]["structure"]["choch"] is None
    assert value["route"]["regime"] == "Unknown"
    assert value["route"]["fail_codes"] == ["range_transition_history_missing"]
    assert (
        value["detection"]["setup_type"] == "sweep_reclaim_with_co_confirmed_structure"
    )
    assert value["chronology"]["intrabar_sequence"] == "unknown"
    assert (
        value["conditions"]["required_failures"]
        == value["conditions"]["veto_failures"]
        == []
    )
    assert value["mathematical_confirmation"]["status"] not in {
        "insufficient",
        "opposed",
        "unstable",
    }
    assert value["mathematical_confirmation"]["risk_grade"] != "blocked"
    assert value["retained_analysis_blockers"] == ["multi_timeframe_not_aligned"]
    assert market.model_dump_json(round_trip=True) == before
    assert (
        module.verify_sweep_history_permission(result, market, **inputs(direction))
        == result
    )
    assert all(
        value[name] is False
        for name in (
            "execution_authority",
            "source_authenticity_verified",
            "complete_path_verified",
            "qualification_performed",
            "predictive_point_in_time_verified",
            "original_source_verified",
            "atomic_risk_reserved",
            "runtime_admissible",
        )
    )
    assert not result.execution_authority and not result.source_authenticity_verified


@pytest.mark.parametrize("direction", ("long", "short"))
def test_prefix_receipt_distinction_and_unchanged_original_expiry(direction):
    market = sweep_market(direction)
    first = module.evaluate_sweep_history_permission(market, **inputs(direction))
    later = module.evaluate_sweep_history_permission(
        market, **inputs(direction, observed_at=OBSERVED + timedelta(seconds=1))
    )
    assert first.admitted and later.admitted
    a, b = (json.loads(item.receipt_json) for item in (first, later))
    assert a["event_key"] == b["event_key"]
    assert a["chronology"]["expires_at"] == b["chronology"]["expires_at"]
    assert a["range_context"] == b["range_context"]
    assert a["range_context"]["past_first_available_at"] is None
    assert a["source_received_at"] == OBSERVED.isoformat()
    assert (
        a["range_context"]["actual_current_source_received_at"] == OBSERVED.isoformat()
    )
    assert a["range_context"]["cutoff"] < a["source_received_at"]
    assert a["range_context_sha256"] == journal.digest(
        journal.canonical(a["range_context"])
    )
    assert all(item["included_count"] >= 200 for item in a["range_context"]["lineage"])
    assert any(
        item["excluded_later_count"] > 0 for item in a["range_context"]["lineage"]
    )


@pytest.mark.parametrize("direction", ("long", "short"))
def test_old_history_contract_keeps_sweep_wait(direction):
    old = evaluate_regime_admission(
        sweep_market(direction), strategy=module.STRATEGY, **inputs(direction)
    )
    assert old.history_verified and not old.admitted
    assert old.code == "sweep_htf_policy_unspecified"


@pytest.mark.parametrize("direction", ("long", "short"))
def test_constant_htf_wicks_cannot_replace_missing_mathematical_reliability(direction):
    market = sweep_market(direction)
    for tf in ("4H", "1H"):
        market.candles[tf] = [
            row.model_copy(
                update={
                    "open": D(100),
                    "close": D(100),
                    "high": D("100.3"),
                    "low": D("99.7"),
                }
            )
            for row in market.candles[tf]
        ]
    result = module.evaluate_sweep_history_permission(market, **inputs(direction))
    value = json.loads(result.receipt_json)
    assert not result.admitted and result.code == "prior_range_safety_denied"
    assert value["range_context"]["mathematical_core"]["status"] == "insufficient"
    assert D(value["range_context"]["mathematical_core"]["coverage"]) < D("0.35")
    assert value["policy_sha256"] == module.POLICY_SHA256
    assert not result.execution_authority


@pytest.mark.parametrize(
    "mutation",
    (
        "gap",
        "duplicate",
        "future",
        "open",
        "missing_frame",
        "too_short",
        "hidden_source",
    ),
)
def test_bad_source_cannot_create_history_permission(mutation):
    market = sweep_market()
    rows = market.candles["15m"]
    if mutation == "gap":
        del rows[30]
    elif mutation == "duplicate":
        rows[30] = rows[29].model_copy()
    elif mutation == "future":
        rows[-1] = rows[-1].model_copy(update={"timestamp": OBSERVED})
    elif mutation == "open":
        rows[-1] = rows[-1].model_copy(update={"confirmed": False})
    elif mutation == "missing_frame":
        del market.candles["1H"]
    elif mutation == "too_short":
        market.candles["4H"] = market.candles["4H"][-199:]
    else:
        market.__dict__["caller_passed"] = True
    result = module.evaluate_sweep_history_permission(market, **inputs())
    assert not result.admitted
    assert not result.execution_authority


@pytest.mark.parametrize(
    "mutation",
    (
        "compression",
        "directional_htf",
        "no_far_resistance",
        "no_sweep_wick",
        "post_setup_touch",
        "extreme_5m",
    ),
)
def test_htf_event_and_existing_safety_failures_stay_denied(mutation):
    market = sweep_market()
    if mutation == "compression":
        for tf in ("4H", "1H"):
            market.candles[tf] = [
                row.model_copy(
                    update={
                        "high": row.close + D("0.05"),
                        "low": row.close - D("0.05"),
                    }
                )
                for row in market.candles[tf]
            ]
    elif mutation == "directional_htf":
        rows = market.candles["4H"]
        market.candles["4H"] = [
            row.model_copy(
                update={
                    "open": D(98) + D(i) / 100,
                    "close": D(98) + D(i) / 100,
                    "high": D("98.3") + D(i) / 100,
                    "low": D("97.7") + D(i) / 100,
                }
            )
            for i, row in enumerate(rows)
        ]
    elif mutation == "no_far_resistance":
        row = market.candles["15m"][-20]
        market.candles["15m"][-20] = row.model_copy(
            update={"high": row.close + D("0.05")}
        )
    elif mutation == "no_sweep_wick":
        row = market.candles["15m"][-1]
        market.candles["15m"][-1] = row.model_copy(update={"low": D("100.4")})
    elif mutation == "post_setup_touch":
        row = market.candles["5m"][-1]
        market.candles["5m"][-1] = row.model_copy(update={"low": D("98.4")})
    else:
        market.candles["5m"] = [
            row.model_copy(update={"high": row.close + D(2), "low": row.close - D(2)})
            for row in market.candles["5m"]
        ]
    result = module.evaluate_sweep_history_permission(market, **inputs())
    assert not result.admitted, json.loads(result.receipt_json)


def test_original_cutoff_cannot_renew_expired_or_missing_tail_event():
    result = module.evaluate_sweep_history_permission(
        sweep_market(), **inputs(observed_at=OBSERVED + timedelta(minutes=5))
    )
    assert not result.admitted
    assert result.code == "source_history_incomplete"


@pytest.mark.parametrize(
    "changes",
    (
        {"direction": "neutral"},
        {"report_id": "bad report"},
        {"analysis_version": " wrong "},
        {"expected_policy_sha256": "0" * 64},
        {"expected_policy_sha256": True},
    ),
)
def test_exact_preregistered_policy_and_coordinates(changes):
    with pytest.raises(module.SweepHistoryPermissionError):
        module.evaluate_sweep_history_permission(sweep_market(), **inputs(**changes))


def test_self_signed_or_modified_receipt_does_not_replace_source_replay():
    market = sweep_market()
    result = module.evaluate_sweep_history_permission(market, **inputs())
    value = json.loads(result.receipt_json)
    value["policy_sha256"] = "a" * 64
    forged = module.SweepHistoryPermission(journal.canonical(value))
    with pytest.raises(module.SweepHistoryPermissionError, match="replay_mismatch"):
        module.verify_sweep_history_permission(forged, market, **inputs())
    changed = market.model_copy(deep=True)
    row = changed.candles["4H"][0]
    changed.candles["4H"][0] = row.model_copy(
        update={"volume_contracts": row.volume_contracts + 1}
    )
    with pytest.raises(module.SweepHistoryPermissionError, match="replay_mismatch"):
        module.verify_sweep_history_permission(result, changed, **inputs())


def test_nonrecord_never_invokes_untrusted_result_serializer():
    class Hostile:
        def model_dump(self, **kwargs):
            raise AssertionError("must never invoke")

    with pytest.raises(
        module.SweepHistoryPermissionError, match="exact_sweep_evidence"
    ):
        module.verify_sweep_history_permission(Hostile(), sweep_market(), **inputs())
