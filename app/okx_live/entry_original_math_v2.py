"""One original worker's full pre-G12 replay, never a saved-receipt permit.

Only the actual running coordinator can mint/bind/consume this proof. Current
G1--G11 mathematics still runs in full; all original inputs and custody are
reopened before the fixed-candidate recheck. The proof has no public constructor.
"""

import asyncio
from sys import _getframe
from types import BuiltinFunctionType
from weakref import WeakKeyDictionary

from app.okx_live import entry_proof_inputs_v1 as inputs

_PROOFS = WeakKeyDictionary()
_SESSIONS = WeakKeyDictionary()


def _need(value):
    if value is not True:
        raise ValueError("native_live_original_math_proof_unavailable") from None


class _OriginalMathProofV2:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("source-owned original proof only")


def _q():
    from app.okx_live import owned_qualification_v1 as q

    return q


def _frame(session):
    q = _q()
    _need(type(_getframe) is BuiltinFunctionType and _getframe.__module__ == "sys")
    frame = _getframe(2)
    _need(
        frame.f_code is q._ENTRY_RUN_BODY_SOURCE_V1.__code__
        and frame.f_globals is vars(q)
        and frame.f_locals.get("session") is session
    )


def _record(session, *, phase):
    q = _q()
    proof = _SESSIONS.get(session)
    record = _PROOFS.get(proof)
    state = q._session(session, current=True)
    task = asyncio.current_task()
    _need(type(proof) is _OriginalMathProofV2 and type(record) is dict)
    _need(
        record["phase"] == phase
        and record["coordinator"] is q
        and record["session"] is session
        and record["task"] is task
        and not task.cancelling()
        and record["invocation"] is state["invocation"]
        and record["initial"] is state["initial_math"]
        and record["initial_public"] is state["initial_public_owner"]
    )
    return proof, record, state


def _digest(state, record):
    q = _q()
    actual = q.source._SOURCES[record["source"]]
    return inputs.exact_input_digest_v1(
        {
            "initial_math": state["initial_math"],
            "original_candidate_json": state["original_candidate_json"],
            "policy": state["policy"],
            "policy_sha256": state["policy_sha256"],
            "settings_json": state["settings_json"],
            "source_data": {name: actual[name] for name in record["identity_keys"]},
            "report_id": state["report_id"],
            "instrument_id": state["instrument_id"],
            "strategy": state["strategy"],
            "direction": state["direction"],
            "process_sha256": state["process_sha256"],
            "approval_sha256": state["approval_sha256"],
            "arm_id": state["arm_id"],
            "deadline": state["deadline"],
            "expiry_monotonic_ns": state["expiry_monotonic_ns"],
            "math_source_constants": q.entry_math_closure.original_entry_math_data_v1(),
        },
        invocation=state["invocation"],
    )


def _bank(proof, record):
    q = _q()
    _need(record.get("reader_active", False) is False)
    record["reader_active"] = True
    try:
        return q.public._recheck_owned_initial_entry_bytes_v1(
            record["initial_public"], proof, key=q._PUBLIC_SOURCE_KEY_V1
        )
    finally:
        record["reader_active"] = False


def _publication_digest(state):
    return inputs.exact_input_digest_v1(
        {
            "snapshot": state["snapshot"],
            "receipt": state["publication"].receipt,
            "publication_json": state["publication_json"],
            "barrier": state["publication"].publication_barrier,
            "snapshot_sha256": state["publication"].snapshot_sha256,
        },
        invocation=state["invocation"],
    )


def require_public_bank_scope_v2(proof, public_owner, *, key):
    q = _q()
    _need(key is q.public._ENTRY_BANK_KEY_V1)
    record = _PROOFS.get(proof)
    _need(type(proof) is _OriginalMathProofV2 and type(record) is dict)
    _need(
        record["coordinator"] is q
        and record["task"] is asyncio.current_task()
        and not record["task"].cancelling()
        and record["reader_active"] is True
        and record["phase"] in {"preparing", "prepared", "rechecking"}
        and public_owner is record["initial_public"]
        and _SESSIONS.get(record["session"]) is proof
    )


def prepare_original_math_v2(session, *, key):
    q = _q()
    _need(key is q._ENTRY_ORIGINAL_MATH_KEY_V2 and session not in _SESSIONS)
    _frame(session)
    state = q._session(session, current=True)
    original = state["initial_math"]
    _need(original.original is None and original.pre_evidence_complete)
    # Full original mathematics is recomputed BEFORE any G12 artifact exists.
    q.math.verify_live_qualification_math_v1(original)
    q.source._retained_source_identity_v1(state["source_owner"])
    proof = object.__new__(_OriginalMathProofV2)
    record = {
        "coordinator": q,
        "session": session,
        "task": asyncio.current_task(),
        "invocation": state["invocation"],
        "source": state["source_owner"],
        "lease": q.source._SOURCES[state["source_owner"]]["lease"],
        "initial": original,
        "initial_public": state["initial_public_owner"],
        "identity_keys": tuple(state["identity"]),
        "phase": "preparing",
    }
    _PROOFS[proof], _SESSIONS[session] = record, proof
    try:
        record["bank"] = _bank(proof, record)
        record["account_inventory"] = inputs.account_raw_inventory_v1(record["source"])
        record["input_sha256"] = _digest(state, record)
        q._NATIVE_GUARD()
        record["phase"] = "prepared"
    except BaseException:
        retire_original_math_v2(session, key=key)
        raise


def bind_original_publication_v2(session, *, key):
    q = _q()
    _need(key is q._ENTRY_ORIGINAL_MATH_KEY_V2)
    _frame(session)
    _, record, state = _record(session, phase="prepared")
    q._publication(state)
    record["publication_sha256"] = _publication_digest(state)
    record["phase"] = "published"


def recheck_original_math_v2(
    session, current_source, *, risk_inputs, consumed_event_keys, key
):
    q = _q()
    _need(key is q._ENTRY_ORIGINAL_MATH_KEY_V2)
    _frame(session)
    proof, record, state = _record(session, phase="published")
    record["phase"] = "rechecking"  # Burn before any validation or current math.
    try:
        q.source._retained_source_identity_v1(record["source"])
        current = q.source._SOURCES[state["source_owner"]]
        _need(current["previous_source_owner"] is record["source"])
        _need(current["lease"] is record["lease"])
        _need(_digest(state, record) == record["input_sha256"])
        _need(
            inputs.account_raw_inventory_v1(record["source"])
            == record["account_inventory"]
        )
        _need(_bank(proof, record) == record["bank"])
        q._publication(state)
        _need(record["publication_sha256"] == _publication_digest(state))
        q._NATIVE_GUARD()
        return q.math._recheck_verified_original_data_v2(
            current_source,
            original=record["initial"],
            consumed_event_keys=consumed_event_keys,
            publication_candidate_sha256=state["snapshot"].candidate_sha256,
            risk_inputs=risk_inputs,
        )
    finally:
        record["phase"] = "spent"


def retire_original_math_v2(session, *, key):
    _need(key is _q()._ENTRY_ORIGINAL_MATH_KEY_V2)
    proof = _SESSIONS.pop(session, None)
    if proof is not None:
        record = _PROOFS.pop(proof, None)
        if type(record) is dict:
            record["phase"] = "burned"
