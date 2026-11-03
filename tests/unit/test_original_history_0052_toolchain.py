"""UNRUN pure syntax/closed-contract tests; no synthetic native admission."""

import ast

import pytest

from app.domain import account_history_original_frame_v1 as frame
from scripts import original_history_0052_toolchain as current
from scripts import verify_final_hermetic as legacy


@pytest.mark.parametrize(
    "name",
    [
        "MIGRATION_REVISION_PROBE_0052",
        "HISTORY_PARTITION_RETENTION_PROBE_0052",
        "COVERAGE_NATIVE_CATALOG_CAPTURE_0052",
        "HISTORY_PARTITION_NATIVE_CATALOG_CAPTURE_0052",
        "PROSPECTIVE_CUTOVER_NATIVE_CATALOG_CAPTURE_0052",
        "PREDECESSOR_0047_IDENTITY_PROBE_0052",
        "ORIGINAL_FRAME_INSTALLATION_NATIVE_CATALOG_CAPTURE_0052",
        "ORIGINAL_FRAME_ABSENT_PROBE_0051",
    ],
)
def test_new_fixed_probe_templates_are_valid_python_without_running_them(name):
    # Parsing is the whole test. No template, CLI, SQL or subprocess is run.
    assert isinstance(ast.parse(getattr(current, name)), ast.Module)


def test_template_replacement_refuses_missing_or_repeated_source_marker():
    assert current._once("left MARK right", "MARK", "NEW") == "left NEW right"
    for source in ("absent", "MARK MARK"):
        with pytest.raises(RuntimeError, match="^db0052_predecessor_template_changed$"):
            current._once(source, "MARK", "NEW")


def _negative_frame_capture(head):
    # These are explicitly synthetic NEGATIVE source pins, never native pins.
    files = {
        name: {"sha256": "a" * 64}
        for name in current.ORIGINAL_FRAME_INSTALLATION_CAPTURE_PATHS
    }
    document = {
        "schema": "ctcc.original_history_frame_owner_catalog_observation.v1",
        "source_head": head,
        "source_migration_sha256": "b" * 64,
        "database_head": head,
        "session_facts": ["17", "170000", "owner", "owner", "synthetic", "pg_catalog"],
        "catalog": None,
        "partition_catalog": {
            "server": {
                "version_num": "170000",
                "current_user": "owner",
                "session_user": "owner",
                "database": "synthetic",
            }
        },
        "installation": {"passed": True},
        "source_files": {name: row["sha256"] for name, row in files.items()},
        "manifest_sha256": "c" * 64,
        "native_baseline_admission": False,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    return files, document


@pytest.mark.parametrize("head", ["0051", "0053"])
def test_wrong_head_refuses_before_any_native_catalog_verifier(monkeypatch, head):
    files, document = _negative_frame_capture(head)

    def forbidden(*_args, **_kwargs):
        pytest.fail("wrong_head_reached_native_verifier")

    monkeypatch.setattr(frame, "verify_owner_installation_catalog", forbidden)
    with pytest.raises(
        ValueError, match="^original_frame_owner_catalog_observation_invalid$"
    ):
        current.verify_original_frame_installation_native_catalog_capture_0052(
            document,
            migration_sha256="b" * 64,
            manifest_sha256="c" * 64,
            source_files=files,
        )


def test_caller_passed_true_cannot_replace_actual_catalogs():
    files, document = _negative_frame_capture("0052")
    with pytest.raises(Exception) as error:
        current.verify_original_frame_installation_native_catalog_capture_0052(
            document,
            migration_sha256="b" * 64,
            manifest_sha256="c" * 64,
            source_files=files,
        )
    assert type(error.value).__name__ == "AccountHistoryPartitionError"
    assert str(error.value) == "account_history_partition_catalog_shape"


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_contradictory_session_observation_refuses_before_native_verifier(
    monkeypatch, index
):
    files, document = _negative_frame_capture("0052")
    document["session_facts"][index] = "170001" if index == 1 else "contradictory"

    def forbidden(*_args, **_kwargs):
        pytest.fail("contradictory_session_reached_native_verifier")

    monkeypatch.setattr(frame, "verify_owner_installation_catalog", forbidden)
    with pytest.raises(
        ValueError, match="^original_frame_owner_catalog_observation_invalid$"
    ):
        current.verify_original_frame_installation_native_catalog_capture_0052(
            document,
            migration_sha256="b" * 64,
            manifest_sha256="c" * 64,
            source_files=files,
        )


def test_0051_revision_declaration_cannot_enter_0052_retention_contract():
    with pytest.raises(
        RuntimeError, match="^history_partition_nonempty_snapshot_invalid$"
    ):
        current.history_partition_nonempty_snapshot_0052(
            legacy.expected_migration_revision_readback("0051"), {}
        )


def test_0052_formal_probe_fetches_actual_frame_and_retains_0051_template():
    old = legacy.MIGRATION_REVISION_PROBE_0051
    new = current.MIGRATION_REVISION_PROBE_0052
    assert "original_frame_catalog=original_frame" in new
    assert "await connection.scalar(text(FRAME_SQL))" in new
    assert 'isolation_level="READ COMMITTED"' in new
    assert "sys.argv[1]!='0052'" in new
    assert "original_frame_catalog=original_frame" not in old
    assert 'isolation_level="REPEATABLE READ"' in old
    assert "'0053'" not in new


def test_function_absence_is_measured_at_true_0051_predecessor():
    expected = current.expected_original_frame_absent_readback()
    assert expected == {
        "schema": "ctcc.original_history_frame_absent.v1",
        "database_head": "0051",
        "functions_present": [],
        "execution_authority": False,
    }
    assert (
        "await connection.run_sync(database_head)"
        in current.ORIGINAL_FRAME_ABSENT_PROBE_0051
    )
    assert "p.proname=:name" in current.ORIGINAL_FRAME_ABSENT_PROBE_0051
