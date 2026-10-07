"""Cold-start synthetic capture; no database, credentials or exchange requests."""

import asyncio
import hashlib
import json

import pytest

from app.database.repositories.qualification_ledger import (
    LedgerBootstrapCheckpoint,
    QualificationLedgerRepository,
)
from app.trade_qualification import account_runtime as runtime
from app.trade_qualification import reservations
from tests.unit.test_qualification_account_collector import SECRETS
from tests.unit.test_qualification_account_materializer import (
    expired_hold,
    ledger_evidence,
)
from tests.unit.test_qualification_account_runtime import setup as account_setup
from tests.unit.test_qualification_account_v4 import plan as v4_plan
from tests.unit.test_qualification_account_v4 import script as v4_script


def unknown_state():
    return ledger_evidence().state.model_copy(
        update={"account_revision": 0, "ledger_revision": 0, "claims_sha256": None}
    )


def setup(monkeypatch, *, states=None, v4=False, **options):
    session, harness, _, args = account_setup(
        monkeypatch, selected=v4_plan() if v4 else None, **options
    )
    if v4:
        harness.script = v4_script()
    states = [unknown_state(), unknown_state()] if states is None else states
    seen = []

    async def read(self, scope):
        state = states[len(seen)]
        value = LedgerBootstrapCheckpoint(
            state, harness.clock(), harness.clock(), reservations.digest(state)
        )
        seen.append(value)
        return value

    monkeypatch.setattr(QualificationLedgerRepository, "initialize_capture_scope", read)
    monkeypatch.setattr(
        QualificationLedgerRepository, "read_bootstrap_checkpoint", read
    )
    args.pop("inputs")
    args.pop("expected_inputs_sha256")
    return session, harness, seen, args


@pytest.mark.asyncio
@pytest.mark.parametrize("v4", [False, True])
async def test_cold_start_retains_unknown_without_fake_empty_claims(monkeypatch, v4):
    session, harness, seen, args = setup(monkeypatch, v4=v4)
    result = await session.collect_bootstrap(**args)
    receipt = json.loads(result.receipt_json)
    assert not result.account_complete and not result.execution_authority
    assert result.admission == "DENY"
    assert result.transport_provenance == "synthetic_transport"
    assert result.ledger_checkpoint.state == unknown_state()
    assert not result.ledger_checkpoint.account_initialized
    assert receipt["schema_version"] == "ctcc.demo_account_bootstrap_capture.v1"
    assert receipt["account_revision"] == receipt["ledger_revision"] == 0
    assert receipt["account_revision_published"] is False
    assert "account_claims_not_initialized" in result.blocking_reasons
    assert result.receipt_sha256 == hashlib.sha256(result.receipt_json).hexdigest()
    assert all(secret.encode() not in result.receipt_json for secret in SECRETS)
    assert result.packet.plan.expected_uid.encode() not in result.receipt_json
    assert len(seen) == 2 and harness.requests
    assert all(request.method == "GET" for request in harness.requests)
    assert all(
        request.headers["x-simulated-trading"] == "1" for request in harness.requests
    )
    harness.assert_closed()
    with pytest.raises(runtime.AccountRuntimeError, match="owned_ledger_checkpoint"):
        session._check_checkpoint(result.ledger_checkpoint, unknown_state().scope)
    count = len(harness.requests)
    with pytest.raises(
        runtime.AccountRuntimeError, match="account_session_already_used"
    ):
        await session.collect_bootstrap(**args)
    assert len(harness.requests) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("forge_local_pin", [False, True])
async def test_in_place_credential_change_denies_bootstrap_before_io(
    monkeypatch, forge_local_pin
):
    session, harness, seen, args = setup(monkeypatch)
    object.__setattr__(
        session._credentials, "passphrase", "synthetic-only-altered-passphrase"
    )
    if forge_local_pin:
        session._credential_pin = runtime.collector._credential_content_pin(
            session._credentials
        )
    with pytest.raises(runtime.AccountRuntimeError, match="account_runtime_invalid"):
        await session.collect_bootstrap(**args)
    assert session._used is True
    assert harness.requests == [] and seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["reserved", "consumed", "uncertain"])
async def test_existing_holds_remain_observable_and_never_resolved(monkeypatch, hold):
    state = ledger_evidence(active=(expired_hold(state=hold),)).state
    session, harness, _, args = setup(monkeypatch, states=[state, state])
    result = await session.collect_bootstrap(**args)
    assert result.ledger_checkpoint.account_initialized
    assert result.ledger_checkpoint.state.active == state.active
    assert "unresolved_local_holds" in result.blocking_reasons
    assert result.admission == "DENY" and not result.account_complete
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"ledger_revision": 1},
        {"claims_sha256": "a" * 64},
        {"account_revision": 1},
        {"active": (expired_hold(),)},
    ],
)
async def test_malformed_uninitialized_checkpoint_denies_before_io(monkeypatch, change):
    state = unknown_state().model_copy(update=change)
    session, harness, _, args = setup(monkeypatch, states=[state])
    with pytest.raises(
        runtime.AccountRuntimeError, match="bootstrap_checkpoint_invalid"
    ):
        await session.collect_bootstrap(**args)
    assert not harness.requests


@pytest.mark.asyncio
async def test_real_revision_change_during_capture_cannot_be_ignored(monkeypatch):
    session, harness, _, args = setup(
        monkeypatch, states=[unknown_state(), ledger_evidence().state]
    )
    with pytest.raises(runtime.AccountRuntimeError, match="ledger_revision_changed"):
        await session.collect_bootstrap(**args)
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["initialize_capture_scope", "read_bootstrap_checkpoint"]
)
async def test_database_uncertainty_never_returns_receipt_or_reuses_session(
    monkeypatch, stage
):
    session, harness, _, args = setup(monkeypatch)

    async def fail(self, scope):
        raise RuntimeError(SECRETS[0])

    monkeypatch.setattr(QualificationLedgerRepository, stage, fail)
    with pytest.raises(runtime.AccountRuntimeError) as caught:
        await session.collect_bootstrap(**args)
    assert all(secret not in str(caught.value) for secret in SECRETS)
    count = len(harness.requests)
    with pytest.raises(
        runtime.AccountRuntimeError, match="account_session_already_used"
    ):
        await session.collect_bootstrap(**args)
    assert len(harness.requests) == count


@pytest.mark.asyncio
async def test_cancelled_bootstrap_cannot_be_retried_on_same_session(monkeypatch):
    session, harness, _, args = setup(monkeypatch)

    async def cancel(self, scope):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        QualificationLedgerRepository, "initialize_capture_scope", cancel
    )
    with pytest.raises(asyncio.CancelledError):
        await session.collect_bootstrap(**args)
    with pytest.raises(
        runtime.AccountRuntimeError, match="account_session_already_used"
    ):
        await session.collect_bootstrap(**args)
    assert not harness.requests


@pytest.mark.asyncio
async def test_wrong_exact_uid_or_failed_transport_never_produces_bootstrap(
    monkeypatch,
):
    def changed(stream, index, data):
        if stream == "config_after":
            return [{**data[0], "uid": "999999"}]

    session, harness, _, args = setup(monkeypatch, change=changed)
    with pytest.raises(runtime.AccountRuntimeError):
        await session.collect_bootstrap(**args)
    harness.assert_closed()
