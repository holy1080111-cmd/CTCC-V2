"""Documented REST fill exceptions over synthetic bytes, never account authority."""

import json

import pytest

from app.trade_qualification import account_capture as capture
from tests.unit import test_qualification_account_capture as old
from tests.unit import test_qualification_account_v4 as v4
from tests.unit.test_qualification_account_runtime import regional


def observation(changes, *, stream="fills_history", selected=None):
    return old.stream_observation(
        stream, [old.row(stream, fillPnl="-1", **changes)], selected=selected
    )


@pytest.mark.parametrize("selected", [old.plan(), regional(), v4.plan()])
@pytest.mark.parametrize(
    "subtype", [str(n) for n in (*range(100, 108), *range(125, 129))]
)
def test_documented_negative_trade_id_retained_without_changing_bill_identity(
    selected, subtype
):
    item = observation(
        {"tradeId": "-700", "subType": subtype},
        stream="fills_recent",
        selected=selected,
    )
    source = json.loads(item.rows[0].canonical_json)
    assert source["tradeId"] == "-700"
    assert source["ordId"] == "800"
    assert item.rows[0].row_id == source["billId"] == "900"
    assert item.rows[0].missing_fields == ()


@pytest.mark.parametrize("subtype", [str(n) for n in range(204, 210)])
@pytest.mark.parametrize("stream", ["fills_recent", "fills_history"])
def test_block_order_id_is_explicit_empty_not_missing_or_invented(subtype, stream):
    item = observation({"ordId": "", "subType": subtype}, stream=stream)
    source = json.loads(item.rows[0].canonical_json)
    assert source["ordId"] == ""
    assert "blockTdId" not in source
    assert source["tradeId"] == "700"
    assert item.rows[0].row_id == "900"


@pytest.mark.parametrize("kind", v4.PRODUCTS)
@pytest.mark.parametrize(
    "changes", [{"tradeId": "-700", "subType": "100"}, {"ordId": "", "subType": "206"}]
)
def test_product_scoped_archive_retains_exceptional_row(kind, changes):
    stream = f"fills_history_{kind.lower()}"
    row = v4.product_row("fills_history", kind, fillPnl="-1", **changes)
    item = old.stream_observation(stream, [row], selected=v4.plan())
    assert json.loads(item.rows[0].canonical_json) == row
    assert item.rows[0].row_id == row["billId"]


@pytest.mark.parametrize(
    "changes",
    [
        {"tradeId": "-700", "subType": "1"},
        {"tradeId": "-700", "subType": "110"},
        {"tradeId": "-700", "subType": "111"},
        {"tradeId": "-700", "subType": "206"},
        {"tradeId": "-700", "subType": "unknown"},
        {"tradeId": "-700", "subType": None},
        {"tradeId": "-700", "subType": 100},
        {"ordId": "", "subType": "100"},
        {"ordId": "", "subType": "1"},
        {"ordId": "", "subType": None},
        {"ordId": "", "subType": 206},
        {"ordId": "", "tradeId": "-700", "subType": "206"},
    ],
)
def test_unknown_or_conflicting_exception_context_is_rejected(changes):
    with pytest.raises(capture.AccountCaptureError):
        observation(changes)


@pytest.mark.parametrize("field,subtype", [("tradeId", "100"), ("ordId", "206")])
@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        -7,
        True,
        " ",
        " 700",
        "700 ",
        "0",
        "-0",
        "+7",
        "-07",
        "--7",
        "-７",
        "7\n",
        "-" + "1" * 41,
    ],
)
def test_exception_does_not_relax_identifier_grammar(field, subtype, value):
    with pytest.raises(capture.AccountCaptureError):
        observation({field: value, "subType": subtype})


@pytest.mark.parametrize("field", ["tradeId", "ordId", "subType"])
def test_absent_field_is_not_explicit_block_empty(field):
    row = old.row("fills_history", ordId="", subType="206", fillPnl="-1")
    del row[field]
    with pytest.raises(capture.AccountCaptureError):
        old.stream_observation("fills_history", [row])


def test_negative_trade_id_keeps_existing_magnitude_bound():
    value = "-" + "9" * 40
    item = observation({"tradeId": value, "subType": "125"})
    assert json.loads(item.rows[0].canonical_json)["tradeId"] == value


@pytest.mark.parametrize("bill_id", ["-900", "", "0"])
def test_exception_never_changes_positive_bill_cursor_contract(bill_id):
    with pytest.raises(capture.AccountCaptureError):
        observation({"tradeId": "-700", "subType": "100", "billId": bill_id})


def test_exception_packet_replays_but_account_and_execution_remain_incomplete():
    row = old.row("fills_history", tradeId="-700", subType="100", fillPnl="-1")
    selected, items = old.records(pages={"fills_history": [[row], []]})
    packet = old.verify(selected, items)
    archive = [
        item for item in packet.observations if item.request.stream == "fills_history"
    ]
    assert dict(archive[1].request.parameters)["after"] == row["billId"]
    assert archive[1].previous_page_sha256 == archive[0].receipt_sha256
    assert packet.account_complete is False
    assert packet.execution_authority is False
    assert packet.source_authenticity_verified is False
    assert packet.incomplete_reasons
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=capture.plan_sha256(selected)
    )
    replay = capture.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=frozen.sha256,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    assert replay == packet
