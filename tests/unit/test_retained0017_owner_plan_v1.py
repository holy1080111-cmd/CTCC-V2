"""Source/identity/snapshot refusal contracts; no SQL, account or migration."""

from __future__ import annotations

import copy
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts import retained0017_owner_plan_v1 as subject
from tests.native_private_temp_v1 import _private_root_path


def _read_published_bytes(path):
    """Independent file read, including Windows roots beyond MAX_PATH."""
    if os.name == "nt":
        text = os.fspath(path)
        assert path.is_absolute()
        if not text.startswith("\\\\?\\"):
            text = (
                "\\\\?\\UNC\\" + text[2:]
                if text.startswith("\\\\")
                else "\\\\?\\" + text
            )
        path = Path(text)
    return path.read_bytes()


def _pins():
    return subject.RetainedOwnerPins0017V1(
        "a" * 40,
        "b" * 40,
        "c" * 64,
        "d" * 64,
        subject.RetainedOwnerRoute0017V1(
            "e" * 64,
            "f" * 64,
            "1" * 64,
            "postgres.test",
            5432,
            "retained",
            "ctcc_owner",
            42,
            43,
        ),
    )


def _snapshot():
    return {
        "schema": "ctcc.retained0017.physical_snapshot.v1",
        "database_head": "0017",
        "catalog_sha256": "2" * 64,
        "actor_sha256": "3" * 64,
        "tables": [["legacy", 44, ["id", "uncertain"], 2, 90, "4" * 64]],
        "sequences": [["legacy_id_seq", 45, 7, True]],
        "total_row_bytes": 90,
        "exchange_flat": None,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def test_source_plan_uses_original17_columns_and_exact18_to55_chain():
    plan = subject._source_plan()
    assert plan["source_head"] == "0058" and plan["database_head_required"] == "0017"
    assert [row[0] for row in plan["migrations"]] == [f"{i:04d}" for i in range(1, 56)]
    columns = plan["minimum17_columns"]
    assert columns["analysis_runs"][-2:] == ["created_at", "updated_at"]
    assert "realized_pnl_events" in columns["demo_automation_state"]
    assert "operator_reconciled_at" in columns["okx_live_execution_intents"]
    assert "control_revision" not in columns["demo_automation_state"]
    assert "demo_account_controls" not in columns
    assert plan["mutation_implemented"] is False
    assert plan["requires_separate_0034_policy_review"] is True
    assert plan["execution_authority"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_commit", "a" * 64),
        ("source_tree", "B" * 40),
        ("source_archive_sha256", "not-a-hash"),
        ("preserved_backup_sha256", None),
        ("expected_catalog_sha256", True),
    ],
)
def test_source_and_preservation_pins_are_closed(field, value):
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._checked_pins(replace(_pins(), **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_head", "0058"),
        ("database_head", "17"),
        ("port", True),
        ("port", 0),
        ("port", 65536),
        ("owner_oid", True),
        ("database_oid", 0),
        ("database", ""),
        ("owner", ""),
        ("cluster_system_identifier_sha256", "wrong"),
    ],
)
def test_actual17_route_cannot_be_projected_or_missing(field, value):
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._checked_pins(
            replace(_pins(), route=replace(_pins().route, **{field: value}))
        )


def _relations():
    return (
        ("legacy", 44, "r", "p", 43, False, False, False, ["id", "uncertain"]),
        (
            "legacy_id_seq",
            45,
            "S",
            "p",
            43,
            False,
            False,
            False,
            ["last_value", "log_cnt", "is_called"],
        ),
    )


@pytest.mark.parametrize(
    "index,value",
    [
        (0, "legacy; DROP TABLE x"),
        (1, True),
        (2, "v"),
        (2, "f"),
        (3, "u"),
        (4, 99),
        (5, True),
        (6, True),
        (7, True),
        (8, ["uncertain", "id"]),
        (8, ["id"]),
        (8, ["id", "uncertain", "extra"]),
    ],
)
def test_schema_inventory_rejects_unknown_shape_owner_or_hidden_rows(index, value):
    rows = list(_relations())
    changed = list(rows[0])
    changed[index] = value
    rows[0] = tuple(changed)
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._checked_relations(tuple(rows), {"legacy": ["id", "uncertain"]}, 43)


def test_schema_inventory_requires_all_original_relations_and_unique_identity():
    shape = {"legacy": ["id", "uncertain"]}
    assert set(subject._checked_relations(_relations(), shape, 43)) == {
        "legacy",
        "legacy_id_seq",
    }
    for rows in ((), _relations()[1:], _relations() + (_relations()[0],)):
        with pytest.raises(subject.Retained0017PlanRefused):
            subject._checked_relations(rows, shape, 43)
    aliased = list(_relations()[1])
    aliased[1] = 44
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._checked_relations((_relations()[0], tuple(aliased)), shape, 43)


@pytest.mark.parametrize(
    "row",
    [
        (True, 0, "a" * 64),
        (0, False, "a" * 64),
        (-1, 0, "a" * 64),
        (subject.MAX_ROWS + 1, 0, "a" * 64),
        (0, subject.MAX_TABLE_BYTES + 1, "a" * 64),
        (0, 0, None),
    ],
)
def test_multiset_capacity_unknown_and_invalid_hash_refuse(row):
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._checked_table_result(row)


@pytest.mark.parametrize(
    "columns", [[], ["id", "id"], ["id;DROP TABLE secret"], [True], ("id",)]
)
def test_original_row_encoding_accepts_only_exact_native_source_column_names(columns):
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._row_expression(columns)


@pytest.mark.parametrize(
    "field", ["tables", "sequences", "catalog_sha256", "actor_sha256"]
)
def test_independent_reads_must_retain_rows_sequence_schema_and_actor(field):
    first = _snapshot()
    second = copy.deepcopy(first)
    second[field] = [] if type(first[field]) is list else "0" * 64
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._join(first, second, _pins(), {"source": "fixture"})


def test_equal_snapshots_and_reviewed_hash_do_not_prove_backup_link_flat_or_ddl_authority():
    first = _snapshot()
    value = subject._join(first, copy.deepcopy(first), _pins(), {"source": "fixture"})
    assert value["status"] == "OBSERVED_UNREVIEWED_CATALOG_DATA"
    assert value["expected_catalog_pin_matched"] is False
    reviewed = replace(_pins(), expected_catalog_sha256=first["catalog_sha256"])
    value = subject._join(first, copy.deepcopy(first), reviewed, {"source": "fixture"})
    assert value["expected_catalog_pin_matched"] is True
    for flag in (
        "backup_to_database_lineage_verified",
        "concurrent_writers_excluded",
        "schema_upgrade_authorized",
        "account_complete",
        "execution_authority",
    ):
        assert value[flag] is False
    assert value["exchange_flat"] is None and value["admission"] == "DENY"
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._join(
            first, first, replace(reviewed, expected_catalog_sha256="0" * 64), {}
        )


def test_public_projection_never_exposes_private_snapshot_rows_or_route():
    value = subject._join(
        _snapshot(), _snapshot(), _pins(), {"private_marker": "never-stdout"}
    )
    raw = subject._canonical(subject._public_result(value))
    for token in (
        b"never-stdout",
        b"postgres.test",
        b"ctcc_owner",
        b"legacy",
        b"uncertain",
    ):
        assert token not in raw
    assert b'"exchange_flat":null' in raw and b'"execution_authority":false' in raw


def test_private_receipt_uses_native_private_directory_and_no_clobber(tmp_path):
    root = _private_root_path(tmp_path)
    value = subject._join(_snapshot(), _snapshot(), _pins(), {})
    digest = subject._publish_private_receipt(value, root)
    path = root / ("retained0017-plan-" + digest + ".json")
    assert _read_published_bytes(path) == subject._canonical(value)
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._publish_private_receipt(value, root)
    assert _read_published_bytes(path) == subject._canonical(value)


@pytest.mark.parametrize("change", ["none", "hash", "extra", "foreign_head"])
def test_private_reviewed_inputs_are_closed_and_independently_hash_pinned(
    tmp_path, change
):
    root = _private_root_path(tmp_path)
    value = {"schema": subject.PINS_SCHEMA, **asdict(_pins())}
    if change == "extra":
        value["passed"] = True
    if change == "foreign_head":
        value["route"]["database_head"] = "0058"
    raw = subject._canonical(value)
    path = root / "reviewed-pins.json"
    if os.name == "nt":
        from app.trade_evidence.native_private_root_v2 import (
            _open_new_windows_private_evidence_file,
        )
        from app.trade_evidence.storage import _windows_root

        # A new file under an elevated Windows runner can otherwise be owned
        # by Administrators even when the disposable parent is user-owned.
        with _windows_root(root) as opened:
            fd = _open_new_windows_private_evidence_file(opened, path.name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
    else:
        path.write_bytes(raw)
        path.chmod(0o600)
    if change == "none":
        assert subject._load_pins(path, subject._sha(raw)) == _pins()
    else:
        with pytest.raises(subject.Retained0017PlanRefused):
            subject._load_pins(
                path, "0" * 64 if change == "hash" else subject._sha(raw)
            )


def test_late_private_receipt_identity_failure_preserves_accepted_bytes(
    tmp_path, monkeypatch
):
    from app.trade_qualification import account_native_proof_storage as storage

    root = _private_root_path(tmp_path)
    value = subject._join(_snapshot(), _snapshot(), _pins(), {})
    actual = storage._root_identity(root)
    calls = []

    def changed(_):
        calls.append(True)
        return actual if len(calls) == 1 else (actual[0], actual[1] + 1)

    monkeypatch.setattr(storage, "_root_identity", changed)
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._publish_private_receipt(value, root)
    raw = subject._canonical(value)
    assert (
        _read_published_bytes(
            root / ("retained0017-plan-" + subject._sha(raw) + ".json")
        )
        == raw
    )


def test_cli_refusal_redacts_private_exception_and_never_connects(monkeypatch, capsys):
    def refuse(*_):
        raise RuntimeError("never-stdout-secret-dsn-account")

    monkeypatch.setattr(subject, "_load_pins", refuse)
    monkeypatch.setattr(
        subject, "create_async_engine", lambda *_a, **_k: pytest.fail("no DB call")
    )
    assert (
        subject.main(
            [
                "--inspect-retained-clone",
                "--reviewed-pins-file",
                "private",
                "--reviewed-pins-sha256",
                "a" * 64,
                "--owner-dsn-file",
                "private",
                "--ca-file",
                "private",
                "--source-archive-file",
                "source.tar",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "never-stdout" not in captured.out + captured.err
    assert '"execution_authority":false' in captured.out


@pytest.mark.parametrize(
    "suffix",
    [
        ["--unknown", "PRIVATE_DSN_CANARY"],
        ["--reviewed-pins-sha256"],
        ["PRIVATE_DSN_CANARY"],
    ],
)
def test_cli_parser_refuses_without_echoing_unknown_or_invalid_private_arguments(
    monkeypatch, capsys, suffix
):
    monkeypatch.setattr(
        subject, "create_async_engine", lambda *_a, **_k: pytest.fail("no DB call")
    )
    arguments = [
        "--inspect-retained-clone",
        "--reviewed-pins-file",
        "PRIVATE_PATH_CANARY",
        "--reviewed-pins-sha256",
        "a" * 64,
        "--owner-dsn-file",
        "PRIVATE_PATH_CANARY",
        "--ca-file",
        "PRIVATE_PATH_CANARY",
        "--source-archive-file",
        "PRIVATE_PATH_CANARY",
    ]
    assert subject.main(arguments + suffix) == 1
    captured = capsys.readouterr()
    assert "PRIVATE_DSN_CANARY" not in captured.out + captured.err
    assert "PRIVATE_PATH_CANARY" not in captured.out + captured.err
    assert '"status":"REFUSED"' in captured.out


def test_readonly_snapshot_rejects_caller_fake_connection_before_sql():
    with pytest.raises(subject.Retained0017PlanRefused):
        import asyncio

        asyncio.run(subject._snapshot(object(), _pins(), {}))
