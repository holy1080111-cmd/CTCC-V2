"""Pure canonical bytes and clock primitives shared by trusted source domains.

This module has no exchange, account, storage, network, or execution dependency.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_RAW = 1024 * 1024
MAX_TIMESTAMP_NS = 32_503_680_000_000_000_000
MAX_CLOCK_DEVIATION_NS = 5_000_000
MAX_CLOCK_BATCH_NS = 60_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
Ns = Annotated[int, Field(ge=0, le=MAX_TIMESTAMP_NS)]


class PublicReceiptError(ValueError):
    """Fixed source-evidence rejection codes, never raw diagnostics."""


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


def utc_from_ns(value: int) -> datetime:
    if type(value) is not int or not 0 <= value <= MAX_TIMESTAMP_NS:
        raise PublicReceiptError("timestamp_invalid")
    # Existing datetime contracts store microseconds. Round up conservatively.
    return EPOCH + timedelta(microseconds=(value + 999) // 1000)


class ClockStamp(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    utc_ns: Ns
    monotonic_ns: Ns

    @model_validator(mode="before")
    @classmethod
    def plain_clock_stamp(cls, value):
        _plain(value)
        if type(value) is dict and any(
            name in value
            and (type(value[name]) is not bool or value[name] is not False)
            for name in ("predictive_oos_eligible", "execution_authority")
        ):
            raise PublicReceiptError("authority_forbidden")
        return value


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
        if current.monotonic_ns - first.monotonic_ns > MAX_CLOCK_BATCH_NS:
            raise PublicReceiptError("clock_lease_expired")
        previous = current
    return tuple(values)
