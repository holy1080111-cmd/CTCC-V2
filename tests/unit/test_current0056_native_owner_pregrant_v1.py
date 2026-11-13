"""Offline owner 0056 plan and refusal tests; no database is opened."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import current0055_native_account_source_acl_v1 as account_acl
from scripts import current0055_native_lifecycle_acl_v1 as lifecycle_acl
from scripts import current0055_runtime_grants as core_acl
from scripts import current0056_native_owner_pregrant_v1 as owner56

_NAMES = {
    "writer": "ctcc_test_writer",
    "custody": "ctcc_test_custody",
    "frame_reader": "ctcc_test_frame",
}


def test_exact_0056_plan_is_only_the_inherited_42_statements():
    core, account = account_acl._plans(**_NAMES)
    previous, lifecycle = lifecycle_acl._plans(**_NAMES)
    assert previous == core + account
    expected = tuple(item.sql() for item in previous + lifecycle)
    actual = owner56.exact_42_grant_sql_0056(**_NAMES)
    assert actual == expected
    assert (len(core), len(account), len(lifecycle), len(actual)) == (28, 11, 3, 42)
    assert len(set(actual)) == 42
    assert all("gate3_fitted_" not in statement for statement in actual)
    assert all(" TO PUBLIC" not in statement for statement in actual)


def test_role_name_cannot_expand_or_inject_grant_plan():
    with pytest.raises(
        ValueError, match="current0055_core_grant_scope_or_catalog_changed"
    ):
        owner56.exact_42_grant_sql_0056(
            **(_NAMES | {"writer": "ctcc_writer; GRANT ALL"})
        )


def test_same_length_different_grant_matrix_is_denied(monkeypatch):
    original = lifecycle_acl._plans

    def changed(*args, **kwargs):
        previous, delta = original(*args, **kwargs)
        wrong = replace(delta[0], target="demo_automation_runs")
        return previous, (wrong, *delta[1:])

    monkeypatch.setattr(lifecycle_acl, "_plans", changed)
    with pytest.raises(
        owner56.NativeOwnerPregrant0056Error,
        match="^native_owner_0056_pregrant_denied$",
    ):
        owner56.exact_42_grant_sql_0056(**_NAMES)


@pytest.mark.asyncio
async def test_unsealed_references_refuse_before_source_or_database(monkeypatch):
    observed = []

    class ForbiddenFactory:
        def __call__(self):
            observed.append("opened")
            raise AssertionError("database must not open")

    monkeypatch.setattr(
        owner56.fitted56,
        "pinned_migration",
        lambda source: observed.append("read_source"),
    )
    assert owner56.EXPECTED_0056_OWNER_INHERITED_NATIVE_SHA256 == ""
    assert owner56.fitted56.EXPECTED_NATIVE_REFERENCE_SHA256 == ""
    with pytest.raises(
        owner56.NativeOwnerPregrant0056Error,
        match="^native_owner_0056_independent_reference_unsealed$",
    ):
        await owner56.observe_owner_pregrant_0056(
            ForbiddenFactory(), source=Path("unused"), **_NAMES
        )
    assert observed == []


@pytest.mark.asyncio
async def test_one_sealed_reference_is_still_a_denial(monkeypatch):
    monkeypatch.setattr(
        owner56, "EXPECTED_0056_OWNER_INHERITED_NATIVE_SHA256", "a" * 64
    )
    with pytest.raises(
        owner56.NativeOwnerPregrant0056Error,
        match="^native_owner_0056_independent_reference_unsealed$",
    ):
        await owner56.observe_owner_pregrant_0056(
            object(), source=Path("unused"), **_NAMES
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("granted", [False, True])
async def test_acl_phase_checks_full_exact_plan_and_control_guard(monkeypatch, granted):
    plan = owner56._grant_objects(**_NAMES)
    role_oids = dict(zip(_NAMES.values(), (11, 12, 13), strict=True))
    expected = core_acl._expected_acl(plan, role_oids, 99)
    targets = {key: index for index, key in enumerate(expected, start=1)}
    observed = []

    async def target_oids(connection, asked, owner, *, granted):
        assert connection is observed_connection
        assert asked == expected and owner == 99
        observed.append(("targets", granted))
        return targets

    async def access(connection, asked, roles, *, granted, targets):
        assert connection is observed_connection
        assert asked == plan and roles == role_oids
        assert targets == expected_targets
        observed.append(("access", granted))

    async def relations(connection, asked, owner):
        assert connection is observed_connection and owner == 99
        observed.append(("relations", frozenset(asked)))
        return asked

    guard = AsyncMock()
    observed_connection = object()
    expected_targets = targets
    monkeypatch.setattr(core_acl, "_target_oids", target_oids)
    monkeypatch.setattr(core_acl, "_access", access)
    monkeypatch.setattr(account_acl, "_delta_relations", relations)
    monkeypatch.setattr(lifecycle_acl, "_guard", guard)

    result = await owner56._exact_acl_phase(
        observed_connection,
        role_oids,
        99,
        **_NAMES,
        granted=granted,
    )
    assert result == targets
    assert observed[:2] == [("targets", granted), ("access", granted)]
    assert len([item for item in observed if item[0] == "relations"]) == 2
    guard.assert_awaited_once()


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def one(self):
        return self.value


class _OwnerSession:
    def __init__(self, state):
        self.state = state

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, parameters=None):
        query = str(statement)
        self.state["queries"].append(query)
        if query == owner56.inherited56.OWNER_CATALOG_SQL:
            values = self.state["graph"]
            return _ScalarResult(values.pop(0))
        if query == str(owner56.core_acl._OWNER_POLICY_SQL):
            assert parameters == {"owner": 101}
            return _ScalarResult((True, True))
        assert query.startswith("SET ")
        return None

    async def connection(self):
        return self

    async def scalar(self, statement):
        if statement is owner56.roles56.current55._OWNER_OID_SQL:
            return 101
        assert str(statement) == (
            "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
        )
        return "1234567890123456789"


class _OwnerFactory:
    def __init__(self, state):
        self.state = state
        self.kw = {
            "bind": SimpleNamespace(
                dialect=SimpleNamespace(name="postgresql", driver="asyncpg"),
                url=SimpleNamespace(username="owner"),
            )
        }

    @asynccontextmanager
    async def __call__(self):
        yield _OwnerSession(self.state)


@pytest.mark.asyncio
@pytest.mark.parametrize("readwrite", [False, True])
async def test_graph_diagnostic_repeats_actual_owner_and_denied_acl(
    monkeypatch, readwrite
):
    state = {"queries": [], "graph": [{"same": "graph"}] * 2}
    identity = ("owner", "owner", "none", 101, 93)
    owner = _OwnerFactory(state)
    role_probe = AsyncMock(return_value=identity)
    acl = AsyncMock()
    monkeypatch.setattr(owner56.roles56, "_identity_at_0056", role_probe)
    monkeypatch.setattr(
        owner56.inherited56,
        "observe_owner_installation_0056",
        lambda graph: {
            "database_head": "0056",
            "role_oid": 101,
            "native_definitions_sha256": "a" * 64,
            "native_baseline_admission": False,
            "execution_authority": False,
        },
    )
    monkeypatch.setattr(
        owner56.inherited56,
        "observe_owner_readwrite_installation_0056",
        owner56.inherited56.observe_owner_installation_0056,
        raising=False,
    )
    monkeypatch.setattr(
        owner56.core_acl,
        "_roles",
        AsyncMock(
            return_value=dict(zip(_NAMES.values(), (201, 202, 203), strict=True))
        ),
    )
    monkeypatch.setattr(owner56, "_exact_acl_phase", acl)
    actual = await owner56._read_owner_pregrant_graph_0056(
        owner, "owner", tuple(_NAMES.values()), readwrite=readwrite
    )
    assert actual[0] == identity and actual[1] == "a" * 64
    assert len(actual[2]) == 64
    assert actual[3] == owner56.sha(b"1234567890123456789")
    assert state["queries"].count(owner56.inherited56.OWNER_CATALOG_SQL) == 2
    expected_mode = (
        "SET TRANSACTION READ WRITE" if readwrite else "SET TRANSACTION READ ONLY"
    )
    assert state["queries"].index(expected_mode) < state["queries"].index(
        owner56.inherited56.OWNER_CATALOG_SQL
    )
    assert role_probe.await_count == 2
    assert acl.await_args.kwargs["granted"] is False


@pytest.mark.asyncio
async def test_fitted_diagnostic_uses_real_pregrant_validator_contract(monkeypatch):
    state = {"queries": [], "graph": []}
    owner = _OwnerFactory(state)
    identity = ("owner", "owner", "none", 101, 93)
    monkeypatch.setattr(
        owner56.roles56, "_identity_at_0056", AsyncMock(return_value=identity)
    )
    catalog = {"same": "fitted"}
    reads = [catalog, catalog]

    async def read_catalog(_session, query):
        assert query == owner56.fitted56.CATALOG_SQL
        return reads.pop(0)

    def normalize(document, **kwargs):
        assert document == catalog
        assert kwargs == {
            "owner": "owner",
            "demo_roles": tuple(_NAMES.values()),
            "fitted_roles": None,
        }
        return {"normalized": True}

    def verify(document, *, pregrant):
        assert document == {"normalized": True} and pregrant is True
        return "c" * 64

    monkeypatch.setattr(owner56.roles56, "_catalog", read_catalog)
    monkeypatch.setattr(
        owner56.fitted56, "inspect_catalog_document", lambda *_args, **_kw: "b" * 64
    )
    monkeypatch.setattr(owner56.fitted_pair, "normalized_catalog_document", normalize)
    monkeypatch.setattr(
        owner56.fitted_pair,
        "verify_normalized_catalog_document",
        verify,
    )
    actual = await owner56._read_fitted_owner_pregrant_catalog_0056(
        owner, "owner", tuple(_NAMES.values())
    )
    assert actual == (identity, "b" * 64, "c" * 64)
    assert "SET TRANSACTION READ ONLY" in state["queries"]


@pytest.mark.asyncio
async def test_isolated_pregrant_diagnostic_returns_only_inert_hashes(monkeypatch):
    state = {"queries": [], "graph": []}
    owner = _OwnerFactory(state)
    monkeypatch.setattr(owner56, "async_sessionmaker", _OwnerFactory)
    monkeypatch.setattr(owner56, "AsyncEngine", SimpleNamespace)
    monkeypatch.setattr(owner56.fitted56, "pinned_migration", lambda source: None)
    identity = ("owner", "owner", "none", 101, 93)
    events = []

    async def fitted(*_args):
        events.append("fitted")
        return identity, "b" * 64, "c" * 64

    async def inherited(*_args, **kwargs):
        events.append("inherited-readwrite" if kwargs.get("readwrite") else "inherited")
        return identity, "a" * 64, "d" * 64, "e" * 64

    monkeypatch.setattr(owner56, "_read_fitted_owner_pregrant_catalog_0056", fitted)
    monkeypatch.setattr(owner56, "_read_owner_pregrant_graph_0056", inherited)
    receipt = await owner56.observe_owner_pregrant_diagnostic_0056(
        owner, source=Path("synthetic"), **_NAMES
    )
    assert events == [
        "fitted",
        "inherited",
        "inherited-readwrite",
        "fitted",
        "inherited",
    ]
    assert receipt.inherited_native_definitions_sha256 == "a" * 64
    assert receipt.inherited_readwrite_native_definitions_sha256 == "a" * 64
    assert receipt.inherited_raw_owner_graph_sha256 == "d" * 64
    assert receipt.cluster_system_identifier_sha256 == "e" * 64
    assert receipt.fitted_raw_owner_catalog_sha256 == "b" * 64
    assert receipt.fitted_normalized_owner_catalog_sha256 == "c" * 64
    assert receipt.grant_statements == 42 and receipt.grants_present is False
    assert receipt.fitted_normalized_reference_available is False
    assert receipt.actual_measurement_independently_attested is False
    assert receipt.native_reference_sealed is receipt.startup_admission is False
    assert receipt.execution_authority is False and receipt.admission == "DENY"
    assert state["queries"] == []


@pytest.mark.asyncio
async def test_isolated_pregrant_diagnostic_denies_cross_database_identity(monkeypatch):
    owner = _OwnerFactory({"queries": [], "graph": []})
    monkeypatch.setattr(owner56, "async_sessionmaker", _OwnerFactory)
    monkeypatch.setattr(owner56, "AsyncEngine", SimpleNamespace)
    monkeypatch.setattr(owner56.fitted56, "pinned_migration", lambda source: None)
    good = ("owner", "owner", "none", 101, 93)
    changed = ("owner", "owner", "none", 101, 94)
    monkeypatch.setattr(
        owner56,
        "_read_fitted_owner_pregrant_catalog_0056",
        AsyncMock(return_value=(good, "b" * 64, "c" * 64)),
    )
    monkeypatch.setattr(
        owner56,
        "_read_owner_pregrant_graph_0056",
        AsyncMock(return_value=(changed, "a" * 64, "d" * 64, "e" * 64)),
    )
    with pytest.raises(
        owner56.NativeOwnerPregrant0056Error,
        match="^native_owner_0056_pregrant_diagnostic_denied$",
    ):
        await owner56.observe_owner_pregrant_diagnostic_0056(
            owner, source=Path("synthetic"), **_NAMES
        )
