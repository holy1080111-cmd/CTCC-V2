"""Declared synthetic providers for the four real sampled-HWM owner hooks.

No owner registry is injected. The real V4 acquisition entry registers the
owner and the real four hooks MOVE, check, burn and release it. Acquisition,
original guard I/O, financial observation, SQL/custody providers and the
collector's value projection are explicitly synthetic here. These cases prove
hook lifetime/failure control flow, not TLS, PostgreSQL, native-clock or account
qualification. The separate balance/core tests retain the real row verifiers.
"""

import asyncio
import copy
import gc
import pickle
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from weakref import ref

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import account_history_partition as partition_db
from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import account_history_partition_v1 as partition
from app.domain.source_primitives import sha
from app.trade_qualification import account_native_sampled_hwm_v1 as collector
from app.trade_qualification import native_account_risk_assembly_v1 as assembly

REAL_CHECK_V4 = assembly._check_v4
REAL_PACKAGE_INVENTORY_V4 = assembly._package_inventory_v4
from app.trade_qualification.reservations import LedgerScope

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = LedgerScope(account_id="123456789", settlement_currency="USDT")


def pin_declared_origin_callees(monkeypatch):
    """Keep the real origin guard around the two explicitly synthetic callees."""
    monkeypatch.setattr(
        assembly,
        "_ORIGIN_PIN_FUNCTIONS_V5",
        tuple(
            (module, name, assembly._v3_callable_pin(getattr(module, name)))
            if module is assembly and name in {"_check_v4", "_move_assembly_owner_v3"}
            else (module, name, pin)
            for module, name, pin in assembly._ORIGIN_PIN_FUNCTIONS_V5
        ),
    )


@asynccontextmanager
async def declared_hook_providers(monkeypatch):
    """Finite provider seam; no real source/SQL/credential or opaque-registry writes."""
    engine = create_async_engine(
        "postgresql+asyncpg://declared_synthetic@localhost/declared_synthetic",
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    calls, retained = [], []
    flags = {"elapsed_ns": 0, "inventory": 0, "defect": None, "release_unknown": False}
    source_marker, history_marker, original_owner = object(), object(), object()
    view = {
        "current": SimpleNamespace(expires_at=NOW + timedelta(seconds=20)),
        "control": SimpleNamespace(
            state=SimpleNamespace(lease_until=NOW + timedelta(seconds=20))
        ),
    }
    facts = SimpleNamespace(
        scope=SCOPE,
        batches=(
            SimpleNamespace(
                sequence=1, event_sha256="a" * 64, document_json=b"declared-B3"
            ),
        ),
    )
    active = {}
    unknown_before = set(assembly._HWM_RELEASE_UNKNOWN)

    def stamp():
        return {
            "utc_ns": int(NOW.timestamp()) * 1_000_000_000 + flags["elapsed_ns"],
            "monotonic_ns": 1_000_000_000 + flags["elapsed_ns"],
        }

    def check(state):
        context = state["v3_context"]
        actual = assembly._context()
        if actual[:3] != context[:3] or actual[3] is not context[3]():
            assembly._deny("native_hwm_context_changed")
        if flags["elapsed_ns"] >= 60_000_000_000:
            assembly._deny("native_hwm_original_expiry")
        if flags["defect"] in ("source", "control"):
            assembly._deny("native_hwm_source_changed")
        return stamp()

    def factory_pin(value):
        assert type(value) is async_sessionmaker
        return value, engine, value.kw["autoflush"], value.kw["expire_on_commit"]

    async def databases(state):
        check(state)
        calls.append("database_identity_readback")
        if flags["defect"] == "database":
            assembly._deny("native_hwm_source_changed")

    async def acquire(**kwargs):
        assert kwargs["_history_version"] is assembly._CURRENT_HISTORY_V4
        calls.append("acquire_original_sources")
        return original_owner

    def move(owner, initial):
        assert owner is original_owner
        calls.append("move_original_sources")
        state = {
            **initial,
            "history": history_marker,
            "coverage": source_marker,
            "v3_view": view,
            "v3_anchors": {"shorter_monotonic_deadline_ns": 61_000_000_000},
        }
        assembly._transfer_native_origin_v5(initial, state)
        active["state"] = state
        return state

    async def replay(state, previous_facts=None, previous_view=None):
        check(state)
        calls.append("original_sources_control_custody_replay")
        if previous_facts is not None:
            assert previous_facts is facts and previous_view is view
        return facts, view

    def release(state):
        calls.append("release_original_sources")
        assembly._forget_native_origin_v5(state)
        state.clear()
        if flags["defect"] == "release" or flags["release_unknown"]:
            raise RuntimeError("declared synthetic release unknown")

    def inventory():
        return {"declared_synthetic_revision": flags["inventory"]}

    monkeypatch.setattr(custody, "_native_engine", lambda _engine: True)
    repository = custody.RuntimeBundleCustodyRepository(factory)
    destination = {"factory": factory, "custody": repository}

    def coverage_state(binding):
        assert binding is source_marker
        return destination, stamp()

    class PartitionProvider:
        def __init__(self):
            self.reads = 0

        async def read(self, scope):
            assert scope == partition.HistoryPartitionScopeV1(
                "demo", SCOPE.account_id, "USDT"
            )
            calls.append("exact_prefix_readback")
            self.reads += 1
            await asyncio.sleep(0)
            value = prefix(str(100 + self.reads))
            if flags["defect"] == "prefix":
                value = replace(value, b3_documents=(b"declared-CHANGED-B3",))
            elif flags["defect"] == "member":
                value = replace(
                    value, members=(replace(value.members[0], record_json=b"changed"),)
                )
            elif flags["defect"] == "same_xid":
                value = replace(value, transaction_id=active["last_xid"])
            active["last_xid"] = value.transaction_id
            return value

    partition_provider = PartitionProvider()

    def prefix(xid):
        return partition_db.HistoryPartitionReadbackV1(
            partition.HistoryPartitionScopeV1("demo", SCOPE.account_id, "USDT"),
            (
                partition_db.HistoryNativeMemberReadbackV1(
                    1, b"declared-member", "b" * 64, NOW
                ),
            ),
            (),
            (b"declared-B3",),
            1,
            "a" * 64,
            1,
            0,
            0,
            NOW,
            100,
            xid,
        )

    async def observation(state):
        calls.extend(("registration", "genesis_request", "full_prefix_financial_read"))
        retained.append("declared-registration-may-have-committed")
        state["v3_repository"] = partition_provider
        active["last_xid"] = "100"
        if flags["defect"] == "observe_cancel":
            raise asyncio.CancelledError("private arguments must not escape")
        if flags["defect"] == "observe_unknown":
            raise RuntimeError("private arguments must not escape")
        replay_value = assembly._v4_stream.OriginalHistoryFinancialStreamReplayV3(
            b'{"declared":"stream"}',
            assembly._v4_stream.OriginalHistoryStreamReplayV1(
                b'{"declared":"original"}'
            ),
            assembly._v4_financial.GlobalFinancialHistoryFactsV2(
                b'{"declared":"financial"}'
            ),
        )
        acquired = assembly._v4_reader.OriginalHistoryFinancialPgReadObservationV3(
            replay_value,
            b'{"declared":"acquired"}',
            1,
            NOW,
            NOW,
            NOW,
            NOW,
        )
        observed = assembly.NativeAccountHistoryFinancialObservationV4(
            acquired,
            view["current"],
            b'{"declared":"native"}',
        )
        return (
            observed,
            facts,
            view,
            prefix("100"),
            NOW + timedelta(seconds=20),
            stamp(),
        )

    async def seal(repo, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        assert repo is repository and expected_sha256 == sha(bundle_raw)
        calls.append("custody_commit_attempt")
        retained.append(bundle_raw)
        if flags["defect"] == "commit_unknown":
            raise RuntimeError("private SQL exception must not escape")
        if flags["defect"] == "commit_cancel":
            raise asyncio.CancelledError("private SQL exception must not escape")
        value = custody.RetainedRuntimeBundleReadback(
            "c" * 32,
            expected_sha256,
            proof_envelope_sha256,
            NOW,
            NOW,
            bundle_raw,
            100,
            "201",
            100,
            "202",
        )
        active["committed"] = value
        return (
            value  # Explicit synthetic custody provider, not an actual owned receipt.
        )

    def consume(value, *, expected_bundle_sha256, expected_proof_envelope_sha256):
        assert value is active["committed"]
        assert value.bundle_sha256 == expected_bundle_sha256
        assert value.proof_envelope_sha256 == expected_proof_envelope_sha256
        return value

    async def read_retained(repo, **kwargs):
        assert repo is repository
        calls.append("custody_separate_readback")
        if flags["defect"] == "retained_unknown":
            raise RuntimeError("private readback exception must not escape")
        return replace(active["committed"], readback_transaction_id="203")

    def project(_facts, actual_view, _stamp, observation_value):
        # The actual byte-reuse contract is checked here, but value verification
        # is NOT substituted into any production source file.
        assert _facts is facts
        source = {
            "declared_synthetic": True,
            "observation_sha256": sha(observation_value.receipt_json),
        }
        receipt = {
            "original_lifetimes": {"shorter_monotonic_deadline_ns": 61_000_000_000},
            "original_request_expires_at": (NOW + timedelta(seconds=20)).isoformat(),
        }
        return source, receipt, actual_view

    monkeypatch.setattr(assembly.history, "_factory_pin", factory_pin)
    monkeypatch.setattr(assembly, "_package_inventory_v3", inventory)
    monkeypatch.setattr(assembly, "_package_inventory_v4", inventory)
    monkeypatch.setattr(assembly, "_package_inventory_hwm_v1", inventory)
    monkeypatch.setattr(
        assembly,
        "_database_connection_pin_v4",
        lambda value: (factory_pin(value), ("same-route",)),
    )
    monkeypatch.setattr(assembly, "_check_v4", check)
    monkeypatch.setattr(assembly, "_bind_databases_v4", databases)
    monkeypatch.setattr(assembly, "_acquire_native_account_risk_assembly", acquire)
    monkeypatch.setattr(assembly, "_move_assembly_owner_v3", move)
    pin_declared_origin_callees(monkeypatch)
    monkeypatch.setattr(assembly, "_replay_partition_sources_v3", replay)
    monkeypatch.setattr(assembly, "_release_v3", release)
    monkeypatch.setattr(assembly, "_observe_financial_v4", observation)
    monkeypatch.setattr(
        assembly, "_partition_fences_v3", lambda _facts, _view: (1, 0, 0)
    )
    monkeypatch.setattr(assembly.coverage, "_check_binding", coverage_state)
    monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)
    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "read_retained_bundle", read_retained
    )
    monkeypatch.setattr(custody, "_consume_runtime_bundle_custody_receipt", consume)
    monkeypatch.setattr(collector, "_view", project)
    monkeypatch.setattr(collector, "_UNKNOWN_BINDINGS", [])
    monkeypatch.setattr(
        assembly,
        "_HWM_PIN_FUNCTIONS",
        tuple(
            (module, name, assembly._v3_callable_pin(getattr(module, name)))
            for module, name, _pin in assembly._HWM_PIN_FUNCTIONS
        ),
    )
    owners = []

    async def new_owner():
        owner = await assembly.acquire_native_account_history_financial_v4(
            read_session=None,
            private_client=None,
            scope_config=None,
            current_account_plan=None,
            session_factory=factory,
            custody_session_factory=factory,
            frame_session_factory=factory,
            frame_collector_role_oid=100,
            history_raw_root=None,
            history_companion_root=None,
            account_proof_root=None,
            account_native_root=None,
        )
        owners.append(owner)
        return owner

    try:
        owner = await new_owner()
        yield SimpleNamespace(
            owner=owner,
            new_owner=new_owner,
            native_stamp=stamp,
            factory=factory,
            repository=repository,
            destination=destination,
            calls=calls,
            flags=flags,
            retained=retained,
            active=active,
            view=view,
        )
    finally:
        flags["defect"] = None
        flags["release_unknown"] = False
        for binding, record in tuple(assembly._HWM_BINDINGS.items()):
            if record.get("state") is active.get("state"):
                assembly._HWM_BINDINGS.pop(binding, None)
                release(record["state"])
        for owner in owners:
            if owner in assembly._V4_OWNERS:
                release(assembly._V4_OWNERS.pop(owner))
        for binding in set(assembly._HWM_RELEASE_UNKNOWN) - unknown_before:
            assembly._HWM_RELEASE_UNKNOWN.pop(binding, None)
        await engine.dispose()


def take(io):
    return assembly._take_native_hwm_binding_v1(io.owner, key=collector._SOURCE_KEY)


def release(binding):
    assembly._release_native_hwm_binding_v1(binding, key=collector._SOURCE_KEY)


@pytest.mark.asyncio
async def test_call_time_real_v4_registry_move_is_exclusive(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        state = io.active["state"]
        origin = assembly._origin_record_v5(state)
        assert origin[0] == "V4_ORIGINAL_ORDER" and origin[4] == "HELD"
        binding = take(io)
        assert io.owner not in assembly._V4_OWNERS
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_history_financial_v4(io.owner)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            take(io)
        assert "registration" not in io.calls
        release(binding)
        assert io.calls.count("release_original_sources") == 1
        assert id(state) not in assembly._STATE_ORIGINS_V5


@pytest.mark.asyncio
async def test_expired_binding_still_releases_exactly_once_without_rechecking_or_renewal(
    monkeypatch,
):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        io.flags["elapsed_ns"] = 60_000_000_000
        release(binding)
        release(binding)
        assert io.calls.count("release_original_sources") == 1
        assert "registration" not in io.calls


@pytest.mark.asyncio
async def test_changed_fixed_collector_callee_burns_before_any_observation(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        monkeypatch.setattr(collector, "_view", lambda *_args: None)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="source_changed"
        ):
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        assert io.calls.count("release_original_sources") == 1
        assert "registration" not in io.calls


@pytest.mark.asyncio
async def test_wrong_key_cannot_move_the_real_owner(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="fixed_collector_required"
        ):
            assembly._take_native_hwm_binding_v1(io.owner, key=object())
        assert io.owner in assembly._V4_OWNERS
        release(take(io))


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_binding_cannot_be_constructed_copied_or_saved(operation):
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="not_constructible"
    ):
        assembly._NativeHWMBindingV1()
    shell = object.__new__(assembly._NativeHWMBindingV1)
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="not_transferable"
    ):
        operation(shell)
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="binding_unavailable"
    ):
        assembly._native_hwm_binding_v1(shell, key=collector._SOURCE_KEY)


@pytest.mark.asyncio
async def test_three_rechecks_observe_once_and_reuse_original_immutable_bytes(
    monkeypatch,
):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        observations = []
        for _ in range(3):
            _facts, _view, stamp, value = await assembly._native_hwm_recheck_v1(
                binding, key=collector._SOURCE_KEY
            )
            observations.append(value)
            io.flags["elapsed_ns"] += 1_000_000
            assert stamp["monotonic_ns"] >= 1_000_000_000
        assert io.calls.count("registration") == io.calls.count("genesis_request") == 1
        assert io.calls.count("full_prefix_financial_read") == 1
        assert io.calls.count("exact_prefix_readback") == 2
        assert observations[0] is not observations[1]
        assert all(
            value.receipt_json == observations[0].receipt_json for value in observations
        )
        assert all(
            value.original_history.acquisition_receipt_json
            == observations[0].original_history.acquisition_receipt_json
            for value in observations
        )
        assert io.calls.count("release_original_sources") == 0
        context = assembly._native_hwm_binding_v1(binding, key=collector._SOURCE_KEY)
        assert (
            context["factory"] is io.factory
            and context["custody_repository"] is io.repository
        )
        with pytest.raises(TypeError):
            context["factory"] = object()
        release(binding)
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
async def test_mutated_returned_dto_does_not_pollute_the_next_materialization(
    monkeypatch,
):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        _facts, _view, _stamp, first = await assembly._native_hwm_recheck_v1(
            binding, key=collector._SOURCE_KEY
        )
        object.__setattr__(
            first.original_history.replay.financial_facts, "receipt_json", b"changed"
        )
        object.__setattr__(first, "receipt_json", b"changed")
        _facts, _view, _stamp, second = await assembly._native_hwm_recheck_v1(
            binding, key=collector._SOURCE_KEY
        )
        assert second.receipt_json == b'{"declared":"native"}'
        assert (
            second.original_history.replay.financial_facts.receipt_json
            == b'{"declared":"financial"}'
        )
        release(binding)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["source", "control", "database", "prefix", "member", "same_xid", "inventory"],
)
async def test_changed_original_source_or_prefix_burns_without_reobservation(
    monkeypatch, defect
):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        if defect == "inventory":
            io.flags["inventory"] += 1
        else:
            io.flags["defect"] = defect
        with pytest.raises(assembly.NativeAccountRiskAssemblyError):
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        assert io.calls.count("registration") == 1
        assert io.calls.count("release_original_sources") == 1
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="binding_unavailable"
        ):
            assembly._native_hwm_binding_v1(binding, key=collector._SOURCE_KEY)
        release(binding)
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["factory", "custody"])
async def test_original_destination_identity_is_held(monkeypatch, replacement):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        if replacement == "factory":
            io.destination["factory"] = async_sessionmaker(
                io.factory.kw["bind"],
                autoflush=False,
                expire_on_commit=False,
            )
        else:
            io.destination["custody"] = custody.RuntimeBundleCustodyRepository(
                io.factory
            )
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="context_changed"
        ):
            assembly._native_hwm_binding_v1(binding, key=collector._SOURCE_KEY)
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed_ns", [20_000_000_000, 60_000_000_000])
async def test_synchronous_preseal_context_rejects_original_expiry(
    monkeypatch, elapsed_ns
):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        io.flags["elapsed_ns"] = elapsed_ns
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="original_expiry"
        ):
            assembly._native_hwm_binding_v1(binding, key=collector._SOURCE_KEY)
        assert "custody_commit_attempt" not in io.calls
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
async def test_foreign_task_recheck_burns_without_original_db_write(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)

        async def foreign():
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)

        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="context_changed"
        ):
            await asyncio.create_task(foreign())
        assert "registration" not in io.calls
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["observe_cancel", "observe_unknown"])
async def test_uncertain_first_observation_is_never_retried(monkeypatch, defect):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        io.flags["defect"] = defect
        error_type = (
            asyncio.CancelledError
            if defect == "observe_cancel"
            else assembly.NativeAccountRiskAssemblyError
        )
        with pytest.raises(error_type) as rejected:
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        if defect == "observe_cancel":
            assert rejected.value.args == ()
        assert io.retained == ["declared-registration-may-have-committed"]
        assert io.calls.count("release_original_sources") == 1
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="binding_unavailable"
        ):
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        assert io.calls.count("registration") == 1


@pytest.mark.asyncio
async def test_fourth_recheck_burns_instead_of_creating_another_lease(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        binding = take(io)
        for _ in range(3):
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="recheck_count"
        ):
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        assert io.calls.count("registration") == 1
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
async def test_collector_holds_real_binding_through_commit_and_separate_readback(
    monkeypatch,
):
    async with declared_hook_providers(monkeypatch) as io:
        result = await collector.record_owned_native_sampled_hwm_v1(io.owner)
        assert type(result) is custody.RetainedRuntimeBundleReadback
        assert io.calls.count("registration") == 1
        assert io.calls.count("custody_commit_attempt") == 1
        assert io.calls.count("custody_separate_readback") == 1
        assert io.calls.index("release_original_sources") > io.calls.index(
            "custody_separate_readback"
        )
        assert io.calls.count("release_original_sources") == 1
        assert result.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ["commit_unknown", "commit_cancel", "retained_unknown"]
)
async def test_collector_unknown_commit_or_readback_preserves_evidence_and_burns(
    monkeypatch, defect
):
    async with declared_hook_providers(monkeypatch) as io:
        io.flags["defect"] = defect
        expected = (
            asyncio.CancelledError
            if defect == "commit_cancel"
            else collector.NativeSampledHWMError
        )
        with pytest.raises(expected) as rejected:
            await collector.record_owned_native_sampled_hwm_v1(io.owner)
        if defect == "commit_cancel":
            assert rejected.value.args == ()
        assert len(io.retained) == 2
        assert io.calls.count("custody_commit_attempt") == 1
        assert io.calls.count("release_original_sources") == 1
        with pytest.raises(collector.NativeSampledHWMError):
            await collector.record_owned_native_sampled_hwm_v1(io.owner)
        assert io.calls.count("custody_commit_attempt") == 1


@pytest.mark.asyncio
async def test_unknown_release_is_retained_and_not_retried(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        state = io.active["state"]
        history_owner, coverage_owner = state["history"], state["coverage"]
        io.flags["defect"] = "release"
        with pytest.raises(
            collector.NativeSampledHWMError, match="finalization_unknown"
        ):
            await collector.record_owned_native_sampled_hwm_v1(io.owner)
        assert len(collector._UNKNOWN_BINDINGS) == 1
        binding = collector._UNKNOWN_BINDINGS[0]
        assert binding in assembly._HWM_RELEASE_UNKNOWN
        retained = assembly._HWM_RELEASE_UNKNOWN[binding]
        assert retained["state"] is state
        assert retained["release_source_state"]["history"] is history_owner
        assert retained["release_source_state"]["coverage"] is coverage_owner
        assert type(assembly._HWM_RELEASE_UNKNOWN) is dict
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="release_unknown"
        ):
            release(binding)
        assert io.calls.count("release_original_sources") == 1


@pytest.mark.asyncio
async def test_failed_move_before_return_strongly_retains_unknown_original_sources(
    monkeypatch,
):
    async with declared_hook_providers(monkeypatch) as io:
        state = io.active["state"]
        history_owner, coverage_owner = state["history"], state["coverage"]
        io.flags.update(defect="source", release_unknown=True)
        with pytest.raises(
            collector.NativeSampledHWMError, match="write_or_readback_unknown"
        ):
            await collector.record_owned_native_sampled_hwm_v1(io.owner)
        assert io.owner not in assembly._V4_OWNERS
        assert collector._UNKNOWN_BINDINGS == []  # take() never returned a binding.
        assert len(assembly._HWM_RELEASE_UNKNOWN) == 1
        binding_ref = next(ref(binding) for binding in assembly._HWM_RELEASE_UNKNOWN)
        gc.collect()
        assert binding_ref() is not None
        retained = assembly._HWM_RELEASE_UNKNOWN[binding_ref()]
        assert retained["state"] is state
        assert retained["release_source_state"]["history"] is history_owner
        assert retained["release_source_state"]["coverage"] is coverage_owner
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="release_unknown"
        ):
            release(binding_ref())
        assert io.calls.count("release_original_sources") == 1
        assert "registration" not in io.calls


@pytest.mark.asyncio
async def test_unknown_release_admission_cap_precedes_next_real_owner_move(monkeypatch):
    async with declared_hook_providers(monkeypatch) as io:
        assert assembly._HWM_MAX_BINDINGS == 16
        assert assembly._MAX_OWNERS == 256
        for index in range(16):
            io.flags["defect"] = None
            owner = io.owner if index == 0 else await io.new_owner()
            io.flags.update(defect="source", release_unknown=True)
            with pytest.raises(collector.NativeSampledHWMError):
                await collector.record_owned_native_sampled_hwm_v1(owner)
        assert len(assembly._HWM_RELEASE_UNKNOWN) == 16
        assert io.calls.count("release_original_sources") == 16
        io.flags["defect"] = None
        next_owner = await io.new_owner()
        with pytest.raises(collector.NativeSampledHWMError):
            await collector.record_owned_native_sampled_hwm_v1(next_owner)
        assert next_owner in assembly._V4_OWNERS
        assert len(assembly._HWM_RELEASE_UNKNOWN) == 16
        assert io.calls.count("release_original_sources") == 16
        assert "registration" not in io.calls


def real_v4_guard_with_declared_prior_providers(monkeypatch, io):
    # Keep real V4 closure/route/callee/data checks. The preceding V3 native I/O
    # and lifetime measurement remain explicitly synthetic, as in this fixture.
    monkeypatch.setattr(assembly, "_check_v3", lambda _state: None)
    monkeypatch.setattr(
        assembly,
        "_financial_lifetimes_v2",
        lambda _state, _view: (None, io.native_stamp()),
    )
    monkeypatch.setattr(assembly, "_check_v4", REAL_CHECK_V4)
    pin_declared_origin_callees(monkeypatch)
    monkeypatch.setattr(
        assembly,
        "_V4_PIN_FUNCTIONS",
        tuple(
            (module, name, assembly._v3_callable_pin(getattr(module, name)))
            for module, name, _pin in assembly._V4_PIN_FUNCTIONS
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["public_v4", "sampled_hwm"])
@pytest.mark.parametrize(
    "defect",
    [
        "measurement_callee",
        "measurement_constant",
        "source_inventory",
        "original_wire_event",
        "original_wire_readback",
        "original_wire_clock_predicate",
        "original_wire_journal",
        "original_wire_issuer",
        "original_wire_event_type",
    ],
)
async def test_both_entries_deny_changed_measurement_dependency_before_observation(
    monkeypatch, entry, defect
):
    async with declared_hook_providers(monkeypatch) as io:
        real_v4_guard_with_declared_prior_providers(monkeypatch, io)
        assert (
            "app/trade_qualification/account_original_balance_measurement_v1.py"
            in assembly._V4_EXTRA_PATHS
        )
        if defect == "measurement_callee":
            monkeypatch.setattr(
                assembly._hwm_measurement, "_rational", lambda *_args, **_kwargs: None
            )
        elif defect == "measurement_constant":
            monkeypatch.setattr(assembly._hwm_measurement, "MAX_MEASUREMENT_BYTES", 1)
        elif defect == "original_wire_event":
            monkeypatch.setattr(
                assembly._v4_wire.journal, "_original_wire_event", lambda *_args: None
            )
        elif defect == "original_wire_readback":
            monkeypatch.setattr(
                assembly._v4_wire.journal,
                "_original_wire_readback",
                lambda *_args: None,
            )
        elif defect == "original_wire_clock_predicate":
            monkeypatch.setattr(
                assembly._v4_wire.journal,
                "_checked_readback_clocks",
                lambda *_args: None,
            )
        elif defect == "original_wire_journal":
            monkeypatch.setattr(assembly._v4_wire, "journal", object())
        elif defect == "original_wire_issuer":
            monkeypatch.setattr(assembly._v4_wire.journal, "_ISSUER", object())
        elif defect == "original_wire_event_type":
            monkeypatch.setattr(assembly._v4_wire.journal, "_JournalEvent", object())
        else:
            io.flags["inventory"] += 1
        expected = (
            assembly.NativeAccountRiskAssemblyError
            if entry == "public_v4"
            else collector.NativeSampledHWMError
        )
        with pytest.raises(expected):
            if entry == "public_v4":
                assembly.consume_owned_native_account_history_financial_v4(io.owner)
            else:
                await collector.record_owned_native_sampled_hwm_v1(io.owner)
        assert io.owner not in assembly._V4_OWNERS
        assert io.calls.count("release_original_sources") == 1
        assert "registration" not in io.calls
        assert "custody_commit_attempt" not in io.calls


def test_real_v4_inventory_checks_measurement_source_body_against_manifest(monkeypatch):
    helper = "app/trade_qualification/account_original_balance_measurement_v1.py"
    sources = {
        name: ("declared-source:" + name).encode() for name in assembly._V4_EXTRA_PATHS
    }
    assert helper in sources
    manifest = "".join(
        sha(raw) + "  " + name + "\n" for name, raw in sources.items()
    ).encode("ascii")
    monkeypatch.setattr(
        assembly, "_package_inventory_v3", lambda: {"manifest_sha256": sha(manifest)}
    )

    def declared_file(path, _bound):
        name = path.relative_to(assembly._ROOT).as_posix()
        raw = manifest if name == "MANIFEST.sha256" else sources[name]
        return raw, (name, sha(raw))

    monkeypatch.setattr(assembly.coverage, "_read_file", declared_file)
    first = REAL_PACKAGE_INVENTORY_V4()
    assert helper in first["extra"]
    sources[helper] = b"declared-CHANGED-measurement-source"
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError,
        match="native_financial_v4_source_changed",
    ):
        REAL_PACKAGE_INVENTORY_V4()


def test_combined_ri_custody_dependencies_are_in_declared_and_structural_pin_inventories():
    """Combined current54+RI+HWM closure; no historical-source fallback."""
    names = [
        name
        for module, declared in assembly._V3_DECLARED_BINDINGS
        if module is custody
        for name in declared
    ]
    data = [
        name
        for module, declared in assembly._V3_DECLARED_DATA
        if module is custody
        for name in declared
    ]
    for name in (
        "_custody_foreign_key_identity",
        "_custody_foreign_key_identity_current0054",
    ):
        assert names.count(name) == 1
        assert (
            sum(
                module is custody and member == name
                for module, member, _pin in assembly._V3_PIN_FUNCTIONS
            )
            == 1
        )
    for name in (
        "_INCOMING_CUSTODY_FK_SQL",
        "_INCOMING_CUSTODY_FK_SQL_CURRENT0054",
        "_HEAD_LAYOUT_SQL",
        "_HEAD_SQL",
    ):
        assert names.count(name) == 1
        assert data.count(name) == 1
        assert (
            sum(
                module is custody and member == name
                for module, member, _pin in assembly._V3_PIN_DATA
            )
            == 1
        )
    assembly._v3_check_declared()


@pytest.mark.parametrize(
    ("dependency", "defect"),
    [
        pytest.param(
            "_custody_foreign_key_identity", "helper_identity", id="helper_identity"
        ),
        pytest.param("_custody_foreign_key_identity", "helper_code", id="helper_code"),
        pytest.param("_INCOMING_CUSTODY_FK_SQL", "sql_identity", id="sql_identity"),
        pytest.param("_INCOMING_CUSTODY_FK_SQL", "sql_text", id="sql_text"),
        pytest.param(
            "_custody_foreign_key_identity_current0054",
            "helper_identity",
            id="current54_helper_identity",
        ),
        pytest.param(
            "_custody_foreign_key_identity_current0054",
            "helper_code",
            id="current54_helper_code",
        ),
        pytest.param(
            "_INCOMING_CUSTODY_FK_SQL_CURRENT0054",
            "sql_identity",
            id="current54_incoming_sql_identity",
        ),
        pytest.param(
            "_INCOMING_CUSTODY_FK_SQL_CURRENT0054",
            "sql_text",
            id="current54_incoming_sql_text",
        ),
        pytest.param(
            "_HEAD_LAYOUT_SQL", "sql_identity", id="current54_head_layout_sql_identity"
        ),
        pytest.param(
            "_HEAD_LAYOUT_SQL", "sql_text", id="current54_head_layout_sql_text"
        ),
        pytest.param("_HEAD_SQL", "sql_identity", id="current54_head_sql_identity"),
        pytest.param("_HEAD_SQL", "sql_text", id="current54_head_sql_text"),
    ],
)
def test_combined_ri_custody_dependency_mutation_denies_real_declared_guard(
    monkeypatch, dependency, defect
):
    """Real guard only; never executes the changed helper or a SQL statement."""
    assembly._v3_check_declared()
    if defect == "helper_identity":
        monkeypatch.setattr(custody, dependency, lambda *_args: None)
    elif defect == "helper_code":

        def changed(role, triggers, incoming):
            return None

        monkeypatch.setattr(getattr(custody, dependency), "__code__", changed.__code__)
    elif defect == "sql_identity":
        monkeypatch.setattr(
            custody, dependency, text("SELECT 0 AS declared_changed_sql")
        )
    else:
        statement = getattr(custody, dependency)
        monkeypatch.setattr(
            statement, "text", statement.text + "\n-- declared unexecuted SQL mutation"
        )
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError,
        match="^native_financial_v3_(function_or_alias_changed|policy_or_sql_changed)$",
    ):
        assembly._v3_check_declared()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_cap", [0, 17, True])
async def test_sampled_hwm_fixed_cap_mutation_denies_before_move_and_during_binding(
    monkeypatch, changed_cap
):
    async with declared_hook_providers(monkeypatch) as io:
        assert assembly._HWM_MAX_BINDINGS == 16
        assert assembly._MAX_OWNERS == 256
        monkeypatch.setattr(assembly, "_HWM_MAX_BINDINGS", changed_cap)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="binding_bound"
        ):
            take(io)
        assert io.owner in assembly._V4_OWNERS
        assert io.calls.count("release_original_sources") == 0
        monkeypatch.setattr(assembly, "_HWM_MAX_BINDINGS", 16)
        binding = take(io)
        monkeypatch.setattr(assembly, "_HWM_MAX_BINDINGS", changed_cap)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="source_changed"
        ):
            await assembly._native_hwm_recheck_v1(binding, key=collector._SOURCE_KEY)
        assert io.calls.count("release_original_sources") == 1
        assert "registration" not in io.calls
        release(binding)
        assert io.calls.count("release_original_sources") == 1
