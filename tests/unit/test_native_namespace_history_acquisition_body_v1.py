"""Prepared entire-body synthetic integration; run one exact case per cohort.

No native TLS/OKX/PG/schema seal or public caller DTO is accepted by this test.
The actual new _acquire orchestration and pure readers are never replaced.
"""

import asyncio
import json
from contextlib import contextmanager
from weakref import ref

import pytest

from app.trade_qualification import native_namespace_history_bundle_v1 as bundle
from app.trade_qualification import native_namespace_history_source_v1 as source
from tests.native_namespace_history_acquisition_fixture import (
    refresh_source_provider_pins,
    whole_acquisition_fixture,
)


@pytest.mark.asyncio
async def test_actual_full_acquire_and_readback_use_same_lease_without_current_renewal(
    monkeypatch, tmp_path
):
    assert not source._OWNERS
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        initial_lease, initial_client = state["lease"], state["private_client"]
        initial_windows = fixture.initial_windows
        initial_record = fixture.initial_record
        owner = await source._acquire(state)
        assert source._OWNERS.get(owner) is state
        assert (
            state["lease"] is initial_lease
            and state["private_client"] is initial_client
        )
        assert state["session"]._checked_read_session(state["plan"]) is initial_lease
        assert state["session"] is not state["moved"]["current_account"]["session"]
        assert state["session"]._used is True
        assert state["moved"]["historical"]["record"].record_json == initial_record
        assert fixture.initial_windows == initial_windows
        assert (
            state["facts"].packet.plan.contract_version == "ctcc.demo_account_plan.v6"
        )
        assert len(bundle.namespace_queries(state["facts"].packet)) == 52
        assert all(
            request.method == "GET" and request.headers["x-simulated-trading"] == "1"
            for request in fixture.io.harness.requests
        )
        assert len(fixture.io.harness.requests) == len(fixture.io.harness.script)
        assert len(fixture.retained) == 3  # Two V4 originals plus this exact V6 bundle.
        request_count = len(fixture.io.harness.requests)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError, match="attempt_already_spent"
        ):
            await source._acquire(state)
        assert len(fixture.io.harness.requests) == request_count
        assert source._OWNERS.get(owner) is state
        raw = state["facts"].bundle_raw
        document = json.loads(raw)
        assert (
            document["phase"]["original_current_expiry"]
            == initial_windows[-1].isoformat()
        )
        assert document["namespace_finality"] == "UNKNOWN"
        assert all(document[name] is False for name in bundle.FALSE_FIELDS)
        # Baseline valid full pure replay before each direct alias mutation.
        assert (
            bundle.replay_native_namespace_history_bundle_v1(
                raw, expected_sha256=state["facts"].bundle_sha256
            ).execution_authority
            is False
        )
        for alias in ("sha", "canonical", "utc_from_ns", "validate_stamps"):
            with monkeypatch.context() as altered:
                altered.setattr(bundle, alias, lambda *_args, **_kwargs: None)
                with pytest.raises(
                    bundle.NamespaceHistoryBundleError,
                    match="original_replay_function_changed",
                ):
                    bundle.replay_native_namespace_history_bundle_v1(
                        raw, expected_sha256=state["facts"].bundle_sha256
                    )
        facts = await source.consume_owned_native_namespace_history_v1(owner)
        assert facts.bundle_raw == raw and facts.execution_authority is False
        assert facts.namespace_finality == "UNKNOWN"
        assert state == {} and not source._OWNERS
        assert (fixture.raw_root / "source.bundle.json").read_bytes() == raw
        assert (fixture.raw_root / "accepted.json").exists()
        assert not (fixture.raw_root / "failed.json").exists()
        assert all(
            client.is_closed and response.is_closed
            for client, response in fixture.config_clients
        )
        fixture.io.harness.assert_closed()


@pytest.mark.asyncio
async def test_actual_success_owner_abandonment_releases_binding_and_blocks_saved_copy(
    monkeypatch, tmp_path
):
    assert not source._OWNERS
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        attempt = source._Attempt(source._acquire(fixture.state), fixture.state)
        owner = await attempt
        assert attempt._state is None
        assert source._OWNERS.get(owner) is fixture.state
        binding = fixture.state["binding"]
        state_ref, owner_ref = ref(fixture.state), ref(owner)
        saved_copy = dict(fixture.state)
        accepted_raw = (fixture.raw_root / "accepted.json").read_bytes()
        bundle_raw = (fixture.raw_root / "source.bundle.json").read_bytes()
        request_count = len(fixture.io.harness.requests)
        assert source._MOVED_BINDINGS[binding]["state_ref"]() is fixture.state
        # The fixture holds no hidden strong state across yield. Drop its one
        # explicit state field, then the successful owner; retain the completed
        # wrapper, binding, detached copy and durable evidence throughout.
        del fixture.state
        assert state_ref() is not None  # The genuine owner still owns state.
        del owner
        assert owner_ref() is None and state_ref() is None
        assert binding not in source._MOVED_BINDINGS
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="original_moved_binding_required",
        ):
            await source._acquire(saved_copy)
        exact_clone = dict.__new__(source._NamespaceHistoryStateV1)
        exact_clone.update(saved_copy)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="moved_state_already_bound_or_unavailable",
        ):
            source._bind_moved_state(exact_clone, key=source._HOOK_KEY)
        assert len(fixture.io.harness.requests) == request_count
        assert (fixture.raw_root / "accepted.json").read_bytes() == accepted_raw
        assert (fixture.raw_root / "source.bundle.json").read_bytes() == bundle_raw
        assert not (fixture.raw_root / "failed.json").exists()
        assert not source._OWNERS


@pytest.mark.asyncio
async def test_direct_first_send_cancellation_releases_binding_after_failure_retention(
    monkeypatch, tmp_path
):
    assert not source._OWNERS
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        source._check(state)
        binding = state["binding"]
        retained_before = {
            identifier: retained.bundle_raw
            for identifier, retained in fixture.retained.items()
        }
        current = asyncio.current_task()
        cancellation_count = current.cancelling()
        calls = []

        async def cancel_first_custody_read(_repository, **query):
            calls.append(query["bundle_id"])
            assert asyncio.current_task() is current
            current.cancel()
            await asyncio.sleep(0)
            raise AssertionError("cancelled native read cannot return")

        # Only the native DB read seam changes; actual _acquire, checked await,
        # same-task cancellation and failure publisher/cleanup execute intact.
        monkeypatch.setattr(
            source.custody.RuntimeBundleCustodyRepository,
            "read_retained_bundle",
            cancel_first_custody_read,
        )
        refresh_source_provider_pins(monkeypatch)
        source._check(state)
        try:
            with pytest.raises(asyncio.CancelledError):
                await source._acquire(state)
        finally:
            while current.cancelling() > cancellation_count:
                current.uncancel()
        assert len(calls) == 1
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not source._OWNERS and not fixture.io.harness.requests
        assert {
            identifier: retained.bundle_raw
            for identifier, retained in fixture.retained.items()
        } == retained_before
        assert json.loads((fixture.raw_root / "failed.json").read_bytes()) == {
            "schema_version": "ctcc.native_namespace_history_attempt_failed.v1",
            "outcome": "INCOMPLETE",
            "execution_authority": False,
        }
        assert (fixture.raw_root / "attempt.claim.json").exists()
        assert not (fixture.raw_root / "accepted.json").exists()


@pytest.mark.asyncio
async def test_failure_metadata_write_rejection_still_releases_original_private_state(
    monkeypatch, tmp_path
):
    assert not source._OWNERS
    async with whole_acquisition_fixture(
        monkeypatch, tmp_path, failure="custody_commit_unknown"
    ) as fixture:
        state = fixture.state
        source._check(state)
        binding = state["binding"]
        original_root = source.storage._root_context
        rejected = []

        class FailureMetadataIO:
            def __init__(self, directory):
                self.directory = directory

            def __getattr__(self, name):
                return getattr(self.directory, name)

            def publish(self, name, raw):
                if name == "failed.json":
                    rejected.append(name)
                    raise OSError("synthetic-final-failure-metadata-write-unknown")
                self.directory.publish(name, raw)

        @contextmanager
        def reject_failure_metadata(root):
            with original_root(root) as directory:
                yield FailureMetadataIO(directory)

        # The lowest filesystem write seam alone refuses the final failure
        # marker. Existing raw/B1 journals must survive and no owner may appear.
        monkeypatch.setattr(source.storage, "_root_context", reject_failure_metadata)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="failure_retention_incomplete",
        ):
            await source._acquire(state)
        assert rejected == ["failed.json"]
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not source._OWNERS and len(fixture.chains) == 3
        assert (fixture.raw_root / "attempt.claim.json").exists()
        assert not (fixture.raw_root / "failed.json").exists()
        assert not (fixture.raw_root / "accepted.json").exists()
        assert any(
            point.event.raw_body
            for points in fixture.chains.values()
            for point in points
        )
        fixture.io.harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "custody_commit_unknown",
        "custody_readback_changed",
        "native_context_exit",
        "native_context_exit_alias",
        "raw_context_exit",
    ],
)
async def test_actual_full_acquire_late_failures_keep_raw_and_never_register_owner(
    monkeypatch, tmp_path, failure
):
    assert not source._OWNERS
    async with whole_acquisition_fixture(
        monkeypatch, tmp_path, failure=failure
    ) as fixture:
        state = fixture.state
        binding = state["binding"]
        with pytest.raises(source.NativeNamespaceHistorySourceError):
            await source._acquire(state)
        # Direct-body failure owns cleanup, independently of wrapper/GC.
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not source._OWNERS
        assert len(fixture.chains) == 3
        assert all(
            points[-1].event.raw_body is None for points in fixture.chains.values()
        )
        assert any(
            point.event.raw_body
            for points in fixture.chains.values()
            for point in points
        )
        assert (fixture.raw_root / "attempt.claim.json").exists()
        failure_raw = (fixture.raw_root / "failed.json").read_bytes()
        assert json.loads(failure_raw)["outcome"] == "INCOMPLETE"
        assert json.loads(failure_raw)["execution_authority"] is False
        if failure.startswith("custody_"):
            assert not (fixture.raw_root / "accepted.json").exists()
        else:
            assert (fixture.raw_root / "source.bundle.json").exists()
            assert (fixture.raw_root / "accepted.json").exists()
        assert all(
            client.is_closed and response.is_closed
            for client, response in fixture.config_clients
        )
        fixture.io.harness.assert_closed()
