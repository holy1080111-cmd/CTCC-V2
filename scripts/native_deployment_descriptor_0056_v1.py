"""Export stable DB0056 deployment pins and diagnose an owner-free runtime."""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config.settings import get_settings
from app.core import native_role_identity_0056_v2 as identity
from app.core.native_only_lifespan import require_native_only_settings
from app.database.native_pg_tls_v1 import verified_native_pg_tls_context_v1
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical, sha
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_qualification.account_native_proof_storage import _root_context
from scripts.current0056_reviewed_owner_portable_v1 import (
    load_reviewed_owner_portable_0056_v1,
)
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_postgrant_readback_0056_v1 import (
    _SCHEMA as _OWNER_READBACK_SCHEMA,
)
from scripts.native_owner_postgrant_readback_0056_v1 import (
    _load_plan,
    observe_owner_postgrant_readback_0056_v1,
)
from scripts.native_owner_reviewed_source_0056_v1 import (
    require_exact_installed_source_0056_v1,
)
from scripts.native_owner_target_plan_0056_v1 import _require_private_file

_SOURCE = Path(__file__).resolve().parents[1]
_SCHEMA = "ctcc.native_only.db0056_deployment_descriptor.inert.v1"
_OUTPUT_SCHEMA = "ctcc.native_only.db0056_deployment_descriptor_diagnostic.inert.v1"
_LEAF = "db0056-deployment-descriptor.json"
_LIMIT = 8192
_SHA = re.compile(r"[0-9a-f]{64}", re.ASCII)
_GIT_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)


class NativeDeploymentDescriptor0056Error(ValueError):
    """Fixed refusal without deployment secrets or private file names."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise NativeDeploymentDescriptor0056Error(
            "db0056_deployment_descriptor_refused"
        ) from None


def _matches(value: object, pattern: re.Pattern) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


@dataclass(frozen=True, slots=True, repr=False)
class _Descriptor:
    source_commit: str
    source_tree: str
    source_archive_sha256: str
    source_manifest_sha256: str
    tls_ca_sha256: str
    restricted_role_identity_sha256: str
    inherited_native_definitions_sha256: str
    granted_inherited_acl_shape_sha256: str
    reviewed_portable_reference_sha256: str
    owner_observed_cluster_sha256: str
    owner_grant_plan_sha256: str

    def __post_init__(self) -> None:
        _need(
            all(
                _matches(
                    value,
                    _GIT_SHA if name in ("source_commit", "source_tree") else _SHA,
                )
                for name, value in asdict(self).items()
            )
        )

    @property
    def payload(self) -> bytes:
        return canonical({"schema": _SCHEMA, "database_head": "0056", **asdict(self)})


def _read_private_descriptor(path: Path, expected: str) -> _Descriptor:
    _need(_matches(expected, _SHA))
    _require_private_file(path)
    with _root_context(path.parent) as directory:
        raw = directory.read(path.name, _LIMIT)
    _need(sha(raw) == expected)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and set(value) == {"schema", "database_head", *_Descriptor.__dataclass_fields__}
        and value["schema"] == _SCHEMA
        and value["database_head"] == "0056"
    )
    descriptor = _Descriptor(
        **{name: value[name] for name in _Descriptor.__dataclass_fields__}
    )
    _need(descriptor.payload == raw)
    return descriptor


def _publish_descriptor(root: Path, descriptor: _Descriptor) -> str:
    path = require_private_evidence_base_v2(str(root))
    payload = descriptor.payload
    digest = sha(payload)
    with _root_context(path) as directory:
        directory.publish(_LEAF, payload)
        _require_private_file(path / _LEAF)
        _need(directory.read(_LEAF, _LIMIT) == payload)
    _need(_read_private_descriptor(path / _LEAF, digest) == descriptor)
    return digest


async def export_owner_verified_descriptor_0056_v1(
    *,
    plan_file: Path,
    plan_file_sha256: str,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
    descriptor_directory: Path,
) -> str:
    """Observe the actual owner and restricted roles before publishing stable pins."""
    require_private_evidence_base_v2(str(descriptor_directory))
    pins = _load_plan(plan_file, plan_file_sha256)
    reference = load_reviewed_owner_portable_0056_v1(_SOURCE)
    _need(
        reference.inherited_native_definitions_sha256
        == pins.portable_inherited_definitions_sha256
        and reference.fitted_normalized_owner_catalog_sha256
        == pins.portable_fitted_pregrant_normalized_sha256
    )
    owner_readback = await observe_owner_postgrant_readback_0056_v1(
        pins,
        owner_dsn_file=owner_dsn_file,
        ca_file=ca_file,
        source_archive_file=source_archive_file,
    )
    _need(_load_plan(plan_file, plan_file_sha256) == pins)
    _need(
        type(owner_readback) is dict
        and owner_readback["schema"] == _OWNER_READBACK_SCHEMA
        and owner_readback["status"] == "OBSERVED_DENY"
        and owner_readback["database_head"] == "0056"
        and owner_readback["restricted_logins_observed"] == 3
        and owner_readback["separate_read_only_owner_observations"] == 2
        and owner_readback["target_grant_pins_sha256"] == sha(canonical(asdict(pins)))
        and owner_readback["cluster_system_identifier_sha256"]
        == pins.route.cluster_system_identifier_sha256
        and owner_readback["inherited_native_definitions_sha256"]
        == reference.inherited_native_definitions_sha256
        and owner_readback["startup_admission"]
        is owner_readback["execution_authority"]
        is False
        and owner_readback["external_approval_authenticated"] is False
        and owner_readback["admission"] == "DENY"
    )
    return _publish_descriptor(
        descriptor_directory,
        _Descriptor(
            pins.source_commit,
            pins.source_tree,
            pins.source_archive_sha256,
            pins.route.source_manifest_sha256,
            pins.route.ca_sha256,
            owner_readback["restricted_role_identity_sha256"],
            owner_readback["inherited_native_definitions_sha256"],
            owner_readback["granted_inherited_acl_shape_sha256"],
            reference.reference_sha256,
            owner_readback["cluster_system_identifier_sha256"],
            owner_readback["target_grant_pins_sha256"],
        ),
    )


def _check_installed_release(
    descriptor: _Descriptor, source_archive_file: Path
) -> None:
    require_exact_installed_source_0056_v1(
        _SOURCE,
        source_archive_file,
        source_commit=descriptor.source_commit,
        source_tree=descriptor.source_tree,
        source_archive_sha256=descriptor.source_archive_sha256,
        source_manifest_sha256=descriptor.source_manifest_sha256,
    )
    reference = load_reviewed_owner_portable_0056_v1(_SOURCE)
    _need(
        descriptor.inherited_native_definitions_sha256
        == reference.inherited_native_definitions_sha256
        and descriptor.reviewed_portable_reference_sha256 == reference.reference_sha256
    )


def _check_runtime_settings(settings, descriptor: _Descriptor) -> None:
    require_native_only_settings(settings)
    _need(
        settings.ctcc_native_only_lifespan_v1 is True
        and settings.ctcc_native_pg_tls_ca_sha256 == descriptor.tls_ca_sha256
    )
    verified_native_pg_tls_context_v1(
        Path(settings.ctcc_native_pg_tls_ca_file), descriptor.tls_ca_sha256
    )


def _check_restricted_role_observation(actual, descriptor: _Descriptor) -> None:
    _need(
        type(actual) is identity.NativeRoleIdentity0056V2
        and actual.stable_identity_sha256 == descriptor.restricted_role_identity_sha256
        and actual.inherited_native_definitions_sha256
        == descriptor.inherited_native_definitions_sha256
        and actual.granted_inherited_acl_shape_sha256
        == descriptor.granted_inherited_acl_shape_sha256
        and actual.deployment_reference_authenticated is False
        and actual.startup_admission is actual.execution_authority is False
        and actual.admission == "DENY"
    )


async def verify_restricted_deployment_descriptor_0056_v1(
    *,
    descriptor_file: Path,
    descriptor_sha256: str,
    source_archive_file: Path,
) -> str:
    """Use only the configured restricted logins and replay exact stable pins."""
    descriptor = _read_private_descriptor(descriptor_file, descriptor_sha256)
    _check_installed_release(descriptor, source_archive_file)
    settings = get_settings()
    _check_runtime_settings(settings, descriptor)
    selected = selectors.select_configured_native_demo_factories_v2()
    try:
        actual = await identity.observe_restricted_role_identity_0056_v2(
            selected, source=_SOURCE
        )
        _check_restricted_role_observation(actual, descriptor)
    finally:
        await selectors._dispose_failed_selection_factories_v2(selected)
    _need(get_settings() is settings)
    _check_runtime_settings(settings, descriptor)
    _check_installed_release(descriptor, source_archive_file)
    _need(_read_private_descriptor(descriptor_file, descriptor_sha256) == descriptor)
    return descriptor_sha256


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _need(False)


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(allow_abbrev=False)
    actions = parser.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export-owner-verified", allow_abbrev=False)
    verify = actions.add_parser("verify-restricted", allow_abbrev=False)
    for action in (export, verify):
        action.add_argument("--source-archive-file", required=True, type=Path)
    export.add_argument("--plan-file", required=True, type=Path)
    export.add_argument("--plan-file-sha256", required=True)
    export.add_argument("--owner-dsn-file", required=True, type=Path)
    export.add_argument("--ca-file", required=True, type=Path)
    export.add_argument("--descriptor-directory", required=True, type=Path)
    verify.add_argument("--descriptor-file", required=True, type=Path)
    verify.add_argument("--descriptor-sha256", required=True)
    result = {
        "schema": _OUTPUT_SCHEMA,
        "deployment_reference_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
        "order_writes": 0,
    }
    try:
        options = vars(parser.parse_args(argv))
        exported = options.pop("action") == "export-owner-verified"
        operation = (
            export_owner_verified_descriptor_0056_v1
            if exported
            else verify_restricted_deployment_descriptor_0056_v1
        )
        digest = asyncio.run(operation(**options))
        result.update(
            status="DESCRIPTOR_EXPORTED" if exported else "RESTRICTED_MATCH_OBSERVED",
            descriptor_sha256=digest,
            owner_cluster_observed_in_invocation=exported,
        )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- never echo private inputs
        print(canonical({**result, "status": "REFUSED"}).decode("ascii"))
        return 1
    print(canonical(result).decode("ascii"))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
