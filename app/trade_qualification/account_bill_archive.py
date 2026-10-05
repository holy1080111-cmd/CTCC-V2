"""Offline validation primitives for OKX's quarterly since-2021 bills archive.

This module performs no network, credential, storage, or trading operations. It
validates supplied response/archive bytes and deliberately cannot authenticate
an account, establish complete history, or authorize execution.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

MAX_CONTROL_BYTES = 131072
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_CSV_BYTES = 256 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_COLUMNS = 128
MAX_FIELD_CHARS = 131072
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_MS = re.compile(r"[1-9][0-9]{11,14}\Z")
_CURRENCY = re.compile(r"[A-Z0-9]{1,20}\Z")
_REQUIRED_COLUMNS = frozenset({"billId", "ccy", "type", "subType", "ts", "balChg"})


class BillArchiveError(ValueError):
    """Stable local rejection reason; never carries remote text or account data."""


def _fail(code: str):
    raise BillArchiveError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def quarter_bounds(year: int, quarter: int) -> tuple[datetime, datetime]:
    """Return calendar half-open UTC bounds; OKX's Q2 example remains disputed."""
    if (
        type(year) is not int
        or not 2021 <= year <= 9998
        or type(quarter) is not int
        or quarter not in {1, 2, 3, 4}
    ):
        _fail("bill_archive_quarter_invalid")
    start_month = (quarter - 1) * 3 + 1
    start_day = 1
    if year == 2021 and quarter == 1:
        start_month, start_day = 2, 1
    end_month = quarter * 3 + 1
    end_year = year
    if end_month > 12:
        end_month -= 12
        end_year += 1
    return (
        datetime(year, start_month, start_day, tzinfo=UTC),
        datetime(end_year, end_month, 1, tzinfo=UTC),
    )


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("bill_archive_duplicate_json_key")
        result[key] = value
    return result


def _decode_control(raw: bytes):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CONTROL_BYTES:
        _fail("bill_archive_control_bytes_invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except BillArchiveError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("bill_archive_control_json_invalid")
    if type(value) is not dict:
        _fail("bill_archive_control_shape_invalid")
    return value


def _server_time_ms(value) -> datetime:
    if type(value) is not str or _MS.fullmatch(value) is None:
        _fail("bill_archive_server_time_invalid")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(value))
    except (OverflowError, ValueError):
        _fail("bill_archive_server_time_invalid")


@dataclass(frozen=True, slots=True)
class BillArchiveApplyReceipt:
    result: str
    first_server_receipt_at: datetime
    body_sha256: str
    canonical_sha256: str
    earliest_status_check_at: datetime | None
    permission: str = "read"
    order_write: bool = False
    account_complete: bool = False
    source_authenticity_verified: bool = False
    execution_authority: bool = False


def parse_apply_response(raw: bytes) -> BillArchiveApplyReceipt:
    """Replay the response to POST apply; ``false`` means generating, not empty."""
    value = _decode_control(raw)
    if set(value) != {"code", "data", "msg"} or value["code"] != "0":
        _fail("bill_archive_apply_response_invalid")
    if type(value["msg"]) is not str or type(value["data"]) is not list:
        _fail("bill_archive_apply_response_invalid")
    if len(value["data"]) != 1 or type(value["data"][0]) is not dict:
        _fail("bill_archive_apply_response_invalid")
    row = value["data"][0]
    if (
        set(row) != {"result", "ts"}
        or type(row["result"]) is not str
        or row["result"] not in {"true", "false"}
    ):
        _fail("bill_archive_apply_response_invalid")
    received_at = _server_time_ms(row["ts"])
    return BillArchiveApplyReceipt(
        result=row["result"],
        first_server_receipt_at=received_at,
        body_sha256=_sha(raw),
        canonical_sha256=_sha(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        ),
        earliest_status_check_at=(
            received_at + timedelta(hours=2)
            if row["result"] == "false"
            else received_at
        ),
    )


@dataclass(frozen=True, slots=True)
class BillArchiveStatusReceipt:
    state: str
    first_server_receipt_at: datetime
    file_href_sha256: str | None
    body_sha256: str
    canonical_sha256: str
    sensitive_href_retained: bool = False
    account_complete: bool = False
    source_authenticity_verified: bool = False
    execution_authority: bool = False


def parse_status_response(
    raw: bytes, *, allowed_download_hosts: tuple[str, ...]
) -> BillArchiveStatusReceipt:
    """Hash, but never return or persist, the temporary signed download URL."""
    if (
        type(allowed_download_hosts) is not tuple
        or any(
            type(host) is not str
            or not host
            or host != host.lower()
            or "/" in host
            or ":" in host
            for host in allowed_download_hosts
        )
        or len(set(allowed_download_hosts)) != len(allowed_download_hosts)
    ):
        _fail("bill_archive_download_host_pin_invalid")
    value = _decode_control(raw)
    if set(value) != {"code", "data", "msg"} or value["code"] != "0":
        _fail("bill_archive_status_response_invalid")
    if type(value["msg"]) is not str or type(value["data"]) is not list:
        _fail("bill_archive_status_response_invalid")
    if len(value["data"]) != 1 or type(value["data"][0]) is not dict:
        _fail("bill_archive_status_response_invalid")
    row = value["data"][0]
    if not {"state", "ts"}.issubset(row) or set(row) - {"state", "ts", "fileHref"}:
        _fail("bill_archive_status_response_invalid")
    if type(row["state"]) is not str or row["state"] not in {
        "finished",
        "ongoing",
        "failed",
    }:
        _fail("bill_archive_state_invalid")
    received_at = _server_time_ms(row["ts"])
    href_digest = None
    href = row.get("fileHref")
    if row["state"] == "finished":
        if type(href) is not str or not href:
            _fail("bill_archive_finished_link_missing")
        try:
            parsed = urlsplit(href)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            _fail("bill_archive_download_url_invalid")
        if (
            parsed.scheme != "https"
            or host not in allowed_download_hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
            or not parsed.path
            or any(ord(character) <= 32 or ord(character) == 127 for character in href)
        ):
            _fail("bill_archive_download_url_invalid")
        href_digest = _sha(href.encode("utf-8"))
    elif href is not None and href != "":
        _fail("bill_archive_unfinished_link_invalid")
    return BillArchiveStatusReceipt(
        state=row["state"],
        first_server_receipt_at=received_at,
        file_href_sha256=href_digest,
        body_sha256=_sha(raw),
        canonical_sha256=_sha(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        ),
    )


@dataclass(frozen=True, slots=True, repr=False)
class BillArchiveRow:
    """Exact CSV strings; unrecognized source columns remain preserved."""

    fields: tuple[tuple[str, str], ...]
    bill_id: str
    currency: str
    balance_change: str
    balance_update_at: datetime

    def __repr__(self):
        return "<BillArchiveRow private>"


@dataclass(frozen=True, slots=True, repr=False)
class BillArchiveCsvReceipt:
    year: int
    quarter: int
    archive_sha256: str
    csv_sha256: str
    rowset_sha256: str
    row_count: int
    first_bill_id: str
    last_bill_id: str
    rows: tuple[BillArchiveRow, ...] = field(repr=False)
    interval_semantics: str = "calendar_quarter_checked_against_rows"
    source_authenticity_verified: bool = False
    quarter_coverage_verified: bool = False
    account_complete: bool = False
    execution_authority: bool = False

    def __repr__(self):
        return "<BillArchiveCsvReceipt incomplete-private-data>"


def _read_zip_csv(raw_archive: bytes) -> bytes:
    if type(raw_archive) is not bytes or not 1 <= len(raw_archive) <= MAX_ARCHIVE_BYTES:
        _fail("bill_archive_size_invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(raw_archive), "r") as archive:
            members = archive.infolist()
            if len(members) != 1:
                _fail("bill_archive_member_count_invalid")
            member = members[0]
            mode = member.external_attr >> 16
            if (
                member.filename != member.filename.rsplit("/", 1)[-1]
                or "/" in member.filename
                or "\\" in member.filename
                or member.filename in {"", ".", ".."}
                or not member.filename.lower().endswith(".csv")
                or stat.S_ISLNK(mode)
                or member.flag_bits & 1
                or member.compress_type
                not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or member.file_size < 1
                or member.file_size > MAX_CSV_BYTES
                or member.compress_size < 1
                or member.file_size > max(member.compress_size * 200, 1024 * 1024)
            ):
                _fail("bill_archive_member_invalid")
            result = bytearray()
            with archive.open(member, "r") as stream:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    if len(result) + len(chunk) > MAX_CSV_BYTES:
                        _fail("bill_archive_csv_size_invalid")
                    result.extend(chunk)
            if len(result) != member.file_size:
                _fail("bill_archive_csv_size_mismatch")
            return bytes(result)
    except BillArchiveError:
        raise
    except (
        OSError,
        RuntimeError,
        EOFError,
        OverflowError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ):
        _fail("bill_archive_zip_invalid")


def parse_bill_archive_zip(
    raw_archive: bytes, *, year: int, quarter: int
) -> BillArchiveCsvReceipt:
    """Validate archive integrity, identity order, required fields and UTC window.

    A passing parse is an incomplete evidence receipt. It never claims OKX source
    authenticity, quarter completeness, a complete account, or trading authority.
    """
    start, end = quarter_bounds(year, quarter)
    csv_bytes = _read_zip_csv(raw_archive)
    try:
        text = io.TextIOWrapper(
            io.BytesIO(csv_bytes), encoding="utf-8-sig", newline="", errors="strict"
        )
        reader = csv.reader(text, strict=True)
        headers = next(reader)
        if (
            not headers
            or len(headers) > MAX_COLUMNS
            or any(
                type(name) is not str or not name or len(name) > 96 for name in headers
            )
            or len(headers) != len(set(headers))
            or not _REQUIRED_COLUMNS.issubset(headers)
        ):
            _fail("bill_archive_csv_header_invalid")
        indexes = {name: headers.index(name) for name in _REQUIRED_COLUMNS}
        rows = []
        previous_id = None
        for values in reader:
            if not values or len(values) != len(headers) or len(rows) >= MAX_ROWS:
                _fail("bill_archive_csv_row_invalid")
            if any(
                type(value) is not str or len(value) > MAX_FIELD_CHARS
                for value in values
            ):
                _fail("bill_archive_csv_field_invalid")
            bill_id = values[indexes["billId"]]
            currency = values[indexes["ccy"]]
            balance_change = values[indexes["balChg"]]
            raw_time = values[indexes["ts"]]
            if (
                _ID.fullmatch(bill_id) is None
                or _CURRENCY.fullmatch(currency) is None
                or not values[indexes["type"]]
                or not values[indexes["subType"]]
                or _MS.fullmatch(raw_time) is None
                or not balance_change
            ):
                _fail("bill_archive_csv_required_field_invalid")
            try:
                amount = Decimal(balance_change)
            except (InvalidOperation, ValueError):
                _fail("bill_archive_csv_balance_change_invalid")
            parts = amount.as_tuple()
            if not amount.is_finite() or parts.exponent < -40 or len(parts.digits) > 96:
                _fail("bill_archive_csv_balance_change_invalid")
            try:
                updated_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                    milliseconds=int(raw_time)
                )
            except (OverflowError, ValueError):
                _fail("bill_archive_csv_time_invalid")
            if not start <= updated_at < end:
                _fail("bill_archive_csv_outside_quarter")
            numeric_id = int(bill_id)
            if previous_id is not None and numeric_id >= previous_id:
                _fail("bill_archive_csv_bill_id_order_invalid")
            previous_id = numeric_id
            rows.append(
                BillArchiveRow(
                    fields=tuple(zip(headers, values, strict=True)),
                    bill_id=bill_id,
                    currency=currency,
                    balance_change=balance_change,
                    balance_update_at=updated_at,
                )
            )
    except BillArchiveError:
        raise
    except (UnicodeError, csv.Error, StopIteration, ValueError, OverflowError):
        _fail("bill_archive_csv_invalid")
    if not rows:
        _fail("bill_archive_csv_empty")
    canonical_rows = json.dumps(
        [[list(field) for field in row.fields] for row in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return BillArchiveCsvReceipt(
        year=year,
        quarter=quarter,
        archive_sha256=_sha(raw_archive),
        csv_sha256=_sha(csv_bytes),
        rowset_sha256=_sha(canonical_rows),
        row_count=len(rows),
        first_bill_id=rows[0].bill_id,
        last_bill_id=rows[-1].bill_id,
        rows=tuple(rows),
    )
