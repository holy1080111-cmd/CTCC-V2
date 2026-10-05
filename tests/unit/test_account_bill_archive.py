from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest

from app.trade_qualification import account_bill_archive as bills


def _json(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _zip_csv(text, *, name="bills.csv"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text.encode("utf-8"))
    return output.getvalue()


def _csv(rows):
    return "billId,ccy,type,subType,ts,balChg,source_extra\n" + "\n".join(rows) + "\n"


def test_quarter_bounds_include_2021_february_start_and_are_half_open():
    assert bills.quarter_bounds(2021, 1) == (
        datetime(2021, 2, 1, tzinfo=UTC),
        datetime(2021, 4, 1, tzinfo=UTC),
    )
    assert bills.quarter_bounds(2024, 2) == (
        datetime(2024, 4, 1, tzinfo=UTC),
        datetime(2024, 7, 1, tzinfo=UTC),
    )
    with pytest.raises(bills.BillArchiveError, match="bill_archive_quarter_invalid"):
        bills.quarter_bounds(True, 2)


def test_apply_false_is_generation_in_progress_not_empty_history():
    receipt = bills.parse_apply_response(
        _json(
            {
                "code": "0",
                "data": [{"result": "false", "ts": "1711929600000"}],
                "msg": "",
            }
        )
    )
    assert receipt.result == "false"
    assert receipt.earliest_status_check_at == datetime(2024, 4, 1, 2, tzinfo=UTC)
    assert receipt.permission == "read"
    assert receipt.order_write is False
    assert receipt.account_complete is False
    assert receipt.execution_authority is False


def test_apply_rejects_duplicate_json_keys_and_invalid_result_type():
    with pytest.raises(bills.BillArchiveError, match="bill_archive_duplicate_json_key"):
        bills.parse_apply_response(b'{"code":"0","code":"0","data":[],"msg":""}')
    with pytest.raises(
        bills.BillArchiveError, match="bill_archive_apply_response_invalid"
    ):
        bills.parse_apply_response(
            _json(
                {
                    "code": "0",
                    "data": [{"result": [], "ts": "1711929600000"}],
                    "msg": "",
                }
            )
        )


def test_status_hashes_temporary_download_url_without_returning_or_rendering_it():
    href = "https://archive.example/private/bills.zip?proof=fixture-value"
    receipt = bills.parse_status_response(
        _json(
            {
                "code": "0",
                "data": [
                    {"state": "finished", "ts": "1711929600000", "fileHref": href}
                ],
                "msg": "",
            }
        ),
        allowed_download_hosts=("archive.example",),
    )
    assert receipt.state == "finished"
    assert receipt.file_href_sha256 == hashlib.sha256(href.encode()).hexdigest()
    assert href not in repr(receipt)
    assert href not in repr(
        receipt.__dict__ if hasattr(receipt, "__dict__") else receipt
    )
    assert receipt.sensitive_href_retained is False
    assert receipt.account_complete is False
    assert receipt.execution_authority is False


@pytest.mark.parametrize(
    "href,hosts",
    [
        ("http://archive.example/bills.zip", ("archive.example",)),
        ("https://other.example/bills.zip", ("archive.example",)),
        ("https://user:pass@archive.example/bills.zip", ("archive.example",)),
    ],
)
def test_status_rejects_unpinned_or_unsafe_download_url(href, hosts):
    raw = _json(
        {
            "code": "0",
            "data": [{"state": "finished", "ts": "1711929600000", "fileHref": href}],
            "msg": "",
        }
    )
    with pytest.raises(
        bills.BillArchiveError, match="bill_archive_download_url_invalid"
    ):
        bills.parse_status_response(raw, allowed_download_hosts=hosts)


def test_ongoing_status_has_no_url_and_stays_incomplete():
    receipt = bills.parse_status_response(
        _json(
            {
                "code": "0",
                "data": [{"state": "ongoing", "ts": "1711929600000"}],
                "msg": "",
            }
        ),
        allowed_download_hosts=("archive.example",),
    )
    assert receipt.state == "ongoing"
    assert receipt.file_href_sha256 is None
    assert receipt.account_complete is False


def test_unknown_download_host_allows_state_only_but_rejects_finished_link():
    ongoing = bills.parse_status_response(
        _json(
            {
                "code": "0",
                "data": [{"state": "ongoing", "ts": "1711929600000"}],
                "msg": "",
            }
        ),
        allowed_download_hosts=(),
    )
    assert ongoing.state == "ongoing"
    assert ongoing.file_href_sha256 is None
    with pytest.raises(
        bills.BillArchiveError, match="bill_archive_download_url_invalid"
    ):
        bills.parse_status_response(
            _json(
                {
                    "code": "0",
                    "data": [
                        {
                            "state": "finished",
                            "ts": "1711929600000",
                            "fileHref": "https://archive.example/private/file.zip",
                        }
                    ],
                    "msg": "",
                }
            ),
            allowed_download_hosts=(),
        )


def test_csv_archive_preserves_unknown_fields_and_checks_descending_bill_ids():
    raw = _zip_csv(
        _csv(
            [
                "9002,USDT,2,1,1711929600001,-0.01,keep-me",
                "9001,USDT,2,2,1711929600000,0.02,keep-me-too",
            ]
        )
    )
    receipt = bills.parse_bill_archive_zip(raw, year=2024, quarter=2)
    assert receipt.row_count == 2
    assert receipt.first_bill_id == "9002"
    assert receipt.last_bill_id == "9001"
    assert dict(receipt.rows[0].fields)["source_extra"] == "keep-me"
    assert receipt.archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.source_authenticity_verified is False
    assert receipt.quarter_coverage_verified is False
    assert receipt.account_complete is False
    assert receipt.execution_authority is False
    assert "keep-me" not in repr(receipt)


@pytest.mark.parametrize(
    "rows,reason",
    [
        (
            [
                "9001,USDT,2,1,1711929600000,0.01,x",
                "9002,USDT,2,2,1711929600001,0.02,y",
            ],
            "bill_archive_csv_bill_id_order_invalid",
        ),
        (
            [
                "9001,USDT,2,1,1711929600000,0.01,x",
                "9001,USDT,2,2,1711929600001,0.02,y",
            ],
            "bill_archive_csv_bill_id_order_invalid",
        ),
        (["9001,USDT,2,1,1719792000000,0.01,x"], "bill_archive_csv_outside_quarter"),
    ],
)
def test_csv_archive_rejects_order_conflicts_and_out_of_quarter_rows(rows, reason):
    with pytest.raises(bills.BillArchiveError, match=reason):
        bills.parse_bill_archive_zip(_zip_csv(_csv(rows)), year=2024, quarter=2)


def test_csv_archive_rejects_empty_rows_missing_schema_and_unsafe_zip_member():
    with pytest.raises(bills.BillArchiveError, match="bill_archive_csv_empty"):
        bills.parse_bill_archive_zip(
            _zip_csv("billId,ccy,type,subType,ts,balChg,source_extra\n"),
            year=2024,
            quarter=2,
        )
    with pytest.raises(bills.BillArchiveError, match="bill_archive_csv_header_invalid"):
        bills.parse_bill_archive_zip(
            _zip_csv("billId,ccy,type,subType,ts\n9001,USDT,2,1,1711929600000\n"),
            year=2024,
            quarter=2,
        )
    with pytest.raises(bills.BillArchiveError, match="bill_archive_member_invalid"):
        bills.parse_bill_archive_zip(
            _zip_csv(_csv(["9001,USDT,2,1,1711929600000,0.01,x"]), name="../bills.csv"),
            year=2024,
            quarter=2,
        )
