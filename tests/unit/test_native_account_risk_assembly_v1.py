"""Synthetic orchestration with real original-row windows/current consumer.

The fixed source hooks below explicitly replace native acquisition, SQL, OS and
custody providers. They never create a production history/coverage owner or
native receipt. Real B1/B3 parsing and financial/current replay still execute.
These tests establish control flow and falsifications, not native acceptance.
"""

import asyncio
import copy
import json
import pickle
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.source_primitives import sha
from app.trade_qualification import account_history_source_bundle_v1 as bundle
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_risk_windows_v1 as windows
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import native_account_risk_assembly_v1 as assembly
from tests.unit import test_account_lifecycle_history as original
from tests.unit.research.test_demo_session_route_preflight import _client
from tests.unit.test_account_current_source_v7 import (
    current_plan_v7,
    recorded_v7,
    verify_v7,
)
from tests.unit.test_account_history_risk_v8 import inputs
from tests.unit.test_qualification_account_capture import MAIN_UID, NOW, UID


@asynccontextmanager
async def synthetic_assembly(monkeypatch, tmp_path):
    """Provider-only seam; no opaque source registry is written by this test."""
    private = _client()
    owners, bindings, calls = set(), set(), []
    durable = ["synthetic_B1_B3_retained", "synthetic_coverage_journal_retained"]
    flags = {
        "inventory": 0,
        "cloned_factory": False,
        "cancel": False,
        "release_coverage_error": False,
        "mutate_on_coverage_replay": None,
    }
    with private.pinned_read_session() as lease:
        old_regional, old_credentials = original.regional, original.credentials

        def declared_regional(**changes):
            return old_regional(
                session_binding_id=lease.binding_id,
                registration_evidence_sha256="e" * 64,
                **changes,
            )

        def declared_credentials(**changes):
            return old_credentials(session_binding_id=lease.binding_id, **changes)

        # Bind original source plans before their synthetic acquisition; never
        # relabel/re-hash a packet after it has been observed.
        monkeypatch.setattr(original, "regional", declared_regional)
        monkeypatch.setattr(original, "credentials", declared_credentials)
        sources, arguments = await inputs(monkeypatch)
        chain, _ = await recorded_v7(
            monkeypatch,
            offset_seconds=2002,
            plan_changes={
                "session_binding_id": lease.binding_id,
                "registration_evidence_sha256": "e" * 64,
            },
        )
        checked_at = NOW + timedelta(seconds=2010)
        reference = observed.source_reference(chain)
        packet = lifecycle._packet(chain, reference)
        source = SimpleNamespace(
            packet=packet,
            reference=reference,
            current_receipt_json=verify_v7(chain, validated_at=checked_at).receipt_json,
        )
        identity = json.loads(source.current_receipt_json)["identity"]
        history_packet = lifecycle._packet(*sources[-1])
        facts = bundle.HistorySourceFactsV1(
            b"synthetic retained bundle provider",
            sha(b"synthetic retained bundle provider"),
            "b" * 64,
            arguments["scope"],
            sources,
            arguments["index_batches"],
            sources[-1][1],
            history_packet,
            None,
        )
        stamp = {
            "utc_ns": int(checked_at.timestamp()) * 1_000_000_000,
            "monotonic_ns": 1_000_000_000,
        }
        view = {
            "current": source,
            "identity": identity,
            "scope": facts.scope,
            "control": SimpleNamespace(
                state=SimpleNamespace(emergency_stop=True, arm_request_id=None)
            ),
            "coverage": SimpleNamespace(journal_sha256="c" * 64),
            "activation_complete": stamp.copy(),
            "checked": stamp.copy(),
            "custody_id": "d" * 32,
        }

        def inventory():
            return {"source_sha256": "a" * 64, "revision": flags["inventory"]}

        # A real, unconnected SQLAlchemy factory satisfies constructor type
        # checks. Every source/SQL provider remains explicitly synthetic.
        engine = create_async_engine(
            "postgresql+asyncpg://synthetic@localhost/synthetic", poolclass=NullPool
        )
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        custody_factory = async_sessionmaker(
            engine, autoflush=False, expire_on_commit=False
        )

        def factory_pin(value):
            return value, engine, False, False

        def keyed(key):
            assert key is assembly._HOOK_KEY

        async def acquire_history(**kwargs):
            assert kwargs["read_session"] is lease
            calls.append("acquire_history")
            owner = object()
            owners.add(owner)
            return owner

        async def acquire_coverage(**kwargs):
            assert kwargs["account_session"]._read_session is lease
            calls.append("acquire_coverage")
            if flags["cancel"]:
                raise asyncio.CancelledError
            owner = object()
            owners.add(owner)
            return owner

        def move(kind, owner, *, key):
            keyed(key)
            assert owner in owners
            owners.remove(owner)
            moved = object()
            bindings.add(moved)
            calls.append("move_" + kind)
            return moved

        def release(kind, value, *, key):
            keyed(key)
            owners.discard(value)
            bindings.discard(value)
            calls.append("release_" + kind)
            if kind == "coverage" and flags["release_coverage_error"]:
                raise RuntimeError("synthetic_cleanup_failure")

        def history_state(value, *, key):
            keyed(key)
            assert value in bindings
            if flags["cloned_factory"]:
                raise history.NativeHistorySourceError(
                    "native_history_native_factory_changed"
                )
            return {"synthetic_provider": True}

        def coverage_state(value):
            assert value in bindings
            return {"synthetic_provider": True}, stamp.copy()

        async def replay_history(value, *, key):
            history_state(value, key=key)
            calls.append("replay_history")
            return facts, stamp.copy()

        async def replay_coverage(value, *, key):
            keyed(key)
            coverage_state(value)
            calls.append("replay_coverage")
            if flags["mutate_on_coverage_replay"] == calls.count("replay_coverage"):
                flags["cloned_factory"] = True
            return view

        monkeypatch.setattr(assembly, "_package_inventory", inventory)
        monkeypatch.setattr(history, "_factory_pin", factory_pin)
        monkeypatch.setattr(
            history, "acquire_native_history_source_facts_v1", acquire_history
        )
        monkeypatch.setattr(
            coverage, "acquire_controlled_demo_writer_coverage_v1", acquire_coverage
        )
        monkeypatch.setattr(
            history,
            "_take_financial_assembly_v1",
            lambda owner, *, key: move("history", owner, key=key),
        )
        monkeypatch.setattr(
            coverage,
            "_take_financial_assembly_v1",
            lambda owner, *, key: move("coverage", owner, key=key),
        )
        monkeypatch.setattr(
            history,
            "_release_financial_assembly_v1",
            lambda value, *, key: release("history", value, key=key),
        )
        monkeypatch.setattr(
            coverage,
            "_release_financial_assembly_v1",
            lambda value, *, key: release("coverage", value, key=key),
        )
        monkeypatch.setattr(history, "_financial_state_v1", history_state)
        monkeypatch.setattr(coverage, "_check_binding", coverage_state)
        monkeypatch.setattr(history, "_replay_financial_assembly_v1", replay_history)
        monkeypatch.setattr(coverage, "_replay_financial_assembly_v1", replay_coverage)
        # Admit only these declared synthetic providers, before acquisition.
        monkeypatch.setattr(
            assembly,
            "_FUNCTION_PINS",
            tuple(
                (module, name, getattr(module, name))
                for module, name, _ in assembly._FUNCTION_PINS
            ),
        )
        plan = current_plan_v7(
            session_binding_id=lease.binding_id,
            registration_evidence_sha256="e" * 64,
        )
        args = {
            "read_session": lease,
            "private_client": private,
            "scope_config": history.NativeHistoryScopeV1(
                UID, MAIN_UID, "e" * 64, plan.leverage_instrument_ids
            ),
            "current_account_plan": plan,
            "session_factory": factory,
            "custody_session_factory": custody_factory,
            "history_raw_root": tmp_path / "history-raw",
            "history_companion_root": tmp_path / "history-companion",
            "account_proof_root": tmp_path / "account-raw",
            "account_native_root": tmp_path / "account-native",
        }
        initial = set(assembly._OWNERS)
        try:
            yield SimpleNamespace(
                args=args,
                calls=calls,
                durable=durable,
                owners=owners,
                bindings=bindings,
                flags=flags,
                view=view,
                facts=facts,
            )
        finally:
            for owner in set(assembly._OWNERS) - initial:
                assembly._release(assembly._OWNERS.pop(owner))
            await engine.dispose()


@pytest.mark.asyncio
async def test_synthetic_positive_native_financial_driver_uses_original_math_and_current_consumer(
    monkeypatch,
    tmp_path,
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        assert io.calls[:4] == [
            "acquire_history",
            "move_history",
            "acquire_coverage",
            "move_coverage",
        ]
        result = await assembly.consume_owned_native_account_risk_assembly_v1(owner)
        window = json.loads(result.financial_windows.receipt_json)
        evaluated = json.loads(result.risk_evaluation.receipt_json)
        receipt = json.loads(result.receipt_json)
        assert window["original_fill_cashflows"] and window["retained_source_index"]
        assert (
            evaluated["financial_window_receipt_sha256"]
            == result.financial_windows.receipt_sha256
        )
        assert (
            evaluated["current_packet_sha256"]
            == io.view["current"].reference.packet_sha256
        )
        assert windows._read_ratio(evaluated["equity"]) > 0
        assert (
            window["daily_net_pnl"]
            is window["weekly_net_pnl"]
            is window["loss_streak"]
            is None
        )
        assert evaluated["complete_net_daily_loss_pct"] is evaluated["snapshot"] is None
        assert "historical_native_prefix_unproven" in window["missing_obligations"]
        assert receipt["bootstrap_only"] is True and receipt["admission"] == "DENY"
        assert not any(
            (
                result.account_complete,
                result.account_revision_published,
                result.native_history_prefix_verified,
                result.post_g12_eligible,
                result.flat_start_permission,
                result.execution_authority,
            )
        )
        assert not io.owners and not io.bindings and len(io.durable) == 2
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_risk_assembly_v1(owner)


@pytest.mark.asyncio
async def test_call_time_burn_precedes_first_await_and_never_remints(
    monkeypatch, tmp_path
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        pending = assembly.consume_owned_native_account_risk_assembly_v1(owner)
        assert owner not in assembly._OWNERS
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_risk_assembly_v1(owner)
        pending.close()
        assert len(io.durable) == 2


@pytest.mark.asyncio
async def test_foreign_task_consumption_burns_owner_and_both_sources(
    monkeypatch, tmp_path
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)

        async def foreign():
            assembly.consume_owned_native_account_risk_assembly_v1(owner)

        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="context_changed"
        ):
            await asyncio.create_task(foreign())
        assert owner not in assembly._OWNERS and not io.bindings and not io.owners
        assert len(io.durable) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ["inventory", "fill_helper", "balance_helper", "lifecycle_helper"]
)
async def test_held_source_or_financial_leaf_substitution_burns_without_result(
    monkeypatch,
    tmp_path,
    defect,
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        if defect == "inventory":
            io.flags["inventory"] += 1
        else:
            module, name = {
                "fill_helper": (windows, "_fill_window_totals"),
                "balance_helper": (mapping, "_balance"),
                "lifecycle_helper": (lifecycle, "_source_rows"),
            }[defect]
            monkeypatch.setattr(module, name, lambda *_args, **_kwargs: None)
        with pytest.raises(assembly.NativeAccountRiskAssemblyError, match="changed"):
            assembly.consume_owned_native_account_risk_assembly_v1(owner)
        assert owner not in assembly._OWNERS and not io.bindings
        assert len(io.durable) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["acquire_history", "acquire_coverage"])
async def test_outer_failure_immediately_after_acquire_burns_unmoved_source_owner(
    monkeypatch,
    tmp_path,
    stage,
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        original_check = assembly._check

        def checked(state):
            original_check(state)
            if io.calls and io.calls[-1] == stage:
                raise assembly.NativeAccountRiskAssemblyError(
                    "synthetic_late_guard_failure"
                )

        monkeypatch.setattr(assembly, "_check", checked)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="late_guard_failure"
        ):
            await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        assert not io.owners and not io.bindings and len(io.durable) == 2
        assert "move_" + stage.removeprefix("acquire_") not in io.calls


@pytest.mark.asyncio
async def test_cloned_history_factory_changed_during_last_cross_source_await_is_rejected(
    monkeypatch,
    tmp_path,
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        # Consumption performs two full replays. Mutate the original history
        # private-factory guard at the last coverage await, after its last read.
        io.flags["mutate_on_coverage_replay"] = io.calls.count("replay_coverage") + 4
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="consumption_failed"
        ):
            await assembly.consume_owned_native_account_risk_assembly_v1(owner)
        assert owner not in assembly._OWNERS and not io.owners and not io.bindings
        assert len(io.durable) == 2


@pytest.mark.asyncio
async def test_cancelled_second_acquisition_releases_first_moved_source(
    monkeypatch, tmp_path
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        io.flags["cancel"] = True
        with pytest.raises(asyncio.CancelledError):
            await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        assert not io.owners and not io.bindings and len(io.durable) == 2


@pytest.mark.asyncio
async def test_first_release_failure_cannot_leave_other_source_held(
    monkeypatch, tmp_path
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        io.flags["inventory"] += 1
        io.flags["release_coverage_error"] = True
        with pytest.raises(RuntimeError, match="synthetic_cleanup_failure"):
            assembly.consume_owned_native_account_risk_assembly_v1(owner)
        assert owner not in assembly._OWNERS and not io.owners and not io.bindings
        assert io.calls[-2:] == ["release_coverage", "release_history"]
        assert len(io.durable) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["uid", "settlement_currency"])
async def test_changed_current_identity_cannot_reuse_original_history(
    monkeypatch, tmp_path, field
):
    async with synthetic_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        io.view["identity"][field] = "another"
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError,
            match="original_sources_mismatch|consumption_failed",
        ):
            await assembly.consume_owned_native_account_risk_assembly_v1(owner)
        assert owner not in assembly._OWNERS and not io.bindings


def test_portable_or_forged_objects_cannot_supply_an_assembly_owner():
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="not_constructible"
    ):
        assembly.OwnedNativeAccountRiskAssemblyV1()
    forged = object.__new__(assembly.OwnedNativeAccountRiskAssemblyV1)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="not_transferable"
        ):
            operation(forged)
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
    ):
        assembly.consume_owned_native_account_risk_assembly_v1(forged)


def test_materializer_is_in_exact_financial_custody_and_changed_bytes_are_rejected(
    monkeypatch,
):
    assert "app/trade_qualification/account_materializer.py" in assembly.SOURCE_PATHS
    raw_by_name = {
        name: ("source " + name).encode("ascii") for name in assembly.SOURCE_PATHS
    }
    manifest = "".join(
        f"{sha(raw)}  {name}\n" for name, raw in raw_by_name.items()
    ).encode("ascii")
    monkeypatch.setattr(
        coverage, "_package_inventory", lambda: {"synthetic_writer_guard": True}
    )

    def read_file(path, maximum):
        name = path.relative_to(assembly._ROOT).as_posix()
        raw = manifest if name == "MANIFEST.sha256" else raw_by_name[name]
        assert len(raw) <= maximum
        return raw, ("synthetic_file_identity", len(raw), sha(raw))

    monkeypatch.setattr(coverage, "_read_file", read_file)
    before = assembly._package_inventory()
    raw_by_name["app/trade_qualification/account_materializer.py"] += b"changed"
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="packaged_source_changed"
    ):
        assembly._package_inventory()
    assert before["source_sha256"]
