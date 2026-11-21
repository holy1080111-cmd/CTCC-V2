"""Actual archived reference DATA validates only the reviewed source literal."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject

FIXTURE = Path(__file__).parents[1] / "fixtures/retained0017_official_reference_v1"
CATALOG = "42c73fa8af380f446fabfdf7bf0b4479dc4f26a1a44bbf6e195e9b612e70375e"
PREFIX = "7a998b64b61f5bb30d65c405a995f3d00ffc12d15344b7cbda309f1c58b17cd2"


def _raw(label):
    origin = json.loads((FIXTURE / "origin.json").read_bytes())
    name = f"retained0017-reference-{label}-raw.json"
    raw = (FIXTURE / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == origin["files"][name]["sha256"]
    return json.loads(raw)


def test_archived_origin_retains_two_distinct_actual_instances_and_six_bytes():
    origin = json.loads((FIXTURE / "origin.json").read_bytes())
    assert origin["catalog_sha256"] == CATALOG
    assert origin["source17_prefix_sha256"] == PREFIX
    assert origin["commit"] == "4c117cb63ef9606e19b91c0607e1b462789119e5"
    assert origin["official_ci_run"] == 44867198530
    assert origin["explicit_database_collation"] == "en_US.utf8"
    assert origin["explicit_database_ctype"] == "en_US.utf8"
    assert origin["schema_upgrade_authority"] is False
    assert origin["runtime_ready"] is False
    assert len(origin["files"]) == 6
    for name, facts in origin["files"].items():
        raw = (FIXTURE / name).read_bytes()
        assert len(raw) == facts["bytes"]
        assert hashlib.sha256(raw).hexdigest() == facts["sha256"]
    physical = []
    for label in ("a", "b"):
        receipt = json.loads(
            (FIXTURE / f"retained0017-reference-{label}-receipt.json").read_bytes()
        )
        assert receipt["reference_seal_installed"] is False
        assert receipt["schema_upgrade_authorized"] is False
        assert receipt["execution_authority"] is False
        assert receipt["physical"]["actual_tls"] is True
        assert receipt["physical"]["owner_memberships"] == 0
        assert receipt["physical"]["owner_flags"] == {
            "rolsuper": False,
            "rolinherit": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolcanlogin": True,
            "rolreplication": False,
            "rolbypassrls": False,
        }
        physical.append(receipt["physical"])
    for key in (
        "database",
        "database_oid",
        "owner",
        "owner_oid",
        "marker_relation_oid",
    ):
        assert physical[0][key] != physical[1][key]


@pytest.mark.parametrize("label", ("a", "b"))
def test_actual_catalog_matches_literal_without_granting_any_upgrade(label):
    document = _raw(label)
    before = subject.canonical(document)
    result = subject.require_sealed_catalog17_v1(document)
    assert result["catalog_sha256"] == CATALOG
    assert result["schema_upgrade_authorized"] is False
    assert result["execution_authority"] is False
    assert subject.canonical(document) == before


def test_other_observed_locale_is_not_silently_mapped_to_reviewed_locale():
    document = _raw("a")
    database = [row for kind, row, _ in document["entries"] if kind == "database"]
    assert len(database) == 1
    database[0]["datcollate"] = database[0]["datctype"] = "C"
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1(document)


def test_claimed_pass_receipt_cannot_substitute_actual_catalog():
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1(
            {
                "passed": True,
                "catalog_sha256": CATALOG,
                "source17_prefix_sha256": PREFIX,
            }
        )


def test_actual_catalog_still_requires_observed_tls_profile():
    document = _raw("a")
    document["profile"]["tls"] = False
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1(document)
