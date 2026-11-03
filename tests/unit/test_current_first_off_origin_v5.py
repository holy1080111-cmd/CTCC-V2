"""Real origin helper mechanics only; no account/native/complete acceptance."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.trade_qualification import account_current_first_off_foundation_v5 as public
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_original_position_path_v1 as position
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from app.trade_qualification import native_account_risk_assembly_v1 as native


@pytest.fixture
def isolated_origin_state():
    # Isolate contents, preserving actual production registry identities and
    # their real pinned constants. No guard function or pin table is replaced.
    registries = (
        native._STATE_ORIGINS_V5,
        native._V5_OWNERS,
        native._V5_RELEASE_UNKNOWN,
        native._V5_ACQUISITIONS,
        native._V5_API_ASOF_INFLIGHT,
    )
    native._origin_storage_v5()
    saved = tuple(dict(registry) for registry in registries)
    for registry in registries:
        registry.clear()
    try:
        yield registries
    finally:
        for registry, before in zip(registries, saved, strict=True):
            registry.clear()
            registry.update(before)


def _registered(state, *, version="V5_CURRENT_FIRST_OFF", phase="ACQUIRING"):
    # Value-level source-generation fixture; no actual source owner is minted.
    nonce, ticket = object(), object()
    state["_native_source_generation"] = nonce
    state["_native_source_version"] = version
    record = (version, nonce, state, ticket, phase, None)
    native._STATE_ORIGINS_V5[id(state)] = record
    return record


@pytest.mark.parametrize(
    "case",
    [
        "unregistered",
        "missing_generation",
        "missing_version",
        "changed_generation",
        "downgrade_to_v4",
        "copied_with_markers",
        "copied_without_markers",
    ],
)
def test_missing_changed_or_copied_origin_never_falls_back_to_v4(
    isolated_origin_state, case
):
    state = {}
    _registered(state)
    if case == "unregistered":
        native._STATE_ORIGINS_V5.clear()
    elif case == "missing_generation":
        del state["_native_source_generation"]
    elif case == "missing_version":
        del state["_native_source_version"]
    elif case == "changed_generation":
        state["_native_source_generation"] = object()
    elif case == "downgrade_to_v4":
        state["_native_source_version"] = "V4_ORIGINAL_ORDER"
    elif case == "copied_with_markers":
        state = dict(state)
    elif case == "copied_without_markers":
        state = {}
    with pytest.raises(
        native.NativeAccountRiskAssemblyError,
        match="generation_missing_changed_or_copied",
    ):
        native._check_native_origin_dispatch_v5(state)


def test_unknown_registered_generation_version_denies(isolated_origin_state):
    state = {}
    _registered(state, version="UNKNOWN_SOURCE_VERSION")
    with pytest.raises(
        native.NativeAccountRiskAssemblyError, match="source_version_unknown"
    ):
        native._check_native_origin_dispatch_v5(state)


def test_an_unminted_plain_dictionary_cannot_enter_the_v4_branch(isolated_origin_state):
    with pytest.raises(
        native.NativeAccountRiskAssemblyError,
        match="generation_missing_changed_or_copied",
    ):
        native._check_native_origin_dispatch_v5({})


def test_registered_original_v4_initial_state_keeps_its_separate_branch(
    isolated_origin_state,
):
    state = {}
    native._register_v4_initial_origin_v5(state)
    native._check_native_origin_dispatch_v5(state)
    assert native._origin_record_v5(state)[0] == "V4_ORIGINAL_ORDER"
    assert "_native_source_generation" in state
    # This checks generation mechanics only. _check_v4 still requires all real
    # factory/source/control/lease guards before this state can be used.


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["INITIAL", "ACQUIRING"])
async def test_v5_pre_registration_state_cannot_move_into_bank(
    isolated_origin_state, phase
):
    from app.trade_qualification.account_current_first_off_foundation_v5 import (
        _SOURCE_KEY,
    )

    state = {}
    _registered(state, phase=phase)
    owner = object.__new__(native.OwnedNativeAccountHistoryFoundationV5)
    native._V5_OWNERS[owner] = state
    with pytest.raises(
        native.NativeAccountRiskAssemblyError, match="asof_phase_changed"
    ):
        await native._continue_current_first_api_asof_v3(owner, key=_SOURCE_KEY)
    assert native._V5_OWNERS[owner] is state  # Denial occurred before MOVE.


def test_cleanup_cannot_leave_a_requalifiable_generation(isolated_origin_state):
    state = {}
    _registered(state)
    native._forget_native_origin_v5(state)
    assert state["_native_source_version"] == "V5_CURRENT_FIRST_OFF"
    with pytest.raises(
        native.NativeAccountRiskAssemblyError,
        match="generation_missing_changed_or_copied",
    ):
        native._check_native_origin_dispatch_v5(state)


def test_cancellation_release_retains_original_refs_and_attempts_each_source_once(
    isolated_origin_state,
    monkeypatch,
):
    history_binding, coverage_binding, original_account_ref = (
        object(),
        object(),
        object(),
    )
    history_source = {"original_history_source": object()}
    account = {"source": original_account_ref}
    coverage_source = {"account": account}
    state = {
        "history": history_binding,
        "coverage": coverage_binding,
        "lease": object(),
    }
    origin = _registered(state)
    monkeypatch.setattr(native.history, "_FINANCIAL", {history_binding: history_source})
    monkeypatch.setattr(
        native.coverage, "_BINDINGS", {coverage_binding: coverage_source}
    )
    calls = []

    def history_release(binding, *, key):
        calls.append(("history", binding))
        history_source.clear()

    def coverage_release(binding, *, key):
        calls.append(("coverage", binding))
        account.clear()
        coverage_source.clear()
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        native.history, "_release_financial_assembly_v1", history_release
    )
    monkeypatch.setattr(
        native.coverage, "_release_financial_assembly_v1", coverage_release
    )
    with pytest.raises(asyncio.CancelledError):
        native._release_current_first_partial_v5(state, None, None)
    assert calls == [("coverage", coverage_binding), ("history", history_binding)]
    assert state == {} and id(state) not in native._STATE_ORIGINS_V5
    (retained,) = native._V5_RELEASE_UNKNOWN.values()
    assert retained["original_account"]["source"] is original_account_ref
    assert retained["origin"] is origin
    assert retained["history_source"]["original_history_source"] is not None
    assert native._V5_OWNERS == {}


@pytest.mark.parametrize(
    "registry_names",
    [
        (
            "_STATE_ORIGINS_V5",
            "_STATE_ORIGIN_STORAGE_V5",
            "_STATE_ORIGIN_STORAGE_ID_V5",
        ),
        ("_V5_ACQUISITIONS", "_V5_ACQUISITION_STORAGE", "_V5_ACQUISITION_STORAGE_ID"),
        ("_V5_OWNERS", "_V5_OWNER_STORAGE", "_V5_OWNER_STORAGE_ID"),
        ("_V5_RELEASE_UNKNOWN", "_V5_RELEASE_STORAGE", "_V5_RELEASE_STORAGE_ID"),
        ("_V5_API_ASOF_INFLIGHT", "_V5_API_ASOF_STORAGE", "_V5_API_ASOF_STORAGE_ID"),
    ],
)
def test_replaced_registry_alias_and_matching_new_ID_cannot_escape_frozen_pin(
    isolated_origin_state,
    monkeypatch,
    registry_names,
):
    state = {}
    _registered(state)
    replacement = {}
    monkeypatch.setattr(native, registry_names[0], replacement)
    monkeypatch.setattr(native, registry_names[1], replacement)
    monkeypatch.setattr(native, registry_names[2], id(replacement))
    # Identity self-consistency alone is insufficient. The separate frozen
    # numeric pin must reject even when all three mutable globals agree.
    with pytest.raises(
        native.NativeAccountRiskAssemblyError, match="generation_policy_changed"
    ):
        native._origin_record_v5(state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "empty_modules",
        "copied_modules",
        "empty_paths",
        "checker",
        "collector",
        "bank_method",
        "numeric_unit_parser",
        "dataclass_field",
    ],
)
async def test_bank_entry_callee_closure_cannot_be_vacated_before_owner_lookup(
    isolated_origin_state,
    monkeypatch,
    change,
):
    if change == "empty_modules":
        monkeypatch.setattr(native, "_API_ASOF_MODULES_V3", ())
    elif change == "copied_modules":
        monkeypatch.setattr(
            native,
            "_API_ASOF_MODULES_V3",
            tuple(item for item in native._API_ASOF_MODULES_V3),
        )
    elif change == "empty_paths":
        monkeypatch.setattr(native, "_API_ASOF_SOURCE_PATHS_V3", ())
    elif change == "checker":
        monkeypatch.setattr(native, "_API_ASOF_REQUIRE_V3", lambda: None)
    elif change == "collector":
        monkeypatch.setattr(
            native, "_collect_current_first_api_asof_v3", lambda *a, **kw: None
        )
    elif change == "bank_method":
        monkeypatch.setattr(
            bank.OriginalAccountApiPopulationAccumulatorV2,
            "_capture_after_verified_current_v2",
            lambda *a, **kw: None,
        )
    elif change == "numeric_unit_parser":
        monkeypatch.setattr(position, "_NUMBER", re.compile(".*"))
    else:
        monkeypatch.setattr(
            bank.OriginalAccountWindowOperandsValuesV3, "account_complete", True
        )
    with pytest.raises(
        native.NativeAccountRiskAssemblyError, match="asof_(callee|boundary)_changed"
    ):
        await native._continue_current_first_api_asof_v3(
            object(), key=public._SOURCE_KEY
        )
    assert not native._V5_API_ASOF_INFLIGHT


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_move", "after_move"])
@pytest.mark.parametrize(
    "alias",
    [
        "_api_asof_bank",
        "_api_asof_reader",
        "_api_asof_positions",
        "_v5_public",
        "_v4_financial",
        "_v4_stream",
        "_v4_wire",
        "_v4_position_sessions",
        "_hwm_measurement",
        "mapping",
        "history",
        "current",
        "coverage",
        "custody",
        "capture",
        "financial_v2",
        "history_bundle",
        "asyncio",
        "datetime",
        "ControlledDemoAccountSession",
        "_json_lexical_guard",
    ],
)
async def test_active_owner_alias_rebinding_refuses_without_losing_held_custody(
    isolated_origin_state,
    monkeypatch,
    phase,
    alias,
):
    # Explicit inert custody fixture, not an acquired source or native account.
    # The before-MOVE branch calls the real public boundary; the after-MOVE
    # branch keeps its same captured production checker across an async yield.
    owner = object.__new__(native.OwnedNativeAccountHistoryFoundationV5)
    state = {"original_source_reference": object()}
    checker = native._API_ASOF_REQUIRE_V3
    unknown_before = dict(native._V5_RELEASE_UNKNOWN)
    if phase == "before_move":
        native._V5_OWNERS[owner] = state
    else:
        native._V5_API_ASOF_INFLIGHT[id(state)] = state
    monkeypatch.setattr(native, alias, SimpleNamespace())
    with pytest.raises(
        native.NativeAccountRiskAssemblyError,
        match="native_current_first_v5_asof_owner_alias_changed",
    ):
        if phase == "before_move":
            await native._continue_current_first_api_asof_v3(
                owner, key=public._SOURCE_KEY
            )
        else:
            await asyncio.sleep(0)
            checker()
    if phase == "before_move":
        assert native._V5_OWNERS[owner] is state
        assert not native._V5_API_ASOF_INFLIGHT
    else:
        assert native._V5_API_ASOF_INFLIGHT[id(state)] is state
        assert owner not in native._V5_OWNERS
    assert native._V5_RELEASE_UNKNOWN == unknown_before
    assert state["original_source_reference"] is not None


@pytest.mark.asyncio
async def test_controlled_current_acquisition_actually_selects_bank_continuation_once(
    monkeypatch,
):
    # Deliberate callsite mechanism stub only; it mints no native source owner.
    original, returned, calls = object(), object(), []

    class Kernel:
        async def acquire_native_account_history_foundation_v5(self, **arguments):
            calls.append(("acquire", arguments))
            return original

        async def _continue_current_first_api_asof_v3(self, owner, *, key):
            assert owner is original and key is public._SOURCE_KEY
            calls.append(("bank", owner))
            return returned

    kernel = Kernel()
    monkeypatch.setattr(public, "_assembly", lambda: kernel)
    result = await public.acquire_current_first_off_foundation_v5(
        read_session=None,
        private_client=None,
        scope_config=None,
        current_account_plan=None,
        session_factory=None,
        custody_session_factory=None,
        frame_session_factory=None,
        frame_collector_role_oid=None,
        history_raw_root=None,
        history_companion_root=None,
        account_proof_root=None,
        account_native_root=None,
    )
    assert result is returned
    assert [kind for kind, _value in calls] == ["acquire", "bank"]
    assert not native._V5_API_ASOF_INFLIGHT


def test_existing_native_plan_default_is_unchanged_and_asof_query_begin_only_expands_coverage():
    # Plan arithmetic only; SimpleNamespace is not an accepted native lease.
    at = datetime(2026, 11, 2, 0, 0, 1, tzinfo=UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = at - epoch
    stamp = {
        "utc_ns": (
            (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        )
        * 1000
    }
    scope = history.NativeHistoryScopeV1("10001", "10001", "1" * 64, ("BTC-USDT-SWAP",))
    lease = SimpleNamespace(binding_id="0" * 32)
    old = history._native_plan(scope, lease, stamp)
    explicit_default = history._native_plan(
        scope, lease, stamp, _asof_history_start=None
    )
    assert old.history_start == explicit_default.history_start == at - timedelta(days=7)
    assert old.history_end == explicit_default.history_end == at
    begin = at - timedelta(days=7, seconds=90)
    expanded = history._native_plan(scope, lease, stamp, _asof_history_start=begin)
    assert expanded.history_start == begin
    assert expanded.history_end == old.history_end
    assert expanded.created_at == old.created_at


@pytest.mark.parametrize(
    "offset",
    [
        timedelta(days=9),
        timedelta(days=6),
        timedelta(days=7, seconds=60, microseconds=1),
    ],
)
def test_asof_query_override_cannot_shorten_required_history_be_unbounded_or_rewrite_clock(
    offset,
):
    at = datetime(2026, 11, 2, 0, 0, 1, tzinfo=UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = at - epoch
    stamp = {"utc_ns": (delta.days * 86400 + delta.seconds) * 1_000_000_000}
    scope = history.NativeHistoryScopeV1("10001", "10001", "1" * 64, ("BTC-USDT-SWAP",))
    with pytest.raises(
        history.NativeHistorySourceError, match="asof_query_bound_invalid"
    ):
        history._native_plan(
            scope,
            SimpleNamespace(binding_id="0" * 32),
            stamp,
            _asof_history_start=at - offset,
        )
