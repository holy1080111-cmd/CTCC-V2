"""Isolated PostgreSQL DB0017 read after synthetic V3/B1 readback.

Requires an explicit migrated DATABASE_URL. V3 and B1 readback are synthetic;
the event observation is a real, read-only DB0017 transaction. No OKX IO.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.database.repositories.account_capture_journal import (
    LockedAccountSourceJoinReadback,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.trade_qualification import account_capture
from app.trade_qualification import post_g12_account_event_observation_v4 as bridge
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated PostgreSQL fixture
)
from tests.unit.research import test_post_g12_account_event_observation_v4 as unit_v4
from tests.unit.research import test_post_g12_account_history_join_v3 as source_fixture
from tests.unit.research.test_post_g12_account_event_observation_v4 import (
    _locked_account_receipt,
    _publish_synthetic_artifacts,
    _receipt,
)
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


@pytest.fixture(scope="module")
def public_source_inputs_fixture():
    return source_fixture._source_inputs_fixture.__wrapped__()


@pytest.fixture(scope="module")
async def public_journal_fixture(public_source_inputs_fixture, tmp_path_factory):
    # The shared unit packet deliberately has funding older than this
    # candidate's original 30-second economics policy. Build a new immutable
    # synthetic journal with a genuinely fresher exchange funding timestamp.
    with pytest.MonkeyPatch.context() as patch:
        clock, directory, harness, publications = unit_v4.public_fixture.setup(
            patch, public_source_inputs_fixture[0]
        )
        unit_v4.public_fixture.fresh_funding_return(harness, clock)
        result = await unit_v4.public_fixture.invoke(
            tmp_path_factory.mktemp("v4-public-fresh-funding")
        )
        assert result.packet is not None and result.journal_sha256 is not None
        harness.assert_closed()
        assert not publications
        unit_v4.public_fixture.empty_registries()
        return result, dict(directory.content)


async def test_v4_real_uid_lock_observation_waits_after_fresh_v3_receipt(
    database, tmp_path, monkeypatch, unique_uid_source, public_journal_fixture
):
    source, values, run, account_session, scope = unique_uid_source
    expected = bridge._original_binding(
        source.market, run, values, account_session, scope, HISTORY_ID
    )
    public_stamp = decode(public_journal_fixture[1]["summary.json"])["completed"]
    public_completed = utc_from_ns(public_stamp["utc_ns"])
    public_started = joined._utc(
        decode(public_journal_fixture[0].packet.packet_json)["started_at"]
    )
    # The original candidate lives for five minutes, while the public packet's
    # measured freshness lease is much shorter. Keep the synthetic V3/DB
    # sequence immediately after the actual journal's completed clock pair.
    v3_observed = public_completed + timedelta(milliseconds=400)
    logical_now = public_completed + timedelta(milliseconds=700)
    validation_at = public_completed + timedelta(seconds=1)
    assert (
        source.evaluated_at
        < public_started
        < public_completed
        < v3_observed
        < logical_now
        < validation_at
        < expected["deadline"]
    )
    elapsed_ns = int((validation_at - public_completed).total_seconds() * 1_000_000_000)
    monkeypatch.setattr(
        bridge,
        "native_stamp",
        lambda: {
            "utc_ns": int(validation_at.timestamp()) * 1_000_000_000
            + validation_at.microsecond * 1_000,
            "monotonic_ns": public_stamp["monotonic_ns"] + elapsed_ns,
        },
    )
    ledger = QualificationLedgerRepository(database[1], clock=lambda: logical_now)
    await ledger.initialize_capture_scope(scope)
    join_root = tmp_path / "join"
    join_root.mkdir()
    (tmp_path / "g12").mkdir()
    (tmp_path / "recheck").mkdir()
    (tmp_path / "public").mkdir()
    (tmp_path / "account").mkdir()
    event_root = tmp_path / "event"
    event_root.mkdir()
    published = asyncio.Event()
    synthetic_receipt = []

    async def synthetic_v3(*args, **kwargs):
        assert args[4] == join_root
        assert kwargs["account_session"] is account_session
        times = {
            "publication_completed_at": source.evaluated_at,
            "public_first_request_started_at": public_started,
            "account_history_readback_started_at": public_completed
            + timedelta(milliseconds=50),
            "account_history_readback_completed_at": public_completed
            + timedelta(milliseconds=60),
            "account_history_replay_verified_at": public_completed
            + timedelta(milliseconds=70),
            "account_session_first_http_started_at": public_completed
            + timedelta(milliseconds=80),
            "public_only_recheck_observed_at": public_completed
            + timedelta(milliseconds=180),
            "observed_at": v3_observed,
        }
        receipt = _publish_synthetic_artifacts(
            tmp_path,
            expected,
            _receipt(
                expected, changed={name: at.isoformat() for name, at in times.items()}
            ),
            public_journal_fixture,
            account_session,
        )
        synthetic_receipt.append(receipt)
        account_session._used = True
        identity = joined.source_runtime._native_recheck_root_identity(join_root)
        joined._publish_receipt(join_root, receipt, expected_root_identity=identity)
        published.set()
        return receipt

    monkeypatch.setattr(
        joined, "publish_capture_public_account_history_v3", synthetic_v3
    )

    # This synthetic V3 journal uses independent test clock/source fixtures;
    # the unit late-G1 case checks the new time fence itself.
    def synthetic_final_g1(packet, frozen_pre, observed_at):
        assert packet.bundle_sha256 == public_journal_fixture[0].packet.bundle_sha256
        assert frozen_pre.evaluation_sha256 == run.evaluation_sha256
        assert observed_at < expected["deadline"]

    monkeypatch.setattr(joined.prior, "_require_final_original_g1", synthetic_final_g1)

    class SyntheticAccountRepository:
        def __init__(self, factory, *, clock):
            assert factory is database[1] and clock is ledger.clock

        async def read_locked_current_history_join(
            self,
            read_scope,
            *,
            history_capture_id,
            current_capture_id,
            expected_policy_sha256,
        ):
            assert read_scope == scope
            assert history_capture_id == HISTORY_ID
            assert current_capture_id == source_fixture.CURRENT_ID
            assert (
                expected_policy_sha256 == bridge.account_join.V7_ORDERED_POLICY_SHA256
            )
            return LockedAccountSourceJoinReadback(
                canonical(
                    _locked_account_receipt(tmp_path, expected, synthetic_receipt[0])
                )
            )

    monkeypatch.setattr(
        bridge, "AccountCaptureJournalRepository", SyntheticAccountRepository
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
                session_factory=database[1],
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
