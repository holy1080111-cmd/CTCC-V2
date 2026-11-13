"""Synthetic DB0056 version, frame/writer and same-backend composition tests."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import native_role_preflight_0056_full_diagnostic_v1 as subject
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as old_pair,
)
from app.database.repositories import (
    collector_current_0056_multirole_catalog_v1 as multirole,
)
from app.domain import collector_current_0055_catalog_v1 as old
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from tests.unit.test_controlled_history_current55_schema_v2 import _catalog


def test_exact_actual56_query_has_a_distinct_schema_and_no_head_override():
    old_tag = f"'schema','{old.SCHEMA}'"
    new_tag = f"'schema','{inherited.SCHEMA}'"
    assert old.ACTOR_CATALOG_SQL.count(old_tag) == 1
    assert inherited.ACTOR_CATALOG_SQL.count(new_tag) == 1
    assert (
        inherited.ACTOR_CATALOG_SQL.replace(new_tag, old_tag) == old.ACTOR_CATALOG_SQL
    )
    assert old.OWNER_CATALOG_SQL.count(old_tag) == 1
    assert inherited.OWNER_CATALOG_SQL.count(new_tag) == 1
    assert (
        inherited.OWNER_CATALOG_SQL.replace(new_tag, old_tag) == old.OWNER_CATALOG_SQL
    )


@pytest.mark.parametrize("reader", [False, True])
def test_controlled_history_keeps_exact_actor_and_head(reader):
    value = _catalog(reader=reader)
    actual_actor = 301 if reader else 201
    kwargs = {
        "owner": 101,
        "actor": actual_actor,
        "owner_mode": False,
        "reader_role_oid": 301 if reader else None,
    }
    with pytest.raises(inherited.history55.ControlledHistoryCurrent55Error):
        inherited.observe_controlled_history_0056(value, **kwargs)
    value["head"] = "0056"
    accepted = inherited.observe_controlled_history_0056(value, **kwargs)
    assert accepted["writer_role_oid"] == 201 and accepted["raw_role_oid"] == 301
    with pytest.raises(inherited.history55.ControlledHistoryCurrent55Error):
        inherited.history55.observe_catalog(value, **kwargs)
    with pytest.raises(inherited.history55.ControlledHistoryCurrent55Error):
        inherited.observe_controlled_history_0056(value, **{**kwargs, "actor": 999})


def _role(name, oid):
    return {
        "oid": oid,
        "name": name,
        "current_user": name,
        "session_user": name,
        "setting": "none",
        "login": True,
        "superuser": False,
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "membership": False,
        "create": False,
    }


def _context(role, *, schema, read_only, head="0056"):
    return {
        "schema_version": schema,
        "database_head": head,
        "actor": role["oid"],
        "table_oid": 501,
        "server": {
            "version_num": "170011",
            "current_user": role["name"],
            "session_user": role["name"],
            "role_setting": "none",
            "database": "synthetic",
            "timezone": "UTC",
            "search_path": "pg_catalog, public, pg_temp",
            "isolation": "read committed",
            "read_only": read_only,
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
            "replication_role": "origin",
            "recovery": False,
            "role_oid": role["oid"],
            "superuser": False,
            "can_login": True,
        },
        "parent_facts": [],
    }


@pytest.mark.parametrize("read_only", ["on", "off"])
def test_inherited_context_preserves_both_transaction_contracts(read_only):
    role = _role("synthetic", 201)
    value = _context(role, schema="fixed", read_only=read_only)
    kwargs = {
        "role": role,
        "read_only": read_only,
        "parents": frozenset(),
        "schema_name": "fixed",
    }
    assert inherited._context(value, **kwargs) == {}
    with pytest.raises(old.Current0055CatalogError):
        old._context(value, **kwargs)
    value["database_head"] = "0055"
    with pytest.raises(old.Current0055CatalogError):
        inherited._context(value, **kwargs)


def test_multirole_wire_rejects_legacy_schema_and_duplicate_role():
    raw = dict.fromkeys(old.CATALOG_KEYS)
    writer = dict.fromkeys(old.CATALOG_KEYS)
    raw.update(schema=old.SCHEMA, role=_role("raw", 301))
    writer.update(schema=old.SCHEMA, role=_role("writer", 201))
    kwargs = {
        "raw_name": "raw",
        "writer_name": "writer",
        "approved_raw_role_oid": 301,
        "approved_writer_role_oid": 201,
        "parent_owner_oid": 101,
    }
    with pytest.raises(old_pair.Current0055MultiRoleError):
        multirole.verify_0056_pair(raw, writer, **kwargs)
    raw["schema"] = writer["schema"] = inherited.SCHEMA
    with pytest.raises(old_pair.Current0055MultiRoleError):
        old_pair._verify_pair(
            raw,
            writer,
            raw_name="raw",
            writer_name="writer",
            approved_raw_role_oid=301,
            parent_owner_oid=101,
        )
    writer["role"]["oid"] = 301
    with pytest.raises(old_pair.Current0055MultiRoleError):
        multirole.verify_0056_pair(raw, writer, **kwargs)


def test_multirole_pairs_actual_roles_without_projecting_rows(monkeypatch):
    def graph(role, *, raw):
        data = dict.fromkeys(old.CATALOG_KEYS)
        history = _catalog(reader=raw)
        history["head"] = "0056"
        data.update(schema=inherited.SCHEMA, role=role, controlled_history55=history)
        shared = dict.fromkeys(old.BASE_KEYS)
        shared.update(owner=101, actor=role["oid"], table_ok=True, bad_table_acl=raw)
        data["lineage_base"] = shared
        for component, version, parents in (
            ("issued", "0054.actual_issued", old.PARENTS),
            ("lineage", "0053.actual_owner", old.old53.PARENTS),
        ):
            context = _context(
                role,
                schema=f"ctcc.collector_current{version}_catalog.v1",
                read_only="off" if raw else "on",
            )
            context["parent_facts"] = [
                {
                    "name": name,
                    "oid": index + 500,
                    "owner": 101,
                    "kind": "r",
                    "persistence": "p",
                    "rls": False,
                    "force_rls": False,
                }
                for index, name in enumerate(sorted(parents))
            ]
            data[component] = {
                **shared,
                **context,
                "native_definitions": {},
                "ri_triggers": [],
                "custody_incoming": [],
            }
        return data

    raw = graph(_role("synthetic_raw", 301), raw=True)
    writer = graph(_role("synthetic_writer", 201), raw=False)
    kwargs = {
        "raw_name": "synthetic_raw",
        "writer_name": "synthetic_writer",
        "approved_raw_role_oid": 301,
        "approved_writer_role_oid": 201,
        "parent_owner_oid": 101,
    }

    def writer_definitions(value):
        assert value is writer
        return {"native_definitions_sha256": "1" * 64}

    monkeypatch.setattr(inherited, "observe_writer_0056", writer_definitions)
    result = multirole.verify_0056_pair(raw, writer, **kwargs)
    assert result["actual_separate_roles_observed"] is True
    assert result["database_head"] == "0056"
    assert result["native_baseline_admission"] is result["execution_authority"] is False
    raw["issued"]["server"]["current_user"] = "synthetic_writer"
    with pytest.raises(old.Current0055CatalogError):
        multirole.verify_0056_pair(raw, writer, **kwargs)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def all(self):
        return self.value


class _Session:
    def __init__(self, name, state):
        self.name = name
        self.state = state

    @asynccontextmanager
    async def begin(self):
        self.state["events"].append((self.name, "begin"))
        yield

    async def execute(self, statement, parameters=None):
        text = str(statement)
        self.state["events"].append((self.name, text))
        if text == str(subject._HEAD):
            heads = self.state["heads"].get(self.name, ["0056"])
            tls = self.state["tls"].get(self.name, True)
            return _Result([(head, tls) for head in heads])
        if text == str(subject.current55._OWNER_OID_SQL):
            return _Result(101)
        if text == str(subject._FITTED_DEMO_ACCESS):
            assert parameters == {"roles": ["writer", "custody", "frame"]}
            rows = [
                (role, routine, False)
                for role in parameters["roles"]
                for routine in subject._FITTED_FUNCTION_NAMES
            ]
            fault = self.state.get("fitted_access_fault")
            if fault == "effective-execute":
                role, routine, _ = rows[0]
                rows[0] = (role, routine, True)
            elif fault == "missing-routine":
                rows.pop()
            elif fault == "unexpected-routine":
                role, _, _ = rows[0]
                rows[0] = (role, "gate3_fitted_unexpected", False)
            return _Result(rows)
        if text == subject.fitted.CATALOG_SQL:
            values = self.state["fitted"]
            return _Result(values.pop(0))
        if text == subject.partition_policy.CATALOG_SQL:
            return _Result({"tables": [{"owner_oid": 101}]})
        if text == subject.frame_policy.CATALOG_SQL:
            return _Result({"role": self.name})
        if text == inherited.ACTOR_CATALOG_SQL:
            return _Result({"schema": inherited.SCHEMA, "role": self.name})
        assert text.startswith("SET ")
        return None

    async def connection(self):
        return self


class _Engine:
    def __init__(self, url):
        self.url = url


class _Factory:
    def __init__(self, role, names, state):
        self.role = role
        self.kw = {"bind": _Engine("synthetic-url")}
        self.sessions = iter(_Session(name, state) for name in names)

    @asynccontextmanager
    async def __call__(self):
        yield next(self.sessions)


@pytest.fixture
def scenario(monkeypatch):
    state = {
        "events": [],
        "heads": {},
        "tls": {},
        "fitted": [{"same": "catalog"} for _ in range(4)],
        "db": {},
        "pair_override": {},
    }
    writer = _Factory("writer", ("writer", "writer-final"), state)
    carrier = _Factory("custody", ("custody",), state)
    frame = _Factory("frame", ("frame",), state)
    owner = _Factory("owner", ("owner", "owner-final"), state)
    selected = subject.selectors.ConfiguredNativeDemoFactoriesV2(
        writer, carrier, frame, carrier.kw["bind"], frame.kw["bind"]
    )

    async def settings(session, *, frame):
        state["events"].append((session.name, f"frame:{frame}"))

    async def identity(session, name):
        assert session.name.removesuffix("-final") == name
        state["events"].append((session.name, "identity"))
        return (
            name,
            name,
            "none",
            {"writer": 201, "custody": 211, "frame": 301, "owner": 101}[name],
            state["db"].get(session.name, 93),
            datetime(2026, 1, 1, tzinfo=UTC),
            170011,
            "synthetic",
            5432,
        )

    async def acl(session, names, role):
        assert session.name == "writer"
        assert names == ("writer", "custody", "frame")
        assert role == 201
        state["events"].append((session.name, "acl"))

    def fitted_catalog(document, **roles):
        assert roles == {
            "owner": "owner",
            "demo_roles": ("writer", "custody", "frame"),
            "fitted_roles": None,
        }
        return subject.sha(subject.canonical(document))

    def frame_catalog(frame_value, partition, *, approved_collector_role_oid):
        assert frame_value == {"role": "frame"}
        assert partition["tables"][0]["owner_oid"] == 101
        assert approved_collector_role_oid == 301
        state["events"].append(("frame", "strict-frame-policy"))

    def verify_pair(frame_value, writer_value, **roles):
        assert frame_value["role"] == "frame" and writer_value["role"] == "writer"
        assert roles == {
            "raw_name": "frame",
            "writer_name": "writer",
            "approved_raw_role_oid": 301,
            "approved_writer_role_oid": 201,
            "parent_owner_oid": 101,
        }
        state["events"].append(("writer+frame", "pair"))
        return {
            "schema": "ctcc.collector_current0056.observed_multirole_catalog.v1",
            "database_head": "0056",
            "actual_separate_roles_observed": True,
            "native_baseline_admission": False,
            "account_complete": False,
            "execution_authority": False,
            "native_definitions_sha256": "1" * 64,
            **state["pair_override"],
        }

    class _Custody:
        def __init__(self, factory):
            assert factory is carrier

        @staticmethod
        async def _catalog(session):
            assert session.name == "custody"
            state["events"].append((session.name, "raw-custody"))
            return "2" * 64

    monkeypatch.setattr(
        subject.native_factories, "_factory_route", lambda f: (None, None, "pg", f.role)
    )
    monkeypatch.setattr(subject.selectors, "_database_route", lambda url: url)
    monkeypatch.setattr(subject.current55, "_session_settings", settings)
    monkeypatch.setattr(subject.current55, "_identity", identity)
    monkeypatch.setattr(subject.current55, "_exact_existing_acl", acl)
    monkeypatch.setattr(subject.custody, "RuntimeBundleCustodyRepository", _Custody)
    monkeypatch.setattr(subject.fitted, "pinned_migration", lambda _: None)
    monkeypatch.setattr(subject.fitted, "inspect_catalog_document", fitted_catalog)
    monkeypatch.setattr(subject.frame_policy, "verify_catalog", frame_catalog)
    monkeypatch.setattr(
        subject.partition_policy,
        "verify_account_history_partition_catalog",
        lambda _: None,
    )
    monkeypatch.setattr(multirole, "verify_0056_pair", verify_pair)
    return state, selected, owner


@pytest.mark.asyncio
async def test_full_observation_binds_sessions_and_never_admits(scenario):
    state, selected, owner = scenario
    result = await subject.observe_native_roles_0056_v1(
        selected, owner_factory=owner, source=Path("synthetic")
    )
    assert result.schema == "ctcc.native_only.actual0056_role_observation.v1"
    assert result.database_head == "0056" and result.admission == "DENY"
    assert result.native_reference_sealed is result.native_baseline_admission is False
    assert result.startup_admission is result.execution_authority is False
    assert result.account_complete is False
    assert subject.EXPECTED_0056_NATIVE_REFERENCE_SHA256 == ""
    assert result.inherited_native_definitions_sha256 == "1" * 64
    assert result.raw_custody_catalog_sha256 == "2" * 64
    events = state["events"]
    assert events.count(("owner", subject.fitted.CATALOG_SQL)) == 2
    assert events.count(("owner-final", subject.fitted.CATALOG_SQL)) == 2
    assert events.index(("owner", "SET TRANSACTION READ ONLY")) < events.index(
        ("owner", subject.fitted.CATALOG_SQL)
    )
    assert ("frame", "frame:True") in events
    assert ("writer", "frame:False") in events
    assert ("writer+frame", "pair") in events
    assert events.index(("writer", "acl")) < events.index(
        ("writer", str(subject._FITTED_DEMO_ACCESS))
    )
    assert events.index(
        ("custody", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    ) < events.index(("custody", str(subject._HEAD)))
    assert events.index(("custody", str(subject._HEAD))) < events.index(
        ("custody", "raw-custody")
    )


@pytest.mark.asyncio
async def test_restricted_observation_uses_only_three_roles_and_never_admits(scenario):
    state, selected, _owner = scenario
    result = await subject.observe_restricted_native_roles_0056_v1(
        selected, source=Path("synthetic")
    )
    assert result.schema == "ctcc.native_only.restricted0056_role_observation.v1"
    assert result.database_head == "0056" and result.admission == "DENY"
    assert result.native_reference_sealed is result.native_baseline_admission is False
    assert result.startup_admission is result.execution_authority is False
    assert result.account_complete is False
    assert result.inherited_native_definitions_sha256 == "1" * 64
    assert result.raw_custody_catalog_sha256 == "2" * 64
    events = state["events"]
    assert all(name != "owner" and name != "owner-final" for name, _ in events)
    assert ("writer+frame", "pair") in events
    assert events.index(("writer", "acl")) < events.index(
        ("writer", str(subject._FITTED_DEMO_ACCESS))
    )
    assert events.index(("custody", str(subject._HEAD))) < events.index(
        ("custody", "raw-custody")
    )
    assert events.index(("custody", "raw-custody")) < events.index(
        ("writer-final", str(subject._HEAD))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "writer-head",
        "frame-head",
        "custody-head",
        "custody-tls",
        "custody-database",
        "effective-execute",
        "missing-routine",
        "unexpected-routine",
        "pair-native-admission",
        "route-mismatch",
        "duplicate-role",
    ],
)
async def test_restricted_observation_denies_changed_identity_or_authority(
    scenario, fault
):
    state, selected, _owner = scenario
    if fault.endswith("-head"):
        state["heads"][fault.removesuffix("-head")] = ["0055"]
    elif fault == "custody-tls":
        state["tls"]["custody"] = False
    elif fault == "custody-database":
        state["db"]["custody"] = 94
    elif fault in {"effective-execute", "missing-routine", "unexpected-routine"}:
        state["fitted_access_fault"] = fault
    elif fault == "pair-native-admission":
        state["pair_override"]["native_baseline_admission"] = True
    elif fault == "route-mismatch":
        selected.writer_factory.kw["bind"] = _Engine("different-url")
    else:
        selected.frame_reader_factory.role = "writer"
    with pytest.raises(
        subject.NativeRoles0056ObservationError,
        match="^native_roles_0056_observation_unavailable$",
    ):
        await subject.observe_restricted_native_roles_0056_v1(
            selected, source=Path("synthetic")
        )
    assert all(name != "owner" and name != "owner-final" for name, _ in state["events"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "fitted-first-read",
        "fitted-fresh-read",
        "owner-database",
        "custody-database",
        "frame-head",
        "writer-final-head",
        "owner-tls",
        "custody-tls",
        "frame-tls",
        "writer-tls",
        "old-pair-schema",
        "pair-native-admission",
        "effective-execute",
        "missing-routine",
        "unexpected-routine",
    ],
)
async def test_full_observation_fails_closed_on_drift(scenario, fault):
    state, selected, owner = scenario
    if fault == "fitted-first-read":
        state["fitted"][1] = {"catalog": "changed within transaction"}
    elif fault == "fitted-fresh-read":
        state["fitted"][2:] = [{"catalog": "changed on fresh read"}] * 2
    elif fault == "owner-database":
        state["db"]["owner"] = state["db"]["owner-final"] = 94
    elif fault == "custody-database":
        state["db"]["custody"] = 95
    elif fault in {"frame-head", "writer-final-head"}:
        state["heads"][fault.removesuffix("-head")] = ["0055"]
    elif fault.endswith("-tls"):
        state["tls"][fault.removesuffix("-tls")] = False
    elif fault == "old-pair-schema":
        state["pair_override"]["schema"] = (
            "ctcc.collector_current0055.actual_multirole_catalog.v1"
        )
    elif fault in {"effective-execute", "missing-routine", "unexpected-routine"}:
        state["fitted_access_fault"] = fault
    else:
        state["pair_override"]["native_baseline_admission"] = True
    with pytest.raises(
        subject.NativeRoles0056ObservationError,
        match="^native_roles_0056_observation_unavailable$",
    ):
        await subject.observe_native_roles_0056_v1(
            selected, owner_factory=owner, source=Path("synthetic")
        )
