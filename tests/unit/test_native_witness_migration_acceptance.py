"""DB0047 downgrade evidence, without PostgreSQL or local process launches."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_final_hermetic import (
    MIGRATION_REVISION_PROBE,
    MIGRATION_REVISION_READER,
    NATIVE_IDENTITY_DOWNGRADE_DENIAL,
    NATIVE_IDENTITY_RETENTION_PROBE,
    NATIVE_WITNESS_IDENTITY_SCHEMA,
    NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL,
    Run,
    expected_migration_revision_readback,
    expected_native_identity_empty_downgrade_readback,
    native_identity_empty_before_readback,
    native_identity_empty_downgrade_readback,
    native_identity_nonempty_downgrade_readback,
    native_identity_nonempty_snapshot,
    verify_native_identity_downgrade_denial_log,
)
from scripts.verify_native_witness_identity_schema import (
    expected_legacy_witness_identity_schema,
)


def test_probes_compile_and_capture_head_catalog_and_rows_in_one_readonly_snapshot():
    for name, source in (
        ("reader", MIGRATION_REVISION_READER),
        ("revision", MIGRATION_REVISION_PROBE),
        ("retention", NATIVE_IDENTITY_RETENTION_PROBE),
    ):
        compile(source, f"<synthetic-compile-{name}>", "exec")
        assert source.count("engine.connect()") == 1
        assert 'isolation_level="REPEATABLE READ"' in source
        assert 'text("SET TRANSACTION READ ONLY")' in source
    assert "read_head(retain=True)" in NATIVE_IDENTITY_RETENTION_PROBE
    assert "native-identity-0047-empty-down" in MIGRATION_REVISION_PROBE


def test_distinct_empty_downgrade_embeds_unmodified_legacy_v3_and_exact_sql_bodies():
    observed = expected_native_identity_empty_downgrade_readback("c" * 64)
    assert observed["legacy_revision_readback"] == expected_migration_revision_readback(
        "0046"
    )
    assert (
        observed["legacy_revision_readback"]["schema"]
        == "ctcc.migration_revision_readback.v3"
    )
    assert (
        "public_witness_native_identity_schema"
        not in observed["legacy_revision_readback"]
    )
    legacy = observed["legacy_witness_catalog"]
    assert legacy["constraint"] == []
    assert [row[1] for row in legacy["columns"]] == ["bigint", "bigint"]
    source = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/0024_public_receipt_witness.py"
    )
    sql = [
        node.value
        for node in ast.walk(ast.parse(source.read_bytes()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for name in (
        "public_receipt_witness_append",
        "public_receipt_witness_insert_guard",
    ):
        statement = next(value for value in sql if f"CREATE FUNCTION {name}(" in value)
        body = statement.split("AS $$", 1)[1].split("$$ LANGUAGE", 1)[0]
        expected = next(row[1] for row in legacy["functions"] if row[0] == name)
        assert hashlib.sha256(body.encode()).hexdigest() == expected
    assert (
        native_identity_empty_downgrade_readback(
            observed["legacy_revision_readback"], legacy, "c" * 64
        )
        == observed
    )


@pytest.mark.parametrize(
    "drift",
    ("numeric", "new_function", "constraint", "read_acl", "trigger", "bool_alias"),
)
def test_empty_downgrade_rejects_old_v3_alone_hiding_bad_witness_restoration(drift):
    old = expected_migration_revision_readback("0046")
    catalog = expected_legacy_witness_identity_schema()
    if drift == "numeric":
        catalog["columns"][0][1] = "numeric"
    elif drift == "new_function":
        catalog["functions"][0][1] = NATIVE_WITNESS_IDENTITY_SCHEMA["functions"][0][1]
    elif drift == "constraint":
        catalog["constraint"] = deepcopy(NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"])
    elif drift == "read_acl":
        catalog["functions"][-1][-1] = False
    elif drift == "trigger":
        catalog["triggers"][0][1] = "D"
    else:
        catalog["table_present"] = 1
    with pytest.raises(
        RuntimeError, match="native_identity_empty_downgrade_readback_mismatch"
    ):
        native_identity_empty_downgrade_readback(old, catalog, "c" * 64)


@pytest.fixture
def sealed(monkeypatch):
    value = list(NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"])
    value[-1] = "9" * 64  # Synthetic unit metadata; not a native schema seal.
    monkeypatch.setitem(NATIVE_WITNESS_IDENTITY_SCHEMA, "constraint", value)


def _snapshot():
    return native_identity_nonempty_snapshot(
        expected_migration_revision_readback("0047"),
        {
            "row_count": 4,
            "small_row_count": 1,
            "wide_row_count": 3,
            "records_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
        },
    )


@pytest.mark.parametrize("digest", ("c" * 64, None, "bad", 1))
def test_empty_before_requires_current_head_and_exact_actual_object_digest(
    sealed, digest
):
    if isinstance(digest, str) and len(digest) == 64:
        observed = native_identity_empty_before_readback(
            expected_migration_revision_readback("0047"), digest
        )
        assert observed["object_custody_sha256"] == digest
        assert observed["revision_readback"]["observed"] == "0047"
    else:
        with pytest.raises(
            RuntimeError, match="native_identity_object_custody_digest_invalid"
        ):
            native_identity_empty_before_readback(
                expected_migration_revision_readback("0047"), digest
            )


def test_nonempty_snapshot_covers_every_column_and_actual_catalog_custody(sealed):
    before = _snapshot()
    receipt = native_identity_nonempty_downgrade_readback(before, deepcopy(before))
    assert receipt["result"] == "DENIED" and receipt["rollback"] == "EXACT"
    assert receipt["policy_refusal"] == NATIVE_IDENTITY_DOWNGRADE_DENIAL
    assert "row_to_json(b)::text" in NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL
    assert "LIMIT 100001" in NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL
    assert all(
        value in NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL
        for value in (
            "proacl",
            "proowner",
            "prosrc",
            "pg_get_constraintdef",
            "pg_get_triggerdef",
        )
    )


@pytest.mark.parametrize(
    "field,value",
    (
        ("small_row_count", 0),
        ("wide_row_count", 0),
        ("row_count", 100001),
        ("row_count", True),
        ("row_count", 5),
        ("records_sha256", "bad"),
    ),
)
def test_nonempty_requires_both_sizes_complete_bounded_counts_and_hashes(
    sealed, field, value
):
    before = _snapshot()
    before["retention"][field] = value
    with pytest.raises(RuntimeError, match="native_identity_nonempty"):
        native_identity_nonempty_downgrade_readback(before, before)


@pytest.mark.parametrize("drift", ("record", "catalog", "head", "authority"))
def test_nonempty_downgrade_rejects_any_changed_data_catalog_head_or_false_alias(
    sealed, drift
):
    before = _snapshot()
    after = deepcopy(before)
    if drift == "record":
        after["retention"]["records_sha256"] = "c" * 64
    elif drift == "catalog":
        after["retention"]["catalog_sha256"] = "d" * 64
    elif drift == "head":
        after["revision_readback"]["observed"] = "0046"
    else:
        after["execution_authority"] = 0
    with pytest.raises(RuntimeError, match="native_identity_nonempty"):
        native_identity_nonempty_downgrade_readback(before, after)


def _denial():
    return (
        "INFO Running downgrade 0047 -> 0046\n"
        '  File "/app/migrations/versions/0047_public_witness_native_identity.py", line 320\n'
        f"asyncpg.exceptions.RaiseError: {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\n"
        f"sqlalchemy.exc.DBAPIError: (asyncpg.Error): {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\n"
    )


@pytest.mark.parametrize(
    "drift",
    (
        None,
        "wrong_transition",
        "extra_transition",
        "wrong_policy",
        "duplicate_error",
        "no_module",
    ),
)
def test_exact47_refusal_is_separate_from_old42_refusal(tmp_path, drift):
    text = _denial()
    if drift == "wrong_transition":
        text = text.replace("0047 -> 0046", "0042 -> 0041")
    elif drift == "extra_transition":
        text += "INFO Running downgrade 0046 -> 0045\n"
    elif drift == "wrong_policy":
        text = text.replace(
            NATIVE_IDENTITY_DOWNGRADE_DENIAL,
            "submission_transport_authenticity_downgrade_denied",
        )
    elif drift == "duplicate_error":
        text += f"asyncpg.exceptions.RaiseError: {NATIVE_IDENTITY_DOWNGRADE_DENIAL}\n"
    elif drift == "no_module":
        text = text.replace(
            "/app/migrations/versions/0047_public_witness_native_identity.py", "unknown"
        )
    path = tmp_path / "refusal.log"
    path.write_text(text)
    if drift:
        with pytest.raises(
            ValueError, match="native_identity_downgrade_policy_refusal_unverified"
        ):
            verify_native_identity_downgrade_denial_log(path)
    else:
        verify_native_identity_downgrade_denial_log(path)


@pytest.mark.parametrize("code,valid", ((1, True), (0, True), (1, False)))
def test_runner_accepts_only_failed_exact47_policy_and_records_it(
    tmp_path, monkeypatch, code, valid
):
    def fake_run(*_args, **kwargs):
        output = (
            _denial()
            if valid
            else _denial().replace(NATIVE_IDENTITY_DOWNGRADE_DENIAL, "unexpected")
        )
        kwargs["stdout"].write(output.encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(subprocess, "run", fake_run)
    run = Run(tmp_path)
    if code and valid:
        run.command(
            "native-denial",
            ["synthetic-no-process"],
            expected_refusal=NATIVE_IDENTITY_DOWNGRADE_DENIAL,
        )
    else:
        with pytest.raises((RuntimeError, ValueError), match="policy_refusal"):
            run.command(
                "native-denial",
                ["synthetic-no-process"],
                expected_refusal=NATIVE_IDENTITY_DOWNGRADE_DENIAL,
            )
    step = json.loads((tmp_path / "steps.json").read_text())[0]
    assert step.get("policy_refusal") == (
        NATIVE_IDENTITY_DOWNGRADE_DENIAL if code and valid else None
    )
