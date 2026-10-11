"""Pure edge checks for the DENY-only V7 native DB-clock recency companion."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories import account_current_history_observation as prior
from app.database.repositories import account_current_history_recency as recency

AT = datetime(2026, 10, 12, 0, 0, 0, tzinfo=UTC)


def page(eof: datetime) -> dict[str, str]:
    return {
        "request_started_at": (eof - timedelta(milliseconds=3)).isoformat(),
        "headers_received_at": (eof - timedelta(milliseconds=1)).isoformat(),
        "body_exhausted_at": eof.isoformat(),
        "body_completed_at": (eof + timedelta(milliseconds=1)).isoformat(),
    }


def test_policy_is_new_and_cannot_issue_account_or_execution_authority() -> None:
    policy = json.loads(recency.POLICY_BYTES)
    assert recency.POLICY_SHA256 != prior.POLICY_SHA256
    assert policy["prior_join_policy_sha256"] == prior.POLICY_SHA256
    assert policy["maximum_age_seconds"] == 30
    assert policy["account_complete"] is False
    assert policy["flat_start_permission"] is False
    assert policy["execution_authority"] is False
    view = recency.CurrentHistoryRecencyReadback(b"{}")
    assert view.account_complete is False
    assert view.flat_start_permission is False
    assert view.execution_authority is False
    assert view.admission == "DENY"


def test_native_clock_query_is_schema_qualified() -> None:
    assert "pg_catalog.clock_timestamp()" in str(recency._native_db_clock_statement())


def test_oldest_original_eof_controls_exact_30_second_boundary() -> None:
    at_boundary = recency.evaluate_db_clock_page_recency(
        [page(AT - timedelta(seconds=30)), page(AT - timedelta(seconds=2))], AT
    )
    assert at_boundary["recorded_eof_db_clock_delta_within_30_seconds"] is True
    assert at_boundary["age_microseconds"] == 30_000_000

    one_microsecond_stale = recency.evaluate_db_clock_page_recency(
        [page(AT - timedelta(seconds=30, microseconds=1))], AT
    )
    assert (
        one_microsecond_stale["recorded_eof_db_clock_delta_within_30_seconds"] is False
    )
    assert one_microsecond_stale["age_microseconds"] == 30_000_001


@pytest.mark.parametrize(
    "change",
    (
        {"request_started_at": AT.isoformat()},
        {"headers_received_at": (AT - timedelta(seconds=40)).isoformat()},
        {"body_exhausted_at": (AT + timedelta(microseconds=1)).isoformat()},
        {"body_completed_at": (AT + timedelta(microseconds=1)).isoformat()},
        {"body_exhausted_at": None},
    ),
)
def test_future_out_of_order_or_missing_page_time_never_gets_recency(
    change: dict[str, str | None],
) -> None:
    corrupted = page(AT - timedelta(seconds=1))
    corrupted.update(change)
    with pytest.raises(recency.CurrentHistoryRecencyError):
        recency.evaluate_db_clock_page_recency([corrupted], AT)


def test_page_chain_cannot_overlap_or_use_naive_or_missing_db_clock() -> None:
    first = page(AT - timedelta(seconds=5))
    overlapping = page(AT - timedelta(seconds=6))
    with pytest.raises(
        recency.CurrentHistoryRecencyError,
        match="recency_page_order_or_future_invalid",
    ):
        recency.evaluate_db_clock_page_recency([first, overlapping], AT)
    with pytest.raises(recency.CurrentHistoryRecencyError):
        recency.evaluate_db_clock_page_recency([first], AT.replace(tzinfo=None))
    with pytest.raises(recency.CurrentHistoryRecencyError):
        recency.evaluate_db_clock_page_recency([], AT)
