"""Synthetic v6 B1/native proof replay; never account or execution acceptance."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_current_source_verifier as current_source
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_materializer as materializer
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_current_components as components
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import (
    account_observation_runtime,
    account_portfolio_runtime,
    account_runtime,
)
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_account_portfolio_runtime import prepare as portfolio_prepare
from tests.unit.test_qualification_account_algo_history_v6 import plan
from tests.unit.test_qualification_account_capture import UID
from tests.unit.test_qualification_account_collector import SECRETS, credentials
from tests.unit.test_qualification_account_materializer import (
    inputs as materialization_inputs,
)
from tests.unit.test_qualification_account_runtime import setup as account_setup


@pytest.mark.asyncio
async def test_v6_original_b1_native_proof_replays_only_bounded_queries(monkeypatch):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_v7=False, algo_v6=True, verify_baseline=False
    )
    reference = observed.source_reference(chain)
    packet = capture.verify_demo_account_packet(
        next(
            point.event.packet_payload for point in chain if point.event.packet_payload
        ),
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    assert type(packet.plan) is capture.AllProductDemoAccountCapturePlanV6
    assert "algo_history_epoch_coverage_unverified" in packet.incomplete_reasons
    assert proof.contract_for_plan(packet.plan) == (
        proof.ALGO_V6_SCHEMA,
        proof.ALGO_V6_POLICY_SHA256,
    )
    checked = replay(raw, files, chain, scope)
    assert type(checked) is proof.NativeAlgoHistoryV6ProofReplay
    assert checked.packet == packet
    assert checked.account_complete is False
    assert checked.flat_start_permission is False
    assert checked.source_authenticity_verified is False
    assert checked.snapshot is checked.owner is None
    assert checked.execution_authority is False
    assert checked.admission == "DENY"
    historical = json.loads(checked.history_query_receipt_json)
    assert historical["schema_version"] == "ctcc.observed_algo_history_query.v1"
    assert historical["policy_sha256"] == history.ALGO_V6_POLICY_SHA256
    assert len(historical["algo_history_queries"]) == 24
    assert historical["algo_history_terminal_partition_verified"] is False
    assert historical["algo_history_epoch_coverage_verified"] is False
    assert historical["historical_retention_verified"] is False
    assert historical["admission"] == "DENY"
    assert storage._readback_schema(json.loads(raw)) == (
        storage.ALGO_V6_READBACK_SCHEMA
    )

    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda root: (11, 22))

    @contextmanager
    def roots(root):
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    receipt_pin = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    reread, receipt_raw = storage.read_native_account_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(raw),
        expected_readback_sha256=receipt_pin,
    )
    assert type(reread) is proof.NativeAlgoHistoryV6ProofReplay
    assert reread.packet == packet
    assert json.loads(receipt_raw)["schema_version"] == storage.ALGO_V6_READBACK_SCHEMA
    assert directory.content["proof.json"] == raw
    for old_policy in (
        current_source.POLICY_SHA256,
        current_source.V6_POLICY_SHA256,
        current_source.V7_POLICY_SHA256,
    ):
        with pytest.raises(current_source.CurrentAccountSourceError):
            current_source.verify_current_account_sources(
                chain,
                reference=reference,
                scope=scope,
                validated_at=packet.completed_at,
                expected_policy_sha256=old_policy,
            )
    with pytest.raises(
        materializer.AccountMaterializationError,
        match="algo_history_v6_portfolio_materialization_forbidden",
    ):
        materializer.materialize_demo_portfolio_snapshot(
            reread.packet,
            expected_plan_sha256=reference.plan_sha256,
            expected_packet_sha256=reference.packet_sha256,
            inputs=materialization_inputs(),
        )
    diagnostic_receipt = canonical(
        {
            "schema_version": "ctcc.initial_native_algo_history_diagnostic.v1",
            "policy_sha256": proof.ALGO_V6_POLICY_SHA256,
            "source_reference": observed.reference_document(reference),
            "proof_sha256": sha(raw),
            "proof_readback_sha256": receipt_pin,
            "history_query_receipt_sha256": sha(checked.history_query_receipt_json),
            "bounded_algo_history_chain_observed": True,
            "algo_history_terminal_partition_verified": False,
            "algo_history_epoch_coverage_verified": False,
            "native_sampled_hwm_verified": False,
            "observed_at": packet.completed_at.isoformat(),
            "expires_at": checked.expires_at.isoformat(),
            "snapshot": None,
            "account_complete": False,
            "account_revision_published": False,
            "flat_start_permission": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    diagnostic = runtime.InitialNativeAccountDiagnostic(diagnostic_receipt)
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=packet.plan_sha256,
    )
    assert diagnostic.snapshot is diagnostic.owner is None
    assert diagnostic._origin_lease is diagnostic._raw_packet_lease is None
    assert json.loads(diagnostic.receipt_json)["account_revision_published"] is False
    with pytest.raises(runtime.NativeAccountRawPacketError):
        runtime._consume_native_demo_raw_packet(diagnostic, session)
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(diagnostic, session)
    with pytest.raises(components.NativeCurrentComponentsError):
        components.observe_native_demo_current_components(diagnostic, session)
    assert not runtime._CAPTURES and not boundary._BOUNDARIES
    assert UID.encode() in b"".join(
        point.event.event_json + (point.event.packet_payload or b"") for point in chain
    )
    for published in (
        raw,
        receipt_raw,
        checked.history_query_receipt_json,
        diagnostic.receipt_json,
    ):
        assert UID.encode() not in published
        assert all(secret.encode() not in published for secret in SECRETS)
    changed = json.loads(receipt_raw)
    changed["schema_version"] = storage.V7_FLAT_READBACK_SCHEMA
    directory.content["readback.json"] = canonical(changed)
    with pytest.raises(proof.NativeAccountProofError):
        storage.read_native_account_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(raw),
            expected_readback_sha256=sha(directory.content["readback.json"]),
        )
    assert directory.content["proof.json"] == raw


@pytest.mark.asyncio
async def test_v6_cross_version_relabel_or_mutated_b1_chain_denies(monkeypatch):
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, current_v7=False, algo_v6=True, verify_baseline=False
    )
    assert replay(raw, files, chain, scope).account_complete is False
    value = json.loads(raw)
    for schema, policy in (
        (proof.SCHEMA, proof.POLICY_SHA256),
        (proof.V3_SCHEMA, proof.V3_POLICY_SHA256),
        (proof.V7_FLAT_SCHEMA, proof.V7_FLAT_POLICY_SHA256),
        (proof.ALGO_V6_SCHEMA, proof.POLICY_SHA256),
    ):
        changed = {**value, "schema_version": schema, "policy_sha256": policy}
        with pytest.raises(proof.NativeAccountProofError):
            replay(canonical(changed), files, chain, scope)
    changed = {**value, "algo_history_epoch_coverage_verified": True}
    with pytest.raises(proof.NativeAccountProofError):
        replay(canonical(changed), files, chain, scope)
    with pytest.raises(proof.NativeAccountProofError):
        replay(raw, files, chain[:-1], scope)
    point = next(point for point in chain if point.event.raw_body is not None)
    object.__setattr__(point.event, "raw_body", b'{"code":"0","data":[]}')
    with pytest.raises(proof.NativeAccountProofError):
        replay(raw, files, chain, scope)


@pytest.mark.asyncio
async def test_v6_session_denies_legacy_materializer_before_network(monkeypatch):
    selected = plan()
    session, harness, _checks, arguments = account_setup(monkeypatch, selected=selected)
    assert session._used is False
    assert proof.current_use_contract_for_plan(selected) == (
        proof.ALGO_V6_SCHEMA,
        proof.ALGO_V6_POLICY_SHA256,
    )
    with pytest.raises(account_runtime.AccountRuntimeError):
        await session.collect_and_materialize(**arguments)
    assert session._used is True
    assert harness.requests == []


@pytest.mark.asyncio
async def test_v6_session_denies_older_b5_and_index_routes_before_io(monkeypatch):
    _old, harness, _checks, arguments, events, reads, appended = portfolio_prepare(
        monkeypatch
    )
    selected = plan()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    with pytest.raises(account_portfolio_runtime.components.PortfolioComponentError):
        await account_portfolio_runtime.collect_owned_portfolio_components(
            session, **arguments
        )
    assert not events and not reads and not appended and not harness.requests
    assert session._used is False
    with pytest.raises(observed.AccountObservationError):
        await account_observation_runtime.collect_observed_execution_window(
            session,
            **{
                name: arguments[name]
                for name in (
                    "repository",
                    "journal_repository",
                    "observation_repository",
                    "clock",
                    "barrier_completed_at",
                    "window",
                    "expected_revision",
                )
            },
        )
    assert not events and not reads and not appended and not harness.requests
    assert session._used is False


def test_v6_history_policy_and_session_pin_are_distinct_from_older_versions():
    selected = plan()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    assert session._used is False
    assert proof.ALGO_V6_SCHEMA not in {
        proof.SCHEMA,
        proof.V3_SCHEMA,
        proof.EXPOSED_V4_SCHEMA,
        proof.V7_FLAT_SCHEMA,
    }
    assert history.ALGO_V6_POLICY_SHA256 not in {
        history.POLICY_SHA256,
        history.V2_POLICY_SHA256,
    }
    with pytest.raises(history.HistoryQueryVerificationError):
        history.verify_algo_history_query_chain_v6(
            (),
            expected_head_sha256="0" * 64,
            expected_plan_sha256="0" * 64,
            expected_packet_sha256="0" * 64,
            expected_account_id=selected.expected_uid,
            expected_settlement_currency="USDT",
            expected_policy_sha256=history.V2_POLICY_SHA256,
        )


@pytest.mark.asyncio
async def test_v6_native_entry_failure_issues_only_deny_diagnostic(monkeypatch):
    selected = plan()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    monkeypatch.setattr(native.clock, "native_stamp", Samples())
    monkeypatch.setattr(runtime, "_configured_factory", lambda _factory: True)

    async def fail_before_source(*_args, **_kwargs):
        raise RuntimeError("synthetic source unavailable")

    monkeypatch.setattr(runtime, "_capture_initial_current", fail_before_source)
    diagnostic = await runtime.capture_initial_native_account(
        session, session_factory=object(), proof_root=Path.cwd()
    )
    value = json.loads(diagnostic.receipt_json)
    assert value["schema_version"] == "ctcc.initial_native_algo_history_diagnostic.v1"
    assert value["policy_sha256"] == proof.ALGO_V6_POLICY_SHA256
    assert value["code"] == "native_account_initial_denied"
    assert value["bounded_algo_history_chain_observed"] is False
    assert value["algo_history_epoch_coverage_verified"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["account_revision_published"] is False
    assert value["source_authenticity_verified"] is False
    assert value["admission"] == "DENY"
    assert diagnostic.owner is diagnostic.snapshot is None
    assert diagnostic._origin_lease is diagnostic._raw_packet_lease is None
    assert session._used is True
