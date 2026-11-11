"""Synthetic timing replay only. No Linux syscall, socket, DB or provider access."""

from __future__ import annotations

import base64
from copy import deepcopy
from types import MappingProxyType

import pytest

from app.domain import linux_native_clock_v1 as clock
from app.domain.source_primitives import PublicReceiptError, canonical, decode, sha
from app.mie.validation import linux_post_read_boundary_v1 as boundary
from tests.unit.test_linux_native_clock_v1 import BASE, MONO
from tests.unit.test_linux_native_clock_v1 import (
    native_seam as native_seam,  # noqa: PLC0414 -- reuse the fixed synthetic native fixture.
)


def _stamp(delta):
    return {
        "utc_ns": BASE + delta,
        "monotonic_ns": MONO + delta,
        "monotonic_before_ns": MONO + delta - 10,
        "monotonic_after_ns": MONO + delta + 10,
        "boottime_ns": MONO + delta + 20_000_000,
    }


def _envelope(raw):
    return {"raw_base64": base64.b64encode(raw).decode("ascii"), "raw_sha256": sha(raw)}


def _alter_health(record, *, key, value):
    data = decode(base64.b64decode(record["raw_base64"]))
    for phase in ("host_before", "host_after"):
        data[phase][key] = value
    return _envelope(canonical(data))


@pytest.fixture
def staged(native_seam):
    before = clock._observe(native_seam())

    class LaterReader(native_seam):
        def __init__(self):
            super().__init__()
            self.index = 10

        def status(self):
            result, error, raw = super().status()
            timex = clock._Timex.from_buffer_copy(raw)
            timex.time.tv_usec += 2000
            return result, error, bytes(timex)

    after = clock._observe(LaterReader())
    original = decode(before)["host_after"]
    profile = {
        "profile_id": "synthetic-linux-pg-only.v1",
        "server_image_digest": "sha256:" + "a" * 64,
        "server_build": "PostgreSQL 17.11 on synthetic-linux test runtime",
        "server_version_num": 170011,
        "quantization": boundary.QUANTIZATION,
        "quantum_ns": 1000,
        "measurement_receipt_sha256": "b" * 64,
        "postgres_source_sha256": "c" * 64,
    }
    return {
        "schema_version": boundary.SCHEMA,
        "source_readback_sha256": "d" * 64,
        "source_available_at_ns": BASE + 600_000,
        "health_before": _envelope(before),
        "health_after": _envelope(after),
        "data_readback_completed": _stamp(700_123),
        "query_before": _stamp(900_123),
        "query_after": _stamp(1_500_321),
        "validation_completed": _stamp(2_600_333),
        "database": {
            "profile_id": profile["profile_id"],
            "profile_sha256": sha(canonical(profile)),
            "server_image_digest": profile["server_image_digest"],
            "server_build": profile["server_build"],
            "server_version_num": profile["server_version_num"],
            "boot_domain": {key: original[key] for key in boundary._DOMAIN_FIELDS},
            "boot_domain_receipt_sha256": "e" * 64,
            "database_oid": 51,
            "login_oid": 101,
            "backend_pid": 55,
            "readback_transaction_xid": "10001",
            "sample_transaction_xid": "10002",
            "transaction_read_only": "on",
            "postmaster_started_utc_ns": BASE - 1_000_000,
            "transaction_started_utc_ns": BASE + 1_000_000,
            "sampled_utc_ns": BASE + 1_200_000,
        },
        "upstream_server": None,
        "last_upstream_sync_at": None,
        **dict.fromkeys(boundary._NON_CLAIMS, False),
    }, profile


def _replay(
    data, *, raw=None, source_sha256=None, available_at_ns=None, domain_sha256=None
):
    payload = canonical(data) if raw is None else raw
    return boundary.replay_linux_post_read_boundary_v1(
        payload,
        expected_sha256=sha(payload),
        expected_source_sha256=data["source_readback_sha256"]
        if source_sha256 is None
        else source_sha256,
        expected_source_available_at_ns=data["source_available_at_ns"]
        if available_at_ns is None
        else available_at_ns,
        expected_database_domain_receipt_sha256=data["database"][
            "boot_domain_receipt_sha256"
        ]
        if domain_sha256 is None
        else domain_sha256,
    )


def _fixture_registry(monkeypatch, profile):
    monkeypatch.setattr(
        boundary,
        "_REVIEWED_PG_PROFILES",
        MappingProxyType({profile["profile_id"]: profile}),
    )


def test_linux_nanosecond_bracket_contains_measured_postgres_cell(staged, monkeypatch):
    data, profile = staged
    _fixture_registry(monkeypatch, profile)
    data["query_after"] = _stamp(1_200_999)
    result = _replay(data)
    assert result == data
    assert result["upstream_server"] is result["last_upstream_sync_at"] is None
    assert all(result[key] is False for key in boundary._NON_CLAIMS)
    assert result["query_before"]["utc_ns"] % 100 != 0


def test_unreviewed_postgres_profile_cannot_be_supplied_by_caller(staged):
    data, _ = staged
    assert not boundary._REVIEWED_PG_PROFILES
    with pytest.raises(
        boundary.LinuxPostReadBoundaryError, match="pg_profile_unreviewed"
    ):
        _replay(data)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("native_boot", "native_domain_changed"),
        ("native_namespace", "native_domain_changed"),
        ("database_boot", "database_domain_changed"),
        ("database_namespace", "database_domain_changed"),
        ("repeated_xid", "database_identity_changed"),
        ("non_linux_build", "database_identity_changed"),
        ("image_changed", "database_identity_changed"),
        ("non_microsecond", "database_timestamp_invalid"),
        ("clipped_cell", "quantization_cell_outside_bracket"),
        ("transaction_predates_readback", "quantization_cell_outside_bracket"),
        ("readback_predates_source", "quantization_cell_outside_bracket"),
        ("lease", "probe_expired"),
        ("overlap", "suspend_or_reversed"),
        ("host_suspend", "suspend_or_reversed"),
        ("invented_upstream", "authority_forbidden"),
        ("accepted_role_assertion", "authority_forbidden"),
    ],
)
def test_replay_refuses_domain_timing_profile_and_authority_drift(
    staged, monkeypatch, case, reason
):
    data, profile = staged
    _fixture_registry(monkeypatch, profile)
    database = data["database"]
    if case in {"native_boot", "native_namespace"}:
        key, value = (
            ("boot_id_sha256", "f" * 64)
            if case == "native_boot"
            else ("time_namespace", "time:[1234567]")
        )
        data["health_after"] = _alter_health(data["health_after"], key=key, value=value)
    elif case == "database_boot":
        database["boot_domain"]["boot_id_sha256"] = "f" * 64
    elif case == "database_namespace":
        database["boot_domain"]["time_namespace"] = "time:[1234567]"
    elif case == "repeated_xid":
        database["sample_transaction_xid"] = database["readback_transaction_xid"]
    elif case == "non_linux_build":
        database["server_build"] = "PostgreSQL 17.11 on x86_64-windows"
    elif case == "image_changed":
        database["server_image_digest"] = "sha256:" + "f" * 64
    elif case == "non_microsecond":
        database["sampled_utc_ns"] += 1
    elif case == "clipped_cell":
        data["query_after"] = _stamp(1_200_998)
    elif case == "transaction_predates_readback":
        database["transaction_started_utc_ns"] = BASE + 700_000
    elif case == "readback_predates_source":
        data["source_available_at_ns"] = BASE + 800_000
    elif case == "lease":
        data["validation_completed"] = _stamp(5_000_000_001)
    elif case == "overlap":
        data["query_before"] = _stamp(700_124)
    elif case == "host_suspend":
        data["validation_completed"]["boottime_ns"] += 5_000_001
    elif case == "invented_upstream":
        data["last_upstream_sync_at"] = "synthetic"
    elif case == "accepted_role_assertion":
        data["durable_role_custody_verified"] = True
    with pytest.raises(PublicReceiptError, match=reason):
        _replay(data)


def test_unknown_namespace_offset_refuses_even_with_rehashed_domain(
    staged, monkeypatch
):
    data, profile = staged
    _fixture_registry(monkeypatch, profile)
    phase = decode(base64.b64decode(data["health_after"]["raw_base64"]))
    offsets = "monotonic 0 1\nboottime 0 0\n"
    for record in (phase["host_before"], phase["host_after"]):
        record["namespace_offsets_ascii"] = offsets
        record["namespace_offsets_sha256"] = sha(offsets.encode())
    data["health_after"] = _envelope(canonical(phase))
    with pytest.raises(PublicReceiptError, match="linux_clock_namespace_offset"):
        _replay(data)


def test_source_and_canonical_receipt_pins_are_independent(staged, monkeypatch):
    data, profile = staged
    _fixture_registry(monkeypatch, profile)
    with pytest.raises(PublicReceiptError, match="source_pin_changed"):
        _replay(data, source_sha256="f" * 64)
    with pytest.raises(PublicReceiptError, match="source_pin_changed"):
        _replay(data, available_at_ns=data["source_available_at_ns"] + 1)
    with pytest.raises(PublicReceiptError, match="database_domain_changed"):
        _replay(data, domain_sha256="f" * 64)
    with pytest.raises(PublicReceiptError, match="receipt_noncanonical"):
        _replay(data, raw=canonical(data) + b"\n")
    changed = deepcopy(data)
    changed["database"]["profile_sha256"] = "f" * 64
    with pytest.raises(PublicReceiptError, match="pg_profile_changed"):
        _replay(changed)


def test_draft_clock_acquisition_refuses_before_native_call(monkeypatch):
    def forbidden():
        pytest.fail("disabled draft must never acquire a native token")

    monkeypatch.setattr(clock, "observe_linux_clock_v1", forbidden)
    with pytest.raises(PublicReceiptError, match="acquisition_disabled"):
        boundary.capture_local_health_bytes()
