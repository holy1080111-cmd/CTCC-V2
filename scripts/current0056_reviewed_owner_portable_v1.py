"""Verify the portable owner baseline measured in two hosted DB0056 clusters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.domain.source_primitives import canonical, sha
from scripts.current0056_fitted_catalog_measurement_v1 import INHERITED_CASE
from scripts.current0056_migration_pin import (
    MIGRATION_INVENTORY_COUNT,
    MIGRATION_INVENTORY_SHA256,
    MIGRATION_PATH,
    MIGRATION_SHA256,
)
from scripts.current0056_native_owner_pregrant_v1 import (
    NativeOwnerPregrant0056Diagnostic,
)
from scripts.evidence_json import read_evidence_json

_SOURCE = Path(__file__).resolve().parents[1]
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_RUN = "https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/43465801180"
_EVIDENCE = "scripts/db0056_owner_reference_43465801180"
_PRODUCER_COMMIT = "7461b4de830f6f6fa6d430093714b47303a1b709"
_PRODUCER_ARCHIVE = "7804492a7ff50efaacd239d5bfba64759437a46f3901b1a82225ec65af385914"
_MEASUREMENTS = (
    (
        14059009371,
        "24034fea7280288493d3e201899941a53bfda86c77459c83e3f1b7a1d93e3370",
    ),
    (
        14058774762,
        "d93520d37eb7c514d241b55baa0b27448ed8667452d7f4a062733a7d688f2b7a",
    ),
)
_REVIEWED_FILES = {
    "app/domain/collector_current_0056_inherited_catalog_v1.py": "b7fbeff9e3b43c04778d7d6a26cd6df9fe66a1aec9a7d33f45c607434ac3a639",
    "app/domain/collector_current_0055_catalog_v1.py": "fa7e80105102a9eca72176c7c9243c604d275020fae37dd4fbd12f7a81d28652",
    "app/core/native_role_preflight_0056_full_diagnostic_v1.py": "0d8984cddbc46a724f5d93dba88af7392276eb0a42e560334914686ce0717aa9",
    "app/core/native_role_preflight_v1.py": "1ba3931e8cf2e81c712592834de37da900a86bc8cf2499b22cf9eca230fccb9d",
    "scripts/current0056_native_owner_pregrant_v1.py": "cf36f15a2e54df40ea1e232651a10fdbb0432304ff4cc3b8c1bf11ddcf805833",
    "scripts/current0056_fitted_delta_catalog_v1.py": "2b91b135ba24345628649d491d3c829664f2c7320c59c197f30dac5c7af2dceb",
    "scripts/current0056_fitted_catalog_pair_v1.py": "74abcc03b4c19f8d0d65c57e723ae6ea673ab1c619bcdd30d66c299c0ff52ac8",
    "scripts/current0056_fitted_catalog_measurement_v1.py": "a03e192daa4dbeeb14d57ccfd8810993af5c34c19fe5165dbcbe5e61ead89606",
    "scripts/current0055_runtime_grants.py": "19f26c51146b564b830641a2e37227225e8b5484e75ce2a79620666656679ed8",
    "scripts/current0055_native_account_source_acl_v1.py": "6a8cbe69bcddd47b3d1ea005e1914403c9f18add2f5df077ef80c6284fd672ab",
    "scripts/current0055_native_lifecycle_acl_v1.py": "7505dbc6d5fefa0148a05f30fa16d7a666c7f8c3c3473a1645403724648be795",
    "pyproject.toml": "c47e6ba2cbb0f938ea961a7ff075e550e3c66c30b883e2d4fd1162ca94f83785",
    "requirements/validation-linux-py312.lock": "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4",
    "requirements/validation-linux-py312.json": "c2641f377984e206567f22a869a5b2ff4de1b553c0a64c6e4add7ebf20f27b95",
}
_INHERITED = (
    "owner_pregrant_inherited_sha256",
    "owner_pregrant_readwrite_inherited_sha256",
    "postgrant_readonly_inherited_sha256",
    "postgrant_readwrite_inherited_sha256",
)
_NORMALIZED = "owner_pregrant_fitted_normalized_sha256"
_RAW = "owner_pregrant_fitted_raw_sha256"


class ReviewedOwnerPortable0056Error(ValueError):
    """Fixed refusal without local route, actor or catalog values."""


@dataclass(frozen=True, slots=True)
class ReviewedOwnerPortable0056V1:
    inherited_native_definitions_sha256: str
    fitted_normalized_owner_catalog_sha256: str
    reference_sha256: str
    startup_admission: Literal[False] = field(default=False, init=False)
    grant_authority: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(condition: bool) -> None:
    if condition is not True:
        raise ReviewedOwnerPortable0056Error(
            "db0056_reviewed_portable_reference_refused"
        ) from None


def _read(root: Path, name: str, budget: int = 1_048_576) -> bytes:
    return _read_bounded_regular_file_v1(root / name, budget)


def _reviewed_source(root: Path) -> None:
    _need(root.resolve(strict=True) == root and root == _SOURCE)
    _need(
        all(
            sha(_read(root, name)) == digest for name, digest in _REVIEWED_FILES.items()
        )
    )
    names = sorted((root / "migrations").rglob("*.py"))
    _need(len(names) == MIGRATION_INVENTORY_COUNT)
    migration = {
        path.relative_to(root).as_posix(): sha(
            _read_bounded_regular_file_v1(path, 1_048_576)
        )
        for path in names
    }
    _need(
        migration.get(MIGRATION_PATH) == MIGRATION_SHA256
        and sha(canonical(migration)) == MIGRATION_INVENTORY_SHA256
    )


def _measurement(root: Path, index: int, expected: str) -> dict:
    raw = _read(root, f"{_EVIDENCE}/cluster-{index}.inherited.json", 8192)
    _need(sha(raw) == expected)
    doc = read_evidence_json(raw)
    _need(
        type(doc) is dict
        and canonical(doc) == raw
        and doc["schema"]
        == "ctcc.current0056.inherited_native_catalog_measurement.inert.v1"
        and doc["case"] == list(INHERITED_CASE)
        and doc["native_reference_sealed"] is False
        and doc["startup_admission"] is doc["execution_authority"] is False
        and doc["source"]["commit"] == _PRODUCER_COMMIT
        and doc["source"]["archive_sha256"] == _PRODUCER_ARCHIVE
        and doc["source"]["migration_head"] == "0056"
        and doc["source"]["migration_leaf_sha256"] == MIGRATION_SHA256
        and doc["source"]["migration_inventory_sha256"] == MIGRATION_INVENTORY_SHA256
        and set(doc["definitions"]) == {*_INHERITED, _NORMALIZED, _RAW}
        and all(_HEX.fullmatch(value) for value in doc["definitions"].values())
        and _HEX.fullmatch(doc["cluster_system_identifier_sha256"]) is not None
    )
    return doc


def verify_reviewed_owner_portable_0056_v1(
    source: Path,
    observed: NativeOwnerPregrant0056Diagnostic,
) -> ReviewedOwnerPortable0056V1:
    """Match current owner observations to source-pinned portable definitions."""
    reference = load_reviewed_owner_portable_0056_v1(source)
    _need(
        type(observed) is NativeOwnerPregrant0056Diagnostic
        and observed.grants_present is False
        and observed.native_reference_sealed is False
        and observed.startup_admission is observed.execution_authority is False
        and observed.admission == "DENY"
        and reference.inherited_native_definitions_sha256
        == observed.inherited_native_definitions_sha256
        == observed.inherited_readwrite_native_definitions_sha256
        and reference.fitted_normalized_owner_catalog_sha256
        == observed.fitted_normalized_owner_catalog_sha256
    )
    return reference


def load_reviewed_owner_portable_0056_v1(source: Path) -> ReviewedOwnerPortable0056V1:
    """Read the two pinned measurements without fabricating an owner observation."""
    try:
        _reviewed_source(source)
        first, second = (
            _measurement(source, index, digest)
            for index, (_, digest) in enumerate(_MEASUREMENTS, 1)
        )
        _need(
            first["source"] == second["source"]
            and first["run_id"] != second["run_id"]
            and first["cluster_system_identifier_sha256"]
            != second["cluster_system_identifier_sha256"]
            and first["definitions"][_RAW] != second["definitions"][_RAW]
        )
        definitions = first["definitions"]
        inherited = definitions[_INHERITED[0]]
        normalized = definitions[_NORMALIZED]
        _need(
            all(
                captured["definitions"][name] == inherited
                for captured in (first, second)
                for name in _INHERITED
            )
            and normalized == second["definitions"][_NORMALIZED]
        )
        reference = canonical(
            {
                "schema": "ctcc.native_owner_0056.reviewed_portable_reference.v1",
                "run": _RUN,
                "measurements": [
                    {"artifact_id": artifact, "receipt_sha256": digest}
                    for artifact, digest in _MEASUREMENTS
                ],
                "inherited_native_definitions_sha256": inherited,
                "fitted_normalized_owner_catalog_sha256": normalized,
            }
        )
        return ReviewedOwnerPortable0056V1(inherited, normalized, sha(reference))
    except ReviewedOwnerPortable0056Error:
        raise
    except Exception:  # noqa: BLE001 -- redact path and observed values.
        _need(False)
