"""Offline preparation only; no PostgreSQL connection or migration runs here."""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

import certifi
import pytest
from sqlalchemy.engine import make_url

from app.database.native_pg_tls_v1 import NativePgTlsError
from tests import native_owner_migration_lab_0056_v1 as subject


@pytest.fixture
def inputs(tmp_path):
    payload = Path(certifi.where()).read_bytes()
    ca_file = tmp_path / "reviewed-lab-ca.pem"
    ca_file.write_bytes(payload)
    return {
        "owner_url": make_url(
            "postgresql+asyncpg://ctcc@ctcc-final-012345abcdef-postgres:5432/"
            "ctcc_native_obs56_012345abcdef"
        ),
        "ca_file": ca_file,
        "ca_sha256": hashlib.sha256(payload).hexdigest(),
        "manifest_sha256": hashlib.sha256(
            (subject._SOURCE / "MANIFEST.sha256").read_bytes()
        ).hexdigest(),
    }


def test_exact_lab_source_and_ca_prepare_without_database(inputs):
    tls, scope, script = subject._prepare(**inputs)
    assert type(tls) is ssl.SSLContext
    assert tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname is True
    assert scope.actual_source_head == scope.database_target == "0056"
    assert scope.manifest_sha256 == inputs["manifest_sha256"]
    assert tuple(script.get_heads()) == ("0056",)


@pytest.mark.parametrize(
    "url",
    (
        "postgresql+asyncpg://ctcc@external.example:5432/ctcc_native_obs56_012345abcdef",
        "postgresql+asyncpg://ctcc:password@ctcc-final-012345abcdef-postgres:5432/ctcc_native_obs56_012345abcdef",
        "postgresql+asyncpg://writer@ctcc-final-012345abcdef-postgres:5432/ctcc_native_obs56_012345abcdef",
        "postgresql+asyncpg://ctcc@ctcc-final-012345abcdef-postgres:5432/ctcc",
        "postgresql+asyncpg://ctcc@ctcc-final-012345abcdef-postgres:5433/ctcc_native_obs56_012345abcdef",
        "postgresql+psycopg://ctcc@ctcc-final-012345abcdef-postgres:5432/ctcc_native_obs56_012345abcdef",
        "postgresql+asyncpg://ctcc@ctcc-final-012345abcdef-postgres:5432/ctcc_native_obs56_012345abcdef?ssl=disable",
    ),
)
def test_nonisolated_or_ambiguous_route_refuses_before_ca_read(
    inputs, url, monkeypatch
):
    monkeypatch.setattr(
        subject,
        "verified_native_pg_tls_context_v1",
        lambda *args: pytest.fail("invalid route reached CA acquisition"),
    )
    inputs["owner_url"] = make_url(url)
    with pytest.raises(
        subject.LabOwnerMigration0056Error,
        match="^native_owner_migration_lab_0056_denied$",
    ):
        subject._prepare(**inputs)


def test_bad_ca_and_unpinned_manifest_refuse_offline(inputs):
    inputs["ca_sha256"] = "0" * 64
    with pytest.raises(NativePgTlsError, match="^native_pg_tls_unavailable$"):
        subject._prepare(**inputs)
    inputs["ca_sha256"] = hashlib.sha256(inputs["ca_file"].read_bytes()).hexdigest()
    inputs["manifest_sha256"] = "0" * 64
    with pytest.raises(
        subject.LabOwnerMigration0056Error,
        match="^native_owner_migration_lab_0056_denied$",
    ):
        subject._prepare(**inputs)
