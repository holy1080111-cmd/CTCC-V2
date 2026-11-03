"""Prepared synthetic boundary tests; no native history/G6 acceptance claim."""

import asyncio
import copy
import inspect
import pickle
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from weakref import ref

import pytest

from app.domain import prospective_event_cutover_v2 as records
from app.domain.source_primitives import PublicReceiptError, utc_from_ns
from app.exchange.okx.errors import OkxPrivateApiError
from app.trade_qualification import native_namespace_history_bundle_v1 as bundle
from app.trade_qualification import native_namespace_history_source_v1 as source
from app.trade_qualification import native_namespace_phase_v1 as phase
from tests.prospective_cutover_fixtures import cutover_record_fixture
from tests.unit.research.test_demo_session_route_preflight import _client
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_qualification_account_collector import (
    API_KEY,
    API_SECRET,
    PASSPHRASE,
)


def _refresh_test_pins(monkeypatch):
    # Explicitly declared synthetic IO providers only; never native acceptance.
    monkeypatch.setattr(
        source,
        "_PINS",
        tuple(
            (
                module,
                name,
                getattr(getattr(module, name), "__func__", getattr(module, name)),
            )
            for module, name, _ in source._PINS
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", [None, "intermediate_digest", "last_source", "late_unknown", "bound"]
)
async def test_actual_historical_custody_helper_reads_every_hop_or_preserves_failure(
    monkeypatch, tmp_path, damage
):
    from app.domain.source_primitives import sha

    at = datetime(2026, 10, 25, tzinfo=UTC)
    repository = object()
    originals = (b"original-cutover", b"middle-phase", b"last-current-phase")
    reads = []

    def pins(index):
        return {
            "source_custody_id": str(index) * 32,
            "source_bundle_sha256": sha(originals[index]),
            "source_proof_envelope_sha256": str(index + 3) * 64,
        }

    original = {**pins(0), "custody": repository, "bundle_raw": originals[0]}
    moved = {
        "historical": {"phase_source": original},
        "custody": repository,
        "current_account": {"bundle_raw": originals[-1]},
        "hops": tuple(
            {"active": SimpleNamespace(state_document=pins(index))} for index in (1, 2)
        ),
    }
    durable = tmp_path / "retained-b1-journal.bin"
    durable.write_bytes(b"durable-before-late-failure")

    async def read(owner, *, bundle_id, expected_sha256, proof_envelope_sha256):
        assert owner is repository
        reads.append((bundle_id, expected_sha256, proof_envelope_sha256))
        index = int(bundle_id[0])
        if damage == "late_unknown" and index == 2:
            raise RuntimeError("synthetic-readback-unknown")
        raw = (
            b"changed-current"
            if damage == "last_source" and index == 2
            else originals[index]
        )
        digest = (
            "0" * 64
            if damage == "intermediate_digest" and index == 1
            else expected_sha256
        )
        return source.custody.RetainedRuntimeBundleReadback(
            bundle_id, digest, proof_envelope_sha256, at, at, raw
        )

    # Only native custody readback IO and the already-declared _await_state
    # context providers are synthetic. The actual original _await body,
    # original deadline and both source-integrity fences execute unchanged.
    monkeypatch.setattr(
        source.custody.RuntimeBundleCustodyRepository, "read_retained_bundle", read
    )
    if damage == "bound":
        monkeypatch.setattr(source.custody, "MAX_BUNDLE_BYTES", 1)
    with _await_state(monkeypatch) as state:
        state["moved"].update(moved)
        _refresh_test_pins(monkeypatch)
        if damage is None:
            await source._historical_custody(state)
            assert state["hop_source_bundles"] == originals[1:]
            assert reads == [
                (
                    pins(index)["source_custody_id"],
                    pins(index)["source_bundle_sha256"],
                    pins(index)["source_proof_envelope_sha256"],
                )
                for index in range(3)
            ]
        else:
            with pytest.raises(
                (source.NativeNamespaceHistorySourceError, RuntimeError)
            ):
                await source._historical_custody(state)
            assert "hop_source_bundles" not in state
            assert (
                len(reads)
                == {
                    "intermediate_digest": 2,
                    "last_source": 3,
                    "late_unknown": 3,
                    "bound": 1,
                }[damage]
            )
        assert durable.read_bytes() == b"durable-before-late-failure"


def _planning_state(*, age_ns=360_000_000_123):
    raw = cutover_record_fixture()
    record = records.decode(raw)
    original = {
        "utc_ns": record["coverage_start"]["utc_ns"] + age_ns,
        "monotonic_ns": 9_000_000_000,
    }
    old = current_plan_v7(
        expected_uid=record["account_id"],
        session_binding_id="synthetic-same-native-session",
    )
    return {
        "original": original,
        "lease": SimpleNamespace(binding_id=old.session_binding_id),
        "moved": {
            "historical": {"record": SimpleNamespace(record_json=raw)},
            "current_account": {"plan": old},
        },
    }


def test_new_V6_plan_uses_exact_start_and_fixed_cutoff_without_renewing_old_source():
    state = _planning_state()
    old = state["moved"]["current_account"]["plan"]
    before = old.model_dump()
    plan = source._plan(state)
    start = records.decode(state["moved"]["historical"]["record"].record_json)[
        "coverage_start"
    ]["utc_ns"]
    assert plan.contract_version == "ctcc.demo_account_plan.v6"
    assert plan.session_binding_id == old.session_binding_id
    assert plan.expected_uid == old.expected_uid
    assert plan.history_start == utc_from_ns(start // 1_000_000 * 1_000_000)
    assert plan.history_end == utc_from_ns(
        state["original"]["utc_ns"] // 1_000_000 * 1_000_000
    )
    assert old.model_dump() == before
    assert plan.max_batch_seconds == 120
    assert bundle.MAX_CLOCK_BATCH_NS == 60_000_000_000
    assert bundle.MAX_CLOCK_DEVIATION_NS == 5_000_000


@pytest.mark.parametrize("age", [0, bundle.MAX_LOOKBACK_NS + 1])
def test_outside_original_start_profile_is_refused_instead_of_shifted_forward(age):
    state = _planning_state(age_ns=age)
    before = state["moved"]["historical"]["record"].record_json
    with pytest.raises(
        source.NativeNamespaceHistorySourceError,
        match="original_start_outside_closed_profile",
    ):
        source._plan(state)
    assert state["moved"]["historical"]["record"].record_json == before


def test_forged_dto_and_unregistered_owner_cannot_enter_before_native_io(tmp_path):
    for candidate in (
        object(),
        SimpleNamespace(passed=True, uid="730051"),
        object.__new__(phase.OwnedNativeNamespacePhaseV1),
    ):
        with pytest.raises(
            records.ProspectiveCutoverError, match="phase_owner_unavailable"
        ):
            source.acquire_owned_native_namespace_history_v1(
                candidate, raw_root=tmp_path / "raw", native_root=tmp_path / "clock"
            )
    assert not (tmp_path / "raw").exists()


@pytest.mark.parametrize(
    "target",
    ["_check", "_acquire", "phase_move", "_begin_acquisition", "_bind_moved_state"],
)
def test_saved_entry_refuses_replaced_helpers_before_a_move_or_native_callback(
    monkeypatch, tmp_path, target
):
    entry = source.acquire_owned_native_namespace_history_v1
    with pytest.raises(records.ProspectiveCutoverError):
        entry(object(), raw_root=tmp_path / "raw", native_root=tmp_path / "clock")
    called = []

    def replaced(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("changed callback must not run")

    if target == "phase_move":
        monkeypatch.setattr(phase, "_take_native_namespace_history_v1", replaced)
    else:
        monkeypatch.setattr(source, target, replaced)
    with pytest.raises(
        source.NativeNamespaceHistorySourceError,
        match="original_function_or_policy_changed",
    ):
        entry(object(), raw_root=tmp_path / "raw", native_root=tmp_path / "clock")
    assert not called


@pytest.mark.parametrize("end", ["close", "throw", "failure", "success"])
def test_actual_attempt_terminal_cleanup_preserves_durable_evidence_without_gc(end):
    durable = {"accepted_B1_journal": b"explicit synthetic journal"}
    state = {"native_binding": object(), "transferred": end == "success"}
    entered = []

    async def inner():
        entered.append(True)
        if end == "failure":
            raise RuntimeError("synthetic failure")
        return 7

    native = inner()
    held = source._Attempt(native, state)
    if end == "close":
        held.close()
    elif end == "throw":
        with pytest.raises(RuntimeError):
            held.throw(RuntimeError("synthetic prestart"))
    elif end == "failure":
        with pytest.raises(RuntimeError):
            held.send(None)
    else:
        with pytest.raises(StopIteration) as stopped:
            held.send(None)
        assert stopped.value.value == 7
    assert inspect.getcoroutinestate(native) == inspect.CORO_CLOSED
    assert state if end == "success" else not state
    assert held._state is None
    assert durable == {"accepted_B1_journal": b"explicit synthetic journal"}
    assert entered == ([] if end in {"close", "throw"} else [True])
    held.close()


@pytest.mark.asyncio
async def test_prestart_task_cancel_burns_moved_attempt_without_inner_execution():
    state = {"transferred": False, "moved": object()}
    entered = []

    async def inner():
        entered.append(True)
        await asyncio.Event().wait()

    native = inner()
    held = source._Attempt(native, state)
    task = asyncio.create_task(held)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state == {} and not entered
    assert inspect.getcoroutinestate(native) == inspect.CORO_CLOSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["foreign_task", "closed_lease", "foreign_active_lease", "clock_expired"]
)
async def test_actual_context_guard_requires_same_native_lease_and_original_anchor(
    monkeypatch, change
):
    private = _client(
        okx_demo_api_key=API_KEY,
        okx_demo_api_secret=API_SECRET,
        okx_demo_api_passphrase=PASSPHRASE,
        okx_demo_read_max_retries=0,
    )
    samples = Samples()
    monkeypatch.setattr(source.native.clock, "native_stamp", samples)
    monkeypatch.setattr(phase, "_historical_context", lambda _historical: None)
    pins = (object(), object(), {}, object(), object(), object(), {})
    monkeypatch.setattr(
        source.coverage, "_factory_pins", lambda _factory, _custody: pins
    )
    monkeypatch.setattr(
        source.coverage, "_source_files", lambda root, _names, _maximum: root
    )
    _refresh_test_pins(monkeypatch)
    with private.pinned_read_session() as lease:
        actual = source._context()
        stamp = samples()
        state = dict.__new__(source._NamespaceHistoryStateV1)
        state.update(
            {
                "context": (*actual[:3], ref(actual[3])),
                "original": stamp,
                "last": stamp,
                "lease": lease,
                "private_client": private,
                "factory_pins": pins,
                "moved": {
                    "historical": {},
                    "factory": object(),
                    "custody": object(),
                    "private_root": "old-raw",
                    "native_root": "old-clock",
                    "source_files": {
                        "private_root": "old-raw",
                        "native_root": "old-clock",
                    },
                },
            }
        )
        state["binding"] = source._register_moved_namespace_history_v1(
            state["moved"], key=phase._HOOK_KEY
        )
        source._bind_moved_state(state, key=source._HOOK_KEY)
        source._check(state)
        if change == "foreign_task":

            async def other():
                source._check(state)

            with pytest.raises(
                source.NativeNamespaceHistorySourceError, match="context_changed"
            ):
                await asyncio.create_task(other())
        elif change == "closed_lease":
            lease._lease.active = False
            with pytest.raises(OkxPrivateApiError):
                source._check(state)
        elif change == "foreign_active_lease":
            from app.exchange.okx import private_rest

            token = private_rest._DEMO_READ_SESSION.set(
                SimpleNamespace(_owner=object())
            )
            try:
                with pytest.raises(OkxPrivateApiError):
                    source._check(state)
            finally:
                private_rest._DEMO_READ_SESSION.reset(token)
        else:
            samples.calls += 61000
            with pytest.raises(PublicReceiptError, match="clock_lease_expired"):
                source._check(state)
        source._release_moved(state)
        assert state["binding"] not in source._MOVED_BINDINGS


def test_audit_consumer_cannot_promote_saved_bytes_or_unregistered_owner():
    for owner in (
        b"saved-native-looking-bundle",
        SimpleNamespace(passed=True),
        object.__new__(source.OwnedNativeNamespaceHistoryV1),
    ):
        with pytest.raises(
            source.NativeNamespaceHistorySourceError, match="owner_unavailable"
        ):
            source.consume_owned_native_namespace_history_v1(owner)
    assert not source._OWNERS


@contextmanager
def _await_state(monkeypatch):
    private = _client(
        okx_demo_api_key=API_KEY,
        okx_demo_api_secret=API_SECRET,
        okx_demo_api_passphrase=PASSPHRASE,
        okx_demo_read_max_retries=0,
    )
    samples = Samples()
    monkeypatch.setattr(source.native.clock, "native_stamp", samples)
    monkeypatch.setattr(phase, "_historical_context", lambda _history: None)
    pins = (object(), object(), {}, object(), object(), object(), {})
    monkeypatch.setattr(
        source.coverage, "_factory_pins", lambda _factory, _custody: pins
    )
    monkeypatch.setattr(
        source.coverage, "_source_files", lambda root, _names, _maximum: root
    )
    _refresh_test_pins(monkeypatch)
    with private.pinned_read_session() as lease:
        actual, stamp = source._context(), samples()
        state = dict.__new__(source._NamespaceHistoryStateV1)
        state.update(
            {
                "context": (*actual[:3], ref(actual[3])),
                "original": stamp,
                "last": stamp,
                "loop_deadline": actual[2].time() + 60,
                "lease": lease,
                "private_client": private,
                "factory_pins": pins,
                "moved": {
                    "historical": {},
                    "factory": object(),
                    "custody": object(),
                    "private_root": "old-raw",
                    "native_root": "old-clock",
                    "source_files": {
                        "private_root": "old-raw",
                        "native_root": "old-clock",
                    },
                },
            }
        )
        state["binding"] = source._register_moved_namespace_history_v1(
            state["moved"], key=phase._HOOK_KEY
        )
        source._bind_moved_state(state, key=source._HOOK_KEY)
        try:
            yield state
        finally:
            source._release_moved(state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name,alias",
    [
        ("source", "validate_stamps"),
        ("source", "sha"),
        ("source", "canonical"),
        ("source", "utc_from_ns"),
        ("source", "_state_identity"),
        ("source", "ref"),
        ("source", "_NamespaceHistoryStateV1"),
        ("source", "_release_dead_state"),
        ("bundle", "validate_stamps"),
        ("bundle", "sha"),
        ("bundle", "canonical"),
        ("bundle", "utc_from_ns"),
    ],
)
async def test_alias_replacement_inside_real_await_is_refused_after_valid_baseline(
    monkeypatch, module_name, alias
):
    with _await_state(monkeypatch) as state:
        assert (
            await source._await(state, asyncio.sleep(0, result="baseline"))
            == "baseline"
        )
        deadline = state["loop_deadline"]

        async def mutate():
            await asyncio.sleep(0)
            monkeypatch.setattr(
                source if module_name == "source" else bundle,
                alias,
                lambda *_args, **_kwargs: None,
            )
            return "must-not-be-accepted"

        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="original_function_or_policy_changed",
        ):
            await source._await(state, mutate())
        assert state["loop_deadline"] == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["expired_before_send", "stalled", "suppressed_cancellation"]
)
async def test_actual_await_uses_one_original_deadline_and_rejects_late_results(
    monkeypatch, kind
):
    with _await_state(monkeypatch) as state:
        assert (
            await source._await(state, asyncio.sleep(0, result="baseline"))
            == "baseline"
        )
        ran = []

        async def operation():
            ran.append("entered")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                ran.append("cancelled")
                if kind == "suppressed_cancellation":
                    return "late-result"
                raise
            finally:
                ran.append("closed")

        state["loop_deadline"] = asyncio.get_running_loop().time() + (
            0 if kind == "expired_before_send" else 0.01
        )
        original_deadline = state["loop_deadline"]
        with pytest.raises(
            source.NativeNamespaceHistorySourceError, match="original_deadline_expired"
        ):
            await source._await(state, operation())
        assert ran == (
            [] if kind == "expired_before_send" else ["entered", "cancelled", "closed"]
        )
        assert state["loop_deadline"] == original_deadline


def test_unregistered_internal_dto_cannot_enter_private_source_check():
    with pytest.raises(
        source.NativeNamespaceHistorySourceError,
        match="original_moved_binding_required",
    ):
        source._check({"moved": {"passed": True}})
    with pytest.raises(
        source.NativeNamespaceHistorySourceError, match="original_phase_move_required"
    ):
        source._register_moved_namespace_history_v1({"passed": True}, key=object())


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["close", "throw", "prestart_cancel"])
async def test_moved_binding_is_released_immediately_without_gc(
    monkeypatch, tmp_path, terminal
):
    with _await_state(monkeypatch) as state:
        binding = state["binding"]
        durable = tmp_path / "original-durable-source.bin"
        durable.write_bytes(b"retained-before-cleanup")
        ran = []

        async def inner():
            ran.append(True)

        attempt = source._Attempt(inner(), state)
        if terminal == "close":
            attempt.close()
        elif terminal == "throw":
            with pytest.raises(RuntimeError, match="before-send"):
                attempt.throw(RuntimeError("before-send"))
        else:
            task = asyncio.create_task(attempt)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not ran and state == {}
        assert binding not in source._MOVED_BINDINGS
        assert durable.read_bytes() == b"retained-before-cleanup"
        assert attempt._state is None


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", ["second_send", "copied_state", "rebind"])
async def test_actual_moved_attempt_burn_rejects_retry_and_copied_state_before_io(
    monkeypatch, tmp_path, replay
):
    with _await_state(monkeypatch) as state:
        source._check(state)
        source._begin_acquisition(state)
        binding = state["binding"]
        assert source._MOVED_BINDINGS[binding]["acquisition_started"] is True
        durable = tmp_path / "original-source.bin"
        durable.write_bytes(b"must-survive-retry-rejection")
        if replay == "second_send":
            with pytest.raises(
                source.NativeNamespaceHistorySourceError, match="attempt_already_spent"
            ):
                await source._acquire(state)
        elif replay == "copied_state":
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="original_moved_binding_required",
            ):
                await source._acquire(dict(state))
        else:
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="moved_state_already_bound_or_unavailable",
            ):
                source._bind_moved_state(dict(state), key=source._HOOK_KEY)
        assert source._MOVED_BINDINGS[binding]["state_ref"]() is state
        assert source._MOVED_BINDINGS[binding]["moved"] is state["moved"]
        assert durable.read_bytes() == b"must-survive-retry-rejection"
        assert not source._OWNERS


def test_original_weak_state_death_releases_a_separately_retained_binding_without_gc():
    moved = {"historical_evidence": b"retain-exact-original-tombstones"}
    binding = source._register_moved_namespace_history_v1(moved, key=phase._HOOK_KEY)
    state = dict.__new__(source._NamespaceHistoryStateV1)
    state.update(binding=binding, moved=moved)
    source._bind_moved_state(state, key=source._HOOK_KEY)
    record = source._MOVED_BINDINGS[binding]
    state_ref = ref(state)
    assert record["state_ref"]() is state
    # There is deliberately no owner or coroutine left retaining state, while
    # the original private binding AND registry record remain explicitly held.
    del state
    assert state_ref() is None
    assert record["state_ref"]() is None
    assert binding not in source._MOVED_BINDINGS
    assert moved == {"historical_evidence": b"retain-exact-original-tombstones"}


def test_state_and_binding_can_both_die_without_a_registry_retention_cycle():
    moved = {"original_event_tombstone": "explicit-synthetic-event"}
    binding = source._register_moved_namespace_history_v1(moved, key=phase._HOOK_KEY)
    state = dict.__new__(source._NamespaceHistoryStateV1)
    state.update(binding=binding, moved=moved)
    source._bind_moved_state(state, key=source._HOOK_KEY)
    state_ref, binding_ref = ref(state), ref(binding)
    assert source._MOVED_BINDINGS[binding]["state_ref"]() is state
    del binding
    assert binding_ref() is not None  # The live original state still owns it.
    del state
    assert state_ref() is None and binding_ref() is None
    assert moved["original_event_tombstone"] == "explicit-synthetic-event"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["construct", "copy", "deepcopy", "pickle"])
async def test_live_original_state_cannot_be_constructed_or_serialized_as_authority(
    monkeypatch, operation
):
    with _await_state(monkeypatch) as state:
        source._check(state)
        binding = state["binding"]
        code = (
            "state_not_constructible"
            if operation == "construct"
            else "state_not_transferable"
        )
        with pytest.raises(source.NativeNamespaceHistorySourceError, match=code):
            if operation == "construct":
                source._NamespaceHistoryStateV1(dict(state))
            elif operation == "copy":
                copy.copy(state)
            elif operation == "deepcopy":
                copy.deepcopy(state)
            else:
                pickle.dumps(state)
        # Negative path must not damage the valid, still-unspent original.
        source._check(state)
        assert source._MOVED_BINDINGS[binding]["state_ref"]() is state
        assert source._MOVED_BINDINGS[binding]["acquisition_started"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("clone_kind", ["ordinary_dict", "exact_private_class"])
async def test_copied_or_rebound_state_cannot_claim_or_release_live_original(
    monkeypatch, clone_kind
):
    with _await_state(monkeypatch) as state:
        source._check(state)
        binding = state["binding"]
        clone = (
            dict(state)
            if clone_kind == "ordinary_dict"
            else dict.__new__(source._NamespaceHistoryStateV1)
        )
        clone.update(state)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="original_moved_binding_required",
        ):
            source._check(clone)
        source._release_moved(clone)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="moved_state_already_bound_or_unavailable",
        ):
            source._bind_moved_state(clone, key=source._HOOK_KEY)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="original_moved_binding_required",
        ):
            await source._acquire(clone)
        source._check(state)
        assert source._MOVED_BINDINGS[binding]["state_ref"]() is state
        assert source._MOVED_BINDINGS[binding]["acquisition_started"] is False


@pytest.mark.asyncio
async def test_direct_first_send_failure_releases_state_without_an_attempt_wrapper(
    monkeypatch, tmp_path
):
    with _await_state(monkeypatch) as state:
        source._check(state)
        binding = state["binding"]
        durable = tmp_path / "original-journal.bin"
        durable.write_bytes(b"retained-before-new-attempt")
        # The first-send burn succeeds; a missing private prepared-plan key
        # then fails inside the ACTUAL body before any source IO or root create.
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="native_namespace_history_source_unavailable",
        ):
            await source._acquire(state)
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert durable.read_bytes() == b"retained-before-new-attempt"
        assert not source._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["closed_lease", "expired_anchor", "changed_sha"])
async def test_direct_first_send_fence_failure_retires_unspent_original_before_retry(
    monkeypatch, tmp_path, failure
):
    with _await_state(monkeypatch) as state:
        source._check(state)
        binding = state["binding"]
        record = source._MOVED_BINDINGS[binding]
        assert record["acquisition_started"] is False
        assert record["state_ref"]() is state
        durable = tmp_path / "original-history.bin"
        durable.write_bytes(b"immutable-history-before-first-send")
        with monkeypatch.context() as changed:
            if failure == "closed_lease":
                changed.setattr(state["lease"]._lease, "active", False)
            elif failure == "expired_anchor":
                source.native.clock.native_stamp.calls += 61000
            else:
                changed.setattr(source, "sha", lambda _raw: "0" * 64)
            with pytest.raises(source.NativeNamespaceHistorySourceError):
                await source._acquire(state)
        assert state == {} and binding not in source._MOVED_BINDINGS
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="original_moved_binding_required",
        ):
            await source._acquire(state)
        assert durable.read_bytes() == b"immutable-history-before-first-send"
        assert not source._OWNERS
