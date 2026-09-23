"""Synthetic range constraints, raw-source replay and version boundaries only."""

import json
from datetime import timedelta
from decimal import Context, localcontext

import pytest
from pydantic import ValidationError

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.trade_evidence.gates import (
    HistoryEvidenceGateRunV4,
    publish_qualification_evidence,
)
from app.trade_evidence.models import EvidenceSnapshot
from app.trade_evidence.service import prepare_evidence, validate_snapshot
from app.trade_qualification import history_engine, history_prefix
from app.trade_qualification.location import (
    build_entry_zone,
    build_original_range_anchor_zone,
    evaluate_location,
)
from app.trade_qualification.service import _plain
from tests.unit.qualification_range_fixtures import range_source, v4_inputs
from tests.unit.test_qualification_location import (
    INSTRUMENT,
    REPORT,
    T0,
    D,
    event,
    executable_quote,
)


def range_event(direction="long", **updates):
    basis = {
        "zone_low": "98",
        "zone_high": "102",
        "level": "100",
        "atr": "1",
        "pivot_known_at": (T0 - timedelta(minutes=10)).isoformat(),
        "prior_side": "range_interior",
    }
    basis.update(updates.pop("basis_updates", {}))
    return event(
        direction=direction,
        strategy="range_reversal",
        setup_type="confirmed_range_edge_reclaim",
        invalidation_price=updates.pop(
            "invalidation_price", D("98" if direction == "long" else "102")
        ),
        basis_updates=basis,
        **updates,
    )


TICK = D(".1")


def zone(event, tick=TICK):
    return build_original_range_anchor_zone(
        event,
        tick_size=tick,
        max_allowed_drift_bps=D("10000"),
        expires_at=T0 + timedelta(minutes=3),
    )


@pytest.mark.parametrize("direction", ("long", "short"))
@pytest.mark.parametrize("atr", ("1", "3"))
def test_original_zone_intersection_preserves_every_other_fact(direction, atr):
    detection = range_event(direction, basis_updates={"atr": atr})
    original, _ = build_entry_zone(
        detection,
        tick_size=D(".1"),
        max_allowed_drift_bps=D("10000"),
        expires_at=T0 + timedelta(minutes=3),
    )
    narrowed, code = zone(detection)
    assert code == "passed"
    assert (
        narrowed.zone_low >= original.zone_low
        and narrowed.zone_high <= original.zone_high
    )
    assert narrowed.model_dump(
        exclude={"zone_low", "zone_high"}
    ) == original.model_dump(exclude={"zone_low", "zone_high"})
    bound = min(D(atr), D(2))
    assert (narrowed.zone_low, narrowed.zone_high) == (
        (D("100.1"), D(100) + bound)
        if direction == "long"
        else (D(100) - bound, D("99.9"))
    )


@pytest.mark.parametrize("direction", ("long", "short"))
@pytest.mark.parametrize(
    "point,passes",
    (("anchor", False), ("first_tick", True), ("outer", True), ("outside", False)),
)
def test_strict_anchor_inclusive_outer_and_fixed_quote(direction, point, passes):
    narrowed, _ = zone(range_event(direction))
    price = {
        "anchor": D(100),
        "first_tick": D("100.1" if direction == "long" else "99.9"),
        "outer": D(101 if direction == "long" else 99),
        "outside": D("101.1" if direction == "long" else "98.9"),
    }[point]
    quote = (
        executable_quote(bid=price - D(".01"), ask=price)
        if direction == "long"
        else executable_quote(bid=price, ask=price + D(".01"))
    )
    candidate = D("100.5" if direction == "long" else "99.5")
    result = evaluate_location(
        report_id=REPORT,
        instrument_id=INSTRUMENT,
        direction=direction,
        zone=narrowed,
        candidate_entry=candidate,
        quote=quote,
        current_time=T0 + timedelta(seconds=1),
    )
    assert result.passed is passes
    assert result.reference_price == price
    assert not result.execution_authority


@pytest.mark.parametrize("direction", ("long", "short"))
def test_non_grid_anchor_rounds_strictly_inward(direction):
    detection = range_event(
        direction,
        basis_updates={"level": "100.05", "zone_low": "98.05", "zone_high": "102.05"},
        invalidation_price=D("98.05" if direction == "long" else "102.05"),
    )
    result, code = zone(detection)
    assert code == "passed"
    assert (result.zone_low, result.zone_high) == (
        (D("100.1"), D(101)) if direction == "long" else (D("99.1"), D(100))
    )


@pytest.mark.parametrize("direction", ("long", "short"))
def test_no_grid_tick_never_expands_band(direction):
    result, code = zone(range_event(direction, basis_updates={"atr": ".01"}))
    assert result is None and code == "range_anchor_no_executable_tick"


@pytest.mark.parametrize(
    "field,value",
    (
        ("atr", "NaN"),
        ("atr", "0"),
        ("atr", "-1"),
        ("atr", "1e999"),
        ("level", "Infinity"),
        ("prior_side", "invented"),
        ("pivot_known_at", (T0 - timedelta(minutes=5)).isoformat()),
        ("setup_timeframe", "1H"),
    ),
)
def test_malformed_or_future_original_basis_is_denied(field, value):
    result, code = zone(range_event(basis_updates={field: value}))
    assert result is None and code != "passed"


@pytest.mark.parametrize("missing", ("level", "atr", "pivot_known_at", "prior_side"))
def test_missing_original_basis_is_denied(missing):
    result, code = zone(range_event(basis_remove=(missing,)))
    assert result is None and code != "passed"


def test_duplicate_original_basis_cannot_be_serialized_away():
    detection = range_event()
    dirty = detection.model_copy(
        update={"setup_basis": (*detection.setup_basis, ("level", "101"))}
    )
    assert zone(dirty)[0] is None


def test_hostile_object_is_rejected_before_attribute_callback():
    class Hostile:
        def __getattribute__(self, name):
            pytest.fail("unknown callback reached")

    assert zone(Hostile()) == (None, "range_anchor_source_invalid")


@pytest.fixture(scope="module", params=("long", "short"))
def source(request):
    return range_source(request.param)


def prefix(source, **changes):
    args = v4_inputs(source)
    args.pop("risk_inputs")
    args["policy"] = args["policy"].prefix
    args.update(changes)
    return history_prefix.evaluate_history_qualification_prefix_v4(
        source.market, **args
    )


def test_range_g1_g7_replays_raw_event_and_binds_profile(source):
    result = prefix(source)
    assert result.prefix_complete
    assert result.result.range_anchor_policy == "ctcc-original-range-anchor-v1"
    assert (
        result.result.gates[6].measured_values["original_event_key"]
        == result.timing.event_key
    )
    assert result.result.entry_zone.source_sha256 == result.detection.source_sha256
    assert result.result.history_admission_sha256 is None
    restored = history_prefix.HistoryQualificationPrefixRunV4.model_validate_json(
        result.model_dump_json(round_trip=True)
    )
    assert restored == result
    assert result.result.candidate_entry == source.market.ticker.last
    basis = dict(result.detection.setup_basis)
    with localcontext(Context(prec=100)):
        assert D(basis["atr"]) != D(basis["level"]) - D(basis["zone_low"])
    assert not result.execution_authority


def test_original_bad_candidate_is_not_repaired_by_favorable_quote(source):
    original = prefix(source)
    anchor = D(dict(original.detection.setup_basis)["level"])
    intent = v4_inputs(source)["intent"].model_copy(update={"candidate_entry": anchor})
    result = prefix(source, intent=intent)
    assert result.result.fail_codes == ("candidate_outside_entry_zone",)
    assert result.result.candidate_entry == anchor
    assert result.location.reference_price != anchor


def test_g8_existing_alignment_blocker_is_retained_until_separate_policy(
    source, tmp_path
):
    args = v4_inputs(source)
    result = history_engine.evaluate_history_pre_evidence_v4(source.market, **args)
    assert result.prefix.prefix_complete
    assert result.result.fail_codes == ("source_data_blockers",)
    assert not result.pre_evidence_complete

    def forbidden():
        pytest.fail("failed G8 reached publication clock")

    evidence = publish_qualification_evidence(
        tmp_path,
        source.market,
        run=result,
        **args,
        purpose="synthetic_test",
        clock=forbidden,
    )
    assert type(evidence) is HistoryEvidenceGateRunV4
    assert evidence.receipt is None and evidence.snapshot is None
    assert not list(tmp_path.iterdir())
    assert (
        HistoryEvidenceGateRunV4.model_validate_json(
            evidence.model_dump_json(round_trip=True)
        )
        == evidence
    )


def test_source_bound_snapshot_rebuilds_narrowed_zone(source):
    result = prefix(source)
    source_data = json.loads(result.data_result.source_json)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source_data["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source_data["analysis"]), strict=True
    )
    snapshot = prepare_evidence(
        market,
        analysis,
        qualification=result.result,
        detection=result.detection,
        quote=source.quote.quote,
        protection=None,
        prepared_at=source.evaluated_at + timedelta(seconds=1),
        purpose="synthetic_test",
        zone_tick_size=source.tick_size,
    )
    assert validate_snapshot(snapshot) == snapshot
    assert (
        EvidenceSnapshot.model_validate_json(snapshot.model_dump_json(round_trip=True))
        == snapshot
    )
    assert snapshot.qualification.entry_zone == result.result.entry_zone
    from app.trade_evidence.renderer import render_evidence

    rendered = dict(render_evidence(snapshot))
    assert set(rendered) == {
        "4h.png",
        "1h.png",
        "15m.png",
        "5m.png",
        "summary.png",
        "report.json",
    }
    report = json.loads(rendered["report.json"])
    assert (
        report["snapshot"]["qualification"]["range_anchor_policy"]
        == "ctcc-original-range-anchor-v1"
    )
    assert report["snapshot"]["purpose"] == "synthetic_test"
    for name, raw in rendered.items():
        if name.endswith(".png"):
            assert raw.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("field", ("contract_version", "range_anchor_policy"))
def test_v4_required_policy_markers_cannot_be_defaulted(source, field):
    policy = v4_inputs(source)["policy"].prefix
    values = _plain(policy)
    values.pop(field)
    with pytest.raises(ValidationError):
        history_prefix.HistoryQualificationPrefixPolicyV4.model_validate(
            values, strict=True
        )


def test_other_strategy_cannot_use_range_profile(source):
    intent = v4_inputs(source)["intent"].model_copy(update={"strategy": "fvg_return"})
    with pytest.raises(ValueError, match="range_only"):
        prefix(source, intent=intent)


def test_forged_range_zone_rejected_by_full_prefix_rebuild(source):
    result = prefix(source)
    values = _plain(result)
    values["result"]["entry_zone"]["zone_low"] -= D(".01")
    with pytest.raises(
        ValidationError, match="range_anchor_source_zone_binding_mismatch"
    ):
        history_prefix.HistoryQualificationPrefixRunV4.model_validate(
            values, strict=True
        )


@pytest.mark.parametrize(
    "path",
    (
        ("contract_version",),
        ("prefix", "contract_version"),
        ("prefix", "result", "contract_version"),
        ("prefix", "policy", "contract_version"),
        ("prefix", "policy", "range_anchor_policy"),
        ("prefix", "result", "range_anchor_policy"),
        ("policy", "contract_version"),
        ("policy", "prefix", "contract_version"),
        ("result", "contract_version"),
        ("result", "range_anchor_policy"),
    ),
)
@pytest.mark.parametrize("mutation", ("missing", "unknown"))
def test_all_nested_v4_markers_are_explicit(source, path, mutation):
    run = history_engine.evaluate_history_pre_evidence_v4(
        source.market, **v4_inputs(source)
    )
    values = run.model_dump(mode="json", round_trip=True)
    target = values
    for key in path[:-1]:
        target = target[key]
    if mutation == "missing":
        target.pop(path[-1])
    else:
        target[path[-1]] = "unknown"
    with pytest.raises(ValidationError):
        history_engine.HistoryPreEvidenceRunV4.model_validate_json(json.dumps(values))


@pytest.mark.parametrize("mutation", ("source", "level", "event", "entry", "expiry"))
def test_changed_original_record_is_never_reauthenticated(source, mutation):
    run = prefix(source)
    args = v4_inputs(source)
    args.pop("risk_inputs")
    args["policy"] = args["policy"].prefix
    if mutation == "source":
        market = source.market.model_copy(deep=True)
        row = market.candles["15m"][-10]
        market.candles["15m"][-10] = row.model_copy(
            update={"high": row.high + D(".001")}
        )
        with pytest.raises(ValueError):
            history_prefix.verify_history_qualification_prefix_v4(run, market, **args)
        return
    values = _plain(run)
    if mutation == "level":
        values["detection"]["setup_basis"] = tuple(
            (k, "101" if k == "level" else v)
            for k, v in values["detection"]["setup_basis"]
        )
    elif mutation == "event":
        values["detection"]["trigger"]["trigger_type"] = "invented_other_event"
    elif mutation == "entry":
        values["intent"]["candidate_entry"] += D(".01")
    else:
        values["result"]["entry_zone"]["expires_at"] += timedelta(seconds=1)
    with pytest.raises(ValueError):
        candidate = history_prefix.HistoryQualificationPrefixRunV4.model_validate(
            values, strict=True
        )
        history_prefix.verify_history_qualification_prefix_v4(
            candidate, source.market, **args
        )


@pytest.mark.parametrize(
    "mutation", ("stale", "future", "crossed", "wrong_report", "wrong_instrument")
)
def test_range_location_retains_shared_quote_fail_closed_checks(mutation):
    narrowed, _ = zone(range_event())
    q = executable_quote(bid=D("100.49"), ask=D("100.5"))
    updates = {
        "stale": {"quote_time": T0 - timedelta(seconds=60)},
        "future": {"quote_time": T0 + timedelta(seconds=2)},
        "crossed": {"bid": D(101)},
        "wrong_report": {"report_id": "different-report"},
        "wrong_instrument": {"instrument_id": "ETH-USDT-SWAP"},
    }[mutation]
    result = evaluate_location(
        report_id=REPORT,
        instrument_id=INSTRUMENT,
        direction="long",
        zone=narrowed,
        candidate_entry=D("100.5"),
        quote=q.model_copy(update=updates),
        current_time=T0 + timedelta(seconds=1),
    )
    assert not result.passed


def test_current_v4_recomputes_only_g1_g4_and_preserves_original_geometry(source):
    from app.trade_qualification.current_conditions import (
        HistoryCurrentConditionsResultV4,
        _evaluate_current_conditions,
        copy_current_conditions,
    )

    args = v4_inputs(source)
    result = _evaluate_current_conditions(
        source.market,
        intent=args["intent"],
        policy=args["policy"].prefix,
        quote=source.quote,
        reference=source.reference,
        observed_at=source.evaluated_at,
        history_pins={
            "contract_version": "ctcc-history-current-conditions-v4",
            "origin_sha256": "a" * 64,
            "original_event_key": "b" * 64,
        },
    )
    assert result.passed and len(result.gates) == 4
    assert result.result.candidate_entry == args["intent"].candidate_entry
    assert (
        result.result.entry_zone
        is result.result.trigger
        is result.result.stop_loss
        is result.result.take_profit
        is None
    )
    assert not result.execution_authority
    assert copy_current_conditions(result) == result
    assert (
        HistoryCurrentConditionsResultV4.model_validate_json(
            result.model_dump_json(round_trip=True)
        )
        == result
    )
