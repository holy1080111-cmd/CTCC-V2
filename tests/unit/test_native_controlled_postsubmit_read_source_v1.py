"""Finite DATA/clock/scanner boundaries, never a fabricated native owner."""

import pytest

from app.trade_qualification import native_controlled_account_source_v6 as source
from app.trade_qualification import native_postsubmit_reconciliation_v1 as postsubmit
from app.trade_qualification.reservations import LedgerScope


def _stamp(seconds):
    return {
        "utc_ns": 1_000_000_000 + seconds * 1_000_000_000,
        "monotonic_ns": 2_000_000_000 + seconds * 1_000_000_000,
    }


def _window(now, *, last=0, enforce=True, extra=0):
    start = _stamp(0)
    return source._reconciliation_read_window_v6(
        start,
        _stamp(last),
        _stamp(now),
        start["utc_ns"] + 60_000_000_000 + extra,
        start["monotonic_ns"] + 60_000_000_000,
        enforce=enforce,
    )


def test_independent_read_window_has_one_fixed_deadline_and_late_data_is_distinct():
    assert _window(59, last=58) == _stamp(59)
    # Late DATA scanning is explicitly distinct from a positive read step.
    assert _window(61, last=60, enforce=False) == _stamp(61)
    with pytest.raises(source.ControlledAccountSourceError, match="read_deadline"):
        _window(60, last=59)
    with pytest.raises(
        source.ControlledAccountSourceError, match="read_window_changed"
    ):
        _window(61, last=60, extra=1_000_000_000)


@pytest.mark.parametrize(
    "fault", ("utc_bool", "mono_bool", "reverse", "deadline_bool", "nonbool_enforce")
)
def test_new_read_window_rejects_typed_clock_faults(fault):
    first, last, now = _stamp(0), _stamp(1), _stamp(2)
    utc = first["utc_ns"] + 60_000_000_000
    mono = first["monotonic_ns"] + 60_000_000_000
    enforce = True
    if fault == "utc_bool":
        now["utc_ns"] = True
    elif fault == "mono_bool":
        now["monotonic_ns"] = True
    elif fault == "reverse":
        now = _stamp(0)
    elif fault == "deadline_bool":
        utc = True
    elif fault == "nonbool_enforce":
        enforce = "yes"
    with pytest.raises(ValueError):
        source._reconciliation_read_window_v6(
            first, last, now, utc, mono, enforce=enforce
        )


def test_normal_json_fragment_is_not_declared_a_complete_document():
    token = "NATIVE_SYNTHETIC_SECRET"
    source._scan_reconciliation_received_buffer_v6(b'{"message":"', (token,))
    source._scan_reconciliation_received_buffer_v6(b'normal"}', (token,))
    source._scan_reconciliation_complete_document_v6(b'{"message":"normal"}', (token,))
    with pytest.raises(
        source.ControlledAccountSourceError, match="incomplete_or_secret_json"
    ):
        source._scan_reconciliation_complete_document_v6(b'{"message":"', (token,))


def test_terminal_raw_join_and_decoded_json_both_reject_secret_echo():
    token = "NATIVE_SYNTHETIC_SECRET"
    first, second = b"NATIVE_SYNTHETIC_", b"SECRET"
    source._scan_reconciliation_received_buffer_v6(first, (token,))
    source._scan_reconciliation_received_buffer_v6(second, (token,))
    with pytest.raises(ValueError, match="credential_echo_rejected"):
        source._scan_reconciliation_received_buffer_v6(first + second, (token,))
    escaped = (
        b'{"message":"'
        + b"".join((f"\\u{ord(value):04x}").encode("ascii") for value in token)
        + b'"}'
    )
    with pytest.raises(
        source.ControlledAccountSourceError, match="incomplete_or_secret_json"
    ):
        source._scan_reconciliation_complete_document_v6(escaped, (token,))


def test_constructed_context_and_foreign_generation_are_inert_data():
    before = len(source._RECONCILIATION_READ_V6)
    fake = postsubmit.NativeReconciliationSourceContextV1(
        client=object(),
        scope=LedgerScope(
            environment="demo", account_id="550099", settlement_currency="USDT"
        ),
        session_factory=object(),
        origin="https://openapi.okx.com",
        credential_session_sha256="a" * 64,
        source_generation=object(),
        control_owner=None,
    )
    with pytest.raises(source.ControlledAccountSourceError, match="genuine_recovery"):
        source._replay_native_recovery_read_source_v1(
            fake.source_generation, key=postsubmit._SOURCE_KEY_V1
        )
    with pytest.raises(
        source.ControlledAccountSourceError, match="genuine_fresh_recovery_selection"
    ):
        source._acquire_native_recovery_read_source_v1(
            fake, key=postsubmit._SOURCE_KEY_V1
        )
    with pytest.raises(
        source.ControlledAccountSourceError, match="genuine_submitted_transport"
    ):
        source._replay_issued_demo_reconciliation_context_v2(
            fake, key=postsubmit._SOURCE_KEY_V1
        )
    assert len(source._RECONCILIATION_READ_V6) == before


def test_private_data_constructor_still_needs_genuine_moved_source_authority():
    with pytest.raises(
        source.ControlledAccountSourceError, match="genuine_moved_recovery_selection"
    ):
        source._new_reconciliation_source_v6(
            authority=object(),
            settings=None,
            credentials=(),
            scope=None,
            origin="https://openapi.okx.com",
            writer_factory=None,
            raw_factory=None,
            control_owner=None,
            source_state=None,
            transport=None,
            selector_record=None,
            private_tokens=(),
        )


# These negative-only regressions reuse the EXACT pristine inner production
# function code with inert closure captures. They neither patch production
# globals/registries nor manufacture a genuine native source/Root response.
def _inert_inhibitor(*, current, target_missing=False):
    from types import CodeType, FunctionType, SimpleNamespace

    source._NATIVE_GUARD()
    codes = tuple(
        value
        for value in source._make_reconciliation_inhibitor_v6.__code__.co_consts
        if type(value) is CodeType and value.co_name == "inhibit"
    )
    assert len(codes) == 1
    key, generation, task = object(), object(), object()
    factory, owner = object(), object()
    scope = LedgerScope(
        environment="demo", account_id="550099", settlement_currency="USDT"
    )
    state = {}
    transport_key = object()
    record = {
        "context": (1, 2, 3, lambda: task),
        "inhibit_started": False,
        "control_owner": owner if current else None,
        "transport": transport_key if current else None,
        "writer_factory": factory,
        "source_state": state,
        "scope": scope,
    }
    transport = {
        "owner": owner,
        "factory": factory,
        "state": state,
        "scope": SimpleNamespace(environment="demo", account_id="550099"),
    }
    records = {generation: record}
    events = []
    operation = object()

    def target_reader(_generation, *, key):
        events.append("target")
        if target_missing:
            raise source.ControlledAccountSourceError(
                "synthetic_new_target_unavailable"
            )
        return SimpleNamespace(
            scope=scope, session_factory=factory, reservation_id="b" * 64
        )

    def saved_check(_):
        events.append("guard")

    def revoke(_owner, *, stop):
        assert _owner is owner and stop is True
        events.append("local_stop")

    def original_inhibit(value):
        assert value is transport
        events.append("original_persistence")
        return operation

    def persist(value):
        assert value is record
        events.append("recovery_persistence")
        return operation

    values = {
        "source_key": key,
        "records": records,
        "context": lambda: (1, 2, 3, task),
        "revoke": revoke,
        "saved_check": saved_check,
        "saved_check_code": saved_check.__code__,
        "saved_guard": {},
        "target_reader": target_reader,
        "target_key": object(),
        "original_inhibit": original_inhibit,
        "persist": persist,
    }

    def cell(value):
        return (lambda: value).__closure__[0]

    code = codes[0]
    assert set(code.co_freevars) <= set(values)
    function = FunctionType(
        code,
        {
            "__builtins__": __builtins__,
            "_deny": source._deny,
            "_TRANSPORT_V6": {transport_key: transport},
        },
        closure=tuple(cell(values[name]) for name in code.co_freevars),
    )
    return function, key, generation, record, events, operation


def test_actual_negative_current_stop_code_cannot_be_blocked_by_missing_new_target():
    function, key, generation, record, events, operation = _inert_inhibitor(
        current=True, target_missing=True
    )
    assert function(generation, key=key) is operation
    assert events == ["local_stop", "original_persistence"]
    assert record["inhibit_operation"] is operation
    with pytest.raises(source.ControlledAccountSourceError, match="stop_once_required"):
        function(generation, key=key)
    assert events == ["local_stop", "original_persistence"]


def test_actual_negative_recovery_stop_code_still_requires_new_durable_target():
    function, key, generation, record, events, _ = _inert_inhibitor(
        current=False, target_missing=True
    )
    with pytest.raises(
        source.ControlledAccountSourceError, match="synthetic_new_target_unavailable"
    ):
        function(generation, key=key)
    assert events == ["guard", "target"] and record["inhibit_started"] is True
    assert "inhibit_operation" not in record


def test_discard_entry_is_the_actual_closed_safety_function_not_the_removed_body():
    assert (
        source.discard_issued_account_owner_v1.__qualname__
        == "_install_safety_entries_v6.<locals>.discard"
    )


# These tests mutate only DATA/code bindings under a reversible pytest context.
# They exercise the actual installed source guard; no owner, SQL result, native
# profile, source packet or passing authority is manufactured.
@pytest.mark.parametrize("component", ("closure_schema", "event_helper"))
def test_source_kernel_rejects_new55_closure_namespace_mutations(
    monkeypatch, component
):
    from app.database.repositories import native_zero_fok_event_schema_v55 as events
    from app.domain import native_zero_fok_closure_schema_v55 as closure
    from app.trade_qualification.native_continuation_guard_v1 import (
        NativeSourceClosureError,
    )

    source._NATIVE_GUARD()
    with monkeypatch.context() as patch:
        if component == "closure_schema":
            patch.setattr(closure, "CURRENT_BODY_SHA256", "0" * 64)
        else:
            patch.setattr(
                events,
                "require_actual_zero_fok_event_schema_v55",
                lambda _session: None,
            )
        with pytest.raises(
            NativeSourceClosureError, match="native_continuation_source_changed"
        ):
            source._NATIVE_GUARD()
    # The exact original identities are restored before any other test resumes.
    source._NATIVE_GUARD()


def test_flat_cleanup_constructors_and_unregistered_handles_are_not_authority():
    with pytest.raises(source.ControlledAccountSourceError, match="not_constructible"):
        source._PreparedFlatCleanupV6()
    with pytest.raises(source.ControlledAccountSourceError, match="not_constructible"):
        source._CompletedFlatCleanupV6()
    before = tuple(
        len(value)
        for value in (
            source._FLAT_CLEANUP_RECORDS_V6,
            source._FLAT_CLEANUP_PREPARED_V6,
            source._FLAT_CLEANUP_COMPLETED_V6,
            source._STATES_V6,
            source._UNKNOWN_V6,
        )
    )
    fake_prepared = object.__new__(source._PreparedFlatCleanupV6)
    fake_completed = object.__new__(source._CompletedFlatCleanupV6)
    with pytest.raises(source.ControlledAccountSourceError, match="prepared_once"):
        source.finish_native_flat_source_cleanup_v1(
            fake_prepared, key=postsubmit._SOURCE_SETTLEMENT_KEY_V1
        )
    with pytest.raises(source.ControlledAccountSourceError, match="genuine_completion"):
        source._replay_native_flat_source_cleanup_completion_v1(
            fake_completed, key=postsubmit._SOURCE_SETTLEMENT_KEY_V1
        )
    with pytest.raises(source.ControlledAccountSourceError, match="retirement_once"):
        source.retire_native_flat_source_cleanup_v1(
            fake_completed, key=postsubmit._SOURCE_SETTLEMENT_KEY_V1
        )
    assert before == tuple(
        len(value)
        for value in (
            source._FLAT_CLEANUP_RECORDS_V6,
            source._FLAT_CLEANUP_PREPARED_V6,
            source._FLAT_CLEANUP_COMPLETED_V6,
            source._STATES_V6,
            source._UNKNOWN_V6,
        )
    )


def test_cleanup_data_view_cannot_replace_the_owned_completion():
    value = source.NativeFlatSourceCleanupCompletionViewV1(
        object(), object(), object(), object(), object(), object(), (), b"{}"
    )
    assert value.execution_authority is value.order_retry_authority is False
    with pytest.raises(source.ControlledAccountSourceError, match="genuine_completion"):
        source._replay_native_flat_source_cleanup_completion_v1(
            value, key=postsubmit._SOURCE_SETTLEMENT_KEY_V1
        )


def test_combined_retained_budget_still_counts_actual_extra_bytes_and_identity_once():
    # Pure DATA budget mechanics, never a genuine source or family owner.
    payload = bytes(bytearray(source._MAX_RETAINED_V6))
    source._retained_budget_v6({"claims_json": payload}, extra=(payload,))
    with pytest.raises(
        source.ControlledAccountSourceError, match="retained_byte_capacity"
    ):
        source._retained_budget_v6({"claims_json": payload}, extra=(b"x",))


def test_late_passive_scan_does_not_renew_positive_read_admission():
    # Inspecting retained DATA does not move the original source deadlines.
    start = _stamp(0)
    late = _window(61, last=60, enforce=False)
    assert late == _stamp(61)
    with pytest.raises(ValueError, match="clock_lease_expired|read_deadline"):
        source._reconciliation_read_window_v6(
            start,
            late,
            _stamp(62),
            start["utc_ns"] + 60_000_000_000,
            start["monotonic_ns"] + 60_000_000_000,
            enforce=True,
        )


@pytest.mark.parametrize("axis", ("utc_ns", "monotonic_ns"))
def test_each_original_deadline_is_strict_for_positive_reads(axis):
    start, last, now = _stamp(0), _stamp(59), _stamp(60)
    # The other clock is still just inside the same original 5ms drift bound.
    other = "monotonic_ns" if axis == "utc_ns" else "utc_ns"
    now[other] -= 5_000_000
    assert (
        source._reconciliation_read_window_v6(
            start,
            last,
            now,
            start["utc_ns"] + 60_000_000_000,
            start["monotonic_ns"] + 60_000_000_000,
            enforce=False,
        )
        is now
    )
    with pytest.raises(source.ControlledAccountSourceError, match="read_deadline"):
        source._reconciliation_read_window_v6(
            start,
            last,
            now,
            start["utc_ns"] + 60_000_000_000,
            start["monotonic_ns"] + 60_000_000_000,
            enforce=True,
        )


@pytest.mark.parametrize(
    "fault",
    (
        "utc_reverse",
        "mono_reverse",
        "utc_jump",
        "mono_jump",
        "accumulated_jump",
        "utc_bool",
        "mono_bool",
        "timestamp_bound",
        "deadline_changed",
        "deadline_bool",
        "nonbool_enforce",
    ),
)
def test_passive_late_data_still_rejects_invalid_causal_clocks(fault):
    start, last, now = _stamp(0), _stamp(60), _stamp(61)
    utc = start["utc_ns"] + 60_000_000_000
    mono = start["monotonic_ns"] + 60_000_000_000
    enforce = False
    if fault == "utc_reverse":
        now["utc_ns"] = last["utc_ns"] - 1
    elif fault == "mono_reverse":
        now["monotonic_ns"] = last["monotonic_ns"] - 1
    elif fault == "utc_jump":
        now["utc_ns"] += 5_000_001
    elif fault == "mono_jump":
        now["monotonic_ns"] += 5_000_001
    elif fault == "accumulated_jump":
        # Each adjacent drift is below 5ms; the original-start relation is not.
        last["utc_ns"] += 4_000_000
        now["utc_ns"] += 8_000_000
    elif fault == "utc_bool":
        now["utc_ns"] = True
    elif fault == "mono_bool":
        now["monotonic_ns"] = True
    elif fault == "timestamp_bound":
        now["utc_ns"] = now["monotonic_ns"] = 32_503_680_000_000_000_001
    elif fault == "deadline_changed":
        utc += 1
    elif fault == "deadline_bool":
        mono = True
    elif fault == "nonbool_enforce":
        enforce = None
    with pytest.raises(ValueError):
        source._reconciliation_read_window_v6(
            start,
            last,
            now,
            utc,
            mono,
            enforce=enforce,
        )


def test_close_read_requires_genuine_moved_entry_and_preserves_first_generation():
    from app.trade_qualification import native_controlled_close_v1 as close

    before = tuple(
        len(mapping)
        for mapping in (
            source._RECONCILIATION_READ_V6,
            source._RECONCILIATION_TRANSPORT_V6,
            source._CLOSE_READ_USES_V6,
            source._TRANSPORT_V6,
        )
    )
    copied_view = close.NativeCloseEntryForSourceV1(object(), object(), object())
    assert copied_view.execution_authority is copied_view.order_retry_authority is False
    with pytest.raises(
        source.ControlledAccountSourceError, match="genuine_close_entry"
    ):
        source.acquire_native_close_read_source_v1(
            copied_view, key=close._SOURCE_KEY_V1
        )
    forged = object.__new__(close.OwnedNativeCloseEntryV1)
    with pytest.raises(ValueError):
        source.acquire_native_close_read_source_v1(forged, key=close._SOURCE_KEY_V1)
    assert before == tuple(
        len(mapping)
        for mapping in (
            source._RECONCILIATION_READ_V6,
            source._RECONCILIATION_TRANSPORT_V6,
            source._CLOSE_READ_USES_V6,
            source._TRANSPORT_V6,
        )
    )


def test_close_read_foreign_generation_and_constructor_data_cannot_inhibit_or_replay():
    with pytest.raises(
        source.ControlledAccountSourceError, match="genuine_close_read_generation"
    ):
        source._replay_native_close_read_source_v1(
            object(), key=postsubmit._SOURCE_KEY_V1
        )
    with pytest.raises(
        source.ControlledAccountSourceError, match="close_stop_original_generation"
    ):
        source._inhibit_native_close_read_source_v1(
            object(), key=postsubmit._SOURCE_KEY_V1
        )
    with pytest.raises(
        source.ControlledAccountSourceError, match="close_stop_fixed_purpose"
    ):
        source._inhibit_native_close_read_source_v1(object(), key=object())


def test_close_read_parent_relation_does_not_accept_a_copied_closed_flag():
    from app.trade_qualification import native_controlled_close_v1 as close

    fake = {
        "close_entry_cap": object.__new__(close.OwnedNativeCloseEntryV1),
        "generation": object(),
        "closed": True,
        "read_lease": None,
    }
    with pytest.raises(
        source.ControlledAccountSourceError, match="genuine_close_read_generation"
    ):
        source._close_prior_read_v6(fake)


# Negative WRITE source component DATA only. No synthetic view is passed into
# an authenticated Root/Source registry, database or physical SDK call.
def _negative_write_budget():
    start = _stamp(0)
    return {
        "started_utc_ns": start["utc_ns"],
        "started_monotonic_ns": start["monotonic_ns"],
        "expires_utc_ns": start["utc_ns"] + 10_000_000_000,
        "expires_monotonic_ns": start["monotonic_ns"] + 10_000_000_000,
        "maximum_seconds": 10,
        "entry_lease_renewed": False,
    }


def _negative_write_window(budget, now, last=0, *, read_utc=None, read_mono=None):
    first = _stamp(0)
    return source._close_write_window_v6(
        budget,
        _stamp(last),
        _stamp(now),
        first["utc_ns"] + 60_000_000_000 if read_utc is None else read_utc,
        first["monotonic_ns"] + 60_000_000_000 if read_mono is None else read_mono,
    )


def test_negative_write_uses_the_committed_ten_seconds_without_renewing_read_or_entry():
    budget = _negative_write_budget()
    original = dict(budget)
    assert _negative_write_window(budget, 9, last=8) == _stamp(9)
    assert budget == original
    with pytest.raises(ValueError):
        _negative_write_window(budget, 10, last=9)
    with pytest.raises(ValueError):
        _negative_write_window(budget, 11, last=9)
    # A shorter actual read deadline remains a cap on the negative budget.
    short = dict(budget)
    short["expires_utc_ns"] -= 1_000_000_000
    short["expires_monotonic_ns"] -= 1_000_000_000
    assert _negative_write_window(
        short,
        8,
        last=7,
        read_utc=short["expires_utc_ns"],
        read_mono=short["expires_monotonic_ns"],
    ) == _stamp(8)
    with pytest.raises(ValueError):
        _negative_write_window(
            short,
            9,
            last=8,
            read_utc=short["expires_utc_ns"],
            read_mono=short["expires_monotonic_ns"],
        )


@pytest.mark.parametrize(
    "fault",
    (
        "extend_utc",
        "extend_mono",
        "read_utc",
        "read_mono",
        "max_bool",
        "max_eleven",
        "renewed",
        "renewed_zero",
        "missing",
        "extra",
        "started_bool",
        "deadline_bool",
        "reverse",
        "clock_jump",
        "negative_clock",
    ),
)
def test_negative_write_rejects_changed_or_noncausal_original_budget(fault):
    budget, first, now, last = _negative_write_budget(), _stamp(0), _stamp(2), _stamp(1)
    read_utc, read_mono = (
        first["utc_ns"] + 60_000_000_000,
        first["monotonic_ns"] + 60_000_000_000,
    )
    if fault == "extend_utc":
        budget["expires_utc_ns"] += 1
    elif fault == "extend_mono":
        budget["expires_monotonic_ns"] += 1
    elif fault == "read_utc":
        read_utc = budget["expires_utc_ns"] - 1
    elif fault == "read_mono":
        read_mono = budget["expires_monotonic_ns"] - 1
    elif fault == "max_bool":
        budget["maximum_seconds"] = True
    elif fault == "max_eleven":
        budget["maximum_seconds"] = 11
    elif fault == "renewed":
        budget["entry_lease_renewed"] = True
    elif fault == "renewed_zero":
        budget["entry_lease_renewed"] = 0
    elif fault == "missing":
        budget.pop("started_monotonic_ns")
    elif fault == "extra":
        budget["passed"] = True
    elif fault == "started_bool":
        budget["started_utc_ns"] = True
    elif fault == "deadline_bool":
        read_mono = True
    elif fault == "reverse":
        last = _stamp(3)
    elif fault == "clock_jump":
        now["utc_ns"] += 5_000_001
    else:
        now["monotonic_ns"] = -1
    with pytest.raises(ValueError):
        source._close_write_window_v6(budget, last, now, read_utc, read_mono)


def test_negative_write_constructor_foreign_intent_and_copied_token_are_inert():
    from app.exchange.okx import native_demo_write_v2 as write

    with pytest.raises(source.ControlledAccountSourceError):
        source._DemoCloseTransportBindingV1()
    with pytest.raises(ValueError):
        source.bind_native_close_transport_v1({}, key=write._CLOSE_INTENT_KEY_V1)
    unregistered = object.__new__(source._DemoCloseTransportBindingV1)
    with pytest.raises(source.ControlledAccountSourceError):
        source._replay_native_close_transport_v1(
            unregistered, key=write._CLOSE_INTENT_KEY_V1
        )
    with pytest.raises(source.ControlledAccountSourceError):
        source._inhibit_native_close_transport_v1(
            unregistered, key=write._CLOSE_INTENT_KEY_V1
        )


def _inert_negative_write_inhibitor():
    from types import CodeType, FunctionType

    source._NATIVE_GUARD()
    codes = tuple(
        value
        for value in source._make_close_transport_inhibitor_v6.__code__.co_consts
        if type(value) is CodeType and value.co_name == "inhibit"
    )
    assert len(codes) == 1
    key, task, owner, factory, operation = (
        object(),
        object(),
        object(),
        object(),
        object(),
    )
    token = object.__new__(source._DemoCloseTransportBindingV1)
    scope = LedgerScope(
        environment="demo", account_id="550099", settlement_currency="USDT"
    )
    source_state, intent_state = {"phase": "UNKNOWN"}, {}
    original = {
        "state": source_state,
        "owner": owner,
        "factory": factory,
        "scope": scope,
    }
    read_record = {
        "source_state": source_state,
        "control_owner": owner,
        "writer_factory": factory,
        "scope": scope,
    }
    record = {
        "key": key,
        "inhibited": False,
        "intent_state": intent_state,
        "context": (1, 2, 3, lambda: task),
        "original_transport": original,
        "read_record": read_record,
        "phase": "BOUND",
        "inhibit_operation": None,
    }
    events = []

    def original_inhibit(value):
        assert value is original
        events.extend(("local_revoke", "bounded_persistence_created"))
        return operation

    values = {
        "records": {token: record},
        "uses": {id(intent_state): token},
        "record_type": source._DemoCloseTransportBindingV1,
        "context": lambda: (1, 2, 3, task),
        "original_inhibit": original_inhibit,
    }

    def cell(value):
        return (lambda: value).__closure__[0]

    code = codes[0]
    assert set(code.co_freevars) <= set(values)
    function = FunctionType(
        code,
        {"__builtins__": __builtins__, "_deny": source._deny},
        closure=tuple(cell(values[name]) for name in code.co_freevars),
    )
    return function, token, key, record, events, operation


def test_actual_negative_write_inhibitor_code_stops_after_unknown_without_positive_clock():
    function, token, key, record, events, operation = _inert_negative_write_inhibitor()
    assert function(token, key=key) is operation
    assert events == ["local_revoke", "bounded_persistence_created"]
    assert record["phase"] == "UNKNOWN" and record["inhibited"] is True
    assert record["inhibit_operation"] is operation
    with pytest.raises(source.ControlledAccountSourceError):
        function(token, key=key)
    assert len(events) == 2


@pytest.mark.parametrize("fault", ("key", "owner", "factory", "scope"))
def test_actual_negative_write_inhibitor_code_rejects_foreign_scope_before_persistence(
    fault,
):
    function, token, key, record, events, _ = _inert_negative_write_inhibitor()
    if fault == "key":
        key = object()
    elif fault == "owner":
        record["read_record"]["control_owner"] = object()
    elif fault == "factory":
        record["read_record"]["writer_factory"] = object()
    else:
        record["read_record"]["scope"] = LedgerScope(
            environment="demo", account_id="550098", settlement_currency="USDT"
        )
    with pytest.raises(source.ControlledAccountSourceError):
        function(token, key=key)
    assert events == [] and record["inhibited"] is False


def test_negative_write_retained_data_uses_identity_dedup_and_existing_combined_limit():
    one = b"retained-independent-row"
    values = source._close_write_buffer_bytes_v6(((one, one), {"receipt": "wire"}))
    assert sum(len(value) for value in values) == len(one) + len(b"wire")
    source._retained_budget_v6({}, extra=values)
    with pytest.raises(
        source.ControlledAccountSourceError, match="retained_byte_capacity"
    ):
        source._retained_budget_v6({}, extra=(b"x" * (source._MAX_RETAINED_V6 + 1),))


@pytest.mark.parametrize("component", ("intent_schema", "index_catalog"))
def test_source_guard_pins_both_new55_once_index_leaf_definitions(
    monkeypatch, component
):
    from app.domain import native_close_intent_catalog_v55 as index
    from app.domain import native_close_intent_schema_v55 as intent
    from app.trade_qualification.native_continuation_guard_v1 import (
        NativeSourceClosureError,
    )

    source._NATIVE_GUARD()
    with monkeypatch.context() as patch:
        if component == "intent_schema":
            patch.setattr(intent, "INDEX_SPEC_SHA256", "0" * 64)
        else:
            patch.setattr(index, "observe_catalog", lambda *_args, **_kwargs: {})
        with pytest.raises(NativeSourceClosureError):
            source._NATIVE_GUARD()
    source._NATIVE_GUARD()


# Third post-close read component DATA/negative checks only. No copied response,
# constructor or inert closure capsule is admitted to an owned production map.
def test_post_close_read_needs_the_actual_one_use_received_close_cap():
    from app.trade_qualification import native_controlled_close_v1 as close

    with pytest.raises(ValueError):
        close.OwnedNativeClosePostReadV1()
    before = (
        len(source._RECONCILIATION_READ_V6),
        len(source._RECONCILIATION_TRANSPORT_V6),
        len(source._CLOSE_READ_USES_V6),
        len(source._CLOSE_TRANSPORT_V6),
    )
    with pytest.raises(source.ControlledAccountSourceError):
        source.acquire_native_close_post_read_source_v1(
            object(), key=close._SOURCE_KEY_V1
        )
    copied = object.__new__(close.OwnedNativeClosePostReadV1)
    with pytest.raises(ValueError):
        source.acquire_native_close_post_read_source_v1(
            copied, key=close._SOURCE_KEY_V1
        )
    assert before == (
        len(source._RECONCILIATION_READ_V6),
        len(source._RECONCILIATION_TRANSPORT_V6),
        len(source._CLOSE_READ_USES_V6),
        len(source._CLOSE_TRANSPORT_V6),
    )


def test_third_read_foreign_generation_or_copied_closed_lease_is_not_a_source():
    fake = {"generation": object(), "close_post_read_cap": object(), "closed": True}
    with pytest.raises(source.ControlledAccountSourceError):
        source._close_post_prior_reads_v6(fake)
    with pytest.raises(source.ControlledAccountSourceError):
        source._replay_native_close_post_read_source_v1(
            object(), key=postsubmit._SOURCE_KEY_V1
        )
    with pytest.raises(source.ControlledAccountSourceError):
        source._inhibit_native_close_post_read_source_v1(
            object(), key=postsubmit._SOURCE_KEY_V1
        )


def test_third_read_constructor_data_cannot_select_a_client_factory_or_route():
    scope = LedgerScope(
        environment="demo", account_id="550099", settlement_currency="USDT"
    )
    with pytest.raises(source.ControlledAccountSourceError):
        source._new_reconciliation_source_v6(
            authority=object(),
            settings=object(),
            credentials=("key", "secret", "pass"),
            scope=scope,
            origin="https://openapi.okx.com",
            writer_factory=object(),
            raw_factory=object(),
            control_owner=None,
            source_state={},
            transport=object(),
            selector_record=object(),
            private_tokens=("not-an-actual-signature",),
            close_post_read_cap=object(),
        )


def test_a_new_read_clock_window_does_not_renew_the_spent_negative_write_deadline():
    first, now = _stamp(120), _stamp(179)
    assert (
        source._reconciliation_read_window_v6(
            first,
            _stamp(178),
            now,
            first["utc_ns"] + 60_000_000_000,
            first["monotonic_ns"] + 60_000_000_000,
            enforce=True,
        )
        == now
    )
    with pytest.raises(ValueError):
        source._close_write_window_v6(
            _negative_write_budget(),
            _stamp(9),
            now,
            _stamp(0)["utc_ns"] + 60_000_000_000,
            _stamp(0)["monotonic_ns"] + 60_000_000_000,
        )
    with pytest.raises(ValueError):
        source._reconciliation_read_window_v6(
            first,
            now,
            _stamp(180),
            first["utc_ns"] + 60_000_000_000,
            first["monotonic_ns"] + 60_000_000_000,
            enforce=True,
        )


def _inert_third_read_inhibitor():
    from types import CodeType, FunctionType

    source._NATIVE_GUARD()
    codes = tuple(
        value
        for value in source._make_close_post_read_inhibitor_v6.__code__.co_consts
        if type(value) is CodeType and value.co_name == "inhibit"
    )
    assert len(codes) == 1
    generation, cap, key, task, operation = (
        object(),
        object(),
        object(),
        object(),
        object(),
    )
    record = {
        "close_post_read_cap": cap,
        "context": (1, 2, 3, lambda: task),
        "inhibit_started": False,
    }
    events = []

    def original_inhibit(actual, *, key):
        assert actual is generation
        if record["inhibit_started"]:
            raise source.ControlledAccountSourceError("synthetic_stop_once")
        record["inhibit_started"] = True
        events.extend(("original_local_revoke", "original_bounded_persistence_created"))
        return operation

    values = {
        "records": {generation: record},
        "uses": {cap: generation},
        "purpose": key,
        "context": lambda: (1, 2, 3, task),
        "original_inhibit": original_inhibit,
    }

    def cell(value):
        return (lambda: value).__closure__[0]

    code = codes[0]
    assert set(code.co_freevars) <= set(values)
    function = FunctionType(
        code,
        {"__builtins__": __builtins__, "_deny": source._deny},
        closure=tuple(cell(values[name]) for name in code.co_freevars),
    )
    return function, generation, key, record, events, operation


def test_actual_third_read_inhibitor_delegates_original_stop_without_clock_renewal():
    function, generation, key, record, events, operation = _inert_third_read_inhibitor()
    assert function(generation, key=key) is operation
    assert events == ["original_local_revoke", "original_bounded_persistence_created"]
    assert record["inhibit_started"] is True
    with pytest.raises(source.ControlledAccountSourceError):
        function(generation, key=key)


def test_actual_third_read_inhibitor_rejects_foreign_purpose_before_delegate():
    function, generation, _, record, events, _ = _inert_third_read_inhibitor()
    with pytest.raises(source.ControlledAccountSourceError):
        function(generation, key=object())
    assert events == [] and record["inhibit_started"] is False


def _third_read_budget_capsule(first, preflight, third, write):
    from types import FunctionType

    source._NATIVE_GUARD()
    record = {"close_post_read_cap": object(), "source_state": {}, "buffers": third}

    def retained(value):
        return value["buffers"]

    def prior_reads(value):
        assert value is record
        return {"buffers": first}, {"buffers": preflight}

    def write_buffers(value):
        assert value["buffers"] is preflight
        return write

    function = FunctionType(
        source._reconciliation_budget_v6.__code__,
        {
            "__builtins__": __builtins__,
            "_reconciliation_retained_buffers_v6": retained,
            "_close_post_prior_reads_v6": prior_reads,
            "_close_write_retained_buffers_v6": write_buffers,
            "_retained_budget_v6": source._retained_budget_v6,
        },
    )
    return function, record


def test_actual_combined_budget_code_counts_three_read_generations_and_negative_write_data():
    # Exact production budget code with inert byte roots, not an owner-positive.
    shared = b"same-object-counted-once"
    function, record = _third_read_budget_capsule(
        (shared,), (shared,), (shared,), (shared,)
    )
    function(record)
    size = source._MAX_RETAINED_V6 // 4 + 1
    function, record = _third_read_budget_capsule(
        (b"a" * size,), (b"b" * size,), (b"c" * size,), (b"d" * size,)
    )
    with pytest.raises(
        source.ControlledAccountSourceError, match="retained_byte_capacity"
    ):
        function(record)


@pytest.mark.parametrize("component", ("root_buffers", "read_buffers"))
def test_source_guard_pins_actual_passive_live_buffer_helpers(monkeypatch, component):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.exchange.okx import native_reconciliation_observation_v1 as read
    from app.trade_qualification.native_continuation_guard_v1 import (
        NativeSourceClosureError,
    )

    source._NATIVE_GUARD()
    with monkeypatch.context() as patch:
        if component == "root_buffers":
            patch.setattr(
                write,
                "_native_source_retained_buffers_v1",
                lambda *_args, **_kwargs: (),
            )
        else:
            patch.setattr(read, "retained_httpx_journal_data_v1", lambda *_args: ())
        with pytest.raises(NativeSourceClosureError):
            source._NATIVE_GUARD()
    source._NATIVE_GUARD()


def test_empty_terminal_scan_input_is_data_without_an_observed_success_claim():
    source._scan_reconciliation_received_buffer_v6(b"", ("SYNTHETIC_NO_ACK_TOKEN",))
    with pytest.raises(source.ControlledAccountSourceError):
        source._scan_reconciliation_complete_document_v6(
            b'{"partial":', ("SYNTHETIC_NO_ACK_TOKEN",)
        )


def test_source_guard_pins_actual_final_close_facts_leaf(monkeypatch):
    from app.trade_qualification import native_close_final_facts_v1 as final
    from app.trade_qualification.native_continuation_guard_v1 import (
        NativeSourceClosureError,
    )

    source._NATIVE_GUARD()
    with monkeypatch.context() as patch:
        patch.setattr(final, "_ORDER_ROLES", ())
        with pytest.raises(NativeSourceClosureError):
            source._NATIVE_GUARD()
    source._NATIVE_GUARD()
