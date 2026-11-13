"""Target binding, real engine construction and denial without database I/O."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.core.native_role_identity_0056_v2 import NativeRoleIdentity0056V2
from app.core.native_role_witness_0056_v1 import runtime_witness_0056
from app.domain.source_primitives import canonical, sha
from scripts import native_owner_postgrant_readback_0056_v1 as readback
from tests.unit.test_native_owner_grant_0056_draft_v1 import _pins


def test_loads_exact_published_plan_and_rejects_changed_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(readback.target_plan, "_require_private_file", Mock())
    path = tmp_path / "target.json"
    pins = _pins()
    raw = canonical(
        {
            "schema": readback.target_plan._PRIVATE_PLAN_SCHEMA,
            "pins": asdict(pins),
        }
    )
    path.write_bytes(raw)
    assert readback._load_plan(path, sha(raw)) == pins
    with pytest.raises(readback.OwnerPostgrantReadbackRefusal):
        readback._load_plan(path, "f" * 64)


def test_postgrant_cli_keeps_deny_for_matching_target(monkeypatch, capsys):
    paths = [
        "--inspect-granted-owner-target",
        "--plan-file",
        "pins",
        "--plan-file-sha256",
        "digest",
        "--owner-dsn-file",
        "owner",
        "--ca-file",
        "ca",
        "--source-archive-file",
        "archive",
    ]
    monkeypatch.setattr(readback, "_load_plan", Mock(return_value=_pins()))
    observe = AsyncMock(return_value={"admission": "DENY", "status": "OBSERVED_DENY"})
    monkeypatch.setattr(readback, "observe_owner_postgrant_readback_0056_v1", observe)
    assert readback.main(paths) == 2
    assert json.loads(capsys.readouterr().out)["admission"] == "DENY"
    observe.assert_awaited_once()
    assert readback.main(paths[1:]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"
    observe.assert_awaited_once()


def test_owner_incarnation_normalizes_to_restricted_server_digest():
    names = ("writer", "custody", "frame")
    route = ("postgresql+asyncpg", "pg.local", 5432, "ctcc")
    boot = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
    shared = (21, boot, 170011, "127.0.0.1/32", 5432)
    role_digest, _ = runtime_witness_0056(
        ("writer", "writer", "none", 42, *shared), names, (route,) * 3
    )
    assert (
        readback._owner_server_sha256(("owner", "owner", "none", 43, *shared))
        == role_digest
    )


@pytest.fixture
def staged(monkeypatch):
    pins = _pins()
    names = (pins.writer, pins.custody, pins.frame_reader)
    oids = (pins.writer_oid, pins.custody_oid, pins.frame_reader_oid)
    route = (
        "postgresql+asyncpg",
        pins.route.host,
        pins.route.port,
        pins.route.database,
    )
    owner_url = URL.create(
        *route[:1],
        username=pins.route.owner,
        password="secret",
        host=route[1],
        port=route[2],
        database=route[3],
    )
    role_urls = tuple(owner_url.set(username=name) for name in names)
    tls = object()
    original = [SimpleNamespace(url=url, dispose=AsyncMock()) for url in role_urls]
    factory = readback.selectors.ConfiguredNativeDemoFactoriesV2
    configured = factory(
        *(SimpleNamespace(kw={"bind": engine}) for engine in original), *original[1:]
    )
    actual = NativeRoleIdentity0056V2(
        pins.route.database_oid,
        pins.route.owner_oid,
        names,
        oids,
        readback.identity56._static_identity_sha256(
            pins.route.database_oid, pins.route.owner_oid, names, oids, route
        ),
        "5" * 64,
        "6" * 64,
        pins.portable_inherited_definitions_sha256,
        readback.roles56._granted_inherited_acl_shape_sha256(
            names, pins.portable_inherited_definitions_sha256
        ),
    )
    opened = []
    sequence = []

    def open_engine(url, **options):
        engine = SimpleNamespace(url=url, dispose=AsyncMock())
        opened.append((engine, options))
        return engine

    async def owner_observation(*_args):
        sequence.append("owner")
        return actual.server_incarnation_sha256

    async def restricted_observation(selected, *, source):
        sequence.append("roles")
        assert source == readback._SOURCE
        assert tuple(
            item.kw["bind"]
            for item in (
                selected.writer_factory,
                selected.custody_factory,
                selected.frame_reader_factory,
            )
        ) == tuple(engine for engine, _ in opened[1:])
        return actual

    settings = SimpleNamespace(ctcc_native_pg_tls_ca_sha256=pins.route.ca_sha256)
    monkeypatch.setattr(
        readback, "require_exact_installed_owner_source_0056_v1", Mock()
    )
    monkeypatch.setattr(readback.target_plan, "_require_private_file", Mock())
    monkeypatch.setattr(
        readback.owner_route,
        "_prepare",
        Mock(return_value=SimpleNamespace(url=owner_url, tls=tls)),
    )
    monkeypatch.setattr(readback, "get_settings", lambda: settings)
    monkeypatch.setattr(
        readback.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: configured,
    )
    monkeypatch.setattr(readback, "create_async_engine", open_engine)
    monkeypatch.setattr(
        readback, "_factory", lambda engine: SimpleNamespace(kw={"bind": engine})
    )
    monkeypatch.setattr(readback, "_owner_incarnation", owner_observation)
    monkeypatch.setattr(
        readback.identity56,
        "observe_restricted_role_identity_0056_v2",
        restricted_observation,
    )
    return SimpleNamespace(**locals())


async def _observe(staged):
    return await readback.observe_owner_postgrant_readback_0056_v1(
        staged.pins,
        owner_dsn_file=Path("owner"),
        ca_file=Path("ca"),
        source_archive_file=Path("archive"),
    )


@pytest.mark.asyncio
async def test_brackets_three_fresh_role_logins_and_disposes_on_success(staged):
    result = await _observe(staged)
    assert staged.sequence == ["owner", "roles", "owner"]
    assert tuple(engine.url for engine, _ in staged.opened) == (
        staged.owner_url,
        *staged.role_urls,
    )
    assert all(
        options
        == {
            "poolclass": NullPool,
            "connect_args": {"ssl": staged.tls, "timeout": 10},
            "hide_parameters": True,
        }
        for _, options in staged.opened
    )
    assert all(engine.dispose.await_count == 1 for engine, _ in staged.opened)
    assert [engine.dispose.await_count for engine in staged.original] == [0, 1, 1]
    assert result["restricted_logins_observed"] == 3
    assert result["separate_read_only_owner_observations"] == 2
    assert result["password_authentication_verified"] is False
    assert result["startup_admission"] is result["execution_authority"] is False
    assert result["admission"] == "DENY"
    assert all(
        secret not in repr(result)
        for secret in ("secret", staged.pins.writer, staged.pins.route.host)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["owner", "route", "role", "ca", "oid", "acl"])
async def test_target_or_configuration_drift_refuses_without_authority(
    staged, monkeypatch, drift
):
    if drift == "owner":
        monkeypatch.setattr(
            readback, "_owner_incarnation", AsyncMock(side_effect=["5" * 64, "f" * 64])
        )
    elif drift == "route":
        staged.original[1].url = staged.role_urls[1].set(host="unreviewed")
    elif drift == "role":
        staged.original[1].url = staged.role_urls[1].set(username=staged.pins.writer)
    elif drift == "ca":
        staged.settings.ctcc_native_pg_tls_ca_sha256 = "f" * 64
    elif drift == "oid":
        monkeypatch.setattr(
            readback.identity56,
            "observe_restricted_role_identity_0056_v2",
            AsyncMock(return_value=replace(staged.actual, role_oids=(101, 102, 104))),
        )
    else:
        monkeypatch.setattr(
            readback.identity56,
            "observe_restricted_role_identity_0056_v2",
            AsyncMock(
                return_value=replace(
                    staged.actual, granted_inherited_acl_shape_sha256="f" * 64
                )
            ),
        )
    with pytest.raises(
        readback.OwnerPostgrantReadbackRefusal,
        match="^native_owner_0056_postgrant_readback_denied$",
    ):
        await _observe(staged)
    if drift in {"route", "role", "ca"}:
        assert staged.opened == []
    else:
        assert len(staged.opened) == 4
        assert all(engine.dispose.await_count == 1 for engine, _ in staged.opened)
