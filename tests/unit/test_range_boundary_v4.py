"""Restrictive V4 boundary wiring; no invented passing G8/G12 or order permit."""

import json
from datetime import timedelta, tzinfo
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.trade_evidence.gates import publish_qualification_evidence
from app.trade_evidence.models import EvidenceQualification
from app.trade_qualification import history_engine, reservations, submission_intent
from app.trade_qualification.recheck_models import freeze_recheck_origin
from tests.unit.qualification_range_fixtures import range_source, v4_inputs


@pytest.fixture(scope="module", params=("long", "short"))
def facts(request):
    source = range_source(request.param)
    args = v4_inputs(source)
    policy = args["policy"]
    # Widen only this synthetic fixture's preregistered drift limit so the
    # independent original anchor/ATR edges can be tested without a drift veto.
    args["policy"] = policy.model_copy(
        update={
            "prefix": policy.prefix.model_copy(
                update={"max_allowed_drift_bps": Decimal(10000)}
            )
        }
    )
    run = history_engine.evaluate_history_pre_evidence_v4(source.market, **args)
    assert run.prefix.prefix_complete
    assert not run.pre_evidence_complete
    assert run.result.fail_codes == ("source_data_blockers",)
    return source, args, run


def input_document(args):
    values = {
        name: value.model_dump(mode="json", round_trip=True)
        if name in ("intent", "quote", "reference", "policy", "risk_inputs")
        else sorted(value)
        if name == "consumed_event_keys"
        else value.isoformat()
        for name, value in args.items()
    }
    return submission_intent._json(values)


def sampled_quote(facts, point):
    source, _, run = facts
    prefix = run.prefix
    band = run.result.entry_zone
    tick = prefix.policy.tick_size
    anchor = Decimal(dict(prefix.detection.setup_basis)["level"])
    long = prefix.intent.direction == "long"
    price = {
        "anchor": anchor,
        "inside": band.zone_low if long else band.zone_high,
        "outer": band.zone_high if long else band.zone_low,
        "outside": band.zone_high + tick if long else band.zone_low - tick,
    }[point]
    return source.quote.quote.model_copy(
        update={
            "bid": price - tick if long else price,
            "ask": price if long else price + tick,
        }
    )


def test_exact_v4_intent_parser_and_guard_preserve_denied_source(facts):
    _, args, run = facts
    raw = input_document(args)
    parsed = submission_intent._original_inputs_document(raw)
    assert type(parsed) is submission_intent._HistoryReplayInputsV4
    assert parsed.policy == run.policy
    assert parsed.evaluated_at == args["evaluated_at"]
    assert (
        submission_intent._original_inputs_document(
            submission_intent._json(parsed.model_dump(mode="json", round_trip=True))
        )
        == parsed
    )
    submission_intent._guard(run)
    assert run.result.fail_codes == ("source_data_blockers",)
    assert not run.execution_authority


def test_incomplete_source_prefix_is_a_bounded_denial(facts):
    source, args, _ = facts
    late = dict(args, evaluated_at=source.evaluated_at + timedelta(days=1))
    run = history_engine.evaluate_history_pre_evidence_v4(source.market, **late)
    assert not run.prefix.prefix_complete
    with pytest.raises(ValueError, match="ledger_original_range_prefix_incomplete"):
        reservations._check_original_range_location(
            run, source.quote.quote, observed_at=late["evaluated_at"]
        )


@pytest.mark.parametrize("node", ("policy", "prefix"))
@pytest.mark.parametrize("marker", (None, "unknown", "v2", "v3"))
def test_original_input_versions_cannot_be_removed_or_mixed(facts, node, marker):
    _, args, _ = facts
    values = json.loads(input_document(args))
    target = values["policy"] if node == "policy" else values["policy"]["prefix"]
    if marker is None:
        target.pop("contract_version")
    else:
        target["contract_version"] = (
            marker
            if marker == "unknown"
            else f"ctcc-history-{'pre-evidence' if node == 'policy' else 'qualification-prefix'}-{marker}"
        )
    with pytest.raises(ValueError):
        submission_intent._original_inputs_document(submission_intent._json(values))


@pytest.mark.parametrize("profile", (None, "unknown", 1, False))
def test_original_input_range_profile_cannot_be_defaulted(facts, profile):
    _, args, _ = facts
    values = json.loads(input_document(args))
    if profile is None:
        values["policy"]["prefix"].pop("range_anchor_policy")
    else:
        values["policy"]["prefix"]["range_anchor_policy"] = profile
    with pytest.raises(ValueError):
        submission_intent._original_inputs_document(submission_intent._json(values))


@pytest.mark.parametrize("change", ("remove_version", "v3", "remove_profile"))
def test_evidence_union_cannot_upcast_v4(facts, change):
    _, _, run = facts
    value = run.prefix.result.model_dump(mode="json", round_trip=True)
    if change == "remove_version":
        value.pop("contract_version")
    elif change == "v3":
        value["contract_version"] = "ctcc-history-qualification-result-v3"
    else:
        value.pop("range_anchor_policy")
    with pytest.raises(ValidationError):
        TypeAdapter(EvidenceQualification).validate_json(json.dumps(value), strict=True)


@pytest.mark.parametrize("point", ("inside", "outer"))
def test_legal_sample_keeps_original_facts_but_reservation_still_denied(facts, point):
    source, _, run = facts
    before = run.model_dump_json(round_trip=True)
    quote = sampled_quote(facts, point)
    result = reservations._check_original_range_location(
        run, quote, observed_at=source.evaluated_at
    )
    assert result.reference_price == (
        quote.ask if run.prefix.intent.direction == "long" else quote.bid
    )
    assert not result.execution_authority
    with pytest.raises(ValueError, match="ledger_range_policy_incomplete"):
        reservations._check_range_reservation_admission(
            run, quote, observed_at=source.evaluated_at
        )
    assert run.model_dump_json(round_trip=True) == before


@pytest.mark.parametrize("point", ("anchor", "outside"))
def test_original_band_rejects_executable_sample_without_clipping(facts, point):
    source, _, run = facts
    quote = sampled_quote(facts, point)
    before = quote.model_dump_json(round_trip=True)
    with pytest.raises(ValueError, match="ledger_original_range_location_denied"):
        reservations._check_range_reservation_admission(
            run, quote, observed_at=source.evaluated_at
        )
    assert quote.model_dump_json(round_trip=True) == before


@pytest.mark.parametrize(
    "change", ("stale", "future", "crossed", "instrument", "report", "expired")
)
def test_changed_or_stale_quote_cannot_reach_admission(facts, change):
    source, _, run = facts
    quote = source.quote.quote
    now = source.evaluated_at
    if change == "stale":
        quote = quote.model_copy(update={"quote_time": now - timedelta(days=1)})
    elif change == "future":
        quote = quote.model_copy(update={"mark_time": now + timedelta(seconds=1)})
    elif change == "crossed":
        quote = quote.model_copy(update={"bid": quote.ask + Decimal(1)})
    elif change == "instrument":
        quote = quote.model_copy(update={"instrument_id": "ETH-USDT-SWAP"})
    elif change == "report":
        quote = quote.model_copy(update={"report_id": "another-range"})
    else:
        now = run.result.entry_zone.expires_at
    with pytest.raises(ValueError, match="ledger_original_range_location_denied"):
        reservations._check_range_reservation_admission(run, quote, observed_at=now)


@pytest.mark.parametrize("change", ("zone", "candidate", "source", "expiry"))
def test_changed_original_facts_cannot_be_used_at_reservation(facts, change):
    source, _, run = facts
    if change == "zone":
        band = run.result.entry_zone.model_copy(update={"zone_low": Decimal(1)})
        changed = run.model_copy(
            update={"result": run.result.model_copy(update={"entry_zone": band})}
        )
    elif change == "candidate":
        intent = run.prefix.intent.model_copy(update={"candidate_entry": Decimal(1)})
        changed = run.model_copy(
            update={"prefix": run.prefix.model_copy(update={"intent": intent})}
        )
    elif change == "source":
        detection = run.prefix.detection.model_copy(update={"source_sha256": "a" * 64})
        changed = run.model_copy(
            update={"prefix": run.prefix.model_copy(update={"detection": detection})}
        )
    else:
        band = run.result.entry_zone.model_copy(
            update={
                "expires_at": run.result.entry_zone.expires_at + timedelta(seconds=1)
            }
        )
        changed = run.model_copy(
            update={"result": run.result.model_copy(update={"entry_zone": band})}
        )
    with pytest.raises(ValueError):
        reservations._check_range_reservation_admission(
            changed, source.quote.quote, observed_at=source.evaluated_at
        )


@pytest.mark.parametrize("binding", (None, False, {}, "legacy-v1"))
def test_v4_cannot_select_legacy_intent_without_full_binding(facts, binding):
    _, _, run = facts
    with pytest.raises(ValueError, match="submit_range_execution_binding_required"):
        submission_intent._require_range_execution_binding(run, binding)


@pytest.mark.parametrize("target", ("run", "quote", "scalar", "clock", "binding"))
def test_new_boundaries_reject_foreign_callbacks(facts, target):
    source, _, run = facts

    class Hostile:
        def __getattribute__(self, name):
            pytest.fail("foreign attribute callback")

    quote, now = source.quote.quote, source.evaluated_at
    if target == "binding":
        with pytest.raises(ValueError):
            submission_intent._require_range_execution_binding(run, Hostile())
        return
    if target == "run":
        run = Hostile()
    elif target == "quote":
        quote = Hostile()
    elif target == "scalar":
        quote = quote.model_copy(update={"ask": Hostile()})
    else:
        now = Hostile()
    with pytest.raises(ValueError):
        reservations._check_original_range_location(run, quote, observed_at=now)


def test_foreign_clock_timezone_never_runs_callback(facts):
    source, _, run = facts

    class ForeignZone(tzinfo):
        def utcoffset(self, value):
            pytest.fail("foreign timezone callback")

    now = source.evaluated_at.replace(tzinfo=ForeignZone())
    with pytest.raises(ValueError):
        reservations._check_original_range_location(
            run, source.quote.quote, observed_at=now
        )


def test_authentic_g8_denial_never_publishes_or_freezes_origin(facts, tmp_path):
    source, args, run = facts

    def forbidden():
        pytest.fail("G8 denial reached publication")

    evidence = publish_qualification_evidence(
        tmp_path,
        source.market,
        run=run,
        **args,
        purpose="synthetic_test",
        clock=forbidden,
    )
    assert evidence.receipt is None
    assert evidence.result.fail_codes == ("source_data_blockers",)
    with pytest.raises(
        ValueError, match="recheck_origin_requires_complete_recorded_g12"
    ):
        freeze_recheck_origin(evidence)
    assert not tuple(tmp_path.iterdir())
