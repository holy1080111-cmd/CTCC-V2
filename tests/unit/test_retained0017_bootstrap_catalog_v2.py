"""Actual bootstrap DATA; public V1 purpose/literal stay unchanged."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import retained0017_bootstrap_catalog_v2 as subject
from scripts import retained0017_catalog_baseline_v1 as original

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/retained0017_bootstrap_source_v2"


def _raw(label="a"):
    return json.loads((FIXTURES / ("reference_" + label + ".raw.json")).read_bytes())


@pytest.mark.parametrize("label", ["a", "b"])
def test_actual_two_independent_cluster_bootstrap_catalogs_match_new_purpose(label):
    value = subject.require_bootstrap_catalog17_v2(_raw(label))
    assert value["catalog_sha256"] == subject.BOOTSTRAP_CATALOG_SHA256
    assert value["reference_purpose"] == "PG17_INITDB_BOOTSTRAP_OWNER_OID10"
    assert value["physical_owner_oid"] == 10 and value["shared_dependency_rows"] == 0
    assert value["schema_upgrade_authorized"] is False
    assert value["runtime_startup_admission"] is value["execution_authority"] is False


def test_actual_reference_origin_keeps_raw_edges_and_distinct_cluster_identities():
    origin = json.loads((FIXTURES / "origin.json").read_bytes())
    assert origin["source_commit"] == "b50ce21c7338a48e7c744cb000d095a60cb030e4"
    assert origin["source_tree"] == "8a81439a81371e33415fccc6b40cb69697e6a7c2"
    assert len(origin["references"]) == 2
    physical = []
    for reference in origin["references"]:
        for name, expected in reference["files"].items():
            raw = (FIXTURES / name).read_bytes()
            assert len(raw) == expected["bytes"]
            assert hashlib.sha256(raw).hexdigest() == expected["sha256"]
        physical.append(reference["physical"])
    assert (
        physical[0]["system_identifier_sha256"]
        != physical[1]["system_identifier_sha256"]
    )
    assert physical[0]["database"] != physical[1]["database"]
    assert _raw("a")["shared_dependencies"] == _raw("b")["shared_dependencies"] == []


def test_public_fixture_inventory_contains_only_catalog_evidence_and_primary_sources():
    expected = {"origin.json", "pair-verification.json", "primary-pinned-owner.json"}
    expected.update(
        "reference_" + label + "." + suffix + ".json"
        for label in ("a", "b")
        for suffix in ("raw", "normalized", "physical", "receipt")
    )
    expected.update(
        "primary/" + name for name in (*subject._PRIMARY_NAMES, "COPYRIGHT.postgresql")
    )
    assert {
        path.relative_to(FIXTURES).as_posix()
        for path in FIXTURES.rglob("*")
        if path.is_file()
    } == expected
    private_marker = b"-----BEGIN " + b"PRIVATE KEY-----"
    assert all(
        private_marker not in path.read_bytes()
        for path in FIXTURES.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("index", range(2, 9))
def test_each_actual_bootstrap_role_flag_is_required_without_structural_relabel(index):
    value = _raw()
    value["actor_physical"]["owner"][index] = False
    # Physical role flags intentionally remain outside structural normalization.
    assert (
        original.sha(original.canonical(original.normalize_catalog17_v1(value)))
        == subject.BOOTSTRAP_CATALOG_SHA256
    )
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)


@pytest.mark.parametrize("owner", [True, 10.0, 11, 16384])
def test_wrong_or_nontyped_bootstrap_owner_is_not_selected(owner):
    value = _raw()
    value["actor_physical"]["owner"][0] = owner
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)


@pytest.mark.parametrize("count", [True, -1, 1])
def test_membership_count_is_exact_zero_not_boolean_or_caller_promise(count):
    value = _raw()
    value["actor_physical"]["membership_count"] = count
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)


def test_membership_digest_cannot_be_a_nonempty_population_with_zero_claimed_count():
    value = _raw()
    value["actor_physical"]["membership_sha256"] = "0" * 64
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)


def test_public_readonly_v1_and_new_bootstrap_purposes_do_not_cross_admit():
    old_path = (
        ROOT
        / "tests/fixtures/retained0017_official_reference_v1/retained0017-reference-a-raw.json"
    )
    old = json.loads(old_path.read_bytes())
    assert (
        original.require_sealed_catalog17_v1(old)["catalog_sha256"]
        == "42c73fa8af380f446fabfdf7bf0b4479dc4f26a1a44bbf6e195e9b612e70375e"
    )
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(old)
    with pytest.raises(original.Retained0017CatalogRefused):
        original.require_sealed_catalog17_v1(_raw())
    changed_mode = json.loads(json.dumps(old))
    changed_mode["profile"]["read_only"] = "off"
    with pytest.raises(original.Retained0017CatalogRefused):
        original.normalize_catalog17_v1(changed_mode)
    # The owner integration adds only its reviewed PRIVATE explicit-mode
    # parameter. Pin the current reviewed file separately; reconstructing the
    # historical bytes below is provenance only, never a current source or
    # admission claim. The public reader's actual mode remains unchanged.
    source = Path(original.__file__).read_bytes()
    assert (
        hashlib.sha256(source).hexdigest()
        == "58b17530c407984da10a9672aedcf7b3b61ab2b00f7310d0639da2a1fe9a84c2"
    )
    private_hunk = b"""def _normalize(document, *, read_only="on"):
    # Public DATA replay remains READ ONLY. The owner-maintenance procedure
    # supplies its actually observed write-transaction mode explicitly; it
    # must never change/relabel the captured profile to reuse the reader.
    _need(type(read_only) is str and read_only in ("on", "off"))
"""
    predicate = b'and profile["read_only"] == read_only'
    assert source.count(private_hunk) == source.count(predicate) == 1
    prior = source.replace(private_hunk, b"def _normalize(document):\n").replace(
        predicate, b'and profile["read_only"] == "on"'
    )
    assert (
        hashlib.sha256(prior).hexdigest()
        == "d0ea6a246f204429e212a389c80bc96c84e3429d55d0aa85eb0749032fe69478"
    )


def test_caller_dropping_owner_edges_and_claiming_flags_cannot_promote_nonbootstrap():
    path = (
        ROOT
        / "tests/fixtures/retained0017_official_reference_v1/retained0017-reference-a-raw.json"
    )
    changed = json.loads(path.read_bytes())
    changed[
        "shared_dependencies"
    ] = []  # Explicit adversarial DATA, never a normalizer operation.
    changed["actor_physical"]["owner"][2:] = [True] * 7
    assert changed["actor_physical"]["owner"][0] != 10
    assert (
        original.sha(original.canonical(original.normalize_catalog17_v1(changed)))
        == subject.BOOTSTRAP_CATALOG_SHA256
    )
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(changed)


@pytest.mark.parametrize(
    "mutation", ["column", "default", "dependency", "edge", "version"]
)
def test_full_actual_catalog_bit_changes_remain_refused(mutation):
    value = _raw()
    if mutation == "column":
        row = next(
            row
            for kind, row, _extra in value["entries"]
            if kind == "attribute" and row["attname"] == "version_num"
        )
        row["atttypmod"] += 1
    elif mutation == "default":
        row = next(
            row
            for kind, row, _extra in value["entries"]
            if kind == "default" and ":constisnull false" in row["adbin"]
        )
        row["adbin"] = row["adbin"].replace(
            ":constisnull false", ":constisnull true", 1
        )
    elif mutation == "dependency":
        value["dependencies"].pop()
    elif mutation == "edge":
        value["shared_dependencies"] = [[0, "pg_database", 1, 0, "pg_authid", 10, "o"]]
    else:
        value["profile"]["server_version"] = "170012"
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)


def test_caller_receipt_or_admission_flag_cannot_replace_actual_raw_catalog():
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(
            {"catalog_sha256": subject.BOOTSTRAP_CATALOG_SHA256, "pass": True}
        )


def test_foreign_value_callback_is_refused_without_invocation():
    called = []

    class Foreign(str):
        def __eq__(self, _other):
            called.append(True)
            return True

    value = _raw()
    value["profile"]["server_version"] = Foreign("170011")
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)
    assert called == []


def test_nonfinite_catalog_statistics_are_refused_without_oid_coercion():
    value = _raw()
    row = next(row for kind, row, _extra in value["entries"] if kind == "relation")
    row["reltuples"] = float("nan")
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(value)


def test_changed_primary_source_bytes_are_refused_before_normalization(monkeypatch):
    actual = subject._read_source

    def changed(path, expected, maximum):
        if path.name == "catalog.c":
            raise OSError("synthetic source mutation")
        return actual(path, expected, maximum)

    monkeypatch.setattr(subject, "_read_source", changed)
    with pytest.raises(subject.BootstrapCatalog17Refused):
        subject.require_bootstrap_catalog17_v2(_raw())
