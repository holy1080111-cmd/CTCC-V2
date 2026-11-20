"""Pure synthetic Live facts: none of these fixtures owns a source or signer."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from app.domain.source_primitives import decode
from app.okx_live import reconciliation_facts_v1 as subject
from tests import native_live_postsubmit_fixtures_v1 as fixture


def _rows(page):
    return decode(page.raw_body)["data"]


@pytest.mark.parametrize(
    "zero,expected", ((False, "FULL_FILL_PROTECTED"), (True, "ZERO_FILL_FLAT"))
)
def test_only_complete_original_raw_facts_classify_without_any_authority(
    zero, expected
):
    value, data = fixture.scenario(zero=zero)
    result = subject.assess_live_postsubmit_facts_v1(value, **data)
    assert result.outcome == expected and result.codes == ()
    assert (
        result.execution_authority
        is result.resolution_authority
        is result.source_authenticity_verified
        is result.economic_qualification_verified
        is False
    )
    assert result.filled_contracts == Decimal(0 if zero else 1)
    assert result.entry_fees_usdt == Decimal(0 if zero else "-0.05")


@pytest.mark.parametrize(
    "mutation",
    (
        "partial",
        "missing_terminal",
        "wrong_cursor",
        "foreign_fill",
        "duplicate_trade",
        "fee_currency",
        "average",
        "price_bound",
        "missing_position",
        "unknown_pending",
        "missing_algo_family",
        "missing_protection",
        "wrong_stop",
        "last_trigger",
        "effective",
        "uncovered",
        "non_reduce",
        "wrong_uid",
        "hedge",
        "stale",
        "pre_submit",
        "future_fill",
        "unknown_number",
        "order_error",
        "missing_tick",
        "attachment_only",
    ),
)
def test_any_uncertain_changed_incomplete_or_unsupported_fact_retains_the_hold(
    mutation,
):
    value, data = fixture.scenario()
    if mutation == "partial":
        row = _rows(data["order_page"])[0]
        row.update(state="partially_filled", accFillSz="0.5")
        data["order_page"] = fixture.rewrite(data["order_page"], [row])
        fill = _rows(data["fill_pages"][0])[0]
        fill["fillSz"] = "0.5"
        data["fill_pages"] = (
            fixture.rewrite(data["fill_pages"][0], [fill]),
            *data["fill_pages"][1:],
        )
    elif mutation == "missing_terminal":
        data["fill_pages"] = data["fill_pages"][:1]
    elif mutation == "wrong_cursor":
        last = data["fill_pages"][-1]
        selector = replace(last.selector, after_bill_id="788")
        data["fill_pages"] = (data["fill_pages"][0], replace(last, selector=selector))
    elif mutation in ("foreign_fill", "fee_currency", "future_fill", "duplicate_trade"):
        fill = _rows(data["fill_pages"][0])[0]
        if mutation == "foreign_fill":
            fill["ordId"] = "124"
        elif mutation == "fee_currency":
            fill["feeCcy"] = "BTC"
        elif mutation == "future_fill":
            fill["ts"] = str(fixture.BASE // 1_000_000 + 199)
        else:
            fill.update(fillSz="0.5")
            second = dict(fill, billId="788")
            data["fill_pages"] = (
                fixture.rewrite(data["fill_pages"][0], [fill, second]),
                fixture.trade(
                    replace(data["fill_pages"][-1].selector, after_bill_id="788"),
                    [],
                    60,
                ),
            )
        if mutation != "duplicate_trade":
            data["fill_pages"] = (
                fixture.rewrite(data["fill_pages"][0], [fill]),
                *data["fill_pages"][1:],
            )
    elif mutation in ("average", "price_bound"):
        row = _rows(data["order_page"])[0]
        row["avgPx"] = "100.2"
        data["order_page"] = fixture.rewrite(data["order_page"], [row])
        if mutation == "price_bound":
            fill = _rows(data["fill_pages"][0])[0]
            fill["fillPx"] = "100.2"
            data["fill_pages"] = (
                fixture.rewrite(data["fill_pages"][0], [fill]),
                *data["fill_pages"][1:],
            )
    elif mutation in (
        "missing_position",
        "unknown_pending",
        "missing_algo_family",
        "unknown_number",
    ):
        pages = list(data["current_pages"])
        if mutation == "missing_position":
            pages[0] = fixture.rewrite(pages[0], [])
        elif mutation == "unknown_pending":
            pages[1] = fixture.current("orders_pending", [{"ordId": "999"}], 80)
            pages.insert(2, fixture.current("orders_pending", [], 86, after="999"))
        elif mutation == "missing_algo_family":
            pages = [page for page in pages if page.selector.stream != "algo_chase"]
        else:
            row = _rows(pages[0])[0]
            row["pos"] = False
            pages[0] = fixture.rewrite(pages[0], [row])
        data["current_pages"] = tuple(pages)
    elif mutation in ("missing_protection", "attachment_only"):
        data["protection_page"] = None
    elif mutation in (
        "wrong_stop",
        "last_trigger",
        "effective",
        "uncovered",
        "non_reduce",
    ):
        row = _rows(data["protection_page"])[0]
        row.update(
            **{
                "wrong_stop": {"slTriggerPx": "98"},
                "last_trigger": {"slTriggerPxType": "last"},
                "effective": {"state": "effective", "actualSz": "1"},
                "uncovered": {"sz": "0.5"},
                "non_reduce": {"reduceOnly": "false"},
            }[mutation]
        )
        data["protection_page"] = fixture.rewrite(data["protection_page"], [row])
        data["current_pages"] = tuple(
            fixture.rewrite(page, [row])
            if page.selector.stream == "algo_oco" and _rows(page)
            else page
            for page in data["current_pages"]
        )
    elif mutation == "wrong_uid":
        data["config_after"] = fixture.config(190, uid="730059")
    elif mutation == "hedge":
        data["config_after"] = fixture.config(190, posMode="long_short_mode")
    elif mutation == "stale":
        data["validated_at"] = fixture.stamp(6000)
    elif mutation == "pre_submit":
        data["submission_barrier"] = fixture.stamp(45)
    elif mutation == "order_error":
        data["order_page"] = fixture.rewrite(data["order_page"], [], code="51603")
    else:
        data["tick_size"] = None
    result = subject.assess_live_postsubmit_facts_v1(value, **data)
    assert result.outcome == "UNCERTAIN" and result.codes
    assert result.resolution_authority is result.execution_authority is False


def test_missing_source_is_unknown_not_a_zero_filled_empty_account():
    value, data = fixture.scenario(zero=True)
    data["current_pages"] = ()
    result = subject.assess_live_postsubmit_facts_v1(value, **data)
    assert result.outcome == "UNCERTAIN"
    assert result.filled_contracts is result.entry_fees_usdt is None


def test_caller_changed_page_authority_flag_refuses_even_with_original_hashes():
    value, data = fixture.scenario()
    object.__setattr__(data["order_page"], "source_authenticity_verified", True)
    assert subject.assess_live_postsubmit_facts_v1(value, **data).outcome == "UNCERTAIN"


def test_source_arithmetic_does_not_inherit_callers_decimal_rounding():
    value, data = fixture.scenario()
    fill = _rows(data["fill_pages"][0])[0]
    fill["fee"] = "-0.12345"
    data["fill_pages"] = (
        fixture.rewrite(data["fill_pages"][0], [fill]),
        *data["fill_pages"][1:],
    )
    with localcontext() as context:
        context.prec = 2
        result = subject.assess_live_postsubmit_facts_v1(value, **data)
    assert result.outcome == "FULL_FILL_PROTECTED"
    assert result.entry_fees_usdt == Decimal("-0.12345")


def test_opaque_lifecycle_constructor_and_saved_data_cannot_own_a_capture():
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    with pytest.raises(lifecycle.NativeLiveLifecycleError):
        lifecycle.OwnedLiveLifecycleV1()
    empty = lifecycle.OwnedLiveLifecycleV1(lifecycle._OWNER_KEY_V1)
    with pytest.raises(lifecycle.NativeLiveLifecycleError):
        lifecycle._record(empty)
