"""Actual PostgreSQL local checkpoint reads after a synthetic V4 boundary.

No OKX transport or order path is installed by this integration test. V4's
source chain is separately covered by its own unit/integration acceptance.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from itertools import count
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import post_g12_final_clock_proof_v1 as clock_proof
from app.trade_qualification import post_g12_local_portfolio_observation_v5 as bridge
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated migrated PostgreSQL fixture
)
from tests.unit.research import test_owned_public_runtime_v2 as public_fixture
from tests.unit.research import test_post_g12_local_portfolio_observation_v5 as unit_v5
from tests.unit.research.test_post_g12_local_portfolio_observation_v5 import (
    _roots,
    _v4,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def public_source_inputs_fixture():
    return unit_v5.source_inputs_fixture.__wrapped__()


@pytest.fixture(scope="module")
async def public_journal_fixture(public_source_inputs_fixture, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        clock, directory, harness, publications = public_fixture.setup(
            patch, public_source_inputs_fixture[0]
        )
        public_fixture.fresh_funding_return(harness, clock)
        result = await public_fixture.invoke(
            tmp_path_factory.mktemp("v5-public-fresh-funding")
        )
        assert result.packet is not None and result.journal_sha256 is not None
        harness.assert_closed()
        assert not publications
        public_fixture.empty_registries()
        return result, dict(directory.content)


async def test_v5_real_uid_locked_local_checkpoint_stays_diagnostic(
    database, tmp_path, monkeypatch, public_journal_fixture
):
    scope = LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}",
        settlement_currency="USDT",
    )
    public_stamp = decode(public_journal_fixture[1]["summary.json"])["completed"]
    public_completed = utc_from_ns(public_stamp["utc_ns"])
    received = public_completed + timedelta(milliseconds=200)
    validation_at = received + timedelta(seconds=1)

    def stamp_at(at):
        elapsed = at - public_completed
        elapsed_ns = (
            (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
        ) * 1_000
        return {
            "utc_ns": int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1_000,
            "monotonic_ns": public_stamp["monotonic_ns"] + elapsed_ns,
        }

    ledger = QualificationLedgerRepository(database[1], clock=lambda: public_completed)
    await ledger.initialize_capture_scope(scope)
    tick = count(1)
    ledger.clock = lambda: received + timedelta(milliseconds=10 * next(tick))
    roots = _roots(tmp_path)
    for name, payload in public_journal_fixture[1].items():
        (roots[1] / name).write_bytes(payload)
    v4, observation = _v4(
        scope,
        public_completed + timedelta(seconds=30),
        received=received,
        public_pins=public_journal_fixture,
    )
    v4 = replace(
        v4,
        receipt_schema_version=prior._CLOCK_SCHEMA,
        final_clock_proof_json=canonical(
            clock_proof.make_proof(
                public_stamp,
                stamp_at(received),
                v4.public_journal_sha256,
            )
        ),
    )
    monkeypatch.setattr(bridge, "native_stamp", lambda: stamp_at(validation_at))
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

    def frozen_binding(_pre, _inputs, _session, read_scope, _capture_id):
        assert read_scope == scope and not calls
        return {
            "account_scope_sha256": v4.scope_sha256,
            "candidate_sha256": v4.candidate_sha256,
            "original_event_key": v4.original_event_key,
            "report_id": v4.report_id,
            "account_plan_sha256": v4.account_plan_sha256,
            "deadline": datetime.fromisoformat(v4.candidate_deadline_at),
        }

    monkeypatch.setattr(prior, "publish_capture_inspect_history_event_v4", synthetic_v4)
    monkeypatch.setattr(ledger, "read_portfolio_checkpoint", actual_read)
    monkeypatch.setattr(
        prior.joined,
        "_original",
        lambda *_args: (
            object(),
            SimpleNamespace(policy=SimpleNamespace(economics=object())),
            object(),
        ),
    )
    monkeypatch.setattr(prior, "_checked_original_binding", frozen_binding)
    monkeypatch.setattr(
        prior.joined.prior, "_require_final_original_g1", lambda *_args: None
    )

    def current_context(packet, *, expected_bundle_sha256, evaluated_at):
        assert packet.bundle_sha256 == expected_bundle_sha256 == v4.public_packet_sha256
        assert evaluated_at == validation_at

    def quote_age(packet, _economics, evaluated_at):
        assert packet.bundle_sha256 == v4.public_packet_sha256
        assert evaluated_at == validation_at
        return True

    # This test targets the locked local checkpoint after earlier market gates.
    # The V4 source replay remains real; economics/profile behavior has separate
    # unit coverage and these stubs must not be cited as market-gate acceptance.
    monkeypatch.setattr(
        bridge.current_economics, "public_market_context_v2", current_context
    )
    monkeypatch.setattr(
        bridge.current_economics, "quote_within_economics_policy_age_v2", quote_age
    )
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
    assert record["public_journal_sha256"] == v4.public_journal_sha256
    assert (
        record["final_clock_proof"]["effective_cutoff_at"] == validation_at.isoformat()
    )
    assert record["code"] == "local_state_blocked"
    assert record["local_flat_at_reads"] is False
    assert record["local_blocking_reasons"]
    assert result.admission == "DENY" and result.execution_authority is False
    assert (roots[5] / "receipt.json").read_bytes() == v4.receipt_json
