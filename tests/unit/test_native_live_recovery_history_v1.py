"""Synthetic raw history/new-clock DATA; no venue, DB, lease or signer claim."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import ClockStamp, canonical, decode, sha, utc_from_ns
from app.exchange.okx.live_account_config_observation_v1 import (
    LiveAccountConfigObservationV1,
    _receipt_document,
)
from app.exchange.okx.live_account_raw_page_v1 import (
    LiveAccountRawPageV1,
    live_account_raw_page_receipt_document_v1,
)
from app.exchange.okx.live_trade_read_page_v1 import (
    live_trade_read_receipt_v1,
    select_live_trade_read_v1,
)
from app.okx_live import owned_recovery_v1 as recovery
from app.okx_live import reconciliation_facts_v1 as facts
from tests import native_live_postsubmit_fixtures_v1 as fixture


def history_scenario(*, zero=False):
    intent, data = fixture.scenario(zero=zero)

    def clock(old):
        # A later UTC day but a DIFFERENT, smaller process monotonic domain.
        return ClockStamp(
            utc_ns=old.utc_ns + 86_400_000_000_000,
            monotonic_ns=old.monotonic_ns - 99_000_000_000,
        )

    def page(old):
        values = {
            "stamps": tuple(clock(item) for item in old.stamps),
            "receipt_sha256": "",
        }
        values["signed_at"] = (
            utc_from_ns(values["stamps"][0].utc_ns)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        selector = getattr(old, "selector", None)
        if selector is not None and getattr(selector, "kind", None) == "fills":
            values["selector"] = select_live_trade_read_v1(
                "fills_history",
                instrument_id=selector.instrument_id,
                order_id=selector.order_id,
                after_bill_id=selector.after_bill_id,
            )
        new = replace(old, **values)
        document = (
            _receipt_document(new)
            if type(new) is LiveAccountConfigObservationV1
            else live_account_raw_page_receipt_document_v1(new)
            if type(new) is LiveAccountRawPageV1
            else live_trade_read_receipt_v1(new)
        )
        return replace(new, receipt_sha256=sha(canonical(document)))

    for key in ("order_page", "config_before", "config_after", "protection_page"):
        if data[key] is not None:
            data[key] = page(data[key])
    for key in ("fill_pages", "current_pages"):
        data[key] = tuple(page(item) for item in data[key])
    data["submission_started"] = None
    data["submission_barrier"] = clock(data["submission_barrier"])
    data["validated_at"] = clock(data["validated_at"])
    data["history"] = facts.LiveReconciliationHistoryV1(utc_from_ns(fixture.BASE))
    return intent, data


@pytest.mark.parametrize(
    "zero,outcome", [(False, "FULL_FILL_PROTECTED"), (True, "ZERO_FILL_FLAT")]
)
def test_new_clock_domain_replays_old_exact_order_and_full_history_without_arm(
    zero, outcome
):
    intent, data = history_scenario(zero=zero)
    assert data["validated_at"].monotonic_ns < fixture.stamp(0).monotonic_ns
    assert data["submission_started"] is None
    assert data["validated_at"].utc_ns > int(
        intent.deadline.timestamp() * 1_000_000_000
    )
    result = facts.assess_live_postsubmit_facts_v1(intent, **data)
    assert result.outcome == outcome and result.codes == ()
    assert (
        result.execution_authority
        is result.resolution_authority
        is result.economic_qualification_verified
        is False
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_history",
        "pretend_current_post",
        "future_intent_record",
        "naive_record",
        "changed_uid",
        "changed_stop",
        "stale",
        "not_found",
        "missing_fill_terminal",
        "partial",
    ],
)
def test_unknown_or_changed_history_current_source_is_held(mutation):
    intent, data = history_scenario()
    if mutation == "missing_history":
        data["history"] = None
    elif mutation == "pretend_current_post":
        data["submission_started"] = fixture.stamp(0)
    elif mutation == "future_intent_record":
        data["history"] = facts.LiveReconciliationHistoryV1(
            intent.deadline + timedelta(seconds=1)
        )
    elif mutation == "naive_record":
        data["history"] = facts.LiveReconciliationHistoryV1(
            intent.deadline.replace(tzinfo=None)
        )
    elif mutation == "stale":
        old = data["validated_at"]
        data["validated_at"] = ClockStamp(
            utc_ns=old.utc_ns + 6_000_000_000,
            monotonic_ns=old.monotonic_ns + 6_000_000_000,
        )
    elif mutation == "missing_fill_terminal":
        data["fill_pages"] = data["fill_pages"][:1]
    elif mutation == "changed_uid":
        old = data["config_after"]
        rows = decode(old.raw_body)["data"]
        rows[0]["uid"] = "730059"
        data["config_after"] = fixture.rewrite(old, rows)
    elif mutation == "changed_stop":
        old = data["protection_page"]
        rows = decode(old.raw_body)["data"]
        rows[0]["slTriggerPx"] = "98"
        data["protection_page"] = fixture.rewrite(old, rows)
    elif mutation == "not_found":
        data["order_page"] = fixture.rewrite(data["order_page"], [], code="51603")
    else:
        old = data["order_page"]
        rows = decode(old.raw_body)["data"]
        rows[0].update(state="partially_filled", accFillSz="0.5")
        data["order_page"] = fixture.rewrite(old, rows)
    result = facts.assess_live_postsubmit_facts_v1(intent, **data)
    assert result.outcome == "UNCERTAIN" and result.codes
    assert result.execution_authority is result.resolution_authority is False


def test_retained_original_utc_is_history_and_old_monotonic_is_never_reused():
    intent, data = history_scenario()
    journal = SimpleNamespace(
        intent=intent,
        intent_recorded_at=utc_from_ns(fixture.BASE),
        proofs=(
            canonical(
                {
                    "submission_started": {
                        "utc_ns": fixture.BASE + 1_000_000,
                        "monotonic_ns": 9_999_999_999_999_999,
                    }
                }
            ),
        ),
    )
    history = recovery._historical_times(journal)
    assert history.submission_started_utc == utc_from_ns(fixture.BASE + 1_000_000)
    data["history"] = history
    assert (
        facts.assess_live_postsubmit_facts_v1(intent, **data).outcome
        == "FULL_FILL_PROTECTED"
    )


@pytest.mark.parametrize(
    "change", ["missing", "after_expiry", "submission_before_recorded", "conflict"]
)
def test_historical_provenance_refuses_unknown_or_conflicting_source(change):
    intent = fixture.intent()
    recorded = utc_from_ns(fixture.BASE)
    proofs = ()
    if change == "missing":
        recorded = None
    elif change == "after_expiry":
        recorded = intent.deadline + timedelta(seconds=1)
    elif change == "submission_before_recorded":
        proofs = (canonical({"submission_started": fixture.stamp(-1).model_dump()}),)
    else:
        proofs = tuple(
            canonical({"submission_started": fixture.stamp(i).model_dump()})
            for i in (0, 1)
        )
    with pytest.raises(recovery.LiveRecoveryError):
        recovery._historical_times(
            SimpleNamespace(intent=intent, intent_recorded_at=recorded, proofs=proofs)
        )
