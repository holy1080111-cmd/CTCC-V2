"""Synthetic V7/9549 account chronology; no authenticated read or order writes."""

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_native_clock as native_clock
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.unit.research.test_public_clock_v2 import fixture as clock_fixture
from tests.unit.research.test_public_clock_v2 import resign
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_native_proof import (
    companion_fixture,
    declared_native_source,
    synthetic_host,
    synthetic_time_probe,
    synthetic_tls_chain,
)
from tests.unit.test_qualification_account_capture import wire
from tests.unit.test_qualification_account_collector import credentials


def _updated_hosts(files):
    changed = dict(files)
    for phase in ("before", "after"):
        name = f"host-{phase}.json"
        host = json.loads(changed[name])
        old = host["observation"]
        updated = clock_fixture(profile_id=proof.public_clock.UPDATED_PROFILE_ID)
        updated["sample"] = old["sample"]
        for kind in ("host_before", "status", "host_after"):
            for edge in ("request_start", "completed"):
                updated["diagnostic"][kind][edge] = old["diagnostic"][kind][edge]
        host["observation"] = resign(updated)
        changed[name] = canonical(host)
    return changed


def _diagnostic_proof(raw, files, *, policy=None):
    value = json.loads(raw)
    value["schema_version"] = proof.V7_9549_DIAGNOSTIC_SCHEMA
    value["policy_sha256"] = policy or proof.V7_9549_DIAGNOSTIC_POLICY_SHA256
    value["clock_file_sha256"] = {name: sha(body) for name, body in files.items()}
    return canonical(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_case", ["fresh_flat", "fresh_exposed"])
async def test_complete_v7_chain_replays_and_separate_9549_readback_is_denied(
    monkeypatch, source_case
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch,
        source_case=source_case,
        current_v7=True,
        gateway_case="ordered",
        verify_baseline=False,
    )
    updated = _updated_hosts(files)
    selected = current_plan_v7()
    with pytest.raises(proof.NativeAccountProofError, match="clock_profile_invalid"):
        proof.contract_for_v7_9549_diagnostic_owned_before(
            selected, files["host-before.json"]
        )
    assert proof.contract_for_v7_9549_diagnostic_owned_before(
        selected, updated["host-before.json"]
    ) == (
        proof.V7_9549_DIAGNOSTIC_SCHEMA,
        proof.V7_9549_DIAGNOSTIC_POLICY_SHA256,
    )
    new_raw = _diagnostic_proof(raw, updated)
    replay = proof.replay_native_account_proof(
        new_raw,
        files=updated,
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(new_raw),
    )
    source = json.loads(replay.current_source_receipt_json)
    assert len(source["current_pages"]) >= 17
    assert set(source["inventory_row_counts"]) == set(
        proof.current.V7_INVENTORY_STREAMS
    )
    assert bool(sum(source["inventory_row_counts"].values())) == (
        source_case == "fresh_exposed"
    )
    assert replay.account_complete is replay.execution_authority is False
    assert json.loads(new_raw)["admission"] == "DENY"
    assert not runtime._CAPTURES and not runtime._ORIGIN_LEASES
    assert not runtime._RAW_PACKET_LEASES

    directory = MemoryDirectory(dict(updated))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))
    opens = []

    @contextmanager
    def roots(root):
        opens.append(root)
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    pin = storage._seal_companion(directory, new_raw, chain=chain, scope=scope)
    reread, receipt_raw = storage.read_native_account_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(new_raw),
        expected_readback_sha256=pin,
    )
    assert len(opens) == 1
    assert json.loads(receipt_raw)["schema_version"] == (
        storage.V7_9549_DIAGNOSTIC_READBACK_SCHEMA
    )
    assert reread.proof_sha256 == replay.proof_sha256
    with pytest.raises(proof.NativeAccountProofError):
        storage.read_native_account_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(new_raw),
            expected_readback_sha256="0" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_case", ["missing", "row_after_outtime"])
async def test_missing_gateway_or_future_row_denies_new_proof(
    monkeypatch, gateway_case
):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch,
        current_v7=True,
        gateway_case=gateway_case,
        verify_baseline=False,
    )
    updated = _updated_hosts(files)
    new_raw = _diagnostic_proof(raw, updated)
    with pytest.raises(proof.NativeAccountProofError):
        proof.replay_native_account_proof(
            new_raw,
            files=updated,
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(new_raw),
        )


@pytest.mark.asyncio
async def test_mixed_profile_wrong_policy_and_missing_owned_before_fail(monkeypatch):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch, current_v7=True, gateway_case="ordered"
    )
    updated = _updated_hosts(files)
    selected = current_plan_v7()
    with pytest.raises(proof.NativeAccountProofError):
        proof.contract_for_v7_9549_diagnostic_owned_before(selected, b"")
    with pytest.raises(proof.NativeAccountProofError):
        proof.contract_for_v7_9549_diagnostic_plan(object())
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_v7_flat_current_use_revoked",
    ):
        proof.current_use_contract_for_plan(selected)
    for changed, policy in (
        ({**updated, "host-after.json": files["host-after.json"]}, None),
        (updated, proof.V7_FLAT_POLICY_SHA256),
    ):
        new_raw = _diagnostic_proof(raw, changed, policy=policy)
        with pytest.raises(proof.NativeAccountProofError):
            proof.replay_native_account_proof(
                new_raw,
                files=changed,
                chain=chain,
                scope=scope,
                expected_proof_sha256=sha(new_raw),
            )


@pytest.mark.asyncio
async def test_old_host_profile_stops_owned_v7_diagnostic_before_any_http(
    monkeypatch,
):
    selected = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    samples = Samples()
    monkeypatch.setattr(native_clock.clock, "native_stamp", samples)
    monkeypatch.setattr(
        native_clock.clock, "native_os_clock", lambda: synthetic_host(samples)
    )
    directory = MemoryDirectory({})

    @contextmanager
    def attempt(_root):
        yield directory

    monkeypatch.setattr(storage, "_companion_attempt", attempt)
    calls = []

    async def never_http(*_args, **_kwargs):
        calls.append("http")
        raise AssertionError("account_or_public_http_started")

    monkeypatch.setattr(runtime, "_time_probe", never_http)
    monkeypatch.setattr(runtime.bootstrap, "_collect_recorded", never_http)
    scope = LedgerScope(
        account_id=selected.expected_uid,
        settlement_currency=selected.settlement_currency,
    )
    with (
        native_clock._claim_initial_session(session),
        native_clock._initial_stage(
            plan_sha256=session._pin,
            scope_sha256=proof.scope_sha256(scope),
            _claimed_session=session,
        ) as stage,
        pytest.raises(
            proof.NativeAccountProofError, match="native_account_clock_profile_invalid"
        ),
    ):
        await runtime._capture_initial_current(
            stage,
            session,
            object(),
            Path.cwd(),
            _v7_diagnostic_sink=[],
        )
    assert calls == []
    assert set(directory.names()) == {"host-before.json"}
    assert not runtime._CAPTURES and not runtime._ORIGIN_LEASES
    assert not runtime._RAW_PACKET_LEASES


@pytest.mark.asyncio
@pytest.mark.parametrize("source_case", ["fresh_flat", "fresh_exposed"])
async def test_owned_v7_9549_runtime_completes_synthetic_readback_without_carrier(
    monkeypatch, source_case
):
    """Run the actual diagnostic branch through owned stage and B1 collection.

    The memory test relabels MockTransport TLS metadata only after B1 closes;
    this tests control flow, not real TLS, PostgreSQL, or OKX authenticity.
    """
    holder = {}

    def gateway_response(_stream, _index, rows):
        request = holder["harness"].requests[-1]
        started = datetime.fromisoformat(request.headers["OK-ACCESS-TIMESTAMP"])
        delta = started - datetime(1970, 1, 1, tzinfo=UTC)
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return wire(rows, inTime=str(micros + 100), outTime=str(micros + 200))

    _old_session, harness, _checkpoints, _args, events = setup(
        monkeypatch, v4=True, change=gateway_response
    )
    holder["harness"] = harness
    plan = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    declared, _bodies, _identity = declared_native_source(source_case, current_v7=True)
    harness.script = declared
    samples = Samples()
    monkeypatch.setattr(native_clock.clock, "native_stamp", samples)
    monkeypatch.setattr(
        native_clock.clock,
        "native_os_clock",
        lambda: synthetic_host(
            samples, profile_id=proof.public_clock.UPDATED_PROFILE_ID
        ),
    )
    monkeypatch.setattr(runtime, "_configured_factory", lambda _factory: True)

    async def time_probe(stage, plan, *, directory, phase, tokens):
        state = native_clock._state(stage)
        outputs = synthetic_time_probe(stage, state, plan, samples, phase)
        for suffix, raw in zip(("json", "raw", "trace.json"), outputs, strict=True):
            name = f"exchange-{phase}.{suffix}"
            if suffix == "trace.json":
                name = f"exchange-{phase}-trace.json"
            storage._publish_part(directory, name, raw)
        return outputs

    monkeypatch.setattr(runtime, "_time_probe", time_probe)
    original_collect = bootstrap._collect_recorded

    async def collect(session, **kwargs):
        harness.clock = kwargs["clock"]
        recorded, owner = await original_collect(session, **kwargs)
        relabeled = synthetic_tls_chain(tuple(events))
        events[:] = relabeled
        owner.previous = observed.source_reference(relabeled).head_sha256
        return (
            replace(
                recorded,
                bootstrap=replace(
                    recorded.bootstrap,
                    transport_provenance="owned_signed_verified_tls",
                ),
            ),
            owner,
        )

    monkeypatch.setattr(runtime.bootstrap, "_collect_recorded", collect)
    content = {}
    opens = []

    @contextmanager
    def attempt(root):
        opens.append(("attempt", root))
        yield MemoryDirectory(content)

    @contextmanager
    def separate(root):
        opens.append(("readback", root))
        yield MemoryDirectory(content)

    monkeypatch.setattr(storage, "_companion_attempt", attempt)
    monkeypatch.setattr(storage, "_root_context", separate)
    monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))
    monkeypatch.setattr(storage, "native_stamp", samples)
    result = await runtime.capture_native_v7_9549_chronology_diagnostic(
        session, session_factory=object(), proof_root=Path.cwd()
    )
    value = json.loads(result.receipt_json)
    assert value.get("code") is None, value.get("code")
    assert value["schema_version"] == (
        "ctcc.initial_native_v7_chronology_diagnostic.w32tm_9549.v1"
    )
    assert value["native_companion_readback_verified"] is True
    assert value["page_count"] >= 17
    assert value["recorded_inventory_nonempty"] is (source_case == "fresh_exposed")
    assert value["gateway_causal_coverage_complete"] is True
    assert value["known_source_row_times_ordered"] is True
    assert value["current_flat_qualified"] is False
    assert value["account_atomic_revision_verified"] is False
    assert value["source_authenticity_verified"] is False
    assert value["snapshot"] is result.snapshot is result.owner is None
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == "DENY"
    assert result._origin_lease is result._raw_packet_lease is None
    assert len([kind for kind, _ in opens if kind == "readback"]) == 1
    assert set(content) == {*proof.FILES, "proof.json", "readback.json"}
    assert not runtime._CAPTURES and not runtime._ORIGIN_LEASES
    assert not runtime._RAW_PACKET_LEASES
    assert not boundary._BOUNDARIES
    harness.assert_closed()
