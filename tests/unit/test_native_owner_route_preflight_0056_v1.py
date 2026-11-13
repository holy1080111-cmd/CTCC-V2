"""Offline owner-route refusal and read-only profile tests; no database access."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import certifi
import pytest

from scripts import native_owner_route_preflight_0056_v1 as subject
from scripts.current_contract_source_scopes_v1 import (
    actual_current_source_scope as real_source_scope,
)

_SECRET = "fixture-password-that-must-never-appear"
_MANIFEST = "a" * 64
_MIGRATION = "b" * 64
_CLUSTER = "12345678901234567890"


@pytest.fixture
def prepared_files(tmp_path: Path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_bytes(Path(certifi.where()).read_bytes())
    secret = tmp_path / "owner-dsn"
    secret.write_text(
        f"postgresql+asyncpg://owner:{_SECRET}@db.example.invalid:5432/ctcc\n",
        encoding="utf-8",
    )
    scope = SimpleNamespace(
        actual_source_head="0056",
        manifest_sha256=_MANIFEST,
        actual_migration_sha256=_MIGRATION,
        execution_authority=False,
    )
    monkeypatch.setattr(subject, "actual_current_source_scope", lambda _: scope)
    pins = subject.OwnerRoutePinsV1(
        source_manifest_sha256=_MANIFEST,
        ca_sha256=hashlib.sha256(ca.read_bytes()).hexdigest(),
        cluster_system_identifier_sha256=hashlib.sha256(
            _CLUSTER.encode("ascii")
        ).hexdigest(),
        host="db.example.invalid",
        port=5432,
        database="ctcc",
        owner="owner",
        database_oid=16384,
        owner_oid=16385,
        database_head="0056",
    )
    return pins, secret, ca, scope


def _prepare(parts):
    pins, secret, ca, _ = parts
    return subject._prepare(pins, owner_dsn_file=secret, ca_file=ca)


def test_exact_source_route_ca_and_secret_are_prepared_without_connection(
    prepared_files, monkeypatch
):
    monkeypatch.setattr(subject, "create_async_engine", lambda *a, **k: pytest.fail())
    prepared = _prepare(prepared_files)
    assert prepared.url.username == "owner"
    assert prepared.url.password == _SECRET
    assert prepared.source_head == "0056"
    assert prepared.manifest_sha256 == _MANIFEST
    assert prepared.migration_sha256 == _MIGRATION
    assert prepared.tls.check_hostname is True
    assert _SECRET not in repr(prepared)


def test_preflight_binds_actual_manifest_and_migration_source(
    prepared_files, monkeypatch
):
    pins, secret, ca, _ = prepared_files
    manifest = subject._SOURCE_ROOT / "MANIFEST.sha256"
    pins = replace(
        pins, source_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(subject, "actual_current_source_scope", real_source_scope)
    prepared = subject._prepare(pins, owner_dsn_file=secret, ca_file=ca)
    assert prepared.manifest_sha256 == pins.source_manifest_sha256
    assert prepared.migration_sha256 != _MIGRATION


@pytest.mark.parametrize(
    "change",
    [
        "source_manifest",
        "source_head",
        "route_host",
        "route_port",
        "route_user",
        "route_query",
        "empty_password",
        "oversized_secret",
        "embedded_newline",
        "ca_digest",
        "cluster_digest",
        "database_oid",
        "invalid_head",
    ],
)
def test_bad_preconnection_identity_is_fixed_refusal(
    prepared_files, change, monkeypatch
):
    pins, secret, ca, scope = prepared_files
    if change == "source_manifest":
        pins = replace(pins, source_manifest_sha256="0" * 64)
    elif change == "source_head":
        scope.actual_source_head = "0055"
    elif change == "route_host":
        secret.write_text(
            f"postgresql+asyncpg://owner:{_SECRET}@wrong.example.invalid:5432/ctcc"
        )
    elif change == "route_port":
        secret.write_text(
            f"postgresql+asyncpg://owner:{_SECRET}@db.example.invalid:5433/ctcc"
        )
    elif change == "route_user":
        secret.write_text(
            f"postgresql+asyncpg://other:{_SECRET}@db.example.invalid:5432/ctcc"
        )
    elif change == "route_query":
        secret.write_text(
            f"postgresql+asyncpg://owner:{_SECRET}@db.example.invalid:5432/ctcc?ssl=false"
        )
    elif change == "empty_password":
        secret.write_text("postgresql+asyncpg://owner@db.example.invalid:5432/ctcc")
    elif change == "oversized_secret":
        secret.write_bytes(b"x" * 8193)
    elif change == "embedded_newline":
        secret.write_text(
            f"postgresql+asyncpg://owner:{_SECRET}@db.example.invalid:5432/ctcc\nother"
        )
    elif change == "ca_digest":
        pins = replace(pins, ca_sha256="0" * 64)
    elif change == "cluster_digest":
        pins = replace(pins, cluster_system_identifier_sha256="invalid")
    elif change == "database_oid":
        pins = replace(pins, database_oid=0)
    elif change == "invalid_head":
        pins = replace(pins, database_head="0054")
    monkeypatch.setattr(subject, "create_async_engine", lambda *a, **k: pytest.fail())
    with pytest.raises(subject.OwnerRoutePreflightError) as caught:
        subject._prepare(pins, owner_dsn_file=secret, ca_file=ca)
    assert str(caught.value) == "native_owner_route_preflight_refused"
    assert _SECRET not in str(caught.value)


def test_secret_link_and_missing_file_refused(prepared_files, tmp_path, monkeypatch):
    pins, secret, ca, _ = prepared_files
    with pytest.raises(subject.OwnerRoutePreflightError):
        subject._prepare(pins, owner_dsn_file=tmp_path / "missing", ca_file=ca)
    linked = tmp_path / "linked-owner-dsn"
    linked.write_bytes(secret.read_bytes())
    original = Path.lstat

    def linked_metadata(path):
        value = original(path)
        if path != linked:
            return value
        fields = list(value)
        fields[0] = stat.S_IFLNK | stat.S_IMODE(value.st_mode)
        return os.stat_result(fields)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", linked_metadata)
        with pytest.raises(subject.OwnerRoutePreflightError):
            subject._prepare(pins, owner_dsn_file=linked, ca_file=ca)
    if os.name != "nt":
        linked.unlink()
        linked = tmp_path / "linked-owner-dsn"
        linked.symlink_to(secret)
        with pytest.raises(subject.OwnerRoutePreflightError):
            subject._prepare(pins, owner_dsn_file=linked, ca_file=ca)


@pytest.mark.asyncio
async def test_two_readbacks_produce_only_redacted_diagnostic(
    prepared_files, monkeypatch
):
    pins, secret, ca, _ = prepared_files
    created = []

    class FakeEngine:
        async def dispose(self):
            created.append("disposed")

    def create(url, **kwargs):
        assert url.password == _SECRET
        assert kwargs["connect_args"]["ssl"].check_hostname is True
        created.append("created")
        return FakeEngine()

    async def read_one(engine, actual_pins):
        assert actual_pins is pins
        created.append("read")
        return (
            pins.cluster_system_identifier_sha256,
            pins.database_oid,
            pins.owner_oid,
        )

    monkeypatch.setattr(subject, "create_async_engine", create)
    monkeypatch.setattr(subject, "_read_one", read_one)
    receipt = await subject.preflight_owner_route_0056_v1(
        pins, owner_dsn_file=secret, ca_file=ca
    )
    assert created == ["created", "read", "read", "disposed"]
    assert receipt["status"] == "PASS"
    assert receipt["independent_readonly_observations"] == 2
    assert receipt["startup_admission"] is False
    assert receipt["execution_authority"] is False
    assert receipt["schema_drift_verified"] is False
    assert receipt["password_authentication_verified"] is False
    assert _SECRET not in str(receipt)
    assert "db.example.invalid" not in str(receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["secret", "ca", "source"])
async def test_between_readback_identity_change_refused(
    prepared_files, monkeypatch, changed
):
    pins, secret, ca, scope = prepared_files

    class FakeEngine:
        async def dispose(self):
            pass

    monkeypatch.setattr(subject, "create_async_engine", lambda *a, **k: FakeEngine())
    count = 0

    async def read_one(engine, actual_pins):
        nonlocal count
        count += 1
        if count == 2:
            if changed == "secret":
                secret.write_text(
                    "postgresql+asyncpg://owner:changed@db.example.invalid:5432/ctcc"
                )
            elif changed == "ca":
                ca.write_bytes(b"changed CA")
            else:
                scope.manifest_sha256 = "0" * 64
        return (
            pins.cluster_system_identifier_sha256,
            pins.database_oid,
            pins.owner_oid,
        )

    monkeypatch.setattr(subject, "_read_one", read_one)
    with pytest.raises(subject.OwnerRoutePreflightError):
        await subject.preflight_owner_route_0056_v1(
            pins, owner_dsn_file=secret, ca_file=ca
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["create", "dispose"])
async def test_engine_failure_never_exposes_private_detail(
    prepared_files, monkeypatch, stage
):
    pins, secret, ca, _ = prepared_files

    class FakeEngine:
        async def dispose(self):
            raise RuntimeError(_SECRET)

    def create(*args, **kwargs):
        if stage == "create":
            raise RuntimeError(_SECRET)
        return FakeEngine()

    async def read_one(*args):
        return (
            pins.cluster_system_identifier_sha256,
            pins.database_oid,
            pins.owner_oid,
        )

    monkeypatch.setattr(subject, "create_async_engine", create)
    monkeypatch.setattr(subject, "_read_one", read_one)
    with pytest.raises(subject.OwnerRoutePreflightError) as caught:
        await subject.preflight_owner_route_0056_v1(
            pins, owner_dsn_file=secret, ca_file=ca
        )
    assert str(caught.value) == "native_owner_route_preflight_refused"
    assert _SECRET not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_index", [None, 0, 6, 7, 10, 11])
async def test_actual_profile_is_readonly_and_checks_owner_tls_head_and_cluster(
    prepared_files, changed_index
):
    pins, _, _, _ = prepared_files
    row = [
        pins.owner,
        pins.owner,
        pins.database,
        pins.port,
        pins.owner_oid,
        pins.database_oid,
        pins.owner_oid,
        True,
        "TLSv1.3",
        "on",
        pins.database_head,
        _CLUSTER,
    ]
    if changed_index is not None:
        row[changed_index] = False if changed_index == 7 else "changed"
    statements = []

    class Transaction:
        async def rollback(self):
            statements.append("ROLLBACK")

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def begin(self):
            return Transaction()

        async def exec_driver_sql(self, statement):
            statements.append(statement)

        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(one=lambda: row)

    engine = SimpleNamespace(connect=lambda: Connection())
    if changed_index is None:
        assert await subject._read_one(engine, pins) == (
            pins.cluster_system_identifier_sha256,
            pins.database_oid,
            pins.owner_oid,
        )
    else:
        with pytest.raises(subject.OwnerRoutePreflightError):
            await subject._read_one(engine, pins)
    assert statements[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    assert statements[-1] == "ROLLBACK"
    assert sum(statement is subject._PROFILE_SQL for statement in statements) == 1


def test_no_explicit_flag_is_fixed_refusal_without_db(capsys, monkeypatch):
    monkeypatch.setattr(subject, "create_async_engine", lambda *a, **k: pytest.fail())
    assert subject.main([]) == 1
    assert capsys.readouterr().out.strip() == (
        '{"status":"REFUSED","code":"native_owner_route_preflight_refused"}'
    )
