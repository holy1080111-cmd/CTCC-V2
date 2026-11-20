"""Actual restricted PostgreSQL recovery; the signed account scope is synthetic."""

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, text

from app.core import native_manual_demo_lifespan_0056_v1 as manual
from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as legacy
from app.trade_qualification import demo_control as domain
from scripts import current0056_fitted_catalog_measurement_v1 as measurement
from tests.controlled_writer_coverage_fixtures import _store_control_fixture
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _SOURCE_ROOT,
    _actual_0056_role_observation_lab,  # noqa: F401
    _delta56_identifier,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual0056_manual_startup_uses_configured_roles_and_durable_restart_cas(
    _actual_0056_role_observation_lab,  # noqa: F811
):
    owner, _, selected, _ = _actual_0056_role_observation_lab
    identity, source_files, source_files_raw = measurement._read_hermetic_inputs()
    source = measurement.source_identity(identity, source_files, source_files_raw)
    at = datetime.now(UTC)
    scope = domain.ControlScope("demo", "730037")
    acquired = domain.acquire(
        None,
        scope=scope,
        owner_sha256="1" * 64,
        pins=domain.ControlPins("2" * 64, "3" * 64, "4" * 64),
        now=at,
    )
    armed = domain.advance(
        acquired,
        owner_sha256=acquired.owner_sha256,
        owner_epoch=acquired.owner_epoch,
        expected_revision=acquired.revision,
        action="arm_requested",
        command_id="6" * 64,
        now=at,
        arm_expires_at=at + timedelta(seconds=27),
    )
    async with owner.begin() as connection:
        event = await _store_control_fixture(
            connection,
            acquired,
            command_id="5" * 64,
            action="acquire",
            previous_sha256=None,
        )
        await _store_control_fixture(
            connection,
            armed,
            command_id="6" * 64,
            action="arm_requested",
            previous_sha256=event,
        )
        stopped = legacy._stopped_values(at, "7" * 64)
        stopped["control_revision"] = 7
        stopped["lock_reasons"] = ["emergency_stop_engaged", "fixture_manual_restart"]
        await connection.execute(insert(DemoAutomationState).values(**stopped))
    urls = tuple(
        factory.kw["bind"].url
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    assert all(url.password is None and not url.query for url in urls)
    child_env = {
        **{name.upper(): "false" for name in manual._DISABLED},
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONPATH": str(_SOURCE_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ENVIRONMENT": "production",
        "TRADING_MODE": "okx_demo",
        "WEB_CONCURRENCY": "1",
        "API_TOKEN": hashlib.sha256(b"ctcc-hermetic-manual-startup-only").hexdigest(),
        "CTCC_NATIVE_DEMO_MANUAL_LIFESPAN_0056_V1": "true",
        "OKX_DEMO_ENABLED": "true",
        "OKX_DEMO_ALLOW_ORDER_WRITES": "false",
        "OKX_DEMO_AUTO_EXECUTION": "false",
        "OKX_DEMO_READ_MAX_RETRIES": "0",
        "OKX_DEMO_REST_BASE_URL": "https://openapi.okx.com",
        "OKX_DEMO_API_KEY": "ctcc-hermetic-manual-key-only",
        "OKX_DEMO_API_SECRET": "ctcc-hermetic-manual-secret-only",
        "OKX_DEMO_API_PASSPHRASE": "ctcc-hermetic-manual-passphrase-only",
        "OKX_DEMO_SCAN_SYMBOLS": "BTC-USDT-SWAP",
        "DATABASE_URL": urls[0].render_as_string(hide_password=False),
        "OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL": urls[1].render_as_string(
            hide_password=False
        ),
        "OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL": urls[2].render_as_string(
            hide_password=False
        ),
        "CTCC_NATIVE_PG_TLS_CA_FILE": os.environ[
            "CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"
        ],
        "CTCC_NATIVE_PG_TLS_CA_SHA256": os.environ[
            "CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"
        ],
        "CTCC_DEVVAL_EXPECTED_SOURCE_MANIFEST_SHA256": source["manifest_sha256"],
        "CTCC_MANUAL_STARTUP_HERMETIC_CHILD": "1",
    }

    async def child(**changes):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-m",
            "tests.integration.native_manual_startup_0056_child_v1",
            cwd=str(_SOURCE_ROOT),
            env={**child_env, **changes},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(240):
                stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert len(stdout) <= 4096 and stderr == b""
        return process.returncode, json.loads(stdout.strip())

    code, positive = await child()
    assert code == 0
    assert positive == {
        "schema": "ctcc.native_manual_demo.actual_pg_child.v1",
        "status": "PASS",
        "source_manifest_sha256": source["manifest_sha256"],
        "database_head": "0056",
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

    async def independent_state():
        async with owner.begin() as connection:
            shared = tuple(
                (
                    await connection.execute(
                        text("""
                SELECT control_revision, armed, emergency_stop, restart_latch_required
                FROM public.demo_automation_state WHERE id=1
            """)
                    )
                ).one()
            )
            account = tuple(
                (
                    await connection.execute(
                        text("""
                SELECT control_revision, emergency_stop, arm_request_id, arm_expires_at
                FROM public.demo_account_controls WHERE account_id='730037'
            """)
                    )
                ).one()
            )
        assert shared == (9, False, True, True)
        assert account == (3, True, None, None)
        return shared, account

    original = await independent_state()
    code, negative = await child(CTCC_DEVVAL_EXPECTED_SOURCE_MANIFEST_SHA256="0" * 64)
    assert code == 2
    assert negative == {
        "status": "FAIL",
        "code": "native_manual_demo_pg_child_denied",
        "stage": "source",
    }
    assert await independent_state() == original

    writer = _delta56_identifier(urls[0].username)
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f"GRANT DELETE ON public.demo_automation_state TO {writer}"
        )
    try:
        code, denied = await child(CTCC_MANUAL_STARTUP_CHILD_MODE="deny_roles")
        assert code == 0
        assert denied == {
            "status": "EXPECTED_DENIAL",
            "reason": "actual_role_drift_before_account_handoff_or_recovery",
            "exchange_requests": 0,
            "order_writes": 0,
            "startup_admission": False,
            "execution_authority": False,
        }
        assert await independent_state() == original
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"REVOKE DELETE ON public.demo_automation_state FROM {writer}"
            )
