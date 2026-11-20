"""Synthetic local journal DATA and refusal tests; no API/venue/SQL requests."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation import native_journal_readout_v1 as journal
from app.demo_automation.native_journal_models_v1 import NativeJournalReadoutV1

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def empty():
    return {
        "observed_at": NOW,
        "total_native": 0,
        "unresolved_native": 0,
        "unresolved_other": 0,
        "entries": [],
    }


def item(state="uncertain", revision=3):
    return {
        "reservation_id": "a" * 64,
        "report_id": "declared-synthetic-journal",
        "instrument_id": "BTC-USDT-SWAP",
        "direction": "long",
        "local_state": state,
        "state_revision": revision,
        "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        "updated_at": NOW.isoformat(),
        "deadline": (NOW + timedelta(seconds=10)).isoformat(),
    }


def test_observed_local_empty_is_data_and_never_exchange_flatness():
    value = journal._readout(empty(), uid="730058", head="0058", limit=20)
    assert value.total_native == value.unresolved_native == value.unresolved_other == 0
    assert value.entries == () and value.truncated is False
    assert (
        value.account_complete
        is value.execution_authority
        is value.order_retry_authority
        is False
    )
    assert "730058" not in value.model_dump_json()


def test_other_unresolved_journal_work_is_not_hidden_by_native_only_listing():
    value = journal._readout(
        {
            **empty(),
            "total_native": 1,
            "unresolved_native": 1,
            "unresolved_other": 2,
            "entries": [item()],
        },
        uid="730058",
        head="0058",
        limit=20,
    )
    assert value.unresolved_native + value.unresolved_other == 3
    assert value.entries[0].protection_status == "NOT_RECHECKED"
    assert value.entries[0].reservation_id == "a" * 64


@pytest.mark.parametrize(
    "key,value",
    (
        ("entries", None),
        ("total_native", None),
        ("total_native", False),
        ("unresolved_native", -1),
        ("unresolved_other", None),
    ),
)
def test_unknown_or_malformed_counts_never_become_zero_or_empty(key, value):
    with pytest.raises((DemoAutomationSafetyError, ValidationError)):
        journal._readout({**empty(), key: value}, uid="730058", head="0058", limit=20)


@pytest.mark.parametrize("mutation", ("missing_row", "duplicate", "revision", "future"))
def test_missing_duplicate_conflicting_and_future_local_rows_refuse(mutation):
    raw = {**empty(), "total_native": 1, "unresolved_native": 1, "entries": [item()]}
    if mutation == "missing_row":
        raw["entries"] = []
    elif mutation == "duplicate":
        raw["entries"] = [item(), item()]
        raw["total_native"] = 2
    elif mutation == "revision":
        raw["entries"][0]["state_revision"] = 4
    else:
        raw["entries"][0]["updated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError):
        journal._readout(raw, uid="730058", head="0058", limit=20)


def test_truncated_listing_remains_explicit_and_has_no_authority_flags():
    value = journal._readout(
        {**empty(), "total_native": 2, "unresolved_native": 2, "entries": [item()]},
        uid="730058",
        head="0058",
        limit=1,
    )
    assert value.truncated is True
    assert len(value.entries) == 1 and value.total_native == 2
    for field in ("account_complete", "execution_authority", "order_retry_authority"):
        for changed in (True, 0, None):
            wire = value.model_dump(mode="json", round_trip=True)
            wire[field] = changed
            import json

            with pytest.raises(ValidationError):
                NativeJournalReadoutV1.model_validate_json(
                    json.dumps(wire), strict=True
                )


def test_query_is_one_scoped_statement_with_no_order_or_database_mutation():
    sql = str(journal._QUERY)
    assert ":uid" in sql and ":limit" in sql
    assert "environment='demo'" in sql and "settlement_currency='USDT'" in sql
    assert "IS DISTINCT FROM 'ctcc-reservation-request-native-v4'" in sql
    assert not any(
        word in sql.upper().split()
        for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CALL", "GRANT")
    )


def test_new_readout_compiled_source_guard_has_no_submission_capability():
    journal._NATIVE_GUARD()


def test_writer_route_uses_direct_tls_pin_and_never_the_factory_identity_tuple(
    monkeypatch,
):
    # Mock DATA routing only. Actual restricted TLS/catalog verification runs
    # separately in the public58 PostgreSQL cases, without patched observers.
    selected, factory, engine = object(), object(), object()
    names = ("synthetic_writer", "synthetic_custody", "synthetic_reader")
    factory_routes = (("factory-w",), ("factory-c",), ("factory-r",))
    direct_route = ("postgresql+asyncpg", "declared-db", 5432, "declared-db")
    calls = []

    def factories(value):
        assert value is selected
        calls.append("factory-identity")
        return factory, engine, names, factory_routes

    def direct(value):
        assert value is engine
        calls.append("direct-tls-route")
        return direct_route

    with monkeypatch.context() as patch:
        patch.setattr(journal.database, "_factory_pins", factories)
        patch.setattr(journal.database.locked, "_direct_tls_route", direct)
        actual = journal._local_writer_pins_v1(selected)
    assert actual == (factory, engine, names, direct_route)
    assert actual[3] is direct_route and actual[3] is not factory_routes
    assert calls == ["factory-identity", "direct-tls-route"]
