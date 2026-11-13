"""Offline byte pin and shared-context mechanics; no PostgreSQL connection."""

from __future__ import annotations

import hashlib
import os
import ssl
import stat
from pathlib import Path

import certifi
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.native_startup_control_observation_0056_v1 import _factory_pins
from app.database.native_pg_tls_v1 import (
    NativePgTlsError,
    verified_native_pg_tls_context_v1,
)
from app.database.repositories.collector_current_0055_multirole_catalog_v1 import (
    _factory_route,
)
from app.database.repositories.public_runtime_bundle_custody import _closure
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)


@pytest.fixture
def public_ca_file(tmp_path: Path) -> tuple[Path, str]:
    # certifi is a locked public CA bundle; no private key enters this fixture.
    payload = Path(certifi.where()).read_bytes()
    path = tmp_path / "reviewed-ca.pem"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _denied(path, expected) -> None:
    with pytest.raises(NativePgTlsError) as caught:
        verified_native_pg_tls_context_v1(path, expected)
    assert str(caught.value) == "native_pg_tls_unavailable"


def test_exact_regular_ca_builds_verified_context(public_ca_file):
    path, expected = public_ca_file
    context = verified_native_pg_tls_context_v1(path, expected)
    assert type(context) is ssl.SSLContext
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.cert_store_stats()["x509_ca"] > 0
    assert context._msg_callback is None and context.sni_callback is None


@pytest.mark.asyncio
async def test_same_context_identity_is_required_by_actual_role_pins(public_ca_file):
    path, expected = public_ca_file
    shared = verified_native_pg_tls_context_v1(path, expected)
    separate = verified_native_pg_tls_context_v1(path, expected)
    assert separate is not shared

    def factory(role, context):
        engine = create_async_engine(
            f"postgresql+asyncpg://{role}@native.example.invalid:5432/ctcc",
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args={"ssl": context},
        )
        return engine, async_sessionmaker(
            engine, autoflush=False, expire_on_commit=False
        )

    writer, writer_factory = factory("writer", shared)
    custody, custody_factory = factory("custody", shared)
    frame, frame_factory = factory("frame", shared)
    other, other_factory = factory("frame", separate)
    try:
        selected = ConfiguredNativeDemoFactoriesV2(
            writer_factory, custody_factory, frame_factory, custody, frame
        )
        routes = tuple(
            _factory_route(candidate)
            for candidate in (writer_factory, custody_factory, frame_factory)
        )
        assert len({route[3] for route in routes}) == 3
        assert routes[0][2] == routes[1][2] == routes[2][2]
        assert _factory_route(other_factory)[2] != routes[0][2]
        for engine in (writer, custody, frame):
            parameters = _closure(engine.sync_engine.pool._creator)["cparams"]
            assert parameters["ssl"] is shared
        assert _factory_pins(selected)[0] is writer_factory
    finally:
        for engine in (writer, custody, frame, other):
            await engine.dispose()


def test_wrong_digest_type_or_contents_fail_closed(public_ca_file):
    path, expected = public_ca_file
    _denied(path, "0" * 64)
    _denied(path, expected.upper())
    _denied(path, 3)
    path.write_bytes(b"changed public CA bytes")
    _denied(path, expected)


def test_missing_nonregular_oversized_or_malformed_ca_fails_closed(tmp_path):
    missing = tmp_path / "missing.pem"
    _denied(missing, "0" * 64)
    _denied(tmp_path, "0" * 64)
    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    _denied(empty, hashlib.sha256(b"").hexdigest())
    oversized = tmp_path / "oversized.pem"
    oversized.write_bytes(b"x" * 1_048_577)
    _denied(oversized, hashlib.sha256(oversized.read_bytes()).hexdigest())
    malformed = tmp_path / "malformed.pem"
    malformed.write_bytes(b"-----BEGIN CERTIFICATE-----\nnot-a-certificate\n")
    _denied(malformed, hashlib.sha256(malformed.read_bytes()).hexdigest())
    _denied(str(malformed), hashlib.sha256(malformed.read_bytes()).hexdigest())
    _denied(Path("relative.pem"), "0" * 64)


def _link_metadata(value: os.stat_result) -> os.stat_result:
    fields = list(value)
    fields[0] = stat.S_IFLNK | stat.S_IMODE(value.st_mode)
    return os.stat_result(fields)


def test_leaf_symlink_metadata_refused_and_posix_link_checked(
    tmp_path, public_ca_file, monkeypatch
):
    source, expected = public_ca_file
    candidate = tmp_path / "metadata-linked-ca.pem"
    candidate.write_bytes(source.read_bytes())
    original = Path.lstat
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "lstat",
            lambda self: (
                _link_metadata(original(self)) if self == candidate else original(self)
            ),
        )
        _denied(candidate, expected)
    if os.name != "nt":
        link = tmp_path / "linked-ca.pem"
        link.symlink_to(source)
        _denied(link, expected)


def test_parent_symlink_metadata_refused_and_posix_link_checked(
    tmp_path, public_ca_file, monkeypatch
):
    source, expected = public_ca_file
    parent = tmp_path / "metadata-linked-directory"
    parent.mkdir()
    (parent / source.name).write_bytes(source.read_bytes())
    original = Path.lstat
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "lstat",
            lambda self: (
                _link_metadata(original(self)) if self == parent else original(self)
            ),
        )
        _denied(parent / source.name, expected)
    if os.name != "nt":
        link = tmp_path / "linked-directory"
        link.symlink_to(source.parent, target_is_directory=True)
        _denied(link / source.name, expected)
