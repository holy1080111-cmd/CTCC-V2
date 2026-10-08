"""Synthetic account proof exercises a DENY-only native-origin handoff.

The fixture labels mock TLS for proof-replay testing. It cannot authenticate an
OKX account, registration region, Demo public request, or order authority.
"""

import asyncio
import gc
import os
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace
from weakref import ref

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import demo_public_origin as demo_origin
from app.trade_qualification import public_source_runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_qualification_account_collector import credentials


async def prepared(monkeypatch):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_only=True
    )
    verified = replay(raw, files, chain, scope)
    reference, packet, _records, joins = proof._source(
        chain, scope, proof_schema=proof.V3_SCHEMA
    )
    plan = packet.plan
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    # Model the state after the native issuer has burned the session and its
    # collector has adopted the exact one-time claim. The independent test
    # below exercises the real claim and stage context managers.
    claim = {"bootstrap_used": True}
    session._used = True
    state = {
        "session": session,
        "session_claim": claim,
        "parent": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "closed": True,
    }
    monkeypatch.setattr(runtime.native, "_state", lambda _stage: state)
    real_claim = runtime.native._session_claim
    session_weak = ref(session)
    monkeypatch.setattr(
        runtime.native,
        "_session_claim",
        lambda candidate: (
            claim if candidate is session_weak() else real_claim(candidate)
        ),
    )
    monkeypatch.setattr(runtime.native.clock, "native_stamp", samples)
    issued = samples()
    receipt = canonical({"proof_sha256": sha(raw), "admission": "DENY"})
    arguments = {
        "receipt_json": receipt,
        "proof_sha256": sha(raw),
        "readback_sha256": "b" * 64,
        "issued": issued,
        "expires": verified.expires_at,
        "deadline": verified.monotonic_deadline_ns,
    }
    return session, packet, reference, joins, arguments


def mint(session, packet, reference, joins, arguments):
    return runtime._mint_demo_account_origin(
        object(), session, packet, reference, joins, **arguments
    )


@pytest.mark.asyncio
async def test_native_origin_is_one_use_same_task_and_never_authoritative(monkeypatch):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    session._used = True
    diagnostic = runtime.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    observed = runtime._consume_demo_account_origin(diagnostic, session)
    assert observed.private_origin == packet.plan.origin
    assert observed.tls_hostname == "openapi.okx.com"
    assert observed.simulated_trading_header == "1"
    assert observed.uid == packet.plan.expected_uid
    assert observed.main_uid == packet.plan.expected_main_uid
    assert observed.session_binding_id == packet.plan.session_binding_id
    assert (
        observed.claimed_registration_evidence_sha256
        == packet.plan.registration_evidence_sha256
    )
    assert observed.account_plan_sha256 == capture.plan_sha256(packet.plan)
    assert observed.account_packet_sha256 == reference.packet_sha256
    assert observed.native_proof_sha256 == arguments["proof_sha256"]
    assert observed.native_readback_sha256 == arguments["readback_sha256"]
    assert observed.signed_account_config_observed is True
    assert observed.registration_region_verified is False
    assert observed.public_source_authenticity_verified is False
    assert observed.execution_authority is False
    assert observed.admission == "DENY"
    route = demo_origin.reviewed_demo_public_route("global")
    with pytest.raises(
        public_source_runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        public_source_runtime._require_trusted_v2_demo_origin_profile(
            {
                "environment": "demo",
                "registration_region": "global",
                "rest_origin": observed.private_origin,
                "ws_origin": route.ws_origin,
                "native_account_origin": observed,
            }
        )
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(diagnostic, session)


@pytest.mark.asyncio
async def test_foreign_task_and_changed_receipt_burn_the_origin_lease(monkeypatch):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    changed_lease = mint(session, packet, reference, joins, arguments)
    session._used = True
    diagnostic = runtime.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    with pytest.raises(runtime.NativeAccountOriginError):
        await asyncio.create_task(
            asyncio.to_thread(runtime._consume_demo_account_origin, diagnostic, session)
        )
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(diagnostic, session)
    changed = runtime.InitialNativeAccountDiagnostic(b"changed-receipt", changed_lease)
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(changed, session)
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(
            runtime.InitialNativeAccountDiagnostic(
                arguments["receipt_json"], changed_lease
            ),
            session,
        )


@pytest.mark.asyncio
async def test_same_credential_object_mutation_burns_origin_lease(monkeypatch):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    diagnostic = runtime.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    original = session._credentials.passphrase
    object.__setattr__(
        session._credentials, "passphrase", "synthetic-only-altered-passphrase"
    )
    with pytest.raises(runtime.NativeAccountOriginError, match="origin_unavailable"):
        runtime._consume_demo_account_origin(diagnostic, session)
    object.__setattr__(session._credentials, "passphrase", original)
    with pytest.raises(runtime.NativeAccountOriginError, match="origin_unavailable"):
        runtime._consume_demo_account_origin(diagnostic, session)


@pytest.mark.asyncio
async def test_unread_or_conflicting_source_cannot_mint(monkeypatch):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(session, SimpleNamespace(plan=packet.plan), reference, joins, arguments)
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(session, packet, reference, joins[:-1], arguments)
    without_host = [dict(item) for item in joins]
    for item in without_host:
        item.pop("tls_hostname", None)
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(session, packet, reference, without_host, arguments)
    wrong_host = [dict(item) for item in joins]
    wrong_host[0]["tls_hostname"] = "us.okx.com"
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(session, packet, reference, wrong_host, arguments)
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(session, packet, reference, joins, {**arguments, "proof_sha256": "0"})
    session._plan = packet.plan.model_copy(update={"expected_uid": "wrong-uid"})
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(session, packet, reference, joins, arguments)


def _rechain_tls_host(chain, hostname):
    result, previous = [], None
    for point in chain:
        record = journal.checked_event(point.event)
        record["previous_sha256"] = previous
        if record["kind"] == "raw_finalized":
            if hostname is None:
                record["data"].pop("tls_hostname", None)
            else:
                record["data"]["tls_hostname"] = hostname
        event = journal._JournalEvent(
            journal._ISSUER,
            journal.canonical(record),
            point.event.raw_body,
            point.event.packet_payload,
        )
        result.append(replace(point, event=event))
        previous = journal.digest(event.event_json)
    return tuple(result)


@pytest.mark.asyncio
async def test_old_chain_replays_but_cannot_mint_without_tls_host(monkeypatch):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch, current_only=True
    )
    original = replay(raw, files, chain, scope)
    assert original.proof_sha256 == sha(raw)
    legacy_chain = _rechain_tls_host(chain, None)
    reference, packet, records, joins = proof._source(
        legacy_chain, scope, proof_schema=proof.V3_SCHEMA
    )
    assert len(records) > 0
    assert packet == original.packet
    assert all("tls_hostname" not in item for item in joins)
    # An old chain remains interpretable; it cannot gain this new observation.
    plan = packet.plan
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    state = {
        "session": session,
        "session_claim": {"bootstrap_used": True},
        "parent": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "closed": True,
    }
    monkeypatch.setattr(runtime.native, "_state", lambda _stage: state)
    session._used = True
    monkeypatch.setattr(
        runtime.native,
        "_session_claim",
        lambda candidate: (
            state["session_claim"]
            if candidate is session
            else pytest.fail("unrelated session claim")
        ),
    )
    issued = {
        "utc_ns": int(original.expires_at.timestamp() * 1_000_000_000) - 2_000_000_000,
        "monotonic_ns": original.monotonic_deadline_ns - 2_000_000_000,
    }
    with pytest.raises(runtime.NativeAccountOriginError):
        mint(
            session,
            packet,
            reference,
            joins,
            {
                "receipt_json": b"old-proof-remains-deny",
                "proof_sha256": sha(raw),
                "readback_sha256": "b" * 64,
                "issued": issued,
                "expires": original.expires_at,
                "deadline": original.monotonic_deadline_ns,
            },
        )
    # Mint denial is read-only: the separately retained proof and original
    # journal still replay at their original hashes.
    assert replay(raw, files, chain, scope).proof_sha256 == sha(raw)


@pytest.mark.asyncio
async def test_recorded_tls_host_conflict_is_rejected_before_observation(monkeypatch):
    _raw, _files, chain, scope, _samples = await companion_fixture(
        monkeypatch, current_only=True
    )
    conflicting = _rechain_tls_host(chain, "us.okx.com")
    with pytest.raises(
        proof.NativeAccountProofError, match="original_tls_hostname_invalid"
    ):
        proof._source(conflicting, scope, proof_schema=proof.V3_SCHEMA)


@pytest.mark.asyncio
async def test_garbage_collected_session_cannot_be_resurrected_by_receipt(monkeypatch):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    diagnostic = runtime.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    state = runtime.native._state(object())
    state["session"] = None
    old = ref(session)
    del session
    gc.collect()
    assert old() is None
    replacement = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=capture.plan_sha256(packet.plan),
    )
    replacement._used = True
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(diagnostic, replacement)


@pytest.mark.asyncio
async def test_finished_task_is_not_retained_by_unconsumed_lease(monkeypatch):
    async def issue_in_task():
        session, packet, reference, joins, arguments = await prepared(monkeypatch)
        lease = mint(session, packet, reference, joins, arguments)
        session._used = True
        return (
            runtime.InitialNativeAccountDiagnostic(arguments["receipt_json"], lease),
            session,
        )

    task = asyncio.create_task(issue_in_task())
    diagnostic, session = await task
    old_task = ref(task)
    # Drop the fixture's synthetic stage closure; only the lease registry may
    # refer to the finished task after this point.
    runtime.native._state(object())["parent"] = None
    monkeypatch.setattr(runtime.native, "_state", lambda _stage: None)
    del task
    await asyncio.sleep(0)
    gc.collect()
    assert old_task() is None
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(diagnostic, session)


@pytest.mark.asyncio
async def test_expiry_and_mutated_session_fail_closed(monkeypatch):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    second_lease = mint(session, packet, reference, joins, arguments)
    session._used = True
    diagnostic = runtime.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    monkeypatch.setattr(
        runtime.native.clock,
        "native_stamp",
        lambda: {
            "utc_ns": arguments["issued"]["utc_ns"] + 1_000_000,
            "monotonic_ns": arguments["deadline"],
        },
    )
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(diagnostic, session)
    monkeypatch.setattr(
        runtime.native.clock,
        "native_stamp",
        lambda: {
            "utc_ns": arguments["issued"]["utc_ns"] + 1_000_000,
            "monotonic_ns": arguments["issued"]["monotonic_ns"] + 1_000_000,
        },
    )
    session._credentials = credentials(
        session_binding_id=packet.plan.session_binding_id
    )
    with pytest.raises(runtime.NativeAccountOriginError):
        runtime._consume_demo_account_origin(
            runtime.InitialNativeAccountDiagnostic(
                arguments["receipt_json"], second_lease
            ),
            session,
        )
