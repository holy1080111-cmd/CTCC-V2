"""Prepare one signed DB0056 account for the existing native Control lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from app.database.repositories.demo_control import ControlObservation
from app.database.repositories.demo_control_current56_v1 import (
    DemoControlRepository0056V1,
)
from app.demo_automation import native_server_selectors_v2 as selectors
from app.demo_automation.native_source_only_v1 import _dispose, _settings
from app.domain.source_primitives import canonical, sha
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_owned_financial_v1 as financial
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification import native_demo_control_owner_v2 as previous
from app.trade_qualification.reservations import LedgerScope


@dataclass(frozen=True, slots=True, repr=False)
class NativeDemoControlPrepared0056:
    """One scoped Control comparison used immediately by the configured caller."""

    scope: LedgerScope = field(repr=False)
    observation: ControlObservation = field(repr=False)
    pins: control.ControlPins = field(repr=False)
    source_genesis_sha256: str
    admission: Literal["DENY"] = field(default="DENY", init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


async def prepare_configured_demo_control_0056(
    selection,
) -> NativeDemoControlPrepared0056:
    """Check genuine signed scope, first ledger source and current stored Control."""
    record, factories, roles = selectors._controlled_identity_0056(selection)
    scope = LedgerScope(
        account_id=record.scope_config.expected_uid,
        settlement_currency=record.current_plan.settlement_currency,
    )
    control_scope = control.ControlScope("demo", scope.account_id)
    repository = DemoControlRepository0056V1(
        factories, roles=roles, scope=control_scope, clock=previous._NOW
    )

    def require_same_selection() -> None:
        current_record, current_factories, current_roles = (
            selectors._controlled_identity_0056(selection)
        )
        if (
            current_record is not record
            or current_factories is not factories
            or current_roles is not roles
        ):
            raise selectors.NativeDemoCallerReadinessErrorV2(
                "native_demo_selector_0056_controlled_identity_changed"
            ) from None

    try:
        before = await repository.read(control_scope)
        require_same_selection()
        genesis = await previous._read_genesis(factories.writer_factory, scope)
        require_same_selection()
        policy = financial.capture_configured_owned_risk_policy_v1(
            drawdown_window_started_at=genesis.window_started_at
        )
        inventory = native._package_inventory_controlled55_v1()
        pins = control.ControlPins(
            config_sha256=sha(
                canonical(
                    {
                        "schema_version": "ctcc.native_demo_control.config.0056.v1",
                        "plan_sha256": selectors.capture.plan_sha256(
                            record.current_plan
                        ),
                        "scope": [
                            scope.environment,
                            scope.account_id,
                            scope.settlement_currency,
                        ],
                        "measured_genesis_sha256": genesis.source_sha256,
                        "configured_policy_sha256": policy.selected_settings_sha256,
                        "writer_inventory_sha256": inventory[0]["writer_sha256"],
                        "selector_sha256": record.route_evidence.binding_sha256,
                        "database_role_route_sha256": roles.role_route_sha256,
                        "database_server_incarnation_sha256": roles.server_incarnation_sha256,
                    }
                )
            ),
            policy_sha256=policy.selected_settings_sha256,
            credential_session_sha256=sha(
                record.read_session.binding_id.encode("ascii")
            ),
        )
        after = await repository.read(control_scope)
        require_same_selection()
        if (
            before.state != after.state
            or before.event_sha256 != after.event_sha256
            or before.observed_at > after.observed_at
            or after.state.scope != control_scope
        ):
            raise selectors.NativeDemoCallerReadinessErrorV2(
                "native_demo_control_0056_original_lineage_changed"
            ) from None
        return NativeDemoControlPrepared0056(scope, after, pins, genesis.source_sha256)
    except (asyncio.CancelledError, selectors.NativeDemoCallerReadinessErrorV2):
        raise
    except Exception:  # noqa: BLE001 -- preserve private lease and stored account data.
        raise selectors.NativeDemoCallerReadinessErrorV2(
            "native_demo_control_0056_prepare_refused"
        ) from None


async def inspect_configured_demo_control_0056(
    instrument_id: str,
) -> NativeDemoControlPrepared0056:
    """Run one approved, execution-disabled Control preparation diagnostic."""
    selection = factories = None
    try:
        settings = _settings(instrument_id)
        client = OkxDemoPrivateRestClient(settings=settings)
        with client.pinned_read_session() as lease:
            selection = (
                await selectors._select_configured_demo_controlled_identity_0056(
                    private_client=client,
                    read_session=lease,
                    instrument_ids=(instrument_id,),
                )
            )
            factories = selection.factories
            result = await prepare_configured_demo_control_0056(selection)
            lease.verify_current()
            return result
    finally:
        await _dispose(selection, factories)
