"""Actual restricted TLS readout boundaries; no venue or full trade-case claim."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import native_ledger_preflight_0056_v1 as ledger
from app.core.native_role_identity_0056_v2 import (
    observe_restricted_role_identity_0056_v2,
)
from app.database.repositories import native_closed_reporting_0056_v1 as report
from app.database.repositories import native_terminal_report_resume_0056_v1 as resume
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import LedgerScope
from tests.integration import test_g12_restricted_writer_0057_pg as lower

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
_report57_lab = lower._isolated_0057_g12_lab


async def test_actual0057_terminal_and_ledger_readouts_keep_exact_profile_and_refuse_drift(
    _report57_lab,
):
    owner, engines, names = _report57_lab
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, *engines[1:])
    assert await report._profile_from_selected_head(selected) is profiles.DB0057
    expected = await observe_restricted_role_identity_0056_v2(
        selected, source=ROOT, profile=profiles.DB0057
    )
    roles = await report._require_matching_roles(selected, expected)
    assert roles.observation.database_head == "0057"
    route = report.locked._direct_tls_route(engines[0])
    async with (
        factories[0]() as session,
        session.begin(),
        report._verified_terminal_writer(
            session, engines[0], roles, names, route, profile=profiles.DB0057
        ) as (catalog, _),
    ):
        assert catalog.database_head == "0057"
        assert catalog.execution_authority is False

    owner_factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    scope = LedgerScope(
        environment="demo", account_id="730075", settlement_currency="USDT"
    )
    await DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).latch_stop(ControlScope(scope.environment, scope.account_id), command_id="a" * 64)
    await QualificationLedgerRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).initialize_capture_scope(scope)
    async with factories[0]() as session:
        tx = await session.begin()
        try:
            observed = await ledger.read_locked_native_ledger_preflight_0056_v1(
                selected, roles, session, scope
            )
            assert observed.database_head == "0057"
            assert observed.scope_state == "BOOTSTRAP"
            assert observed.account_complete is observed.execution_authority is False
        finally:
            await tx.rollback()

    async def counts():
        async with owner.connect() as connection:
            return tuple(
                (
                    await connection.execute(
                        text("""
                SELECT (SELECT count(*) FROM public.qualification_submission_outcomes),
                       (SELECT count(*) FROM public.qualification_report_spool),
                       (SELECT count(*) FROM public.qualification_report_projection_receipts)
            """)
                    )
                ).one()
            )

    before = await counts()
    assert before == (0, 0, 0)
    with pytest.raises(report.NativeTerminalReport0056Error):
        await report.observe_terminal_lineage_0056_v1(selected, spool_id="b" * 64)
    with pytest.raises(report.NativeTerminalReport0056Error):
        await resume._resume_native_terminal_report_0056_v1(
            selected, cleanup_id="c" * 64, expected_roles=expected
        )
    assert await counts() == before
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f'GRANT DELETE ON public.demo_automation_state TO "{names[0]}"'
        )
    try:
        with pytest.raises(report.NativeTerminalReport0056Error):
            await report._require_matching_roles(selected, expected)
        with pytest.raises(report.NativeTerminalReport0056Error):
            await report.observe_terminal_lineage_0056_v1(selected, spool_id="b" * 64)
        assert await counts() == before
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f'REVOKE DELETE ON public.demo_automation_state FROM "{names[0]}"'
            )
