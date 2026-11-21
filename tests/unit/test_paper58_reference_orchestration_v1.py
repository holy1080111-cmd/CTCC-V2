"""Purpose isolation and exact required-case/artifact routing; no SQL."""

import sys
from types import SimpleNamespace

import pytest

from scripts import current0058_collection_v1 as collection
from scripts import current0058_full_hermetic_contract_v1 as full
from scripts import retained0017_reference_measurement_v1 as retained17
from scripts import run_retained0017_reference_probe_v1 as runner


def test_default_reference_mode_does_not_select_or_import_paper58_contract():
    args = SimpleNamespace(native_storage_diagnostic=False)
    assert runner._paper58_mode(args) is False
    assert runner._paper58_contract(args) is None


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_paper58_mode_rejects_non_boolean_flags(value):
    with pytest.raises(ValueError, match="retained0017_reference_probe_refused"):
        runner._paper58_mode(
            SimpleNamespace(
                native_storage_diagnostic=False, paper58_structural_reference=value
            )
        )


def test_purpose_flags_cannot_be_combined_even_through_private_verifier_arguments():
    with pytest.raises(ValueError, match="retained0017_reference_probe_refused"):
        runner._paper58_mode(
            SimpleNamespace(
                native_storage_diagnostic=True, paper58_structural_reference=True
            )
        )


def test_cli_rejects_combined_purposes_before_source_or_resource_reads(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe",
            "--archive",
            "not-opened.tar",
            "--archive-sha256",
            "a" * 64,
            "--commit",
            "b" * 40,
            "--tree",
            "c" * 40,
            "--output",
            "not-created",
            "--native-storage-diagnostic",
            "--paper58-structural-reference",
        ],
    )
    with pytest.raises(SystemExit) as refused:
        runner.main()
    assert refused.value.code == 2


def test_both_new_cases_run_once_in_pg0_without_moving_the_old_pg3_pair():
    for module, name in full.PAPER58_CASES:
        node = module.replace(".", "/") + ".py::" + name
        assert collection.selected_for("full", None, node) is True
        assert [
            i for i in range(5) if collection.selected_for("postgres", i, node)
        ] == [0]
        assert not any(collection.selected_for("shard", i, node) for i in range(8))
        assert (
            sum((module, name) in full.required_cases("postgres", i) for i in range(5))
            == 1
        )
    for module, name in retained17.CASES:
        node = module.replace(".", "/") + ".py::" + name
        assert [
            i for i in range(5) if collection.selected_for("postgres", i, node)
        ] == [3]


def test_formal_paper_artifacts_and_environment_belong_only_to_pg0():
    formal = set(full.PAPER58_REPORTS)
    assert len(formal) == 6
    for index in range(5):
        accepted = full.component_artifacts("postgres", index)
        assert formal <= accepted if index == 0 else formal.isdisjoint(accepted)
        present = (
            "CTCC_DEVVAL_PAPER58_REFERENCE_DIR=/validation-results"
            in full.measurement_environment("postgres", index)
        )
        assert present is (index == 0)


def test_exporter_and_orchestrator_share_one_explicit_new_purpose_abi():
    from scripts import paper58_catalog_reference_v1 as reference

    assert reference.CASES == full.PAPER58_CASES
    assert reference.SCHEMA == reference.IDENTITY_SCHEMA
    assert reference.CONTRACT == "ctcc.source58.paper58_structural_reference.v1"
    assert reference.SUITE == "paper58-structural-reference"
    assert reference.REPORT == "test-results/paper58-structural-reference.xml"
    assert reference.ENV == "CTCC_DEVVAL_PAPER58_REFERENCE_DIR"
    assert (
        reference.SCOPE
        == "two_fresh_canonical55_to58_full_catalog_data_no_paper_admission"
    )
