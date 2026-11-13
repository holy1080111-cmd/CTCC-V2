"""Inspect a configured restricted DB0056 source profile without exchange I/O."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.config.settings import Settings, get_settings
from app.core.native_only_lifespan import require_native_only_settings
from app.database.native_pg_tls_v1 import (
    configured_native_pg_tls_context_v1,
    reviewed_native_pg_tls_route_v1,
)
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2

_Stage = Literal["PASS", "FAIL", "NOT_RUN"]
_ORIGIN = "https://openapi.okx.com"
_SOURCE = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class NativeSourceProfileInspection0056V1:
    settings: _Stage = "NOT_RUN"
    native_profile: _Stage = "NOT_RUN"
    private_evidence_root: _Stage = "NOT_RUN"
    postgres_tls_configuration: _Stage = "NOT_RUN"
    restricted_postgres_roles: _Stage = "NOT_RUN"
    demo_source_configuration: _Stage = "NOT_RUN"
    schema: str = field(
        default="ctcc.demo_db0056.source_profile_inspection.v1", init=False
    )
    admission: Literal["DENY"] = field(default="DENY", init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    exchange_requests: Literal[0] = field(default=0, init=False)

    @property
    def checks_passed(self) -> bool:
        return (
            self.settings
            == self.native_profile
            == self.private_evidence_root
            == self.postgres_tls_configuration
            == self.restricted_postgres_roles
            == self.demo_source_configuration
            == "PASS"
        )


def _settings_profile(settings: Settings) -> None:
    require_native_only_settings(settings)
    if settings.ctcc_native_only_lifespan_v1 is not True:
        raise ValueError("native_source_profile_not_enabled")


def _tls_configuration(settings: Settings) -> None:
    urls = tuple(
        reviewed_native_pg_tls_route_v1(raw)
        for raw in (
            settings.database_url,
            settings.okx_demo_native_custody_database_url.get_secret_value(),
            settings.okx_demo_native_frame_reader_database_url.get_secret_value(),
        )
    )
    routes = {(url.host, url.port or 5432, url.database) for url in urls}
    if len(routes) != 1 or len({url.username for url in urls}) != 3:
        raise ValueError("native_source_profile_roles_not_distinct")
    context = configured_native_pg_tls_context_v1(
        ca_file=settings.ctcc_native_pg_tls_ca_file,
        ca_sha256=settings.ctcc_native_pg_tls_ca_sha256,
        database_url=settings.database_url,
    )
    if context is None:
        raise ValueError("native_source_profile_tls_required")


def _demo_configuration(settings: Settings, instrument: str) -> _Stage:
    if (
        settings.okx_demo_credentials_configured is True
        and settings.okx_demo_rest_base_url == _ORIGIN
        and settings.okx_demo_read_max_retries == 0
        and type(instrument) is str
        and instrument in settings.okx_demo_allowed_symbol_list
    ):
        return "PASS"
    return "FAIL"


async def _observe_configured_roles() -> None:
    # Import only after local checks, so malformed settings never initialize
    # the selector's configured writer or either restricted companion.
    from app.core.native_role_witness_0056_v1 import (
        RestrictedNativeRoles0056WitnessV1,
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.demo_automation.native_server_selectors_v2 import (
        select_configured_native_demo_factories_v2,
    )
    from app.demo_automation.native_source_only_v1 import _dispose

    factories = select_configured_native_demo_factories_v2()
    try:
        async with asyncio.timeout(60):
            result = await observe_restricted_native_roles_0056_witness_v1(
                factories, source=_SOURCE
            )
        if (
            type(result) is not RestrictedNativeRoles0056WitnessV1
            or result.observation.database_head != "0056"
            or result.observation.admission != "DENY"
            or result.startup_admission is not False
            or result.execution_authority is not False
        ):
            raise ValueError("native_source_profile_restricted_roles_refused")
    finally:
        await _dispose(None, factories)


async def inspect_configured_demo_source_profile_0056_v1(
    instrument: str,
) -> NativeSourceProfileInspection0056V1:
    """Collect fixed stage statuses using local configuration and restricted SQL."""
    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 -- configured input values never escape
        return NativeSourceProfileInspection0056V1(settings="FAIL")

    try:
        _settings_profile(settings)
    except Exception:  # noqa: BLE001 -- keep incompatible profiles offline
        return NativeSourceProfileInspection0056V1(
            settings="PASS", native_profile="FAIL"
        )

    try:
        require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
        evidence: _Stage = "PASS"
    except Exception:  # noqa: BLE001 -- the private path never escapes
        evidence = "FAIL"

    try:
        _tls_configuration(settings)
        tls: _Stage = "PASS"
    except Exception:  # noqa: BLE001 -- DSNs, CA path and TLS detail stay private
        tls = "FAIL"

    roles: _Stage = "NOT_RUN"
    if tls == "PASS":
        try:
            await _observe_configured_roles()
            roles = "PASS"
        except Exception:  # noqa: BLE001 -- redact SQL, roles and routes
            roles = "FAIL"

    return NativeSourceProfileInspection0056V1(
        settings="PASS",
        native_profile="PASS",
        private_evidence_root=evidence,
        postgres_tls_configuration=tls,
        restricted_postgres_roles=roles,
        demo_source_configuration=_demo_configuration(settings, instrument),
    )
