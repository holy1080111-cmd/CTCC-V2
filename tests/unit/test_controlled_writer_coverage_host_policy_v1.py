"""Cold host policy and real package-inventory regressions, not native proof.

The source provider below holds actual packaged file bytes in memory. Its
synthetic activation state has no native source, Control owner, repository or
live owner and cannot issue execution authority.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
from weakref import ref

import pytest

_POLICY_PATH = "app/domain/controlled_writer_coverage_catalog_v1.py"
_COLD_PROBE = r"""
import json
import sys

assert (sys.flags.isolated, sys.flags.no_site, sys.flags.dont_write_bytecode) == (1, 1, 1)
sys.path.insert(0, sys.argv[1])

class StdlibHostOnly:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition('.')[0]
        allowed_app = {
            'app',
            'app.domain',
            'app.domain.controlled_writer_coverage_catalog_v1',
        }
        if (root == 'app' and fullname not in allowed_app) or (
            root not in sys.stdlib_module_names and root not in {'app', 'scripts'}
        ):
            raise ModuleNotFoundError('host_dependency_forbidden:' + fullname)

sys.meta_path.insert(0, StdlibHostOnly())
from app.domain import controlled_writer_coverage_catalog_v1 as policy
from scripts import verify_final_hermetic as host
from scripts import verify_linux_shard_union as union
from scripts import verify_controlled_writer_coverage_schema_v1 as adapter

completed = []
expected = host.expected_migration_revision_readback('0048')
assert expected['schema'] == 'ctcc.migration_revision_readback.v5'
assert expected['expected'] == expected['observed'] == '0048'
assert expected['execution_authority'] is False
assert expected['controlled_writer_coverage_schema'] == (
    policy.expected_controlled_writer_coverage_schema()
)
assert expected['controlled_writer_coverage_schema']['native_source_acceptance'] is False
assert union.expected_migration_revision_readback is host.expected_migration_revision_readback
assert adapter.verify_catalog is policy.verify_controlled_writer_coverage_catalog
completed.append('expected_0048')

def refuses(label, operation, expected_type, expected_code):
    try:
        operation()
    except Exception as error:
        assert type(error) is expected_type, (label, type(error).__name__)
        assert str(error) == expected_code, label
    else:
        raise AssertionError(label + ':unexpected_acceptance')
    completed.append(label)

refuses(
    'native_catalog_capture',
    lambda: host.verify_coverage_native_catalog_capture({}, migration_sha256='0' * 64),
    ValueError,
    'coverage_native_catalog_observation_invalid',
)
refuses(
    'nonempty_snapshot',
    lambda: host.coverage_nonempty_snapshot({}, {}),
    RuntimeError,
    'coverage_nonempty_snapshot_invalid',
)
refuses(
    'migration_receipt',
    lambda: host.verify_coverage_migration_receipt(
        {}, manifest_sha256='0' * 64, repository_sha256='1' * 64,
        migration_sha256='2' * 64,
    ),
    ValueError,
    'coverage_migration_receipt_invalid',
)
refuses(
    'migration_revision_readback',
    lambda: host.migration_revision_readback(
        '0048', '0048', False, False, (), coverage_catalog={},
    ),
    policy.ControlledWriterCoverageError,
    'controlled_writer_coverage_shape',
)
assert not any(
    name == 'sqlalchemy' or name.startswith(('sqlalchemy.', 'app.database.', 'app.trade_qualification.'))
    for name in sys.modules
)
print(json.dumps({'completed': completed, 'execution_authority': False}, sort_keys=True))
"""


def test_all_five_host_readers_execute_in_a_cold_stdlib_only_process(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if name in os.environ
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", _COLD_PROBE, str(root)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "completed": [
            "expected_0048",
            "native_catalog_capture",
            "nonempty_snapshot",
            "migration_receipt",
            "migration_revision_readback",
        ],
        "execution_authority": False,
    }


def test_runtime_and_host_share_the_same_policy_objects_and_baseline_owner(monkeypatch):
    from app.database.repositories import controlled_writer_coverage as repository
    from app.domain import controlled_writer_coverage_catalog_v1 as policy
    from scripts import verify_controlled_writer_coverage_schema_v1 as adapter

    for name in (
        "COVERAGE_TABLES",
        "COVERAGE_FUNCTION_SQL",
        "COVERAGE_TABLE_SPECS",
        "COVERAGE_TRIGGER_SPECS",
        "ControlledWriterCoverageError",
        "canonical",
        "expected_controlled_writer_coverage_schema",
        "verify_controlled_writer_coverage_catalog",
    ):
        assert getattr(repository, name) is getattr(policy, name)
    assert adapter.ControlledWriterCoverageError is policy.ControlledWriterCoverageError
    assert adapter.expected_controlled_writer_coverage_schema is (
        policy.expected_controlled_writer_coverage_schema
    )
    assert adapter.verify_catalog is policy.verify_controlled_writer_coverage_catalog
    for function in (
        repository.expected_controlled_writer_coverage_schema,
        repository.verify_controlled_writer_coverage_catalog,
    ):
        assert function.__globals__ is vars(policy)

    monkeypatch.setattr(policy, "PG17_DEPARSED_BASELINE", None)
    monkeypatch.setattr(
        policy, "PG17_DEPARSED_BASELINE_STATUS", "PENDING_NOT_ACQUIRED_UNSEALED_DENY"
    )
    expected = repository.expected_controlled_writer_coverage_schema()
    assert expected == adapter.expected_controlled_writer_coverage_schema()
    assert expected["native_deparser_baseline"] is None
    assert expected["native_baseline_status"] == "PENDING_NOT_ACQUIRED_UNSEALED_DENY"
    assert expected["native_source_acceptance"] is False
    with pytest.raises(
        policy.ControlledWriterCoverageError,
        match=r"^controlled_writer_coverage_catalog_invalid$",
    ):
        adapter.verify_catalog({}, require_native_baseline=1)


@pytest.fixture
def actual_packaged_source_provider(monkeypatch):
    from app.trade_qualification import controlled_writer_coverage_v1 as issuer

    # No real source or manifest is changed. Only the file-read boundary is
    # replaced; the inventory parser, default-denial checks and hashes are real.
    files = {
        relative: (issuer._ROOT / relative).read_bytes()
        for relative in issuer.WRITER_PATHS
    }
    assert _POLICY_PATH in files

    def refresh_manifest():
        files["MANIFEST.sha256"] = "".join(
            f"{hashlib.sha256(raw).hexdigest()}  {relative}\n"
            for relative, raw in sorted(files.items())
            if relative != "MANIFEST.sha256"
        ).encode("ascii")

    refresh_manifest()
    reads = []

    def read_file(path, maximum):
        relative = path.relative_to(issuer._ROOT).as_posix()
        raw = files[relative]
        assert type(raw) is bytes and 0 < len(raw) <= maximum
        reads.append(relative)
        return raw, (
            "synthetic_file_provider",
            relative,
            hashlib.sha256(raw).hexdigest(),
        )

    monkeypatch.setattr(issuer, "_read_file", read_file)
    held = issuer._package_inventory()
    assert set(held["files"]) == set(issuer.WRITER_PATHS)
    assert _POLICY_PATH in held["files"]
    assert set(reads) == {"MANIFEST.sha256", *issuer.WRITER_PATHS}
    return SimpleNamespace(
        issuer=issuer,
        files=files,
        held=held,
        refresh_manifest=refresh_manifest,
    )


def test_real_package_inventory_rejects_only_policy_file_tampering(
    actual_packaged_source_provider,
):
    source = actual_packaged_source_provider
    source.files[_POLICY_PATH] += b"\n# synthetic unit tamper\n"
    with pytest.raises(
        source.issuer.ControlledWriterCoverageError,
        match=r"^controlled_writer_coverage_packaged_source_changed$",
    ):
        source.issuer._package_inventory()


@pytest.mark.asyncio
async def test_coherent_policy_and_manifest_change_invalidates_held_binding(
    actual_packaged_source_provider, monkeypatch
):
    source = actual_packaged_source_provider
    issuer = source.issuer
    source.files[_POLICY_PATH] += b"\n# synthetic coherent update\n"
    source.refresh_manifest()
    current = issuer._package_inventory()
    assert current["files"][_POLICY_PATH] != source.held["files"][_POLICY_PATH]
    assert current["manifest_sha256"] != source.held["manifest_sha256"]
    assert current["writer_sha256"] != source.held["writer_sha256"]

    # Only a synthetic activation record is installed, never a native/live
    # owner. The real guard must reject its old inventory before any subsequent
    # clock, account, Control or repository work can run.
    binding = object.__new__(issuer._ActivationBindingV1)
    factory, engine, custody = object(), object(), object()
    pins = (factory, engine, (), custody, custody, engine, ())
    owners_before = tuple(issuer._OWNERS)
    factory_checks = []

    def fixed_factory_pins(actual_factory, actual_custody):
        assert actual_factory is factory and actual_custody is custody
        factory_checks.append(True)
        return pins

    monkeypatch.setattr(issuer, "_factory_pins", fixed_factory_pins)
    issuer._BINDINGS[binding] = {
        "context": (
            os.getpid(),
            get_ident(),
            asyncio.get_running_loop(),
            ref(asyncio.current_task()),
        ),
        "factory": factory,
        "custody": custody,
        "factory_pins": pins,
        "inventory": source.held,
    }
    try:
        with pytest.raises(
            issuer.ControlledWriterCoverageError,
            match=r"^controlled_writer_coverage_native_identity_changed$",
        ):
            issuer._check_binding(binding)
    finally:
        issuer._BINDINGS.pop(binding, None)
    assert factory_checks == [True]
    assert tuple(issuer._OWNERS) == owners_before
