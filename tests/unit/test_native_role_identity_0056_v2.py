"""Offline tests for stable role identity without a signed reference."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import native_role_identity_0056_v2 as subject
from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_role_witness_0056_v1 as witness56
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)

_NAMES = ("writer", "custody", "frame")
_ROUTE = ("postgresql+asyncpg", "db", 5432, "ctcc")
_ROUTES = (_ROUTE,) * 3
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _identity(name, oid, *, start=_START, database_oid=93):
    return (name, name, "none", oid, database_oid, start, 170011, "127.0.0.1", 5432)


def test_stable_digest_includes_every_role_oid_and_excludes_restart_time():
    first = subject._static_identity_sha256(93, 101, _NAMES, (201, 211, 301), _ROUTE)
    assert first != subject._static_identity_sha256(
        93, 101, _NAMES, (201, 212, 301), _ROUTE
    )
    assert first != subject._static_identity_sha256(
        93, 102, _NAMES, (201, 211, 301), _ROUTE
    )
    assert first != subject._static_identity_sha256(
        93, 101, _NAMES, (201, 211, 301), (*_ROUTE[:1], "other", *_ROUTE[2:])
    )
    # The static digest has no postmaster-start input; live witnesses below do.
    assert (
        witness56.runtime_witness_0056(_identity("writer", 201), _NAMES, _ROUTES)[0]
        != witness56.runtime_witness_0056(
            _identity("writer", 201, start=_START + timedelta(seconds=1)),
            _NAMES,
            _ROUTES,
        )[0]
    )


def test_pure_compare_returns_only_inert_match_and_refuses_any_oid_drift():
    digest = subject._static_identity_sha256(93, 101, _NAMES, (201, 211, 301), _ROUTE)
    observed = subject.NativeRoleIdentity0056V2(
        93, 101, _NAMES, (201, 211, 301), digest, "5" * 64, "6" * 64, "1" * 64, "4" * 64
    )
    expected = {
        "expected_database_oid": 93,
        "expected_owner_oid": 101,
        "expected_role_names": _NAMES,
        "expected_role_oids": (201, 211, 301),
        "expected_route": _ROUTE,
        "expected_sha256": digest,
    }
    result = subject.compare_static_role_identity_0056_v2(observed, **expected)
    assert result["stable_identity_sha256"] == digest
    assert result["admission"] == "DENY"
    assert result["startup_admission"] is result["execution_authority"] is False
    assert result["deployment_reference_authenticated"] is False
    for altered in (
        {"expected_role_oids": (201, 212, 301)},
        {"expected_owner_oid": 102},
        {"expected_database_oid": 94},
        {"expected_route": ("postgresql+asyncpg", "other", 5432, "ctcc")},
        {"expected_sha256": "0" * 64},
    ):
        with pytest.raises(subject.NativeRoleIdentity0056Error):
            subject.compare_static_role_identity_0056_v2(
                observed, **{**expected, **altered}
            )
    with pytest.raises(subject.NativeRoleIdentity0056Error):
        subject.compare_static_role_identity_0056_v2(
            replace(observed, role_oids=(201, 212, 301)), **expected
        )


@pytest.mark.asyncio
async def test_actual_identity_reader_denies_database_name_mismatch(monkeypatch):
    class Session:
        def begin(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def scalar(self, statement):
            assert statement is subject._DATABASE_NAME_SQL
            return "wrong_database"

    session = Session()

    class Factory:
        def __call__(self):
            return session

    async def settings(_session, *, frame):
        assert _session is session and frame is False

    async def identity(_session, name):
        assert _session is session and name == "writer"
        return _identity("writer", 201)

    monkeypatch.setattr(roles56.current55, "_session_settings", settings)
    monkeypatch.setattr(roles56, "_identity_at_0056", identity)
    with pytest.raises(subject.NativeRoleIdentity0056Error):
        await subject._read_identity(Factory(), "writer", "ctcc", with_owner=True)


@pytest.fixture
def scenario(monkeypatch):
    class Factory:
        def __init__(self, name):
            self.name = name
            self.kw = {"bind": SimpleNamespace(url=_ROUTE)}

    factories = tuple(Factory(name) for name in _NAMES)
    selected = ConfiguredNativeDemoFactoriesV2(
        *factories, factories[1].kw["bind"], factories[2].kw["bind"]
    )
    server, route = witness56.runtime_witness_0056(
        _identity("writer", 201), _NAMES, _ROUTES
    )
    witness = witness56.RestrictedNativeRoles0056WitnessV1(
        roles56.RestrictedNativeRoles0056ObservationV1(
            "1" * 64, "2" * 64, "3" * 64, "4" * 64
        ),
        server,
        route,
    )
    state = SimpleNamespace(
        selected=selected,
        witness=[witness, witness],
        identities=[
            (_identity("writer", 201), 101),
            (_identity("custody", 211), None),
            (_identity("frame", 301), None),
            (_identity("writer", 201), None),
        ],
        events=[],
    )

    monkeypatch.setattr(
        roles56.native_factories,
        "_factory_route",
        lambda factory: (None, None, "shared", factory.name),
    )
    monkeypatch.setattr(subject.selectors, "_database_route", lambda _: _ROUTE)

    async def observe(selected, *, source):
        assert selected is state.selected and source == Path("synthetic")
        state.events.append("catalog")
        return state.witness.pop(0)

    async def read(factory, name, expected_database, *, with_owner):
        state.events.append(name)
        identity, owner = state.identities.pop(0)
        assert identity[0] == factory.name == name
        assert expected_database == _ROUTE[3]
        assert with_owner is (name == "writer" and state.events.count("writer") == 1)
        return identity, owner

    monkeypatch.setattr(
        subject.witness56,
        "observe_restricted_native_roles_0056_witness_v1",
        observe,
    )
    monkeypatch.setattr(subject, "_read_identity", read)
    return state


@pytest.mark.asyncio
async def test_observed_role_oids_are_bracketed_by_fresh_catalog_readbacks(scenario):
    result = await subject.observe_restricted_role_identity_0056_v2(
        scenario.selected, source=Path("synthetic")
    )
    assert scenario.events == [
        "catalog",
        "writer",
        "custody",
        "frame",
        "writer",
        "catalog",
    ]
    assert result.database_oid == 93 and result.owner_oid == 101
    assert result.role_oids == (201, 211, 301)
    assert result.stable_identity_sha256 == subject._static_identity_sha256(
        93, 101, _NAMES, (201, 211, 301), _ROUTE
    )
    assert result.deployment_reference_authenticated is False
    assert result.startup_admission is result.execution_authority is False
    assert result.admission == "DENY"
    assert "writer" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["restart", "custody_server", "catalog_change"])
async def test_observation_denies_between_readback_drift(scenario, fault):
    if fault == "restart":
        scenario.identities[3] = (
            _identity("writer", 201, start=_START + timedelta(seconds=1)),
            None,
        )
    elif fault == "custody_server":
        scenario.identities[1] = (_identity("custody", 211, database_oid=94), None)
    else:
        scenario.witness[1] = replace(
            scenario.witness[1],
            observation=replace(
                scenario.witness[1].observation,
                raw_custody_catalog_sha256="8" * 64,
            ),
        )
    with pytest.raises(subject.NativeRoleIdentity0056Error):
        await subject.observe_restricted_role_identity_0056_v2(
            scenario.selected, source=Path("synthetic")
        )
