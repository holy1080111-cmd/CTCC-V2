"""Read the three exact current0055 catalog receipts from one pytest run."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

NATIVE_DEFINITIONS_SHA256 = (
    "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
)
_CLASSNAME = "tests.integration.test_native_filled_flat_current55_pg"
_DRIFT_CASE = "test_actual_current55_full_model_has_no_alembic_drift"
_OWNER_CASE = (
    "test_actual_current55_owner_catalog_survives_empty_downgrade_and_reupgrade"
)
_WRITER_CASE = (
    "test_actual_current55_writer_login_matches_owner_and_rejects_missing_privilege"
)
_PROPERTIES = {
    _DRIFT_CASE: "ctcc_pg55_full_metadata_drift_v1",
    _OWNER_CASE: "ctcc_pg55_owner_observation_v1",
    _WRITER_CASE: "ctcc_pg55_writer_observation_v1",
}
_OWNER_SCHEMA = "ctcc.current55.actual_pg17_owner_reupgrade_observation.v1"
_WRITER_SCHEMA = "ctcc.current55.actual_pg17_restricted_writer_observation.v1"
_FALSE_FLAGS = {"native_baseline_admission", "account_complete", "execution_authority"}
_COUNTS = {"tests": "3", "failures": "0", "errors": "0", "skipped": "0"}
_MAX_REPORT_BYTES = 64 * 1024
_XID = re.compile(r"[1-9][0-9]{0,18}")


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("current0055_catalog_report_invalid")


def _read_report(path: Path) -> bytes:
    _require(not path.is_symlink() and path.is_file())
    with path.open("rb") as source:
        raw = source.read(_MAX_REPORT_BYTES + 1)
    _require(0 < len(raw) <= _MAX_REPORT_BYTES)
    return raw


def _decode_receipt(value: str) -> object:
    def unique_object(pairs):
        result = dict(pairs)
        _require(len(result) == len(pairs))
        return result

    result = json.loads(value, object_pairs_hook=unique_object)
    _require(
        json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        == value
    )
    return result


def _case_receipts(raw: bytes) -> dict:
    document = raw.decode("utf-8")
    _require("<!" not in document and "\x00" not in document)
    root = ET.fromstring(document)
    _require(root.tag == "testsuites" and len(root) == 1)
    suite = root[0]
    _require(
        suite.tag == "testsuite"
        and len(suite) == len(_PROPERTIES)
        and all(suite.get(name) == count for name, count in _COUNTS.items())
    )
    receipts = {}
    for case in suite:
        name = case.get("name")
        _require(
            case.tag == "testcase"
            and case.get("classname") == _CLASSNAME
            and name in _PROPERTIES
            and name not in receipts
            and len(case) == 1
            and case[0].tag == "properties"
            and len(case[0]) == 1
        )
        item = case[0][0]
        _require(
            item.tag == "property"
            and len(item) == 0
            and set(item.attrib) == {"name", "value"}
            and item.get("name") == _PROPERTIES[name]
        )
        receipts[name] = _decode_receipt(item.attrib["value"])
    return receipts


def _xid(value: object) -> str:
    _require(
        type(value) is str
        and _XID.fullmatch(value) is not None
        and int(value) <= 2**63 - 1
    )
    return value


def _owner_readback(observation: object) -> tuple[str, int]:
    _require(
        type(observation) is dict
        and set(observation) == {"definitions_sha256", "version", "xid"}
        and observation["definitions_sha256"] == NATIVE_DEFINITIONS_SHA256
        and type(observation["version"]) is int
        and 170000 <= observation["version"] < 180000
    )
    return _xid(observation["xid"]), observation["version"]


def _receipt_envelope(receipt: object, schema: str, fields: set[str]) -> None:
    _require(
        type(receipt) is dict
        and set(receipt) == _FALSE_FLAGS | fields | {"schema"}
        and receipt["schema"] == schema
        and all(receipt[name] is False for name in _FALSE_FLAGS)
    )


def _verify_owner(receipt: dict) -> int:
    _receipt_envelope(receipt, _OWNER_SCHEMA, {"first", "second"})
    first_xid, first_version = _owner_readback(receipt["first"])
    second_xid, second_version = _owner_readback(receipt["second"])
    _require(first_xid != second_xid and first_version == second_version)
    return first_version


def _verify_writer(receipt: dict, expected_version: int) -> None:
    missing_grants = {
        "missing_report_projection_insert_rejected",
        "missing_result_insert_rejected",
    }
    _receipt_envelope(
        receipt, _WRITER_SCHEMA, {"owner", "writer_reads"} | missing_grants
    )
    owner_xid, owner_version = _owner_readback(receipt["owner"])
    reads = receipt["writer_reads"]
    _require(
        owner_version == expected_version
        and all(receipt[name] is True for name in missing_grants)
        and type(reads) is list
        and len(reads) == 2
    )
    xids = {owner_xid}
    for read, readonly in zip(reads, (False, True), strict=True):
        _require(
            type(read) is dict
            and set(read) == {"definitions_sha256", "readonly", "xid"}
            and read["definitions_sha256"] == NATIVE_DEFINITIONS_SHA256
            and read["readonly"] is readonly
        )
        xids.add(_xid(read["xid"]))
    _require(len(xids) == 3)


def verify_current55_catalog_report(path: Path) -> dict:
    """Verify the shape and contents of the three catalog test receipts."""
    try:
        raw = _read_report(path)
        receipts = _case_receipts(raw)
        drift = receipts[_DRIFT_CASE]
        owner = receipts[_OWNER_CASE]
        writer = receipts[_WRITER_CASE]
        _require(type(drift) is list and not drift)
        _verify_writer(writer, _verify_owner(owner))
    except (ET.ParseError, OSError, RecursionError, ValueError):
        raise ValueError("current0055_catalog_report_invalid") from None
    return {
        "schema": "ctcc.current55.catalog_junit_readback.v1",
        "status": "PASS",
        "junit_sha256": hashlib.sha256(raw).hexdigest(),
        "passed": 3,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "full_metadata_drift": drift,
        "owner": owner,
        "writer": writer,
        "native_definitions_sha256": NATIVE_DEFINITIONS_SHA256,
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
