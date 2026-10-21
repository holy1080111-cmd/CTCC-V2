"""Full native ID width, exact Decimal decoding and non-rounding SQL contract."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import Numeric

from app.database.models.public_receipt_witness import PublicReceiptWitnessRevision
from app.database.repositories.public_receipt_witness import (
    PublicWitnessError,
    _native_identity_integer,
    verify_witness_chain,
)
from app.public_market_source.public_receipt_storage import (
    NATIVE_DEVICE_MAX,
    NATIVE_INODE_MAX,
    PublicJournalCheckpointV1,
)

ROOT = Path(__file__).resolve().parents[3]


def _row(device, inode):
    genesis = "a" * 64
    checkpoint = PublicJournalCheckpointV1(
        genesis_sha256=genesis,
        root_device=device,
        root_inode=inode,
        sequence=0,
        head_sha256=genesis,
        attempt_sequence=0,
        attempt_head_sha256=genesis,
    )
    raw = checkpoint.canonical_bytes().decode()
    checkpoint_hash = hashlib.sha256(raw.encode()).hexdigest()
    fields = (
        genesis,
        "0",
        "b" * 32,
        str(device),
        str(inode),
        "initialize",
        "idle",
        "",
        "",
        "",
        "",
        checkpoint_hash,
        raw,
    )
    return SimpleNamespace(
        journal_key=genesis,
        revision=0,
        journal_id="b" * 32,
        root_device=Decimal(device),
        root_inode=Decimal(inode),
        transition="initialize",
        state="idle",
        operation_id=None,
        plan_sha256=None,
        attempt_outcome=None,
        previous_record_sha256=None,
        checkpoint_json=raw,
        checkpoint_sha256=checkpoint_hash,
        record_sha256=hashlib.sha256("\x1f".join(fields).encode()).hexdigest(),
    )


@pytest.mark.parametrize(
    "device,inode",
    [
        (0, 2**64 + 3),
        (2**63 + 1, 2**64 + 3),
        (NATIVE_DEVICE_MAX, NATIVE_INODE_MAX),
        (1, 2),
    ],
)
def test_decimal_database_native_identity_replays_exact_canonical_integer(
    device, inode
):
    row = _row(device, inode)
    actual = verify_witness_chain((row,))
    assert type(actual.root_device) is int and actual.root_device == device
    assert type(actual.root_inode) is int and actual.root_inode == inode
    assert actual.record_sha256 == row.record_sha256
    assert actual.execution_authority is False


def test_scaled_and_exponent_decimal_decode_preserves_legacy_integer_record_hash():
    row = _row(10, 2000)
    row.root_device, row.root_inode = Decimal("10.000"), Decimal("2E+3")
    actual = verify_witness_chain((row,))
    assert (actual.root_device, actual.root_inode) == (10, 2000)
    assert actual.record_sha256 == row.record_sha256


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        1.0,
        "1",
        Decimal("1.2"),
        Decimal("1e-99"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal(-1),
        -1,
        2**64,
        Decimal(2**64),
    ],
)
def test_bad_device_values_cannot_round_or_impersonate_an_integer(value):
    with pytest.raises(PublicWitnessError, match="witness_native_identity_invalid"):
        _native_identity_integer(value, minimum=0, maximum=NATIVE_DEVICE_MAX)


@pytest.mark.parametrize(
    "value",
    [0, Decimal(0), 2**128, Decimal(2**128), Decimal("1.0000000000000000000000000001")],
)
def test_bad_inode_values_do_not_truncate_or_exceed_unsigned_128(value):
    with pytest.raises(PublicWitnessError, match="witness_native_identity_invalid"):
        _native_identity_integer(value, minimum=1, maximum=NATIVE_INODE_MAX)


def test_integer_subclass_is_not_a_native_database_identity():
    class ClaimedInteger(int):
        pass

    with pytest.raises(PublicWitnessError, match="witness_native_identity_invalid"):
        _native_identity_integer(
            ClaimedInteger(1), minimum=0, maximum=NATIVE_DEVICE_MAX
        )


@pytest.mark.parametrize(
    "device,inode", [(2**64, 1), (0, 2**128), (True, 1), (0, False)]
)
def test_checkpoint_native_integer_bounds_fail_closed_without_rewriting_bytes(
    device, inode
):
    with pytest.raises(ValidationError):
        _row(device, inode)


def test_numeric_model_has_no_scale_rounding_typmod():
    table = PublicReceiptWitnessRevision.__table__
    for name in ("root_device", "root_inode"):
        kind = table.c[name].type
        assert (
            isinstance(kind, Numeric) and kind.precision is None and kind.scale is None
        )
        assert table.c[name].nullable is False
    condition = next(
        c for c in table.constraints if c.name.endswith("native_identity_bounds")
    )
    assert str(NATIVE_DEVICE_MAX) in str(condition.sqltext)
    assert str(NATIVE_INODE_MAX) in str(condition.sqltext)
    assert "trunc(root_device)" in str(condition.sqltext)
    assert "trunc(root_inode)" in str(condition.sqltext)


def _migration():
    path = ROOT / "migrations/versions/0047_public_witness_native_identity.py"
    spec = importlib.util.spec_from_file_location("test_migration_0047", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_functions_embedded_exact_and_record_hash_algorithm_unchanged():
    module = _migration()
    tree = ast.parse(
        (ROOT / "migrations/versions/0024_public_receipt_witness.py").read_bytes()
    )
    upgrade = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    sql = [
        n.value.args[0].value
        for n in upgrade.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and n.value.args
        and isinstance(n.value.args[0], ast.Constant)
        and isinstance(n.value.args[0].value, str)
    ]
    assert module._LEGACY_INSERT in sql and module._LEGACY_APPEND in sql
    old_hash = module._LEGACY_INSERT.split("NEW.record_sha256 :=", 1)[1]
    assert module._INSERT.split("NEW.record_sha256 :=", 1)[1] == old_hash
    assert "public_witness_revision_denied" in module._INSERT
    assert "public_witness_capture_denied" in module._INSERT
    assert "CREATE OR REPLACE FUNCTION" in module._INSERT
    for legacy in (module._LEGACY_INSERT, module._LEGACY_APPEND):
        qualified = module._replace_function(legacy)
        assert (
            qualified.replace(
                "CREATE OR REPLACE FUNCTION public.", "CREATE FUNCTION ", 1
            )
            == legacy
        )
    assert "SECURITY DEFINER" in module._APPEND
    assert "SET search_path=pg_catalog,pg_temp" in module._APPEND
    assert "(p_record->>'revision')::bigint" in module._APPEND
    for field in ("root_device", "root_inode"):
        assert f"(p_record->>'{field}')::numeric" in module._APPEND
        assert f"(j->>'{field}')::numeric" in module._INSERT
        assert f"(j->>'{field}')::bigint" not in module._INSERT


def test_sql_preserves_raw_checkpoint_number_lexemes_and_rejects_duplicates_before_cast():
    module = _migration()
    assert "source_json := NEW.checkpoint_json::json;" in module._INSERT
    for field in ("root_device", "root_inode"):
        assert (
            f"json_typeof(source_json->'{field}') IS DISTINCT FROM 'number'"
            in module._INSERT
        )
        assert f"WHERE key='{field}') <> 1" in module._INSERT
        assert (
            f"jsonb_typeof(p_record->'{field}') IS DISTINCT FROM 'number'"
            in module._APPEND
        )
    assert module._INSERT.index("!~") < module._INSERT.index(
        "(source_json->>'root_device')::numeric"
    )
    assert module._APPEND.index("!~") < module._APPEND.index(
        "(p_record->>'root_device')::numeric"
    )
    assert module._DEVICE_LEXEME == "^(0|[1-9][0-9]{0,19})$"
    assert module._INODE_LEXEME == "^[1-9][0-9]{0,38}$"


def test_migration_upgrades_without_dml_and_downgrade_checks_empty_before_narrowing(
    monkeypatch,
):
    module = _migration()
    calls = []
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=calls.append))
    module.upgrade()
    assert module.revision == "0047" and module.down_revision == "0046"
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in calls[0]
    assert "TYPE NUMERIC USING root_device::numeric" in calls[1]
    assert "TYPE NUMERIC USING root_inode::numeric" in calls[1]
    assert "NUMERIC(" not in "\n".join(calls)
    assert "root_device=trunc(root_device)" in calls[2]
    assert "root_inode=trunc(root_inode)" in calls[2]
    calls.clear()
    module.downgrade()
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in calls[0]
    assert (
        "IF EXISTS(SELECT 1 FROM public.public_receipt_witness_revisions)" in calls[1]
    )
    assert "public_witness_wide_identity_downgrade_requires_empty" in calls[1]
    assert "DROP CONSTRAINT" in calls[2] and "TYPE BIGINT" in calls[3]
    assert calls[4] == module._replace_function(module._LEGACY_INSERT)
    assert calls[5] == module._replace_function(module._LEGACY_APPEND)


def test_schema_function_body_pins_are_source_derived_without_native_claim():
    from scripts.verify_native_witness_identity_schema import (
        NATIVE_WITNESS_IDENTITY_SCHEMA,
    )

    module = _migration()
    expected = {row[0]: row[1] for row in NATIVE_WITNESS_IDENTITY_SCHEMA["functions"]}
    for name, sql in (
        ("public_receipt_witness_append", module._APPEND),
        ("public_receipt_witness_insert_guard", module._INSERT),
    ):
        body = sql.split("AS $$", 1)[1].split("$$ LANGUAGE", 1)[0]
        assert hashlib.sha256(body.encode()).hexdigest() == expected[name]
    assert NATIVE_WITNESS_IDENTITY_SCHEMA["columns"] == [
        ["root_device", "numeric", -1, True, True, True],
        ["root_inode", "numeric", -1, True, True, True],
    ]


def test_unsealed_native_baseline_denies_even_matching_template_and_union(tmp_path):
    from copy import deepcopy

    from scripts.verify_final_hermetic import (
        ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
        ACCOUNT_REVISION_WITNESS_TRIGGERS,
        PUBLIC_RUNTIME_BUNDLE_SCHEMA,
        migration_revision_readback,
    )
    from scripts.verify_linux_shard_union import verify_union
    from scripts.verify_native_witness_identity_schema import (
        NATIVE_WITNESS_IDENTITY_SCHEMA,
        require_native_witness_identity_baseline,
    )

    assert NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"][-1] is None
    with pytest.raises(
        RuntimeError, match="native_witness_identity_schema_baseline_unsealed"
    ):
        require_native_witness_identity_baseline()
    with pytest.raises(
        RuntimeError, match="native_witness_identity_schema_baseline_unsealed"
    ):
        migration_revision_readback(
            "0047",
            "0047",
            True,
            True,
            ACCOUNT_REVISION_WITNESS_TRIGGERS,
            True,
            True,
            ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
            deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA),
            deepcopy(NATIVE_WITNESS_IDENTITY_SCHEMA),
        )
    with pytest.raises(
        RuntimeError, match="native_witness_identity_schema_baseline_unsealed"
    ):
        verify_union(tmp_path, commit="a" * 40, tree="b" * 40, manifest_sha256="c" * 64)


@pytest.mark.parametrize("drift", (None, "typmod", "function", "constraint", "trigger"))
def test_synthetically_sealed_v4_readback_requires_exact_native_catalog(
    monkeypatch, drift
):
    from copy import deepcopy

    from scripts.verify_final_hermetic import (
        ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
        ACCOUNT_REVISION_WITNESS_TRIGGERS,
        PUBLIC_RUNTIME_BUNDLE_SCHEMA,
        expected_migration_revision_readback,
        migration_revision_readback,
    )
    from scripts.verify_native_witness_identity_schema import (
        NATIVE_WITNESS_IDENTITY_SCHEMA,
    )

    constraint = list(NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"])
    constraint[-1] = "9" * 64  # Unit fixture only, never a native baseline.
    monkeypatch.setitem(NATIVE_WITNESS_IDENTITY_SCHEMA, "constraint", constraint)
    catalog = deepcopy(NATIVE_WITNESS_IDENTITY_SCHEMA)
    if drift == "typmod":
        catalog["columns"][0][2] = 0
    elif drift == "function":
        catalog["functions"][0][1] = "a" * 64
    elif drift == "constraint":
        catalog["constraint"][-1] = "b" * 64
    elif drift == "trigger":
        catalog["triggers"][0][1] = "D"
    args = (
        "0047",
        "0047",
        True,
        True,
        ACCOUNT_REVISION_WITNESS_TRIGGERS,
        True,
        True,
        ACCOUNT_MEASURED_HWM_V2_TRIGGERS_0045,
        deepcopy(PUBLIC_RUNTIME_BUNDLE_SCHEMA),
        catalog,
    )
    if drift:
        with pytest.raises(RuntimeError, match="migration_revision_readback_mismatch"):
            migration_revision_readback(*args)
    else:
        accepted = migration_revision_readback(*args)
        assert accepted == expected_migration_revision_readback("0047")
        assert accepted["schema"] == "ctcc.migration_revision_readback.v4"
