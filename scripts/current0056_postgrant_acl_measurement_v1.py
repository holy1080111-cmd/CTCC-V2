"""Inert, role-neutral DB0056 postgrant ACL evidence from two actual observers.

This is a separate receipt from the historical fitted and inherited v1 files.
The hosted test supplies actual full and owner-free restricted observations;
the pure verifier can only check retained bytes and cannot attest their origin.
No receipt here is a native reference or startup/trading permit.
"""

from __future__ import annotations

import hashlib
import json
import re

from scripts import current0056_fitted_catalog_measurement_v1 as earlier
from scripts import current0056_fitted_catalog_pair_v1 as fitted_pair
from scripts.evidence_json import read_evidence_json

SCHEMA = "ctcc.current0056.postgrant_inherited_acl_measurement.redacted.inert.v1"
FILE = "current0056-postgrant-acl-redacted.json"
CASE = earlier.INHERITED_CASE
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROLE_SOURCE = "app/core/native_role_preflight_0056_full_diagnostic_v1.py"
_PRODUCER_SOURCE = "scripts/current0056_postgrant_acl_measurement_v1.py"
_FULL_SCHEMA = "ctcc.native_only.actual0056_role_observation.v1"
_RESTRICTED_SCHEMA = "ctcc.native_only.restricted0056_role_observation.v1"


class PostgrantAclMeasurementRefusal(ValueError):
    pass


def _need(condition: bool, code: str) -> None:
    if condition is not True:
        raise PostgrantAclMeasurementRefusal(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _source(identity, source_files, source_files_raw):
    source = earlier.source_identity(identity, source_files, source_files_raw)
    for path in (_ROLE_SOURCE, _PRODUCER_SOURCE):
        _need(
            type(source_files.get(path)) is dict
            and _digest(source_files[path].get("sha256")),
            "postgrant_acl_source_inventory",
        )
    return {
        **source,
        "role_observer_sha256": source_files[_ROLE_SOURCE]["sha256"],
        "acl_measurement_producer_sha256": source_files[_PRODUCER_SOURCE]["sha256"],
    }


def _observations(full, restricted):
    from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56

    _need(
        type(full) is roles56.NativeRoles0056ObservationV1
        and type(restricted) is roles56.RestrictedNativeRoles0056ObservationV1
        and full.schema == _FULL_SCHEMA
        and restricted.schema == _RESTRICTED_SCHEMA
        and full.database_head == restricted.database_head == "0056"
        and full.admission == restricted.admission == "DENY"
        and all(
            getattr(value, flag) is False
            for value in (full, restricted)
            for flag in (
                "native_reference_sealed",
                "native_baseline_admission",
                "startup_admission",
                "account_complete",
                "execution_authority",
            )
        )
        and all(
            _digest(value)
            for value in (
                full.inherited_native_definitions_sha256,
                restricted.inherited_native_definitions_sha256,
                full.granted_inherited_acl_shape_sha256,
                restricted.granted_inherited_acl_shape_sha256,
                full.frame_writer_observation_sha256,
                restricted.frame_writer_observation_sha256,
                full.raw_custody_catalog_sha256,
                restricted.raw_custody_catalog_sha256,
                full.fitted_owner_catalog_sha256,
            )
        )
        and full.inherited_native_definitions_sha256
        == restricted.inherited_native_definitions_sha256
        and full.granted_inherited_acl_shape_sha256
        == restricted.granted_inherited_acl_shape_sha256,
        "postgrant_acl_observations_changed",
    )


def build_measurement(
    *,
    identity,
    source_files,
    source_files_raw,
    cluster_system_identifier_sha256,
    full,
    restricted,
) -> bytes:
    """Build only a diagnostic over the actual objects held by the hosted test."""
    source = _source(identity, source_files, source_files_raw)
    _need(_digest(cluster_system_identifier_sha256), "postgrant_acl_cluster")
    _observations(full, restricted)
    receipt = {
        "schema": SCHEMA,
        "case": list(CASE),
        "scope": "one_disposable_linux_pg17_tls_cluster_postgrant_demo_acl",
        "source": source,
        "pg_image": fitted_pair.PG_IMAGE,
        "pg_server_build": fitted_pair.PG_BUILD,
        "run_id": identity["run_id"],
        "cluster_system_identifier_sha256": cluster_system_identifier_sha256,
        "full_role_observation_schema": _FULL_SCHEMA,
        "restricted_role_observation_schema": _RESTRICTED_SCHEMA,
        "inherited_native_definitions_sha256": full.inherited_native_definitions_sha256,
        "full_granted_acl_shape_sha256": full.granted_inherited_acl_shape_sha256,
        "restricted_granted_acl_shape_sha256": restricted.granted_inherited_acl_shape_sha256,
        "role_names_retained": False,
        "raw_acl_retained": False,
        "actual_measurement_independently_attested": False,
        "native_reference_sealed": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return _canonical(receipt)


def verify_measurement(raw: bytes, *, identity, source_files) -> dict:
    """Replay retained bytes against an independently admitted archive map."""
    _need(type(raw) is bytes and 0 < len(raw) <= 8_192, "postgrant_acl_size")
    try:
        value = read_evidence_json(raw)
        expected_source = _source(identity, source_files, _canonical(source_files))
        _need(
            type(value) is dict
            and _canonical(value) == raw
            and set(value)
            == {
                "schema",
                "case",
                "scope",
                "source",
                "pg_image",
                "pg_server_build",
                "run_id",
                "cluster_system_identifier_sha256",
                "full_role_observation_schema",
                "restricted_role_observation_schema",
                "inherited_native_definitions_sha256",
                "full_granted_acl_shape_sha256",
                "restricted_granted_acl_shape_sha256",
                "role_names_retained",
                "raw_acl_retained",
                "actual_measurement_independently_attested",
                "native_reference_sealed",
                "startup_admission",
                "execution_authority",
                "admission",
            }
            and value["schema"] == SCHEMA
            and value["case"] == list(CASE)
            and value["scope"]
            == "one_disposable_linux_pg17_tls_cluster_postgrant_demo_acl"
            and value["source"] == expected_source
            and value["pg_image"] == fitted_pair.PG_IMAGE
            and value["pg_server_build"] == fitted_pair.PG_BUILD
            and value["run_id"] == identity["run_id"]
            and value["full_role_observation_schema"] == _FULL_SCHEMA
            and value["restricted_role_observation_schema"] == _RESTRICTED_SCHEMA
            and all(
                _digest(value[key])
                for key in (
                    "cluster_system_identifier_sha256",
                    "inherited_native_definitions_sha256",
                    "full_granted_acl_shape_sha256",
                    "restricted_granted_acl_shape_sha256",
                )
            )
            and value["full_granted_acl_shape_sha256"]
            == value["restricted_granted_acl_shape_sha256"]
            and all(
                value[key] is False
                for key in (
                    "role_names_retained",
                    "raw_acl_retained",
                    "actual_measurement_independently_attested",
                    "native_reference_sealed",
                    "startup_admission",
                    "execution_authority",
                )
            )
            and value["admission"] == "DENY",
            "postgrant_acl_identity",
        )
        return value
    except (KeyError, TypeError, ValueError) as error:
        if type(error) is PostgrantAclMeasurementRefusal:
            raise
        raise PostgrantAclMeasurementRefusal("postgrant_acl_invalid") from None


def compare_retained_measurements(
    first_raw,
    second_raw,
    *,
    first_identity,
    first_source_files,
    first_expected_sha256,
    second_identity,
    second_source_files,
    second_expected_sha256,
) -> dict:
    """Require two pinned, separately initialized clusters; still DENY."""
    _need(
        _digest(first_expected_sha256)
        and _digest(second_expected_sha256)
        and type(first_raw) is bytes
        and type(second_raw) is bytes
        and _sha(first_raw) == first_expected_sha256
        and _sha(second_raw) == second_expected_sha256,
        "postgrant_acl_external_pin",
    )
    first = verify_measurement(
        first_raw, identity=first_identity, source_files=first_source_files
    )
    second = verify_measurement(
        second_raw, identity=second_identity, source_files=second_source_files
    )
    _need(
        first["source"] == second["source"]
        and first["run_id"] != second["run_id"]
        and first["cluster_system_identifier_sha256"]
        != second["cluster_system_identifier_sha256"]
        and first["inherited_native_definitions_sha256"]
        == second["inherited_native_definitions_sha256"]
        and first["full_granted_acl_shape_sha256"]
        == second["full_granted_acl_shape_sha256"],
        "postgrant_acl_pair_mismatch",
    )
    return {
        "schema": "ctcc.current0056.postgrant_inherited_acl_pair.redacted.inert.v1",
        "source": first["source"],
        "pg_image": fitted_pair.PG_IMAGE,
        "pg_server_build": fitted_pair.PG_BUILD,
        "inherited_native_definitions_sha256": first[
            "inherited_native_definitions_sha256"
        ],
        "granted_inherited_acl_shape_sha256": first["full_granted_acl_shape_sha256"],
        "measurement_receipt_sha256": sorted((_sha(first_raw), _sha(second_raw))),
        "actual_measurement_independently_attested": False,
        "native_reference_sealed": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def retain_hermetic_measurement(
    *, cluster_system_identifier_sha256, full, restricted
) -> None:
    identity, files, raw_files = earlier._read_hermetic_inputs()
    raw = build_measurement(
        identity=identity,
        source_files=files,
        source_files_raw=raw_files,
        cluster_system_identifier_sha256=cluster_system_identifier_sha256,
        full=full,
        restricted=restricted,
    )
    verify_measurement(raw, identity=identity, source_files=files)
    earlier._publish_redacted(FILE, raw)
