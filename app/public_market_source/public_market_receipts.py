"""Versioned public minute observations; integrity is not source authentication.

Only the owned collector can mint the process-local publication carrier. These
portable contracts deliberately have no execution or predictive authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Ns = Annotated[int, Field(ge=0, le=32_503_680_000_000_000_000)]
ORIGINS = ("https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com")
ENDPOINT = "/api/v5/market/history-candles"
TIME_ENDPOINT = "/api/v5/public/time"
MAX_RAW = 1024 * 1024
MINUTE_NS = 60_000_000_000
MAX_BATCH_NS = 60_000_000_000
MAX_CLOCK_DEVIATION_NS = 5_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class PublicReceiptError(ValueError):
    """Fixed local rejection codes, never raw transport or host diagnostics."""


def sha(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise PublicReceiptError("bytes_required")
    return hashlib.sha256(payload).hexdigest()


def _plain(value, depth=0):
    if depth > 24:
        raise PublicReceiptError("structure_limit")
    kind = type(value)
    if kind in (str, int, bool) or value is None:
        return
    if kind in (list, tuple):
        if len(value) > 4096:
            raise PublicReceiptError("structure_limit")
        for item in value:
            _plain(item, depth + 1)
        return
    if kind is dict:
        if len(value) > 128 or any(type(key) is not str for key in value):
            raise PublicReceiptError("structure_invalid")
        for item in value.values():
            _plain(item, depth + 1)
        return
    # Do not invoke arbitrary serializers, bytes.hex, Decimal, tzinfo or iterators.
    raise PublicReceiptError("plain_scalar_required")


def canonical(value) -> bytes:
    _plain(value)
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicReceiptError("duplicate_json_key")
        result[key] = value
    return result


def decode(raw: bytes, maximum=MAX_RAW):
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise PublicReceiptError("raw_size_invalid")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        _plain(result)
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PublicReceiptError("raw_json_invalid") from exc


class ReceiptContract(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    @model_validator(mode="before")
    @classmethod
    def plain_only(cls, value, info):
        _plain(value)
        if type(value) is dict:
            for name in ("predictive_oos_eligible", "execution_authority"):
                if name in value and (
                    type(value[name]) is not bool or value[name] is not False
                ):
                    raise PublicReceiptError("authority_forbidden")
            if "runtime_consumers" in value and (
                type(value["runtime_consumers"]) is not int
                or value["runtime_consumers"] != 0
            ):
                raise PublicReceiptError("authority_forbidden")
            if info.mode == "json":
                value = dict(value)
                for name, field in cls.model_fields.items():
                    if name in value:
                        value[name] = _json_tuple(value[name], field.annotation)
        return value

    def canonical_bytes(self):
        return canonical(self.model_dump(mode="json"))

    def canonical_sha256(self):
        return sha(self.canonical_bytes())


def _json_tuple(value, annotation):
    """Explicit JSON-array decoding for tuple fields, not source row repair."""
    if get_origin(annotation) is tuple and type(value) is list:
        args = get_args(annotation)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_json_tuple(item, args[0]) for item in value)
        if len(value) == len(args):
            return tuple(
                _json_tuple(item, item_type)
                for item, item_type in zip(value, args, strict=True)
            )
    return value


def checked(value, cls):
    if type(value) is not cls:
        raise PublicReceiptError("exact_contract_required")
    # Walk native instance fields before pydantic can invoke user serialization.
    fields = object.__getattribute__(value, "__dict__")
    _plain(fields)
    return cls.model_validate(dict(fields))


class PublicMinuteCapturePlanV1(ReceiptContract):
    schema_version: Literal["ctcc.public.minute_plan.v1"] = "ctcc.public.minute_plan.v1"
    origin: Literal[
        "https://openapi.okx.com", "https://us.okx.com", "https://eea.okx.com"
    ]
    instrument_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USDT-SWAP$")
    instrument_type: Literal["SWAP"] = "SWAP"
    environment: Literal["public_production"] = "public_production"
    interval_seconds: Literal[60] = 60
    start_ns: Ns
    end_ns: Ns
    expected_rows: int = Field(ge=1, le=3000)
    created_ns: Ns
    page_size: int = Field(ge=1, le=100, default=100)
    max_pages: int = Field(ge=1, le=30, default=30)
    max_response_bytes: int = Field(ge=1024, le=MAX_RAW, default=MAX_RAW)
    clock_policy: Literal["ctcc.windows_w32time_causal.v1"] = (
        "ctcc.windows_w32time_causal.v1"
    )
    source_parser: Literal["okx.history_candles.nine_strings.v1"] = (
        "okx.history_candles.nine_strings.v1"
    )
    volume_column: Literal["vol"] = "vol"
    volume_unit: Literal["contracts"] = "contracts"
    revision_policy: Literal["provider_correctable"] = "provider_correctable"
    current_claim: Literal["computational"] = "computational"
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def coordinates(self):
        if self.start_ns % MINUTE_NS or self.end_ns % MINUTE_NS:
            raise PublicReceiptError("minute_boundary_invalid")
        if self.end_ns - self.start_ns != self.expected_rows * MINUTE_NS:
            raise PublicReceiptError("minute_window_invalid")
        if self.max_pages * self.page_size < self.expected_rows:
            raise PublicReceiptError("page_budget_insufficient")
        return self


def utc_from_ns(value: int) -> datetime:
    if type(value) is not int or not 0 <= value <= 32_503_680_000_000_000_000:
        raise PublicReceiptError("timestamp_invalid")
    # Existing datetime contracts store microseconds. Round UP conservatively.
    return EPOCH + timedelta(microseconds=(value + 999) // 1000)


def request_query(plan: PublicMinuteCapturePlanV1, after: int, remaining: int):
    return (
        ("instId", plan.instrument_id),
        ("bar", "1m"),
        ("after", str(after // 1_000_000)),
        ("limit", str(min(plan.page_size, remaining))),
    )


def parsed_rows(raw: bytes):
    body = decode(raw)
    if (
        type(body) is not dict
        or set(body) != {"code", "msg", "data"}
        or body["code"] != "0"
        or body["msg"] != ""
    ):
        raise PublicReceiptError("source_response_invalid")
    rows = body["data"]
    if type(rows) is not list or not 1 <= len(rows) <= 100:
        raise PublicReceiptError("source_rows_invalid")
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 9
            or any(type(x) is not str for x in row)
        ):
            raise PublicReceiptError("source_columns_invalid")
        if (
            re.fullmatch(r"[1-9][0-9]{12}", row[0]) is None
            or int(row[0]) % 60000
            or row[8] != "1"
        ):
            raise PublicReceiptError("source_minute_unconfirmed")
        for text in row[1:8]:
            if re.fullmatch(r"(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?", text) is None:
                raise PublicReceiptError("source_number_invalid")
        o, h, low, c = map(Decimal, row[1:5])
        if min(o, h, low, c) <= 0 or not low <= min(o, c) <= max(o, c) <= h:
            raise PublicReceiptError("source_ohlc_invalid")
    return tuple(tuple(row) for row in rows), canonical(body)


def row_identity(plan, row):
    return sha(
        canonical(
            {
                "source_parser": plan.source_parser,
                "origin": plan.origin,
                "endpoint": ENDPOINT,
                "instrument_type": plan.instrument_type,
                "instrument_id": plan.instrument_id,
                "interval_seconds": 60,
                "opening_ms": row[0],
            }
        )
    )


def row_content_sha(plan, row):
    return sha(canonical({"row_identity": row_identity(plan, row), "row": row}))


class ClockStamp(ReceiptContract):
    utc_ns: Ns
    monotonic_ns: Ns


def validate_stamps(stamps):
    if type(stamps) not in (list, tuple) or not 2 <= len(stamps) <= 512:
        raise PublicReceiptError("clock_sequence_invalid")
    values = [ClockStamp.model_validate(item) for item in stamps]
    first = values[0]
    previous = first
    for current in values[1:]:
        if (
            current.utc_ns < previous.utc_ns
            or current.monotonic_ns < previous.monotonic_ns
        ):
            raise PublicReceiptError("clock_reversed")
        if (
            abs(
                (current.utc_ns - first.utc_ns)
                - (current.monotonic_ns - first.monotonic_ns)
            )
            > MAX_CLOCK_DEVIATION_NS
        ):
            raise PublicReceiptError("clock_jump")
        if current.monotonic_ns - first.monotonic_ns > MAX_BATCH_NS:
            raise PublicReceiptError("clock_lease_expired")
        previous = current
    return tuple(values)


class PublicRawReceiptV1(ReceiptContract):
    schema_version: Literal["ctcc.public.raw_receipt.v1"] = "ctcc.public.raw_receipt.v1"
    plan_sha256: Sha
    endpoint: Literal["/api/v5/market/history-candles", "/api/v5/public/time"]
    query: tuple[tuple[str, str], ...]
    page_index: int = Field(ge=0, le=29)
    previous_page_sha256: Sha | None
    cursor_type: Literal["candle_open_ms"] | None
    first_row_identity: Sha | None
    last_row_identity: Sha | None
    timestamp_semantics: Literal["confirmed_candle_open", "server_response_time"]
    body_sha256: Sha
    body_size: int = Field(ge=1, le=MAX_RAW)
    canonical_body_sha256: Sha
    request_start: dict
    headers_received: dict
    body_complete: dict
    validation_complete: dict
    tls_peer_sha256: Sha
    tls_hostname: str = Field(pattern=r"^(?:openapi|us|eea)\.okx\.com$")
    tls_version: Literal["TLSv1.2", "TLSv1.3"]
    response_headers: tuple[tuple[str, str], ...]
    transport_origin: Literal["owned_native_tls", "synthetic_test"]

    @model_validator(mode="after")
    def times(self):
        validate_stamps(
            (
                self.request_start,
                self.headers_received,
                self.body_complete,
                self.validation_complete,
            )
        )
        if any(
            name not in {"content-type", "content-length", "content-encoding", "date"}
            for name, _ in self.response_headers
        ):
            raise PublicReceiptError("private_header_rejected")
        return self


def verify_raw(plan, receipt, raw):
    plan = checked(plan, PublicMinuteCapturePlanV1)
    receipt = checked(receipt, PublicRawReceiptV1)
    if type(raw) is not bytes:
        raise PublicReceiptError("bytes_required")
    if (
        receipt.plan_sha256 != plan.canonical_sha256()
        or receipt.tls_hostname != plan.origin.removeprefix("https://")
    ):
        raise PublicReceiptError("source_plan_mismatch")
    if (
        len(raw) > plan.max_response_bytes
        or sha(raw) != receipt.body_sha256
        or len(raw) != receipt.body_size
    ):
        raise PublicReceiptError("raw_identity_mismatch")
    parsed = decode(raw)
    if sha(canonical(parsed)) != receipt.canonical_body_sha256:
        raise PublicReceiptError("canonical_identity_mismatch")
    if receipt.endpoint == TIME_ENDPOINT:
        if (
            receipt.query
            or receipt.cursor_type is not None
            or receipt.first_row_identity is not None
            or receipt.last_row_identity is not None
            or receipt.timestamp_semantics != "server_response_time"
            or receipt.page_index != 0
            or receipt.previous_page_sha256 is not None
            or type(parsed) is not dict
            or set(parsed) != {"code", "msg", "data"}
            or parsed["code"] != "0"
            or parsed["msg"] != ""
            or type(parsed["data"]) is not list
            or len(parsed["data"]) != 1
            or type(parsed["data"][0]) is not dict
            or set(parsed["data"][0]) != {"ts"}
        ):
            raise PublicReceiptError("time_response_invalid")
        value = parsed["data"][0]["ts"]
        if type(value) is not str or re.fullmatch(r"[1-9][0-9]{12}", value) is None:
            raise PublicReceiptError("time_response_invalid")
        server_ns = int(value) * 1_000_000
        if (
            not receipt.request_start["utc_ns"]
            <= server_ns
            <= receipt.body_complete["utc_ns"]
        ):
            raise PublicReceiptError("exchange_time_outside_request")
        return server_ns
    rows, _ = parsed_rows(raw)
    if (
        receipt.cursor_type != "candle_open_ms"
        or receipt.timestamp_semantics != "confirmed_candle_open"
        or receipt.first_row_identity != row_identity(plan, rows[0])
        or receipt.last_row_identity != row_identity(plan, rows[-1])
    ):
        raise PublicReceiptError("source_identity_semantics_mismatch")
    if any(
        int(row[0]) * 1_000_000 + MINUTE_NS > receipt.body_complete["utc_ns"]
        for row in rows
    ):
        raise PublicReceiptError("future_candle")
    return rows


class MeasuredPublicMinuteReceiptV1(ReceiptContract):
    schema_version: Literal["ctcc.public.measured_minutes.v1"] = (
        "ctcc.public.measured_minutes.v1"
    )
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    attempt_sha256: Sha | None = None
    plan: dict
    plan_sha256: Sha
    os_clock_before: dict
    os_clock_after: dict
    time_before: dict
    time_after: dict
    pages: tuple[dict, ...] = Field(min_length=1, max_length=30)
    raw_files: tuple[tuple[str, str], ...]
    rows: tuple[dict, ...] = Field(min_length=1, max_length=3000)
    validation_complete: dict
    transport_origin: Literal["owned_native_tls", "synthetic_test"]
    current_claim: Literal["computational"] = "computational"
    predictive_oos_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def safe_inventory(self):
        names = (
            "time-before.raw",
            *(f"page-{i:03d}.raw" for i in range(len(self.pages))),
            "time-after.raw",
        )
        if tuple(name for name, _ in self.raw_files) != names:
            raise PublicReceiptError("raw_file_inventory_mismatch")
        return self
