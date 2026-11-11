"""Isolated Linux DevVal post-read clock replay draft, without trading authority."""

from __future__ import annotations

import base64
import re
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType

from app.domain import linux_native_clock_v1 as linux_clock
from app.domain import source_primitives
from app.domain.source_primitives import (
    MAX_TIMESTAMP_NS,
    PublicReceiptError,
    canonical,
    decode,
    sha,
)

SCHEMA = "ctcc.mie.linux_post_read_clock_boundary.v1"
QUANTIZATION = "ctcc.pg17.11.linux.realtime_floor_microsecond.v1"
PG_QUANTUM_NS = 1000
MAX_RECEIPT_BYTES = 192 * 1024
LINUX_CLOCK_SOURCE_SHA256 = (
    "9af5cf49eb89e359def0baf1b29b9f286c229fc1e96daa4fab848a8db3438ad7"
)
SOURCE_PRIMITIVES_SHA256 = (
    "660a18a2bab787297c370d5316fa08cbf0ecf01273ea95ed990b949c4953420d"
)
NATIVE_ACQUISITION_ADMITTED = False

# Only a separately reviewed exact Linux PostgreSQL binary and physical timing
# measurement may add an entry. The MSVC or hermetic test result is no profile.
_REVIEWED_PG_PROFILES = MappingProxyType({})
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_IDENTIFIER = re.compile(r"[a-zA-Z0-9._-]{1,128}\Z")
_XID = re.compile(r"[1-9][0-9]{0,19}\Z")
_DOMAIN_FIELDS = (
    "boot_id_sha256",
    "kernel_release",
    "machine",
    "time_namespace",
    "namespace_offsets_sha256",
    "timezone_id",
    "utc_offset_minutes",
)
_NON_CLAIMS = {
    "database_boot_identity_attested",
    "database_binary_attested",
    "durable_role_custody_verified",
    "execution_authority",
    "future_decision_authority",
    "historical_availability_verified",
    "independent_absolute_utc_verified",
    "predictive_oos_eligible",
    "same_domain_future_availability_eligible",
    "upstream_freshness_verified",
}


class LinuxPostReadBoundaryError(PublicReceiptError):
    """Fixed replay refusals, with no raw DB or private clock details."""


def _require(ok, code):
    if not ok:
        raise LinuxPostReadBoundaryError(code) from None


def _keys(value, keys):
    _require(
        type(value) is dict and set(value) == keys, "linux_post_read_shape_invalid"
    )


def _hash(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _require_exact_clock_source():
    for module, expected in (
        (linux_clock, LINUX_CLOCK_SOURCE_SHA256),
        (source_primitives, SOURCE_PRIMITIVES_SHA256),
    ):
        source = Path(module.__file__)
        _require(
            source.is_absolute()
            and source.resolve(strict=True) == source
            and source.is_file()
            and source.stat().st_size <= 131_072
            and sha(source.read_bytes()) == expected,
            "linux_post_read_clock_source_changed",
        )


def capture_local_health_bytes():
    """Fixed owned Linux acquisition, disabled in this review draft."""
    _require(
        NATIVE_ACQUISITION_ADMITTED is True, "linux_post_read_acquisition_disabled"
    )
    _require_exact_clock_source()
    return linux_clock.consume_owned_linux_clock_v1(
        linux_clock.observe_linux_clock_v1()
    )


def _health(record):
    _keys(record, {"raw_base64", "raw_sha256"})
    encoded = record["raw_base64"]
    _require(
        type(encoded) is str
        and 0 < len(encoded) <= 4 * ((linux_clock.MAX_PAYLOAD_BYTES + 2) // 3)
        and _hash(record["raw_sha256"]),
        "linux_post_read_health_encoding_invalid",
    )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise LinuxPostReadBoundaryError(
            "linux_post_read_health_encoding_invalid"
        ) from None
    _require(
        base64.b64encode(raw).decode("ascii") == encoded
        and sha(raw) == record["raw_sha256"],
        "linux_post_read_health_pin_changed",
    )
    return linux_clock.replay_linux_clock_observation_v1(raw)


def _pg_profile(profile, *, id_expected, hash_expected):
    _keys(
        profile,
        {
            "profile_id",
            "server_image_digest",
            "server_build",
            "server_version_num",
            "quantization",
            "quantum_ns",
            "measurement_receipt_sha256",
            "postgres_source_sha256",
        },
    )
    _require(
        type(id_expected) is str
        and _IDENTIFIER.fullmatch(id_expected) is not None
        and profile["profile_id"] == id_expected
        and _hash(hash_expected)
        and sha(canonical(profile)) == hash_expected
        and type(profile["server_image_digest"]) is str
        and profile["server_image_digest"].startswith("sha256:")
        and _hash(profile["server_image_digest"][7:])
        and type(profile["server_build"]) is str
        and re.fullmatch(r"PostgreSQL 17\.11 on [ -~]{1,140}", profile["server_build"])
        is not None
        and "linux" in profile["server_build"].lower()
        and type(profile["server_version_num"]) is int
        and profile["server_version_num"] == 170011
        and profile["quantization"] == QUANTIZATION
        and type(profile["quantum_ns"]) is int
        and profile["quantum_ns"] == PG_QUANTUM_NS
        and _hash(profile["measurement_receipt_sha256"])
        and _hash(profile["postgres_source_sha256"]),
        "linux_post_read_pg_profile_changed",
    )


def _chronology(pre, post, record):
    samples = (
        *pre["samples"],
        record["data_readback_completed"],
        record["query_before"],
        record["query_after"],
        *post["samples"],
        record["validation_completed"],
    )
    # The V1 native primitive accepts 2–4 original brackets at a time. Check
    # adjacent brackets and every prefix endpoint against the same first stamp.
    for left, right in pairwise(samples):
        linux_clock._sample_sequence([left, right])
    for last in samples[2:]:
        linux_clock._sample_sequence([samples[0], last])


def _database(value, profile, host_domain, expected_domain_receipt_sha256):
    _keys(
        value,
        {
            "profile_id",
            "profile_sha256",
            "boot_domain",
            "boot_domain_receipt_sha256",
            "server_image_digest",
            "server_build",
            "server_version_num",
            "database_oid",
            "login_oid",
            "backend_pid",
            "readback_transaction_xid",
            "sample_transaction_xid",
            "transaction_read_only",
            "postmaster_started_utc_ns",
            "transaction_started_utc_ns",
            "sampled_utc_ns",
        },
    )
    _pg_profile(
        profile, id_expected=value["profile_id"], hash_expected=value["profile_sha256"]
    )
    _keys(value["boot_domain"], set(_DOMAIN_FIELDS))
    _require(
        canonical(value["boot_domain"]) == canonical(host_domain)
        and _hash(expected_domain_receipt_sha256)
        and value["boot_domain_receipt_sha256"] == expected_domain_receipt_sha256,
        "linux_post_read_database_domain_changed",
    )
    _require(
        all(
            value[key] == profile[key]
            for key in ("server_image_digest", "server_build", "server_version_num")
        )
        and type(value["server_version_num"]) is int
        and _integer(value["database_oid"], 1, 2**32 - 1)
        and _integer(value["login_oid"], 1, 2**32 - 1)
        and _integer(value["backend_pid"], 1, 2**31 - 1)
        and value["transaction_read_only"] == "on"
        and all(
            type(value[key]) is str
            and _XID.fullmatch(value[key]) is not None
            and int(value[key]) < 2**64
            for key in ("readback_transaction_xid", "sample_transaction_xid")
        )
        and value["readback_transaction_xid"] != value["sample_transaction_xid"],
        "linux_post_read_database_identity_changed",
    )
    times = tuple(
        value[key]
        for key in (
            "postmaster_started_utc_ns",
            "transaction_started_utc_ns",
            "sampled_utc_ns",
        )
    )
    _require(
        all(
            _integer(item, 0, MAX_TIMESTAMP_NS) and item % PG_QUANTUM_NS == 0
            for item in times
        )
        and times[0] <= times[1] <= times[2],
        "linux_post_read_database_timestamp_invalid",
    )
    return times[-1]


def _boundary(
    data,
    profile,
    expected_source_sha256,
    expected_source_available_at_ns,
    expected_database_domain_receipt_sha256,
):
    _keys(
        data,
        {
            "schema_version",
            "source_readback_sha256",
            "source_available_at_ns",
            "health_before",
            "health_after",
            "data_readback_completed",
            "query_before",
            "query_after",
            "validation_completed",
            "database",
            "upstream_server",
            "last_upstream_sync_at",
            *_NON_CLAIMS,
        },
    )
    _require(
        data["schema_version"] == SCHEMA
        and _hash(expected_source_sha256)
        and data["source_readback_sha256"] == expected_source_sha256
        and _integer(expected_source_available_at_ns, 0, MAX_TIMESTAMP_NS)
        and type(data["source_available_at_ns"]) is int
        and data["source_available_at_ns"] == expected_source_available_at_ns,
        "linux_post_read_source_pin_changed",
    )
    _require(
        data["upstream_server"] is None
        and data["last_upstream_sync_at"] is None
        and all(data[key] is False for key in _NON_CLAIMS),
        "linux_post_read_authority_forbidden",
    )
    pre, post = _health(data["health_before"]), _health(data["health_after"])
    _require(
        pre["profile_sha256"] == post["profile_sha256"]
        and canonical(pre["host_after"]) == canonical(post["host_before"]),
        "linux_post_read_native_domain_changed",
    )
    domain = {key: pre["host_after"][key] for key in _DOMAIN_FIELDS}
    _chronology(pre, post, data)
    sampled = _database(
        data["database"], profile, domain, expected_database_domain_receipt_sha256
    )
    _require(
        data["source_available_at_ns"] <= data["data_readback_completed"]["utc_ns"]
        and data["data_readback_completed"]["utc_ns"]
        <= data["database"]["transaction_started_utc_ns"]
        and data["query_before"]["utc_ns"] <= sampled
        and sampled + PG_QUANTUM_NS - 1 <= data["query_after"]["utc_ns"],
        "linux_post_read_database_quantization_cell_outside_bracket",
    )
    return data


def replay_linux_post_read_boundary_v1(
    raw: bytes,
    *,
    expected_sha256: str,
    expected_source_sha256: str,
    expected_source_available_at_ns: int,
    expected_database_domain_receipt_sha256: str,
) -> dict:
    """Strict pure replay. Neither caller bytes nor replay create native custody."""
    _require_exact_clock_source()
    _require(
        type(raw) is bytes
        and 0 < len(raw) <= MAX_RECEIPT_BYTES
        and _hash(expected_sha256)
        and sha(raw) == expected_sha256,
        "linux_post_read_receipt_pin_changed",
    )
    value = decode(raw, MAX_RECEIPT_BYTES)
    _require(
        type(value) is dict and canonical(value) == raw,
        "linux_post_read_receipt_noncanonical",
    )
    database = value.get("database")
    profile_id = database.get("profile_id") if type(database) is dict else None
    profile = _REVIEWED_PG_PROFILES.get(profile_id) if type(profile_id) is str else None
    _require(profile is not None, "linux_post_read_pg_profile_unreviewed")
    return _boundary(
        value,
        profile,
        expected_source_sha256,
        expected_source_available_at_ns,
        expected_database_domain_receipt_sha256,
    )
