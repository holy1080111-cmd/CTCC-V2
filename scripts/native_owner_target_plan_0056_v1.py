"""Inspect one reviewed DB0056 owner target before installing any grants."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.domain.source_primitives import canonical, sha
from app.trade_evidence.native_private_root_v2 import (
    _windows_private,
    require_private_evidence_base_v2,
)
from scripts import current0056_native_owner_pregrant_v1 as owner_pregrant
from scripts import native_owner_grant_0056_draft_v1 as draft
from scripts.evidence_json import read_evidence_json
from scripts.native_owner_reviewed_source_0056_v1 import (
    require_exact_installed_owner_source_0056_v1,
)
from scripts.native_owner_route_preflight_0056_v1 import (
    OwnerRoutePinsV1,
    preflight_owner_route_0056_v1,
)
from scripts.native_owner_route_preflight_0056_v1 import _prepare as prepare_route
from scripts.native_owner_upgrade_0056_v1 import (
    OwnerUpgradePinsV1,
    _checked_profile,
)

_SOURCE = Path(__file__).resolve().parents[1]
_INPUT = "ctcc.native_owner_0056.target_plan_inputs.v1"
_OUTPUT = "ctcc.native_owner_0056.target_plan_observation.v1"
_ROLES = ("writer", "custody", "frame_reader")


class OwnerTargetPlanRefusal(ValueError):
    """Fixed denial without a route, role, path, or permission to grant."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise OwnerTargetPlanRefusal("native_owner_0056_target_plan_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class OwnerTargetPlanInputsV1:
    source: OwnerUpgradePinsV1
    writer: str
    custody: str
    frame_reader: str

    def role_names(self) -> dict[str, str]:
        _need(type(self.source) is OwnerUpgradePinsV1)
        _need(
            type(self.source.route) is OwnerRoutePinsV1
            and self.source.route.database_head == "0056"
        )
        names = {role: getattr(self, role) for role in _ROLES}
        _need(self.source.route.owner not in names.values())
        _need(len(owner_pregrant.exact_42_grant_sql_0056(**names)) == 42)
        return names


def _require_private_file(path: Path) -> None:
    require_private_evidence_base_v2(str(path.parent))
    metadata = path.lstat()
    _need(
        stat.S_ISREG(metadata.st_mode)
        and not getattr(metadata, "st_file_attributes", 0) & 1024
        and metadata.st_nlink == 1
    )
    if os.name == "nt":
        _windows_private(path)
    else:
        _need(metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o077)


def _load_inputs(path: Path, expected_sha256: str) -> OwnerTargetPlanInputsV1:
    _need(draft._digest(expected_sha256))
    _require_private_file(path)
    raw = _read_bounded_regular_file_v1(path, 32768)
    _need(sha(raw) == expected_sha256)
    value = read_evidence_json(raw)
    _need(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "source_commit",
            "source_tree",
            "source_archive_sha256",
            "route",
            *_ROLES,
        }
        and value["schema"] == _INPUT
        and type(value["route"]) is dict
        and set(value["route"]) == set(OwnerRoutePinsV1.__dataclass_fields__)
    )
    result = OwnerTargetPlanInputsV1(
        OwnerUpgradePinsV1(
            value["source_commit"],
            value["source_tree"],
            value["source_archive_sha256"],
            OwnerRoutePinsV1(**value["route"]),
        ),
        **{role: value[role] for role in _ROLES},
    )
    result.role_names()
    return result


def _reviewed_portable(observed: owner_pregrant.NativeOwnerPregrant0056Diagnostic):
    from scripts.current0056_reviewed_owner_portable_v1 import (
        ReviewedOwnerPortable0056V1,
        verify_reviewed_owner_portable_0056_v1,
    )

    reference = verify_reviewed_owner_portable_0056_v1(_SOURCE, observed)
    _need(
        type(reference) is ReviewedOwnerPortable0056V1
        and reference.grant_authority is False
        and reference.startup_admission is False
        and reference.execution_authority is False
        and reference.admission == "DENY"
        and draft._digest(reference.reference_sha256)
        and reference.inherited_native_definitions_sha256
        == observed.inherited_native_definitions_sha256
        and reference.fitted_normalized_owner_catalog_sha256
        == observed.fitted_normalized_owner_catalog_sha256
    )
    return reference.reference_sha256


async def _read_target(factory, inputs: OwnerTargetPlanInputsV1):
    """Read the original catalog in both modes without calling the writer."""
    roles = inputs.role_names()
    route = inputs.source.route
    names = tuple(roles.values())
    observed = await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
        factory, source=_SOURCE, **roles
    )
    _need(
        type(observed) is owner_pregrant.NativeOwnerPregrant0056Diagnostic
        and observed.cluster_system_identifier_sha256
        == route.cluster_system_identifier_sha256
        and observed.grants_present
        is observed.startup_admission
        is observed.execution_authority
        is False
    )
    reference_sha = _reviewed_portable(observed)
    readwrite = await owner_pregrant._read_owner_pregrant_graph_0056(
        factory, route.owner, names, readwrite=True
    )
    _need(
        readwrite[1] == observed.inherited_native_definitions_sha256
        and readwrite[3] == route.cluster_system_identifier_sha256
    )
    async with factory() as session, session.begin():
        # The original fitted verifier asserts READ WRITE at catalog-read time.
        # All SQL after the transaction settings is SELECT.
        for statement in draft._SETTINGS:
            await session.execute(text(statement))
        await session.execute(text("SET LOCAL search_path TO pg_catalog, pg_temp"))
        profile = tuple((await session.execute(draft._PROFILE_SQL)).one())
        _checked_profile(profile, route, "0056", read_only=False)
        identity = await draft.roles56._identity_at_0056(session, route.owner)
        _need(identity == readwrite[0])
        connection = await session.connection()
        role_oids = await draft.core_acl._roles(connection, names, route.owner_oid)
        await draft.roles56._require_no_effective_demo_fitted_execute(connection, names)
        fitted = await draft.roles56._catalog(
            session, draft.fitted56.OWNER_READWRITE_CATALOG_SQL
        )
        draft.fitted56.inspect_owner_readwrite_catalog(
            fitted, owner=route.owner, demo_roles=names
        )
        _need(
            identity == await draft.roles56._identity_at_0056(session, route.owner)
            and role_oids
            == await draft.core_acl._roles(connection, names, route.owner_oid)
        )
    pins = draft.OwnerGrantDraftPinsV1(
        source_commit=inputs.source.source_commit,
        source_tree=inputs.source.source_tree,
        source_archive_sha256=inputs.source.source_archive_sha256,
        route=route,
        **roles,
        **{f"{role}_oid": role_oids[name] for role, name in roles.items()},
        portable_inherited_definitions_sha256=observed.inherited_native_definitions_sha256,
        portable_fitted_pregrant_normalized_sha256=observed.fitted_normalized_owner_catalog_sha256,
        raw_inherited_owner_readonly_sha256=observed.inherited_raw_owner_graph_sha256,
        raw_inherited_owner_readwrite_sha256=readwrite[2],
        raw_fitted_owner_readonly_sha256=observed.fitted_raw_owner_catalog_sha256,
        raw_fitted_owner_readwrite_sha256=sha(canonical(fitted)),
        exact_42_plan_sha256=observed.exact_grant_plan_sha256,
    )
    # The draft's observation reopens the catalog independently and enforces
    # physical equality across the read-only and read-write fitted snapshots.
    await draft._read_before(factory, pins)
    async with factory() as session, session.begin():
        for statement in owner_pregrant._OWNER_SETTINGS:
            await session.execute(text(statement))
        profile = tuple((await session.execute(draft._PROFILE_SQL)).one())
        _checked_profile(profile, route, "0056", read_only=True)
        final = await draft.roles56._identity_at_0056(session, route.owner)
        _need(
            final == identity
            and await draft.core_acl._roles(
                await session.connection(), names, route.owner_oid
            )
            == role_oids
        )
    return pins, reference_sha


def _receipt(pins: draft.OwnerGrantDraftPinsV1, reference_sha: str) -> dict:
    return {
        "schema": _OUTPUT,
        "status": "OBSERVED_DENY",
        "database_head": "0056",
        "source_commit": pins.source_commit,
        "source_tree": pins.source_tree,
        "source_archive_sha256": pins.source_archive_sha256,
        "source_manifest_sha256": pins.route.source_manifest_sha256,
        "portable_reference_matched": True,
        "portable_reference_sha256": reference_sha,
        "target_grant_pins_sha256": sha(canonical(asdict(pins))),
        "raw_inherited_owner_readonly_sha256": pins.raw_inherited_owner_readonly_sha256,
        "raw_inherited_owner_readwrite_sha256": pins.raw_inherited_owner_readwrite_sha256,
        "raw_fitted_owner_readonly_sha256": pins.raw_fitted_owner_readonly_sha256,
        "raw_fitted_owner_readwrite_sha256": pins.raw_fitted_owner_readwrite_sha256,
        "exact_42_plan_sha256": pins.exact_42_plan_sha256,
        "grant_statements": 42,
        "grants_present": False,
        "external_approval_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


async def observe_reviewed_owner_target_plan_0056_v1(
    inputs: OwnerTargetPlanInputsV1,
    *,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
) -> dict:
    """Bind the exact reviewed source, cluster, role definitions, and ACL plan."""
    try:
        _need(type(inputs) is OwnerTargetPlanInputsV1)
        inputs.role_names()
        require_exact_installed_owner_source_0056_v1(
            _SOURCE, inputs.source, source_archive_file
        )
        _require_private_file(owner_dsn_file)
        route = inputs.source.route
        prepared = prepare_route(route, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
        async with asyncio.timeout(160):
            gate = await preflight_owner_route_0056_v1(
                route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
            )
            _need(
                gate["status"] == "PASS"
                and gate["route_sha256"] == prepared.route_sha256
                and gate["startup_admission"] is gate["execution_authority"] is False
            )
            engine = create_async_engine(
                prepared.url,
                poolclass=NullPool,
                connect_args={"ssl": prepared.tls, "timeout": 10},
                hide_parameters=True,
            )
            try:
                factory = async_sessionmaker(
                    engine, autoflush=False, expire_on_commit=False
                )
                pins, reference_sha = await _read_target(factory, inputs)
            finally:
                await engine.dispose()
        after = prepare_route(route, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
        _need(
            after.url == prepared.url
            and after.manifest_sha256 == prepared.manifest_sha256
            and after.migration_sha256 == prepared.migration_sha256
            and after.route_sha256 == prepared.route_sha256
        )
        require_exact_installed_owner_source_0056_v1(
            _SOURCE, inputs.source, source_archive_file
        )
        return _receipt(pins, reference_sha)
    except (asyncio.CancelledError, OwnerTargetPlanRefusal):
        raise
    except Exception:  # noqa: BLE001 -- redact credentials, route, roles and catalog fields
        _need(False)


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _need(False)


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--inspect-reviewed-owner-target", action="store_true")
    parser.add_argument("--reviewed-inputs-file", type=Path, required=True)
    parser.add_argument("--reviewed-inputs-sha256", required=True)
    parser.add_argument("--owner-dsn-file", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--source-archive-file", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        _need(args.inspect_reviewed_owner_target is True)
        inputs = _load_inputs(args.reviewed_inputs_file, args.reviewed_inputs_sha256)
        receipt = asyncio.run(
            observe_reviewed_owner_target_plan_0056_v1(
                inputs,
                owner_dsn_file=args.owner_dsn_file,
                ca_file=args.ca_file,
                source_archive_file=args.source_archive_file,
            )
        )
    except BaseException:  # noqa: BLE001 -- fixed refusal, including interruption
        print(
            json.dumps(
                {
                    "schema": _OUTPUT,
                    "status": "REFUSED",
                    "admission": "DENY",
                    "execution_authority": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
