"""One invocation's verified mathematical inputs, never a reusable receipt.

Only the original coordinator frame can prepare/complete this private proof.
Native guards reopen raw custody and evidence, verify every actual model/input,
and freshly inspect quote/WS/clock boundaries. No saved DATA creates a seal.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from sys import _getframe
from types import BuiltinFunctionType
from weakref import WeakKeyDictionary

from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.okx_live import entry_proof_inputs_v1 as inputs

_PENDING = WeakKeyDictionary()
_SEALED = WeakKeyDictionary()
_OWNER_KEY = object()


def _need(value):
    if value is not True:
        raise ValueError("native_live_entry_exact_input_proof_unavailable") from None


class _OriginalEntryProofV1:
    __slots__ = ("__weakref__",)

    def __new__(cls, *, key=None):
        _need(cls is _OriginalEntryProofV1 and key is _OWNER_KEY)
        return object.__new__(cls)


_PROOFS = WeakKeyDictionary()


def _coordinator():
    from app.okx_live import owned_qualification_v1 as q

    return q


def _source_frame(session, *, value=None, owner=None):
    q = _coordinator()
    _need(type(_getframe) is BuiltinFunctionType and _getframe.__module__ == "sys")
    frame = _getframe(2)
    _need(
        frame.f_code is q._ENTRY_RUN_BODY_SOURCE_V1.__code__
        and frame.f_globals is vars(q)
    )
    _need(frame.f_locals.get("session") is session)
    if value is not None:
        _need(frame.f_locals.get("value") is value)
    if owner is not None:
        _need(frame.f_locals.get("qualified") is owner)


def _financial(state):
    q = _coordinator()
    owner = state["financial_owner"]
    _need(type(owner) is q.financial.OwnedLiveFinancialEpochV1)
    record = q.financial._OWNED.get(owner)
    facts = state["current_financial_facts"]
    _need(type(record) is dict and record["task"] is asyncio.current_task())
    _need(type(facts) is q.financial.LiveFinancialWindowFactsV1)
    _need(
        record["source"] is state["source_owner"]
        and record["sample_json"] == facts.receipt_json
    )
    return facts, record


def _document(state, value):
    facts, financial = _financial(state)
    return {
        "initial_math": state["initial_math"],
        "current_math": state["current_math"],
        "original_candidate_json": state["original_candidate_json"],
        "snapshot": state["snapshot"],
        "publication_receipt": state["publication"].receipt,
        "publication_json": state["publication_json"],
        "publication_barrier": state["publication"].publication_barrier,
        "publication_snapshot_sha256": state["publication"].snapshot_sha256,
        "policy": state["policy"],
        "policy_sha256": state["policy_sha256"],
        "settings_json": state["settings_json"],
        "identity": dict(state["identity"]),
        "actual_source_data": {
            name: _coordinator().source._SOURCES[state["source_owner"]][name]
            for name in state["identity"]
        },
        "current_instrument": state["current_instrument"],
        "current_local": state["current_local"],
        "financial_facts": facts,
        "original_financial_epoch": financial["epoch_json"],
        "original_financial_sample": financial["sample_json"],
        "control": state["control"],
        "control_readback": state["control_readback"],
        "caps": state["caps"],
        "arm_id": state["arm_id"],
        "approval_sha256": state["approval_sha256"],
        "process_sha256": state["process_sha256"],
        "deadline": state["deadline"],
        "expiry_monotonic_ns": state["expiry_monotonic_ns"],
        "instrument_id": state["instrument_id"],
        "strategy": state["strategy"],
        "direction": state["direction"],
        "report_id": state["report_id"],
        "intent": value,
        "math_source_constants": _coordinator().entry_math_closure.original_entry_math_data_v1(),
    }


def _digest(state, value):
    return inputs.exact_input_digest_v1(
        _document(state, value), invocation=state["invocation"]
    )


def _bindings(state):
    q = _coordinator()
    _, financial = _financial(state)
    return (
        state["runtime"],
        state["invocation"],
        state["source_owner"],
        state["financial_owner"],
        state["initial_public_owner"],
        state["current_public_owner"],
        financial["factory"],
        financial["pins"],
        q.source._SOURCES[state["source_owner"]]["lease"],
    )


def prepare_pending_entry_v1(session, *, key):
    """Called synchronously by original run immediately after its full R7."""
    q = _coordinator()
    _need(key is q._ENTRY_PROOF_KEY_V1 and session not in _PENDING)
    state = q._session(session, current=True)
    value = state["entry_native_intent"]
    _source_frame(session, value=value)
    initial, current = state["initial_math"], state["current_math"]
    _need(initial.pre_evidence_complete and current.pre_evidence_complete)
    _need(
        current.original is not None
        and current.original.receipt_json == initial.receipt_json
    )
    _need(current.source is state["current_public"])
    _need(
        current.source.invocation is state["invocation"]
        and current.source.stage == "post_g12"
    )
    _need(current.publication_candidate_sha256 == state["snapshot"].candidate_sha256)
    _need(state["run_started"] is True and state["approval_used"] is True)
    facts, _ = _financial(state)
    q._publication(state)
    q.source._replay_owned_live_source_v1(
        state["source_owner"], key=q.source._SOURCE_KEY
    )
    expiry = min(
        value.deadline,
        state["deadline"],
        current.source.expires_at,
        initial.original_zone.expires_at,
        current.original_zone.expires_at,
        initial.detection.trigger.expires_at,
        current.detection.trigger.expires_at,
    )
    _need(type(expiry) is datetime and expiry.tzinfo is UTC)
    proof = _OriginalEntryProofV1(key=_OWNER_KEY)
    _PROOFS[proof] = {
        "phase": "pending",
        "coordinator": q,
        "session": session,
        "state_id": id(state),
        "task": asyncio.current_task(),
        "bindings": _bindings(state),
        "value": value,
        "input_sha256": _digest(state, value),
        "account_inventory_sha256": inputs.account_raw_inventory_v1(
            state["source_owner"]
        ),
        "expires_at": expiry,
        "cash_cutoff": q.final_recheck.next_cash_window_change_v1(
            facts, evaluated_at=current.source.evaluated_at
        ),
        "initial_packet_sha256": initial.source.packet.bundle_sha256,
        "initial_inventory_sha256": initial.source.raw_inventory_sha256,
        "current_packet_sha256": current.source.packet.bundle_sha256,
        "current_inventory_sha256": current.source.raw_inventory_sha256,
        "initial_receipt_sha256": initial.evaluation_sha256,
        "current_receipt_sha256": current.evaluation_sha256,
        "prepared_at": q._stamp(),
    }
    _PENDING[session] = proof


def _proof(owner, record, state, *, active=True):
    q = _coordinator()
    proof = _SEALED.get(owner)
    value = _PROOFS.get(proof) if type(proof) is _OriginalEntryProofV1 else None
    _need(type(value) is dict and value["session"] is record["session"])
    _need(value["coordinator"] is q)
    _need(value["state_id"] == id(state) and value["task"] is asyncio.current_task())
    _need(
        value["phase"] == "sealed"
        if active
        else value["phase"] in ("completing", "sealed")
    )
    _need(
        all(
            actual is original
            for actual, original in zip(
                _bindings(state), value["bindings"], strict=True
            )
        )
    )
    _need(record["facts"].intent_data is value["value"])
    _need(q._QUALIFIED.get(owner) is record)
    return proof, value


def complete_native_entry_v1(session, owner, *, key):
    q = _coordinator()
    _need(key is q._ENTRY_PROOF_KEY_V1 and owner not in _SEALED)
    _source_frame(session, owner=owner)
    proof = _PENDING.pop(session, None)
    data = _PROOFS.get(proof) if type(proof) is _OriginalEntryProofV1 else None
    _need(type(data) is dict and data["phase"] == "pending")
    record = q._QUALIFIED.get(owner)
    state = q._session(session, current=True)
    _need(type(record) is dict and record["session"] is session)
    rid, _, intent_sha = exact_live_intent_document_v1(data["value"])
    readback = state["entry_native_readback"]
    _need(
        type(readback) is dict
        and readback["reservation_id"] == rid
        and readback["intent_sha256"] == intent_sha
        and readback["state"] == "reserved"
    )
    _need(record["facts"].intent_readback == canonical(readback))
    data.update(phase="completing", intent_readback=record["facts"].intent_readback)
    _SEALED[owner] = proof
    try:
        replay_native_entry_v1(owner, record, state, key=key, completing=True)
        data["phase"] = "sealed"
    except BaseException:
        data["phase"] = "burned"
        _SEALED.pop(owner, None)
        raise


def require_original_public_owner_v1(owner, public_owner, *, key):
    q = _coordinator()
    _need(key is q.public._ENTRY_BANK_KEY_V1)
    record = q._QUALIFIED.get(owner)
    _need(type(record) is dict)
    state = q._OWNED.get(record["session"])
    _need(type(state) is dict)
    _, proof = _proof(owner, record, state, active=False)
    _need(proof.get("reader_active") is True)
    _need(public_owner is proof["bindings"][4] or public_owner is proof["bindings"][5])


def _time(state, proof, stamp):
    q = _coordinator()
    now = utc_from_ns(stamp.utc_ns)
    _need(
        now.tzinfo is UTC and now < proof["expires_at"] and now < proof["cash_cutoff"]
    )
    current = state["current_math"]
    _need(current.source.evaluated_at <= now)
    # All operands stay byte-for-byte fixed. Only their age/window inclusion
    # can change before the next completed candle; reject at those boundaries
    # instead of retiming an old financial or G1 record.
    market = current.source.market
    data_policy = current.policy.prefix.data
    for at, seconds in (
        (market.received_at, data_policy.maximum_snapshot_age_seconds),
        (market.ticker.timestamp, data_policy.maximum_quote_age_seconds),
        (market.order_book.timestamp, data_policy.maximum_quote_age_seconds),
        *(
            (item.source_time, current.source.packet.policy.market_aux.max_age_seconds)
            for item in current.source.packet.market_aux.provenance
        ),
    ):
        _need(timedelta(0) <= now - at <= timedelta(seconds=seconds))
    risk = current.risk_inputs
    account, authority = risk.claims.account, risk.claims.authority
    for observed in (
        account.balance_stamp,
        account.positions_stamp,
        account.history_stamp,
        account.reservations_stamp,
        authority.stamp,
        risk.instrument,
    ):
        _need(
            timedelta(0)
            <= now - observed.observed_at
            <= timedelta(seconds=risk.claims.risk_policy.max_age_seconds)
            and timedelta(0)
            <= now - observed.received_at
            <= timedelta(seconds=risk.claims.risk_policy.max_age_seconds)
        )
    facts, _ = _financial(state)
    _need(facts.history_start <= now - timedelta(days=7))
    for frame in current.source.packet.candles.frames:
        _need(q.public.candles._floor(now, frame.timeframe) == frame.verified_through)
    actual = q.source._SOURCES[state["source_owner"]]
    validate_stamps((actual["current_completed"].model_dump(), stamp.model_dump()))
    _need(
        0
        <= stamp.monotonic_ns - actual["current_completed"].monotonic_ns
        <= actual["plan"].max_current_age_ns
    )
    _need(
        stamp.monotonic_ns < state["expiry_monotonic_ns"]
        and utc_from_ns(stamp.utc_ns) < state["deadline"]
    )


def replay_native_entry_v1(owner, record, state, *, key, completing=False):
    q = _coordinator()
    _need(key is q._ENTRY_PROOF_KEY_V1 and record["revoked"] is False)
    proof_owner = _SEALED.get(owner)
    proof = _PROOFS.get(proof_owner)
    try:
        proof_owner, proof = _proof(owner, record, state, active=not completing)
        value = proof["value"]
        _need(record["facts"].intent_readback == proof["intent_readback"])
        q.source._replay_owned_live_source_v1(
            state["source_owner"], key=q.source._SOURCE_KEY
        )
        _need(
            inputs.account_raw_inventory_v1(state["source_owner"])
            == proof["account_inventory_sha256"]
        )
        _need(proof.get("reader_active", False) is False)
        proof["reader_active"] = True
        try:
            old = q.public._recheck_owned_initial_entry_bytes_v1(
                state["initial_public_owner"], owner, key=q._PUBLIC_SOURCE_KEY_V1
            )
            quote = q.public._replay_owned_live_entry_quote_v1(
                state["current_public_owner"], owner, key=q._PUBLIC_SOURCE_KEY_V1
            )
        finally:
            proof["reader_active"] = False
        _need(
            old[0] == proof["initial_inventory_sha256"]
            and old[1] == proof["initial_packet_sha256"]
        )
        _need(type(quote) is q.public.LiveEntryQuoteReplayV1)
        _need(
            quote.raw_inventory_sha256 == proof["current_inventory_sha256"]
            and quote.original_packet_sha256 == proof["current_packet_sha256"]
        )
        _need(
            quote.quote == state["current_public"].quote
            and quote.reference == state["current_public"].reference
        )
        _need(quote.original_publication_sha256 == sha(state["publication_json"]))
        q._publication(state)
        _need(_digest(state, value) == proof["input_sha256"])
        q._NATIVE_GUARD()
        _need(
            asyncio.current_task() is proof["task"] and not proof["task"].cancelling()
        )
        stamp = q._stamp()
        validate_stamps((state["last"].model_dump(), stamp.model_dump()))
        state["last"] = stamp
        now = utc_from_ns(stamp.utc_ns)
        _time(state, proof, stamp)
        q.ledger._control(state["control"], value, now)
        inspection = decode(
            q.public.live_quote.inspect_live_executable_quote_v1(
                quote.quote, current_time=now
            ).receipt_json,
            maximum=32768,
        )
        _need(inspection["profile_satisfied"] is True)
        _need(
            0 <= (now - quote.reference.source_time).total_seconds() <= 5
            and 0 <= (now - quote.reference.received_at).total_seconds() <= 5
        )
        record["last_final_recheck_json"] = canonical(
            {
                "schema": "ctcc.native_live.same_invocation_exact_entry_recheck.v1",
                "original_initial_math_sha256": proof["initial_receipt_sha256"],
                "original_current_math_sha256": proof["current_receipt_sha256"],
                "original_input_sha256": proof["input_sha256"],
                "raw_inventory_sha256": quote.raw_inventory_sha256,
                "intent_sha256": exact_live_intent_document_v1(value)[2],
                "evaluated_at": now.isoformat(),
                "expires_at": proof["expires_at"].isoformat(),
                "cash_cutoff": proof["cash_cutoff"].isoformat(),
                "full_math_recomputed": False,
                "original_exact_inputs_replayed": True,
                "execution_authority": False,
            }
        )
        _need(_SEALED.get(owner) is proof_owner)
        proof["last_native_quote"] = quote
        proof["final_clock_used"] = False
    except BaseException:
        if type(proof) is dict:
            proof["phase"] = "burned"
        raise


def require_native_final_clock_v1(owner, *, key):
    """One final clock check after heavy replay, directly before native IO."""
    q = _coordinator()
    _need(key is q.physical._QUALIFICATION_SOURCE_KEY_V1)
    record = q._QUALIFIED.get(owner)
    _need(
        type(record) is dict and record["spent"] is True and record["revoked"] is False
    )
    state = q._session(record["session"], current=True)
    _, proof = _proof(owner, record, state)
    try:
        _need(proof.get("final_clock_used") is False)
        proof["final_clock_used"] = True
        quote = proof["last_native_quote"]
        stamp = q._stamp()
        validate_stamps((state["last"].model_dump(), stamp.model_dump()))
        state["last"] = stamp
        _time(state, proof, stamp)
        now = utc_from_ns(stamp.utc_ns)
        inspected = decode(
            q.public.live_quote.inspect_live_executable_quote_v1(
                quote.quote, current_time=now
            ).receipt_json,
            maximum=32768,
        )
        _need(inspected["profile_satisfied"] is True)
        _need(
            0 <= (now - quote.reference.source_time).total_seconds() <= 5
            and 0 <= (now - quote.reference.received_at).total_seconds() <= 5
        )
        return
    except BaseException:
        proof["phase"] = "burned"
        raise


def retire_pending_entry_v1(session, *, key):
    q = _coordinator()
    _need(key is q._ENTRY_PROOF_KEY_V1)
    proof = _PENDING.pop(session, None)
    if proof is not None and proof in _PROOFS:
        _PROOFS[proof]["phase"] = "burned"
