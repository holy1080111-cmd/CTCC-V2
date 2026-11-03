"""The source55/database54 receipt cannot impersonate an exact source54 seal."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical
from scripts import historical0054_on_current0055_observation as subject


def _receipt(monkeypatch):
    scope = SimpleNamespace(
        actual_source_head="0055",
        database_target="0054",
        actual_migration_sha256="a" * 64,
        historical_prefix_sha256="b" * 64,
        manifest_sha256="c" * 64,
    )
    monkeypatch.setattr(subject, "_scope", lambda root: scope)
    observed = {
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    monkeypatch.setattr(
        subject.policy, "observe_actual_owner_catalog", lambda catalog: observed
    )
    document = {
        "schema": subject.SCHEMA,
        "source_head": "0055",
        "database_head": "0054",
        "source_scope": {
            "schema": "ctcc.actual_source_historical_database_scope.v1",
            "source_head": "0055",
            "database_head": "0054",
            "migration_sha256": scope.actual_migration_sha256,
            "historical_prefix_sha256": scope.historical_prefix_sha256,
            "manifest_sha256": scope.manifest_sha256,
            "account_complete": False,
            "execution_authority": False,
        },
        "owner_catalog": {"real_catalog": True},
        "current0054_observation": observed,
        "native_baseline_admission": False,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    return document, canonical(document) + b"\n"


def test_distinct_source55_database54_observation_shape(monkeypatch):
    document, raw = _receipt(monkeypatch)
    assert (
        subject.verify_historical0054_owner_on_current0055(document, raw)
        == document["current0054_observation"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema", "ctcc.collector_current0054.native_owner_observation.v1"),
        ("source_head", "0054"),
        ("database_head", "0055"),
        ("execution_authority", True),
        ("native_baseline_admission", True),
    ),
)
def test_relabel_or_authority_mutation_is_rejected(monkeypatch, field, value):
    document, _ = _receipt(monkeypatch)
    forged = deepcopy(document)
    forged[field] = value
    with pytest.raises(ValueError, match="source0055_historical0054_receipt_invalid"):
        subject.verify_historical0054_owner_on_current0055(
            forged, canonical(forged) + b"\n"
        )
