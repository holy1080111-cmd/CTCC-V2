"""Isolated PostgreSQL UID-lock read after a synthetic V3 publication.

Requires an explicit migrated DATABASE_URL. The V3 publisher is synthetic;
the event observation is a real, read-only DB0017 transaction. No OKX IO.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture
from app.trade_qualification import post_g12_account_event_observation_v4 as bridge
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated PostgreSQL fixture
)
from tests.unit.research import test_post_g12_account_history_join_v3 as source_fixture
from tests.unit.research.test_post_g12_account_event_observation_v4 import _receipt
from tests.unit.research.test_post_g12_account_history_join_v3 import HISTORY_ID
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_qualification_account_collector import credentials

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _source_for_unique_uid():
    source, original, _ = source_fixture._source_inputs_fixture.__wrapped__()
    uid = f"123456789{uuid4().int % 10**12:012d}"
    risk = original["risk_inputs"]
    account = risk.account.model_copy(
        update={
            "account_id": uid,
            **{
                name: getattr(risk.account, name).model_copy(update={"account_id": uid})
                for name in (
                    "balance_stamp",
                    "positions_stamp",
                    "history_stamp",
                    "reservations_stamp",
                )
            },
        }
    )
    authority = risk.authority.model_copy(
        update={"stamp": risk.authority.stamp.model_copy(update={"account_id": uid})}
    )
    values = {
        **original,
        "risk_inputs": risk.model_copy(
            update={"account": account, "authority": authority}
        ),
    }
    run = evaluate_pre_evidence(source.market, **values)
    assert run.pre_evidence_complete
    plan = current_plan_v7(expected_uid=uid)
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=account_capture.plan_sha256(plan),
    )
    return (
        source,
        values,
        run,
        session,
        LedgerScope(account_id=uid, settlement_currency="USDT"),
    )


@pytest.fixture
def unique_uid_source():
    # Build the original-byte source before pytest starts the async test loop.
    return _source_for_unique_uid()


async def test_v4_real_uid_lock_observation_waits_after_fresh_v3_receipt(
    database, tmp_path, monkeypatch, unique_uid_source
):
    source, values, run, account_session, scope = unique_uid_source
    expected = bridge._original_binding(
        source.market, run, values, account_session, scope, HISTORY_ID
    )
    logical_now = expected["deadline"] - timedelta(milliseconds=500)
    ledger = QualificationLedgerRepository(database[1], clock=lambda: logical_now)
    await ledger.initialize_capture_scope(scope)
    join_root = tmp_path / "join"
    join_root.mkdir()
    event_root = tmp_path / "event"
    event_root.mkdir()
    published = asyncio.Event()

    async def synthetic_v3(*args, **kwargs):
        assert args[4] == join_root
        assert kwargs["account_session"] is account_session
        receipt = _receipt(expected)
        account_session._used = True
        identity = joined.source_runtime._native_recheck_root_identity(join_root)
        joined._publish_receipt(join_root, receipt, expected_root_identity=identity)
        published.set()
        return receipt

    monkeypatch.setattr(
        joined, "publish_capture_public_account_history_v3", synthetic_v3
    )
    async with database[1]() as writer, writer.begin():
        await ledger._locked(writer, scope)
        waiting = asyncio.create_task(
            bridge.publish_capture_inspect_history_event_v4(
                tmp_path / "g12",
                tmp_path / "public",
                tmp_path / "account",
                tmp_path / "recheck",
                join_root,
                event_root,
                source.market,
                run=run,
                original_inputs=values,
                market_policy=object(),
                account_session=account_session,
                session_factory=object(),
                history_capture_id=HISTORY_ID,
                ledger=ledger,
                scope=scope,
            )
        )
        await asyncio.wait_for(published.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not waiting.done()
        assert (join_root / "receipt.json").exists()
    result = await asyncio.wait_for(waiting, timeout=5)
    assert result.code == "trusted_execution_inputs_missing"
    assert result.ledger_event_state == "absent_at_read"
    assert result.ledger_observation_sha256 is not None
    assert result.v3_receipt_sha256 is not None
    assert result.admission == "DENY"
    assert result.atomic_risk_reserved is False
    assert result.durable_intent_created is False
    assert result.execution_authority is False
    assert (
        bridge.read_post_g12_account_event_observation_v4(
            event_root,
            expected_sha256=result.receipt_sha256,
            expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                event_root
            ),
        )
        == result
    )
