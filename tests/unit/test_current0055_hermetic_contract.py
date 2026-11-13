"""Offline checks for the DB0055 artifact contract, without Docker or SQL."""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import current0055_hermetic_contract as contract
from scripts import current0056_hermetic_contract as current56
from scripts import verify_current0055_hermetic as runner


def test_migration_readback_binds_actual_head_and_archive_digest():
    files = {
        "migrations/env.py": {"sha256": "a" * 64},
        "migrations/versions/0054_previous.py": {"sha256": "b" * 64},
        "migrations/versions/0055_latest.py": {"sha256": "c" * 64},
        "app/other.py": {"sha256": "d" * 64},
    }
    digest = contract.migration_digest(files)
    observed = {
        "schema": "ctcc.migration_identity.v1",
        "status": "PASS",
        "source_head": "0055",
        "database_head": "0055",
        "migration_sha256": digest,
        "execution_authority": False,
    }
    assert (
        contract.migration_readback(json.dumps(observed).encode(), digest) == observed
    )
    for override in (
        {"database_head": "0054"},
        {"migration_sha256": "0" * 64},
        {"execution_authority": 0},
    ):
        with pytest.raises(ValueError, match="^current55_database_identity_mismatch$"):
            contract.migration_readback(
                json.dumps(observed | override).encode(), digest
            )
    files["migrations/versions/0056_unreviewed.py"] = {"sha256": "e" * 64}
    with pytest.raises(ValueError, match="^current55_archive_migration_scope_invalid$"):
        contract.migration_digest(files)


def test_cases_only_components_cannot_claim_anchor_receipts():
    for suite, index in (("shard", 0), ("postgres", 1)):
        names = contract.component_stage_names(suite, index)
        assert (
            "current55-catalog-tests" not in names and "api-after-restart" not in names
        )
        assert contract.CATALOG_REPORT not in contract.component_artifacts(suite, index)
    names = contract.component_stage_names("postgres", 0)
    assert names.index("current55-catalog-tests") < names.index("postgres-intent")
    assert names.index("api-restart") < names.index("migration-identity-after-restart")


def test_docker_configuration_is_fresh_and_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/owned-docker.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_CONFIG", "synthetic-host-config")
    with runner._isolated_client(tmp_path):
        assert Path(os.environ["DOCKER_CONFIG"]) == tmp_path / "docker-client"
        assert list(Path(os.environ["DOCKER_CONFIG"]).iterdir()) == []
    assert os.environ["DOCKER_CONFIG"] == "synthetic-host-config"
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    with (
        pytest.raises(ValueError, match="^current55_local_docker_socket_required$"),
        runner._isolated_client(tmp_path),
    ):
        pytest.fail("remote Docker endpoint reached the runner")


@pytest.mark.parametrize(
    ("mode", "module", "reason", "kind", "expected_count", "error"),
    (
        (
            "shard",
            "tests.unit.test_highvol_installer_validator",
            "Native PowerShell required",
            "pytest.skip",
            1,
            None,
        ),
        (
            "full",
            "tests.unit.test_highvol_installer_validator",
            "Native PowerShell required",
            "pytest.skip",
            1,
            None,
        ),
        (
            "postgres",
            "tests.unit.test_highvol_installer_validator",
            "Native PowerShell required",
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            "tests.integration.test_native_filled_flat_current55_pg",
            "Native PowerShell required",
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            "tests.unit.test_highvol_installer_validator",
            "unexpected missing dependency",
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            "tests.unit.test_highvol_installer_validator",
            "Native PowerShell required",
            "pytest.xfail",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            "tests.unit.test_highvol_installer_validator",
            "Native PowerShell required",
            "pytest.skip",
            0,
            "current55_linux_skip_count_mismatch",
        ),
    ),
)
def test_current55_linux_skip_is_bound_to_native_case_and_reason(
    mode, module, reason, kind, expected_count, error
):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    case = ET.SubElement(
        suite,
        "testcase",
        classname=module,
        name="test_unlisted_package_member_rejects_before_execution[unexpected.txt]",
    )
    ET.SubElement(case, "skipped", type=kind, message=reason)
    raw = ET.tostring(root)
    if error is None:
        contract._verify_platform_skips(raw, mode=mode, expected_skipped=expected_count)
    else:
        with pytest.raises(ValueError, match=f"^{error}$"):
            contract._verify_platform_skips(
                raw, mode=mode, expected_skipped=expected_count
            )


_LIVE_CUSTODY_MODULE = "tests.unit.exchange.test_live_account_config_custody_v1"
_WINDOWS_CUSTODY_CASE = (
    "test_windows_private_file_is_checked_before_write_and_remains_reserved"
)
_WINDOWS_ACL_REASON = "requires native Windows ACLs"


@pytest.mark.parametrize(
    ("mode", "module", "name", "reason", "kind", "expected_count", "error"),
    (
        (
            "shard",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE,
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            1,
            None,
        ),
        (
            "full",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE,
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            1,
            None,
        ),
        (
            "postgres",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE,
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            "tests.unit.exchange.test_other_custody",
            _WINDOWS_CUSTODY_CASE,
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            _LIVE_CUSTODY_MODULE,
            "test_live_config_custody_preserves_late_failed_attempt",
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE + "[forged]",
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE,
            "wrong platform reason",
            "pytest.skip",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE,
            _WINDOWS_ACL_REASON,
            "pytest.xfail",
            1,
            "current55_unapproved_linux_skip",
        ),
        (
            "shard",
            _LIVE_CUSTODY_MODULE,
            _WINDOWS_CUSTODY_CASE,
            _WINDOWS_ACL_REASON,
            "pytest.skip",
            0,
            "current55_linux_skip_count_mismatch",
        ),
    ),
)
def test_current56_linux_skip_adds_only_the_exact_native_custody_case(
    mode, module, name, reason, kind, expected_count, error
):
    root = ET.Element("testsuite")
    case = ET.SubElement(root, "testcase", classname=module, name=name)
    ET.SubElement(case, "skipped", type=kind, message=reason)
    raw = ET.tostring(root)
    with pytest.raises(ValueError, match="^current55_unapproved_linux_skip$"):
        contract._verify_platform_skips(raw, mode=mode, expected_skipped=expected_count)
    if error is None:
        current56._verify_platform_skips(
            raw, mode=mode, expected_skipped=expected_count
        )
    else:
        with pytest.raises(ValueError, match=f"^{error}$"):
            current56._verify_platform_skips(
                raw, mode=mode, expected_skipped=expected_count
            )
