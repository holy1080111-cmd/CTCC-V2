"""Synthetic source/evidence boundaries, never native PG or G6 acceptance."""

import ast
import copy
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain import prospective_event_cutover_catalog_v2 as policy
from scripts import diagnose_runtime_custody_postgres as diagnostic
from scripts import verify_final_hermetic as host
from scripts import verify_linux_shard_union as union


def test_all_current_union_migration_logs_are_pairs_and_partition_remains0050():
    source = ast.parse(Path(union.__file__).read_text(encoding="utf-8"))
    assignments = [
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "migration_logs"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Tuple)
    assert all(
        isinstance(item, ast.Tuple) and len(item.elts) == 2 for item in value.elts
    )
    observed = {item.elts[0].value: item.elts[1] for item in value.elts}
    parent = observed["migration-history-partition-empty-before"]
    assert isinstance(parent, ast.Call) and parent.args[0].value == "0050"
    assert "migration-prospective-cutover-empty-before" in observed
    assert "migration-prospective-cutover-empty-readback" in observed


def _capture(monkeypatch):
    assert (
        policy.PG17_DEPARSED_BASELINE
        == "531b56a9680701a6f1bd462c52d0c57edb883194cbf88c75b84bfa7b391b66fd"
    )
    assert policy.PG17_DEPARSED_BASELINE_STATUS == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    monkeypatch.setattr(policy, "PG17_DEPARSED_BASELINE", None)
    monkeypatch.setattr(
        policy, "PG17_DEPARSED_BASELINE_STATUS", "UNSEALED_REQUIRES_NATIVE_PG17_CAPTURE"
    )
    pins = {
        name: {"sha256": "a" * 64} for name in host.PROSPECTIVE_CUTOVER_CAPTURE_PATHS
    }
    sentinel = {"synthetic_fixture_only": True}
    calls = []

    def verify(value, *, require_native_baseline=True):
        assert value == sentinel
        calls.append(require_native_baseline)
        if require_native_baseline:
            from app.domain.prospective_event_cutover_v2 import ProspectiveCutoverError

            raise ProspectiveCutoverError("prospective_cutover_native_baseline_pending")

    def deparser(value):
        assert value == sentinel
        return "f" * 64

    monkeypatch.setattr(policy, "verify_catalog", verify)
    monkeypatch.setattr(policy, "deparser_sha256", deparser)
    value = {
        "schema": "ctcc.prospective_cutover_native_catalog_observation.v2",
        "source_head": "0051",
        "database_head": "0051",
        "source_migration_sha256": "b" * 64,
        "source_files": {name: item["sha256"] for name, item in pins.items()},
        "manifest_sha256": "c" * 64,
        "catalog": sentinel,
        "session_facts": ["17.0", "170000", "ctcc", "ctcc", "ctcc", "pg_catalog"],
        "effective_ddl_sha256": policy.ddl_identity(),
        "observed_deparser_sha256": "f" * 64,
        "native_baseline_admission": False,
        "g6_qualified": False,
        "execution_authority": False,
    }
    return value, pins, calls


def test_actual_capture_contract_can_retain_observation_while_formal_gate_refuses(
    monkeypatch,
):
    from app.domain.prospective_event_cutover_v2 import ProspectiveCutoverError

    assert (
        policy.PG17_DEPARSED_BASELINE
        == "531b56a9680701a6f1bd462c52d0c57edb883194cbf88c75b84bfa7b391b66fd"
    )
    value, pins, calls = _capture(monkeypatch)
    before = copy.deepcopy(value)
    observed = host.verify_prospective_cutover_native_catalog_capture(
        value,
        migration_sha256="b" * 64,
        manifest_sha256="c" * 64,
        source_files=pins,
        require_native_baseline=False,
    )
    assert observed["native_acceptance"] is False and calls == [False]
    with pytest.raises(
        ProspectiveCutoverError, match="prospective_cutover_native_baseline_pending"
    ):
        host.verify_prospective_cutover_native_catalog_capture(
            value,
            migration_sha256="b" * 64,
            manifest_sha256="c" * 64,
            source_files=pins,
        )
    assert calls == [False, True] and value == before
    assert policy.PG17_DEPARSED_BASELINE is None


@pytest.mark.parametrize(
    "damage",
    (
        "head",
        "database",
        "pin",
        "manifest",
        "ddl",
        "deparser",
        "g6",
        "admission",
        "bool_type",
        "current_schema",
    ),
)
def test_capture_metadata_tampering_is_not_repaired_or_relabelled(monkeypatch, damage):
    value, pins, calls = _capture(monkeypatch)
    if damage == "head":
        value["source_head"] = "0050"
    elif damage == "database":
        value["database_head"] = "0050"
    elif damage == "pin":
        value["source_files"][next(iter(value["source_files"]))] = "0" * 64
    elif damage == "manifest":
        value["manifest_sha256"] = "0" * 64
    elif damage == "ddl":
        value["effective_ddl_sha256"] = "0" * 64
    elif damage == "deparser":
        value["observed_deparser_sha256"] = "0" * 64
    elif damage == "g6":
        value["g6_qualified"] = True
    elif damage == "admission":
        value["native_baseline_admission"] = True
    elif damage == "bool_type":
        value["execution_authority"] = 0
    else:
        value["session_facts"][-1] = "public"
    before = copy.deepcopy(value)
    with pytest.raises(
        ValueError, match="prospective_cutover_native_catalog_observation_invalid"
    ):
        host.verify_prospective_cutover_native_catalog_capture(
            value,
            migration_sha256="b" * 64,
            manifest_sha256="c" * 64,
            source_files=pins,
            require_native_baseline=False,
        )
    assert value == before and calls == []


@pytest.mark.parametrize(
    "damage", (None, "formal_g6", "missing_capture", "wrong_head", "cleanup")
)
def test_capture_only_main_requires_actual_capture_pending_proof_and_owned_cleanup(
    tmp_path, monkeypatch, damage
):
    """Real host and cleanup helpers, synthetic Docker/catalog transport only."""
    from scripts import prospective_capture_custody as custody
    from tests.unit.test_prospective_capture_custody import (
        RUN_ID,
        FakeDocker,
        _capture_bundle,
    )

    commit, tree = "a" * 40, "b" * 40
    raw = io.BytesIO()
    with tarfile.open(
        fileobj=raw,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": commit},
    ) as archive:
        entry = tarfile.TarInfo("synthetic-public-file")
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    archive_raw = raw.getvalue()
    archive_path = tmp_path / "exact-source.tar"
    archive_path.write_bytes(archive_raw)
    source = Path(diagnostic.__file__).parents[1]
    files = {
        name: {"sha256": hashlib.sha256((source / name).read_bytes()).hexdigest()}
        for name in (
            "scripts/diagnose_runtime_custody_postgres.py",
            "scripts/verify_final_hermetic.py",
            "scripts/verify_linux_shard.py",
            "scripts/prospective_capture_custody.py",
        )
    }
    files["requirements/validation-linux-py312.lock"] = {
        "sha256": diagnostic.LOCK_SHA256
    }
    files, captures, formal = _capture_bundle(monkeypatch, files)
    monkeypatch.setattr(diagnostic, "archive_tree", lambda _archive: tree)
    monkeypatch.setattr(diagnostic, "archive_files", lambda _archive: files)
    monkeypatch.setattr(
        diagnostic, "uuid4", lambda: SimpleNamespace(hex=RUN_ID + "0" * 20)
    )
    fake = FakeDocker()
    fake.visible["container"] = {"ctcc-final-" + RUN_ID + "-postgres"}
    fake.query_failure = 1 if damage == "cleanup" else None
    monkeypatch.setattr(custody.subprocess, "run", fake)

    def forbidden_legacy_cleanup(*_args, **_kwargs):
        raise AssertionError("new profile must use its strict shared-deadline helper")

    monkeypatch.setattr(diagnostic, "cleanup_resources", forbidden_legacy_cleanup)
    calls = []

    class FakeRun:
        def __init__(self, output, **_kwargs):
            self.output, self.steps, self.deadline = output, [], float("inf")

        def command(self, name, command, **_kwargs):
            calls.append((name, command))
            self.steps.append({"name": name, "exit_code": 0})
            result = b""
            if name == "image-identity":
                result = b"sha256:" + b"d" * 64
            elif name == "prospective-cutover-current-identity":
                result = json.dumps(
                    {
                        "schema": "ctcc.migration_identity.v1",
                        "status": "PASS",
                        "source_head": "0050" if damage == "wrong_head" else "0051",
                        "database_head": "0051",
                        "migration_sha256": custody._migration_pin(files),
                        "execution_authority": False,
                    }
                ).encode()
            elif name in {stage for _, stage in custody.CAPTURES}:
                filename = next(
                    filename for filename, stage in custody.CAPTURES if name == stage
                )
                captured = (
                    json.dumps(
                        captures[filename], sort_keys=True, separators=(",", ":")
                    ).encode()
                    + b"\n"
                )
                if not (
                    damage == "missing_capture" and filename.startswith("prospective-")
                ):
                    (self.output / "test-results" / filename).write_bytes(captured)
                receipt = {
                    "artifact": "test-results/" + filename,
                    "sha256": hashlib.sha256(captured).hexdigest(),
                    "native_baseline_admission": False,
                    "execution_authority": False,
                }
                if filename.startswith("prospective-"):
                    receipt["g6_qualified"] = False
                result = json.dumps(receipt).encode()
            elif name == "prospective-cutover-formal-catalog-probe":
                result = json.dumps(
                    {**formal, "g6_qualified": damage == "formal_g6"}
                ).encode()
            (self.output / (name + ".log")).write_bytes(result)

    monkeypatch.setattr(diagnostic, "Run", FakeRun)
    output = tmp_path / "capture"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnostic",
            "--profile",
            diagnostic.PROSPECTIVE_CUTOVER_CAPTURE_PROFILE,
            "--archive",
            str(archive_path),
            "--archive-sha256",
            hashlib.sha256(archive_raw).hexdigest(),
            "--commit",
            commit,
            "--tree",
            tree,
            "--output",
            str(output),
        ],
    )
    result = diagnostic.main()
    identity = json.loads((output / "export/identity.json").read_bytes())
    assert fake.calls
    assert (
        identity["execution_authority"] is False
        and identity["external_order_writes"] == 0
    )
    assert identity["full_postgresql_acceptance"] is False
    assert result == (0 if damage is None else 1)
    if damage is None:
        assert (
            identity["tests_executed"] == 0
            and identity["capture_export_accepted"] is True
        )
        assert (
            identity["formal_catalog_probe"]["result"]
            == "DENIED_NATIVE_BASELINE_PENDING"
        )
        assert not (output / "export/diagnostic-junit.xml").exists()
        assert len(identity["trusted_capture_artifacts"]) == 3
    else:
        assert identity["trusted_capture_artifacts"] is None
        assert identity["capture_export_accepted"] is False
    assert archive_path.read_bytes() == archive_raw
    assert (
        identity["native_baseline_admission"] is False
        and identity["g6_qualified"] is False
    )
    commands = [command for name, command in calls if name.endswith("catalog")]
    assert all(
        "--env-file" not in command
        and "LIVE_TRADING=false" in command
        and "OKX_DEMO_ALLOW_ORDER_WRITES=false" in command
        for command in commands
    )
    assert policy.PG17_DEPARSED_BASELINE is None
