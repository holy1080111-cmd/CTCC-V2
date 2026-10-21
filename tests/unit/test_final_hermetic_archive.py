"""Archive admission must reject source substitutions before Docker starts."""

import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace

import pytest

from scripts.verify_final_hermetic import (
    ACCOUNT_MEASURED_HWM_V2_TRIGGERS,
    ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
    ACCOUNT_REVISION_WITNESS_TRIGGERS,
    DOWNGRADE_DENIAL,
    PUBLIC_RUNTIME_BUNDLE_SCHEMA,
    PUBLIC_RUNTIME_BUNDLE_SCHEMA_ABSENT,
    RUNTIME_CUSTODY_PREFLIGHT_CASES,
    Run,
    archive_files,
    archive_tree,
    cleanup_resources,
    expected_migration_revision_readback,
    migration_revision_readback,
    run_runtime_bundle_custody_preflight,
    verify_copied_source,
    verify_downgrade_denial_log,
)
from tests.durable_migration_fixtures import load_migration


def test_db0044_guard_body_hash_is_the_reviewed_hermetic_catalog_pin():
    sql = load_migration("0044")._guard()
    match = re.search(r"AS \$\$(.*?)\$\$ LANGUAGE", sql, re.DOTALL)
    assert match is not None
    assert (
        hashlib.sha256(match.group(1).encode()).hexdigest()
        == (ACCOUNT_MEASURED_HWM_V2_TRIGGERS[0][6])
    )


def test_db0045_repaired_guard_body_and_reverse_are_exactly_pinned():
    old_sql = load_migration("0044")._guard()
    old_match = re.search(r"AS \$\$(.*?)\$\$ LANGUAGE", old_sql, re.DOTALL)
    assert old_match is not None
    old_body = old_match.group(1)
    migration = load_migration("0045")
    repaired = migration._transform(old_body)
    assert (
        hashlib.sha256(repaired.encode()).hexdigest()
        == (ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045[0][6])
    )
    assert migration._transform(repaired, reverse=True) == old_body


def test_db0046_raw_custody_function_bodies_match_reviewed_catalog_pins():
    migration = load_migration("0046")
    bodies = {
        "append": migration._APPEND_BODY,
        "guard": migration._INSERT_BODY,
        "immutable": migration._IMMUTABLE_BODY,
        "read": migration._READ_BODY,
    }
    for row in PUBLIC_RUNTIME_BUNDLE_SCHEMA["functions"]:
        name = row[0].removeprefix("public_runtime_bundle_v1_")
        assert hashlib.sha256(bodies.pop(name).encode()).hexdigest() == row[1]
    assert not bodies


def _head_readback(runtime_catalog):
    return migration_revision_readback(
        "0046",
        "0046",
        True,
        True,
        ACCOUNT_REVISION_WITNESS_TRIGGERS,
        True,
        True,
        ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
        runtime_catalog,
    )


def test_expected_db0046_readback_cannot_mutate_reviewed_catalog_pins():
    expected = expected_migration_revision_readback("0046")
    expected["public_runtime_bundle_schema"]["functions"][0][1] = "0" * 64
    assert _head_readback(copy.deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA)) == (
        expected_migration_revision_readback("0046")
    )
    assert expected != expected_migration_revision_readback("0046")


@pytest.mark.parametrize(
    "drift",
    (
        "missing_catalog",
        "missing_table",
        "nonpersistent_table",
        "column_type",
        "column_nullable",
        "extra_column",
        "missing_function",
        "function_body",
        "function_security",
        "function_set_returning",
        "function_search_path",
        "function_owner",
        "function_native_shape",
        "function_public_execute",
        "disabled_trigger",
        "trigger_event",
        "trigger_filter",
        "extra_trigger",
        "constraint_hash",
        "constraint_not_validated",
        "constraint_deferrable",
    ),
)
def test_db0046_readback_rejects_rehashed_raw_custody_catalog_drift(drift):
    catalog = copy.deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA)
    if drift == "missing_catalog":
        catalog = None
    elif drift == "missing_table":
        catalog = copy.deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA_ABSENT)
    elif drift == "nonpersistent_table":
        catalog["table_shape"] = False
    elif drift == "column_type":
        catalog["columns"][3][1] = "text"
    elif drift == "column_nullable":
        catalog["columns"][3][2] = False
    elif drift == "extra_column":
        catalog["columns"].append(["unreviewed", "text", True, True, True])
    elif drift == "missing_function":
        catalog["functions"].pop()
    elif drift == "function_body":
        catalog["functions"][0][1] = "0" * 64
    elif drift == "function_security":
        catalog["functions"][0][2] = False
    elif drift == "function_set_returning":
        catalog["functions"][3][3] = False
    elif drift == "function_search_path":
        catalog["functions"][0][6] = False
    elif drift == "function_owner":
        catalog["functions"][0][7] = False
    elif drift == "function_native_shape":
        catalog["functions"][0][8] = False
    elif drift == "function_public_execute":
        catalog["functions"][0][9] = False
    elif drift == "disabled_trigger":
        catalog["triggers"][0][1] = "D"
    elif drift == "trigger_event":
        catalog["triggers"][0][2] = 7
    elif drift == "trigger_filter":
        catalog["triggers"][0][4] = False
    elif drift == "extra_trigger":
        catalog["triggers"].append(["unreviewed", "A", 7, "guard", True])
    elif drift == "constraint_hash":
        catalog["constraints"][0][5] = "0" * 64
    elif drift == "constraint_not_validated":
        catalog["constraints"][0][2] = False
    else:
        catalog["constraints"][0][3] = True
    with pytest.raises(RuntimeError, match="migration_revision_readback_mismatch"):
        _head_readback(catalog)
    accepted = _head_readback(copy.deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA))
    assert accepted == expected_migration_revision_readback("0046")
    assert accepted["schema"] == "ctcc.migration_revision_readback.v3"


@pytest.mark.parametrize("revision", ("0001", "0042", "0043", "0044", "0045"))
def test_historical_readback_keeps_v2_schema_and_requires_runtime_custody_absence(
    revision,
):
    witness = revision in ("0043", "0044", "0045")
    hwm = revision in ("0044", "0045")
    args = (
        revision,
        revision,
        witness,
        witness,
        ACCOUNT_REVISION_WITNESS_TRIGGERS if witness else (),
        hwm,
        hwm,
        (
            ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045
            if revision == "0045"
            else ACCOUNT_MEASURED_HWM_V2_TRIGGERS
        )
        if hwm
        else (),
    )
    result = migration_revision_readback(*args, PUBLIC_RUNTIME_BUNDLE_SCHEMA_ABSENT)
    assert result == expected_migration_revision_readback(revision)
    assert result["schema"] == "ctcc.migration_revision_readback.v2"
    assert "public_runtime_bundle_schema" not in result
    with pytest.raises(RuntimeError, match="migration_revision_readback_mismatch"):
        migration_revision_readback(*args, PUBLIC_RUNTIME_BUNDLE_SCHEMA)


@pytest.mark.parametrize("revision", ("0001", "0042"))
def test_migration_readback_requires_witness_absence_before_db0043(revision):
    assert migration_revision_readback(revision, revision, False, False, ()) == (
        expected_migration_revision_readback(revision)
    )
    for table, guard, triggers in (
        (True, False, ()),
        (False, True, ()),
        (False, False, ACCOUNT_REVISION_WITNESS_TRIGGERS),
    ):
        with pytest.raises(RuntimeError, match="migration_revision_readback_mismatch"):
            migration_revision_readback(revision, revision, table, guard, triggers)


@pytest.mark.parametrize(
    "drift",
    (
        "extra",
        "disabled",
        "wrong_function_oid",
        "wrong_type",
        "when_clause",
        "column_filter",
        "function_body",
    ),
)
def test_migration_readback_rejects_any_db0043_trigger_catalog_drift(drift):
    catalog = [list(row) for row in ACCOUNT_REVISION_WITNESS_TRIGGERS]
    if drift == "extra":
        catalog.append(["unreviewed_trigger", "O", 7, False])
    elif drift == "disabled":
        catalog[0][1] = "D"
    elif drift == "wrong_function_oid":
        catalog[1][3] = False
    elif drift == "when_clause":
        catalog[0][4] = False
    elif drift == "column_filter":
        catalog[1][5] = False
    elif drift == "function_body":
        catalog[2][6] = "0" * 64
    else:
        catalog[2][2] = 33
    with pytest.raises(RuntimeError, match="migration_revision_readback_mismatch"):
        migration_revision_readback("0043", "0043", True, True, catalog)
    assert migration_revision_readback(
        "0043", "0043", True, True, ACCOUNT_REVISION_WITNESS_TRIGGERS
    ) == expected_migration_revision_readback("0043")


@pytest.mark.parametrize(
    "drift",
    ("missing_table", "missing_guard", "extra", "disabled", "body", "function"),
)
@pytest.mark.parametrize(
    "revision,pin",
    (
        ("0044", ACCOUNT_MEASURED_HWM_V2_TRIGGERS),
        ("0045", ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045),
    ),
)
def test_migration_readback_rejects_hwm_catalog_drift(drift, revision, pin):
    catalog = [list(row) for row in pin]
    table, guard = True, True
    if drift == "missing_table":
        table = False
    elif drift == "missing_guard":
        guard = False
    elif drift == "extra":
        catalog.append(["unreviewed_trigger", "O", 7, False])
    elif drift == "disabled":
        catalog[0][1] = "D"
    elif drift == "body":
        catalog[0][6] = "0" * 64
    else:
        catalog[0][3] = False
    with pytest.raises(RuntimeError, match="migration_revision_readback_mismatch"):
        migration_revision_readback(
            revision,
            revision,
            True,
            True,
            ACCOUNT_REVISION_WITNESS_TRIGGERS,
            table,
            guard,
            catalog,
        )
    assert migration_revision_readback(
        revision,
        revision,
        True,
        True,
        ACCOUNT_REVISION_WITNESS_TRIGGERS,
        True,
        True,
        pin,
    ) == expected_migration_revision_readback(revision)


def archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as target:
        for name, raw, kind in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(raw) if kind == tarfile.REGTYPE else 0
            item.linkname = "outside" if kind == tarfile.SYMTYPE else ""
            target.addfile(item, io.BytesIO(raw))
    data.seek(0)
    return tarfile.open(fileobj=data)


@pytest.mark.parametrize(
    "name",
    (
        "../escape",
        "/absolute",
        ".env",
        "config/.env.live",
        ".git/config",
        "reports/private.json",
        "backups/database",
        "config/runtime.token",
        "config/RUNTIME.TOKEN",
        "config/.ENV.live",
        "private-notion/destination.json",
        "PRIVATE-NOTION/nested/data.json",
        "config/exchange.key",
        "config/private.pem",
        "config/account.p12",
        "config/account.pfx",
        "config/login.kdbx",
        "config/store.jks",
        "config/key.keystore",
        ".npmrc",
        "config/.pypirc",
        "config/id_ed25519",
        "config/CREDENTIALS.JSON",
        "config/secrets.json",
        "config/TOKEN.JSON",
        "private/harmless.txt",
        "secrets/harmless.txt",
        "credentials/harmless.txt",
        ".key",
        ".token",
        ".pem",
        "config/.p12",
        "config/.pfx",
        "config/.kdbx",
        "config/.jks",
        "config/.keystore",
        ".env/secret.txt",
        "config/.ENV.live/data.txt",
        "config/private.pem.bak",
        "config/exchange.key.old",
        "config/credentials.json.backup",
    ),
)
def test_archive_refuses_non_source_paths(name):
    with (
        archive([(name, b"not-a-secret", tarfile.REGTYPE)]) as value,
        pytest.raises(ValueError),
    ):
        archive_tree(value)


def test_archive_refuses_symlink_and_duplicate_members():
    with (
        archive([("link", b"", tarfile.SYMTYPE)]) as value,
        pytest.raises(ValueError, match="non_source"),
    ):
        archive_tree(value)
    with (
        archive(
            [("a", b"first", tarfile.REGTYPE), ("a", b"second", tarfile.REGTYPE)]
        ) as value,
        pytest.raises(ValueError, match="duplicate"),
    ):
        archive_tree(value)


@pytest.mark.parametrize(
    "name", (".git", "config/.env", "config/.ENV.live", "unlisted")
)
def test_archive_refuses_private_or_empty_directory_members(name):
    with (
        archive(
            [
                ("tracked.py", b"pass\n", tarfile.REGTYPE),
                (name, b"", tarfile.DIRTYPE),
            ]
        ) as value,
        pytest.raises(ValueError),
    ):
        archive_tree(value)


def test_archive_accepts_directory_with_tracked_source_descendant():
    entries = [("config/source.py", b"pass\n", tarfile.REGTYPE)]
    with archive(entries) as bare:
        expected_tree = archive_tree(bare)
    with archive([("config", b"", tarfile.DIRTYPE), *entries]) as declared:
        assert archive_tree(declared) == expected_tree


def test_archive_tree_changes_when_checkout_bytes_change():
    # Windows archive conversion must not masquerade as the original Git tree.
    with archive([("source.py", b"pass\n", tarfile.REGTYPE)]) as original:
        original_tree = archive_tree(original)
    with archive([("source.py", b"pass\r\n", tarfile.REGTYPE)]) as changed:
        assert archive_tree(changed) != original_tree


def test_empty_tree_matches_git_known_empty_tree_identity():
    with archive([]) as value:
        assert archive_tree(value) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def test_actual_copied_bytes_cannot_rely_on_normalized_manifest(tmp_path):
    with archive([("source.py", b"pass\n", tarfile.REGTYPE)]) as original:
        files = archive_files(original)
    mapping = tmp_path / "source-files.json"
    mapping.write_text(json.dumps(files))
    digest = hashlib.sha256(mapping.read_bytes()).hexdigest()
    source = tmp_path / "source"
    source.mkdir()
    copied = source / "source.py"
    copied.write_bytes(b"pass\n")
    assert verify_copied_source(source, mapping, digest) == 1
    copied.write_bytes(b"pass\r\n")
    with pytest.raises(ValueError, match="copied_source_mismatch"):
        verify_copied_source(source, mapping, digest)
    copied.write_bytes(b"pass\n")
    mapping.write_text("{}")
    with pytest.raises(ValueError, match="source_file_map_digest_mismatch"):
        verify_copied_source(source, mapping, digest)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits require Linux")
@pytest.mark.parametrize("original_executable", (False, True))
def test_copied_source_rejects_executable_bit_drift(tmp_path, original_executable):
    source = tmp_path / "source"
    source.mkdir()
    copied = source / "example.sh"
    copied.write_bytes(b"#!/bin/sh\nexit 0\n")
    mapping = tmp_path / "source-files.json"
    mapping.write_text(
        json.dumps(
            {
                "example.sh": {
                    "sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
                    "executable": original_executable,
                }
            }
        )
    )
    digest = hashlib.sha256(mapping.read_bytes()).hexdigest()
    copied.chmod(0o755 if original_executable else 0o644)
    assert verify_copied_source(source, mapping, digest) == 1
    copied.chmod(0o644 if original_executable else 0o755)
    with pytest.raises(ValueError, match="copied_source_mismatch"):
        verify_copied_source(source, mapping, digest)


def test_build_receives_admitted_archive_even_if_original_is_replaced(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from scripts import verify_final_hermetic as verifier

    content = io.BytesIO()
    commit = "a" * 40
    with tarfile.open(
        fileobj=content, mode="w", pax_headers={"comment": commit}
    ) as saved:
        raw = b"FROM scratch\nCOPY example /example\n"
        item = tarfile.TarInfo("Dockerfile")
        item.size = len(raw)
        item.mode = 0o644
        saved.addfile(item, io.BytesIO(raw))
        item = tarfile.TarInfo("example")
        item.size = 3
        item.mode = 0o755
        saved.addfile(item, io.BytesIO(b"run"))
    admitted = content.getvalue()
    with tarfile.open(fileobj=io.BytesIO(admitted)) as saved:
        tree = archive_tree(saved)
    archive_path = tmp_path / "source.tar"
    archive_path.write_bytes(admitted)
    output = tmp_path / "results"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_final_hermetic.py",
            "--archive",
            str(archive_path),
            "--archive-sha256",
            hashlib.sha256(admitted).hexdigest(),
            "--commit",
            commit,
            "--tree",
            tree,
            "--output",
            str(output),
        ],
    )
    original_read = Path.read_bytes

    def replaced_after_read(path):
        raw = original_read(path)
        if path == archive_path:
            path.write_bytes(b"untrusted replacement after admission read")
        return raw

    monkeypatch.setattr(Path, "read_bytes", replaced_after_read)
    calls = []

    def stop_at_build(args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("test_stopped_before_docker")

    monkeypatch.setattr(subprocess, "run", stop_at_build)
    monkeypatch.setattr(verifier, "cleanup_resources", lambda *_: [])
    with pytest.raises(RuntimeError, match="test_stopped_before_docker"):
        verifier.main()
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ["docker", "build"] and args[-1] == "-"
    assert str(output / "source") not in args
    assert kwargs["input"] == admitted
    assert original_read(archive_path) != kwargs["input"]
    assert (output / "source" / "example").read_bytes() == b"run"


def test_command_timeout_is_durable_failed_step(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        Run(tmp_path).command("linux-full", ["docker", "run"], timeout=1)
    step = json.loads((tmp_path / "steps.json").read_text())[0]
    assert step["exit_code"] is None
    assert step["failure_type"] == "TimeoutExpired"
    assert step["name"] == "linux-full"
    assert step["timeout_scope"] == "stage"


@pytest.mark.parametrize(
    "returncode,marker,passes",
    (
        (1, DOWNGRADE_DENIAL, True),
        (0, DOWNGRADE_DENIAL, False),
        (1, "network_unavailable", False),
    ),
)
def test_migration_policy_refusal_requires_exact_database_error_and_failed_exit(
    tmp_path, monkeypatch, returncode, marker, passes
):
    output = (
        "INFO Running downgrade 0046 -> 0045\n"
        "INFO Running downgrade 0045 -> 0044\n"
        "INFO Running downgrade 0044 -> 0043\n"
        "INFO Running downgrade 0043 -> 0042\n"
        "INFO Running downgrade 0042 -> 0041\n"
        '  File "/app/migrations/versions/'
        '0042_submission_transport_authenticity_denial.py", line 109\n'
        f"asyncpg.exceptions.RaiseError: {marker}\n"
        f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {marker}\n"
    ).encode()

    def docker(*args, **kwargs):
        kwargs["stdout"].write(output)
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", docker)
    run = Run(tmp_path)
    if passes:
        run.command(
            "migration-downgrade-denial",
            ["docker", "run"],
            expected_refusal=DOWNGRADE_DENIAL,
        )
        verify_downgrade_denial_log(tmp_path / "migration-downgrade-denial.log")
    else:
        with pytest.raises((RuntimeError, ValueError), match="policy_refusal"):
            run.command(
                "migration-downgrade-denial",
                ["docker", "run"],
                expected_refusal=DOWNGRADE_DENIAL,
            )
    step = json.loads((tmp_path / "steps.json").read_text())[0]
    assert step.get("policy_refusal") == (DOWNGRADE_DENIAL if passes else None)
    assert step["exit_code"] == returncode


@pytest.mark.parametrize(
    "missing_transition",
    (
        "0046 -> 0045",
        "0045 -> 0044",
        "0044 -> 0043",
        "0043 -> 0042",
        "0042 -> 0041",
    ),
)
def test_migration_policy_refusal_rejects_missing_transition(
    tmp_path, missing_transition
):
    log = tmp_path / "migration-downgrade-denial.log"
    transitions = "".join(
        f"INFO Running downgrade {transition}\n"
        for transition in (
            "0046 -> 0045",
            "0045 -> 0044",
            "0044 -> 0043",
            "0043 -> 0042",
            "0042 -> 0041",
        )
        if transition != missing_transition
    )
    log.write_text(
        transitions + '  File "/app/migrations/versions/'
        '0042_submission_transport_authenticity_denial.py", line 109\n'
        f"asyncpg.exceptions.RaiseError: {DOWNGRADE_DENIAL}\n"
        f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {DOWNGRADE_DENIAL}\n"
    )
    with pytest.raises(
        ValueError, match="migration_downgrade_policy_refusal_unverified"
    ):
        verify_downgrade_denial_log(log)


def test_global_deadline_caps_stage_timeout_and_preserves_failed_step(
    tmp_path, monkeypatch
):
    class Clock:
        now = 10.0

        def __call__(self):
            return self.now

    clock = Clock()

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 7.0
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        Run(tmp_path, budget_seconds=7, clock=clock).command(
            "linux-full", ["docker", "run"], timeout=3600
        )
    step = json.loads((tmp_path / "steps.json").read_text())[0]
    assert step["failure_type"] == "TimeoutExpired"
    assert step["timeout_scope"] == "validation"
    assert step["exit_code"] is None
    assert (tmp_path / "linux-full.log").exists()


def test_expired_global_deadline_never_starts_docker(tmp_path, monkeypatch):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()

    def unexpected(*args, **kwargs):
        raise AssertionError("Docker must not start after the validation deadline")

    monkeypatch.setattr(subprocess, "run", unexpected)
    run = Run(tmp_path, budget_seconds=1, clock=clock)
    clock.now = 2.0
    with pytest.raises(TimeoutError, match="validation_deadline_exceeded"):
        run.command("linux-full", ["docker", "run"])
    step = json.loads((tmp_path / "steps.json").read_text())[0]
    assert step["failure_type"] == "TimeoutError"
    assert step["timeout_scope"] == "validation"
    assert step["exit_code"] is None


def test_stage_heartbeat_stops_before_command_returns(tmp_path, monkeypatch):
    from scripts import verify_final_hermetic as verifier

    observed = []
    repeated_heartbeat_seen = threading.Event()

    def capture(value, **kwargs):
        observed.append(value)
        if sum(value.startswith("HERMETIC_STAGE_ALIVE=") for value in observed) >= 2:
            repeated_heartbeat_seen.set()

    def complete_after_heartbeat(*args, **kwargs):
        assert repeated_heartbeat_seen.wait(5)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(verifier, "print", capture, raising=False)
    monkeypatch.setattr(subprocess, "run", complete_after_heartbeat)
    Run(tmp_path, heartbeat_seconds=0.001).command("heartbeat-probe", ["docker", "run"])
    assert (
        sum(
            value.startswith("HERMETIC_STAGE_ALIVE=heartbeat-probe:")
            for value in observed
        )
        >= 2
    )
    assert observed[-1] == "HERMETIC_STAGE_END=heartbeat-probe:0"
    assert not any(
        thread.name == "ctcc-hermetic-heartbeat" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_cleanup_reconciles_attempted_owned_start_even_if_ack_was_lost(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        if "ls" in args:
            return SimpleNamespace(stdout="ctcc-final-run-linux-full\n")
        if "inspect" in args:
            return SimpleNamespace(
                stdout=json.dumps(
                    [{"Config": {"Labels": {"org.ctcc.validation.run": "run"}}}]
                )
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", docker)
    result = cleanup_resources([("container", "ctcc-final-run-linux-full")], "run")
    assert result[0]["status"] == "REMOVED"
    assert calls[-1] == [
        "docker",
        "container",
        "rm",
        "-f",
        "-v",
        "ctcc-final-run-linux-full",
    ]


def test_cleanup_never_removes_other_owners_resource(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            stdout="existing\n"
            if "ls" in args
            else json.dumps(
                [{"Config": {"Labels": {"org.ctcc.validation.run": "other"}}}]
            )
        )

    monkeypatch.setattr(subprocess, "run", docker)
    result = cleanup_resources([("container", "existing")], "run")
    assert result[0]["status"] == "FAIL"
    assert all("rm" not in args for args in calls)


def test_cleanup_does_not_treat_daemon_failure_as_resource_absence(monkeypatch):
    def unavailable(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert cleanup_resources([("container", "new")], "run")[0]["status"] == "FAIL"


def test_cleanup_deadline_marks_remaining_resources_failed_without_more_docker(
    monkeypatch,
):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    calls = []

    def docker(args, **kwargs):
        calls.append((args, kwargs["timeout"]))
        clock.now = 1.0
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", docker)
    result = cleanup_resources(
        [("container", "first"), ("container", "second")],
        "run",
        budget_seconds=1,
        clock=clock,
    )
    assert [item["status"] for item in result] == ["ABSENT", "FAIL"]
    assert result[1]["failure_type"] == "TimeoutError"
    assert len(calls) == 1 and calls[0][1] == 1


@pytest.mark.parametrize(
    "case", ("empty", "skipped", "missing_module", "failure", "false_count")
)
@pytest.mark.parametrize("allow_unrelated_skips", (False, True))
def test_required_postgres_evidence_cannot_pass_without_executed_cases(
    tmp_path, case, allow_unrelated_skips
):
    from scripts.verify_final_hermetic import verify_pytest_report

    module = "required.pg.module" if case != "missing_module" else "unrelated"
    child = (
        "<skipped/>" if case == "skipped" else "<failure/>" if case == "failure" else ""
    )
    count = 0 if case == "empty" else 2 if case == "false_count" else 1
    cases = (
        ""
        if case == "empty"
        else f'<testcase classname="{module}" name="case">{child}</testcase>'
    )
    path = tmp_path / "result.xml"
    path.write_text(
        f'<testsuites><testsuite tests="{count}" errors="0" failures="{int(case == "failure")}" skipped="{int(case == "skipped")}">{cases}</testsuite></testsuites>'
    )
    with pytest.raises(ValueError):
        verify_pytest_report(
            path,
            required_modules=("required.pg.module",),
            allow_unrelated_skips=allow_unrelated_skips,
        )


def test_full_suite_platform_skip_is_retained_but_not_counted_as_pass(tmp_path):
    from scripts.verify_final_hermetic import verify_pytest_report

    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite tests="2" errors="0" failures="0" skipped="1">'
        '<testcase classname="executed" name="ran"/>'
        '<testcase classname="platform" name="skipped"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    result = verify_pytest_report(path)
    assert result["passed"] == 1 and result["skipped"] == 1
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_suite_required_postgres_all_pass_with_unrelated_platform_skip(tmp_path):
    from scripts.verify_final_hermetic import verify_pytest_report

    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite tests="4" errors="0" failures="0" skipped="1">'
        '<testcase classname="required.pg.module" name="first"/>'
        '<testcase classname="required.pg.module" name="second"/>'
        '<testcase classname="required.pg.other" name="concurrent"/>'
        '<testcase classname="platform" name="skipped"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    modules = ("required.pg.module", "required.pg.other")
    # PG-only validation remains strict unless the caller explicitly identifies
    # a full suite that can contain unrelated platform cases.
    with pytest.raises(ValueError, match="required_execution_not_verified"):
        verify_pytest_report(path, required_modules=modules)
    result = verify_pytest_report(
        path, required_modules=modules, allow_unrelated_skips=True
    )
    assert result["tests"] == 4 and result["passed"] == 3
    assert result["skipped"] == 1
    assert result["required_module_passed"] == {
        "required.pg.module": 2,
        "required.pg.other": 1,
    }


@pytest.mark.parametrize("separate_suites", (False, True))
def test_one_pass_cannot_hide_skip_in_same_required_module(tmp_path, separate_suites):
    from scripts.verify_final_hermetic import verify_pytest_report

    passed = '<testcase classname="required.pg.module" name="ran"/>'
    skipped = (
        '<testcase classname="required.pg.module" name="missing"><skipped/></testcase>'
    )
    if separate_suites:
        suites = (
            '<testsuite tests="1" errors="0" failures="0" skipped="0">'
            f"{passed}</testsuite>"
            '<testsuite tests="1" errors="0" failures="0" skipped="1">'
            f"{skipped}</testsuite>"
        )
    else:
        suites = (
            '<testsuite tests="2" errors="0" failures="0" skipped="1">'
            f"{passed}{skipped}</testsuite>"
        )
    path = tmp_path / "result.xml"
    path.write_text(f"<testsuites>{suites}</testsuites>")
    with pytest.raises(ValueError, match="required_execution_not_verified"):
        verify_pytest_report(
            path,
            required_modules=("required.pg.module",),
            allow_unrelated_skips=True,
        )


def test_unrelated_failure_is_not_hidden_by_allow_unrelated_skips(tmp_path):
    from scripts.verify_final_hermetic import verify_pytest_report

    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite tests="3" errors="0" failures="1" skipped="1">'
        '<testcase classname="required.pg.module" name="ran"/>'
        '<testcase classname="platform" name="skipped"><skipped/></testcase>'
        '<testcase classname="unrelated" name="failed"><failure/></testcase>'
        "</testsuite></testsuites>"
    )
    with pytest.raises(ValueError, match="required_execution_not_verified"):
        verify_pytest_report(
            path,
            required_modules=("required.pg.module",),
            allow_unrelated_skips=True,
        )


@pytest.mark.parametrize(
    ("class_name", "expected_success"),
    (("required.pg.module.SomeTests", False), ("required.pg.module_other", True)),
)
def test_required_module_class_skips_are_not_unrelated(
    tmp_path, class_name, expected_success
):
    from scripts.verify_final_hermetic import verify_pytest_report

    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite tests="2" errors="0" failures="0" skipped="1">'
        '<testcase classname="required.pg.module" name="ran"/>'
        f'<testcase classname="{class_name}" name="missing"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    if expected_success:
        result = verify_pytest_report(
            path,
            required_modules=("required.pg.module",),
            allow_unrelated_skips=True,
        )
        assert result["passed"] == 1 and result["skipped"] == 1
    else:
        with pytest.raises(ValueError, match="required_execution_not_verified"):
            verify_pytest_report(
                path,
                required_modules=("required.pg.module",),
                allow_unrelated_skips=True,
            )


def test_required_module_execution_can_be_in_a_test_class(tmp_path):
    from scripts.verify_final_hermetic import verify_pytest_report

    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0">'
        '<testcase classname="required.pg.module.SomeTests" name="ran"/>'
        "</testsuite></testsuites>"
    )
    result = verify_pytest_report(path, required_modules=("required.pg.module",))
    assert result["required_module_passed"] == {"required.pg.module": 1}


@pytest.mark.parametrize("case_name", ("other", "target"))
def test_exact_required_archive_case_cannot_be_omitted(tmp_path, case_name):
    from scripts.verify_final_hermetic import verify_pytest_report

    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0">'
        f'<testcase classname="required.pg.module" name="{case_name}"/>'
        "</testsuite></testsuites>"
    )
    required = (("required.pg.module", "target"),)
    if case_name == "other":
        with pytest.raises(ValueError, match="required_execution_not_verified"):
            verify_pytest_report(
                path,
                required_modules=("required.pg.module",),
                required_cases=required,
            )
    else:
        result = verify_pytest_report(
            path,
            required_modules=("required.pg.module",),
            required_cases=required,
        )
        assert result["required_case_passed"] == {"required.pg.module::target": 1}


def _runtime_custody_report(path, *, damage=None):
    import xml.etree.ElementTree as ET

    document = ET.Element("testsuites")
    suite = ET.SubElement(
        document, "testsuite", tests="4", errors="0", failures="0", skipped="0"
    )
    for module, name in RUNTIME_CUSTODY_PREFLIGHT_CASES:
        ET.SubElement(suite, "testcase", classname=module, name=name)
    if damage == "missing":
        suite.remove(suite[-1])
        suite.set("tests", "3")
    elif damage == "changed":
        suite[0].set("name", "test_different_case")
    elif damage == "skipped":
        ET.SubElement(suite[0], "skipped")
        suite.set("skipped", "1")
    path.write_bytes(ET.tostring(document))


def test_runtime_custody_preflight_uses_only_four_reviewed_credential_free_cases(
    tmp_path,
):
    calls = []
    mounts = ("-v", "isolated-results:/validation-results")

    def container(name, command, **kwargs):
        calls.append((name, command, kwargs))
        _runtime_custody_report(tmp_path / "runtime-bundle-custody-preflight.xml")

    report = run_runtime_bundle_custody_preflight(
        container, test_results=tmp_path, mounts=mounts
    )
    assert report["passed"] == report["tests"] == 4
    assert report["failures"] == report["errors"] == report["skipped"] == 0
    assert len(report["required_case_passed"]) == 4
    assert len(calls) == 1
    name, command, options = calls[0]
    assert name == "runtime-bundle-custody-preflight"
    assert options == {"mounts": mounts, "timeout": 300}
    assert command[:3] == ["python", "-m", "scripts.hermetic_pytest"]
    assert "--tb=no" in command
    assert "--show-capture=no" in command
    assert command[command.index("-o") + 1] == "junit_logging=no"
    assert command[-4:] == [
        f"{module.replace('.', '/')}.py::{case}"
        for module, case in RUNTIME_CUSTODY_PREFLIGHT_CASES
    ]


@pytest.mark.parametrize("damage", ("missing", "changed", "skipped"))
def test_runtime_custody_preflight_requires_all_four_executed_cases(tmp_path, damage):
    def container(*args, **kwargs):
        _runtime_custody_report(
            tmp_path / "runtime-bundle-custody-preflight.xml", damage=damage
        )

    with pytest.raises(ValueError, match="pytest_required_execution_not_verified"):
        run_runtime_bundle_custody_preflight(
            container, test_results=tmp_path, mounts=()
        )


def test_runtime_custody_failed_stage_cannot_reuse_existing_passing_xml(tmp_path):
    _runtime_custody_report(tmp_path / "runtime-bundle-custody-preflight.xml")

    def container(*args, **kwargs):
        raise RuntimeError("validation_step_failed:runtime-bundle-custody-preflight")

    with pytest.raises(RuntimeError, match="validation_step_failed"):
        run_runtime_bundle_custody_preflight(
            container, test_results=tmp_path, mounts=()
        )


def test_runtime_custody_no_actual_report_is_not_a_preflight_pass(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_runtime_bundle_custody_preflight(
            lambda *args, **kwargs: None, test_results=tmp_path, mounts=()
        )
