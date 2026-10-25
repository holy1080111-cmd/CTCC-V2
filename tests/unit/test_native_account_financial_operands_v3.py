"""Declared synthetic I/O, never native/TLS/PG/account or trading acceptance.

The full original history acquisition, B1/B3/bundle/companion replay, ordinary
V1/V3 MOVE/burn and existing financial math execute. Coverage and partition DB
providers are explicitly synthetic. No production owner registry is injected.
"""

import asyncio
import copy
import gc
import json
import pickle
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import account_history_partition as partition_db
from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import account_history_partition_catalog_v1 as partition_policy
from app.domain import account_history_partition_v1 as records
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_current_source_v4 as current_source
from app.trade_qualification import account_history_source_bundle_v1 as bundle
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import native_account_risk_assembly_v1 as assembly
from tests.unit.test_account_current_source_v7 import (
    current_plan_v7,
    recorded_v7,
    verify_v7,
)
from tests.unit.test_account_history_partition_v1 import (
    SCOPE,
    SOURCE_POLICY,
    original_documents,
    original_linkage,
)
from tests.unit.test_account_history_source_v1 import acquisition_fixture
from tests.unit.test_native_account_financial_operands_v2 import (
    synthetic_original_assembly,
)


def _admit_declared_synthetic_providers(monkeypatch, extra=()):
    """Test-only import pins AFTER named synthetic providers, BEFORE acquire.

    This is not production source admission, a native baseline override, or a
    gate replacement. No owner registry, formal policy or V3 guard is written.
    """
    monkeypatch.setattr(
        assembly,
        "_FUNCTION_PINS",
        tuple(
            (owner, name, getattr(owner, name))
            for owner, name, _ in assembly._FUNCTION_PINS
        ),
    )
    declared = (*assembly._V3_DECLARED_BINDINGS, *extra)
    monkeypatch.setattr(
        assembly,
        "_V3_PIN_FUNCTIONS",
        tuple(
            (owner, name, assembly._v3_callable_pin(getattr(owner, name)))
            for owner, names in declared
            for name in names
        ),
    )
    monkeypatch.setattr(
        assembly,
        "_V3_PIN_DATA",
        tuple(
            (owner, name, assembly._v3_data_pin(getattr(owner, name)))
            for owner, names in assembly._V3_DECLARED_DATA
            for name in names
        ),
    )


@asynccontextmanager
async def synthetic_full_source_v3(monkeypatch, tmp_path, *, defect=None):
    """Fresh full original history fixture; fixed synthetic coverage/DB I/O."""
    original_bootstrap_collect = bootstrap._collect_recorded
    with acquisition_fixture(monkeypatch, tmp_path) as source_io:
        lease = source_io.args["read_session"]
        with monkeypatch.context() as current_patch:
            # This independent current fixture owns its own journal/event list.
            # Do not call the enclosing history fixture wrapper, which closes
            # over a different event list; restore it on context exit.
            current_patch.setattr(
                bootstrap, "_collect_recorded", original_bootstrap_collect
            )
            current_chain, _harness = await recorded_v7(
                current_patch,
                offset_seconds=1,
                plan_changes={
                    "session_binding_id": lease.binding_id,
                    "registration_evidence_sha256": "e" * 64,
                },
            )
        source_io.samples.calls += 3000
        plan = current_plan_v7(
            session_binding_id=lease.binding_id, registration_evidence_sha256="e" * 64
        )
        calls, bindings, owners, rows = [], set(), set(), []
        state = {
            "view": None,
            "repository": None,
            "defect": defect,
            "replays": 0,
            "final_coverage_replays": 0,
            "head_advanced": False,
            "advanced_after_final_snapshot": False,
        }
        original_inventory = "a" * 64

        def inventory():
            return {"source_sha256": "b" * 64, "manifest_sha256": original_inventory}

        def inventory_v3():
            return {"source_sha256": "c" * 64, "manifest_sha256": original_inventory}

        async def acquire_coverage(**kwargs):
            assert kwargs["private_client"] is source_io.args["private_client"]
            assert kwargs["account_session"]._read_session is lease
            calls.append("acquire_coverage")
            current_ref = observed.source_reference(current_chain)
            packet = lifecycle._packet(current_chain, current_ref)
            checked = utc_from_ns(source_io.samples()["utc_ns"])
            receipt = verify_v7(current_chain, validated_at=checked).receipt_json
            current = SimpleNamespace(
                packet=packet,
                reference=current_ref,
                current_receipt_json=receipt,
                expires_at=checked + timedelta(seconds=20),
                monotonic_deadline_ns=source_io.samples()["monotonic_ns"]
                + 20_000_000_000,
            )
            audit = canonical(
                {
                    "environment": "demo",
                    "account_id": packet.plan.expected_uid,
                    "requested_settlement_currency": "USDT",
                    "currency_states": [
                        {
                            "scope": {
                                "environment": "demo",
                                "account_id": packet.plan.expected_uid,
                                "settlement_currency": "USDT",
                            },
                            "account_revision": 0,
                            "ledger_revision": 0,
                            "claims_sha256": None,
                            "active": [],
                        }
                    ],
                }
            ).decode("ascii")
            state["view"] = {
                "current": current,
                "identity": json.loads(receipt)["identity"],
                "scope": source_io.saved["facts"].scope,
                "control": SimpleNamespace(
                    state=SimpleNamespace(
                        emergency_stop=True,
                        arm_request_id=None,
                        revision=1,
                        lease_until=checked + timedelta(seconds=20),
                    )
                ),
                "coverage": SimpleNamespace(
                    journal_sha256="d" * 64,
                    audit_json=audit,
                    audit_sha256=sha(audit.encode()),
                ),
            }
            state["anchor"] = source_io.samples()
            owner = object()
            owners.add(owner)
            return owner

        def move_coverage(owner, *, key):
            assert key is assembly._HOOK_KEY and owner in owners
            owners.remove(owner)
            binding = object()
            bindings.add(binding)
            calls.append("move_coverage")
            return binding

        def coverage_state(binding):
            assert binding in bindings
            return {
                "anchor": state["anchor"],
                "account": {
                    "bundle_raw": canonical(
                        {"chain": current_source._chain_document(current_chain)}
                    )
                },
            }, source_io.samples()

        def release_coverage(binding, *, key):
            assert key is assembly._HOOK_KEY
            bindings.discard(binding)
            owners.discard(binding)
            calls.append("release_coverage")

        async def replay_coverage(binding, *, key):
            assert key is assembly._HOOK_KEY and binding in bindings
            state["replays"] += 1
            calls.append("replay_coverage")
            if state["repository"] is not None and state["repository"].reads >= 3:
                state["final_coverage_replays"] += 1
                if (
                    state["defect"] == "late_B3_at_last_coverage"
                    and state["final_coverage_replays"] == 2
                ):
                    state["head_advanced"] = True
                    calls.append(
                        "new_B3_after_final_history_before_last_coverage_return"
                    )
            if state["defect"] == "postcommit_custody" and rows:
                source_io.saved["raw"] += b" "
            if state["defect"] == "late_expiry" and rows:
                source_io.samples.calls += 61_000
            if state["defect"] == "late_control" and rows:
                state["view"]["control"].state = SimpleNamespace(
                    **{**vars(state["view"]["control"].state), "revision": 2}
                )
            return state["view"]

        original_seal = custody.RuntimeBundleCustodyRepository.seal_bundle

        async def seal(self, **kwargs):
            # Finish the extra replay before the original fixture issues its
            # short-lived receipt; publish facts only after the seal succeeds.
            facts = bundle.replay_history_source_bundle_v1(
                kwargs["bundle_raw"],
                expected_sha256=kwargs["expected_sha256"],
            )
            value = await original_seal(self, **kwargs)
            source_io.saved["facts"] = facts
            return value

        async def custody_deployment(self):
            # The original fixture's first deployment precedes B1; replay is a
            # second declared fixed DB provider, not a baseline admission.
            source_io.saved["deployment"] = True
            calls.append("custody_deployment")

        async def retained(self, *, bundle_id, expected_sha256, proof_envelope_sha256):
            calls.append("custody_full_readback")
            raw = source_io.saved["raw"]
            stamp = utc_from_ns(source_io.samples()["utc_ns"])
            return custody.RetainedRuntimeBundleReadback(
                bundle_id,
                expected_sha256,
                proof_envelope_sha256,
                stamp,
                stamp,
                raw,
            )

        class PartitionIO:
            def __init__(self, factory, *, clock):
                self.session_factory, self.clock, self._poisoned = factory, clock, False
                self.reads = 0
                state["repository"] = self

            async def verify_deployment(self):
                calls.append("partition_formal_provider")
                if state["defect"] == "formal_gate":
                    raise partition_policy.AccountHistoryPartitionError(
                        "synthetic_formal_denial"
                    )

            async def read(self, scope):
                await asyncio.sleep(0)
                self.reads += 1
                calls.append("partition_read_" + str(self.reads))
                facts = source_io.saved["facts"]
                fences = assembly._partition_fences_v3(facts, state["view"])
                return partition_db.HistoryPartitionReadbackV1(
                    scope,
                    tuple(rows),
                    (),
                    tuple(batch.document_json for batch in facts.batches),
                    facts.batches[-1].sequence + int(state["head_advanced"]),
                    "f" * 64
                    if state["head_advanced"]
                    else facts.batches[-1].event_sha256,
                    *fences,
                    self.clock(),
                    101,
                    str(self.reads),
                )

            async def read_final_source_fence(self, scope, *, capture_ids):
                calls.append("final_main_same_transaction_B1_B3_DB50")
                observed = await self.read(scope)
                facts = source_io.saved["facts"]
                original = (
                    *facts.sources,
                    (current_chain, state["view"]["current"].reference),
                )
                assert capture_ids == tuple(
                    reference.capture_id for _chain, reference in original
                )
                chains = tuple(
                    (reference.capture_id, chain) for chain, reference in original
                )
                fence = partition_db.HistoryFinalSourceFenceReadbackV1(
                    observed, chains, self.clock()
                )
                if state["defect"] == "growth_after_final_snapshot_close":
                    state["advanced_after_final_snapshot"] = True
                return fence

            def append_member_and_checkpoint(self, scope, **kwargs):
                assert not self._poisoned
                self._poisoned = True
                calls.append("partition_attempt_burn")
                registration = records.derive_native_member_registration_v1(
                    scope=scope,
                    b3_document_raw=kwargs["b3_document_raw"],
                    native_linkage_raw=kwargs["native_linkage_raw"],
                    expected_b3_policy_sha256=kwargs["expected_b3_policy_sha256"],
                    expected_b3_event_sha256=kwargs["expected_b3_event_sha256"],
                )

                async def append_once():
                    if state["defect"] == "before_commit":
                        raise RuntimeError("synthetic_before_commit")
                    facts = source_io.saved["facts"]
                    rows.append(
                        partition_db.HistoryNativeMemberReadbackV1(
                            facts.batches[-1].sequence,
                            registration.record_json,
                            registration.record_sha256,
                            self.clock(),
                        )
                    )
                    calls.extend(
                        ("main_commit", "main_close", "main_independent_readback")
                    )
                    if state["defect"] == "commit_unknown":
                        raise RuntimeError("synthetic_after_possible_commit")
                    if state["defect"] == "cancel_after_commit":
                        raise asyncio.CancelledError
                    result = await self.read(scope)
                    if state["defect"] == "wrong_readback":
                        return replace(
                            result, ledger_revision=result.ledger_revision + 1
                        )
                    return result

                return append_once()

        monkeypatch.setattr(assembly, "_package_inventory", inventory)
        monkeypatch.setattr(assembly, "_package_inventory_v3", inventory_v3)
        monkeypatch.setattr(
            coverage, "acquire_controlled_demo_writer_coverage_v1", acquire_coverage
        )
        monkeypatch.setattr(coverage, "_take_financial_assembly_v1", move_coverage)
        monkeypatch.setattr(coverage, "_check_binding", coverage_state)
        monkeypatch.setattr(
            coverage, "_release_financial_assembly_v1", release_coverage
        )
        monkeypatch.setattr(coverage, "_replay_financial_assembly_v1", replay_coverage)
        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository,
            "verify_deployment",
            custody_deployment,
        )
        monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)
        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository, "read_retained_bundle", retained
        )
        monkeypatch.setattr(
            partition_db, "AccountHistoryPartitionRepository", PartitionIO
        )
        _admit_declared_synthetic_providers(
            monkeypatch,
            (
                (
                    PartitionIO,
                    (
                        "__init__",
                        "read",
                        "verify_deployment",
                        "append_member_and_checkpoint",
                        "read_final_source_fence",
                    ),
                ),
            ),
        )
        args = {
            **{
                name: value
                for name, value in source_io.args.items()
                if name not in {"raw_root", "companion_root"}
            },
            "current_account_plan": plan,
            "history_raw_root": source_io.args["raw_root"],
            "history_companion_root": source_io.args["companion_root"],
            "account_proof_root": tmp_path / "current-raw",
            "account_native_root": tmp_path / "current-native",
        }
        initial = set(assembly._V3_OWNERS)
        try:
            yield SimpleNamespace(
                args=args,
                source=source_io,
                calls=calls,
                rows=rows,
                state=state,
                bindings=bindings,
                owners=owners,
            )
        finally:
            for owner in set(assembly._V3_OWNERS) - initial:
                assembly._release_v3(assembly._V3_OWNERS.pop(owner))


@pytest.mark.asyncio
async def test_v3_full_original_history_move_core_registration_and_inert_math(
    monkeypatch, tmp_path
):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        assert type(owner) is assembly.OwnedNativeAccountFinancialOperandsV3
        assert owner not in assembly._OWNERS
        result = await assembly.consume_owned_native_account_financial_operands_v3(
            owner
        )
        receipt = json.loads(result.receipt_json)
        linkage = json.loads(json.loads(io.rows[0].record_json)["native_linkage_json"])
        original = json.loads(io.source.saved["raw"])
        assert (
            linkage["native_invocation_id"]
            == original["temporal"]["stage"]["invocation_id"]
        )
        assert linkage["original_started"] == original["temporal"]["original_started"]
        assert linkage["bundle_sha256"] == sha(io.source.saved["raw"])
        assert receipt["latest_native_registration_sha256"] == io.rows[0].record_sha256
        assert io.calls.count("partition_attempt_burn") == 1
        assert io.calls.index("main_close") < io.calls.index(
            "main_independent_readback"
        )
        assert io.calls.count("custody_full_readback") >= 4
        assert not io.bindings and not io.owners
        assert len(io.rows) == 1
        for field in (
            "account_complete",
            "account_revision_published",
            "native_history_prefix_verified",
            "clear_stop_eligible",
            "arm_eligible",
            "execution_authority",
        ):
            assert getattr(result, field) is False
        for field in (
            "funding_accrual_events",
            "complete_daily_net_pnl",
            "complete_weekly_net_pnl",
            "loss_streak_seed",
            "historical_native_hwm",
            "current_history_tail_finality",
        ):
            assert receipt[field] is None
        assert receipt["admission"] == "DENY"
        assert receipt["capture33_streaming_implemented"] is False
        assert receipt["cross_database_atomicity"] is False


@pytest.mark.asyncio
async def test_v3_call_time_burn_and_close_release_without_first_await(
    monkeypatch, tmp_path
):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        pending = assembly.consume_owned_native_account_financial_operands_v3(owner)
        assert owner not in assembly._V3_OWNERS
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_financial_operands_v3(owner)
        pending.close()
        assert not io.bindings and not io.owners and not io.rows


@pytest.mark.asyncio
async def test_v3_foreign_task_burns_before_database_call(monkeypatch, tmp_path):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)

        async def foreign():
            return await assembly.consume_owned_native_account_financial_operands_v3(
                owner
            )

        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="context_changed"
        ):
            await asyncio.create_task(foreign())
        assert owner not in assembly._V3_OWNERS
        assert not io.bindings and not io.owners and not io.rows


@pytest.mark.asyncio
async def test_v3_cancellation_after_possible_commit_keeps_record_and_burns(
    monkeypatch, tmp_path
):
    async with synthetic_full_source_v3(
        monkeypatch, tmp_path, defect="cancel_after_commit"
    ) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        with pytest.raises(asyncio.CancelledError):
            await assembly.consume_owned_native_account_financial_operands_v3(owner)
        assert len(io.rows) == 1 and io.calls.count("partition_attempt_burn") == 1
        assert not io.bindings and not io.owners
        assert owner not in assembly._V3_OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_v3_move", "after_v3_move"])
async def test_v3_late_acquisition_failure_releases_original_sources_and_owner(
    monkeypatch, tmp_path, phase
):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        old_owners = set(assembly._OWNERS)
        original_acquire = assembly.acquire_native_account_risk_assembly_v1
        original_move = assembly._move_assembly_owner_v3

        async def fail_before_move(**kwargs):
            owner = await original_acquire(**kwargs)
            monkeypatch.setattr(partition_db, "_APPEND_SQL", "SELECT 1")
            return owner

        def fail_after_move(owner, initial):
            result = original_move(owner, initial)
            monkeypatch.setattr(partition_db, "_APPEND_SQL", "SELECT 1")
            return result

        if phase == "before_v3_move":
            monkeypatch.setattr(
                assembly, "acquire_native_account_risk_assembly_v1", fail_before_move
            )
        else:
            monkeypatch.setattr(assembly, "_move_assembly_owner_v3", fail_after_move)
        _admit_declared_synthetic_providers(monkeypatch)
        with pytest.raises(assembly.NativeAccountRiskAssemblyError):
            await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        assert set(assembly._OWNERS) == old_owners
        assert not io.bindings and not io.owners and not io.rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "formal_gate",
        "before_commit",
        "commit_unknown",
        "wrong_readback",
        "postcommit_custody",
        "late_expiry",
        "late_control",
    ],
)
async def test_v3_unknown_or_changed_source_retains_rows_and_never_retries(
    monkeypatch, tmp_path, defect
):
    async with synthetic_full_source_v3(monkeypatch, tmp_path, defect=defect) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        with pytest.raises(assembly.NativeAccountRiskAssemblyError):
            await assembly.consume_owned_native_account_financial_operands_v3(owner)
        assert io.calls.count("partition_attempt_burn") <= 1
        assert len(io.rows) == (0 if defect in {"formal_gate", "before_commit"} else 1)
        assert not io.bindings and not io.owners
        assert "attempt.claim.json" in io.source.files["raw"]
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_financial_operands_v3(owner)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "function",
        "leaf",
        "SQL",
        "policy",
        "alias",
        "original_factory",
        "custody_factory",
        "private_factory",
        "companion",
        "raw",
    ],
)
async def test_v3_held_union_tamper_burns_before_append(monkeypatch, tmp_path, change):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        if change == "function":
            monkeypatch.setattr(assembly, "_partition_fences_v3", lambda *_: (1, 0, 0))
        elif change == "leaf":
            monkeypatch.setattr(records, "_reference", lambda value: value)
        elif change == "SQL":
            monkeypatch.setattr(partition_db, "_APPEND_SQL", "SELECT 1")
        elif change == "policy":
            monkeypatch.setattr(partition_policy, "PG17_DEPARSED_BASELINE", "f" * 64)
        elif change == "alias":
            monkeypatch.setattr(partition_db, "canonical", lambda value: b"{}")
        elif change in {"original_factory", "custody_factory"}:
            key = (
                "session_factory"
                if change == "original_factory"
                else "custody_session_factory"
            )
            io.args[key].kw["autoflush"] = True
        elif change == "private_factory":
            # Access is test-only tamper, never the owner-issuing path.
            held = assembly._V3_OWNERS[owner]
            native = history._financial_state_v1(
                held["history"], key=assembly._HOOK_KEY
            )
            native["factory"].kw["autoflush"] = True
        elif change == "companion":
            io.source.files["clock"]["host-after.json"] += b" "
        elif change == "raw":
            io.source.files["raw"]["source.bundle.json"] += b" "
        with pytest.raises(
            (assembly.NativeAccountRiskAssemblyError, history.NativeHistorySourceError)
        ):
            await assembly.consume_owned_native_account_financial_operands_v3(owner)
        assert not io.rows
        assert not io.bindings and not io.owners


@pytest.mark.asyncio
async def test_v3_rejects_old_minimal_v2_bundle_without_fabricating_temporal_proof(
    monkeypatch, tmp_path
):
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        # V2's deliberately minimal bundle is never upgraded into a native link.
        assert "temporal" not in json.loads(io.financial_facts[0].bundle_raw)
        manifest = "a" * 64
        monkeypatch.setattr(
            assembly,
            "_package_inventory",
            lambda: {"source_sha256": "b" * 64, "manifest_sha256": manifest},
        )
        monkeypatch.setattr(
            assembly,
            "_package_inventory_v3",
            lambda: {"source_sha256": "c" * 64, "manifest_sha256": manifest},
        )
        _admit_declared_synthetic_providers(monkeypatch)
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        try:
            with pytest.raises(KeyError, match="temporal"):
                assembly._latest_native_linkage_v3(
                    assembly._V3_OWNERS[owner], io.financial_facts[0]
                )
        finally:
            assembly._release_v3(assembly._V3_OWNERS.pop(owner))


@pytest.mark.asyncio
async def test_v3_keeps_existing_reducer_exact_closed_run_arithmetic_separate_from_complete_net(
    monkeypatch, tmp_path
):
    # The V3 call target is this exact unchanged reducer. This test independently
    # exercises its populated original-source arithmetic, not native V3 IO.
    async with synthetic_original_assembly(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v1(**io.args)
        result = await assembly.consume_owned_native_account_financial_operands_v2(
            owner
        )
        receipt = json.loads(result.financial_operands.receipt_json)
        assert receipt["reconciled_run_count"] == 1
        assert receipt["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
            "numerator": "7",
            "denominator": "20",
        }
        full = json.loads(result.receipt_json)
        assert full["complete_daily_net_pnl"] is full["complete_weekly_net_pnl"] is None
        assert full["loss_streak_seed"] is full["historical_native_hwm"] is None


def test_v3_exact32_checkpoint_keeps_original_missing_members_explicit():
    documents = original_documents(32)
    latest = SimpleNamespace(sequence=32, event_sha256=sha(documents[-1]))
    facts = SimpleNamespace(scope=SCOPE, batches=(latest,))
    linkage = records.canonical(original_linkage(documents[-1]))
    registration = records.derive_native_member_registration_v1(
        scope=SCOPE,
        b3_document_raw=documents[-1],
        native_linkage_raw=linkage,
        expected_b3_policy_sha256=SOURCE_POLICY,
        expected_b3_event_sha256=sha(documents[-1]),
    )
    before = SimpleNamespace(members=(), checkpoints=(), b3_documents=documents)
    checkpoint = assembly._partition_checkpoint_v3(
        facts, before, registration, SOURCE_POLICY
    )
    decoded = json.loads(checkpoint)
    # The pure checkpoint itself defines the field schema; never assume a
    # portable storage checkpoint proves a historical native prefix.
    assert decoded["source_authenticity_verified"] is False
    assert decoded["execution_authority"] is False
    assert decoded["missing_native_registration_sequences"] == list(range(1, 32))
    assert decoded["native_registration_count"] == 1
    replayed = records.replay_partition_checkpoints_v1(
        scope=SCOPE,
        checkpoints=(checkpoint,),
        expected_checkpoint_sha256=sha(checkpoint),
        expected_b3_policy_sha256=SOURCE_POLICY,
        expected_b3_head_sha256=sha(documents[-1]),
    )
    assert replayed.record_json == checkpoint


def test_v3_stored_latest_is_not_adopted_and_capture33_does_not_reset():
    documents = original_documents(64)  # noqa: F841 -- preserve reviewed fixture construction.
    facts = SimpleNamespace(scope=SCOPE, batches=(SimpleNamespace(sequence=64),))
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="streaming_source_unavailable"
    ):
        assembly._partition_checkpoint_v3(
            facts,
            SimpleNamespace(members=()),
            SimpleNamespace(record_json=b"{}"),
            SOURCE_POLICY,
        )
    facts = SimpleNamespace(scope=SCOPE, batches=(SimpleNamespace(sequence=1),))
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="existing_member_not_adoptable"
    ):
        assembly._partition_checkpoint_v3(
            facts,
            SimpleNamespace(members=(SimpleNamespace(b3_sequence=1),)),
            SimpleNamespace(record_json=b"{}"),
            SOURCE_POLICY,
        )


@pytest.mark.parametrize(
    "operation",
    [
        lambda: assembly.OwnedNativeAccountFinancialOperandsV3(),
        lambda: copy.copy(
            object.__new__(assembly.OwnedNativeAccountFinancialOperandsV3)
        ),
        lambda: pickle.dumps(
            object.__new__(assembly.OwnedNativeAccountFinancialOperandsV3)
        ),
    ],
)
def test_v3_no_constructor_copy_pickle_or_saved_row_can_issue(operation):
    with pytest.raises(assembly.NativeAccountRiskAssemblyError):
        operation()
    with pytest.raises(
        assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
    ):
        assembly.consume_owned_native_account_financial_operands_v3(
            {"passed": True, "member": b"{}"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("temporary", [True, False])
async def test_v3_temporary_and_retained_await_keep_wrapper_through_real_yields(
    monkeypatch, tmp_path, temporary
):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        if temporary:
            result = await assembly.consume_owned_native_account_financial_operands_v3(
                owner
            )
        else:
            pending = assembly.consume_owned_native_account_financial_operands_v3(owner)
            result = await pending
            with pytest.raises(
                assembly.NativeAccountRiskAssemblyError, match="already_used"
            ):
                await pending
        assert type(result) is assembly.NativeAccountFinancialOperandsV3
        assert json.loads(result.receipt_json)["final_main_snapshot_only"] is True
        assert io.calls.count("final_main_same_transaction_B1_B3_DB50") == 1
        assert not io.bindings and not io.owners


@pytest.mark.asyncio
async def test_v3_unawaited_drop_burns_and_releases_both_original_sources(
    monkeypatch, tmp_path
):
    async with synthetic_full_source_v3(monkeypatch, tmp_path) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        pending = assembly.consume_owned_native_account_financial_operands_v3(owner)
        assert owner not in assembly._V3_OWNERS
        del pending
        gc.collect()
        assert not io.bindings and not io.owners and not io.rows
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="owner_unavailable"
        ):
            assembly.consume_owned_native_account_financial_operands_v3(owner)


@pytest.mark.asyncio
async def test_v3_B3_growth_in_last_coverage_await_is_refused_by_last_main_fence(
    monkeypatch, tmp_path
):
    """Synthetic DB scheduling, paired with a real PG legal-B3 mechanism case."""
    async with synthetic_full_source_v3(
        monkeypatch, tmp_path, defect="late_B3_at_last_coverage"
    ) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="partition_sources_changed"
        ):
            await assembly.consume_owned_native_account_financial_operands_v3(owner)
        assert "new_B3_after_final_history_before_last_coverage_return" in io.calls
        assert io.calls.count("final_main_same_transaction_B1_B3_DB50") == 1
        assert io.calls.count("partition_attempt_burn") == 1 and len(io.rows) == 1
        assert not io.bindings and not io.owners


@pytest.mark.asyncio
async def test_v3_growth_during_final_close_remains_original_bounded_inert_snapshot(
    monkeypatch, tmp_path
):
    async with synthetic_full_source_v3(
        monkeypatch, tmp_path, defect="growth_after_final_snapshot_close"
    ) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        result = await assembly.consume_owned_native_account_financial_operands_v3(
            owner
        )
        receipt = json.loads(result.receipt_json)
        assert io.state["advanced_after_final_snapshot"] is True
        assert receipt["final_main_snapshot_only"] is True
        assert receipt["freshness_or_revision_renewed"] is False
        assert receipt["cross_database_atomicity"] is False
        assert receipt["admission"] == "DENY" and result.execution_authority is False
        # No later coverage/custody/database query follows the coupled final
        # main fence. Future reservation/intent authority must reread revisions.
        final_index = io.calls.index("final_main_same_transaction_B1_B3_DB50")
        assert not any(
            call.startswith("replay_") or call.startswith("custody_")  # noqa: PIE810 -- preserve trace assertion.
            for call in io.calls[final_index + 1 :]
        )


@pytest.mark.asyncio
async def test_v3_cancellation_after_wrapper_suspends_preserves_committed_record(
    monkeypatch, tmp_path
):
    async with synthetic_full_source_v3(
        monkeypatch, tmp_path, defect="cancel_after_commit"
    ) as io:
        owner = await assembly.acquire_native_account_risk_assembly_v3(**io.args)
        pending = assembly.consume_owned_native_account_financial_operands_v3(owner)
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(io.rows) == 1 and io.calls.count("partition_attempt_burn") == 1
        assert not io.bindings and not io.owners
        with pytest.raises(
            assembly.NativeAccountRiskAssemblyError, match="already_used"
        ):
            await pending
