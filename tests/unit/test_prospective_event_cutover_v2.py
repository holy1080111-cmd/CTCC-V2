"""Synthetic contract/MOVE safety; no native source or PostgreSQL acceptance."""

import asyncio
import copy
import pickle
from dataclasses import replace
from datetime import timedelta

import pytest

from app.database.repositories import prospective_event_cutover_v2 as repository
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import prospective_event_cutover_v2 as issuer
from tests.prospective_cutover_fixtures import (
    cutover_record_fixture,
    empty_scan_document,
)
from tests.unit.test_controlled_writer_coverage_contracts import (
    synthetic_io as synthetic_io,  # noqa: PLC0414 -- explicit pytest fixture re-export.
)


def test_original_candle_open_must_follow_start_not_only_close():
    raw = cutover_record_fixture()
    start = records.stamp_utc(records.decode(raw)["coverage_start"])
    with pytest.raises(
        records.ProspectiveCutoverError, match="trigger_predates_coverage"
    ):
        records.require_trigger_after_start(
            raw, original_trigger_time=start + timedelta(seconds=299), bar_seconds=300
        )
    with pytest.raises(
        records.ProspectiveCutoverError, match="trigger_predates_coverage"
    ):
        records.require_trigger_after_start(
            raw, original_trigger_time=start + timedelta(seconds=300), bar_seconds=300
        )
    records.require_trigger_after_start(
        raw,
        original_trigger_time=start + timedelta(seconds=300, microseconds=1),
        bar_seconds=300,
    )


@pytest.mark.parametrize("field", records.FALSE_FIELDS)
@pytest.mark.parametrize("value", [True, 0, None, "false"])
def test_cutover_cannot_assert_complete_history_or_authority(field, value):
    document = records.decode(cutover_record_fixture())
    document[field] = value
    with pytest.raises(records.ProspectiveCutoverError, match="authority_claim"):
        records.decode(records.canonical(document))


@pytest.mark.parametrize(
    "change",
    [
        "equal_utc",
        "equal_monotonic",
        "reverse",
        "bool_revision",
        "float_revision",
        "different_uid",
        "changed_scan",
        "duplicate_key",
    ],
)
def test_cutover_rejects_causal_identity_or_wire_damage(change):
    raw = cutover_record_fixture()
    document = records.decode(raw)
    if change == "equal_utc":
        document["coverage_start"]["utc_ns"] = document["post_active_readback"][
            "utc_ns"
        ]
    elif change == "equal_monotonic":
        document["coverage_start"]["monotonic_ns"] = document["post_active_readback"][
            "monotonic_ns"
        ]
    elif change == "reverse":
        document["post_active_readback"], document["coverage_start"] = (
            document["coverage_start"],
            document["post_active_readback"],
        )
    elif change == "bool_revision":
        document["ledger_revision"] = False
    elif change == "float_revision":
        document["ledger_revision"] = 0.0
    elif change == "different_uid":
        document["account_id"] = "730052"
    elif change == "changed_scan":
        document["journal_scan_json"] = document["journal_scan_json"].replace(
            '"event_count":0', '"event_count":1'
        )
    elif change == "duplicate_key":
        raw = raw[:-1] + b',"g6_qualified":false}'
    with pytest.raises(records.ProspectiveCutoverError):
        records.decode(
            raw if change == "duplicate_key" else records.canonical(document)
        )


def test_nanosecond_rounding_never_moves_coverage_start_earlier():
    document = records.decode(cutover_record_fixture())
    stamp = dict(document["coverage_start"])
    exact = records.stamp_utc(stamp)
    stamp["utc_ns"] += 1
    assert records.stamp_utc(stamp) == exact + timedelta(microseconds=1)


@pytest.fixture
def synthetic_append(monkeypatch, synthetic_io):
    io = synthetic_io
    io.cutover_calls = 0
    io.cutover_failure = None
    io.cutover_records = []

    async def append(self, binding, *, key):
        io.cutover_calls += 1
        state, stamp = coverage._prospective_cutover_state_v2(binding, key=key)
        post = {
            name: state["activation_complete"][name]
            for name in ("utc_ns", "monotonic_ns")
        }
        start = {name: stamp[name] for name in ("utc_ns", "monotonic_ns")}
        raw = cutover_record_fixture(
            template=state["template"],
            active=state["active"],
            post_active=post,
            start=start,
            scan=empty_scan_document(
                state["ledger_scope"].account_id, utc_from_ns(post["utc_ns"])
            ),
        )
        io.cutover_records.append(raw)
        io.durable.append("synthetic_cutover_committed")
        if io.cutover_failure == "unknown_commit":
            records.deny("prospective_cutover_commit_or_readback_unknown")
        if io.cutover_failure == "changed_source":
            io.file_revision += 1
        if io.cutover_failure == "control_after_session_exit":
            # Explicit synthetic simulation of another DB owner advancing
            # Control after the held readback transaction's final release.
            io.control = replace(io.control, revision=io.control.revision + 1)
        if io.cutover_failure == "cancel":
            asyncio.current_task().cancel()
            await asyncio.Event().wait()
        return repository._CutoverReadback(
            raw,
            records.digest(raw),
            records.stamp_utc(start),
            records.stamp_utc(start),
            frozenset(),
        )

    monkeypatch.setattr(
        repository.NativeProspectiveCutoverRepository, "_append_native", append
    )
    # These pins apply only to the explicit synthetic providers in this test.
    monkeypatch.setattr(
        issuer,
        "_PINS",
        tuple((owner, name, getattr(owner, name)) for owner, name, _ in issuer._PINS),
    )
    yield io
    for owner in list(issuer._OWNERS):
        owner.close()


@pytest.mark.asyncio
async def test_positive_issuer_moves_real_registry_owner_and_keeps_original_false_contract(
    synthetic_append,
):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    pending = issuer.commit_prospective_event_cutover_v2(original)
    assert original not in coverage._OWNERS
    assert coverage._CUTOVERS[binding] is True
    assert io.cutover_calls == 0
    owner = await pending
    assert type(owner) is issuer.OwnedProspectiveDemoCutoverV2
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert io.cutover_calls == 1
    saved = issuer._OWNERS[owner]["record"]
    document = records.decode(saved.record_json)
    assert all(document[name] is False for name in records.FALSE_FIELDS)
    assert (
        document["coverage_start"]["monotonic_ns"]
        > document["post_active_readback"]["monotonic_ns"]
    )
    assert io.control.emergency_stop is True and io.control.arm_request_id is None
    for action in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(records.ProspectiveCutoverError, match="not_transferable"):
            action(owner)
    owner.close()


@pytest.mark.asyncio
async def test_control_advance_after_readback_release_remains_only_historical_custody(
    synthetic_append,
):
    io = synthetic_append
    original = await io.open_owner()
    old_revision = io.control.revision
    io.cutover_failure = "control_after_session_exit"
    owner = await issuer.commit_prospective_event_cutover_v2(original)
    document = records.decode(issuer._OWNERS[owner]["record"].record_json)
    assert document["control_revision"] == old_revision
    assert io.control.revision == old_revision + 1
    assert all(document[name] is False for name in records.FALSE_FIELDS)
    assert document["record_kind"] == "prospective_cutover_not_g6_or_order_authority"
    assert io.cutover_calls == 1 and len(io.cutover_records) == 1
    owner.close()


@pytest.mark.asyncio
async def test_discard_before_await_closes_both_awaitables_and_burns_original(
    synthetic_append,
):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    pending = issuer.commit_prospective_event_cutover_v2(original)
    pending.close()
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert io.cutover_calls == 0
    with pytest.raises(
        coverage.ControlledWriterCoverageError, match="owner_unavailable"
    ):
        issuer.commit_prospective_event_cutover_v2(original)


@pytest.mark.asyncio
async def test_throw_before_first_send_cleans_retained_wrapper_without_gc(
    synthetic_append,
):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    pending = issuer.commit_prospective_event_cutover_v2(original)
    with pytest.raises(RuntimeError, match="synthetic_prestart_throw"):
        pending.throw(RuntimeError("synthetic_prestart_throw"))
    assert pending._closed is True
    assert pending._inner.cr_frame is None and pending._coroutine.cr_frame is None
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert io.cutover_calls == 0 and original not in coverage._OWNERS
    # Keep pending strongly referenced. No finalizer or gc.collect is involved.
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        pending.send(None)
    with pytest.raises(
        RuntimeError, match=r"\Acannot reuse already awaited coroutine\Z"
    ):
        pending.throw(ValueError("synthetic_after_terminal"))
    assert io.cutover_calls == 0


@pytest.mark.asyncio
async def test_prestart_task_cancellation_cleans_retained_wrapper_without_gc(
    synthetic_append,
):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    pending = issuer.commit_prospective_event_cutover_v2(original)
    task = asyncio.create_task(pending)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pending._closed is True
    assert pending._inner.cr_frame is None and pending._coroutine.cr_frame is None
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert io.cutover_calls == 0 and original not in coverage._OWNERS


@pytest.mark.asyncio
async def test_send_throw_after_success_cannot_reenter_or_remove_historical_owner(
    synthetic_append,
):
    io = synthetic_append
    original = await io.open_owner()
    pending = issuer.commit_prospective_event_cutover_v2(original)
    owner = await pending
    assert pending._closed is True and owner in issuer._OWNERS
    assert pending._inner.cr_frame is None and pending._coroutine.cr_frame is None
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        pending.send(None)
    with pytest.raises(
        RuntimeError, match=r"\Acannot reuse already awaited coroutine\Z"
    ):
        pending.throw(ValueError("synthetic_terminal_throw"))
    pending.close()
    assert owner in issuer._OWNERS and io.cutover_calls == 1
    owner.close()


@pytest.mark.asyncio
async def test_late_exception_closes_retained_inner_without_gc(synthetic_append):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    io.cutover_failure = "unknown_commit"
    pending = issuer.commit_prospective_event_cutover_v2(original)
    with pytest.raises(
        records.ProspectiveCutoverError, match="commit_or_readback_unknown"
    ):
        await pending
    assert pending._closed is True
    assert pending._inner.cr_frame is None and pending._coroutine.cr_frame is None
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert len(io.cutover_records) == 1 and io.cutover_calls == 1
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        pending.send(None)
    assert len(io.cutover_records) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown_commit", "changed_source"])
async def test_late_failure_preserves_durable_marker_and_never_restores_input(
    synthetic_append, failure
):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]
    io.cutover_failure = failure
    with pytest.raises(
        (records.ProspectiveCutoverError, coverage.ControlledWriterCoverageError)
    ):
        await issuer.commit_prospective_event_cutover_v2(original)
    assert len(io.cutover_records) == 1 and io.cutover_calls == 1
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert not issuer._OWNERS
    assert original not in coverage._OWNERS


@pytest.mark.asyncio
async def test_foreign_task_burns_input_without_running_cutover(synthetic_append):
    io = synthetic_append
    original = await io.open_owner()
    binding = coverage._OWNERS[original]

    async def foreign():
        return issuer.commit_prospective_event_cutover_v2(original)

    with pytest.raises(coverage.ControlledWriterCoverageError, match="context_changed"):
        await asyncio.create_task(foreign())
    assert binding not in coverage._BINDINGS and binding not in coverage._CUTOVERS
    assert io.cutover_calls == 0


@pytest.mark.asyncio
async def test_forged_or_saved_cutover_objects_cannot_create_native_owner():
    with pytest.raises(records.ProspectiveCutoverError, match="not_constructible"):
        issuer.OwnedProspectiveDemoCutoverV2()
    for value in (
        object(),
        object.__new__(coverage.OwnedControlledDemoWriterCoverageV1),
        cutover_record_fixture(),
    ):
        with pytest.raises(
            coverage.ControlledWriterCoverageError, match="owner_unavailable"
        ):
            issuer.commit_prospective_event_cutover_v2(value)


def test_new_native_seal_is_explicitly_unsealed_and0050_is_not_replaced():
    from app.domain import account_history_partition_catalog_v1 as parent

    assert catalog.PG17_DEPARSED_BASELINE is None
    assert (
        catalog.PG17_DEPARSED_BASELINE_STATUS == "UNSEALED_REQUIRES_NATIVE_PG17_CAPTURE"
    )
    assert (
        parent.PG17_DEPARSED_BASELINE
        == "37190935d1b8e2807d26590225165779bb12e9684c260dd19fb61fba0d3c64c3"
    )


def test_migration_frozen_ddl_matches_the_effective_declarations():
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/0051_prospective_event_cutover.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    value = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_FROZEN_DDL_SHA256"
            for target in node.targets
        )
    )
    assert value == catalog.ddl_identity()
