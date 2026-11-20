"""Reviewed source57 may inspect the unchanged physical56 portable owner baseline.

These historical references never admit a physical57 catalog, add a grant,
seal a native reference or issue startup/trading authority.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from app.domain.source_primitives import canonical, sha
from scripts import current0056_reviewed_owner_portable_v1 as previous
from scripts import current0057_migration_pin as current
from scripts import current_contract_source_scopes_v1 as scopes

_SOURCE = Path(__file__).resolve().parents[1]
# Explicitly reviewed observer source at sealed e174d67. Runtime owner catalogs
# must still match both original independently measured physical56 references.
_REVIEWED_SOURCE57_FILES = {
    "app/core/native_role_preflight_0056_full_diagnostic_v1.py": "ef2b35fef1539dc22ef4d66ba831338465a606f17f08b3966a2500b8765d7b0a",
    "app/core/native_role_preflight_v1.py": "1ba3931e8cf2e81c712592834de37da900a86bc8cf2499b22cf9eca230fccb9d",
    "app/domain/collector_current_0055_catalog_v1.py": "fa7e80105102a9eca72176c7c9243c604d275020fae37dd4fbd12f7a81d28652",
    "app/domain/collector_current_0056_inherited_catalog_v1.py": "2b6102be5014ab85b29e2ccc016100f45a6cb7b0803398b568c3c2885659a17f",
    "pyproject.toml": "c47e6ba2cbb0f938ea961a7ff075e550e3c66c30b883e2d4fd1162ca94f83785",
    "requirements/validation-linux-py312.json": "c2641f377984e206567f22a869a5b2ff4de1b553c0a64c6e4add7ebf20f27b95",
    "requirements/validation-linux-py312.lock": "a44e649cb1d21d3790e132c21699cc172e77ed87682929320cf8f6190fe014e4",
    "scripts/current0055_native_account_source_acl_v1.py": "6a8cbe69bcddd47b3d1ea005e1914403c9f18add2f5df077ef80c6284fd672ab",
    "scripts/current0055_native_lifecycle_acl_v1.py": "7505dbc6d5fefa0148a05f30fa16d7a666c7f8c3c3473a1645403724648be795",
    "scripts/current0055_runtime_grants.py": "19f26c51146b564b830641a2e37227225e8b5484e75ce2a79620666656679ed8",
    "scripts/current0056_fitted_catalog_measurement_v1.py": "91ffef392e4fe86265c2fcae7a580414957e47eb44b38190721c132ad50f5195",
    "scripts/current0056_fitted_catalog_pair_v1.py": "74abcc03b4c19f8d0d65c57e723ae6ea673ab1c619bcdd30d66c299c0ff52ac8",
    "scripts/current0056_fitted_delta_catalog_v1.py": "2b91b135ba24345628649d491d3c829664f2c7320c59c197f30dac5c7af2dceb",
    "scripts/current0056_native_owner_pregrant_v1.py": "cf36f15a2e54df40ea1e232651a10fdbb0432304ff4cc3b8c1bf11ddcf805833",
    "scripts/current0056_reviewed_owner_portable_v1.py": "745dfb240fdc5e163044a6725d32ae6b71aa8a7efb7655e105d99fc61e8b7388",
}


class HistoricalOwnerSource0057Error(ValueError):
    pass


def _need(value):
    if value is not True:
        raise HistoricalOwnerSource0057Error(
            "historical0056_owner_source0057_refused"
        ) from None


def require_historical_owner_source57(source):
    _need(
        isinstance(source, Path)
        and source == _SOURCE
        and source.resolve(strict=True) == source
    )
    with redirect_stdout(io.StringIO()):
        scope = scopes.database0056_source_scope(source)
    _need(
        type(scope) is scopes.ContractSourceScopeV1
        and scope.actual_source_head == "0057"
        and scope.database_target == "0056"
        and scope.actual_migration_sha256 == current.MIGRATION_INVENTORY_SHA256
        and scope.historical_prefix_sha256 == current.PARENT_INVENTORY_SHA256
        and scope.account_complete is scope.execution_authority is False
    )
    _need(
        all(
            sha(previous._read(source, name)) == pin
            for name, pin in _REVIEWED_SOURCE57_FILES.items()
        )
    )
    return scope


def load_historical_owner56_reference_on_source57(source):
    before = require_historical_owner_source57(source)
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
    _need(require_historical_owner_source57(source) == before)
    return previous.ReviewedOwnerPortable0056V1(inherited, normalized, sha(reference))


def verify_historical_owner56_reference_on_source57(source, observed):
    reference = load_historical_owner56_reference_on_source57(source)
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
