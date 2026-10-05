"""Synthetic phase/TLS-labelled replay + memory durability; never native acceptance.

Original MockTransport B1 records are explicitly relabelled only to exercise
pure replay. This cannot pass the actual runtime's owned transport/owner joins.
No configured DB, credentials, OS probe, network or execution is used.
"""

import asyncio
import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.account_time_probe import AccountTimeProbeReceiptV1
from app.trade_qualification.reservations import LedgerScope
from tests.unit.research.test_public_clock_v2 import fixture as clock_fixture
from tests.unit.research.test_public_clock_v2 import resign
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_clock_boundary import private_context_fixture
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_account_ingestion_journal_contracts import setup
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_qualification_account_capture import NOW, UID, ms, wire
from tests.unit.test_qualification_account_collector import SECRETS, Stream, credentials
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_v4 import script as v5_script


def synthetic_host(samples):
    evidence = clock_fixture()
    sample = samples()
    evidence["sample"] = sample
    for kind in ("host_before", "status", "host_after"):
        evidence["diagnostic"][kind]["request_start"] = sample
        evidence["diagnostic"][kind]["completed"] = sample
    return resign(evidence)


def synthetic_time_probe(stage, state, plan, samples, phase):
    stamps = [samples() for _ in range(4)]
    raw = canonical(
        {
            "code": "0",
            "msg": "",
            "data": [{"ts": str(stamps[1]["utc_ns"] // 1_000_000)}],
        }
    )
    receipt = AccountTimeProbeReceiptV1(
        plan_sha256=plan.canonical_sha256(),
        endpoint="/api/v5/public/time",
        query=(),
        page_index=0,
        previous_page_sha256=None,
        cursor_type=None,
        first_row_identity=None,
        last_row_identity=None,
        timestamp_semantics="server_response_time",
        body_sha256=sha(raw),
        body_size=len(raw),
        canonical_body_sha256=sha(raw),
        request_start=stamps[0],
        headers_received=stamps[1],
        body_complete=stamps[2],
        validation_complete=stamps[3],
        tls_peer_sha256="c" * 64,
        tls_hostname="openapi.okx.com",
        tls_version="TLSv1.3",
        response_headers=(),
        transport_origin="owned_native_tls",
    )
    trace = runtime._TimeProbeTrace(stage, plan, phase)
    trace.begin_request(endpoint="/api/v5/public/time", query=(), stamp=stamps[0])
    trace.headers(
        stamp=stamps[1],
        status=200,
        headers=(),
        tls={
            "classification": "owned_native_tls",
            "peer_sha256": "c" * 64,
            "hostname": "openapi.okx.com",
            "version": "TLSv1.3",
        },
        truncated=False,
    )
    trace.chunk(raw, stamps[1])
    trace.body_complete(stamps[2])
    trace.finish_request(
        accepted=True,
        validation_complete=stamps[3],
        error_code="none",
        cleanup="closed",
    )
    trace.client_closed(True)
    trace.receipt_sha256 = receipt.canonical_sha256()
    return receipt.canonical_bytes(), raw, trace.encoded("durable_secret_checked")


def synthetic_tls_chain(chain):
    result, previous = [], None
    for point in chain:
        record = journal.checked_event(point.event)
        record["previous_sha256"] = previous
        data = record["data"]
        if "tls_provenance" in data and data["tls_provenance"] == "synthetic_transport":
            data["tls_provenance"] = "owned_signed_verified_tls"
            data["tls_certificate_sha256"] = "d" * 64
        event = journal._JournalEvent(
            journal._ISSUER,
            journal.canonical(record),
            point.event.raw_body,
            point.event.packet_payload,
        )
        result.append(replace(point, event=event))
        previous = journal.digest(event.event_json)
    return tuple(result)


def declared_native_source(case="fresh_flat", *, current_only=False):
    """New synthetic raw scenarios, fixed before any capture or phase stamp."""
    if case not in {"fresh_flat", "old_anchor_flat", "exposed"}:
        raise ValueError("unsupported_synthetic_native_source")
    pages = source_pages() if case == "exposed" else flat_pages()
    if case == "fresh_flat":
        # Original stage's first synthetic sample is NOW+1ms. This is a new
        # declared raw scenario; it never edits an existing source or receipt.
        pages["account_position_risk"] = [
            [
                {
                    **pages["account_position_risk"][0][0],
                    "ts": ms(NOW + timedelta(milliseconds=2)),
                }
            ]
        ]
    declared = v5_script(
        pages=pages,
        streams=capture.V6_CURRENT_STREAMS if current_only else None,
    )
    bodies = tuple(wire(data) for _, data in declared)
    identity = canonical(
        {
            "schema_version": "ctcc.synthetic_account_native_raw_scenario.v1",
            "case": case,
            "raw_page_sha256": [sha(body) for body in bodies],
            "source_clock": "predeclared_synthetic_only",
            "native_acceptance": False,
            "execution_authority": False,
        }
    )
    return declared, bodies, identity


async def companion_fixture(
    monkeypatch, *, source_case="fresh_flat", current_only=False
):
    session, harness, _, args, events = setup(monkeypatch, v4=True)
    if current_only:
        selected = current_plan()
        session = ControlledDemoAccountSession(
            credentials=credentials(session_binding_id=selected.session_binding_id),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
        )
    proof_schema, proof_policy_sha256 = proof.contract_for_plan(session._plan)
    declared, expected_bodies, _ = declared_native_source(
        source_case, current_only=current_only
    )
    harness.script = declared
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    monkeypatch.setattr(
        native.clock, "native_os_clock", lambda: synthetic_host(samples)
    )
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    with native._initial_stage(
        plan_sha256=session._pin, scope_sha256=proof.scope_sha256(scope)
    ) as stage:
        state = native._state(stage)
        files = {"host-before.json": native._host_observation(stage, "before")}
        plan = proof._exchange_plan(state["started"])
        files["exchange-plan.json"] = plan.canonical_bytes()
        (
            files["exchange-before.json"],
            files["exchange-before.raw"],
            files["exchange-before-trace.json"],
        ) = synthetic_time_probe(stage, state, plan, samples, "before")
        harness.clock = state["clock"]
        args["clock"] = state["clock"]
        args["repository"].clock = state["clock"]
        args["journal_repository"].clock = state["clock"]
        args["journal_repository"].ledger.clock = state["clock"]
        args["barrier_completed_at"] = utc_from_ns(state["started"]["utc_ns"])
        recorded, owner = await bootstrap._collect_recorded(
            session, **args, _native_observer=state["observer"]
        )
        assert (
            tuple(page.response_body for page in recorded.bootstrap.packet.observations)
            == expected_bodies
        )
        original = tuple(events)
        with pytest.raises(
            proof.NativeAccountProofError, match="original_tls_required"
        ):
            proof._source(original, scope, proof_schema=proof_schema)
        chain = synthetic_tls_chain(original)
        files["host-after.json"] = native._host_observation(stage, "after")
        (
            files["exchange-after.json"],
            files["exchange-after.raw"],
            files["exchange-after-trace.json"],
        ) = synthetic_time_probe(stage, state, plan, samples, "after")
        reference, _, records, joins = proof._source(
            chain, scope, proof_schema=proof_schema
        )
        first = next(
            item["stamp"]
            for item in state["witnesses"]
            if item["phase"] == "response_closed"
            and item["stream"] in current.CURRENT_STREAMS
        )
        value = {
            "schema_version": proof_schema,
            "policy_sha256": proof_policy_sha256,
            "scope_sha256": proof.scope_sha256(scope),
            "source_reference": observed.reference_document(reference),
            "journal_terminal_sequence": len(records),
            "journal_owner_sha256": owner.owner_sha,
            "local_checkpoint_sha256": owner.checkpoint,
            "stage": {
                "kind": "initial_account_only",
                "invocation_id": state["invocation_id"],
                "started": state["started"],
            },
            "witnesses": state["witnesses"],
            "source_joins": joins,
            "clock_file_sha256": {name: sha(files[name]) for name in proof.FILES},
            "finalization_witnesses": state["finalization_witnesses"],
            "source_readback_complete": native._sample(stage),
            "proof_persist_start": native._sample(stage),
            "expires_at": utc_from_ns(first["utc_ns"] + 30_000_000_000).isoformat(),
            "monotonic_deadline_ns": first["monotonic_ns"] + 30_000_000_000,
            "historical_hwm_clock_verified": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        raw = canonical(value)
        if source_case == "fresh_flat":
            # Every mutation/storage case first proves the unchanged full
            # baseline. A generic later rejection cannot hide a bad fixture.
            baseline = replay(raw, files, chain, scope)
            current_value = json.loads(baseline.current_source_receipt_json)
            assert current_value["blocking_reasons"] == []
            assert current_value["observed_flat"] is True
            assert not baseline.execution_authority and not baseline.account_complete
            assert value["historical_hwm_clock_verified"] is False
            assert not runtime._CAPTURES and not boundary._BOUNDARIES
    harness.assert_closed()
    return raw, files, chain, scope, samples


def replay(raw, files, chain, scope):
    return proof.replay_native_account_proof(
        raw, files=files, chain=chain, scope=scope, expected_proof_sha256=sha(raw)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [None, "changed_event", "changed_db_timestamp", "reversed_readback", "extended"],
)
async def test_post_companion_recheck_requires_fresh_exact_original_db_chain(
    monkeypatch, mutation
):
    # The synthetic source tests only the strict comparison. A production
    # repository read_chain opens a new transaction and exact-UID account lock.
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch)
    replay(raw, files, chain, scope)
    reference, packet, _, _ = proof._source(chain, scope)
    confirmed = tuple(
        replace(point, readback_at=point.readback_at + timedelta(milliseconds=1))
        for point in chain
    )
    if mutation == "changed_event":
        confirmed = (*confirmed[:-1], replace(confirmed[-1], event=chain[0].event))
    elif mutation == "changed_db_timestamp":
        confirmed = (
            *confirmed[:-1],
            replace(
                confirmed[-1],
                db_recorded_at=confirmed[-1].db_recorded_at + timedelta(microseconds=1),
            ),
        )
    elif mutation == "reversed_readback":
        confirmed = (
            *confirmed[:-1],
            replace(
                confirmed[-1],
                readback_at=chain[-1].readback_at - timedelta(microseconds=1),
            ),
        )
    elif mutation == "extended":
        confirmed = (*confirmed, confirmed[-1])
    repository = object.__new__(AccountCaptureJournalRepository)
    reads = []

    async def read_chain(received_scope, capture_id):
        reads.append((received_scope, capture_id))
        return confirmed

    repository.read_chain = read_chain
    call = runtime._recheck_original_db_chain(
        repository, scope, chain, reference, packet, proof_schema=proof.SCHEMA
    )
    if mutation is None:
        await call
    else:
        with pytest.raises(proof.NativeAccountProofError):
            await call
    assert reads == [(scope, reference.capture_id)]
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
async def test_v6_native_current_proof_has_independent_contract_and_no_authority(
    monkeypatch,
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_only=True
    )
    document = json.loads(raw)
    result = replay(raw, files, chain, scope)
    current_receipt = json.loads(result.current_source_receipt_json)
    assert document["schema_version"] == proof.V3_SCHEMA
    assert document["policy_sha256"] == proof.V3_POLICY_SHA256
    assert current_receipt["policy_sha256"] == current.V6_POLICY_SHA256
    assert current_receipt["history_query_verifier_sha256"] is None
    assert current_receipt["history_join_state"] == "separate_original_history_required"
    assert set(current_receipt["inventory_row_counts"]) == set(
        current.INVENTORY_STREAMS
    )
    assert result.packet.plan.capture_scope == "all_current_standard_products_v6"
    assert not result.account_complete and not result.execution_authority
    assert document["admission"] == "DENY"
    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda root: (11, 22))

    @contextmanager
    def roots(root):
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    pin = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    receipt = json.loads(directory.content["readback.json"])
    assert receipt["schema_version"] == storage.V3_READBACK_SCHEMA
    readback, saved_receipt = storage.read_native_account_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(raw),
        expected_readback_sha256=pin,
    )
    assert saved_receipt == directory.content["readback.json"]
    assert readback.proof_sha256 == sha(raw)
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
async def test_v6_native_proof_rejects_v2_relabel_missing_tail_and_readback_swap(
    monkeypatch,
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_only=True
    )
    document = json.loads(raw)
    for schema, policy, complete in (
        (proof.SCHEMA, proof.POLICY_SHA256, False),
        (proof.V3_SCHEMA, proof.POLICY_SHA256, False),
        (proof.V3_SCHEMA, proof.V3_POLICY_SHA256, True),
    ):
        changed = {
            **document,
            "schema_version": schema,
            "policy_sha256": policy,
            "account_complete": complete,
        }
        with pytest.raises(proof.NativeAccountProofError):
            replay(canonical(changed), files, chain, scope)
    with pytest.raises(proof.NativeAccountProofError):
        replay(raw, files, chain[:-1], scope)

    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda root: (11, 22))

    @contextmanager
    def roots(root):
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    storage._seal_companion(directory, raw, chain=chain, scope=scope)
    changed_receipt = json.loads(directory.content["readback.json"])
    changed_receipt["schema_version"] = storage.READBACK_SCHEMA
    directory.content["readback.json"] = canonical(changed_receipt)
    with pytest.raises(proof.NativeAccountProofError):
        storage.read_native_account_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(raw),
            expected_readback_sha256=sha(directory.content["readback.json"]),
        )
    assert directory.content["proof.json"] == raw
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


class ForeignMapping(dict):
    def __iter__(self):
        CALLBACKS.append("mapping_iter")
        raise AssertionError

    def __getitem__(self, key):
        CALLBACKS.append("mapping_item")
        raise AssertionError

    def values(self):
        CALLBACKS.append("mapping_values")
        raise AssertionError


class ForeignScope:
    @property
    def environment(self):
        CALLBACKS.append("scope_environment")
        raise AssertionError


class ForeignChain(tuple):
    def __getitem__(self, key):
        CALLBACKS.append("chain_item")
        raise AssertionError

    def __iter__(self):
        CALLBACKS.append("chain_iter")
        raise AssertionError


class ForeignEvent:
    @property
    def packet_payload(self):
        CALLBACKS.append("event_packet")
        raise AssertionError


CALLBACKS = []


@pytest.mark.parametrize("foreign", ["mapping", "scope", "chain"])
def test_foreign_replay_ingress_is_rejected_before_callbacks(foreign):
    CALLBACKS.clear()
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    with pytest.raises(proof.NativeAccountProofError):
        if foreign == "mapping":
            replay(b"{}", ForeignMapping(), (), scope)
        elif foreign == "scope":
            replay(b"{}", {}, (), ForeignScope())
        else:
            proof._source(ForeignChain(), scope)
    assert CALLBACKS == []


def test_scope_digest_rejects_foreign_scope_before_properties():
    CALLBACKS.clear()
    with pytest.raises(ValueError):
        proof.scope_sha256(ForeignScope())
    assert CALLBACKS == []


@pytest.mark.asyncio
async def test_whole_original_chain_is_revalidated_before_later_event_payload(
    monkeypatch,
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch)
    forged = object.__new__(journal.JournalReadback)
    object.__setattr__(forged, "event", ForeignEvent())
    object.__setattr__(forged, "db_recorded_at", chain[-1].db_recorded_at)
    object.__setattr__(forged, "readback_at", chain[-1].readback_at)
    CALLBACKS.clear()
    with pytest.raises(proof.NativeAccountProofError):
        replay(raw, files, (*chain[:-1], forged), scope)
    assert CALLBACKS == []
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "source_request_bool",
        "source_page_bool",
        "source_body_time_int",
        "phase_index_bool",
        "phase_request_bool",
        "phase_page_bool",
        "phase_utc_bool",
        "phase_mono_bool",
        "stage_utc_bool",
        "finalizer_mono_bool",
        "readback_utc_bool",
        "persist_mono_bool",
    ],
)
async def test_indices_and_native_timestamps_never_accept_bool_int_aliases(
    monkeypatch, mutation
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch)
    value = json.loads(raw)
    if mutation == "source_request_bool":
        assert value["source_joins"][1]["request_index"] == 1
        value["source_joins"][1]["request_index"] = True
    elif mutation == "source_page_bool":
        assert value["source_joins"][0]["page_index"] == 0
        value["source_joins"][0]["page_index"] = False
    elif mutation == "source_body_time_int":
        value["source_joins"][0]["body_exhausted_at"] = 0
    elif mutation in {"phase_index_bool", "phase_request_bool", "phase_page_bool"}:
        name = {
            "phase_index_bool": "index",
            "phase_request_bool": "request_index",
            "phase_page_bool": "page_index",
        }[mutation]
        assert value["witnesses"][0][name] == 0
        value["witnesses"][0][name] = False
    elif mutation in {"phase_utc_bool", "phase_mono_bool"}:
        name = "utc_ns" if mutation == "phase_utc_bool" else "monotonic_ns"
        value["witnesses"][0]["stamp"][name] = True
    elif mutation == "stage_utc_bool":
        value["stage"]["started"]["utc_ns"] = True
    elif mutation == "finalizer_mono_bool":
        value["finalization_witnesses"][0]["started"]["monotonic_ns"] = True
    elif mutation == "readback_utc_bool":
        value["source_readback_complete"]["utc_ns"] = True
    else:
        value["proof_persist_start"]["monotonic_ns"] = True
    with pytest.raises(proof.NativeAccountProofError):
        replay(canonical(value), files, chain, scope)
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
async def test_time_probe_stamp_is_exact_before_expiry_fields_are_read(monkeypatch):
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state = native._state(stage)
        state["clock_results"]["after"] = "accepted"
        state["current_deadline"] = samples()["monotonic_ns"] + 30_000_000_000
        state["current_expiry"] = NOW + timedelta(seconds=30)
        trace = runtime._TimeProbeTrace(
            stage, proof._exchange_plan(state["started"]), "after"
        )
        CALLBACKS.clear()
        with pytest.raises(proof.NativeAccountProofError, match="exact_clock_stamp"):
            trace.begin_request(
                endpoint="/api/v5/public/time", query=(), stamp=ForeignMapping()
            )
        assert CALLBACKS == [] and trace.events == []


@pytest.mark.asyncio
async def test_original_collector_phase_companion_replays_without_minting_owner(
    monkeypatch,
    record_property,
):
    _, _, raw_identity = declared_native_source()
    record_property("synthetic_raw_scenario", "fresh_flat")
    record_property("synthetic_raw_identity_sha256", sha(raw_identity))
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch)
    before = tuple(item.event for item in chain)
    result = replay(raw, files, chain, scope)
    assert (
        result.proof_sha256 == sha(raw)
        and not result.execution_authority
        and not result.account_complete
    )
    assert json.loads(result.current_source_receipt_json)["observed_flat"] is True
    assert tuple(item.event for item in chain) == before
    assert not runtime._CAPTURES and not boundary._BOUNDARIES
    assert UID.encode() not in raw and all(
        secret.encode() not in raw for secret in SECRETS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_case", ["old_anchor_flat", "exposed"])
async def test_original_stale_anchor_and_exposed_raw_scenarios_are_exact_denials(
    monkeypatch, record_property, source_case
):
    _, _, raw_identity = declared_native_source(source_case)
    record_property("synthetic_raw_scenario", source_case)
    record_property("synthetic_raw_identity_sha256", sha(raw_identity))
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, source_case=source_case
    )
    value = json.loads(raw)
    observed_source = current.verify_current_account_sources(
        chain,
        reference=observed.source_reference(chain),
        scope=scope,
        validated_at=utc_from_ns(value["proof_persist_start"]["utc_ns"]),
    )
    receipt = json.loads(observed_source.receipt_json)
    expected = {"account_anchor_predates_publication"}
    if source_case == "exposed":
        expected.add("current_exposure_requires_protection_and_local_join")
        assert receipt["inventory_row_counts"]["positions"] == 1
        assert receipt["inventory_row_counts"]["orders_pending"] == 1
        assert receipt["inventory_row_counts"]["algo_conditional"] == 1
    else:
        assert all(count == 0 for count in receipt["inventory_row_counts"].values())
    assert set(receipt["blocking_reasons"]) == expected
    assert receipt["observed_flat"] is False
    assert observed_source.account_complete is False
    assert observed_source.execution_authority is False
    assert observed_source.flat_start_permission is False
    assert value["historical_hwm_clock_verified"] is False
    with pytest.raises(
        proof.NativeAccountProofError, match="current_sources_incomplete_or_exposed"
    ):
        replay(raw, files, chain, scope)
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_phase",
        "late_phase",
        "reorder",
        "changed_utc",
        "changed_original",
        "wrong_page",
        "wrong_tls",
        "wrong_plan",
        "wrong_stage",
        "hwm_upgrade",
        "expiry_extend",
        "missing_finalizer",
        "finalizer_rebind",
    ],
)
async def test_rehashed_mutations_cannot_hide_clock_or_source_binding_failure(
    monkeypatch, mutation
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch)
    value = json.loads(raw)
    if mutation == "missing_phase":
        value["witnesses"].pop(2)
    elif mutation == "late_phase":
        value["witnesses"].append(copy.deepcopy(value["witnesses"][-1]))
    elif mutation == "reorder":
        value["witnesses"][1:3] = reversed(value["witnesses"][1:3])
    elif mutation == "changed_utc":
        value["witnesses"][0]["stamp"]["utc_ns"] += 1000
    elif mutation == "changed_original":
        value["witnesses"][0]["source_utc"] = (NOW + timedelta(days=1)).isoformat()
    elif mutation == "wrong_page":
        value["witnesses"][0]["request_index"] = 1
    elif mutation == "wrong_tls":
        value["source_joins"][0]["tls_peer_sha256"] = "0" * 64
    elif mutation == "wrong_plan":
        value["source_reference"]["plan_sha256"] = "0" * 64
    elif mutation == "wrong_stage":
        value["stage"]["kind"] = "post_publication"
    elif mutation == "hwm_upgrade":
        value["historical_hwm_clock_verified"] = True
    elif mutation == "expiry_extend":
        value["monotonic_deadline_ns"] += 1
    elif mutation == "missing_finalizer":
        value["finalization_witnesses"].pop()
    else:
        value["finalization_witnesses"][0]["invocation_binding_sha256"] = "0" * 64
    with pytest.raises(proof.NativeAccountProofError):
        replay(canonical(value), files, chain, scope)
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "raw",
        "future_server",
        "old_profile",
        "after_starts_early",
        "trace_phase_missing",
        "trace_scope",
        "trace_private_header",
        "trace_index_bool",
        "trace_request_bool",
        "trace_chunk_index_bool",
        "trace_stamp_bool",
    ],
)
async def test_changed_clock_files_rejected_even_when_file_pins_rehashed(
    monkeypatch, mutation
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch)
    value = json.loads(raw)
    if mutation == "missing":
        files.pop("exchange-after.raw")
    elif mutation == "extra":
        files["caller-proof.json"] = b"{}"
    elif mutation == "raw":
        files["exchange-after.raw"] += b" "
    elif mutation == "future_server":
        parsed = json.loads(files["exchange-after.raw"])
        parsed["data"][0]["ts"] = str(int(parsed["data"][0]["ts"]) + 60000)
        changed = canonical(parsed)
        data = json.loads(files["exchange-after.json"])
        data.update(
            body_sha256=sha(changed),
            canonical_body_sha256=sha(changed),
            body_size=len(changed),
        )
        files["exchange-after.raw"], files["exchange-after.json"] = (
            changed,
            canonical(data),
        )
    elif mutation.startswith("trace_"):
        trace = json.loads(files["exchange-after-trace.json"])
        if mutation == "trace_phase_missing":
            trace["events"].pop(-2)
        elif mutation == "trace_scope":
            trace["scope_sha256"] = "0" * 64
        elif mutation == "trace_index_bool":
            trace["events"][0]["index"] = False
        elif mutation == "trace_request_bool":
            trace["events"][0]["request_index"] = False
        elif mutation == "trace_chunk_index_bool":
            trace["events"][2]["data"]["chunk_index"] = False
        elif mutation == "trace_stamp_bool":
            trace["events"][0]["stamp"]["monotonic_ns"] = True
        else:
            trace["events"][1]["data"]["safe_headers"] = [
                ["ok-access-sign", "synthetic private header"]
            ]
        for index, event in enumerate(trace["events"]):
            event["previous_sha256"] = (
                None if index == 0 else sha(canonical(trace["events"][index - 1]))
            )
        files["exchange-after-trace.json"] = canonical(trace)
    else:
        host = json.loads(files["host-after.json"])
        evidence = host["observation"]
        if mutation == "old_profile":
            evidence["diagnostic"]["profile_id"] = "unsupported"
        else:
            early = value["stage"]["started"]
            evidence["sample"] = early
            for name in ("host_before", "status", "host_after"):
                evidence["diagnostic"][name]["request_start"] = early
                evidence["diagnostic"][name]["completed"] = early
        resign(evidence)
        files["host-after.json"] = canonical(host)
    value["clock_file_sha256"] = {name: sha(data) for name, data in files.items()}
    with pytest.raises(proof.NativeAccountProofError):
        replay(canonical(value), files, chain, scope)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [None, "late_publish", "changed_readback", "root_changed", "missing_receipt"],
)
async def test_no_clobber_and_separate_readback_never_delete_already_saved_proof(
    monkeypatch, failure
):
    raw, files, chain, scope, samples = await companion_fixture(monkeypatch)
    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda root: (11, 22))
    calls = []

    @contextmanager
    def roots(root):
        calls.append(root)
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    if failure == "late_publish":
        original = directory.publish

        def late(name, value):
            original(name, value)
            if name == "readback.json":
                raise OSError("synthetic late failure")

        monkeypatch.setattr(directory, "publish", late)
        with pytest.raises(OSError):
            storage._seal_companion(directory, raw, chain=chain, scope=scope)
        assert directory.content["proof.json"] == raw
        assert "readback.json" in directory.content
        assert not runtime._CAPTURES
        return
    pin = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    original_saved = directory.content["proof.json"]
    with pytest.raises(FileExistsError):
        directory.publish("proof.json", b"caller replacement")
    if failure == "changed_readback":
        directory.content["readback.json"] += b" "
    elif failure == "root_changed":
        monkeypatch.setattr(storage, "_root_identity", lambda root: (11, 23))
    elif failure == "missing_receipt":
        directory.content.pop("readback.json")
    arguments = {
        "chain": chain,
        "scope": scope,
        "expected_proof_sha256": sha(raw),
        "expected_readback_sha256": pin,
    }
    if failure is None:
        result, _ = storage.read_native_account_companion(Path.cwd(), **arguments)
        assert result.proof_sha256 == sha(raw)
    else:
        with pytest.raises(proof.NativeAccountProofError):
            storage.read_native_account_companion(Path.cwd(), **arguments)
    assert len(calls) == 1 and directory.content["proof.json"] == original_saved
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argument", ["clock", "barrier_completed_at", "native_handle", "proof_dto"]
)
async def test_native_public_entry_has_no_caller_clock_or_publication_attestation(
    monkeypatch, argument
):
    session, harness, _, _, _ = setup(monkeypatch)
    with pytest.raises(TypeError):
        await runtime.capture_initial_native_account(
            session,
            session_factory=None,
            proof_root=Path.cwd(),
            **{argument: lambda: NOW},
        )
    assert not harness.requests and not runtime._CAPTURES


def test_foreign_factory_class_or_missing_config_never_produces_source_authority():
    class ForeignSession(AsyncSession):
        pass

    assert not runtime._configured_factory(async_sessionmaker(class_=ForeignSession))
    assert not runtime._configured_factory(async_sessionmaker())
    assert not runtime._configured_factory(
        async_sessionmaker(bind=object(), autoflush=False, expire_on_commit=False)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["tls_missing", "malformed_partial", "timeout", "escaped_secret"]
)
async def test_existing_fetch_failure_preserves_truthful_trace_and_safe_raw_only(
    monkeypatch, kind
):
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    monkeypatch.setattr(runtime.time_source, "native_stamp", samples)
    requests = []
    if kind == "malformed_partial":
        body = b'{"code":"0","data":'
    elif kind == "escaped_secret":
        secret = "".join(f"\\u{ord(char):04x}" for char in SECRETS[0])
        body = ('{"code":"0","note":"' + secret + '"}').encode()
    else:
        body = b'{"code":"0","msg":"","data":[{"ts":"1789207200000"}]}'

    def handler(request):
        requests.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("synthetic private error")
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=Stream(body)
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    monkeypatch.setattr(runtime.time_source, "_new_client", lambda: client)
    # Explicit hostile test seam: guard says native, original _fetch still
    # verifies real TLS and rejects this MockTransport's absent peer proof.
    monkeypatch.setattr(
        runtime.time_source, "_client_guard", lambda client, **kwargs: True
    )
    directory = MemoryDirectory({})
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state = native._state(stage)
        state["clock_results"]["before"] = "accepted"  # synthetic mechanism only
        plan = proof._exchange_plan(state["started"])
        with pytest.raises(proof.NativeAccountProofError, match="probe_rejected"):
            await runtime._time_probe(
                stage, plan, directory=directory, phase="before", tokens=SECRETS
            )
    assert len(requests) == 1 and requests[0].method == "GET" and client.is_closed
    retained = json.loads(directory.content["exchange-before-trace.json"])
    assert retained["complete"] is False and retained["execution_authority"] is False
    if kind == "tls_missing":
        assert directory.content["exchange-before.raw"] == body
        assert retained["raw_retention"] == "durable_secret_checked"
    else:
        assert "exchange-before.raw" not in directory.content
        assert (
            retained["raw_retention"]
            == {
                "timeout": "not_received",
                "malformed_partial": "withheld_unverifiable_partial",
                "escaped_secret": "withheld_secret",
            }[kind]
        )
    assert "exchange-before.json" not in directory.content
    assert all(
        secret.encode() not in directory.content["exchange-before-trace.json"]
        for secret in SECRETS
    )
    assert not runtime._CAPTURES and not boundary._BOUNDARIES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [None, "digest", "invocation", "foreign_task", "frozen_wall_expired_mono"],
)
async def test_current_only_carrier_burns_before_every_failed_or_successful_consume(
    monkeypatch, failure
):
    # Explicit mechanism fixture, never source acquisition/issuance.
    from app.trade_qualification import account_portfolio_runtime as legacy

    old, fence, invocation, pin, _ = private_context_fixture(monkeypatch)
    old_handoff = legacy._OWNERS.pop(old).handoff
    carrier = object.__new__(runtime._CapturedCurrentNativeAccount)
    runtime._CAPTURES[carrier] = runtime._CurrentNativeHandoff(
        None, None, old_handoff.receipt_json, fence, invocation
    )
    if failure == "frozen_wall_expired_mono":
        record = boundary._BOUNDARIES[fence]
        issue = json.loads(record.native_issue_stamp_json)
        monkeypatch.setattr(
            boundary,
            "native_stamp",
            lambda: {
                "utc_ns": issue["utc_ns"],
                "monotonic_ns": record.monotonic_deadline_ns,
            },
        )

    def consume():
        return runtime._consume_current_native_capture(
            carrier,
            invocation=object() if failure == "invocation" else invocation,
            expected_receipt_sha256="0" * 64 if failure == "digest" else pin,
        )

    if failure is None:
        assert consume().receipt_json == old_handoff.receipt_json
    else:
        with pytest.raises(proof.NativeAccountProofError):
            if failure == "foreign_task":

                async def foreign():
                    return consume()

                await asyncio.create_task(foreign())
            else:
                consume()
    assert carrier not in runtime._CAPTURES and fence not in boundary._BOUNDARIES
    with pytest.raises(proof.NativeAccountProofError, match="unavailable"):
        consume()
