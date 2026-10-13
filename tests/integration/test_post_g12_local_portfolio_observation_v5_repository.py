"""Actual PostgreSQL local checkpoint reads after a synthetic V4 boundary.

No OKX transport or order path is installed by this integration test. V4's
source chain is separately covered by its own unit/integration acceptance.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import decode
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import post_g12_local_portfolio_observation_v5 as bridge
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated migrated PostgreSQL fixture
)
from tests.unit.research.test_post_g12_local_portfolio_observation_v5 import (
    _roots,
    _v4,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_v5_real_uid_locked_local_checkpoint_stays_diagnostic(
    database, tmp_path, monkeypatch
):
    scope = LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}",
        settlement_currency="USDT",
    )
    ledger = QualificationLedgerRepository(database[1], clock=lambda: datetime.now(UTC))
    await ledger.initialize_capture_scope(scope)
    roots = _roots(tmp_path)
    issued = datetime.now(UTC)
    v4, observation = _v4(
        scope,
        issued + timedelta(seconds=30),
        received=issued - timedelta(seconds=1),
    )
    calls = []

    async def synthetic_v4(*args, **kwargs):
        assert args[5] == roots[5] and kwargs["scope"] == scope
        calls.append("v4")
        prior._publish_receipt(
            roots[5],
            v4,
            expected_root_identity=bridge.source_runtime._native_recheck_root_identity(
                roots[5]
            ),
            observation=observation,
        )
        return v4

    original_read = ledger.read_portfolio_checkpoint

    async def actual_read(read_scope):
        calls.append("db_read")
        return await original_read(read_scope)

    monkeypatch.setattr(prior, "publish_capture_inspect_history_event_v4", synthetic_v4)
    monkeypatch.setattr(ledger, "read_portfolio_checkpoint", actual_read)
    result = await bridge.publish_capture_inspect_local_portfolio_v5(
        *roots,
        object(),
        run=object(),
        original_inputs=object(),
        market_policy=object(),
        account_session=object(),
        session_factory=database[1],
        history_capture_id="h" * 32,
        ledger=ledger,
        scope=scope,
    )
    record = decode(result.receipt_json)
    assert calls == ["v4", "db_read", "db_read"]
    assert record["v4_receipt_sha256"] == v4.receipt_sha256
    assert record["first_checkpoint_sha256"] == record["second_checkpoint_sha256"]
    assert record["code"] == "local_state_blocked"
    assert record["local_flat_at_reads"] is False
    assert record["local_blocking_reasons"]
    assert result.admission == "DENY" and result.execution_authority is False
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
