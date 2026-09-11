"""The discussion blueprint is a traceable design index, not runtime settings."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.research.external_benchmarks.strategy_evidence import (
    PACK_FILE_ROLES,
    EvidenceAvailability,
    StrategyFamily,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def blueprint() -> dict:
    return json.loads(
        (ROOT / "config/ctcc_core_blueprint.json").read_text(encoding="utf-8")
    )


def test_blueprint_identifies_the_source_and_cross_midnight_scope(blueprint) -> None:
    assert blueprint["schema_version"] == "ctcc.core.blueprint.v1"
    source = blueprint["source_context"]
    assert source["project"] == "123"
    assert source["conversation_title"] == "CTCC 開發進度"
    assert source["conversation_id"] == "6a94fbd0-34e4-83ee-979c-519b5ecb1c66"
    assert source["timezone"] == "Asia/Taipei"
    start = datetime.fromisoformat(source["included_from"])
    end = datetime.fromisoformat(source["included_through"])
    assert start.utcoffset() == end.utcoffset() == timedelta(hours=8)
    assert (
        start.date().isoformat() == source["requested_discussion_date"] == "2026-09-09"
    )
    assert end.date().isoformat() == "2026-09-10"
    assert start < end and source["crossed_midnight"] is True
    turns = source["source_turn_ids"]
    assert len(turns) >= 10 and len(set(turns)) == len(turns)
    assert all(str(UUID(turn)) == turn for turn in turns)


def test_blueprint_is_not_an_execution_or_promotion_configuration(blueprint) -> None:
    assert blueprint["authority"] == {
        "status": "design_only_not_runtime_configuration",
        "runtime_consumers": 0,
        "execution_authority": False,
        "auto_arm": False,
        "auto_promotion": False,
        "enable_demo_writes": False,
        "enable_live_writes": False,
    }
    assert blueprint["safety_kernel_is_sole_execution_authority"] is True
    assert blueprint["safety_kernel_term"] == (
        "existing_controlled_execution_boundaries_not_a_new_single_module"
    )


def test_all_six_families_are_unique_and_not_claimed_runtime_qualified(
    blueprint,
) -> None:
    families = blueprint["strategy_families"]
    expected = {
        "trend_momentum",
        "structure_smc",
        "mean_reversion",
        "statistical_arbitrage",
        "funding_basis",
        "market_making",
    }
    assert len(families) == len(expected)
    assert {family["id"] for family in families} == expected
    assert {family["status"] for family in families} <= {
        "existing_components_not_a_qualified_family",
        "research_planned",
        "research_planned_separate_execution_review",
    }
    assert next(f for f in families if f["id"] == "market_making")["status"] == (
        "research_planned_separate_execution_review"
    )


def test_regime_routes_reference_declared_families_and_fail_closed(blueprint) -> None:
    assert blueprint["regime_route_contract"] == {
        "namespace": "design_taxonomy_not_mie_market_regime_enum",
        "validated_mapper_required": True,
        "unknown_policy": "no_trade",
        "transition_policy": "no_trade",
    }
    family_ids = {family["id"] for family in blueprint["strategy_families"]}
    routes = blueprint["regime_routes"]
    by_regime = {route["regime"]: route for route in routes}
    assert len(by_regime) == len(routes) == 6
    for route in routes:
        assert set(route["families"]) <= family_ids
        assert len(route["families"]) == len(set(route["families"]))
    assert by_regime["risk_off"]["families"] == []
    assert by_regime["risk_off"]["policy"] == "no_trade"
    assert by_regime["high_volatility"]["policy"] == "reduce_or_veto_only"
    assert (
        by_regime["low_volatility_liquid"]["policy"]
        == "separate_execution_gate_required"
    )


def test_math_and_ai_remain_downward_only_advisers(blueprint) -> None:
    boundary = blueprint["advisory_boundary"]
    assert set(boundary["mathematical_actions"]) == {"retain", "downgrade", "veto"}
    assert set(boundary["ai_rl_actions"]) == {"rank", "downgrade", "veto"}
    assert boundary["ranking_scope"] == "already_eligible_candidates_only"
    for capability in (
        "may_increase_score",
        "may_increase_risk",
        "may_increase_leverage",
        "may_change_protection",
        "may_submit_orders",
    ):
        assert boundary[capability] is False


def test_external_material_cannot_become_ctcc_evidence_by_relabeling(blueprint) -> None:
    evidence = blueprint["external_evidence"]
    assert evidence["author_namespace"] != evidence["ctcc_namespace"]
    assert evidence["author_claims_are_ctcc_results"] is False
    assert set(evidence["tiers"]) == {f"E{index}" for index in range(7)}
    assert evidence["tier_is_verification"] is False
    assert evidence["ctcc_e_label"] == (
        "separately_recomputed_ctcc_evidence_not_automatic_promotion"
    )
    assert evidence["missing_values"] == "null_with_reason_not_zero_or_guessed"
    assert {
        "original_source_url",
        "artifact_sha256",
        "sample_window",
        "metric_definition",
        "cost_assumptions",
        "license_status",
    } <= set(evidence["required_provenance"])
    assert len(evidence["pack_files"]) == len(set(evidence["pack_files"])) == 5
    assert evidence["optional_raw_file"] == "trades.csv"


def test_strategy_intake_taxonomy_and_roles_match_the_core_design(blueprint) -> None:
    evidence = blueprint["external_evidence"]
    assert evidence["metadata_intake_status"] == "strict_offline_declarations_only"
    assert evidence["source_bytes_verified_by_intake"] is False
    assert {family.value for family in StrategyFamily} == {
        family["id"] for family in blueprint["strategy_families"]
    }
    assert {tier.value for tier in EvidenceAvailability} == set(evidence["tiers"])
    assert tuple(evidence["pack_files"]) == PACK_FILE_ROLES


def test_chat_only_leads_have_no_imported_performance_claims(blueprint) -> None:
    leads = blueprint["external_evidence"]["research_leads"]
    assert len(leads) == len({lead["name"] for lead in leads}) == 6
    for lead in leads:
        assert lead["original_source_url"] is None
        assert lead["metrics"] is None
        assert lead["verified"] is False
        assert (
            lead["missing_reason"]
            == "original_source_not_retrievable_from_chat_citations"
        )


def test_continuous_demo_does_not_cancel_mie_evidence_or_execution_gates(
    blueprint,
) -> None:
    tracks = blueprint["validation_tracks"]
    demo = tracks["existing_demo"]
    assert demo["daily_forced_trade_count"] is None
    assert demo["continuous_is_unlimited_authority"] is False
    assert demo["startup_status"] == "not_activated_by_this_blueprint"
    assert {
        "explicit_arm",
        "per_run_submission_cap",
        "protection",
        "reconciliation",
        "weekly_loss",
        "drawdown",
        "portfolio_limits",
        "emergency_stop",
    } <= set(demo["required_controls"])
    assert tracks["mie"] == {
        "mode": "offline_shadow_only",
        "current_claim": "computational",
        "runtime_consumers": 0,
        "execution_authority": False,
        "exposed_retrospective_holdout_predictive_eligible": False,
    }


def test_performance_acceptance_is_more_than_win_rate_or_a_sample_target(
    blueprint,
) -> None:
    assert {
        "trade_win_rate_with_denominator",
        "sample_count",
        "test_type",
        "net_pnl",
        "net_expectancy",
        "max_drawdown",
        "fees",
        "funding",
        "spread",
        "slippage",
        "regime_attribution",
        "strategy_family_attribution",
    } <= set(blueprint["performance_requirements"])
    assert set(blueprint["market_making_metrics"]) == {
        "spread_capture",
        "inventory_pnl",
        "adverse_selection",
        "fill_quality",
    }
    assert blueprint["sample_counts_are_not_sufficiency_guarantees"] is True


def test_promotion_requires_fresh_review_and_separate_execution_acceptance(
    blueprint,
) -> None:
    assert blueprint["promotion_sequence"] == [
        "source_qualification",
        "local_reproduction",
        "leakage_checks",
        "development_validation",
        "frozen_candidate_and_protocol",
        "fresh_holdout",
        "independent_oos_review",
        "cost_adjusted_evidence",
        "separately_authorized_demo_forward",
        "champion_challenger_review",
        "soak",
        "separately_authorized_live_canary",
        "final_acceptance",
    ]


def test_runtime_source_does_not_reference_the_design_blueprint() -> None:
    violations = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "app").rglob("*.py")
        if "ctcc_core_blueprint" in path.read_text(encoding="utf-8-sig")
    ]
    assert violations == []


@pytest.mark.parametrize(
    "relative", ["README.md", "docs/architecture.md", "docs/phases.md"]
)
def test_core_spec_is_linked_from_the_existing_project_indexes(relative) -> None:
    assert "ctcc_core_master.md" in (ROOT / relative).read_text(encoding="utf-8-sig")
