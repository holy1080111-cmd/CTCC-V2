"""Offline source inventory only; no catalog or actor acceptance."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import op
from alembic.migration import MigrationContext

from scripts import paper58_effective_ddl_v1 as source


def test_sql_splitter_preserves_dollar_bodies_quoted_semicolons_and_all_bytes():
    sql = (
        "  CREATE FUNCTION public.first() RETURNS void LANGUAGE plpgsql "
        "AS $body$ BEGIN PERFORM ';CREATE FUNCTION fake()'; END; $body$;\n"
        "/* outer /* nested ; */ comment */ SELECT 'a'';b';  "
    )
    pieces = source._statements(sql)
    assert len(pieces) == 2 and "".join(pieces) == sql
    assert pieces[0].endswith("$body$;")


@pytest.mark.parametrize("sql", ["SELECT 'open", "SELECT $body$open", "/* open"])
def test_unclosed_source_sql_refuses_without_guessing(sql):
    with pytest.raises(ValueError, match="paper58_source_ddl_unresolved"):
        source._statements(sql)


def test_latest_source_override_retains_both_bodies_and_source_provenance():
    before = (
        "CREATE FUNCTION public.sample() RETURNS int LANGUAGE sql AS $$ SELECT 1; $$;"
    )
    after = "CREATE OR REPLACE FUNCTION public.sample() RETURNS int LANGUAGE sql AS $$ SELECT 2; $$;"
    emitted = [
        {"revision": "0056", "status": "SOURCE_SQL_EMITTED", "sql": before},
        {"revision": "0057", "status": "SOURCE_SQL_EMITTED", "sql": after},
    ]
    functions, triggers, versions, unresolved = source._registry(emitted)
    assert functions["sample"]["source_sql"] == after
    assert functions["sample"]["revision"] == "0057"
    assert [item["source_sql"] for item in versions] == [before, after]
    assert not triggers and not unresolved


def test_unsupported_drop_or_overload_history_stays_explicitly_unresolved():
    statements = (
        "CREATE FUNCTION public.sample() RETURNS int LANGUAGE sql AS $$ SELECT 1; $$;"
        "CREATE FUNCTION public.sample(x int) RETURNS int LANGUAGE sql AS $$ SELECT x; $$;"
        "DROP FUNCTION public.sample();"
        'CREATE FUNCTION public."quoted"() RETURNS int LANGUAGE sql AS $$ SELECT 1; $$;'
    )
    result = source._registry(
        [{"revision": "0058", "status": "SOURCE_SQL_EMITTED", "sql": statements}]
    )
    assert len(result[3]) == 3
    assert {item["reason"] for item in result[3]} == {
        "overload_or_drop_history_requires_actual_catalog",
        "object_change_requires_catalog_resolution",
    }


def test_trigger_identity_keeps_its_relation_and_ignores_ddl_inside_body_strings():
    emitted = [
        {
            "revision": "0058",
            "status": "SOURCE_SQL_EMITTED",
            "sql": (
                "CREATE FUNCTION public.body() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'CREATE TRIGGER fake ON wrong'; END; $$;"
                "CREATE TRIGGER actual BEFORE INSERT ON public.target "
                "FOR EACH ROW EXECUTE FUNCTION public.body();"
            ),
        }
    ]
    functions, triggers, versions, unresolved = source._registry(emitted)
    assert set(functions) == {"body"}
    assert set(triggers) == {"target.actual"}
    assert triggers["target.actual"]["table"] == "target"
    assert len(versions) == 2 and not unresolved


def test_connection_getter_refuses_instead_of_supplying_a_catalog_result():
    observed = []

    def upgrade():
        op.execute(
            "CREATE FUNCTION public.partial() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"
        )
        # No connection, scalar method, role flag, or result is supplied here.
        observed.append(op.get_bind().scalar("SELECT role_metadata"))

    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True}
    )
    with pytest.raises(source.SourceObservationRequired):
        source._emit_revision(context, SimpleNamespace(upgrade=upgrade))
    assert observed == []
    # The caller discards the ENTIRE unresolved revision, including partial DDL.
    functions, triggers, versions, unresolved = source._registry(
        [{"revision": "0055", "status": "ACTUAL_CATALOG_REQUIRED", "sql": ""}]
    )
    assert not functions and not triggers and not versions
    assert unresolved == [{"revision": "0055", "reason": "ACTUAL_CATALOG_REQUIRED"}]


def test_real_source_generation_has_no_env_settings_connection_or_fabricated_rows():
    root = Path(__file__).resolve().parents[2]
    code = r"""
import os, sys
root = os.environ.get('SystemRoot')
os.environ.clear()
if root is not None:
    os.environ['SystemRoot'] = root
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event in ('socket.connect', 'subprocess.Popen', 'sqlite3.connect'):
        raise RuntimeError('source_generation_attempted_external_resource')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        name = os.fsdecode(args[0]).replace('\\', '/').rsplit('/', 1)[-1].lower()
        if name == '.env' or name.startswith('.env.'):
            raise RuntimeError('source_generation_attempted_environment_file')
sys.addaudithook(audit)
from scripts.paper58_effective_ddl_v1 import effective_source_ddl_v1
value = effective_source_ddl_v1()
assert not {'app.main','app.database.session','app.paper.service','app.config.settings'} & set(sys.modules)
assert value['database_connections'] == value['observed_query_results_supplied'] == 0
assert value['startup_admitted'] is value['execution_authority'] is False
assert value['complete_effective_catalog'] is False
assert {item['revision'] for item in value['unresolved'] if item['reason'] == 'ACTUAL_CATALOG_REQUIRED'} == {'0045','0049','0050','0051','0052','0053','0054','0055'}
assert value['latest_source_function_definitions']['gate3_fitted_role']['revision'] == '0056'
assert value['latest_source_function_definitions']['native_live_reservation_guard_v1']['revision'] == '0058'
import json
print(json.dumps({'functions':len(value['latest_source_function_definitions']), 'triggers':len(value['latest_source_trigger_definitions']), 'sources':len(value['source_files']), 'revisions':len(value['revision_status'])}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root)],
        cwd=root,
        env={
            name: value
            for name, value in os.environ.items()
            if name in {"SystemRoot", "SYSTEMROOT"}
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["functions"] >= 100 and observed["triggers"] >= 100
    assert observed["sources"] == 90 and observed["revisions"] == 58
