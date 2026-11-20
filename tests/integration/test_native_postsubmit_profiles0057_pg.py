"""Actual isolated57 stopped DATA journal transactions, no venue access."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_database_profile_v1 as profiles
from tests.integration import test_g12_restricted_writer_0057_pg as lower
from tests.integration.test_native_postsubmit_db0056_pg import (
    _assert_stopped_postsubmit_transactions,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_postsubmit0057_lab = lower._isolated_0057_g12_lab


async def test_actual0057_stopped_postsubmit_retains_control_and_exact_journal(
    monkeypatch, _postsubmit0057_lab
):
    owner, engines, names = _postsubmit0057_lab
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, *engines[1:])
    await _assert_stopped_postsubmit_transactions(
        monkeypatch,
        (
            owner,
            async_sessionmaker(owner, autoflush=False, expire_on_commit=False),
            selected,
            names,
        ),
        profile=profiles.DB0057,
    )
