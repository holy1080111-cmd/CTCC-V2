"""Pure fixed-column redaction checks; no app, SQL, sockets or process execution."""

import hashlib
import json
import pickle

import pytest

from scripts.pg_wait_redaction_v1 import (
    COMMAND_CATEGORIES,
    PgObserverFormatDenied,
    PgWaitFormatterV1,
    PgWaitPolicyV1,
    validate_formatted_sample,
)

CANARY = "CTCC_SYNTHETIC_SECRET_CANARY_NEVER_EXPORT_20261022"
SECRET = b"synthetic-memory-only-run-secret"


def policy(**changes):
    return PgWaitPolicyV1(
        **{
            "max_samples": 3,
            "max_rows": 3,
            "max_blockers_per_row": 3,
            "max_query_bytes": 1024,
            "max_duration_ns": 100,
            **changes,
        }
    )


def owner(**changes):
    return PgWaitFormatterV1(run_secret=SECRET, policy=policy(**changes))


def row(**changes):
    return {
        "pid": 2130706001,
        "datname": "ctcc_migration_test_" + "a" * 32,
        "backend_type": "client backend",
        "state": "active",
        "wait_event_type": "Lock",
        "wait_event": "relation",
        "blocking_pids": [2130706002],
        "query_sha256": hashlib.sha256(CANARY.encode()).hexdigest(),
        "query_bytes": len(CANARY.encode()),
        "command_category": "ALTER",
        **changes,
    }


def render(formatter, rows=None, **changes):
    return formatter.format_sample(
        [row()] if rows is None else rows,
        sample_index=changes.get("sample_index", 0),
        elapsed_monotonic_ns=changes.get("elapsed_monotonic_ns", 0),
    )


def denied(formatter, rows=None, **changes):
    with pytest.raises(PgObserverFormatDenied) as caught:
        render(formatter, rows, **changes)
    assert str(caught.value) == "PG_OBSERVER_FORMAT_DENIED"
    assert CANARY not in str(caught.value) + repr(caught.value)
    with pytest.raises(PgObserverFormatDenied):
        render(formatter, sample_index=1, elapsed_monotonic_ns=1)


def test_fixed_columns_redacted_canonical_and_no_identity_or_secret():
    formatter = owner()
    raw = render(formatter)
    report = json.loads(raw)
    assert validate_formatted_sample(raw) == report
    assert report["status"] == "PASS" and report["execution_authority"] is False
    assert CANARY.encode() not in raw and SECRET not in raw
    assert row()["datname"].encode() not in raw
    assert str(row()["pid"]).encode() not in raw
    assert report["rows"][0]["database_kind"] == "MIGRATION_TEST"
    assert report["rows"][0]["query_sha256"] == row()["query_sha256"]
    assert (
        raw
        == json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    )


def test_labels_same_within_run_different_between_runs_and_domains():
    first = owner()
    a = json.loads(render(first))["rows"][0]
    b = json.loads(render(first, sample_index=1, elapsed_monotonic_ns=1))["rows"][0]
    other = PgWaitFormatterV1(run_secret=b"x" * 32, policy=policy())
    c = json.loads(render(other))["rows"][0]
    assert a["pid_label"] == b["pid_label"] != c["pid_label"]
    assert a["database_label"] == b["database_label"] != c["database_label"]
    assert a["pid_label"] != a["database_label"]
    assert SECRET.decode() not in repr(first)
    with pytest.raises(PgObserverFormatDenied):
        pickle.dumps(first)


def test_actual_blocker_order_and_duplicates_are_retained():
    raw = render(
        owner(), [row(blocking_pids=[23, 23, 21]), row(pid=23, blocking_pids=[])]
    )
    rows = json.loads(raw)["rows"]
    assert (
        rows[0]["blocking_pid_labels"][0]
        == rows[0]["blocking_pid_labels"][1]
        == rows[1]["pid_label"]
    )
    assert rows[0]["blocking_pid_labels"][2] != rows[1]["pid_label"]


def test_explicit_null_wait_pair_and_explicit_empty_rows_are_observations():
    report = json.loads(render(owner(), [row(wait_event_type=None, wait_event=None)]))
    assert report["status"] == "PASS"
    assert (
        report["rows"][0]["wait_event_type"]
        == report["rows"][0]["wait_event"]
        == "NOT_WAITING"
    )
    empty = json.loads(render(owner(), []))
    assert empty["status"] == "PASS" and empty["observed_row_count"] == 0
    assert empty["rows"] == []


@pytest.mark.parametrize(
    "database,kind",
    [
        ("ctcc", "PARENT"),
        ("ctcc_clock_" + "a" * 18, "CLOCK_TEST"),
        ("ctcc_rt_" + "b" * 18, "RUNTIME_TEST"),
    ],
)
def test_only_fixed_database_shapes_classified(database, kind):
    report = json.loads(render(owner(), [row(datname=database)]))
    assert report["status"] == "PASS" and report["rows"][0]["database_kind"] == kind


@pytest.mark.parametrize(
    "field",
    [
        "backend_type",
        "state",
        "wait_event_type",
        "wait_event",
        "command_category",
        "datname",
    ],
)
def test_unknown_canary_only_fixed_unknown_and_failure_latch(field):
    formatter = owner()
    raw = render(formatter, [row(**{field: CANARY})])
    report = json.loads(raw)
    assert report["status"] == "FAIL" and report["reasons"]
    assert CANARY.encode() not in raw and SECRET not in raw
    assert "UNKNOWN" in report["rows"][0].values()
    denied(formatter, sample_index=1, elapsed_monotonic_ns=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "活躍"},
        {"state": None},
        {"wait_event_type": None},
        {"wait_event": None},
        {"wait_event_type": "Client", "wait_event": "relation"},
        {"command_category": "UNKNOWN"},
    ],
)
def test_explicit_unknown_is_failure_without_raw_field_export(changes):
    raw = render(owner(), [row(**changes)])
    assert json.loads(raw)["status"] == "FAIL"
    for value in changes.values():
        if isinstance(value, str) and not value.isascii():
            assert value.encode() not in raw


@pytest.mark.parametrize("field", list(row()))
def test_missing_required_column_never_filled(field):
    incomplete = row()
    del incomplete[field]
    denied(owner(), [incomplete])


@pytest.mark.parametrize(
    "changes",
    [
        {"query": CANARY},
        {"pid": True},
        {"pid": 0},
        {"pid": 2**31},
        {"pid": CANARY},
        {"datname": None},
        {"datname": "a" * 64},
        {"blocking_pids": None},
        {"blocking_pids": (1,)},
        {"blocking_pids": [True]},
        {"blocking_pids": [0]},
        {"blocking_pids": [1, 2, 3, 4]},
        {"query_sha256": None},
        {"query_sha256": "a" * 63},
        {"query_sha256": "A" * 64},
        {"query_sha256": CANARY},
        {"query_bytes": None},
        {"query_bytes": True},
        {"query_bytes": -1},
        {"query_bytes": 1025},
        {"query_bytes": float("nan")},
        {"query_bytes": float("inf")},
        {"state": CANARY * 4},
        {"state": 1},
    ],
)
def test_malformed_oversize_or_extra_columns_fail_closed(changes):
    denied(owner(), [row(**changes)])


@pytest.mark.parametrize(
    "rows", [None, (), {}, "not-rows", [row(), row()], [row()] * 4]
)
def test_invalid_inventory_or_duplicate_backend_denied(rows):
    if rows is None:
        formatter = owner()
        with pytest.raises(PgObserverFormatDenied):
            formatter.format_sample(None, sample_index=0, elapsed_monotonic_ns=0)
    else:
        denied(owner(), rows)


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_index": True},
        {"sample_index": -1},
        {"sample_index": 1},
        {"sample_index": 3},
        {"sample_index": float("nan")},
        {"elapsed_monotonic_ns": True},
        {"elapsed_monotonic_ns": -1},
        {"elapsed_monotonic_ns": 101},
        {"elapsed_monotonic_ns": float("inf")},
        {"elapsed_monotonic_ns": float("nan")},
    ],
)
def test_sample_index_or_duration_bound_denies(changes):
    denied(owner(), **changes)


def test_duration_boundary_backwards_time_and_sample_limit():
    assert json.loads(render(owner(), elapsed_monotonic_ns=100))["status"] == "PASS"
    formatter = owner()
    render(formatter, elapsed_monotonic_ns=2)
    denied(formatter, sample_index=1, elapsed_monotonic_ns=1)
    formatter = owner(max_samples=1)
    render(formatter)
    denied(formatter, sample_index=1, elapsed_monotonic_ns=1)


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("max_samples", 64),
        ("max_rows", 128),
        ("max_blockers_per_row", 64),
        ("max_query_bytes", 1_048_576),
        ("max_duration_ns", 630_000_000_000),
    ],
)
@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf"), None])
def test_policy_missing_nonfinite_and_nonpositive_denied(field, maximum, bad):
    with pytest.raises(PgObserverFormatDenied):
        policy(**{field: bad})
    with pytest.raises(PgObserverFormatDenied):
        policy(**{field: maximum + 1})


@pytest.mark.parametrize(
    "secret", [None, "x" * 32, b"x" * 31, b"x" * 33, bytearray(b"x" * 32)]
)
def test_secret_never_coerced_or_exported(secret):
    with pytest.raises(PgObserverFormatDenied) as caught:
        PgWaitFormatterV1(run_secret=secret, policy=policy())
    assert str(caught.value) == "PG_OBSERVER_FORMAT_DENIED"


@pytest.mark.parametrize("category", sorted(COMMAND_CATEGORIES))
def test_fixed_command_categories_roundtrip(category):
    raw = render(owner(), [row(command_category=category)])
    assert validate_formatted_sample(raw)["status"] == "PASS"


def test_failure_roundtrip_checks_exact_unknown_reasons():
    raw = render(owner(), [row(state=CANARY, wait_event=CANARY)])
    report = validate_formatted_sample(raw)
    assert report["status"] == "FAIL"
    assert report["reasons"] == ["STATE_UNKNOWN", "WAIT_EVENT_UNKNOWN"]
    assert CANARY.encode() not in raw


def canonical_report(report):
    return json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "status",
        "sample_index",
        "elapsed_monotonic_ns",
        "observed_row_count",
        "rows",
        "reasons",
        "raw_sql_or_identity_exported",
        "execution_authority",
    ],
)
def test_reader_rejects_missing_top_column(field):
    report = json.loads(render(owner()))
    del report[field]
    with pytest.raises(PgObserverFormatDenied):
        validate_formatted_sample(canonical_report(report))


@pytest.mark.parametrize(
    "changes",
    [
        {"raw_query": CANARY},
        {"status": "FAIL"},
        {"sample_index": True},
        {"sample_index": 64},
        {"elapsed_monotonic_ns": 630_000_000_001},
        {"observed_row_count": 0},
        {"reasons": [CANARY]},
        {"raw_sql_or_identity_exported": True},
        {"execution_authority": True},
    ],
)
def test_reader_rejects_extra_or_inconsistent_top_values(changes):
    report = {**json.loads(render(owner())), **changes}
    with pytest.raises(PgObserverFormatDenied) as caught:
        validate_formatted_sample(canonical_report(report))
    assert CANARY not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"pid": 123},
        {"pid_label": CANARY},
        {"database_label": CANARY},
        {"database_kind": CANARY},
        {"backend_type": CANARY},
        {"state": CANARY},
        {"wait_event_type": "NOT_WAITING"},
        {"wait_event": CANARY},
        {"blocking_pid_labels": [123]},
        {"query_sha256": CANARY},
        {"query_bytes": True},
        {"query_bytes": 1_048_577},
        {"command_category": CANARY},
        {"raw_query": CANARY},
    ],
)
def test_reader_strict_sanitized_row_shapes(changes):
    report = json.loads(render(owner()))
    report["rows"][0].update(changes)
    with pytest.raises(PgObserverFormatDenied) as caught:
        validate_formatted_sample(canonical_report(report))
    assert CANARY not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{}",
        b"{}",
        b'{"status":"PASS","status":"PASS"}',
        b'{"rows":[{"pid_label":"a","pid_label":"b"}]}',
        b"NaN",
        b"Infinity",
        b"{}" * 600_000,
    ],
    ids=[
        "none",
        "text",
        "empty",
        "duplicate-top",
        "duplicate-row",
        "nan",
        "infinity",
        "oversize",
    ],
)
def test_reader_malformed_duplicate_nonfinite_and_oversize_denies(raw):
    with pytest.raises(PgObserverFormatDenied) as caught:
        validate_formatted_sample(raw)
    assert str(caught.value) == "PG_OBSERVER_FORMAT_DENIED"


def test_reader_rejects_duplicate_backend_rows_and_noncanonical_json():
    report = json.loads(render(owner()))
    report["rows"].append(report["rows"][0].copy())
    report["observed_row_count"] = 2
    with pytest.raises(PgObserverFormatDenied):
        validate_formatted_sample(canonical_report(report))
    with pytest.raises(PgObserverFormatDenied):
        validate_formatted_sample(
            json.dumps(json.loads(render(owner())), indent=2).encode()
        )


@pytest.mark.parametrize(
    "field",
    [
        "pid_label",
        "database_kind",
        "database_label",
        "backend_type",
        "state",
        "wait_event_type",
        "wait_event",
        "blocking_pid_labels",
        "query_sha256",
        "query_bytes",
        "command_category",
    ],
)
def test_reader_missing_redacted_row_field_denied(field):
    report = json.loads(render(owner()))
    del report["rows"][0][field]
    with pytest.raises(PgObserverFormatDenied):
        validate_formatted_sample(canonical_report(report))


def test_full_hard_limits_still_produce_bounded_validated_bytes():
    formatter = owner(
        max_samples=64,
        max_rows=128,
        max_blockers_per_row=64,
        max_query_bytes=1_048_576,
        max_duration_ns=630_000_000_000,
    )
    rows = [
        row(
            pid=index + 1,
            datname="ctcc",
            blocking_pids=list(range(129, 193)),
            query_bytes=1_048_576,
            wait_event_type=None,
            wait_event=None,
        )
        for index in range(128)
    ]
    raw = render(formatter, rows, elapsed_monotonic_ns=630_000_000_000)
    assert len(raw) <= 1_048_576
    result = validate_formatted_sample(raw)
    assert result["status"] == "PASS" and result["observed_row_count"] == 128
