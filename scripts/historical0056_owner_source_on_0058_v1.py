"""Reviewed source58 may inspect the unchanged physical56 portable owner baseline.

These historical references never admit a physical57 catalog, add a grant,
seal a native reference or issue startup/trading authority.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from app.domain.source_primitives import canonical, sha
from scripts import current0056_reviewed_owner_portable_v1 as previous
from scripts import current0058_migration_pin as current
from scripts import current_contract_source_scopes_v1 as scopes

_SOURCE = Path(__file__).resolve().parents[1]
# Explicitly reviewed source58 observer files. Runtime owner catalogs
# must still match both original independently measured physical56 references.
_REVIEWED_SOURCE58_FILES = {
    "app/core/native_role_preflight_0056_full_diagnostic_v1.py": "ed230a4a839874bbcba846856ecfd7626990e844af162a82d523da6b74932116",
    "app/core/native_role_preflight_v1.py": "1ba3931e8cf2e81c712592834de37da900a86bc8cf2499b22cf9eca230fccb9d",
    "app/domain/collector_current_0055_catalog_v1.py": "fa7e80105102a9eca72176c7c9243c604d275020fae37dd4fbd12f7a81d28652",
    "app/domain/collector_current_0056_inherited_catalog_v1.py": "463a608cfd96bbf3d14f3a99eae0862d5f3d2f812d08349e2b6ca7513a41284c",
    "app/domain/g12_custody_catalog_0057_v1.py": "6e6526b52387e618e3135150ee1a9891036529a834006001c1bfd833fc11f786",
    "app/domain/native_database_profile_v1.py": "666db38b70051332e9a8cc6b3d3409d4f468c1ab17f115faab687dfeab5570b1",
    "app/domain/native_protective_catalog_0058_v1.py": "6ff53c1d9174b8f7e2709ab03c5bdbdca7012a3f0c699a610afe506fa2d859f9",
    "app/domain/native_protective_hold_schema_v58.py": "e2ce5b6ec653f0708d7dca2da7d0a5bb433d49bdacb1ca545a3593da8c4f480b",
    "app/domain/native_submission_binding_schema_v58.py": "00c124764df1860c10ce43a8763428e1fe62f751bc485a24741466e48fe2ecbc",
    "pyproject.toml": "c47e6ba2cbb0f938ea961a7ff075e550e3c66c30b883e2d4fd1162ca94f83785",
    "requirements/validation-linux-py312.json": "c2641f377984e206567f22a869a5b2ff4de1b553c0a64c6e4add7ebf20f27b95",
    "requirements/validation-linux-py312.lock": "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4",
    "scripts/current0055_native_account_source_acl_v1.py": "6a8cbe69bcddd47b3d1ea005e1914403c9f18add2f5df077ef80c6284fd672ab",
    "scripts/current0055_native_lifecycle_acl_v1.py": "7505dbc6d5fefa0148a05f30fa16d7a666c7f8c3c3473a1645403724648be795",
    "scripts/current0055_runtime_grants.py": "19f26c51146b564b830641a2e37227225e8b5484e75ce2a79620666656679ed8",
    "scripts/current0056_fitted_catalog_measurement_v1.py": "83a991ce55c313acf43f9f132531cee0a9e0174403b73b5d596da9d318048ab1",
    "scripts/current0056_fitted_catalog_pair_v1.py": "74abcc03b4c19f8d0d65c57e723ae6ea673ab1c619bcdd30d66c299c0ff52ac8",
    "scripts/current0056_fitted_delta_catalog_v1.py": "2b91b135ba24345628649d491d3c829664f2c7320c59c197f30dac5c7af2dceb",
    "scripts/current0056_native_owner_pregrant_v1.py": "cf36f15a2e54df40ea1e232651a10fdbb0432304ff4cc3b8c1bf11ddcf805833",
    "scripts/current0056_reviewed_owner_portable_v1.py": "745dfb240fdc5e163044a6725d32ae6b71aa8a7efb7655e105d99fc61e8b7388",
    "scripts/current0057_native_g12_acl_v1.py": "67ac11bb11f601c43efaf36a977954d3216d0fa30959f90c5179b0ea5e70ec14",
}


class HistoricalOwnerSource0058Error(ValueError):
    pass


def _need(value):
    if value is not True:
        raise HistoricalOwnerSource0058Error(
            "historical0056_owner_source0058_refused"
        ) from None


def require_historical_owner_source58(source):
    _need(
        isinstance(source, Path)
        and source == _SOURCE
        and source.resolve(strict=True) == source
    )
    with redirect_stdout(io.StringIO()):
        scope = scopes.database0056_source_scope(source)
    _need(
        type(scope) is scopes.ContractSourceScopeV1
        and scope.actual_source_head == "0058"
        and scope.database_target == "0056"
        and scope.actual_migration_sha256 == current.MIGRATION_INVENTORY_SHA256
        and scope.historical_prefix_sha256 == scopes._PREFIX["0056"][0]
        and scope.account_complete is scope.execution_authority is False
    )
    _need(
        all(
            sha(previous._read(source, name)) == pin
            for name, pin in _REVIEWED_SOURCE58_FILES.items()
        )
    )
    return scope


def load_historical_owner56_reference_on_source58(source):
    before = require_historical_owner_source58(source)
    first, second = (
        previous._measurement(source, index, digest)
        for index, (_, digest) in enumerate(previous._MEASUREMENTS, 1)
    )
    _need(
        first["source"] == second["source"]
        and first["run_id"] != second["run_id"]
        and first["cluster_system_identifier_sha256"]
        != second["cluster_system_identifier_sha256"]
        and first["definitions"][previous._RAW] != second["definitions"][previous._RAW]
    )
    inherited = first["definitions"][previous._INHERITED[0]]
    normalized = first["definitions"][previous._NORMALIZED]
    _need(
        all(
            row["definitions"][name] == inherited
            for row in (first, second)
            for name in previous._INHERITED
        )
        and normalized == second["definitions"][previous._NORMALIZED]
    )
    reference = canonical(
        {
            "schema": "ctcc.native_owner_0056.reviewed_portable_reference.v1",
            "run": previous._RUN,
            "measurements": [
                {"artifact_id": artifact, "receipt_sha256": digest}
                for artifact, digest in previous._MEASUREMENTS
            ],
            "inherited_native_definitions_sha256": inherited,
            "fitted_normalized_owner_catalog_sha256": normalized,
        }
    )
    _need(require_historical_owner_source58(source) == before)
    return previous.ReviewedOwnerPortable0056V1(inherited, normalized, sha(reference))


def verify_historical_owner56_reference_on_source58(source, observed):
    reference = load_historical_owner56_reference_on_source58(source)
    _need(
        type(observed) is previous.NativeOwnerPregrant0056Diagnostic
        and observed.database_head == "0056"
        and observed.grant_statements == 42
        and observed.grants_present is observed.native_reference_sealed is False
        and observed.startup_admission is observed.execution_authority is False
        and observed.admission == "DENY"
        and observed.inherited_native_definitions_sha256
        == observed.inherited_readwrite_native_definitions_sha256
        == reference.inherited_native_definitions_sha256
        and observed.fitted_normalized_owner_catalog_sha256
        == reference.fitted_normalized_owner_catalog_sha256
    )
    return reference
