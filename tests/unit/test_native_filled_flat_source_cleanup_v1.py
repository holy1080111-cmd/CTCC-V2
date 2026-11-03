"""Finite DATA/mechanism negatives; no genuine filled/Root/DB capability is minted."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import native_controlled_account_source_v6 as source
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post


def _observation():
    now = datetime(2026, 11, 3, tzinfo=UTC)
    return read.DemoReconciliationObservationV1(
        path="/api/v5/trade/order",
        parameters=(("instId", "BTC-USDT-SWAP"),),
        request_url="https://openapi.okx.com/api/v5/trade/order?instId=BTC-USDT-SWAP",
        request_signature_sha256="a" * 64,
        read_session_id="synthetic-unit-read",
        started_at=now,
        started_monotonic_ns=1,
        headers_at=now,
        headers_monotonic_ns=2,
        eof_at=now,
        eof_monotonic_ns=3,
        http_status=200,
        raw_headers=((b"content-type", b"application/json"),),
        raw_body=b'{"code":"0","data":[]}',
        certificate_sha256="b" * 64,
        tls_hostname="openapi.okx.com",
    )


def _readsets():
    generations = tuple(object() for _ in range(3))
    observations = tuple(_observation() for _ in range(3))
    return generations, tuple(
        (generation, str(index + 1) * 64, (("actual_role", observation),))
        for index, (generation, observation) in enumerate(
            zip(generations, observations, strict=True)
        )
    )


def _registries():
    # Identities only, no receipt/credential or private row contents.
    return tuple(
        tuple((id(key), id(value)) for key, value in mapping.items())
        for mapping in (
            source._FILLED_FLAT_CLEANUP_RECORDS_V6,
            source._FILLED_FLAT_CLEANUP_PREPARED_V6,
            source._FILLED_FLAT_CLEANUP_COMPLETED_V6,
            source._FILLED_FLAT_CLEANUP_RETIRED_V6,
            source._FILLED_FLAT_CLEANUP_TERMINAL_V6,
        )
    )


def test_three_manifest_shape_is_only_data_and_grants_no_owner():
    generations, readsets = _readsets()
    before = _registries()
    assert source._filled_cleanup_generation_tuple_v6(generations) is generations
    assert source._filled_cleanup_readsets_shape_v6(generations, readsets) is readsets
    assert _registries() == before
    assert all(row[2][0][1].execution_authority is False for row in readsets)


@pytest.mark.parametrize(
    "fault", ("list", "two", "four", "duplicate", "integer", "bool")
)
def test_exact_three_distinct_generation_shape_rejects_faults(fault):
    values = tuple(object() for _ in range(3))
    if fault == "list":
        values = list(values)
    elif fault == "two":
        values = values[:2]
    elif fault == "four":
        values = (*values, object())
    elif fault == "duplicate":
        values = (values[0], values[0], values[2])
    else:
        values = (values[0], 1 if fault == "integer" else True, values[2])
    with pytest.raises(source.ControlledAccountSourceError, match="three_generations"):
        source._filled_cleanup_generation_tuple_v6(values)


@pytest.mark.parametrize(
    "fault",
    (
        "missing_manifest",
        "order",
        "duplicate_manifest",
        "upper_hex",
        "non_hex",
        "empty_rows",
        "duplicate_role",
        "foreign_observation",
        "role_type",
        "role_path",
        "mutated_false_flag",
    ),
)
def test_complete_committed_readset_shape_rejects_missing_or_mutated_data(fault):
    generations, rows = _readsets()
    if fault == "missing_manifest":
        rows = rows[:2]
    elif fault == "order":
        rows = (rows[1], rows[0], rows[2])
    else:
        generation, manifest, members = rows[1]
        if fault == "duplicate_manifest":
            manifest = rows[0][1]
        elif fault == "upper_hex":
            manifest = "A" * 64
        elif fault == "non_hex":
            manifest = "z" * 64
        elif fault == "empty_rows":
            members = ()
        elif fault == "duplicate_role":
            members = (*members, members[0])
        elif fault == "foreign_observation":
            members = (("actual_role", object()),)
        elif fault == "role_type":
            members = ((True, members[0][1]),)
        elif fault == "role_path":
            members = (("outside/role", members[0][1]),)
        else:
            object.__setattr__(members[0][1], "execution_authority", True)
        rows = (rows[0], (generation, manifest, members), rows[2])
    with pytest.raises(source.ControlledAccountSourceError):
        source._filled_cleanup_readsets_shape_v6(generations, rows)


def test_observation_equality_does_not_run_foreign_field_callbacks():
    calls = []

    class Foreign:
        def __eq__(self, _other):
            calls.append("foreign_equality")
            raise AssertionError("unexpected callback")

    raw = _observation()
    assert source._filled_cleanup_observation_data_v6(raw) is True
    for name in ("path", "parameters", "raw_headers", "started_at", "raw_body"):
        changed = replace(raw)
        object.__setattr__(changed, name, Foreign())
        assert source._filled_cleanup_observation_data_v6(changed) is False
    assert source._filled_cleanup_observation_data_v6(Foreign()) is False
    assert calls == []


def test_cleanup_clock_uses_original_start_and_never_restarts_five_seconds():
    start = {"utc_ns": 1_000_000_000, "monotonic_ns": 2_000_000_000}
    near = {name: value + 4_999_999_999 for name, value in start.items()}
    end = {name: value + 5_000_000_000 for name, value in start.items()}
    assert source._filled_cleanup_window_v6(start, start, near) is near
    with pytest.raises(source.ControlledAccountSourceError, match="budget_exhausted"):
        source._filled_cleanup_window_v6(start, near, end)
    with pytest.raises(ValueError):
        source._filled_cleanup_window_v6(start, near, start)


@pytest.mark.parametrize(
    "kind",
    (
        source._PreparedFilledFlatCleanupV6,
        source._CompletedFilledFlatCleanupV6,
        source._RetiredFilledFlatCleanupV6,
    ),
)
def test_filled_cleanup_capabilities_cannot_be_constructed(kind):
    with pytest.raises(source.ControlledAccountSourceError, match="not_constructible"):
        kind()


@pytest.mark.parametrize(
    "operation", ("prepare", "finish", "replay", "retire", "receipt", "scan", "compact")
)
def test_unregistered_objects_never_release_or_mint_cleanup_state(operation):
    before = _registries()
    foreign, key = object(), post._FILLED_SOURCE_SETTLEMENT_KEY_V1
    with pytest.raises(ValueError):
        if operation == "prepare":
            source.prepare_native_filled_flat_source_cleanup_v1(foreign, key=key)
        elif operation == "finish":
            source.finish_native_filled_flat_source_cleanup_v1(foreign, key=key)
        elif operation == "replay":
            source._replay_native_filled_flat_source_cleanup_completion_v1(
                foreign, key=key
            )
        elif operation == "retire":
            source.retire_native_filled_flat_source_cleanup_v1(foreign, key=key)
        elif operation == "receipt":
            source._replay_native_filled_flat_source_retirement_v1(foreign, key=key)
        elif operation == "scan":
            source._scan_native_filled_cleanup_retained_data_v1(foreign, b"{}", key=key)
        else:
            source.compact_native_filled_cleanup_retirement_v1(
                foreign, object(), key=key
            )
    assert _registries() == before


def test_one_retained_budget_survives_snapshot_and_new_row_without_copying_text():
    # Pure immutable DATA; no Source/Root/journal ownership is asserted.
    raw, text = b"same retained bytes", "same retained text"
    default = source._close_write_buffer_bytes_v6(((raw, raw), text))
    assert all(type(value) is bytes for value in default)
    snapshot = source._close_write_buffer_bytes_v6(
        ((raw, raw), text), preserve_text=True
    )
    assert sum(value is raw for value in snapshot) == 1
    assert sum(value is text for value in snapshot) == 1
    source._retained_budget_v6({"history_tokens": (snapshot, snapshot)})
    retained = b"x" * (source._MAX_RETAINED_V6 - 32)
    source._retained_budget_v6(
        {"history_tokens": (retained, retained)}, extra=(b"y" * 32,)
    )
    with pytest.raises(
        source.ControlledAccountSourceError, match="retained_byte_capacity"
    ):
        source._retained_budget_v6(
            {"history_tokens": (retained, retained)}, extra=(b"y" * 33,)
        )
    with pytest.raises(source.ControlledAccountSourceError):
        source._close_write_buffer_bytes_v6((raw,), preserve_text="yes")


def test_empty_or_unregistered_facade_data_never_satisfies_retirement():
    # R12 authenticates actual negative Control resolution before facade data.
    with pytest.raises(
        source.ControlledAccountSourceError,
        match="^controlled_account_cleanup_control_resolution_required$",
    ):
        source._filled_cleanup_controllers_v6((), object())
    with pytest.raises(source.ControlledAccountSourceError):
        source._filled_cleanup_controllers_v6(
            (({}, object(), object(), (), "a" * 64, 1),), object()
        )
