"""Historical closure versus NEW old-order DATA; no source/cleanup owner."""

from dataclasses import replace

import pytest

from app.domain.source_primitives import canonical, decode
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- preserve the actual shared fixture registration.
)
from tests.unit.test_native_reconciliation_facts_v1 import data
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- preserve the actual shared fixture registration.
)


def pages(target, edit=None):
    selected = {
        "config_before",
        "order_before",
        "fills_recent",
        "fills_history",
        "order_after",
        "config_after",
    }
    result = []
    for role, page in data(target):
        if role not in selected:
            continue
        payload = decode(page.raw_body)
        if edit is not None:
            edit(role, payload["data"])
        result.append((role, replace(page, raw_body=canonical(payload))))
    return tuple(result)


def test_new_read_can_confirm_old_zero_without_asserting_present_account_flat(target):
    historical = facts.assess_native_reconciliation_v1(target, data(target))

    def changed_mode(role, rows):
        if role.startswith("config_"):
            rows[0]["posMode"] = (
                "long_short_mode" if rows[0]["posMode"] == "net_mode" else "net_mode"
            )

    receipt = decode(
        facts.assess_previously_closed_zero_v1(
            target,
            pages(target, changed_mode),
            closure_outcome_id="a" * 64,
            historical_assessment=historical,
        )
    )
    assert receipt["current_whole_account_flat_asserted"] is False
    assert (
        receipt["execution_authority"] is False
        and receipt["order_retry_authority"] is False
    )
    assert len(receipt["fresh_pages"]) == 6


@pytest.mark.parametrize("change", ("future", "identity", "nonzero", "changed_order"))
def test_new_read_cannot_suppress_actual_old_order_conflicts(target, change):
    historical = facts.assess_native_reconciliation_v1(target, data(target))

    def mutate(role, rows):
        if change == "future" and role.startswith("order_"):
            rows[0]["uTime"] = "9999999999999"
        elif change == "identity" and role.startswith("config_"):
            rows[0]["mainUid"] = "9988776655"
        elif change == "nonzero" and role.startswith("order_"):
            rows[0]["accFillSz"] = "0.1"
        elif change == "changed_order" and role == "order_after":
            rows[0]["ordId"] = "999999"

    with pytest.raises(facts.NativeReconciliationFactsError):
        facts.assess_previously_closed_zero_v1(
            target,
            pages(target, mutate),
            closure_outcome_id="a" * 64,
            historical_assessment=historical,
        )


def test_filled_protected_history_never_becomes_a_zero_closure(target):
    historical = facts.assess_native_reconciliation_v1(
        target, data(target, filled=True)
    )
    with pytest.raises(
        facts.NativeReconciliationFactsError, match="closed_data_required"
    ):
        facts.assess_previously_closed_zero_v1(
            target,
            pages(target),
            closure_outcome_id="a" * 64,
            historical_assessment=historical,
        )
