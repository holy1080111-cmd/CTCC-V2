"""Declared control arithmetic/privilege negatives, never an actual Live Arm."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.database.repositories import native_live_operator_control_v1 as subject
from app.domain import native_live_operator_control_schema_v1 as spec

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def request(action="prepare", **changes):
    value = subject.LiveOperatorControlRequestV1(
        action,
        object(),
        None,
        "123456",
        "123450",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        None,
        None,
        None,
        NOW + timedelta(seconds=180),
        (Decimal("0.25"), Decimal("0.25"), Decimal(25), Decimal(25)),
        None,
    )
    return replace(value, **changes)


def stopped():
    return subject._change(None, request(), now=NOW, writer_oid=42)


def arm_pair():
    row = stopped()
    clear = request(
        "clear_stop", expected_revision=row["revision"], approval_sha256="e" * 64
    )
    row = subject._change(row, clear, now=NOW + timedelta(seconds=1), writer_oid=42)
    arm = request(
        "arm",
        expected_revision=row["revision"],
        approval_sha256="e" * 64,
        arm_id="f" * 64,
        arm_expires_at=NOW + timedelta(seconds=25),
    )
    return subject._change(row, arm, now=NOW + timedelta(seconds=2), writer_oid=42), arm


def test_prepare_is_stopped_and_clear_is_a_distinct_operation():
    row = stopped()
    assert row["armed"] is False and row["emergency_stop"] is True
    with pytest.raises(subject.LiveOperatorControlError):
        subject._change(
            row,
            request(
                "arm",
                expected_revision=1,
                approval_sha256="e" * 64,
                arm_id="f" * 64,
                arm_expires_at=NOW + timedelta(seconds=25),
            ),
            now=NOW + timedelta(seconds=1),
            writer_oid=42,
        )
    assert row == stopped()


def test_refresh_changes_only_source_revision_counter_and_update_without_extending_arm():
    row, arm = arm_pair()
    refresh = replace(
        arm,
        action="refresh",
        expected_revision=row["revision"],
        source_revision_sha256="1" * 64,
    )
    after = subject._change(row, refresh, now=NOW + timedelta(seconds=3), writer_oid=42)
    assert {name for name in row if row[name] != after[name]} == {
        "source_revision_sha256",
        "revision",
        "updated_at",
    }
    assert (
        after["arm_id"] == row["arm_id"]
        and after["arm_expires_at"] == row["arm_expires_at"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"arm_expires_at": NOW + timedelta(seconds=26)},
        {"arm_id": "1" * 64},
        {"credential_session_sha256": "1" * 64},
        {"process_sha256": "1" * 64},
        {"policy_sha256": "1" * 64},
        {"expected_revision": 1},
        {"lease_until": NOW + timedelta(seconds=181)},
        {"approval_sha256": None},
        {"caps": (Decimal("0.25"), Decimal("0.25"), Decimal(25), Decimal(26))},
    ],
)
def test_refresh_refuses_scope_process_session_expiry_and_caps_drift(change):
    row, arm = arm_pair()
    refresh = replace(arm, action="refresh", expected_revision=row["revision"])
    with pytest.raises(subject.LiveOperatorControlError):
        subject._change(
            row,
            replace(refresh, **change),
            now=NOW + timedelta(seconds=3),
            writer_oid=42,
        )


def test_stop_is_negative_and_preserves_every_other_account_field():
    row, arm = arm_pair()
    stop = replace(
        arm, action="stop", expected_revision=row["revision"], approval_sha256=None
    )
    after = subject._change(row, stop, now=NOW + timedelta(minutes=1), writer_oid=42)
    assert after["armed"] is False and after["emergency_stop"] is True
    assert {name for name in row if row[name] != after[name]} == {
        "armed",
        "arm_id",
        "arm_expires_at",
        "emergency_stop",
        "revision",
        "updated_at",
    }


def test_prepare_cannot_relax_saved_caps_or_restore_an_arm():
    row = stopped()
    lower = replace(
        request(),
        expected_revision=1,
        caps=(Decimal("0.1"), Decimal("0.1"), Decimal(10), Decimal(10)),
    )
    after = subject._change(row, lower, now=NOW + timedelta(seconds=1), writer_oid=42)
    restore = replace(request(), expected_revision=after["revision"])
    again = subject._change(
        after, restore, now=NOW + timedelta(seconds=2), writer_oid=42
    )
    assert (
        tuple(
            again[k]
            for k in (
                "per_trade_risk_cap",
                "portfolio_risk_cap",
                "margin_cap",
                "micro_notional_cap",
            )
        )
        == lower.caps
    )
    armed, _arm = arm_pair()
    with pytest.raises(subject.LiveOperatorControlError):
        subject._change(
            armed,
            replace(request(), expected_revision=armed["revision"]),
            now=NOW + timedelta(seconds=3),
            writer_oid=42,
        )


@pytest.mark.parametrize(
    "expiry", [None, NOW, NOW + timedelta(seconds=40), NOW.replace(tzinfo=None)]
)
def test_arm_expiry_unknown_expired_extended_or_naive_is_refused(expiry):
    row = stopped()
    clear = request("clear_stop", expected_revision=1, approval_sha256="e" * 64)
    row = subject._change(row, clear, now=NOW, writer_oid=42)
    arm = request(
        "arm",
        expected_revision=2,
        approval_sha256="e" * 64,
        arm_id="f" * 64,
        arm_expires_at=expiry,
    )
    with pytest.raises(subject.LiveOperatorControlError):
        subject._change(row, arm, now=NOW + timedelta(seconds=1), writer_oid=42)


def test_operator_has_exact_fifteen_grants_and_no_ledger_financial_write_or_execute():
    statements = spec.live_operator_control_grant_sql_v1("ctcc_live_operator")
    assert len(statements) == 15
    assert len(spec.READ_TABLES) == 12
    assert sum("GRANT INSERT" in sql for sql in statements) == 1
    assert sum("GRANT UPDATE" in sql for sql in statements) == 1
    assert all(
        "EXECUTE" not in sql and "DELETE" not in sql and "TRUNCATE" not in sql
        for sql in statements
    )
    assert all(
        "native_live_controls_v1" in sql
        for sql in statements
        if "INSERT" in sql or "UPDATE" in sql
    )
    for invalid in ("public", "role;GRANT ALL", '"bad"', "", True):
        with pytest.raises(ValueError):
            spec.live_operator_control_grant_sql_v1(invalid)


def test_unconfigured_operator_factory_and_owner_role_are_refused_before_io():
    with pytest.raises(
        (subject.LiveOperatorControlError, subject.ledger.LiveLedgerDataError)
    ):
        subject.NativeLiveOperatorControlRepositoryV1(
            object(), subject.LiveOperatorRolePinsV1(1, 2, 3, "operator", 2)
        )
