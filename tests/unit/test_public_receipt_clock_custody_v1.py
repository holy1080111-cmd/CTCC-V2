"""Synthetic catalog/native-API rejection tests, never actual PostgreSQL proof."""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import pickle
import re
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_receipt_clock_custody_v1 as custody
from app.database.repositories.public_receipt_clock_catalog_v1 import (
    CHECKS,
    COLUMNS,
    FUNCTION_HASHES,
    FUNCTION_METADATA,
    INDEXES,
    KEYS,
    TABLES,
    WITNESS,
    CatalogExpressionError,
    expression_tree,
)
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)

ROOT = Path(__file__).resolve().parents[2]


def source_bodies():
    bodies = {}
    for name in (
        "0024_public_receipt_witness.py",
        "0035_public_receipt_post_read_observation.py",
    ):
        module = ast.parse((ROOT / "migrations/versions" / name).read_bytes())
        for node in ast.walk(module):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
            ):
                sql = node.args[0].value
                match = re.search(r"CREATE FUNCTION (?:public\.)?(\w+)", sql)
                if match:
                    bodies[match.group(1)] = sql.split("$$")[1]
    module = ast.parse(
        (
            ROOT / "migrations/versions/0047_public_witness_native_identity.py"
        ).read_bytes()
    )
    selected = [
        node
        for node in module.body
        if isinstance(node, (ast.Assign, ast.For))
        or isinstance(node, ast.FunctionDef)
        and node.name in ("_replace_once", "_replace_function")
    ]
    scope = {}
    exec(  # noqa: S102 -- Reviewed fixed migration AST string derivation, no upgrade.
        compile(
            ast.Module(selected, type_ignores=[]),
            "<reviewed-migration-string-derivation>",
            "exec",
        ),
        scope,
    )
    for key in ("_INSERT", "_APPEND"):
        sql = scope[key]
        bodies[re.search(r"FUNCTION public\.(\w+)", sql).group(1)] = sql.split("$$")[1]
    return bodies


def catalog_pair():
    """Explicit synthetic complete catalog, actual migration prosrc strings."""
    bodies = source_bodies()
    function_ids = {name: 700 + index for index, name in enumerate(bodies)}
    index_ids = {
        name: 1000 + index
        for index, name in enumerate(
            name for table in TABLES for name in INDEXES[table]
        )
    }
    catalogs = []
    for kind, login in (("witness", 300), ("observation", 301)):
        catalog = {name: [] for name in custody._QUERIES}
        catalog["role"] = {
            "direct_login": True,
            "login_oid": login,
            "privileged": False,
            "member_other": False,
            "table_access": False,
            "can_create": False,
            "fsync": "on",
            "full_page_writes": "on",
            "synchronous_commit": "on",
            "replication_role": "origin",
            "catalog_search_path": "pg_catalog",
            "recovery": False,
            "server_version_num": "170011",
            "database_oid": 500,
            "database_name": "synthetic_catalog",
            "server_address": "127.0.0.1/32",
            "server_port": 55432,
            "server_started": datetime(2026, 1, 1, tzinfo=UTC),
        }
        for table in TABLES:
            catalog["tables"].append(
                {
                    "relname": table,
                    "table_oid": 600 if table == WITNESS else 601,
                    "owner_oid": 400,
                    "relkind": "r",
                    "persistence": "p",
                    "rls": False,
                    "force_rls": False,
                    "relispartition": False,
                    "replica_identity": "d",
                    "inherited": False,
                    "foreign_acl": False,
                    "foreign_column_acl": False,
                }
            )
            for ordinal, (name, type_name, notnull) in enumerate(COLUMNS[table], 1):
                catalog["columns"].append(
                    {
                        "relname": table,
                        "attnum": ordinal,
                        "attname": name,
                        "type_name": type_name,
                        "type_namespace": "pg_catalog",
                        "collation_oid": 100
                        if type_name == "text"
                        or type_name.startswith("character varying")
                        else 0,
                        "collation_name": "default"
                        if type_name == "text"
                        or type_name.startswith("character varying")
                        else None,
                        "collation_namespace": "pg_catalog"
                        if type_name == "text"
                        or type_name.startswith("character varying")
                        else None,
                        "atttypmod": int(type_name.split("(")[1][:-1]) + 4
                        if type_name.startswith("character varying")
                        else -1,
                        "attnotnull": notnull,
                        "attisdropped": False,
                        "identity": "",
                        "generated": "",
                        "default_expression": "clock_timestamp()"
                        if table == WITNESS and name == "recorded_at"
                        else None,
                        "unsafe_default": False,
                    }
                )
            prefix = (
                "public_receipt_witness"
                if table == WITNESS
                else "public_receipt_post_read"
            )
            for suffix, code in (
                ("_insert_guard", 7),
                ("_immutable", 27),
                ("_no_truncate", 34),
            ):
                catalog["triggers"].append(
                    {
                        "relname": table,
                        "trigger_oid": 800 + len(catalog["triggers"]),
                        "function_oid": function_ids[
                            prefix
                            + (
                                "_insert_guard"
                                if suffix == "_insert_guard"
                                else "_immutable"
                            )
                        ],
                        "tgname": prefix + suffix,
                        "tgtype": code,
                        "enabled": "O",
                        "tgisinternal": False,
                        "constraint_oid": 0,
                        "tgnargs": 0,
                        "tgargs": b"",
                        "qualification": None,
                        "tgoldtable": None,
                        "tgnewtable": None,
                        "proname": prefix
                        + (
                            "_insert_guard"
                            if suffix == "_insert_guard"
                            else "_immutable"
                        ),
                        "function_namespace": "public",
                        "tgdeferrable": False,
                        "tginitdeferred": False,
                    }
                )
            for name in (*CHECKS[table], *KEYS[table]):
                constraint_kind, keys = (
                    ("c", None) if name in CHECKS[table] else KEYS[table][name]
                )
                is_fk = constraint_kind == "f"
                catalog["constraints"].append(
                    {
                        "relname": table,
                        "constraint_oid": 900 + len(catalog["constraints"]),
                        "index_oid": index_ids["pk_public_receipt_witness_revisions"]
                        if is_fk
                        else index_ids[name]
                        if constraint_kind in ("p", "u")
                        else 0,
                        "conname": name,
                        "kind": constraint_kind,
                        "convalidated": True,
                        "condeferrable": False,
                        "condeferred": False,
                        "connoinherit": constraint_kind != "c",
                        "conislocal": True,
                        "coninhcount": 0,
                        "conkey": sorted(
                            custody._check_columns(
                                expression_tree(CHECKS[table][name], table), table
                            )
                        )
                        if constraint_kind == "c"
                        else keys,
                        "confkey": [1, 2] if is_fk else None,
                        "referenced_table": WITNESS if is_fk else None,
                        "referenced_namespace": "public" if is_fk else None,
                        "update_action": "a" if is_fk else " ",
                        "delete_action": "a" if is_fk else " ",
                        "match_type": "s" if is_fk else " ",
                        "expression": CHECKS[table].get(name),
                        "unsafe_dependencies": False,
                        "fk_triggers": sorted(
                            [
                                [f, "pg_catalog", t, "O", 0, "", True, False, False]
                                for f, t in (
                                    ("RI_FKey_check_ins", 5),
                                    ("RI_FKey_check_upd", 17),
                                    ("RI_FKey_noaction_del", 9),
                                    ("RI_FKey_noaction_upd", 17),
                                )
                            ]
                        )
                        if is_fk
                        else [],
                    }
                )
            for name, (keys, opclasses, predicate) in INDEXES[table].items():
                catalog["indexes"].append(
                    {
                        "relname": table,
                        "index_name": name,
                        "index_oid": index_ids[name],
                        "owner_oid": 400,
                        "relkind": "i",
                        "persistence": "p",
                        "index_namespace": "public",
                        "amname": "btree",
                        "indisunique": True,
                        "indisprimary": name.startswith("pk_"),
                        "indisexclusion": False,
                        "indisvalid": True,
                        "indisready": True,
                        "indislive": True,
                        "indimmediate": True,
                        "indnatts": len(keys),
                        "indnkeyatts": len(keys),
                        "keys": keys,
                        "options": [0] * len(keys),
                        "expression": None,
                        "predicate": predicate,
                        "opclasses": [[op, "pg_catalog"] for op in opclasses],
                        "collations": [
                            "default" if op == "text_ops" else "none"
                            for op in opclasses
                        ],
                    }
                )
        for name, body in bodies.items():
            definer, language, result, returns_set, args, volatility = (
                FUNCTION_METADATA[name]
            )
            appropriate = (kind == "witness") == name.startswith(
                "public_receipt_witness_"
            )
            grantee = 300 if name.startswith("public_receipt_witness_") else 301
            catalog["functions"].append(
                {
                    "proname": name,
                    "function_oid": function_ids[name],
                    "prosrc": body,
                    "prosecdef": definer,
                    "lanname": language,
                    "return_type": result,
                    "return_namespace": "public" if result in TABLES else "pg_catalog",
                    "proretset": returns_set,
                    "argtypes": args,
                    "volatility": volatility,
                    "proconfig": ["search_path=pg_catalog, pg_temp"],
                    "owner_oid": 400,
                    "proisstrict": False,
                    "parallel": "u",
                    "proleakproof": False,
                    "kind": "f",
                    "support": 0,
                    "pronargdefaults": 0,
                    "variadic": 0,
                    "proargmodes": None,
                    "argument_namespaces": ["pg_catalog"]
                    * (len(args.split(", ")) if args else 0),
                    "can_execute": name.endswith(("_append", "_read")) and appropriate,
                    "acl": sorted(
                        [[400, 400, "EXECUTE", False]]
                        + (
                            [[grantee, 400, "EXECUTE", False]]
                            if name.endswith(("_append", "_read"))
                            else []
                        )
                    ),
                }
            )
        catalogs.append(catalog)
    return catalogs


def test_function_body_pins_are_exact_reviewed_migration_sources():
    assert {
        key: hashlib.sha256(value.encode()).hexdigest()
        for key, value in source_bodies().items()
    } == FUNCTION_HASHES


def test_check_policy_is_exact_migration_expression_not_a_db_learned_baseline():
    observed = {}
    paths = (
        "0024_public_receipt_witness.py",
        "0035_public_receipt_post_read_observation.py",
        "0047_public_witness_native_identity.py",
    )
    for path in paths:
        module = ast.parse((ROOT / "migrations/versions" / path).read_bytes())
        scope = {
            "DEVICE_MAX": "18446744073709551615",
            "INODE_MAX": "340282366920938463463374607431768211455",
        }
        for call in ast.walk(module):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute"
                and call.args
            ):
                continue
            arg = call.args[0]
            if isinstance(arg, ast.Constant) and type(arg.value) is str:
                sql = arg.value
            elif isinstance(arg, ast.JoinedStr) and any(
                isinstance(part, ast.Constant) and "ADD CONSTRAINT" in str(part.value)
                for part in arg.values
            ):
                sql = eval(
                    compile(ast.Expression(arg), "<fixed-migration-literal>", "eval"),
                    scope,
                )
            else:
                continue
            for match in re.finditer(r"CONSTRAINT\s+(\w+)\s+CHECK\s*\(", sql):
                start = match.end()
                depth = 1
                quoted = False
                cursor = start
                while depth:
                    char = sql[cursor]
                    if char == "'":
                        quoted = not quoted
                    elif not quoted:
                        depth += (char == "(") - (char == ")")
                    cursor += 1
                observed[match.group(1)] = sql[start : cursor - 1]
    expected = {
        name: (table, raw)
        for table, checks in CHECKS.items()
        for name, raw in checks.items()
    }
    assert set(observed) == set(expected)
    for name, (table, raw) in expected.items():
        assert expression_tree(observed[name], table) == expression_tree(raw, table)


def test_synthetic_catalog_only_passes_pure_validator_never_mints_owner():
    assert custody._validate_pair(catalog_pair()) is None
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        custody.OwnedClockCustodyDeploymentV1(object())
    assert not custody._OWNERS


@pytest.mark.parametrize(
    "category,index,field,value",
    [
        ("role", None, "direct_login", False),
        ("role", None, "privileged", True),
        ("role", None, "member_other", True),
        ("role", None, "table_access", True),
        ("role", None, "can_create", True),
        ("role", None, "fsync", "off"),
        ("role", None, "full_page_writes", "off"),
        ("role", None, "synchronous_commit", "off"),
        ("role", None, "replication_role", "replica"),
        ("role", None, "catalog_search_path", "public,pg_catalog"),
        ("role", None, "login_oid", True),
        ("role", None, "database_oid", 500.0),
        ("role", None, "server_version_num", "170012"),
        ("tables", 0, "owner_oid", 300),
        ("tables", 0, "foreign_acl", True),
        ("tables", 0, "foreign_column_acl", True),
        ("tables", 0, "rls", True),
        ("tables", 0, "persistence", "u"),
        ("tables", 0, "inherited", True),
        ("functions", 0, "prosrc", "BEGIN RETURN NEW; END"),
        ("functions", 0, "proconfig", ["search_path=public, pg_catalog"]),
        ("functions", 0, "owner_oid", 400.0),
        ("functions", 0, "return_namespace", "shadow"),
        ("functions", 0, "prosecdef", True),
        ("functions", 0, "can_execute", True),
        ("functions", 0, "acl", [[0, 400, "EXECUTE", False]]),
        ("functions", 0, "argument_namespaces", ["shadow"]),
        ("functions", 0, "support", False),
        ("columns", 0, "type_namespace", "shadow"),
        ("columns", 0, "attnotnull", 1),
        ("columns", 0, "unsafe_default", True),
        ("columns", 3, "type_name", "numeric(20,0)"),
        ("columns", 3, "atttypmod", 0),
        ("triggers", 0, "enabled", "D"),
        ("triggers", 0, "enabled", "A"),
        ("triggers", 0, "qualification", "true"),
        ("triggers", 0, "function_namespace", "shadow"),
        ("triggers", 0, "tgtype", True),
        ("constraints", 0, "convalidated", False),
        ("constraints", 0, "condeferrable", True),
        ("constraints", 0, "unsafe_dependencies", True),
        ("indexes", 0, "indisvalid", False),
        ("indexes", 0, "owner_oid", False),
        ("indexes", 0, "opclasses", [["shadow_ops", "public"]]),
        ("indexes", 0, "options", [1, 0]),
        ("indexes", 0, "keys", [1, 3]),
    ],
)
def test_catalog_drift_denied(category, index, field, value):
    pair = catalog_pair()
    target = pair[0][category] if index is None else pair[0][category][index]
    target[field] = value
    with pytest.raises(
        (custody.PublicReceiptClockCustodyError, CatalogExpressionError)
    ):
        custody._validate_pair(pair)


@pytest.mark.parametrize(
    "category", ["tables", "functions", "columns", "triggers", "constraints", "indexes"]
)
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown_field"])
def test_exact_inventories_missing_duplicate_unknown_denied(category, mutation):
    pair = catalog_pair()
    rows = pair[0][category]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    else:
        rows[0]["unknown"] = False
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        custody._validate_pair(pair)


@pytest.mark.parametrize(
    "change",
    [
        "unsigned_upper",
        "operator",
        "grouping",
        "foreign_cast",
        "fractional_rounding",
        "extra_predicate",
        "unknown_function",
        "unknown_column",
    ],
)
def test_constraint_source_semantics_not_relaxed(change):
    original = CHECKS[WITNESS][
        "ck_public_receipt_witness_revisions_native_identity_bounds"
    ]
    modified = {
        "unsigned_upper": original.replace(
            "18446744073709551615", "18446744073709551616"
        ),
        "operator": original.replace("root_device=trunc", "root_device<>trunc"),
        "grouping": original.replace(" AND ", " OR ", 1),
        "foreign_cast": original.replace(
            "root_device=trunc(root_device)", "root_device::bigint=trunc(root_device)"
        ),
        "fractional_rounding": original.replace(
            "root_device=trunc(root_device)", "root_device=round(root_device)"
        ),
        "extra_predicate": original + " OR root_inode=0",
        "unknown_function": original.replace("trunc(", "public.trunc("),
        "unknown_column": original.replace("root_inode", "foreign_inode"),
    }[change]
    try:
        actual = expression_tree(modified, WITNESS)
    except CatalogExpressionError:
        return
    assert actual != expression_tree(original, WITNESS)


def test_pg_deparser_source_spellings_preserve_grouping_and_exact_unsigned_integers():
    assert expression_tree(
        "(transition)::text = ANY ((ARRAY['initialize'::character varying,'open_attempt'::character varying])::text[])",
        WITNESS,
    ) == expression_tree("transition IN ('initialize','open_attempt')", WITNESS)
    assert expression_tree(
        "(root_device >= 0::numeric) AND (root_device <= '18446744073709551615'::numeric)",
        WITNESS,
    ) == expression_tree("root_device BETWEEN 0 AND 18446744073709551615", WITNESS)
    assert expression_tree(
        "state='idle' AND (transition='initialize' OR transition='append_capture')",
        WITNESS,
    ) != expression_tree(
        "(state='idle' AND transition='initialize') OR transition='append_capture'",
        WITNESS,
    )


def repositories():
    engines = [
        create_async_engine(
            "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:1/synthetic",
            poolclass=NullPool,
        )
        for _ in range(2)
    ]
    factories = [
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        for engine in engines
    ]
    witness = PublicReceiptWitnessRepository(factories[0])
    return witness, PublicReceiptPostReadRepository(factories[1], witness)


def synthetic_readback(digest="a"):
    scope = custody.ClockCustodyScopeV1(
        500, "b" * 64, "c" * 64, 55432, "2026-01-01T00:00:00+00:00", "170011", 300, 400
    )
    return custody.ClockCustodyDeploymentReadbackV1(
        digest * 64, (scope, scope), ("d" * 64, "e" * 64)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "instance_hook",
        "factory_callback",
        "class_override",
        "foreign_observation_witness",
    ],
)
def test_native_preflight_rejects_ordinary_caller_hooks_before_any_db_io(
    monkeypatch, mutation
):
    witness, observation = repositories()
    called = []

    async def forbidden_collect(*_):
        called.append(True)
        raise AssertionError

    monkeypatch.setattr(custody, "_collect", forbidden_collect)
    if mutation == "instance_hook":
        witness.verify_role = lambda: None
    elif mutation == "factory_callback":
        witness.session_factory.kw["info"] = {"callback": lambda: None}
    elif mutation == "class_override":
        witness.session_factory.class_ = object
    else:
        observation.witness_repository = repositories()[0]

    async def run():
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            await custody.acquire_clock_custody_deployment_v1(
                witness_repository=witness, observation_repository=observation
            )

    asyncio.run(run())
    assert not called


def test_opaque_lifecycle_foreign_task_copy_expiry_drift_and_saved_dto_denied(
    monkeypatch,
):
    witness, observation = repositories()

    async def synthetic_collect(_state):
        return synthetic_readback()

    monkeypatch.setattr(custody, "_collect", synthetic_collect)

    async def run():
        owner = await custody.acquire_clock_custody_deployment_v1(
            witness_repository=witness, observation_repository=observation
        )
        facts = custody.readback_clock_custody_deployment_v1(owner)
        assert (
            facts.execution_authority
            is facts.absolute_utc_verified
            is facts.independently_protected
            is False
        )
        assert (
            facts.canonical_json_bytes() == synthetic_readback().canonical_json_bytes()
        )
        with pytest.raises(FrozenInstanceError):
            facts.scopes[0].login_oid = 301
        for call in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(custody.PublicReceiptClockCustodyError):
                call(owner)

        async def other_task():
            with pytest.raises(custody.PublicReceiptClockCustodyError):
                custody.readback_clock_custody_deployment_v1(owner)

        await asyncio.create_task(other_task())
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            custody.readback_clock_custody_deployment_v1(facts)
        assert (
            await custody.recheck_clock_custody_deployment_v1(
                owner, witness_repository=witness, observation_repository=observation
            )
            == facts
        )

        async def altered(_state):
            return synthetic_readback("f")

        monkeypatch.setattr(custody, "_collect", altered)
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            await custody.recheck_clock_custody_deployment_v1(
                owner, witness_repository=witness, observation_repository=observation
            )
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            custody.readback_clock_custody_deployment_v1(owner)
        monkeypatch.setattr(custody, "_collect", synthetic_collect)
        second = await custody.acquire_clock_custody_deployment_v1(
            witness_repository=witness, observation_repository=observation
        )
        custody._OWNERS[second].started -= custody._LIFETIME_NS + 1
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            custody.readback_clock_custody_deployment_v1(second)

    asyncio.run(run())


def test_native_async_session_await_is_bounded_no_owner_on_timeout(monkeypatch):
    witness, observation = repositories()

    async def hanging_execute(*_args, **_kwargs):
        await asyncio.Future()

    monkeypatch.setattr(AsyncSession, "execute", hanging_execute)

    async def run():
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            await custody.acquire_clock_custody_deployment_v1(
                witness_repository=witness, observation_repository=observation
            )

    asyncio.run(run())


def test_cancelled_recheck_poison_is_not_resumable(monkeypatch):
    witness, observation = repositories()

    async def synthetic_collect(_):
        return synthetic_readback()

    monkeypatch.setattr(custody, "_collect", synthetic_collect)

    async def run():
        owner = await custody.acquire_clock_custody_deployment_v1(
            witness_repository=witness, observation_repository=observation
        )

        async def cancelled(_):
            raise asyncio.CancelledError

        monkeypatch.setattr(custody, "_collect", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await custody.recheck_clock_custody_deployment_v1(
                owner, witness_repository=witness, observation_repository=observation
            )
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            custody.readback_clock_custody_deployment_v1(owner)

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        "different_database",
        "different_postmaster",
        "same_login",
        "extra_execute_grant",
        "grant_option",
        "disabled_fk_trigger",
        "wrong_constraint_key",
        "wrong_fk_index",
        "unknown_oid_type",
    ],
)
def test_pair_scope_grants_and_constraint_object_identity_drift_denied(change):
    pair = catalog_pair()
    if change == "different_database":
        pair[1]["role"]["database_oid"] += 1
    elif change == "different_postmaster":
        pair[1]["role"]["server_started"] = datetime(2026, 1, 2, tzinfo=UTC)
    elif change == "same_login":
        pair[1]["role"]["login_oid"] = 300
    elif change == "extra_execute_grant":
        pair[0]["functions"][0]["acl"].append([999, 400, "EXECUTE", False])
    elif change == "grant_option":
        pair[0]["functions"][0]["acl"][0][-1] = True
    elif change == "disabled_fk_trigger":
        next(row for row in pair[0]["constraints"] if row["kind"] == "f")[
            "fk_triggers"
        ][0][3] = "D"
    elif change == "wrong_constraint_key":
        pair[0]["constraints"][0]["conkey"] = [1]
    elif change == "wrong_fk_index":
        next(row for row in pair[0]["constraints"] if row["kind"] == "f")[
            "index_oid"
        ] = 1001
    else:
        pair[0]["functions"][0]["function_oid"] = "700"
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        custody._validate_pair(pair)


def test_factory_and_instance_swap_during_real_native_session_await_denied(monkeypatch):
    witness, observation = repositories()
    calls = []

    async def altered_execute(*_args, **_kwargs):
        calls.append(True)
        witness.session_factory.kw["expire_on_commit"] = True

    monkeypatch.setattr(AsyncSession, "execute", altered_execute)

    async def run():
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            await custody.acquire_clock_custody_deployment_v1(
                witness_repository=witness, observation_repository=observation
            )

    asyncio.run(run())
    assert calls == [True]


def test_catalog_resolution_is_pinned_before_any_old_role_guard(monkeypatch):
    witness, observation = repositories()
    statements = []

    async def observe_execute(_session, statement, *_args, **_kwargs):
        statements.append(str(statement).strip())
        if len(statements) == 3:
            raise ValueError("synthetic_stop_before_any_DB_IO")

    monkeypatch.setattr(AsyncSession, "execute", observe_execute)

    async def run():
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            await custody.acquire_clock_custody_deployment_v1(
                witness_repository=witness, observation_repository=observation
            )

    asyncio.run(run())
    assert statements[:2] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL search_path = pg_catalog",
    ]


@pytest.mark.parametrize(
    "hook", ["json_deserializer", "json_serializer", "dialect_execute", "pool_invoke"]
)
def test_ordinary_native_instance_callbacks_rejected_before_private_io(
    monkeypatch, hook
):
    witness, observation = repositories()
    engine = witness.session_factory.kw["bind"]
    if hook.startswith("json_"):
        setattr(engine.dialect, "_" + hook, lambda *_: None)
    elif hook == "dialect_execute":
        engine.dialect.do_execute = lambda *_: None
    else:
        engine.sync_engine.pool._invoke_creator = lambda *_: None
    called = []

    async def forbidden(_):
        called.append(True)
        raise AssertionError

    monkeypatch.setattr(custody, "_collect", forbidden)

    async def run():
        with pytest.raises(custody.PublicReceiptClockCustodyError):
            await custody.acquire_clock_custody_deployment_v1(
                witness_repository=witness, observation_repository=observation
            )

    asyncio.run(run())
    assert not called


def test_whole_owned_acquire_collect_recheck_and_canonical_tail_synthetic_db_results(
    monkeypatch,
):
    """Actual private native Session/factory code, explicitly synthetic DB rows."""
    witness, observation = repositories()
    pair = catalog_pair()
    old_role = SimpleNamespace(
        direct_login=True,
        privileged=False,
        owner_member=False,
        member_of_other_role=False,
        direct_table_access=False,
        can_read=True,
        can_append=True,
        can_create=False,
        can_witness=False,
    )
    statements = []

    class Rows:
        def __init__(self, values):
            self.values = values

        def mappings(self):
            return self

        def all(self):
            return self.values

        def one_or_none(self):
            return old_role

    async def synthetic_execute(session, statement, *_args, **_kwargs):
        statements.append(str(statement))
        index = 0 if session.bind is witness.session_factory.kw["bind"] else 1
        for name, fixed in custody._QUERIES.items():
            if statement is fixed:
                value = pair[index][name]
                return Rows(
                    [copy.deepcopy(value)] if name == "role" else copy.deepcopy(value)
                )
        return Rows([])

    monkeypatch.setattr(AsyncSession, "execute", synthetic_execute)

    async def run():
        owner = await custody.acquire_clock_custody_deployment_v1(
            witness_repository=witness, observation_repository=observation
        )
        facts = custody.readback_clock_custody_deployment_v1(owner)
        assert all(
            type(scope.server_started) is str
            and scope.server_started == "2026-01-01T00:00:00+00:00"
            for scope in facts.scopes
        )
        assert facts.canonical_json_bytes()
        again = await custody.recheck_clock_custody_deployment_v1(
            owner, witness_repository=witness, observation_repository=observation
        )
        assert again.canonical_json_bytes() == facts.canonical_json_bytes()
        assert (
            facts.absolute_utc_verified
            is facts.host_binary_attested
            is facts.independently_protected
            is facts.execution_authority
            is False
        )

    asyncio.run(run())
    assert (
        len(statements) == 40
    )  # 2 SET + original role + seven catalog queries, two roles, twice.


@pytest.mark.parametrize(
    "index,field,value",
    [
        (5, "collation_name", "C"),
        (6, "collation_namespace", "public"),
        (5, "collation_oid", False),
        (5, "collation_oid", 100.0),
        (5, "collation_oid", 0),
        (5, "collation_name", None),
        (3, "collation_oid", 100),
        (3, "collation_oid", False),
        (3, "collation_oid", 0.0),
        (3, "collation_name", "default"),
        (3, "collation_namespace", "pg_catalog"),
    ],
)
def test_source_column_collation_is_exact_typed_even_on_nonindexed_text(
    index, field, value
):
    pair = catalog_pair()
    pair[0]["columns"][index][field] = value
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        custody._validate_pair(pair)
