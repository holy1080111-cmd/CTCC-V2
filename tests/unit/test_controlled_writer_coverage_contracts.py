"""Synthetic fixed-I/O semantics only; no native, database or trading proof.

The positive test uses explicit private source/DB providers and the real issuer,
Control transition math, wire validators and owner registry. Those substitutions
are never runtime inputs and cannot seal a native deparser/source baseline.
"""

import asyncio
import copy
import pickle
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database.repositories import controlled_writer_coverage as repository
from app.database.repositories import demo_control as controls
from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import demo_control as control
from tests.controlled_writer_coverage_fixtures import (
    coverage_observation_fixture,
    coverage_record_fixture,
)


@pytest.fixture
def synthetic_io(monkeypatch, tmp_path):
    """Replace fixed I/O only. Never expose a fixture-made live binding/owner."""
    io = SimpleNamespace(
        tick=0,
        offset_ns=0,
        stage=None,
        control=None,
        control_event="6" * 64,
        fail=None,
        acquire_calls=0,
        estop_calls=0,
        prepare_calls=0,
        active_calls=0,
        readback_calls=0,
        native_calls=0,
        file_revision=0,
        inventory_revision=0,
        records=[],
        durable=[],
    )
    origin = int(datetime(2026, 10, 24, tzinfo=UTC).timestamp()) * 1_000_000_000

    def stamp():
        io.tick += 1
        delta = io.offset_ns + io.tick * 1_000_000
        return {"utc_ns": origin + delta, "monotonic_ns": 1_000_000_000 + delta}

    io.stamp = stamp
    io.factory = SimpleNamespace(
        kw={"bind": object(), "autoflush": False, "expire_on_commit": False}
    )
    io.custody = object()
    io.scope = control.ControlScope("demo", "730048")

    def inventory():
        coverage._guard_denials()
        return {
            "manifest": ("synthetic", io.inventory_revision),
            "files": {},
            "manifest_sha256": "1" * 64,
            "writer_sha256": "2" * 64,
        }

    def factory_pins(factory, custody):
        return (
            factory,
            factory.kw["bind"],
            tuple(sorted(factory.kw.items())),
            custody,
            custody,
            factory.kw["bind"],
            tuple(sorted(factory.kw.items())),
        )

    async def source_acquire(**_inputs):
        io.native_calls += 1
        io.stage = stamp()
        return object()

    held = {
        "plan_sha256": "3" * 64,
        "bundle_sha256": "4" * 64,
        "custody_id": "5" * 32,
        "bundle_raw": b"EXPLICIT_SYNTHETIC_UNIT_SOURCE",
    }
    receipt = canonical({"synthetic_unit_provider": True})

    def source_replay(_held, *, key):
        assert key is coverage._HOOK_KEY
        value = SimpleNamespace(
            current_receipt_json=receipt,
            reference=SimpleNamespace(head_sha256="a" * 64),
            proof_envelope_sha256="b" * 64,
            native_proof_sha256="c" * 64,
            native_readback_sha256="d" * 64,
            expires_at=utc_from_ns(io.stage["utc_ns"] + 30_000_000_000),
            monotonic_deadline_ns=io.stage["monotonic_ns"] + 30_000_000_000,
        )
        return value, stamp()

    def source_material(_held, _replay):
        identity = {
            "environment": "demo",
            "uid": "730048",
            "main_uid": "730048",
            "account_level": "2",
            "position_mode": "net_mode",
            "settlement_currency": "USDT",
        }
        return (
            {"route_proof": {"credential_session_sha256": "e" * 64}},
            io.stage,
            identity,
        )

    def source_files(root, names, maximum):
        return (
            ("SYNTHETIC_NOT_NATIVE", str(root)),
            {name: (maximum, io.file_revision) for name in names},
        )

    def observation(state):
        return controls.ControlObservation(
            state, io.control_event, utc_from_ns(stamp()["utc_ns"])
        )

    async def read_control(self, scope):
        assert scope == io.scope
        if io.control is None:
            raise control.DemoControlError("control_scope_missing")
        return observation(io.control)

    async def acquire_control(self, scope, *, owner_token, pins, command_id):
        io.acquire_calls += 1
        if io.fail in {"race_acquire", "race_arm"}:
            other = control.acquire(
                None, scope=scope, owner_sha256="f" * 64, pins=pins, now=self.clock()
            )
            if io.fail == "race_arm":
                other = control.advance(
                    other,
                    owner_sha256="f" * 64,
                    owner_epoch=1,
                    expected_revision=1,
                    action="arm_requested",
                    command_id="9" * 64,
                    now=self.clock(),
                    arm_expires_at=other.lease_until - timedelta(seconds=1),
                )
            io.control = other
        io.control = control.acquire(
            io.control,
            scope=scope,
            owner_sha256=control.owner_binding(owner_token),
            pins=pins,
            now=self.clock(),
        )
        result = observation(io.control)
        if io.fail == "acquire_readback_identity":
            result = replace(
                result,
                state=replace(result.state, scope=control.ControlScope("demo", "999")),
            )
        return result

    async def change_control(
        self, scope, *, owner_token, owner_epoch, expected_revision, action, command_id
    ):
        io.estop_calls += 1
        assert action == "estop"
        if io.fail == "race_revision":
            io.control = replace(io.control, revision=io.control.revision + 1)
        io.control = control.advance(
            io.control,
            owner_sha256=control.owner_binding(owner_token),
            owner_epoch=owner_epoch,
            expected_revision=expected_revision,
            action=action,
            command_id=command_id,
            now=self.clock(),
        )
        io.control_event = "7" * 64
        result = observation(io.control)
        if io.fail == "estop_readback_false":
            result = replace(result, state=replace(result.state, emergency_stop=False))
        return result

    def make_record(binding, phase):
        context = coverage._repository_binding_v1(binding, key=coverage._HOOK_KEY)
        now_stamp = stamp()
        now = utc_from_ns(now_stamp["utc_ns"])
        previous = io.records[-1] if io.records else None
        wire = dict(context["state_template"])
        wire.update(
            {
                "generation": 1,
                "epoch_id": "8" * 32,
                "journal_sequence": 1 if phase == "prepared" else 2,
                "state": phase,
                "transition_stamp": now_stamp,
                "created_at": now.isoformat()
                if previous is None
                else previous["state"]["created_at"],
                "updated_at": now.isoformat(),
                "revocation_reason": None,
                "revocation_observed_control": None,
            }
        )
        record = coverage_record_fixture(
            uid=io.scope.account_id,
            now=now,
            control=context["control"].state,
            control_event_sha256=context["control"].event_sha256,
            state_overrides=wire,
            previous_sha256=previous["event_sha256"] if previous else None,
            audit_raw=previous["audit_raw"] if previous else None,
        )
        value = coverage_observation_fixture(
            record["state_raw"],
            record["event_raw"],
            record["audit_raw"],
            prior_records=tuple(io.records),
        )
        io.records.append(record)
        return value

    class SyntheticRepository:
        def __init__(self, factory):
            assert factory is io.factory
            self._factory = factory
            self._poisoned = False

        async def verify_deployment(self):
            if io.fail == "unsealed_native_baseline":
                raise RuntimeError("synthetic_unsealed_baseline_refusal")

        async def _prepare_native(self, binding, *, key):
            io.prepare_calls += 1
            assert key is coverage._HOOK_KEY
            value = make_record(binding, "prepared")
            io.durable.append("prepared_committed")
            if io.fail == "unknown_prepare_commit":
                raise RuntimeError("synthetic_commit_ack_unknown")
            return value

        async def _activate_native(self, binding, prepared, *, key):
            io.active_calls += 1
            assert prepared.state == "prepared" and key is coverage._HOOK_KEY
            value = make_record(binding, "active")
            io.durable.append("active_committed")
            if io.fail == "unknown_active_commit":
                raise RuntimeError("synthetic_commit_ack_unknown")
            return value

        async def _readback_native(self, binding, expected, *, key):
            io.readback_calls += 1
            coverage._repository_binding_v1(binding, key=key)
            if io.fail == "late_cancel":
                asyncio.current_task().cancel()
                await asyncio.Event().wait()
            if io.fail == "changed_b5":
                return replace(expected, audit_json=expected.audit_json + " ")
            return expected

    monkeypatch.setattr(coverage.native_clock, "native_stamp", stamp)
    monkeypatch.setattr(coverage, "_package_inventory", inventory)
    monkeypatch.setattr(coverage, "_factory_pins", factory_pins)
    monkeypatch.setattr(coverage, "_source_material", source_material)
    monkeypatch.setattr(coverage, "_source_files", source_files)
    monkeypatch.setattr(
        coverage.account, "acquire_initial_demo_account_source_v4", source_acquire
    )
    monkeypatch.setattr(
        coverage.account,
        "_take_controlled_writer_coverage_v1",
        lambda _owner, *, key: held,
    )
    monkeypatch.setattr(
        coverage.account, "_replay_controlled_writer_coverage_v1", source_replay
    )

    def synthetic_current_deadline(replay, at):
        if (
            at["monotonic_ns"] >= replay.monotonic_deadline_ns
            or utc_from_ns(at["utc_ns"]) >= replay.expires_at
        ):
            raise coverage.account.CurrentAccountSourceV4Error(
                "account_v4_source_expired"
            )

    monkeypatch.setattr(coverage.account, "_check_current", synthetic_current_deadline)
    monkeypatch.setattr(controls.DemoControlRepository, "read", read_control)
    monkeypatch.setattr(controls.DemoControlRepository, "acquire", acquire_control)
    monkeypatch.setattr(controls.DemoControlRepository, "change", change_control)
    monkeypatch.setattr(
        repository, "ControlledWriterCoverageRepository", SyntheticRepository
    )
    # Explicit synthetic native-I/O identities are pinned only within this test.
    monkeypatch.setattr(
        coverage,
        "_FUNCTION_PINS",
        tuple(
            (owner, name, getattr(owner, name))
            for owner, name, _old in coverage._FUNCTION_PINS
        ),
    )

    async def open_owner():
        return await coverage.acquire_controlled_demo_writer_coverage_v1(
            account_session=object(),
            private_client=object(),
            session_factory=io.factory,
            private_proof_root=tmp_path / "private",
            native_proof_root=tmp_path / "native",
            custody_repository=io.custody,
        )

    io.open_owner = open_owner
    before_owners = set(coverage._OWNERS)
    before_bindings = set(coverage._BINDINGS)
    yield io
    for owner in set(coverage._OWNERS) - before_owners:
        coverage._OWNERS.pop(owner, None)
    for binding in set(coverage._BINDINGS) - before_bindings:
        coverage._BINDINGS.pop(binding, None)


@pytest.mark.asyncio
async def test_synthetic_positive_native_driver_owns_measured_fact_without_trade_authority(
    synthetic_io,
):
    """One pure positive, explicitly not source/PG/native acceptance."""
    io = synthetic_io
    owner = await io.open_owner()
    assert type(owner) is coverage.OwnedControlledDemoWriterCoverageV1
    assert (
        io.acquire_calls == io.estop_calls == io.prepare_calls == io.active_calls == 1
    )
    assert io.control.emergency_stop is True and io.control.arm_request_id is None
    facts = await coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)
    receipt = decode(facts.receipt_json, 65536)
    assert receipt["controlled_ctcc_coverage_verified"] is True
    assert (
        receipt["activation_readback_complete"]["monotonic_ns"]
        < receipt["consumed"]["monotonic_ns"]
    )
    assert all(
        receipt[name] is False
        for name in (
            "account_complete",
            "account_revision_published",
            "flat_start_permission",
            "event_eligibility",
            "execution_authority",
            "external_writer_absence",
            "complete_historical_event_coverage",
        )
    )
    assert "730048" not in facts.receipt_json.decode()
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)


@pytest.mark.asyncio
async def test_call_time_burn_before_first_await_even_if_awaitable_discarded(
    synthetic_io,
):
    owner = await synthetic_io.open_owner()
    pending = coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)
    assert owner not in coverage._OWNERS
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)
    pending.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "race_acquire",
        "race_arm",
        "race_revision",
        "acquire_readback_identity",
        "estop_readback_false",
        "unknown_prepare_commit",
        "unknown_active_commit",
        "changed_b5",
        "unsealed_native_baseline",
    ],
)
async def test_fixed_boundary_failure_never_issues_owner_or_retries(
    synthetic_io, failure
):
    io = synthetic_io
    io.fail = failure
    with pytest.raises(coverage.ControlledWriterCoverageError):
        await io.open_owner()
    assert len(coverage._OWNERS) == 0
    assert io.acquire_calls <= 1 and io.estop_calls <= 1
    assert io.prepare_calls <= 1 and io.active_calls <= 1
    if failure in {"race_acquire", "race_arm"}:
        assert io.estop_calls == 0 and io.control.emergency_stop is False
    if failure == "unknown_prepare_commit":
        assert io.durable == ["prepared_committed"] and io.active_calls == 0
    if failure == "unknown_active_commit":
        assert io.durable == ["prepared_committed", "active_committed"]
    if failure == "unsealed_native_baseline":
        assert io.native_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["factory", "clock", "inventory", "files", "default_deny", "expiry", "repository"],
)
async def test_changed_native_operand_burns_owner_before_database_readback(
    synthetic_io, monkeypatch, change
):
    io = synthetic_io
    owner = await io.open_owner()
    reads = io.readback_calls
    if change == "factory":
        io.factory.kw["autoflush"] = True
    elif change == "clock":
        monkeypatch.setattr(coverage.native_clock, "native_stamp", lambda: io.stamp())
    elif change == "inventory":
        io.inventory_revision += 1
    elif change == "files":
        io.file_revision += 1
    elif change == "default_deny":
        monkeypatch.setattr(
            coverage.execution_authority,
            "enforce_demo_final_dispatch",
            lambda *_a: None,
        )
    elif change == "repository":
        binding = coverage._OWNERS[owner]
        coverage._BINDINGS[binding]["repository"]._factory = object()
    else:
        io.offset_ns += 31_000_000_000
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)
    assert owner not in coverage._OWNERS and io.readback_calls == reads


@pytest.mark.asyncio
async def test_cross_task_consumer_burns_without_transferring_source_ownership(
    synthetic_io,
):
    owner = await synthetic_io.open_owner()

    async def other_task():
        coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)

    with pytest.raises(coverage.ControlledWriterCoverageError):
        await asyncio.create_task(other_task())
    assert owner not in coverage._OWNERS


@pytest.mark.asyncio
async def test_late_real_task_cancellation_keeps_burn_and_committed_journal(
    synthetic_io,
):
    io = synthetic_io
    owner = await io.open_owner()
    io.fail = "late_cancel"
    try:
        with pytest.raises(asyncio.CancelledError):
            await coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)
    finally:
        asyncio.current_task().uncancel()
    assert owner not in coverage._OWNERS
    assert io.durable == ["prepared_committed", "active_committed"]
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage.consume_owned_controlled_demo_writer_coverage_v1(owner)


@pytest.mark.asyncio
async def test_saved_rows_or_receipts_after_restart_cannot_remint_owner(synthetic_io):
    io = synthetic_io
    owner = await io.open_owner()
    saved_rows = tuple(io.records)
    # A restart has no live owner registry. Retained journal data is untouched.
    binding = coverage._OWNERS.pop(owner)
    coverage._BINDINGS.pop(binding)
    for forged in (
        owner,
        saved_rows,
        saved_rows[-1]["state"],
        saved_rows[-1]["state_raw"],
    ):
        with pytest.raises(coverage.ControlledWriterCoverageError):
            coverage.consume_owned_controlled_demo_writer_coverage_v1(forged)
    assert len(io.records) == 2 and io.durable == [
        "prepared_committed",
        "active_committed",
    ]


@pytest.mark.parametrize(
    "kind",
    [coverage._ActivationBindingV1, coverage.OwnedControlledDemoWriterCoverageV1],
)
def test_direct_opaque_construction_has_no_authority(kind):
    with pytest.raises(coverage.ControlledWriterCoverageError):
        kind()


@pytest.mark.parametrize("forged", ["opaque", "dto", "saved_active_dict"])
def test_forged_objects_without_live_registry_never_issue_authority(forged):
    if forged == "opaque":
        value = object.__new__(coverage.OwnedControlledDemoWriterCoverageV1)
    elif forged == "dto":
        value = coverage.ControlledDemoWriterCoverageFactsV1(b'{"passed":true}')
    else:
        value = {"state": "active", "passed": True, "complete": True}
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage.consume_owned_controlled_demo_writer_coverage_v1(value)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_algo",
        "boolean_zero",
        "pending",
        "blocked",
        "different_main_uid",
        "old_policy",
    ],
)
def test_original_current_inventory_fields_cannot_be_filled_or_coerced(defect):
    """Pure validation of fields downstream of V4 ownership, not an issuer."""
    identity = {
        "environment": "demo",
        "uid": "730048",
        "main_uid": "730048",
        "account_level": "2",
        "position_mode": "net_mode",
        "settlement_currency": "USDT",
    }
    document = {
        "identity": identity,
        "policy_sha256": coverage.current.V7_POLICY_SHA256,
        "inventory_row_counts": {
            name: 0 for name in coverage.current.V7_INVENTORY_STREAMS
        },
        "blocking_reasons": [],
        "observed_inventory_state": "observed_empty_supported_queries",
        "observed_flat": True,
    }
    if defect == "missing_algo":
        del document["inventory_row_counts"]["algo_oco"]
    elif defect == "boolean_zero":
        document["inventory_row_counts"]["positions"] = False
    elif defect == "pending":
        document["inventory_row_counts"]["orders_pending"] = 1
    elif defect == "blocked":
        document["blocking_reasons"] = ["incomplete"]
    elif defect == "different_main_uid":
        identity["main_uid"] = "999"
    else:
        document["policy_sha256"] = "0" * 64
    native = canonical({"stage": {"started": {"utc_ns": 1, "monotonic_ns": 1}}})
    held = {"bundle_raw": canonical({"native_proof_hex": native.hex()})}
    replay = SimpleNamespace(
        current_receipt_json=canonical(document),
        packet=SimpleNamespace(
            plan=SimpleNamespace(expected_uid="730048", expected_main_uid="730048")
        ),
    )
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage._source_material(held, replay)


@pytest.mark.asyncio
async def test_live_owner_cannot_be_copied_pickled_or_used_as_repository_binding(
    synthetic_io,
):
    owner = await synthetic_io.open_owner()
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(coverage.ControlledWriterCoverageError):
            operation(owner)
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage._repository_binding_v1(owner, key=coverage._HOOK_KEY)
    with pytest.raises(coverage.ControlledWriterCoverageError):
        coverage._repository_binding_v1(coverage._OWNERS[owner], key=object())


def test_no_public_receipt_or_passed_flag_input_and_no_trade_side_effect_source():
    import ast

    path = Path(coverage.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consume = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "consume_owned_controlled_demo_writer_coverage_v1"
    )
    assert not isinstance(consume, ast.AsyncFunctionDef)
    acquire = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "acquire_controlled_demo_writer_coverage_v1"
    )
    assert acquire.args.kwarg is None
    assert {arg.arg for arg in acquire.args.kwonlyargs} == {
        "account_session",
        "private_client",
        "session_factory",
        "private_proof_root",
        "native_proof_root",
        "custody_repository",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert (
        not {
            "latch_stop",
            "clear_stop",
            "renew",
            "place_order",
            "send",
            "post",
            "reconcile_scope",
        }
        & calls
    )
