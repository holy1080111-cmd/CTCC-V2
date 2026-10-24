"""Synthetic provider orchestration, not TLS, PostgreSQL or native acceptance.

Only fixed source I/O providers are replaced before acquisition. Original B1/B3
rows, native positions archive parsing, V8 arithmetic, current consumer and the
assembly's actual owner/burn/context/function inventory execute unchanged.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_financial_operands_v2 as financial
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import native_account_risk_assembly_v1 as assembly
from tests.unit.test_account_financial_operands_v2 import native_position_source
from tests.unit.test_native_account_risk_assembly_v1 import synthetic_assembly
from tests.unit.test_qualification_account_capture import NOW


@asynccontextmanager
async def synthetic_original_assembly(monkeypatch, tmp_path):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        source = native_position_source(io.facts.reference)
        archive = financial.positions._verify_native_source_archive(
            source.plan,
            source.identity,
            source.archive,
            expected_sha256=source.expected_archive_sha256,
        )
        raw = canonical(
            {
                "position_plan": {
                    "registration_region": source.plan.registration_region,
                    "origin": source.plan.origin,
                    "account_binding_sha256": source.plan.account_binding_sha256,
                    "session_binding_sha256": source.plan.session_binding_sha256,
                    "created_at": source.plan.created_at.isoformat(),
                    "cutoff_u_time_ms": source.plan.cutoff_u_time_ms,
                },
                "position_identity": {
                    "uid": source.identity.uid,
                    "main_uid": source.identity.main_uid,
                    "account_level": source.identity.account_level,
                    "position_mode": source.identity.position_mode,
                    "declared_scope_currency": "USDT",
                },
            }
        )
        facts = [
            replace(io.facts, bundle_raw=raw, bundle_sha256=sha(raw), positions=archive)
        ]
        checked = NOW + timedelta(seconds=2010)
        original_history = {
            "utc_ns": int((checked - timedelta(seconds=1)).timestamp()) * 1_000_000_000,
            "monotonic_ns": 0,
        }
        original_coverage = {
            "utc_ns": original_history["utc_ns"] + 100_000_000,
            "monotonic_ns": 100_000_000,
        }
        io.view["current"].expires_at = checked + timedelta(seconds=20)
        io.view["current"].monotonic_deadline_ns = 21_000_000_000
        io.view["control"].state.lease_until = checked + timedelta(seconds=20)
        original_replay = history._replay_financial_assembly_v1
        original_state = history._financial_state_v1
        original_binding = coverage._check_binding

        async def replay_history(binding, *, key):
            _discarded, stamp = await original_replay(binding, key=key)
            return facts[0], stamp

        def history_state(binding, *, key):
            return {
                **original_state(binding, key=key),
                "original": original_history.copy(),
            }

        def coverage_state(binding):
            state, stamp = original_binding(binding)
            return {**state, "anchor": original_coverage.copy()}, stamp

        monkeypatch.setattr(history, "_replay_financial_assembly_v1", replay_history)
        monkeypatch.setattr(history, "_financial_state_v1", history_state)
        monkeypatch.setattr(coverage, "_check_binding", coverage_state)
        # Pin only declared provider replacements, before real assembly acquire.
        monkeypatch.setattr(
            assembly,
            "_FUNCTION_PINS",
            tuple(
                (module, name, getattr(module, name))
                for module, name, _value in assembly._FUNCTION_PINS
            ),
        )
        io.original_history = original_history
        io.original_coverage = original_coverage
        io.position_source = source
        io.financial_facts = facts
        yield io


@pytest.mark.asyncio
async def test_v2_same_move_original_anchors_and_bounded_net_keep_global_unknowns(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        result = await assembly.consume_owned_native_account_financial_operands_v2(
            owner
        )
        receipt = json.loads(result.receipt_json)
        data = json.loads(result.financial_operands.receipt_json)
        assert data["reconciled_run_count"] == 1
        assert data["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
            "numerator": "7",
            "denominator": "20",
        }
        assert data["observed_account_usd_settle_currency"] is None
        assert (
            receipt["financial_operands_sha256"]
            == result.financial_operands.receipt_sha256
        )
        anchors = receipt["original_source_lifetimes"]
        assert anchors["history_original_anchor"] == io.original_history
        assert anchors["coverage_original_anchor"] == io.original_coverage
        assert anchors["shorter_monotonic_deadline_ns"] == 21_000_000_000
        assert all(
            io.calls.count(name) == 1
            for name in (
                "acquire_history",
                "move_history",
                "acquire_coverage",
                "move_coverage",
            )
        )
        assert io.calls.count("replay_coverage") >= 6
        for key in (
            "funding_accrual_events",
            "complete_daily_net_pnl",
            "complete_weekly_net_pnl",
            "loss_streak_seed",
            "historical_native_hwm",
            "current_history_tail_finality",
        ):
            assert receipt[key] is None
        for key in (
            "account_complete",
            "account_revision_published",
            "clear_stop_eligible",
            "arm_eligible",
            "execution_authority",
            "historical_native_prefix_verified",
        ):
            assert receipt[key] is False
        assert receipt["admission"] == "DENY"
        assert not io.bindings and not io.owners and len(io.durable) == 2
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_risk_assembly_v1(owner)


@pytest.mark.asyncio
async def test_v2_never_awaited_consumption_burns_owner_before_dispatch(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        pending = assembly.consume_owned_native_account_financial_operands_v2(owner)
        pending.close()
        assert owner not in assembly._OWNERS
        for consume in (
            assembly.consume_owned_native_account_financial_operands_v2,
            assembly.consume_owned_native_account_risk_assembly_v1,
        ):
            with pytest.raises(
                assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
            ):
                consume(owner)
        assert len(io.durable) == 2


@pytest.mark.asyncio
async def test_v2_foreign_task_cannot_reuse_original_source_owners(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)

        async def foreign():
            return await assembly.consume_owned_native_account_financial_operands_v2(
                owner
            )

        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="context_changed"
        ):
            await asyncio.create_task(foreign())
        assert owner not in assembly._OWNERS
        assert not io.bindings and not io.owners and len(io.durable) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "inventory",
        "factory",
        "leaf_function",
        "policy",
        "position_constructor",
        "native_identity_constructor",
        "plan_constructor",
        "result_constructor",
        "utc_helper",
        "datetime_helper",
        "source_alias",
    ],
)
async def test_v2_post_acquisition_source_function_and_factory_tamper_burns(
    monkeypatch, tmp_path, change
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        if change == "inventory":
            io.flags["inventory"] += 1
        elif change == "factory":
            io.flags["cloned_factory"] = True
        elif change == "policy":
            monkeypatch.setattr(financial, "POLICY_SHA256", "f" * 64)
        elif change == "position_constructor":
            monkeypatch.setattr(financial, "NativePositionSourceV2", object())
        elif change == "native_identity_constructor":
            monkeypatch.setattr(
                financial.positions, "_NativePositionIdentityV1", object()
            )
        elif change == "plan_constructor":
            monkeypatch.setattr(
                financial.position_pages, "PositionHistoryPlanV1", object()
            )
        elif change == "result_constructor":
            monkeypatch.setattr(assembly, "NativeAccountFinancialOperandsV2", object())
        elif change == "utc_helper":
            monkeypatch.setattr(assembly, "utc_from_ns", object())
        elif change == "datetime_helper":
            monkeypatch.setattr(assembly, "datetime", object())
        elif change == "source_alias":
            monkeypatch.setattr(assembly, "financial_v2", object())
        else:
            monkeypatch.setattr(
                financial.bills, "_payment_sign", lambda value: "synthetic_changed"
            )
        with pytest.raises(
            (assembly.NativeAccountRiskAssemblyError, history.NativeHistorySourceError)
        ):
            assembly.consume_owned_native_account_financial_operands_v2(owner)
        assert owner not in assembly._OWNERS
        assert not io.bindings and len(io.durable) == 2


@pytest.mark.asyncio
async def test_v2_final_replay_factory_race_retains_already_committed_sources(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        initial = io.calls.count("replay_coverage")
        io.flags["mutate_on_coverage_replay"] = initial + 3
        with pytest.raises(
            (assembly.NativeAccountRiskAssemblyError, history.NativeHistorySourceError)
        ):
            await assembly.consume_owned_native_account_financial_operands_v2(owner)
        assert io.calls.count("replay_coverage") == initial + 3
        assert owner not in assembly._OWNERS
        assert not io.bindings and len(io.durable) == 2


@pytest.mark.asyncio
async def test_v2_expired_original_shorter_deadline_cannot_be_extended(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        io.view["current"].monotonic_deadline_ns = 1_000_000_000
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="original_deadline_expired"
        ):
            await assembly.consume_owned_native_account_financial_operands_v2(owner)
        assert owner not in assembly._OWNERS
        assert not io.bindings and len(io.durable) == 2


@pytest.mark.asyncio
async def test_v2_missing_retained_native_archive_never_issues_result(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        io.financial_facts[0] = replace(io.financial_facts[0], positions=None)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="v2_consumption_failed"
        ):
            await assembly.consume_owned_native_account_financial_operands_v2(owner)
        assert owner not in assembly._OWNERS
        assert not io.bindings and len(io.durable) == 2


def test_v2_result_or_claim_dictionary_cannot_be_consumed_as_an_owner():
    for forged in (
        {"account_complete": True},
        financial.AccountFinancialOperandsV2(b"{}"),
    ):
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_financial_operands_v2(forged)
