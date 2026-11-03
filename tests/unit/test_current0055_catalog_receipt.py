import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.current0055_catalog_receipt import (
    NATIVE_DEFINITIONS_SHA256,
    verify_current55_catalog_report,
)

_CASES = (
    (
        "test_actual_current55_full_model_has_no_alembic_drift",
        "ctcc_pg55_full_metadata_drift_v1",
    ),
    (
        "test_actual_current55_owner_catalog_survives_empty_downgrade_and_reupgrade",
        "ctcc_pg55_owner_observation_v1",
    ),
    (
        "test_actual_current55_writer_login_matches_owner_and_rejects_missing_privilege",
        "ctcc_pg55_writer_observation_v1",
    ),
)


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _report() -> ET.Element:
    flags = {
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    definitions = {"definitions_sha256": NATIVE_DEFINITIONS_SHA256}
    readback = {**definitions, "version": 170011}
    owner = {
        **flags,
        "schema": "ctcc.current55.actual_pg17_owner_reupgrade_observation.v1",
        "first": {**readback, "xid": "970"},
        "second": {**readback, "xid": "973"},
    }
    writer = {
        **flags,
        "schema": "ctcc.current55.actual_pg17_restricted_writer_observation.v1",
        "owner": {**readback, "xid": "978"},
        "writer_reads": [
            {**definitions, "xid": "982", "readonly": False},
            {**definitions, "xid": "983", "readonly": True},
        ],
        "missing_report_projection_insert_rejected": True,
        "missing_result_insert_rejected": True,
    }
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests="3", failures="0", errors="0", skipped="0"
    )
    for (name, property_name), payload in zip(_CASES, ([], owner, writer), strict=True):
        case = ET.SubElement(
            suite,
            "testcase",
            classname="tests.integration.test_native_filled_flat_current55_pg",
            name=name,
        )
        props = ET.SubElement(case, "properties")
        ET.SubElement(props, "property", name=property_name, value=_encode(payload))
    return root


def _write_report(root: ET.Element, tmp_path: Path) -> Path:
    path = tmp_path / "catalog.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def test_three_exact_current55_receipts_are_read_back(tmp_path):
    result = verify_current55_catalog_report(_write_report(_report(), tmp_path))
    assert result["passed"] == 3
    assert result["full_metadata_drift"] == []
    assert result["owner"]["second"]["xid"] == "973"
    assert result["writer"]["writer_reads"][1]["readonly"] is True
    assert result["execution_authority"] is False


@pytest.mark.parametrize(
    "change",
    (
        "wrong_class",
        "duplicate_case",
        "duplicate_property",
        "missing_property",
        "skipped_case",
        "wrong_totals",
        "nonempty_drift",
        "duplicate_json_key",
        "wrong_native_sha",
        "other_pg_major",
        "owner_xid_reused",
        "writer_xid_reused",
        "writer_read_order",
        "authority_flag",
        "acl_flag",
        "unexpected_field",
    ),
)
def test_receipt_forgeries_and_partial_runs_are_rejected(tmp_path, change):
    root = _report()
    suite = root[0]
    drift, owner_case, writer_case = suite
    owner_prop = owner_case[0][0]
    writer_prop = writer_case[0][0]
    owner = json.loads(owner_prop.attrib["value"])
    writer = json.loads(writer_prop.attrib["value"])
    if change == "wrong_class":
        writer_case.set("classname", "tests.unit.test_native_filled_flat_current55_pg")
    elif change == "duplicate_case":
        writer_case.set("name", _CASES[1][0])
    elif change == "duplicate_property":
        ET.SubElement(writer_case[0], "property", writer_prop.attrib)
    elif change == "missing_property":
        writer_case.remove(writer_case[0])
    elif change == "skipped_case":
        ET.SubElement(writer_case, "skipped")
    elif change == "wrong_totals":
        suite.set("tests", "4")
    elif change == "nonempty_drift":
        drift[0][0].set("value", '[["remove_table","ledger",null,[]]]')
    elif change == "wrong_native_sha":
        owner["second"]["definitions_sha256"] = "a" * 64
    elif change == "other_pg_major":
        owner["second"]["version"] = 180001
    elif change == "owner_xid_reused":
        owner["second"]["xid"] = owner["first"]["xid"]
    elif change == "writer_xid_reused":
        writer["writer_reads"][0]["xid"] = writer["owner"]["xid"]
    elif change == "writer_read_order":
        writer["writer_reads"].reverse()
    elif change == "authority_flag":
        writer["execution_authority"] = True
    elif change == "acl_flag":
        writer["missing_result_insert_rejected"] = 1
    elif change == "unexpected_field":
        writer["writer_reads"][0]["role_override"] = True
    owner_prop.set("value", _encode(owner))
    writer_prop.set("value", _encode(writer))
    if change == "duplicate_json_key":
        writer_prop.set(
            "value", '{"execution_authority":true,' + writer_prop.attrib["value"][1:]
        )
    with pytest.raises(ValueError, match="^current0055_catalog_report_invalid$"):
        verify_current55_catalog_report(_write_report(root, tmp_path))
