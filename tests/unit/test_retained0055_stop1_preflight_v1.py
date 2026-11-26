from types import SimpleNamespace

import pytest

from scripts import retained0055_stop1_preflight_v1 as subject
from scripts import retained_pg_bootstrap_custody_v1 as custody


@pytest.mark.parametrize("fixture", (False, True))
def test_revision1_preflight_origin_has_no_writer_claim(monkeypatch, fixture):
    owned, engine, pins = object(), object(), object()
    record = SimpleNamespace(
        phase=custody._Phase.READY,
        origin=custody._Origin.STOPPED55_STOP1_PREFLIGHT,
        engine=engine,
        pins=pins,
        preserved_apply_confirmed=False,
    )
    monkeypatch.setattr(custody, "_guard", lambda: None)
    monkeypatch.setattr(custody, "_record", lambda _lease: record)
    with pytest.raises(custody.RetainedBootstrapRefused):
        custody._claim_owned_apply_v1(owned, fixture=fixture)
    with pytest.raises(custody.RetainedBootstrapRefused):
        custody._require_stopped55_negative_v1(owned, engine, pins)
    with pytest.raises(custody.RetainedBootstrapRefused):
        custody._advance_owned_apply_v1(owned, "commit_attempted")
    assert record.phase is custody._Phase.READY


@pytest.mark.parametrize(
    "changed", ("physical_inventory_sha256", "after_sha256", "native_acceptance_sha256")
)
def test_revision1_preflight_refuses_any_changed_predecessor(changed):
    prior = {**subject._EXPECTED_PRIOR, changed: "7" * 64}
    with pytest.raises(custody.RetainedBootstrapRefused):
        subject.require_request_lineage(
            {
                "schema": subject.REQUEST_SCHEMA,
                "predecessor": prior,
                "route": {"cluster_system_identifier_sha256": subject._SYSTEM_SHA256},
            }
        )


@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("commit_attempts", False),
        ("control_revision", 0),
        ("schema_upgrade_authorized", True),
        ("startup_admission", True),
        ("execution_authority", True),
        ("exchange_flat", True),
        ("account_complete", True),
    ),
)
def test_revision1_preflight_result_refuses_changed_readonly_claims(field, wrong):
    reference = subject.require_reference_data()
    redacted = {
        **subject._data(reference.reference_pair_sha256),
        "stopped_inventory_sha256": subject.INVENTORY_SHA256,
        "native_predecessor_acceptance_sha256": subject._ACCEPTANCE_SHA256,
        "stopped_source_unchanged_verified": True,
        "distinct_working_copy_verified": True,
        "owned_server_fast_shutdown_verified": True,
        "commit_attempts": 0,
    }
    subject.require_result(redacted)
    with pytest.raises(custody.RetainedBootstrapRefused):
        subject.require_result({**redacted, field: wrong})
