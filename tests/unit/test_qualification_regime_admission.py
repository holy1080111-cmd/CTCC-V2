"""Synthetic historical-route observations, not calibrated or live samples."""

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analysis.service import analyze_snapshot_at
from app.domain.market import Candle, MarketSnapshot, OrderBookLevel
from app.strategies.regime import route_regime
from app.trade_qualification import regime_admission as subject
from app.trade_qualification.regime_admission import (
    RegimeAdmissionResult,
    evaluate_regime_admission,
    validate_regime_admission,
    verify_regime_admission,
)
from app.trade_qualification.timing import TIMING_POLICIES
from tests.unit.test_qualification_events import OBSERVED, _row, source

D = Decimal
STRATEGIES = ("structure_reversal", "volatility_expansion")
AUTHORITY = (
    "execution_authority",
    "runtime_admissible",
    "source_authenticity_verified",
    "qualification_performed",
    "complete_path_verified",
    "strategy_calibrated",
)


def fixture(strategy="structure_reversal", direction="long"):
    market, _ = source(strategy)
    # Change actual source bars, not caller analysis labels. Reversal needs real
    # 15m follow-through; expansion needs the existing 4H/1H permission boundary.
    frames = (
        ("15m",)
        if strategy == "structure_reversal"
        else (("4H", "1H") if strategy == "volatility_expansion" else ())
    )
    for timeframe in frames:
        market.candles[timeframe] = [
            _row(
                row.timestamp,
                D(100) + D(index) * D(".008"),
                high=D(100) + D(index) * D(".008") + D(".15"),
                low=D(100) + D(index) * D(".008") - D(".15"),
            )
            for index, row in enumerate(market.candles[timeframe])
        ]
    if direction == "short":
        for timeframe, rows in market.candles.items():
            market.candles[timeframe] = [
                row.model_copy(
                    update={
                        "open": D(200) - row.open,
                        "close": D(200) - row.close,
                        "low": D(200) - row.high,
                        "high": D(200) - row.low,
                    }
                )
                for row in rows
            ]
    price = market.candles["5m"][-1].close
    market.ticker = market.ticker.model_copy(
        update={
            "last": price,
            "bid": price - D(".01"),
            "ask": price + D(".01"),
            "open_24h": price,
            "high_24h": price + 1,
            "low_24h": price - 1,
        }
    )
    market.order_book.bids = [OrderBookLevel(price=price - D(".01"), size=D(1))]
    market.order_book.asks = [OrderBookLevel(price=price + D(".01"), size=D(1))]
    market.mark_price = price
    return market


def inputs(strategy="structure_reversal", direction="long", **changes):
    return dict(
        report_id="synthetic-regime",
        strategy=strategy,
        direction=direction,
        observed_at=OBSERVED,
        analysis_version="synthetic-history-v1",
        **changes,
    )


def run(market=None, strategy="structure_reversal", direction="long", **changes):
    values = inputs(strategy, direction)
    values.update(changes)
    return evaluate_regime_admission(market or fixture(strategy, direction), **values)


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_real_ohlc_event_and_existing_required_conditions_admit_history_only(
    strategy, direction
):
    market = fixture(strategy, direction)
    before = deepcopy(market)
    result = run(market, strategy, direction)
    assert result.code == "passed", result
    assert result.admitted and result.history_verified
    assert result.detection.trigger.trigger_time == OBSERVED
    assert result.detection.setup_time < result.detection.trigger.trigger_time
    assert result.detection.source_timeframe == "5m"
    assert result.required_failures == result.veto_failures == ()
    assert result.permission_scope == "history_route_evidence_only"
    assert all(getattr(result, field) is False for field in AUTHORITY)
    assert market == before
    assert (
        verify_regime_admission(result, market, **inputs(strategy, direction)) == result
    )
    assert len(result.evaluation_sha256) == 64


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_legacy_route_is_not_mutated_or_implicitly_enabled(strategy, direction):
    market = fixture(strategy, direction)
    analysis = analyze_snapshot_at(
        market, evaluated_at=OBSERVED, version="synthetic-history-v1"
    )
    old = route_regime(analysis)
    result = run(market, strategy, direction)
    assert strategy not in old.allowed_strategies
    assert result.snapshot_sha256 == old.snapshot_sha256
    assert result.snapshot_allowed_strategies == old.allowed_strategies
    assert result.snapshot_fail_codes == old.fail_codes
    assert route_regime(analysis) == old


@pytest.mark.parametrize("direction", ("long", "short"))
def test_real_sweep_history_does_not_invent_missing_htf_policy(direction):
    strategy = "liquidity_sweep_reversal"
    result = run(fixture(strategy, direction), strategy, direction)
    assert result.history_verified
    assert not result.admitted
    assert result.code == "sweep_htf_policy_unspecified"
    assert dict(result.detection.setup_basis)["intrabar_sequence"] == "unknown"


@pytest.mark.parametrize(
    "strategy",
    (
        "trend_pullback",
        "breakout_continuation",
        "range_reversal",
        "fvg_return",
        "order_block_return",
    ),
)
def test_other_families_remain_with_original_router(strategy):
    result = run(fixture(), strategy)
    assert result.code == "legacy_route_only"
    assert not result.admitted and result.detection is None


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_snapshot_boolean_cannot_replace_missing_previous_history(strategy, direction):
    market = fixture(strategy, direction)
    native = "1H" if strategy == "structure_reversal" else "15m"
    if strategy == "structure_reversal":
        # Remove the opposed historical trend without erasing the latest break.
        for index in range(len(market.candles[native]) - 7):
            old = market.candles[native][index]
            market.candles[native][index] = _row(old.timestamp, D(100))
    else:
        # Prior normal volatility cannot be renamed as prior compression.
        for index in range(-20, -1):
            old = market.candles[native][index]
            market.candles[native][index] = old.model_copy(
                update={"high": old.close + D(".30"), "low": old.close - D(".30")}
            )
    result = run(market, strategy, direction)
    assert not result.admitted
    assert result.code == "setup_missing"


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_no_new_momentum_transition_is_not_an_event(strategy, direction):
    market = fixture(strategy, direction)
    native = "1H" if strategy == "structure_reversal" else "15m"
    setup_close = market.candles[native][-1].timestamp + timedelta(
        seconds=3600 if native == "1H" else 900
    )
    for index, row in enumerate(market.candles["5m"]):
        if row.timestamp >= setup_close:
            # Known pre-state remains false; no False -> True follows setup.
            old = market.candles["5m"][-2]
            market.candles["5m"][index] = old.model_copy(
                update={"timestamp": row.timestamp}
            )
    result = run(market, strategy, direction)
    assert not result.admitted
    assert result.code in {"trigger_missing", "setup_expired"}


@pytest.mark.parametrize("direction", ("long", "short"))
def test_reversal_still_requires_true_fifteen_minute_follow(direction):
    market = fixture(direction=direction)
    raw, _ = source("structure_reversal", direction)
    market.candles["15m"] = raw.candles["15m"]
    result = run(market, direction=direction)
    assert result.history_verified and not result.admitted
    assert result.code == "required_conditions_failed"
    assert "15m_follow" in result.required_failures


@pytest.mark.parametrize("direction", ("long", "short"))
def test_reversal_cannot_remove_four_hour_strong_opposition_veto(direction):
    market = fixture(direction=direction)
    for index, row in enumerate(market.candles["4H"]):
        close = D(120) - D(index) * D(".02")
        if direction == "short":
            close = D(200) - close
        market.candles["4H"][index] = _row(
            row.timestamp, close, high=close + D(".15"), low=close - D(".15")
        )
    result = run(market, direction=direction)
    assert result.history_verified and not result.admitted
    assert result.code in {
        "mathematical_veto",
        "required_conditions_failed",
        "analysis_blocked",
    }
    if result.code == "required_conditions_failed":
        assert "4h_not_opposed" in result.veto_failures


@pytest.mark.parametrize("direction", ("long", "short"))
def test_expansion_does_not_waive_original_htf_alignment(direction):
    market = fixture("volatility_expansion", direction)
    raw, _ = source("volatility_expansion", direction)
    market.candles["4H"] = raw.candles["4H"]
    market.candles["1H"] = raw.candles["1H"]
    result = run(market, "volatility_expansion", direction)
    assert result.history_verified and not result.admitted
    assert result.code == "regime_safety_blocked"


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_original_event_identity_and_expiry_survive_report_rename(strategy):
    market = fixture(strategy)
    first = run(market, strategy)
    second = run(market, strategy, report_id="renamed-report")
    assert first.event_key == second.event_key
    assert first.detection.trigger.expires_at == second.detection.trigger.expires_at
    # Capture is unchanged: no report rename can extend the fixed event window.
    deadline = OBSERVED + timedelta(
        seconds=TIMING_POLICIES[strategy].trigger_ttl_seconds
    )
    assert first.detection.trigger.expires_at == deadline
    expired = run(market, strategy, observed_at=deadline)
    assert not expired.admitted
    assert expired.code in {"trigger_expired", "confirmed_tail_missing"}


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("timeframe", ("4H", "1H", "15m", "5m"))
@pytest.mark.parametrize(
    "mutation",
    (
        "gap",
        "duplicate",
        "reverse",
        "future",
        "unconfirmed",
        "ohlc",
        "nan",
        "grid",
        "short",
    ),
)
def test_source_problems_cannot_be_repaired_by_positive_quality(
    strategy, timeframe, mutation
):
    market = fixture(strategy)
    rows = market.candles[timeframe]
    if mutation == "gap":
        rows.pop(-30)
    elif mutation == "duplicate":
        rows[-3] = deepcopy(rows[-4])
    elif mutation == "reverse":
        rows.reverse()
    elif mutation == "future":
        rows[-1] = rows[-1].model_copy(
            update={"timestamp": OBSERVED + timedelta(days=1)}
        )
    elif mutation == "unconfirmed":
        rows[-1] = rows[-1].model_copy(update={"confirmed": False})
    elif mutation == "ohlc":
        rows[-1] = rows[-1].model_copy(update={"low": rows[-1].high + 1})
    elif mutation == "nan":
        rows[-1] = rows[-1].model_copy(update={"close": D("NaN")})
    elif mutation == "grid":
        rows[-1] = rows[-1].model_copy(
            update={"timestamp": rows[-1].timestamp + timedelta(microseconds=1)}
        )
    else:
        market.candles[timeframe] = rows[-199:]
    assert all(item.ok for item in market.quality.values())
    assert not run(market, strategy).admitted


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_post_setup_wick_invalidation_cannot_be_erased_by_rebound(strategy, direction):
    market = fixture(strategy, direction)
    original = run(market, strategy, direction)
    anchor = original.detection.invalidation_price
    row = market.candles["5m"][-2]
    market.candles["5m"][-2] = row.model_copy(
        update={"low" if direction == "long" else "high": anchor}
    )
    result = run(market, strategy, direction)
    assert result.code == "trigger_invalidated"
    assert not result.admitted
    assert dict(result.detection.setup_basis)["invalidation_timeframe"] == "5m"


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_result_json_roundtrip_immutable_and_hash_replay(strategy):
    result = run(strategy=strategy)
    checked = RegimeAdmissionResult.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert checked == result
    assert validate_regime_admission(checked) == result
    with pytest.raises(ValidationError):
        result.admitted = False
    changed = result.model_copy(update={"analysis_version": "other-version"})
    with pytest.raises(ValueError, match="replay_mismatch"):
        verify_regime_admission(changed, fixture(strategy), **inputs(strategy))


@pytest.mark.parametrize("field", AUTHORITY)
@pytest.mark.parametrize("value", (True, 0, "false"))
def test_no_authority_coercion(field, value):
    result = run()
    with pytest.raises(ValueError):
        validate_regime_admission(result.model_copy(update={field: value}))


@pytest.mark.parametrize(
    "change",
    (
        {"source_sha256": " " + "a" * 64},
        {"hidden": 1},
        {"snapshot_fail_codes": (" padded ",)},
        {"snapshot_fail_codes": tuple("x" for _ in range(33))},
        {"snapshot_fail_codes": iter(("x",))},
    ),
)
def test_dirty_records_rejected_before_serialization(change):
    with pytest.raises(ValueError):
        validate_regime_admission(run().model_copy(update=change))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_non_utc_equivalent_source_is_same_evidence(strategy):
    market = fixture(strategy)
    result = run(market, strategy)
    offset = timezone(timedelta(hours=8))
    for record in (
        market,
        market.ticker,
        market.order_book,
        *(row for rows in market.candles.values() for row in rows),
    ):
        for key, value in tuple(record.__dict__.items()):
            if type(value) is datetime:
                setattr(record, key, value.astimezone(offset))
    assert run(market, strategy, observed_at=OBSERVED.astimezone(offset)) == result


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_hostile_decimal_context_does_not_change_result(strategy):
    market = fixture(strategy)
    expected = run(market, strategy)
    with localcontext() as context:
        context.prec = 6
        assert run(market, strategy) == expected


def test_opaque_and_custom_timezone_callbacks_are_not_executed():
    calls = []

    class Opaque:
        @property
        def __class__(self):
            calls.append("class")
            return Decimal

    class ClockTrap(tzinfo):
        def utcoffset(self, value):
            calls.append("offset")
            return timedelta(0)

    market = fixture()
    market.candles["5m"][-1] = market.candles["5m"][-1].model_copy(
        update={"close": Opaque()}
    )
    assert run(market).code == "source_invalid"
    market = fixture()
    market.received_at = OBSERVED.replace(tzinfo=ClockTrap())
    assert run(market).code == "source_invalid"
    with pytest.raises(ValueError):
        validate_regime_admission(
            run().model_copy(update={"condition_score": Opaque()})
        )
    assert calls == []


def test_untrusted_quality_is_ignored_not_traversed():
    class Forbidden:
        def __iter__(self):
            raise AssertionError("caller quality must not be traversed")

    market = fixture()
    expected = run(market)
    market.quality = Forbidden()
    assert run(market) == expected


@pytest.mark.parametrize("nested", (False, True))
def test_source_subclass_and_hidden_fields_are_rejected(nested):
    class SubCandle(Candle):
        pass

    class SubMarket(MarketSnapshot):
        pass

    market = fixture()
    if nested:
        market.candles["5m"][-1] = SubCandle.model_validate(
            market.candles["5m"][-1].model_dump()
        )
    else:
        market = SubMarket.model_validate(market.model_dump())
    assert run(market).code == "source_invalid"
    market = fixture().model_copy(update={"hidden": "do not drop"})
    assert run(market).code == "source_invalid"


def test_pure_module_has_no_runtime_candidate_or_network_calls():
    tree = ast.parse(Path(subject.__file__).read_text(encoding="utf-8"))
    forbidden = {
        "now",
        "utcnow",
        "get_settings",
        "build_candidate",
        "common_vetoes",
        "evaluate_qualification_prefix",
        "evaluate_pre_evidence",
        "send",
        "request",
        "post",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("seconds", (-1, 1, 600))
def test_fixed_trigger_ttl_cannot_be_shortened_or_extended_in_copied_record(
    strategy, seconds
):
    result = run(strategy=strategy)
    detection = result.detection.model_copy(
        update={
            "trigger": result.detection.trigger.model_copy(
                update={
                    "expires_at": result.detection.trigger.expires_at
                    + timedelta(seconds=seconds),
                }
            ),
        }
    )
    with pytest.raises(ValueError, match="expiry"):
        validate_regime_admission(result.model_copy(update={"detection": detection}))


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("direction", ("long", "short"))
def test_extreme_htf_cannot_be_repaired_by_valid_lower_timeframe_history(
    strategy, direction
):
    market = fixture(strategy, direction)
    for index in range(-20, 0):
        row = market.candles["4H"][index]
        market.candles["4H"][index] = row.model_copy(
            update={
                "high": row.close + D(4),
                "low": row.close - D(4),
            }
        )
    result = run(market, strategy, direction)
    assert result.history_verified
    assert not result.admitted
    assert result.code in {"regime_safety_blocked", "analysis_blocked"}


@pytest.mark.parametrize("field", ("received_at", "ticker", "order_book"))
def test_future_capture_or_component_time_rejected(field):
    market = fixture()
    if field == "received_at":
        market.received_at += timedelta(microseconds=1)
    else:
        part = getattr(market, field)
        part.timestamp += timedelta(microseconds=1)
    assert run(market).code == "source_invalid"


@pytest.mark.parametrize("field", ("symbol", "ticker", "order_book"))
def test_exact_source_instrument_and_symbol_identity(field):
    market = fixture()
    if field == "symbol":
        market.symbol = "ETH/USDT:USDT"
    else:
        getattr(market, field).instrument_id = "ETH-USDT-SWAP"
    assert run(market).code == "source_invalid"


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_does_not_use_legacy_funding_as_a_fresh_cost_permission(strategy):
    market = fixture(strategy)
    first = run(market, strategy)
    market.funding_rate = D(".1")
    second = run(market, strategy)
    assert first.admitted == second.admitted is True
    assert first.source_sha256 != second.source_sha256
    assert second.qualification_performed is False


def test_no_candidate_settings_or_common_legacy_veto_calls(monkeypatch):
    from app.analysis import service as analysis_service
    from app.config import settings
    from app.strategies import base

    def forbidden(*args, **kwargs):
        raise AssertionError("pure history component called a legacy runtime boundary")

    monkeypatch.setattr(base, "build_candidate", forbidden)
    monkeypatch.setattr(base, "common_vetoes", forbidden)
    monkeypatch.setattr(settings, "get_settings", forbidden)
    monkeypatch.setattr(analysis_service, "get_settings", forbidden)
    assert run().admitted


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_history_before_actual_snapshot_receipt_is_rejected(strategy):
    market = fixture(strategy)
    market.received_at -= timedelta(microseconds=1)
    market.ticker.timestamp = market.order_book.timestamp = market.received_at
    assert run(market, strategy).code == "source_invalid"


@pytest.mark.parametrize(
    "target", ("result", "detection", "trigger", "market", "candle", "ticker", "book")
)
@pytest.mark.parametrize(
    "metadata",
    (
        "private",
        "extra",
        "fields_set_wrong_type",
        "fields_set_unknown",
        "fields_set_opaque",
    ),
)
def test_hidden_model_metadata_is_rejected_before_serialization(target, metadata):
    calls = []

    class Opaque:
        def __hash__(self):
            calls.append("hash")
            return 1

        @property
        def __class__(self):
            calls.append("class")
            return str

        def __iter__(self):
            calls.append("iterate")
            return iter(())

    market = fixture()
    result = run(market)
    selected = {
        "result": result,
        "detection": result.detection,
        "trigger": result.detection.trigger,
        "market": market,
        "candle": market.candles["5m"][-1],
        "ticker": market.ticker,
        "book": market.order_book,
    }[target]
    if metadata == "private":
        name, value = "__pydantic_private__", {"opaque": Opaque()}
    elif metadata == "extra":
        name, value = "__pydantic_extra__", {}
    elif metadata == "fields_set_wrong_type":
        name, value = "__pydantic_fields_set__", Opaque()
    elif metadata == "fields_set_unknown":
        name, value = "__pydantic_fields_set__", {"undeclared"}
    else:
        name, value = "__pydantic_fields_set__", {Opaque()}
    object.__setattr__(selected, name, value)
    calls.clear()
    if target in {"result", "detection", "trigger"}:
        with pytest.raises(ValueError):
            validate_regime_admission(result)
        with pytest.raises(ValueError):
            verify_regime_admission(result, market, **inputs())
    else:
        assert run(market).code == "source_invalid"
        with pytest.raises(ValueError, match="replay_mismatch"):
            verify_regime_admission(result, market, **inputs())
    assert calls == []


@pytest.mark.parametrize("source_value", (False, True))
def test_opaque_dictionary_key_is_rejected_without_rehashing(source_value):
    calls = []

    class Key:
        def __hash__(self):
            calls.append("hash")
            return 42

    market = fixture()
    result = run(market)
    selected = market.candles["5m"][-1] if source_value else result.detection
    selected.__dict__[Key()] = "hidden"
    calls.clear()
    if source_value:
        assert run(market).code == "source_invalid"
    else:
        with pytest.raises(ValueError):
            validate_regime_admission(result)
    assert calls == []
