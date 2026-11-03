"""UNRUN carried-source secret-closure tests over explicit synthetic lower IO.

Actual account/bundle replay, the new scan, and original source body execute in
these prepared tests. No fixture is evidence of native signing/TLS/PG/G6.
"""

import asyncio
import builtins
import json
from types import FunctionType

import pytest

from app.trade_qualification import native_namespace_history_source_v1 as source
from tests import native_namespace_history_acquisition_fixture as acquisition_fixture
from tests.native_namespace_history_acquisition_fixture import (
    refresh_source_provider_pins,
    whole_acquisition_fixture,
)
from tests.unit.test_native_namespace_history_source_v1 import _await_state

NEW_SIGNATURE = "U1lOVEhFVElDX09OTFlfTkVXX1NJR05BVFVSRV9FQ0hPPQ=="
CLEAN_NEW_SIGNATURE = "U1lOVEhFVElDX09OTFlfQUJTRU5UX1NJR05BVFVSRV9PTkxZPQ=="


def _tokens(state, *later):
    return (*state["lease"]._credentials, state["lease"].binding_id, *later)


def _retained(fixture):
    return {
        identifier: value.bundle_raw for identifier, value in fixture.retained.items()
    }


def _original_bytes(state):
    return (
        state["moved"]["historical"]["phase_source"]["bundle_raw"],
        *state["hop_source_bundles"],
    )


@pytest.mark.asyncio
async def test_benign_carried_sources_keep_exact_bytes_and_metadata_label_exception(
    monkeypatch, tmp_path
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        source._check(state)
        retained = _retained(fixture)
        originals = _original_bytes(state)
        label = state["lease"].binding_id
        tokens = _tokens(state, CLEAN_NEW_SIGNATURE)
        # The actual old packet deliberately contains this exact nonsecret label
        # in plan/page metadata. A blanket label ban would reject this baseline.
        for raw in originals:
            document = source.account.decode(raw, source.custody.MAX_BUNDLE_BYTES)
            chain = source.account._chain_replay(document["chain"])
            assert any(
                point.event.packet_payload is not None
                and label.encode() in point.event.packet_payload
                for point in chain
            )
        assert source._secret_checked_carried_sources(state, tokens) is None
        assert _original_bytes(state) == originals and _retained(fixture) == retained
        assert not fixture.io.harness.requests and not source._OWNERS
        source._check(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("carried_phase", [1, 2])
async def test_new_signature_hidden_in_valid_carried_body_is_rejected_before_rehex(
    monkeypatch, tmp_path, carried_phase
):
    async with whole_acquisition_fixture(
        monkeypatch,
        tmp_path,
        carried_note=NEW_SIGNATURE,
        carried_note_phase=carried_phase,
    ) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        originals, retained = _original_bytes(state), _retained(fixture)
        baseline = _tokens(state, CLEAN_NEW_SIGNATURE)
        # The marker was a predeclared benign original source value. Each
        # complete source passes the actual original replay and new scan before
        # that marker is added to this invocation's NEW known signature set.
        assert source._secret_checked_carried_sources(state, baseline) is None
        late_tokens = (*baseline, NEW_SIGNATURE)
        assert all(NEW_SIGNATURE.encode() not in raw for raw in originals)
        for raw in originals:
            source.runtime._secret_checked(raw, late_tokens)
        target = originals[carried_phase - 1]
        document = source.account.decode(target, source.custody.MAX_BUNDLE_BYTES)
        chain = source.account._chain_replay(document["chain"])
        assert any(
            point.event.raw_body is not None
            and NEW_SIGNATURE.encode() in point.event.raw_body
            for point in chain
        )
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="carried_source_secret_echo",
        ):
            source._secret_checked_carried_sources(state, late_tokens)
        assert _original_bytes(state) == originals and _retained(fixture) == retained
        assert not fixture.io.harness.requests and not source._OWNERS


@pytest.mark.asyncio
async def test_known_token_in_valid_carried_clock_is_checked_as_original_plaintext(
    monkeypatch, tmp_path
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        originals, retained = _original_bytes(state), _retained(fixture)
        tokens = _tokens(state, CLEAN_NEW_SIGNATURE)
        assert source._secret_checked_carried_sources(state, tokens) is None
        # Use an existing original host diagnostic string as a synthetic newly
        # known token. No accepted clock, sample or signed profile is rewritten.
        document = source.account.decode(originals[0], source.custody.MAX_BUNDLE_BYTES)
        clock = source.account._unhex(
            document["clock_files"]["host-before.json"], source.storage.MAX_PART
        )
        late = json.loads(clock)["observation"]["diagnostic"]["profile_id"]
        assert late.encode() in clock and all(
            late.encode() not in raw for raw in originals
        )
        for raw in originals:
            source.runtime._secret_checked(raw, (*tokens, late))
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="carried_source_secret_echo",
        ):
            source._secret_checked_carried_sources(state, (*tokens, late))
        assert _original_bytes(state) == originals and _retained(fixture) == retained
        assert not fixture.io.harness.requests and not source._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leaf",
    ["saved_scan", "runtime_scan", "json_scan", "packet_scan", "declared_unhex"],
)
async def test_saved_scan_refuses_replaced_direct_secret_leaves_after_valid_baseline(
    monkeypatch, tmp_path, leaf
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        tokens = _tokens(state, CLEAN_NEW_SIGNATURE)
        scan = source._secret_checked_carried_sources
        assert scan(state, tokens) is None
        invoked = []

        def replaced(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("replaced secret leaf must not execute")

        owner, name = {
            "saved_scan": (source, "_secret_checked_carried_sources"),
            "runtime_scan": (source.runtime, "_secret_checked"),
            "json_scan": (source.collector, "_no_secret_json"),
            "packet_scan": (source.journal, "_check_owned_packet_for_storage"),
            "declared_unhex": (source.account, "_unhex"),
        }[leaf]
        with monkeypatch.context() as changed:
            changed.setattr(owner, name, replaced)
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="original_function_or_policy_changed",
            ):
                scan(state, tokens)
        assert not invoked and not fixture.io.harness.requests and not source._OWNERS
        source._check(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_hop", "changed_digest", "malformed_clock"])
async def test_changed_or_incomplete_carried_sources_are_rejected_without_filling_parts(
    monkeypatch, tmp_path, damage
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        tokens = _tokens(state, CLEAN_NEW_SIGNATURE)
        assert source._secret_checked_carried_sources(state, tokens) is None
        originals, retained = _original_bytes(state), _retained(fixture)
        if damage == "missing_hop":
            state["hop_source_bundles"] = ()
        elif damage == "changed_digest":
            state["hop_source_bundles"] = (originals[1] + b" ",)
        else:
            document = source.account.decode(
                originals[1], source.custody.MAX_BUNDLE_BYTES
            )
            document["clock_files"]["host-before.json"] = "not-a-hex-component"
            state["hop_source_bundles"] = (source.canonical(document),)
        with pytest.raises(
            (
                source.NativeNamespaceHistorySourceError,
                source.account.CurrentAccountSourceV4Error,
            )
        ):
            source._secret_checked_carried_sources(state, tokens)
        assert _retained(fixture) == retained
        assert not fixture.io.harness.requests and not source._OWNERS


@pytest.mark.asyncio
async def test_actual_acquire_new_signature_echo_in_carried_source_retains_journals_and_denies_owner(
    monkeypatch, tmp_path
):
    async with whole_acquisition_fixture(
        monkeypatch, tmp_path, carried_note=NEW_SIGNATURE
    ) as fixture:
        state = fixture.state
        source._check(state)
        binding = state["binding"]
        originals = _retained(fixture)
        signed_request = source.collector._signed_request

        def synthetic_new_signing_provider(*args, **kwargs):
            # Explicit lower synthetic signing/HTTP seam only. The dummy Mock
            # server sees this predeclared signature; no native signing claim.
            request, _signature = signed_request(*args, **kwargs)
            request.headers["OK-ACCESS-SIGN"] = NEW_SIGNATURE
            return request, NEW_SIGNATURE

        monkeypatch.setattr(
            source.collector, "_signed_request", synthetic_new_signing_provider
        )
        refresh_source_provider_pins(monkeypatch)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="carried_source_secret_echo",
        ):
            await source._acquire(state)
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not source._OWNERS and _retained(fixture) == originals
        assert len(fixture.chains) == 3  # New original B1 also remains retained.
        assert any(
            point.event.raw_body
            for points in fixture.chains.values()
            for point in points
        )
        assert (fixture.raw_root / "attempt.claim.json").exists()
        assert (
            json.loads((fixture.raw_root / "failed.json").read_bytes())["outcome"]
            == "INCOMPLETE"
        )
        assert not (fixture.raw_root / "source.bundle.json").exists()
        assert not (fixture.raw_root / "accepted.json").exists()
        fixture.io.harness.assert_closed()


# These new canaries are also UNRUN. They check ordinary source-integrity
# contracts, not arbitrary interpreter integrity or a native exchange source.
def _finite_code_probe(*_args, **_kwargs):
    _R8_FINITE_PROBE.append(True)  # noqa: F821 -- installed only by a canary.


def _same_filename_code_probe(monkeypatch, function, invoked):
    monkeypatch.setitem(function.__globals__, "_R8_FINITE_PROBE", invoked)
    return _finite_code_probe.__code__.replace(
        co_filename=function.__code__.co_filename,
        co_name=function.__code__.co_name,
        co_qualname=function.__code__.co_qualname,
    )


def _finite_leaf(name):
    return {
        "saved_scan": (source, "_secret_checked_carried_sources"),
        "saved_event": (source, "_secret_checked_carried_event"),
        "runtime_scan": (source.runtime, "_secret_checked"),
        "raw_scan": (source.collector, "_no_secrets"),
        "json_scan": (source.collector, "_no_secret_json"),
        "event_replay": (source.journal, "checked_event"),
        "packet_scan": (source.journal, "_check_owned_packet_for_storage"),
        "account_replay": (source.account, "replay_current_account_bundle_v4"),
        "declared_unhex": (source.account, "_unhex"),
        "json_decode": (source.account, "decode"),
    }[name]


@pytest.mark.parametrize(
    "leaf",
    [
        "saved_scan",
        "saved_event",
        "runtime_scan",
        "raw_scan",
        "json_scan",
        "event_replay",
        "packet_scan",
        "account_replay",
        "declared_unhex",
        "json_decode",
    ],
)
def test_source_compiled_contract_refuses_in_place_same_filename_leaf_code(
    monkeypatch, leaf
):
    source._pins()  # The genuine existing source contract succeeds first.
    owner, name = _finite_leaf(leaf)
    original = getattr(owner, name)
    invoked = []
    forged = _same_filename_code_probe(monkeypatch, original, invoked)
    assert forged.co_filename == original.__code__.co_filename
    assert forged.co_qualname == original.__code__.co_qualname
    with monkeypatch.context() as changed:
        changed.setattr(original, "__code__", forged)
        assert getattr(owner, name) is original  # No identity pin changes.
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="source_callable_contract_changed",
        ):
            source._pins()
    assert not invoked
    source._pins()


@pytest.mark.parametrize("lookup", ["type", "getattr", "any", "len", "zip"])
def test_source_contract_rejects_global_builtin_shadows_without_invoking_them(
    monkeypatch, lookup
):
    source._pins()
    builtin = getattr(builtins, lookup)
    invoked = []

    def shadow(*args, **kwargs):
        invoked.append(True)
        return builtin(*args, **kwargs)

    with monkeypatch.context() as changed:
        changed.setattr(source, lookup, shadow, raising=False)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="source_callable_contract_changed",
        ):
            source._pins()
    assert not invoked
    source._pins()


@pytest.mark.parametrize(
    "change", ["defaults", "kwdefaults", "foreign_globals", "empty_closure"]
)
def test_same_source_code_requires_original_defaults_closure_and_namespace(
    monkeypatch, change
):
    source._pins()
    original = source._secret_checked_carried_sources
    assert original.__defaults__ is None and original.__kwdefaults__ is None
    assert original.__closure__ is None
    with monkeypatch.context() as changed:
        if change == "defaults":
            changed.setattr(original, "__defaults__", (None,))
        elif change == "kwdefaults":
            changed.setattr(original, "__kwdefaults__", {"extra": None})
        else:
            namespace = (
                dict(original.__globals__)
                if change == "foreign_globals"
                else original.__globals__
            )
            closure = () if change == "empty_closure" else None
            replacement = FunctionType(
                original.__code__,
                namespace,
                original.__name__,
                original.__defaults__,
                closure,
            )
            replacement.__kwdefaults__ = original.__kwdefaults__
            assert replacement.__code__ is original.__code__
            assert replacement.__code__.co_filename == original.__code__.co_filename
            changed.setattr(source, "_secret_checked_carried_sources", replacement)
            # Refreshing the older observed identity pins cannot bless a
            # foreign namespace or closure with identical source code.
            refresh_source_provider_pins(changed)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError,
            match="source_callable_contract_changed",
        ):
            source._pins()
    source._pins()


@pytest.mark.asyncio
async def test_preexisting_same_filename_noop_cannot_become_first_acquisition_baseline(
    monkeypatch, tmp_path
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        source._check(state)
        binding = state["binding"]
        original = source._secret_checked_carried_sources
        invoked = []
        forged = _same_filename_code_probe(monkeypatch, original, invoked)
        with monkeypatch.context() as changed:
            changed.setattr(original, "__code__", forged)
            # The function exists before first acquisition; its filename,
            # namespace, identity and source bytes still match the originals.
            refresh_source_provider_pins(changed)
            assert source._secret_checked_carried_sources is original
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await source._acquire(state)
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not invoked and not fixture.io.harness.requests and not source._OWNERS
        assert not (fixture.raw_root / "accepted.json").exists()
        assert not (fixture.raw_root / "source.bundle.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["pin_code", "pin_defaults", "verifier_code", "verifier_defaults"]
)
async def test_actual_first_send_checks_checker_code_and_captured_defaults_before_call(
    monkeypatch, tmp_path, change
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        source._check(state)
        binding = state["binding"]
        target = (
            source._pins
            if change.startswith("pin_")
            else source._assert_source_callable
        )
        invoked = []
        forged = _same_filename_code_probe(monkeypatch, target, invoked)

        def default_probe(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("changed captured builtin must not run")

        with monkeypatch.context() as changed:
            if change.endswith("code"):
                changed.setattr(target, "__code__", forged)
            else:
                changed.setattr(
                    target, "__defaults__", (default_probe, *target.__defaults__[1:])
                )
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await source._acquire(state)
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not invoked and not fixture.io.harness.requests and not source._OWNERS
        assert not (fixture.raw_root / "accepted.json").exists()


@pytest.mark.asyncio
async def test_actual_acquire_rejects_inflight_guard_code_change_with_new_signature_collision(
    monkeypatch, tmp_path
):
    # Genuine actual-body success is established separately, with an old
    # carried value which is not a signature of that baseline acquisition.
    baseline_path = tmp_path / "baseline"
    baseline_path.mkdir()
    with monkeypatch.context() as baseline_patch:
        async with whole_acquisition_fixture(
            baseline_patch, baseline_path, carried_note=NEW_SIGNATURE
        ) as baseline:
            owner = await source._acquire(baseline.state)
            assert source._OWNERS.get(owner) is baseline.state
            assert (baseline.raw_root / "accepted.json").exists()
            assert baseline.io.harness.requests
            assert all(
                request.headers["OK-ACCESS-SIGN"] != NEW_SIGNATURE
                for request in baseline.io.harness.requests
            )
            owner.close()
            assert not source._OWNERS
    attack_path = tmp_path / "attack"
    attack_path.mkdir()
    with monkeypatch.context() as attack_patch:
        async with whole_acquisition_fixture(
            attack_patch, attack_path, carried_note=NEW_SIGNATURE
        ) as fixture:
            state = fixture.state
            source._check(state)
            binding = state["binding"]
            originals = _retained(fixture)
            carried = (
                _original_bytes(state)
                if "hop_source_bundles" in state
                else (state["moved"]["historical"]["phase_source"]["bundle_raw"],)
            )
            assert all(NEW_SIGNATURE.encode() not in raw for raw in carried)
            original = source._secret_checked_carried_sources
            invoked, signed_calls = [], []
            forged = _same_filename_code_probe(attack_patch, original, invoked)
            signed_request = source.collector._signed_request

            def synthetic_signing_then_change_guard(*args, **kwargs):
                request, _signature = signed_request(*args, **kwargs)
                signed_calls.append(True)
                attack_patch.setattr(original, "__code__", forged)
                request.headers["OK-ACCESS-SIGN"] = NEW_SIGNATURE
                return request, NEW_SIGNATURE

            attack_patch.setattr(
                source.collector, "_signed_request", synthetic_signing_then_change_guard
            )
            refresh_source_provider_pins(attack_patch)
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await source._acquire(state)
            assert signed_calls and not invoked
            assert state == {} and binding not in source._MOVED_BINDINGS
            assert not source._OWNERS and _retained(fixture) == originals
            assert (fixture.raw_root / "attempt.claim.json").exists()
            assert (
                json.loads((fixture.raw_root / "failed.json").read_bytes())["outcome"]
                == "INCOMPLETE"
            )
            assert not (fixture.raw_root / "accepted.json").exists()
            assert not (fixture.raw_root / "source.bundle.json").exists()
            fixture.io.harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["pin_code", "pin_defaults", "verifier_code", "verifier_defaults"]
)
async def test_actual_await_refuses_late_checker_mutation_before_invoking_it(
    monkeypatch, change
):
    with _await_state(monkeypatch) as state:
        assert (
            await source._await(state, asyncio.sleep(0, result="baseline"))
            == "baseline"
        )
        target = (
            source._pins
            if change.startswith("pin_")
            else source._assert_source_callable
        )
        invoked = []
        forged = _same_filename_code_probe(monkeypatch, target, invoked)

        def default_probe(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("changed late checker must not execute")

        with monkeypatch.context() as changed:

            async def change_inside_original_await():
                await asyncio.sleep(0)
                if change.endswith("code"):
                    changed.setattr(target, "__code__", forged)
                else:
                    changed.setattr(
                        target,
                        "__defaults__",
                        (default_probe, *target.__defaults__[1:]),
                    )
                return "changed"

            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await source._await(state, change_inside_original_await())
        assert not invoked
        source._check(state)


# Retained R3 canaries close the reviewed first-send, cleanup and route-raw
# findings. Every case remains UNRUN until separately admitted on exact source.
def _install_original_route_note(monkeypatch, note, phase=1):
    original = acquisition_fixture._config
    calls = []

    def declared_config_source(**kwargs):
        raw = original(**kwargs)
        calls.append(True)
        if len(calls) == phase:
            document = source.account.decode(raw, 8192)
            document["data"][0]["audit_note"] = note
            return source.canonical(document)
        return raw

    # The synthetic HTTP body is declared before either original native issuer
    # sees it. Accepted source bytes/hashes/timestamps are never rewritten.
    monkeypatch.setattr(acquisition_fixture, "_config", declared_config_source)


@pytest.mark.asyncio
async def test_public_consume_first_send_refuses_changed_checker_before_probe(
    monkeypatch, tmp_path
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        owner = await source._acquire(state)
        assert source._OWNERS.get(owner) is state
        binding = state["binding"]
        accepted = (fixture.raw_root / "accepted.json").read_bytes()
        raw = (fixture.raw_root / "source.bundle.json").read_bytes()
        request_count = len(fixture.io.harness.requests)
        attempt = source.consume_owned_native_namespace_history_v1(owner)
        assert owner not in source._OWNERS
        target, invoked = source._check, []
        forged = _same_filename_code_probe(monkeypatch, target, invoked)
        with monkeypatch.context() as changed:
            changed.setattr(target, "__code__", forged)
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await attempt
        assert not invoked and state == {} and attempt._state is None
        assert binding not in source._MOVED_BINDINGS and not source._OWNERS
        assert len(fixture.io.harness.requests) == request_count
        assert (fixture.raw_root / "accepted.json").read_bytes() == accepted
        assert (fixture.raw_root / "source.bundle.json").read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup,behavior",
    [("type", "delegate"), ("type", "raise"), ("dict", "delegate"), ("dict", "raise")],
)
async def test_actual_acquire_builtin_shadow_rejection_keeps_cleanup_canonical(
    monkeypatch, tmp_path, lookup, behavior
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        source._check(state)
        binding = state["binding"]
        invoked = []
        original = getattr(builtins, lookup)

        def shadow(*args, **kwargs):
            invoked.append(True)
            if behavior == "raise":
                raise AssertionError("raw builtin shadow must never execute")
            return original(*args, **kwargs)

        with monkeypatch.context() as changed:
            changed.setattr(source, lookup, shadow, raising=False)
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await source._acquire(state)
        assert not invoked and state == {} and binding not in source._MOVED_BINDINGS
        assert not fixture.io.harness.requests and not source._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup,behavior",
    [("type", "delegate"), ("type", "raise"), ("dict", "delegate"), ("dict", "raise")],
)
async def test_saved_await_first_send_shadow_does_not_run_probe_or_leave_coroutine(
    monkeypatch, lookup, behavior
):
    with _await_state(monkeypatch) as state:
        assert (
            await source._await(state, asyncio.sleep(0, result="baseline"))
            == "baseline"
        )
        saved, invoked, body_started = source._await, [], []
        original = getattr(builtins, lookup)

        def shadow(*args, **kwargs):
            invoked.append(True)
            if behavior == "raise":
                raise AssertionError("raw await builtin must not execute")
            return original(*args, **kwargs)

        async def never_started():
            body_started.append(True)
            return "unavailable"

        operation = never_started()
        with monkeypatch.context() as changed:
            changed.setattr(source, lookup, shadow, raising=False)
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await saved(state, operation)
        assert not invoked and not body_started and operation.cr_frame is None
        source._check(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["type", "isinstance", "dict"])
async def test_exact_weak_binding_cleanup_uses_captured_builtin_references(
    monkeypatch, lookup
):
    with _await_state(monkeypatch) as state:
        source._check(state)
        binding = state["binding"]
        invoked = []

        def shadow(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("cleanup must not invoke a raw builtin shadow")

        with monkeypatch.context() as changed:
            changed.setattr(source, lookup, shadow, raising=False)
            source._release_moved(state)
        assert not invoked and binding not in source._MOVED_BINDINGS
    # The actual weak callback uses the same canonical cleanup references.
    # Invoke it with the exact registered weak reference, not a copied DTO.
    with _await_state(monkeypatch) as state:
        source._check(state)
        binding = state["binding"]
        state_ref = source._MOVED_BINDINGS[binding]["state_ref"]
        invoked = []

        def callback_shadow(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("weak cleanup must not invoke a raw builtin shadow")

        with monkeypatch.context() as changed:
            changed.setattr(source, lookup, callback_shadow, raising=False)
            source._release_dead_state(source.ref(binding), state_ref)
        assert not invoked and binding not in source._MOVED_BINDINGS


@pytest.mark.asyncio
async def test_actual_await_source_profile_refuses_same_object_code_change_before_next_io(
    monkeypatch,
):
    with _await_state(monkeypatch) as state:
        assert (
            await source._await(state, asyncio.sleep(0, result="baseline"))
            == "baseline"
        )
        original, invoked = source._await, []
        forged = _same_filename_code_probe(monkeypatch, original, invoked)
        with monkeypatch.context() as changed:

            async def mutate_original_await_during_owned_io():
                await asyncio.sleep(0)
                changed.setattr(original, "__code__", forged)
                return "changed"

            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await original(state, mutate_original_await_during_owned_io())
        assert not invoked
        source._check(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("carried_phase", [1, 2])
async def test_declared_earlier_route_raw_is_scanned_with_later_terminal_signature(
    monkeypatch, tmp_path, carried_phase
):
    _install_original_route_note(monkeypatch, NEW_SIGNATURE, carried_phase)
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        originals, retained = _original_bytes(state), _retained(fixture)
        tokens = _tokens(state, CLEAN_NEW_SIGNATURE)
        assert source._secret_checked_carried_sources(state, tokens) is None
        document = source.account.decode(
            originals[carried_phase - 1], source.custody.MAX_BUNDLE_BYTES
        )
        route_raw = source.account._unhex(
            document["route_proof"]["config_raw_hex"], 8192
        )
        assert NEW_SIGNATURE.encode() in route_raw
        assert all(NEW_SIGNATURE.encode() not in raw for raw in originals)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError, match="carried_source_secret_echo"
        ):
            source._secret_checked_carried_sources(state, (*tokens, NEW_SIGNATURE))
        assert _original_bytes(state) == originals and _retained(fixture) == retained
        assert not fixture.io.harness.requests and not source._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["invalid_hex", "hash_conflict"])
async def test_declared_route_raw_malformed_or_conflicting_source_is_not_repaired(
    monkeypatch, tmp_path, damage
):
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        tokens = _tokens(state, CLEAN_NEW_SIGNATURE)
        assert source._secret_checked_carried_sources(state, tokens) is None
        originals, retained = _original_bytes(state), _retained(fixture)
        document = source.account.decode(originals[0], source.custody.MAX_BUNDLE_BYTES)
        route = document["route_proof"]
        if damage == "invalid_hex":
            route["config_raw_hex"] = "not-hex"
        else:
            route["config_raw_hex"] = (
                source.account._unhex(route["config_raw_hex"], 8192) + b" "
            ).hex()
        altered = source.canonical(document)
        # A forensic caller hash is allowed only to exercise the original pure
        # replay rejection. It is never installed into the private owner state.
        with pytest.raises(source.account.CurrentAccountSourceV4Error):
            source.account.replay_current_account_bundle_v4(
                altered, expected_sha256=source.sha(altered)
            )
        assert _original_bytes(state) == originals and _retained(fixture) == retained
        assert source._secret_checked_carried_sources(state, tokens) is None


@pytest.mark.asyncio
async def test_actual_acquire_late_signature_in_earlier_route_raw_preserves_journals_and_denies_owner(
    monkeypatch, tmp_path
):
    _install_original_route_note(monkeypatch, NEW_SIGNATURE)
    async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
        state = fixture.state
        await source._historical_custody(state)
        assert (
            source._secret_checked_carried_sources(
                state, _tokens(state, CLEAN_NEW_SIGNATURE)
            )
            is None
        )
        binding, retained = state["binding"], _retained(fixture)
        signed_request = source.collector._signed_request

        def declared_new_signing_provider(*args, **kwargs):
            request, _signature = signed_request(*args, **kwargs)
            request.headers["OK-ACCESS-SIGN"] = NEW_SIGNATURE
            return request, NEW_SIGNATURE

        monkeypatch.setattr(
            source.collector, "_signed_request", declared_new_signing_provider
        )
        refresh_source_provider_pins(monkeypatch)
        with pytest.raises(
            source.NativeNamespaceHistorySourceError, match="carried_source_secret_echo"
        ):
            await source._acquire(state)
        assert state == {} and binding not in source._MOVED_BINDINGS
        assert not source._OWNERS and _retained(fixture) == retained
        assert len(fixture.chains) == 3
        assert (fixture.raw_root / "attempt.claim.json").exists()
        assert (
            json.loads((fixture.raw_root / "failed.json").read_bytes())["outcome"]
            == "INCOMPLETE"
        )
        assert not (fixture.raw_root / "accepted.json").exists()
        assert not (fixture.raw_root / "source.bundle.json").exists()
        fixture.io.harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["acquire", "await"])
async def test_rebound_cleanup_alias_is_rejected_without_invocation_and_cleanup_survives(
    monkeypatch, tmp_path, stage
):
    invoked = []

    def alias_probe(*_args, **_kwargs):
        invoked.append(True)
        raise AssertionError("a rejected cleanup alias must never execute")

    original = source._SOURCE_PIN_CHECKER_DEFAULTS[2]
    altered = (*original[:2], alias_probe, *original[3:])
    if stage == "acquire":
        async with whole_acquisition_fixture(monkeypatch, tmp_path) as fixture:
            state = fixture.state
            source._check(state)
            binding = state["binding"]
            with monkeypatch.context() as changed:
                changed.setattr(source, "_SOURCE_CLEANUP_REFERENCES", altered)
                with pytest.raises(
                    source.NativeNamespaceHistorySourceError,
                    match="source_callable_contract_changed",
                ):
                    await source._acquire(state)
            assert state == {} and binding not in source._MOVED_BINDINGS
            assert not fixture.io.harness.requests and not source._OWNERS
    else:
        with _await_state(monkeypatch) as state:
            assert (
                await source._await(state, asyncio.sleep(0, result="baseline"))
                == "baseline"
            )
            operation = asyncio.sleep(0)
            with monkeypatch.context() as changed:
                changed.setattr(source, "_SOURCE_CLEANUP_REFERENCES", altered)
                with pytest.raises(
                    source.NativeNamespaceHistorySourceError,
                    match="source_callable_contract_changed",
                ):
                    await source._await(state, operation)
            assert operation.cr_frame is None
            source._check(state)
    assert not invoked


@pytest.mark.asyncio
async def test_rejected_await_kwdefaults_never_drive_rejection_cleanup(monkeypatch):
    with _await_state(monkeypatch) as state:
        assert (
            await source._await(state, asyncio.sleep(0, result="baseline"))
            == "baseline"
        )
        saved, invoked = source._await, []

        def rejected_default_probe(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("rejected defaults must not drive cleanup")

        original = source._SOURCE_PIN_CHECKER_DEFAULTS[2]
        altered = (*original[:2], rejected_default_probe, *original[3:])
        operation = asyncio.sleep(0)
        with monkeypatch.context() as changed:
            changed.setattr(saved, "__kwdefaults__", {"_cleanup": altered})
            with pytest.raises(
                source.NativeNamespaceHistorySourceError,
                match="source_callable_contract_changed",
            ):
                await saved(state, operation)
        assert not invoked and operation.cr_frame is None
        source._check(state)


async def _actual_original_route_attempt(monkeypatch, tmp_path):
    # Preserve the genuine temporary filesystem publisher before the existing
    # fixture installs its explicitly synthetic in-memory repository seams.
    original_root = source.storage._root_context
    root = tmp_path / "original-attempt"
    root.mkdir()
    with acquisition_fixture.original_io_setup(
        monkeypatch, tmp_path / "route-provider"
    ) as io:
        with original_root(root) as directory:
            observation = await source.operational._probe_initial_demo_operational_route_retained_v4(
                io.session, io.args["private_client"], proof_directory=directory
            )
            route_state = source.operational._consume_initial_route_state_v4(
                observation, io.session, io.args["private_client"]
            )
            attempt = directory.read("account-route-attempt.v4.json", 32768)
            attempt_plan = directory.read("account-route-attempt.v4.plan.json", 32768)
            assert (
                directory.read("account-route-attempt.v4.raw", 8192)
                == route_state["raw"]
            )
        pin = io.session._pin
        # These are three original bodies under declared synthetic HTTP/TLS/
        # clock IO. They never establish native transport/account authority.
        assert io.route_response.is_closed and io.route_client.is_closed
        assert not io.harness.requests
        io.harness.assert_closed()
        assert not source.operational._PRIVATE_ATTEMPTS
    return attempt, attempt_plan, route_state, pin, root


@pytest.mark.asyncio
async def test_original_route_attempt_actual_producer_roundtrip_has_both_real_digests(
    monkeypatch,
    tmp_path,
):
    (
        attempt,
        attempt_plan,
        route_state,
        pin,
        root,
    ) = await _actual_original_route_attempt(monkeypatch, tmp_path)
    result = source._replay_original_route_attempt(
        attempt,
        attempt_plan_raw=attempt_plan,
        route_state=route_state,
        account_plan_sha256=pin,
    )
    assert "raw_sha256" not in result
    assert result["plan_sha256"] == source.sha(attempt_plan)
    assert result["plan_sha256"] != pin
    assert source.account.decode(attempt_plan, 32768)["account_plan_sha256"] == pin
    assert route_state["plan_sha256"] == pin
    assert (root / "account-route-attempt.v4.plan.json").read_bytes() == attempt_plan
    assert (
        result["observed_sha256"]
        == result["retained_sha256"]
        == source.sha(route_state["raw"])
    )
    assert (
        result["observed_bytes"] == result["retained_bytes"] == len(route_state["raw"])
    )
    assert all(
        result[field] is False
        for field in (
            "source_authenticity_verified",
            "account_complete",
            "execution_authority",
        )
    )
    assert (root / "account-route-attempt.v4.json").read_bytes() == attempt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "observed_digest",
        "retained_digest",
        "observed_size",
        "retained_size",
        "truncated",
        "missing_observed_digest",
        "missing_retained_digest",
        "legacy_raw_hash_only",
        "conflicting_legacy_hash",
        "status",
        "stream_incomplete",
        "raw_name",
        "omitted_for_safety",
        "plan",
        "request_start",
        "headers_received",
        "body_completed",
        "tls_peer",
        "malformed_headers",
        "omitted_count_bool",
        "authority_true",
        "schema",
        "extra_field",
    ],
)
async def test_original_route_attempt_replay_refuses_missing_conflicting_or_incomplete_source(
    monkeypatch, tmp_path, damage
):
    (
        attempt,
        attempt_plan,
        route_state,
        pin,
        root,
    ) = await _actual_original_route_attempt(monkeypatch, tmp_path)
    baseline = source._replay_original_route_attempt(
        attempt,
        attempt_plan_raw=attempt_plan,
        route_state=route_state,
        account_plan_sha256=pin,
    )
    document = source.account.decode(attempt, 32768)
    if damage in {"observed_digest", "retained_digest"}:
        document[
            "observed_sha256" if damage == "observed_digest" else "retained_sha256"
        ] = "0" * 64
    elif damage in {"observed_size", "retained_size"}:
        document[
            "observed_bytes" if damage == "observed_size" else "retained_bytes"
        ] += 1
    elif damage in {"missing_observed_digest", "missing_retained_digest"}:
        document.pop(
            "observed_sha256"
            if damage == "missing_observed_digest"
            else "retained_sha256"
        )
    elif damage == "legacy_raw_hash_only":
        document["raw_sha256"] = document.pop("retained_sha256")
    elif damage == "conflicting_legacy_hash":
        document["raw_sha256"] = "0" * 64
    elif damage in {"truncated", "omitted_for_safety", "authority_true"}:
        document[
            {
                "truncated": "truncated",
                "omitted_for_safety": "raw_omitted_for_credential_safety",
                "authority_true": "execution_authority",
            }[damage]
        ] = True
    elif damage == "status":
        document["status"] = 201
    elif damage == "stream_incomplete":
        document["body_stream_exhausted"] = False
    elif damage == "raw_name":
        document["raw_name"] = "other.raw"
    elif damage == "plan":
        document["plan_sha256"] = "0" * 64
    elif damage in {"request_start", "headers_received", "body_completed"}:
        document[damage] = dict(document[damage], utc_ns=document[damage]["utc_ns"] + 1)
    elif damage == "tls_peer":
        document["tls_peer_sha256"] = "0" * 64
    elif damage == "malformed_headers":
        document["safe_response_headers"] = [["incomplete"]]
    elif damage == "omitted_count_bool":
        document["omitted_header_count"] = True
    elif damage == "schema":
        document["schema_version"] = "ctcc.private_demo_route_attempt.v999"
    else:
        assert damage == "extra_field"
        document["unreviewed"] = False
    altered = source.canonical(document)
    with pytest.raises(
        source.NativeNamespaceHistorySourceError,
        match="namespace_history_route_original_attempt_changed",
    ):
        source._replay_original_route_attempt(
            altered,
            attempt_plan_raw=attempt_plan,
            route_state=route_state,
            account_plan_sha256=pin,
        )
    assert (root / "account-route-attempt.v4.json").read_bytes() == attempt
    assert (root / "account-route-attempt.v4.raw").read_bytes() == route_state["raw"]
    assert (
        source._replay_original_route_attempt(
            attempt,
            attempt_plan_raw=attempt_plan,
            route_state=route_state,
            account_plan_sha256=pin,
        )
        == baseline
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "missing_account_plan",
        "rehash_account_plan",
        "rehash_origin",
        "rehash_method",
        "rehash_endpoint",
        "rehash_retries",
        "rehash_schema",
        "rehash_extra",
        "confused_account_argument",
        "route_state_pin",
        "route_class",
        "attempt_digest_is_account_pin",
    ],
)
async def test_original_retained_wrapper_distinct_plan_links_reject_rehashed_confusion(
    monkeypatch, tmp_path, damage
):
    (
        attempt,
        attempt_plan,
        route_state,
        pin,
        root,
    ) = await _actual_original_route_attempt(monkeypatch, tmp_path)
    baseline = source._replay_original_route_attempt(
        attempt,
        attempt_plan_raw=attempt_plan,
        route_state=route_state,
        account_plan_sha256=pin,
    )
    assert baseline["plan_sha256"] == source.sha(attempt_plan) != pin
    plan = source.account.decode(attempt_plan, 32768)
    report = source.account.decode(attempt, 32768)
    altered_plan, altered_pin, altered_state = attempt_plan, pin, dict(route_state)
    if damage.startswith("rehash_"):
        field, value = {
            "rehash_account_plan": ("account_plan_sha256", "0" * 64),
            "rehash_origin": ("origin", "https://invalid.example"),
            "rehash_method": ("method", "POST"),
            "rehash_endpoint": ("endpoint", "/api/v5/trade/order"),
            "rehash_retries": ("retries", 1),
            "rehash_schema": (
                "schema_version",
                "ctcc.private_demo_route_attempt_plan.v999",
            ),
            "rehash_extra": ("unreviewed", False),
        }[damage]
        plan[field] = value
        altered_plan = source.canonical(plan)
        # A matching outer hash cannot legitimize a changed original plan.
        report["plan_sha256"] = source.sha(altered_plan)
    elif damage == "missing_account_plan":
        plan.pop("account_plan_sha256")
        altered_plan = source.canonical(plan)
        report["plan_sha256"] = source.sha(altered_plan)
    elif damage == "confused_account_argument":
        altered_pin = source.sha(attempt_plan)
    elif damage == "route_state_pin":
        altered_state["plan_sha256"] = "0" * 64
    elif damage == "route_class":
        altered_state["route"] = object()
    else:
        assert damage == "attempt_digest_is_account_pin"
        report["plan_sha256"] = pin
    with pytest.raises(
        source.NativeNamespaceHistorySourceError,
        match="namespace_history_route_original_attempt_changed",
    ):
        source._replay_original_route_attempt(
            source.canonical(report),
            attempt_plan_raw=altered_plan,
            route_state=altered_state,
            account_plan_sha256=altered_pin,
        )
    assert (root / "account-route-attempt.v4.plan.json").read_bytes() == attempt_plan
    assert (root / "account-route-attempt.v4.json").read_bytes() == attempt
    assert (root / "account-route-attempt.v4.raw").read_bytes() == route_state["raw"]
    assert (
        source._replay_original_route_attempt(
            attempt,
            attempt_plan_raw=attempt_plan,
            route_state=route_state,
            account_plan_sha256=pin,
        )
        == baseline
    )
