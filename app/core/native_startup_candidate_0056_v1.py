"""Owner-free DB0056 startup candidate; an observed match still denies boot.

The pins passed here are diagnostic inputs. They are not an authenticated
deployment reference, and this module never grants startup or order authority.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_startup_control_observation_0056_v1 as control56
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import sha
from scripts import manifest

_SOURCE = Path(__file__).resolve().parents[2]
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class NativeStartupCandidate0056Error(RuntimeError):
    """Fixed refusal without source paths, roles, routes, or control rows."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeStartupCandidate0056PinsV1:
    """Exact expected values, with no assertion about who supplied them."""

    source_manifest_sha256: str
    inherited_native_definitions_sha256: str
    granted_inherited_acl_shape_sha256: str
    deployment_frame_writer_sha256: str
    deployment_custody_sha256: str
    deployment_server_incarnation_sha256: str
    deployment_role_route_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class NativeStartupCandidate0056ObservationV1:
    """Coherent diagnostic only; deliberately unusable as a startup seal."""

    source_manifest_sha256: str
    inherited_native_definitions_sha256: str
    granted_inherited_acl_shape_sha256: str
    deployment_frame_writer_sha256: str
    deployment_custody_sha256: str
    deployment_server_incarnation_sha256: str
    deployment_role_route_sha256: str
    control_revision: int = field(repr=False)
    schema: Literal["ctcc.native_only.startup_candidate0056.inert.v1"] = field(
        default="ctcc.native_only.startup_candidate0056.inert.v1", init=False
    )
    independent_reference_authenticated: Literal[False] = field(
        default=False, init=False
    )
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(value: bool) -> None:
    if value is not True:
        raise NativeStartupCandidate0056Error("native_startup_candidate_0056_denied")


def _source_manifest_sha256(source: Path) -> str:
    _need(
        isinstance(source, Path)
        and source.is_dir()
        and not source.is_symlink()
        and not (source / "MANIFEST.sha256").is_symlink()
    )
    path = source / "MANIFEST.sha256"
    before = path.read_bytes()
    _need(
        0 < len(before) <= 2_000_000 and before.endswith(b"\n") and b"\r" not in before
    )
    _need(manifest.read_manifest(path) == manifest.build_manifest(source))
    _need(path.read_bytes() == before)
    return sha(before)


def _pins(pins: NativeStartupCandidate0056PinsV1) -> None:
    _need(
        type(pins) is NativeStartupCandidate0056PinsV1
        and all(
            type(getattr(pins, field_name)) is str
            and _HEX.fullmatch(getattr(pins, field_name)) is not None
            for field_name in pins.__dataclass_fields__
        )
    )


async def observe_selected_native_startup_candidate_0056_v1(
    selected, *, source: Path, pins: NativeStartupCandidate0056PinsV1
) -> NativeStartupCandidate0056ObservationV1:
    """Compare four original restricted reads; never turn input pins into trust."""
    try:
        _pins(pins)
        _need(type(selected) is selectors.ConfiguredNativeDemoFactoriesV2)
        source_sha = _source_manifest_sha256(source)
        _need(source_sha == pins.source_manifest_sha256)
        first = await roles56.observe_restricted_native_roles_0056_witness_v1(
            selected, source=source
        )
        control_before = await control56.observe_startup_control_0056_witness_v1(
            selected
        )
        second = await roles56.observe_restricted_native_roles_0056_witness_v1(
            selected, source=source
        )
        control_after = await control56.observe_startup_control_0056_witness_v1(
            selected
        )
        _need(
            type(first) is roles56.RestrictedNativeRoles0056WitnessV1
            and type(second) is roles56.RestrictedNativeRoles0056WitnessV1
            and type(control_before) is control56.NativeStartupControl0056WitnessV1
            and type(control_after) is control56.NativeStartupControl0056WitnessV1
            and first == second
            and control_before == control_after
            and first.server_incarnation_sha256
            == control_before.server_incarnation_sha256
            == pins.deployment_server_incarnation_sha256
            and first.role_route_sha256
            == control_before.role_route_sha256
            == pins.deployment_role_route_sha256
            and first.observation.inherited_native_definitions_sha256
            == pins.inherited_native_definitions_sha256
            and first.observation.granted_inherited_acl_shape_sha256
            == pins.granted_inherited_acl_shape_sha256
            and first.observation.frame_writer_observation_sha256
            == pins.deployment_frame_writer_sha256
            and first.observation.raw_custody_catalog_sha256
            == pins.deployment_custody_sha256
            and first.observation.admission == "DENY"
            and first.observation.startup_admission is False
            and first.observation.execution_authority is False
            and control_before.observation.startup_admission is False
            and control_before.observation.execution_authority is False
            and type(control_before.observation.control.revision) is int
            and control_before.observation.control.revision >= 1
            and _source_manifest_sha256(source) == source_sha
        )
        return NativeStartupCandidate0056ObservationV1(
            source_sha,
            first.observation.inherited_native_definitions_sha256,
            first.observation.granted_inherited_acl_shape_sha256,
            first.observation.frame_writer_observation_sha256,
            first.observation.raw_custody_catalog_sha256,
            first.server_incarnation_sha256,
            first.role_route_sha256,
            control_before.observation.control.revision,
        )
    except asyncio.CancelledError:
        raise
    except NativeStartupCandidate0056Error:
        raise
    except Exception:  # noqa: BLE001 -- no private DB/source detail escapes
        raise NativeStartupCandidate0056Error(
            "native_startup_candidate_0056_unavailable"
        ) from None


async def preflight_configured_native_startup_candidate_0056_v1(
    expected_writer_factory, *, pins: NativeStartupCandidate0056PinsV1
) -> None:
    """Select the genuine runtime factories and always refuse startup.

    In particular, replacing the DB0055 app.main preflight with this function
    cannot accidentally pass because the old caller ignores return values.
    """
    selected = None
    failure: BaseException | None = None
    try:
        selected = selectors.select_configured_native_demo_factories_v2()
        _need(selected.writer_factory is expected_writer_factory)
        await observe_selected_native_startup_candidate_0056_v1(
            selected, source=_SOURCE, pins=pins
        )
        _need(False)
    except asyncio.CancelledError as error:
        failure = error
    except NativeStartupCandidate0056Error as error:
        failure = error
    except Exception:  # noqa: BLE001 -- no route or credential detail escapes
        failure = NativeStartupCandidate0056Error(
            "native_startup_candidate_0056_unavailable"
        )
    finally:
        if selected is not None:
            for engine in (selected.custody_engine, selected.frame_reader_engine):
                try:
                    await engine.dispose()
                except BaseException as error:  # noqa: BLE001 -- preserve first failure
                    if failure is None:
                        failure = (
                            error
                            if isinstance(error, asyncio.CancelledError)
                            else NativeStartupCandidate0056Error(
                                "native_startup_candidate_0056_disposal_failed"
                            )
                        )
    _need(failure is not None)
    raise failure
