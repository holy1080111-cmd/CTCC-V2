"""UNRUN negative/declaration contracts; no fabricated native positive fixture."""

import pytest

from app.domain import account_history_original_frame_v1 as frame
from scripts import verify_final_hermetic as hermetic


@pytest.mark.parametrize("revision", ["0053", "0099"])
def test_unknown_migration_declaration_refuses(revision):
    with pytest.raises(RuntimeError, match="^migration_revision_readback_mismatch$"):
        hermetic.expected_migration_revision_readback(revision)


@pytest.mark.parametrize("revision", ["0053", "0099"])
def test_unknown_actual_migration_reader_refuses(revision):
    with pytest.raises(RuntimeError, match="^migration_revision_readback_mismatch$"):
        hermetic.migration_revision_readback(revision, revision, False, False, ())


def test_0052_declaration_layers_0051_without_relabelling_old_declared_revision():
    old = hermetic.expected_migration_revision_readback("0051")
    current = hermetic.expected_migration_revision_readback("0052")
    assert old["schema"] == "ctcc.migration_revision_readback.v8"
    assert old["expected"] == old["observed"] == "0051"
    assert current["schema"] == "ctcc.migration_revision_readback.v9"
    assert current["expected"] == current["observed"] == "0052"
    for name in old.keys() - {"schema", "expected", "observed"}:
        assert current[name] == old[name]
    installation = current["original_history_frame_owner_installation"]
    assert installation == frame.expected_owner_installation_schema()
    assert installation["execute_acl"] == "OWNER_ONLY_NO_COLLECTOR_OR_PUBLIC_GRANT"
    assert installation["runtime_admission"] is False
    assert installation["native_history_prefix_verified"] is False
    assert installation["account_complete"] is False
    assert installation["execution_authority"] is False


def test_owner_installer_has_no_seal_off_keyword():
    with pytest.raises(TypeError):
        frame.verify_owner_installation_catalog({}, {}, require_native_baseline=False)
