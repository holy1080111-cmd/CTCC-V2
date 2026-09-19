"""Owned synthetic transport and DB reads; no network, real credentials or orders."""

import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.database.repositories.qualification_ledger import (
    LedgerCaptureCheckpoint,
    QualificationLedgerRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_runtime as module
from app.trade_qualification import reservations
from tests.unit.test_qualification_account_capture import BARRIER, NOW, plan
from tests.unit.test_qualification_account_collector import (
    SECRETS,
    Harness,
    credentials,
)
from tests.unit.test_qualification_account_materializer import (
    expired_hold,
    inputs,
    ledger_evidence,
    snapshot_inputs,
    source_pages,
)

REGISTRATION = b"synthetic registration evidence; not authenticated"


def regional(**changes):
    return capture.RegionalDemoAccountCapturePlan(
        **{
            **capture._plain(plan()),
            "registration_region": "global",
            "origin": "https://openapi.okx.com",
            "registration_evidence_sha256": hashlib.sha256(REGISTRATION).hexdigest(),
            **changes,
        }
    )


def bootstrap(selected=None, *, omit=(), **changes):
    selected = regional() if selected is None else selected
    artifacts = []
    for kind in module.BOOTSTRAP_KINDS:
        if kind in omit:
            continue
        raw = REGISTRATION if kind == "registration" else kind.encode()
        artifacts.append(
            module.BootstrapArtifact(
                kind=kind,
                raw_bytes=raw,
                artifact_sha256=hashlib.sha256(raw).hexdigest(),
                source_receipt_sha256="e" * 64,
                coverage_start=selected.history_start,
                coverage_end=selected.history_end,
                measured_receipt_at=NOW,
            )
        )
    evidence = module.AccountBootstrapEvidence(
        **{
            "account_id": selected.expected_uid,
            "main_uid": selected.expected_main_uid,
            "settlement_currency": selected.settlement_currency,
            "registration_region": selected.registration_region,
            "instrument_ids": selected.leverage_instrument_ids,
            "sealed_at": NOW,
            "artifacts": tuple(artifacts),
            **changes,
        }
    )
    raw = module.freeze_account_bootstrap(evidence)
    return raw, hashlib.sha256(raw).hexdigest()


def setup(monkeypatch, *, selected=None, states=None, **harness_options):
    selected = regional() if selected is None else selected
    harness = Harness(monkeypatch, pages=source_pages(), **harness_options)
    supplied = snapshot_inputs(ledger=None)
    session = module.ControlledDemoAccountSession(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    state = ledger_evidence().state
    states = [state, state] if states is None else states
    observed = []

    async def read(self, scope):
        state = states[len(observed)]
        value = LedgerCaptureCheckpoint(
            state=state,
            observed_at=harness.clock(),
            received_at=harness.clock(),
            state_sha256=reservations.digest(state),
        )
        observed.append(value)
        return value

    monkeypatch.setattr(QualificationLedgerRepository, "read_capture_checkpoint", read)
    repository = QualificationLedgerRepository(None, clock=harness.clock)
    arguments = {
        "repository": repository,
        "clock": harness.clock,
        "barrier_completed_at": BARRIER,
        "inputs": supplied,
        "expected_inputs_sha256": mapping.materialization_inputs_sha256(supplied),
    }
    return session, harness, observed, arguments


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "region,origin",
    [
        ("global", "https://openapi.okx.com"),
        ("us_au", "https://us.okx.com"),
        ("eea", "https://eea.okx.com"),
    ],
)
async def test_complete_synthetic_owned_capture_maps_real_db_revision_but_denies(
    monkeypatch, region, origin
):
    selected = regional(registration_region=region, origin=origin)
    session, harness, observed, arguments = setup(monkeypatch, selected=selected)
    raw, pin = bootstrap(selected)
    result = await session.collect_and_materialize(
        **arguments, bootstrap_payload=raw, expected_bootstrap_sha256=pin
    )
    assert len(observed) == 2
    assert all(
        str(request.url).startswith(origin + "/") for request in harness.requests
    )
    assert all(
        request.headers["x-simulated-trading"] == "1" for request in harness.requests
    )
    assert result.packet.schema_version == "ctcc.demo_account_capture.v3"
    assert (
        tuple(dict.fromkeys(o.request.stream for o in result.packet.observations))
        == capture.STREAMS
    )
    assert result.transport_provenance == "synthetic_transport"
    assert result.materialization_inputs.ledger.state == observed[0].state
    assert (
        result.materialization_inputs.ledger.source.observed_at
        == observed[0].observed_at
    )
    assert result.bootstrap_sha256 == pin
    assert result.admission == "DENY" and result.execution_authority is False
    assert "bootstrap_source_provenance_unverified" in result.blocking_reasons
    assert "continuous_peak_window_unverified" in result.blocking_reasons
    assert not any(
        "bootstrap_" in reason and reason.endswith("_missing")
        for reason in result.blocking_reasons
    )
    assert hashlib.sha256(result.receipt_json).hexdigest() == result.receipt_sha256
    assert all(secret not in result.receipt_json.decode() for secret in SECRETS)
    assert selected.expected_uid not in result.receipt_json.decode()
    harness.assert_closed()
    count = len(harness.requests)
    with pytest.raises(
        module.AccountRuntimeError, match="account_session_already_used"
    ):
        await session.collect_and_materialize(**arguments)
    assert count == len(harness.requests)


@pytest.mark.asyncio
async def test_missing_bootstrap_and_history_remain_unknown(monkeypatch):
    session, _, _, arguments = setup(monkeypatch)
    supplied = inputs()
    arguments.update(
        inputs=supplied,
        expected_inputs_sha256=mapping.materialization_inputs_sha256(supplied),
    )
    result = await session.collect_and_materialize(**arguments)
    assert all(
        "bootstrap_" + kind + "_missing" in result.blocking_reasons
        for kind in module.BOOTSTRAP_KINDS
    )
    assert result.materialization.snapshot is None
    assert result.materialization_inputs.history is None
    assert result.materialization_inputs.peak is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("account_revision", 4),
        ("ledger_revision", 9),
        ("claims_sha256", "a" * 64),
    ],
)
async def test_changed_db_revision_or_claims_denies_after_capture(
    monkeypatch, field, value
):
    state = ledger_evidence().state
    session, harness, _, arguments = setup(
        monkeypatch, states=[state, state.model_copy(update={field: value})]
    )
    with pytest.raises(
        module.AccountRuntimeError, match="ledger_revision_changed_during_capture"
    ):
        await session.collect_and_materialize(**arguments)
    assert harness.requests
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["reserved", "consumed", "uncertain"])
async def test_unresolved_holds_never_disappear_or_start_account_io(monkeypatch, state):
    active = ledger_evidence(active=(expired_hold(state=state),)).state
    session, harness, _, arguments = setup(monkeypatch, states=[active])
    with pytest.raises(module.AccountRuntimeError, match="unresolved_ledger_holds"):
        await session.collect_and_materialize(**arguments)
    assert harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    ["caller_ledger", "wrong_uid", "wrong_pin", "clock", "foreign_repository"],
)
async def test_early_denial_before_any_account_request(monkeypatch, problem):
    session, harness, _, arguments = setup(monkeypatch)
    if problem == "caller_ledger":
        arguments["inputs"] = snapshot_inputs()
    elif problem == "wrong_uid":
        arguments["inputs"] = inputs(account_id="99999")
    elif problem == "clock":
        arguments["clock"] = lambda: BARRIER
    elif problem == "foreign_repository":
        arguments["repository"] = object()
    arguments["expected_inputs_sha256"] = mapping.materialization_inputs_sha256(
        arguments["inputs"]
    )
    if problem == "wrong_pin":
        arguments["expected_inputs_sha256"] = "0" * 64
    with pytest.raises(module.AccountRuntimeError):
        await session.collect_and_materialize(**arguments)
    assert harness.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["config_before", "config_after"])
async def test_wrong_exact_uid_never_returns_partial_runtime_receipt(
    monkeypatch, stream
):
    def change(name, index, data):
        if name == stream:
            return [{**data[0], "uid": "99999"}]

    session, harness, _, arguments = setup(monkeypatch, change=change)
    with pytest.raises(module.AccountRuntimeError, match="account_runtime_invalid"):
        await session.collect_and_materialize(**arguments)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_partial_account_packet_or_replay_dto_cannot_replace_owned_capture(
    monkeypatch,
):
    session, harness, _, arguments = setup(monkeypatch)

    async def replay(**kwargs):
        return object()

    monkeypatch.setattr(collector, "_collect_owned_demo_account_records", replay)
    with pytest.raises(module.AccountRuntimeError, match="owned_capture_required"):
        await session.collect_and_materialize(**arguments)
    assert harness.requests == []


@pytest.mark.asyncio
async def test_transport_error_does_not_emit_partial_receipt_or_leak(monkeypatch):
    session, harness, _, arguments = setup(
        monkeypatch, handler_error=RuntimeError(SECRETS[0])
    )
    with pytest.raises(module.AccountRuntimeError) as caught:
        await session.collect_and_materialize(**arguments)
    assert all(secret not in str(caught.value) for secret in SECRETS)
    harness.assert_closed()


def test_region_is_explicit_matching_and_live_cannot_be_supplied():
    with pytest.raises(
        module.AccountRuntimeError, match="explicit_registration_region_required"
    ):
        module.ControlledDemoAccountSession(
            credentials=credentials(),
            plan=plan(),
            expected_plan_sha256=capture.plan_sha256(plan()),
        )
    with pytest.raises((ValidationError, capture.AccountCaptureError)):
        regional(origin="https://eea.okx.com")
    with pytest.raises((ValidationError, capture.AccountCaptureError)):
        regional(registration_region="unknown")
    forged = credentials()
    object.__setattr__(forged, "environment", "live")
    with pytest.raises(module.AccountRuntimeError, match="account_session_invalid"):
        module.ControlledDemoAccountSession(
            credentials=forged,
            plan=regional(),
            expected_plan_sha256=capture.plan_sha256(regional()),
        )


def test_bootstrap_pins_raw_bytes_and_never_accepts_caller_authority():
    raw, pin = bootstrap()
    checked = module.verify_account_bootstrap(raw, expected_sha256=pin)
    assert checked.missing_kinds == ()
    assert module.freeze_account_bootstrap(checked.evidence) == raw
    for change in ("hash", "raw", "complete", "live", "duplicate"):
        value = json.loads(raw)
        if change == "hash":
            value["artifacts"][0]["artifact_sha256"] = "f" * 64
        elif change == "raw":
            value["artifacts"][0]["raw_bytes"] = b"changed".hex()
        elif change == "complete":
            value["complete"] = True
        elif change == "live":
            value["environment"] = "live"
        else:
            value["artifacts"][1] = value["artifacts"][0]
        changed = capture._canonical(value).encode()
        with pytest.raises(module.AccountRuntimeError, match="bootstrap_invalid"):
            module.verify_account_bootstrap(
                changed, expected_sha256=hashlib.sha256(changed).hexdigest()
            )


@pytest.mark.asyncio
async def test_native_tls_configuration_without_actual_peer_evidence_is_denied(
    monkeypatch,
):
    session, harness, _, arguments = setup(monkeypatch)
    client = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
        follow_redirects=False,
    )
    calls = []

    async def send(request, **kwargs):
        from tests.unit.test_qualification_account_collector import Stream

        calls.append(request)
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "application/json"},
            stream=Stream(b'{"code":"0","data":[]}'),
        )

    # No DNS or socket calls: this response deliberately lacks a negotiated peer.
    monkeypatch.setattr(client, "send", send)
    monkeypatch.setattr(collector, "_new_client", lambda: client)
    with pytest.raises(module.AccountRuntimeError, match="account_runtime_invalid"):
        await session.collect_and_materialize(**arguments)
    assert len(calls) == 1 and client.is_closed
    assert harness.requests == []


@pytest.mark.asyncio
async def test_clock_regression_after_complete_capture_has_no_runtime_receipt(
    monkeypatch,
):
    session, harness, _, arguments = setup(monkeypatch)
    original = harness.clock

    def late_clock():
        if len(harness.requests) == len(harness.script) and all(
            client.is_closed for client in harness.clients
        ):
            return BARRIER
        return original()

    arguments["clock"] = late_clock
    with pytest.raises(module.AccountRuntimeError):
        await session.collect_and_materialize(**arguments)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_missing_bootstrap_kind_is_named_and_never_defaulted(monkeypatch):
    session, _, _, arguments = setup(monkeypatch)
    raw, pin = bootstrap(omit=("funding_accrual", "continuous_peak_window"))
    result = await session.collect_and_materialize(
        **arguments, bootstrap_payload=raw, expected_bootstrap_sha256=pin
    )
    assert "bootstrap_funding_accrual_missing" in result.blocking_reasons
    assert "bootstrap_continuous_peak_window_missing" in result.blocking_reasons
    assert result.admission == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["uid", "instrument", "future", "pin"])
async def test_bootstrap_mismatch_denies_before_io(monkeypatch, problem):
    session, harness, _, arguments = setup(monkeypatch)
    changes = {
        "uid": {"account_id": "99999"},
        "instrument": {"instrument_ids": ("ETH-USDT-SWAP",)},
        "future": {"sealed_at": NOW + timedelta(days=1)},
        "pin": {},
    }[problem]
    raw, pin = bootstrap(**changes)
    if problem == "pin":
        pin = "0" * 64
    with pytest.raises(module.AccountRuntimeError):
        await session.collect_and_materialize(
            **arguments, bootstrap_payload=raw, expected_bootstrap_sha256=pin
        )
    assert harness.requests == []
