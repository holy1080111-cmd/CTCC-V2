"""Authenticated current-UID local journal discovery, with no trading writes."""

import asyncio
import sys

from sqlalchemy import text

from app.api.security import require_ctcc_token
from app.database.repositories import native_closed_reporting_0056_v1 as database
from app.demo_automation import DemoAutomationBusyError, DemoAutomationSafetyError
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation import native_server_selectors_v2 as selectors
from app.demo_automation.native_journal_models_v1 import NativeJournalReadoutV1
from app.domain import native_database_profile_v1 as profiles
from app.domain.source_primitives import canonical, sha
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import native_continuation_guard_v1 as guard

_QUERY = text("""
WITH scoped AS MATERIALIZED (
 SELECT reservation_id,report_id,instrument_id,direction,state AS local_state,
        state_revision,created_at,updated_at,deadline,
        request_json::jsonb->>'contract_version' AS contract_version
 FROM public.qualification_reservations
 WHERE environment='demo' AND account_id=:uid AND settlement_currency='USDT'
), native_rows AS MATERIALIZED (
 SELECT reservation_id,report_id,instrument_id,direction,local_state,state_revision,
        created_at,updated_at,deadline
 FROM scoped WHERE contract_version='ctcc-reservation-request-native-v4'
), page AS (
 SELECT * FROM native_rows
 ORDER BY (local_state='reconciled_flat'),created_at DESC,reservation_id
 LIMIT :limit
)
SELECT pg_catalog.clock_timestamp() AS observed_at,
       (SELECT count(*) FROM native_rows) AS total_native,
       (SELECT count(*) FROM native_rows WHERE local_state<>'reconciled_flat') AS unresolved_native,
       (SELECT count(*) FROM scoped WHERE local_state<>'reconciled_flat'
          AND contract_version IS DISTINCT FROM 'ctcc-reservation-request-native-v4') AS unresolved_other,
       COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY (local_state='reconciled_flat'),created_at DESC,reservation_id) FROM page),'[]'::jsonb) AS entries
""")


def _need(value):
    if value is not True:
        raise DemoAutomationSafetyError("native_journal_readout_unavailable")


def _readout(raw, *, uid, head, limit):
    """Strict DATA projection only. No caller-supplied success flag is read."""
    _need(
        type(raw) is dict
        and set(raw)
        == {
            "observed_at",
            "total_native",
            "unresolved_native",
            "unresolved_other",
            "entries",
        }
    )
    _need(type(raw["entries"]) is list and len(raw["entries"]) <= limit)
    _need(
        all(
            type(raw[key]) is int and raw[key] >= 0
            for key in ("total_native", "unresolved_native", "unresolved_other")
        )
    )
    # SQL returns a verified complete local page in ONE statement snapshot.
    # A genuinely empty local table is distinct from unknown exchange exposure.
    value = {
        **raw,
        "observed_at": raw["observed_at"].isoformat(),
        "account_binding_sha256": sha(canonical(["demo", uid, "USDT"])),
        "database_head": head,
        "limit": limit,
        "truncated": raw["total_native"] > limit,
    }
    return NativeJournalReadoutV1.model_validate_json(canonical(value), strict=True)


async def read_native_journal_for_service_v1(service, *, limit, operator_token):
    """One operator-requested signed GET, then bounded scoped local SELECT only.

    Neither Arm nor order-write enablement is needed to find a lost operation.
    A fresh original selector binds exact UID, credential session and actual
    restricted database roles. This creates no event, intent or reservation.
    """
    _NATIVE_GUARD()
    await require_ctcc_token(operator_token)
    settings = caller._service(service)
    _need(type(limit) is int and 1 <= limit <= 100)
    _need(service._recovered is True and service.repository is not None)
    _need(
        settings.trading_mode == "okx_demo"
        and settings.okx_demo_enabled is True
        and settings.live_trading is False
        and settings.okx_live_enabled is False
        and settings.okx_live_allow_order_writes is False
        and settings.okx_live_auto_execution is False
        and settings.paper_auto_execution is False
    )
    if service._run_lock.locked():
        raise DemoAutomationBusyError("native_demo_run_in_progress")
    selection = factories = None
    async with service._run_lock, asyncio.timeout(20):
        client = OkxDemoPrivateRestClient(settings=settings)
        try:
            with client.pinned_read_session() as lease:
                selection = await selectors._select_configured_demo_runtime_identity_v2(
                    private_client=client,
                    read_session=lease,
                    instrument_ids=(settings.okx_demo_allowed_symbol_list[0],),
                    history_only=True,
                )
                head = selectors._runtime_selection_database_head_v2(
                    selection, history_only=True
                )
                _need(head in ("0056", "0057", "0058"))
                profile = selectors._selection_database_profile_0056(selection)
                _need(profiles.head(profile) == head)
                selected = selectors._record(selection)
                factories = selection.factories
                uid = selection.scope_config.expected_uid
                factory, engine, names, routes = database._factory_pins(factories)
                _need(factory is service.repository.session_factory)
                async with (
                    factory() as session,
                    session.begin(),
                    database._verified_terminal_writer(
                        session,
                        engine,
                        selection.source_roles_0056,
                        names,
                        routes,
                        **profiles.arguments(profile),
                    ),
                ):
                    raw = dict(
                        (await session.execute(_QUERY, {"uid": uid, "limit": limit}))
                        .mappings()
                        .one()
                    )
                    _need(selectors._record(selection) is selected)
                    result = _readout(raw, uid=uid, head=head, limit=limit)
                _need(selectors._record(selection) is selected)
        finally:
            if selection is not None:
                await caller._dispose_selection(selection, factories)
    _NATIVE_GUARD()
    return result


_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    ("read_native_journal_for_service_v1",),
    dependencies=(database, caller, selectors, profiles),
)
