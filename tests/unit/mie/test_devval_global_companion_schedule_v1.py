"""Synthetic pre-window declarations and exact, offline schedule readback."""

from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.mie.validation.contracts import DatasetPartition
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    freeze_global_devval_companion_schedule_v1,
    verify_global_devval_companion_schedule_v1,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    compile_research_campaign,
)
from app.trade_qualification.public_market_collector import _digest
from tests.unit.mie.test_devval_research_blueprint import _blueprint
from tests.unit.test_qualification_public_market_collector import policy


def _inputs(*, origin="https://openapi.okx.com"):
    config = canonical({"scorer_id": "synthetic-frozen-score.v1"})
    original = _blueprint()
    blueprint = original.model_copy(
        update={
            "capture": original.capture.model_copy(update={"origin": origin}),
            "legacy_score_source_sha256": sha(config),
        }
    )
    plan = compile_research_campaign(blueprint)
    selected_policy = policy(counts=(240,) * 4)
    return {
        "blueprint": blueprint,
        "expected_blueprint_sha256": blueprint.canonical_sha256(),
        "campaign_plan": plan,
        "source_checkpoint": DevvalCandidateSourceV1(
            commit=plan.declared_source_commit,
            tree=plan.declared_source_tree,
            manifest_sha256=plan.manifest_sha256,
        ),
        "source_archive_sha256": "d" * 64,
        "market_policy": selected_policy,
        "expected_market_policy_sha256": _digest(selected_policy),
        "legacy_scorer_config_json": config,
        "decision_delay_seconds": 60,
    }


def test_compiles_exact_candidate_close_geometry_and_replays_bytes():
    inputs = _inputs()
    first = freeze_global_devval_companion_schedule_v1(**inputs)
    schedule = verify_global_devval_companion_schedule_v1(
        first.payload, expected_schedule_sha256=first.sha256, **inputs
    )
    assert schedule.canonical_json_bytes() == first.payload
    assert schedule.instrument_ids == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    assert schedule.legacy_scorer_config_sha256 == sha(
        inputs["legacy_scorer_config_json"]
    )
    assert schedule.pre_window_commit_proven is schedule.execution_authority is False
    assert schedule.independent_availability_verified is False

    plan = inputs["campaign_plan"]
    feature = inputs["blueprint"].study.feature_plan
    width = timedelta(seconds=feature.bar_horizon.seconds)
    lead = timedelta(seconds=feature.outcome_horizon_seconds)
    delay = timedelta(seconds=inputs["decision_delay_seconds"])
    for partition in (DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION):
        window = (
            plan.development
            if partition == DatasetPartition.DEVELOPMENT
            else plan.validation
        )
        slots = tuple(item for item in schedule.slots if item.partition == partition)
        assert (
            slots[0].event_closed_at == window.start_at + feature.history_bars * width
        )
        assert slots[-1].event_closed_at == window.end_at - lead
        assert all(
            item.decision_cutoff_at == item.event_closed_at + delay for item in slots
        )

    incomplete = schedule.model_copy(update={"slots": schedule.slots[1:]})
    changed_payload = incomplete.canonical_json_bytes()
    with pytest.raises(ValueError, match="schedule readback differs"):
        verify_global_devval_companion_schedule_v1(
            changed_payload, expected_schedule_sha256=sha(changed_payload), **inputs
        )


@pytest.mark.parametrize("origin", ("https://us.okx.com", "https://eea.okx.com"))
def test_non_global_campaign_refused_even_if_its_blueprint_matches(origin):
    with pytest.raises(ValueError, match="campaign or source differs"):
        freeze_global_devval_companion_schedule_v1(**_inputs(origin=origin))


def test_delay_source_and_scorer_cannot_drift():
    inputs = _inputs()
    with pytest.raises(ValueError, match="declared geometry"):
        freeze_global_devval_companion_schedule_v1(
            **(inputs | {"decision_delay_seconds": 61})
        )
    with pytest.raises(ValueError, match="legacy scorer config differs"):
        freeze_global_devval_companion_schedule_v1(
            **(
                inputs
                | {
                    "legacy_scorer_config_json": b'{"scorer_id": "synthetic-frozen-score.v1"}'
                }
            )
        )
    wrong_source = inputs["source_checkpoint"].model_copy(update={"commit": "f" * 40})
    with pytest.raises(ValueError, match="campaign or source differs"):
        freeze_global_devval_companion_schedule_v1(
            **(inputs | {"source_checkpoint": wrong_source})
        )


def test_market_policy_pin_and_five_second_limit():
    inputs = _inputs()
    with pytest.raises(ValueError, match="market policy differs"):
        freeze_global_devval_companion_schedule_v1(
            **(inputs | {"expected_market_policy_sha256": "0" * 64})
        )
    for ages in ((6, 5, 5), (5, 6, 5), (5, 5, 6)):
        widened = policy(counts=(240,) * 4, ages=ages)
        with pytest.raises(ValueError, match="market policy differs"):
            freeze_global_devval_companion_schedule_v1(
                **(
                    inputs
                    | {
                        "market_policy": widened,
                        "expected_market_policy_sha256": _digest(widened),
                    }
                )
            )


def test_mutated_overlap_and_unbound_campaign_refused():
    inputs = _inputs()
    plan = inputs["campaign_plan"]
    overlapping = plan.model_copy(
        update={
            "validation": plan.validation.model_copy(
                update={"start_at": plan.development.start_at}
            )
        }
    )
    with pytest.raises(ValueError, match="partition or cutoff invalid"):
        freeze_global_devval_companion_schedule_v1(
            **(inputs | {"campaign_plan": overlapping})
        )
    with pytest.raises(ValueError, match="campaign or source differs"):
        freeze_global_devval_companion_schedule_v1(
            **(
                inputs
                | {
                    "campaign_plan": plan.model_copy(
                        update={"campaign_id": "devval:unbound"}
                    )
                }
            )
        )
