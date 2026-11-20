"""Synthetic private DATA admission only; no runtime, DB, Arm or venue writes."""

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace as Data
from weakref import ref

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.exchange.okx import native_live_write_v1 as write
from app.okx_live import owned_qualification_v1 as q
from app.trade_qualification.native_continuation_guard_v1 import (
    NativeSourceClosureError,
    save_native_guard_v1,
)
from tests.unit.test_native_live_source_ledger_v1 import NOW, _control, _value


@asynccontextmanager
async def _scope(monkeypatch):
    """Real transaction objects with explicitly inert source/SQL admission."""
    calls = []
    engine = create_async_engine(
        "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:1/synthetic",
        poolclass=NullPool,
    )

    def no_database(*_args):
        raise AssertionError("actual_database_forbidden")

    event.listen(engine.sync_engine, "do_connect", no_database)
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    pins, runtime, invocation = object(), object(), object()
    value = _value()
    rid, raw, digest = q.exact_live_intent_document_v1(value)
    request = decode(raw, maximum=65536)["request_json"].encode("ascii")
    owner = object.__new__(q.OwnedLiveQualificationV1)
    session_owner = object.__new__(q.OwnedLiveOperatorSessionV1)
    source_owner = object.__new__(q.source.OwnedLiveSourceV1)
    public_owner = object.__new__(q.public.OwnedLivePublicSourceV1)
    time = {"now": NOW, "mono": 1}

    def stamp():
        time["mono"] += 1
        return ClockStamp(
            utc_ns=q.source._utc_ns(time["now"]),
            monotonic_ns=time["mono"],
        )

    def guard():
        calls.append("source-integrity")

    def lease_check():
        calls.append("credential-lease")

    cfg = {"writer_factory": factory, "writer_pins": pins}
    monkeypatch.setattr(q, "_NATIVE_GUARD", guard)
    monkeypatch.setattr(q, "get_settings", lambda: None)
    monkeypatch.setattr(q, "_settings_pin_v1", lambda _settings: b"{}")
    monkeypatch.setattr(q.runtime, "_replay_live_runtime_v1", lambda *_a, **_k: cfg)
    monkeypatch.setattr(q.source, "_configured", lambda _plan: None)
    monkeypatch.setattr(q, "_stamp", stamp)
    monkeypatch.setattr(write, "_stamp", stamp)
    control = _control(value)
    row = canonical(
        {
            name: item.isoformat()
            if hasattr(item, "isoformat")
            else str(item)
            if name.endswith("_cap")
            else item
            for name, item in control.items()
        }
    )
    state = {
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "closed": False,
        "runtime": runtime,
        "settings_json": b"{}",
        "source_owner": source_owner,
        "current_public_owner": public_owner,
        "last": stamp(),
        "active": True,
        "approval_used": True,
        "expiry_monotonic_ns": 1_000_000_000,
        "deadline": value.deadline,
        "invocation": invocation,
        "report_id": "synthetic-scope",
        "control": control,
        "control_readback": q.controls.LiveOperatorControlReadbackV1(row, sha(row)),
    }
    view = {
        "uid": value.account_uid,
        "main_uid": value.main_uid,
        "credential_session_sha256": value.credential_session_sha256,
        "source_revision_sha256": value.source_revision_sha256,
        "lease": Data(verify_current=lease_check),
        "plan": object(),
    }
    q.source._SOURCES[source_owner] = view
    q.public._OWNERS[public_owner] = {
        **{name: state[name] for name in ("task", "loop", "pid", "thread")},
        "params": Data(
            invocation=invocation,
            report_id=state["report_id"],
            instrument_id=value.instrument_id,
            stage="post_g12",
        ),
    }
    q._OWNED[session_owner] = state
    facts = q.LiveQualifiedExecutionFactsV1(
        value,
        b"{}",
        rid,
        request,
        sha(request),
        factory,
        pins,
        source_owner,
        public_owner,
        value.deadline,
        1_000_000_000,
        value.credential_session_sha256,
        value.account_uid,
        value.main_uid,
        value.arm_id,
    )
    qualification = {
        "session": session_owner,
        "facts": facts,
        "spent": True,
        "revoked": False,
    }
    q._QUALIFIED[owner] = qualification
    async with factory() as session, session.begin():
        gate = write._PhysicalLiveWriteGateV1(key=write._GATE_KEY)
        record = {
            "gate": ref(gate),
            "pid": state["pid"],
            "thread": state["thread"],
            "task": state["task"],
            "loop": state["loop"],
            "phase": "prepared",
            "session": session,
            "transaction": session.get_transaction(),
            "facts": facts,
            "owner": owner,
            "api": write._coordinator_api(),
            "legacy_fence_held": True,
            "consumed": True,
            "control": control,
            "preparation_qualification": qualification,
            "preparation_session": session_owner,
            "preparation_lineage": (rid, raw, digest, b"{}", request, sha(request)),
        }
        save_native_guard_v1(record, guard)
        record["preparation_prefix"] = write._capture_preparation_prefix_v2(q)
        write._GATES[gate] = record
        write._HELD[session] = record
        token = write._ACTIVE.set(gate)
        try:
            yield Data(
                gate=gate,
                record=record,
                state=state,
                view=view,
                cfg=cfg,
                qualification=qualification,
                time=time,
                calls=calls,
            )
        finally:
            write._ACTIVE.reset(token)
            write._HELD.pop(session, None)
            write._GATES.pop(gate, None)
            q._QUALIFIED.pop(owner, None)
            q._OWNED.pop(session_owner, None)
            q.source._SOURCES.pop(source_owner, None)
            q.public._OWNERS.pop(public_owner, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_preparation_is_data_only_and_cannot_authorize_plaintext_or_os_write(
    monkeypatch,
):
    async with _scope(monkeypatch) as scope:
        _assert_preparation_is_not_execution(scope)


def _assert_preparation_is_not_execution(scope):
    assert (
        write._require_preparation_scope_v2(
            scope.gate, key=write.transport._TRANSPORT_KEY
        )
        is scope.record
    )
    assert {"source-integrity", "credential-lease"}.issubset(scope.calls)
    # This declared DATA fixture has no G1--G12 original-frame proof. A valid
    # preparation scope therefore cannot authorize either actual write method.
    with pytest.raises((ValueError, TypeError)):
        scope.gate.ssl_write(object(), b"untrusted plaintext")
    with pytest.raises((ValueError, TypeError)):
        scope.gate.raw_write(object(), b"untrusted ciphertext")


def test_each_actual_write_method_requires_full_authority_before_native_io(monkeypatch):
    gate = write._PhysicalLiveWriteGateV1(key=write._GATE_KEY)
    calls = []

    class FullAuthorityRequired(Exception):
        pass

    def full(current, *, key):
        assert current is gate and key is write.transport._TRANSPORT_KEY
        calls.append("full")
        raise FullAuthorityRequired

    monkeypatch.setattr(write, "_require_physical_gate_v1", full)
    for method in (gate.ssl_write, gate.raw_write):
        before = len(calls)
        with pytest.raises(FullAuthorityRequired):
            method(object(), b"no native operation is authorized")
        assert len(calls) == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "revoked",
        "spent",
        "active",
        "inhibit",
        "source",
        "uid",
        "credential",
        "revision",
        "factory",
        "pins",
        "held",
        "consumed",
        "phase",
        "estop",
        "disarm",
        "expiry",
        "lineage",
        "guard",
        "public_owner",
        "transaction",
    ],
)
async def test_preparation_liveness_or_lineage_mutation_fails_closed(
    monkeypatch, mutation
):
    async with _scope(monkeypatch) as scope:
        _assert_mutation_denied(scope, monkeypatch, mutation)


def _assert_mutation_denied(scope, monkeypatch, mutation):
    if mutation in {"revoked", "spent"}:
        scope.qualification[mutation] = mutation == "revoked"
    elif mutation == "active":
        scope.state["active"] = False
    elif mutation == "inhibit":
        scope.state["inhibit_code"] = "synthetic-stop"
    elif mutation == "source":
        scope.state["source_owner"] = object()
    elif mutation in {"uid", "credential", "revision"}:
        key = {
            "uid": "uid",
            "credential": "credential_session_sha256",
            "revision": "source_revision_sha256",
        }[mutation]
        scope.view[key] = "changed"
    elif mutation in {"factory", "pins"}:
        scope.cfg["writer_" + mutation] = object()
    elif mutation == "held":
        write._HELD.pop(scope.record["session"])
    elif mutation == "consumed":
        scope.record["consumed"] = False
    elif mutation == "phase":
        scope.record["phase"] = "spent"
    elif mutation in {"estop", "disarm"}:
        scope.record["control"][
            "emergency_stop" if mutation == "estop" else "armed"
        ] = mutation == "estop"
    elif mutation == "expiry":
        scope.time["now"] = scope.record["facts"].deadline_utc + timedelta(seconds=1)
    elif mutation == "lineage":
        scope.record["preparation_lineage"] = ()
    elif mutation == "guard":

        def foreign():
            raise AssertionError("foreign source guard ran")

        monkeypatch.setattr(q, "_NATIVE_GUARD", foreign)
    elif mutation == "public_owner":
        q.public._OWNERS.pop(scope.record["facts"].public_owner)
    elif mutation == "transaction":
        scope.record["transaction"] = object()
    with pytest.raises((ValueError, KeyError)):
        write._require_preparation_scope_v2(
            scope.gate, key=write.transport._TRANSPORT_KEY
        )


@pytest.mark.asyncio
async def test_preparation_cannot_move_to_another_task(monkeypatch):
    async with _scope(monkeypatch) as scope:

        async def foreign_task():
            write._require_preparation_scope_v2(
                scope.gate, key=write.transport._TRANSPORT_KEY
            )

        with pytest.raises(write.NativeLiveWriteError):
            await asyncio.create_task(foreign_task())


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["_session", "_task", "_need", "_NATIVE_GUARD"])
async def test_changed_canonical_prefix_cannot_execute_foreign_callback(
    monkeypatch, name
):
    async with _scope(monkeypatch) as scope:
        called = []

        def foreign(*_args, **_kwargs):
            called.append(True)
            raise AssertionError("foreign canonical prefix ran")

        monkeypatch.setattr(q, name, foreign)
        with pytest.raises(ValueError):
            write._require_preparation_scope_v2(
                scope.gate, key=write.transport._TRANSPORT_KEY
            )
        assert not called


def test_genuine_guard_accepts_existing_api_cache_population_without_runtime_authority():
    cell = write._COORDINATOR_BINDINGS
    original = cell[0]
    before = (len(q._OWNED), len(q._QUALIFIED), len(write._GATES))
    try:
        cell[0] = None
        q._NATIVE_GUARD()
        api = write._coordinator_api()
        assert write._COORDINATOR_BINDINGS is cell and cell[0][0] is api
        q._NATIVE_GUARD()
        assert before == (len(q._OWNED), len(q._QUALIFIED), len(write._GATES))
    finally:
        cell[0] = original


@pytest.mark.parametrize("value", [[], [None, None], [object()], [({}, "x")]])
def test_changed_or_malformed_api_cache_refuses_without_callbacks(monkeypatch, value):
    monkeypatch.setattr(write, "_COORDINATOR_BINDINGS", value)
    with pytest.raises(write.NativeLiveWriteError):
        write._coordinator_api()


@pytest.mark.parametrize("mutation", ["foreign", "short", "shape", "digest", "calls"])
def test_original_cell_rejects_malformed_or_changed_cache_lineage(mutation):
    cell, invoked = write._COORDINATOR_BINDINGS, []
    previous = cell[0]

    class Foreign:
        def __iter__(self):
            invoked.append("iter")
            raise AssertionError("foreign cache callback")

        def __eq__(self, _other):
            invoked.append("eq")
            raise AssertionError("foreign cache callback")

    try:
        cell[0] = None
        api = write._coordinator_api()
        cached, digest = cell[0]
        if mutation == "foreign":
            cell[0] = Foreign()
        elif mutation == "short":
            cell[0] = ()
        elif mutation == "shape":
            cell[0] = (Foreign(), digest)
        elif mutation == "digest":
            cell[0] = (cached, Foreign())
        else:
            cell[0] = ((api[0], (Foreign(),) * 4, api[2], api[3]), digest)
        with pytest.raises(write.NativeLiveWriteError):
            write._coordinator_api()
        assert not invoked
    finally:
        cell[0] = previous


@pytest.mark.parametrize(
    "name",
    [
        "_require_preparation_scope_v2",
        "_require_held_gate_scope_v2",
        "_require_physical_gate_v1",
        "_require_api_integrity_v2",
    ],
)
def test_replaced_physical_helper_is_denied_by_genuine_source_guard(monkeypatch, name):
    q._NATIVE_GUARD()

    def foreign(*_args, **_kwargs):
        raise AssertionError("foreign physical helper ran")

    monkeypatch.setattr(write, name, foreign)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()


@pytest.mark.parametrize("name", ["ssl_write", "raw_write", "host", "tls_context"])
def test_inplace_gate_method_or_accessor_mutation_is_source_denied(monkeypatch, name):
    q._NATIVE_GUARD()
    member = vars(write._PhysicalLiveWriteGateV1)[name]
    function = member.fget if type(member) is property else member

    def foreign(*_args, **_kwargs):
        raise AssertionError("foreign gate method ran")

    monkeypatch.setattr(function, "__code__", foreign.__code__)
    with pytest.raises(NativeSourceClosureError):
        q._NATIVE_GUARD()
