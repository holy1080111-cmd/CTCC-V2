"""Real Alembic graph and version-table reads; no deployment or Compose calls."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from scripts import verify_migration_identity as module

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    "verify_v168_live_boundary.ps1",
    "verify_mie_gate1.ps1",
    "verify_mie_gate2.ps1",
    "verify_external_benchmark_pack.ps1",
)
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def revision(root, name, parent):
    (root / "migrations" / "versions" / f"{name}.py").write_text(
        f"revision = {name!r}\ndown_revision = {parent!r}\n", encoding="utf-8"
    )


@pytest.fixture
def source(tmp_path):
    (tmp_path / "migrations" / "versions").mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n")
    revision(tmp_path, "0017", None)
    revision(tmp_path, "0018", "0017")
    return tmp_path


@pytest.fixture
def connection():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def database(connection, heads):
    connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
    for head in heads:
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": head}
        )


@pytest.mark.parametrize("head", ("0018", "0021_future"))
def test_actual_source_and_database_are_read_independently_without_hardcoded_head(
    source, connection, head
):
    if head != "0018":
        revision(source, head, "0018")
    database(connection, [head])
    result = module.verify_connection(source, connection)
    assert result["status"] == "PASS"
    assert result["source_head"] == result["database_head"] == head
    assert len(result["migration_sha256"]) == 64
    assert result["execution_authority"] is False
    assert inspect(connection).get_table_names() == ["alembic_version"]
    assert connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalars().all() == [head]


@pytest.mark.parametrize(
    "heads,code",
    (
        ([], "database_head_not_unique"),
        (["0017"], "migration_revision_mismatch"),
        (["0019"], "migration_revision_mismatch"),
        (["0018", "0017"], "database_head_not_unique"),
        ([""], "database_revision_invalid"),
        (["0018\nprivate-value"], "database_revision_invalid"),
    ),
)
def test_missing_stale_future_multiple_and_malformed_database_heads_deny(
    source, connection, heads, code
):
    database(connection, heads)
    with pytest.raises(module.MigrationIdentityError, match=code):
        module.verify_connection(source, connection)
    assert (
        connection.execute(text("SELECT version_num FROM alembic_version"))
        .scalars()
        .all()
        == heads
    )


def test_missing_version_table_is_not_created_or_inferred_from_source(
    source, connection
):
    with pytest.raises(module.MigrationIdentityError, match="database_head_not_unique"):
        module.verify_connection(source, connection)
    assert inspect(connection).get_table_names() == []


@pytest.mark.parametrize("case", ("none", "multiple", "foreign_layout"))
def test_source_head_not_unique_or_wrong_layout_denies_before_database_read(
    source, connection, monkeypatch, case
):
    if case == "none":
        for path in (source / "migrations" / "versions").glob("*.py"):
            path.unlink()
    elif case == "multiple":
        revision(source, "other_branch", "0017")
    else:
        (source / "alembic.ini").write_text(
            "[alembic]\nscript_location = other-source\n"
        )
    monkeypatch.setattr(
        module,
        "database_head",
        lambda _: pytest.fail("database read after invalid source"),
    )
    with pytest.raises(module.MigrationIdentityError):
        module.verify_connection(source, connection)


def test_changed_source_bytes_during_database_read_fail_closed(
    source, connection, monkeypatch
):
    database(connection, ["0018"])
    original = module.database_head

    def read_then_change(connection):
        observed = original(connection)
        with (source / "migrations" / "versions" / "0018.py").open("a") as stream:
            stream.write("\n# Changed source, same revision label.\n")
        return observed

    monkeypatch.setattr(module, "database_head", read_then_change)
    with pytest.raises(module.MigrationIdentityError, match="migration_source_changed"):
        module.verify_connection(source, connection)


@pytest.mark.parametrize(
    "error",
    (
        RuntimeError(
            "postgresql://private-user:synthetic-private-password@private-host/db"
        ),
        module.MigrationIdentityError("do-not-echo-private-header"),
        module.MigrationIdentityError("database_head_not_unique"),
    ),
)
def test_cli_denies_without_leaking_connection_or_unknown_error_text(
    monkeypatch, capsys, error
):
    async def failed(_root):
        raise error

    monkeypatch.setattr(module, "inspect_runtime", failed)
    assert module.main() == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "FAIL" and result["execution_authority"] is False
    assert result["code"] in (
        "migration_identity_read_failed",
        "database_head_not_unique",
    )
    assert "private" not in captured.out and captured.err == ""


def test_current_source_has_one_actual_head():
    identity = module.source_identity(ROOT)
    assert identity.head == "0024"
    assert len(identity.sha256) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ("0018", "0017"))
async def test_runtime_adapter_reads_real_version_table_and_disposes(
    source, connection, monkeypatch, head
):
    from app.config import settings

    database(connection, [head])
    disposed = []
    requested = []

    class AsyncConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def run_sync(self, read):
            return read(connection)

    class Engine:
        def connect(self):
            return AsyncConnection()

        async def dispose(self):
            disposed.append(True)

    def create(url, **_):
        requested.append(url)
        return Engine()

    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: SimpleNamespace(database_url="synthetic-test-url"),
    )
    monkeypatch.setattr(module, "create_async_engine", create)
    if head == "0018":
        result = await module.inspect_runtime(source)
        assert result["database_head"] == head and result["status"] == "PASS"
    else:
        with pytest.raises(
            module.MigrationIdentityError, match="migration_revision_mismatch"
        ):
            await module.inspect_runtime(source)
    assert disposed == [True] and requested == ["synthetic-test-url"]


POWERSHELL_PROBE = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:CTCC_TEST_PS_SOURCE, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw 'PowerShell parser rejected source' }
$steps = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -eq 'Invoke-NativeStep' -and
    $node.CommandElements.Count -eq 3 -and
    $node.CommandElements[1].Value -eq 'Alembic source/database identity'
}, $true))
if ($steps.Count -ne 1) { throw 'Expected one source/database identity probe' }
function docker {
    $global:LASTEXITCODE = [int]$env:CTCC_TEST_MIGRATION_EXIT
    Write-Output $env:CTCC_TEST_MIGRATION_RESPONSE
}
$composeArguments = @()
$script:verifiedMigrationHead = $null
& $steps[0].CommandElements[2].ScriptBlock.GetScriptBlock()
if ($script:verifiedMigrationHead -ne '0021_future') { throw 'Did not read actual head' }
Write-Output ('READBACK_HEAD=' + $script:verifiedMigrationHead)
"""


@pytest.mark.skipif(
    POWERSHELL is None, reason="Native PowerShell parser/probe requires PowerShell"
)
@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "case",
    (
        "valid",
        "db_mismatch",
        "read_error",
        "authority",
        "malformed_json",
        "wrong_schema",
        "numeric_digest",
    ),
)
def test_native_powershell_parser_and_isolated_probe_readback(script, case):
    value = {
        "schema": module.SCHEMA,
        "status": "PASS",
        "source_head": "0021_future",
        "database_head": "0021_future",
        "migration_sha256": "a" * 64,
        "execution_authority": False,
    }
    if case == "db_mismatch":
        value["database_head"] = "0017"
    elif case == "authority":
        value["execution_authority"] = True
    elif case == "wrong_schema":
        value["schema"] = "unknown"
    elif case == "numeric_digest":
        value["migration_sha256"] = int("1" * 64)
    response = "not-json" if case == "malformed_json" else json.dumps(value)
    path = ROOT / "scripts" / script
    raw = path.read_text(encoding="utf-8")
    assert "alembic check" in raw
    assert "ALEMBIC_HEAD=0017" not in raw and 'expected = "0017 (head)"' not in raw
    process = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_PROBE],
        capture_output=True,
        check=False,
        timeout=30,
        env=dict(
            os.environ,
            CTCC_TEST_PS_SOURCE=str(path),
            CTCC_TEST_MIGRATION_RESPONSE=response,
            CTCC_TEST_MIGRATION_EXIT="1" if case == "read_error" else "0",
        ),
    )
    assert (process.returncode == 0) == (case == "valid"), process.stderr
    if case == "valid":
        assert b"READBACK_HEAD=0021_future" in process.stdout
