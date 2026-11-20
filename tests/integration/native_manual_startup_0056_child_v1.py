"""Isolated TLS PostgreSQL startup test; signed account identity is synthetic.

This child has no venue credentials. It is restricted to the disposable CTCC
test database/roles and runs genuine source/role checks and recovery SQL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import ssl
from pathlib import Path

from scripts import manifest

_SOURCE = Path(__file__).resolve().parents[2]
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HOST = re.compile(r"ctcc-final-[0-9a-f]{12}-postgres\Z", re.ASCII)
_DATABASE = re.compile(r"ctcc_native_obs56_[0-9a-f]{12}\Z", re.ASCII)
_ROLE = re.compile(r"ctcc_obs56_(writer|custody|frame)_[0-9a-f]{12}\Z", re.ASCII)
_SCOPE = "730037"
_STAGE = ["source"]


def _need(condition):
    if condition is not True:
        raise ValueError("native_manual_demo_pg_child_denied")


async def _check():
    expected = os.environ.get("CTCC_DEVVAL_EXPECTED_SOURCE_MANIFEST_SHA256")
    mode = os.environ.get("CTCC_MANUAL_STARTUP_CHILD_MODE", "positive")
    raw = (_SOURCE / "MANIFEST.sha256").read_bytes()
    _need(
        os.environ.get("CTCC_MANUAL_STARTUP_HERMETIC_CHILD") == "1"
        and mode in ("positive", "deny_roles")
        and type(expected) is str
        and _SHA.fullmatch(expected) is not None
        and hashlib.sha256(raw).hexdigest() == expected
        and manifest.read_manifest(_SOURCE / "MANIFEST.sha256")
        == manifest.build_manifest(_SOURCE)
    )
    _STAGE[0] = "settings"
    from sqlalchemy import text

    from app.config.settings import get_settings
    from app.core import native_account_restart_recovery_0056_v1 as account_stop
    from app.core import native_manual_demo_lifespan_0056_v1 as manual
    from app.core import native_role_witness_0056_v1 as witness
    from app.database import session as configured
    from app.database.repositories.demo_automation import (
        DemoAutomationRepository,
        DemoAutomationStateConflict,
    )
    from app.demo_automation import native_server_selectors_v2 as selectors
    from app.demo_automation.service import SafeDemoAutomation
    from app.domain.source_primitives import canonical
    from app.exchange.okx.private_rest import (
        DemoConfigSelectorObservationV2,
        OkxDemoPrivateRestClient,
    )
    from app.trade_qualification import demo_control
    from app.trade_qualification import (
        native_controlled_account_source_v6 as controlled,
    )

    settings = get_settings()
    manual.require_manual_demo_settings_0056_v1(settings)
    context = configured.native_pg_tls_context
    _need(
        settings is configured.settings
        and type(context) is ssl.SSLContext
        and context.check_hostname is True
        and context.verify_mode == ssl.CERT_REQUIRED
        and settings.okx_demo_allow_order_writes is False
        and settings.okx_demo_auto_execution is False
        and settings.okx_ws_enabled is False
        and settings.okx_demo_credentials_configured is True
        and settings.okx_live_credentials_configured is False
        and (
            settings.okx_demo_api_key.get_secret_value(),
            settings.okx_demo_api_secret.get_secret_value(),
            settings.okx_demo_api_passphrase.get_secret_value(),
        )
        == (
            "ctcc-hermetic-manual-key-only",
            "ctcc-hermetic-manual-secret-only",
            "ctcc-hermetic-manual-passphrase-only",
        )
    )
    selected = selectors.select_configured_native_demo_factories_v2()
    engines = (
        configured.engine,
        selected.custody_engine,
        selected.frame_reader_engine,
    )
    original_handoff = OkxDemoPrivateRestClient.account_config_selector_v2
    try:
        _need(
            selected.writer_factory is configured.AsyncSessionFactory
            and all(
                engine.url.password is None
                and not engine.url.query
                and _HOST.fullmatch(engine.url.host or "") is not None
                and _DATABASE.fullmatch(engine.url.database or "") is not None
                and _ROLE.fullmatch(engine.url.username or "") is not None
                for engine in engines
            )
            and len({engine.url.username for engine in engines}) == 3
            and len({(e.url.host, e.url.port, e.url.database) for e in engines}) == 1
        )
        scope = demo_control.ControlScope("demo", _SCOPE)
        events = []
        account_readbacks = []

        async def synthetic_signed_account_handoff(client, *, retain_network=False):
            # Only the signed GET's identity observation is synthetic. The
            # configured recovery wrapper, settings validator, original selector,
            # registry, account stop SQL, commit/readback and disposal stay real.
            _STAGE[0] = "synthetic_signed_get"
            _need(type(client) is OkxDemoPrivateRestClient and retain_network is False)
            client._active_read_session().verify_current()
            events.append("synthetic_signed_account_handoff")
            rows = (
                {
                    "uid": _SCOPE,
                    "mainUid": _SCOPE,
                    "fixture": "synthetic_identity_no_exchange",
                },
            )
            return DemoConfigSelectorObservationV2(
                data=rows,
                raw_body=canonical({"code": "0", "data": list(rows)}),
                request_signature=base64.b64encode(
                    hashlib.sha256(b"ctcc-hermetic-identity-fixture-only").digest()
                ).decode("ascii"),
            )

        _STAGE[0] = "runtime_head"
        head = await selectors._observe_runtime_database_head_v2(selected)
        _need(head == "0056" and not events)
        events.append("actual_runtime_head")
        OkxDemoPrivateRestClient.account_config_selector_v2 = (
            synthetic_signed_account_handoff
        )
        repository = DemoAutomationRepository(configured.AsyncSessionFactory)
        if mode == "deny_roles":
            automation = SafeDemoAutomation(settings=settings, repository=repository)
            _STAGE[0] = "manual_startup"
            try:
                await manual.start_manual_demo_0056_v1(
                    settings, configured.AsyncSessionFactory, automation
                )
            except manual.NativeManualDemoStartup0056Error:
                _need(
                    events == ["actual_runtime_head"] and automation._recovered is False
                )
                return {
                    "status": "EXPECTED_DENIAL",
                    "reason": "actual_role_drift_before_account_handoff_or_recovery",
                    "exchange_requests": 0,
                    "order_writes": 0,
                    "startup_admission": False,
                    "execution_authority": False,
                }
            _STAGE[0] = "unexpected_role_admission"
            _need(False)
        original_state = await repository.load_state()
        _need(original_state["_control_revision"] == 7)
        _need(original_state["_restart_latch_required"] is True)
        snapshots = []
        for expected_revision in (8, 9):
            # Each fresh real service starts unrecovered; no flag is patched.
            automation = SafeDemoAutomation(settings=settings, repository=repository)
            _need(automation._recovered is False)
            _STAGE[0] = "manual_startup"
            await manual.start_manual_demo_0056_v1(
                settings, configured.AsyncSessionFactory, automation
            )
            _STAGE[0] = "independent_readback"
            durable = await repository.load_state()
            status = await automation.status()
            roles = await witness.observe_restricted_native_roles_0056_witness_v1(
                selected, source=_SOURCE
            )
            account_readbacks.append(
                await account_stop._observe_stop(selected, roles, scope)
            )
            async with configured.AsyncSessionFactory() as reader, reader.begin():
                row = tuple(
                    (
                        await reader.execute(
                            text("""
                    SELECT control_revision, armed, emergency_stop,
                           restart_latch_required, pg_catalog.pg_current_xact_id()::text,
                           (SELECT ssl FROM pg_catalog.pg_stat_ssl
                            WHERE pid=pg_catalog.pg_backend_pid())
                    FROM public.demo_automation_state WHERE id=1
                """)
                        )
                    ).one()
                )
            _need(
                durable["_control_revision"] == expected_revision
                and durable["armed"] is False
                and durable["emergency_stop"] is True
                and durable["_restart_latch_required"] is True
                and status.recovered is True
                and status.armed is False
                and status.emergency_stop is True
                and row[:4] == (expected_revision, False, True, True)
                and type(row[4]) is str
                and row[5] is True
            )
            snapshots.append((durable, row[4]))
        _need(
            snapshots[0][1] != snapshots[1][1]
            and len(account_readbacks) == 2
            and account_readbacks[0].state.revision == 3
            and account_readbacks[1].state == account_readbacks[0].state
            and events
            == [
                "actual_runtime_head",
                "synthetic_signed_account_handoff",
                "synthetic_signed_account_handoff",
            ]
        )
        _STAGE[0] = "stale_cas"
        stale = {**snapshots[0][0], "_control_revision": 9}
        try:
            await repository.save_state(stale)
        except DemoAutomationStateConflict:
            pass
        else:
            _need(False)
        _need(await repository.load_state() == snapshots[1][0])

        _STAGE[0] = "source_only_recovery_refusal"
        from app.demo_automation.native_source_only_v1 import _dispose

        selection = source_factories = None
        client = OkxDemoPrivateRestClient(settings=settings)
        try:
            with client.pinned_read_session() as lease:
                selection = (
                    await selectors.select_configured_demo_source_identity_0056_v1(
                        private_client=client,
                        read_session=lease,
                        instrument_ids=("BTC-USDT-SWAP",),
                    )
                )
                source_factories = selection.factories
                _need(
                    selectors._record(selection)["source_roles_0056"]
                    is selection.source_roles_0056
                )
                try:
                    selectors._replay_configured_demo_recovery_identity_v2(
                        selection, key=controlled._SOURCE_KEY_V6
                    )
                except selectors.NativeDemoCallerReadinessErrorV2 as error:
                    _need(str(error) == "native_demo_runtime_selector_purpose_refused")
                else:
                    _need(False)
        finally:
            await _dispose(selection, source_factories)
        return {
            "schema": "ctcc.native_manual_demo.actual_pg_child.v1",
            "status": "PASS",
            "source_manifest_sha256": expected,
            "database_head": head,
            "actual_configured_tls_writer": True,
            "head_probe_preceded_account_handoff": True,
            "actual_source_and_role_checks": True,
            "genuine_recover_cas_revisions": [7, 8, 9],
            "independent_readback_transactions": 2,
            "account_arm_revoked_and_reread": True,
            "stale_shared_cas_refused": True,
            "signed_account_handoff": "synthetic_fixture_scope_no_exchange",
            "source_only_recovery_check": "synthetic_signed_selector_with_actual_role_verification_refused",
            "exchange_requests": 0,
            "order_writes": 0,
            "startup_admission": False,
            "execution_authority": False,
        }
    finally:
        OkxDemoPrivateRestClient.account_config_selector_v2 = original_handoff
        await selectors._dispose_failed_selection_factories_v2(selected)
        await configured.engine.dispose()


def main():
    try:
        result = asyncio.run(_check())
    except Exception:  # noqa: BLE001 -- report a fixed stage, never private DB values.
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "code": "native_manual_demo_pg_child_denied",
                    "stage": _STAGE[0],
                },
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
