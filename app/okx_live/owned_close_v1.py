"""Original same-task controlled micro close; saved DATA never owns a write.

This purpose consumes the separately confirmed 120-second human permission.
It cannot clear a stop, adopt a later Control revision, restore an entry Arm,
move the original stop boundary, retry a claim, or close after a restart.
"""

import asyncio
import hashlib
import hmac
import os
import secrets
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import FunctionType
from weakref import WeakKeyDictionary

from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)

_PHYSICAL_SUCCESSOR_KEY_V1 = object()
_OWNER_KEY = object()
_OWNED = WeakKeyDictionary()
_NATIVE_GUARD = None


class LiveControlledCloseError(ValueError):
    pass


def _need(value, code="native_live_controlled_close_unavailable"):
    if value is not True:
        raise LiveControlledCloseError(code) from None


class OwnedLiveControlledCloseV1:
    __slots__ = ("__weakref__",)

    def __new__(cls, *, key=None):
        _need(cls is OwnedLiveControlledCloseV1 and key is _OWNER_KEY)
        return object.__new__(cls)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


def _record(owner):
    _NATIVE_GUARD()
    _need(type(owner) is OwnedLiveControlledCloseV1)
    state = _OWNED.get(owner)
    _need(
        type(state) is dict
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    return state


def _clock(state):
    from app.exchange.okx.live_account_config_observation_v1 import _stamp
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_qualification_v1 as qualification

    original = lifecycle._record(state["lifecycle_owner"])
    consent = qualification._replay_spent_owned_live_close_permission_v1(
        original["qualified_owner"],
        original["handoff"].physical_handle,
        key=lifecycle._CLOSE_PERMISSION_KEY_V1,
    )
    _need(consent == state["consent"])
    now = _stamp()
    if state.get("last_clock") is not None:
        validate_stamps((state["last_clock"].model_dump(), now.model_dump()))
    state["last_clock"] = now
    _need(
        consent.consent_received.utc_ns <= now.utc_ns
        and consent.consent_received.monotonic_ns <= now.monotonic_ns
        and now.monotonic_ns < consent.expiry_monotonic_ns
        and utc_from_ns(now.utc_ns) < consent.expires_at,
        "native_live_controlled_close_consent_expired",
    )
    state["lease"].verify_current()
    return now


def _utc_ns(value):
    _need(type(value) is datetime and value.tzinfo is UTC)
    epoch = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        epoch.days * 86400 + epoch.seconds
    ) * 1_000_000_000 + epoch.microseconds * 1000


def _deadline(state, observed, lease_until):
    consent = state["consent"]
    delta = min(
        5_000_000_000,
        consent.expiry_monotonic_ns - observed.monotonic_ns,
        _utc_ns(consent.expires_at) - observed.utc_ns,
        _utc_ns(lease_until) - observed.utc_ns,
    )
    _need(delta > 0)
    return utc_from_ns(observed.utc_ns + delta), observed.monotonic_ns + delta


def _original(state):
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    original = lifecycle._record(state["lifecycle_owner"])
    _need(original["runtime_owner"] is state["runtime_owner"])
    _need(original["key_owner"] is state["key_owner"])
    _need(original["intent"] is state["intent"])
    _need(original["handoff"].controlled_exit_authorized is True)
    return original


def _source_checks(state):
    from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
    from app.exchange.okx import native_live_write_v1 as entry
    from app.okx_live import trusted_source_bridge_v1 as source

    task = asyncio.current_task()
    _need(
        task is not None and not task.cancelling(),
        "native_live_controlled_close_cancelled",
    )
    original = _original(state)
    rid, _raw, intent_digest = exact_live_intent_document_v1(state["intent"])
    _need(
        rid == state["consent"].reservation_id
        and intent_digest == state["consent"].original_intent_sha256
    )
    _need(
        entry._replay_owned_live_disarm_successor_v1(
            original["handoff"].physical_handle, key=_PHYSICAL_SUCCESSOR_KEY_V1
        )
        == state["disarm_successor"]
    )
    source._retained_source_identity_v1(original["source_owner"])
    now = _clock(state)
    capture = state.get("protected_capture")
    if (
        state.get("action", "close") == "close"
        and capture is not None
        and state["phase"] not in ("terminal_ready", "terminal_spent", "complete")
    ):
        _verify_current_protected_v1(state)
        validate_stamps((capture.validated_at.model_dump(), now.model_dump()))
        _need(now.monotonic_ns - capture.validated_at.monotonic_ns <= 5_000_000_000)
        now = _verify_original_close_public_v1(state, now)
        validate_stamps((capture.validated_at.model_dump(), now.model_dump()))
        _need(now.monotonic_ns - capture.validated_at.monotonic_ns <= 5_000_000_000)
    elif state.get("action") == "cancel_original_protection" and state["phase"] not in (
        "terminal_ready",
        "terminal_spent",
        "complete",
    ):
        from app.okx_live.controlled_close_capture_v1 import (
            verify_owned_cancel_result_v1,
        )

        verify_owned_cancel_result_v1(state)
    return now


def _verify_current_protected_v1(state):
    """Replay the same actual private account/protection bank on every guard."""
    from app.okx_live import controlled_close_capture_v1 as custody
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live.reconciliation_facts_v1 import assess_live_postsubmit_facts_v1

    capture = state["protected_capture"]
    custody.verify_owned_capture_pages_v1(state["protected_owner"], capture)
    child = lifecycle._record(state["protected_owner"])
    assessed = assess_live_postsubmit_facts_v1(
        state["intent"], **lifecycle._capture_arguments(child, capture)
    )
    _need(
        assessed == state["protected_facts"]
        and assessed.outcome == "FULL_FILL_PROTECTED"
    )


def _verify_original_close_public_v1(state, now):
    from app.okx_live import owned_qualification_v1 as qualification
    from app.trade_qualification import native_live_public_source_v1 as public
    from app.trade_qualification.executable_quote_live_v1 import (
        inspect_live_executable_quote_v1,
    )

    evaluation = public._replay_owned_live_controlled_quote_v1(
        state["public_owner"], key=qualification._PUBLIC_SOURCE_KEY_V1
    )
    now = _clock(state)
    inspected = decode(
        inspect_live_executable_quote_v1(
            evaluation.quote, current_time=utc_from_ns(now.utc_ns)
        ).receipt_json,
        maximum=32768,
    )
    _need(
        inspected["profile_satisfied"] is True
        and inspected["quote_sha256"] == state["public_quote_sha256"]
    )
    quote, intent = evaluation.quote.ticker, state["intent"]
    _need(
        (quote.bid >= intent.stop_loss and quote.bid_size_contracts >= intent.contracts)
        if intent.direction == "long"
        else (
            quote.ask <= intent.stop_loss
            and quote.ask_size_contracts >= intent.contracts
        )
    )
    return now


async def _current_control(state):
    from sqlalchemy import text

    from app.database.repositories import native_live_lifecycle_v1 as repo
    from app.domain.native_live_legacy_safety_v1 import (
        require_legacy_live_negative_fence_v1,
    )

    async with state["factory"]() as session, session.begin():
        for sql in repo._SETTINGS:
            await session.execute(text(sql))
        await repo._profile(session, state["pins"])
        await require_legacy_live_negative_fence_v1(session)
        control = dict(
            (
                await session.execute(
                    text(
                        "SELECT * FROM public.native_live_lifecycle_control_fence_v1(:uid)"
                    ),
                    {"uid": state["intent"].account_uid},
                )
            )
            .mappings()
            .one()
        )
        database_now = await session.scalar(text("SELECT clock_timestamp()"))
        _validate_control(state, control, database_now)
    _source_checks(state)
    return control


def _validate_control(state, control, now):
    intent = state["intent"]
    _need(type(control) is dict)
    _need(
        tuple(
            control.get(key)
            for key in (
                "environment",
                "account_uid",
                "main_uid",
                "credential_session_sha256",
                "process_sha256",
                "policy_sha256",
                "revision",
                "armed",
                "emergency_stop",
                "arm_id",
                "arm_expires_at",
            )
        )
        == (
            "live",
            intent.account_uid,
            intent.main_uid,
            intent.credential_session_sha256,
            intent.process_sha256,
            intent.policy_sha256,
            state["disarm_successor"]["after_revision"],
            False,
            False,
            None,
            None,
        ),
        "native_live_controlled_close_control_changed",
    )
    _need(type(now) is datetime and now.tzinfo is UTC)
    _need(type(control.get("lease_until")) is datetime and now < control["lease_until"])


def _metadata(state):
    from app.okx_live import lifecycle_key_v1 as keys

    return {
        key: value
        for key, value in keys.lifecycle_key_metadata_v1(state["key_owner"]).items()
        if key != "key_material_sha256"
    }


def _intent_document(state):
    from app.domain import native_live_controlled_close_schema_v1 as spec

    intent, consent, observation = (
        state["intent"],
        state["consent"],
        state["intent_observation"],
    )
    proof = decode(state["protected_journal"].proofs[-1], maximum=65536)
    request = canonical(
        {
            "instId": intent.instrument_id,
            "tdMode": "isolated",
            "side": "sell" if intent.direction == "long" else "buy",
            "posSide": "net",
            "ordType": "fok",
            "sz": format(intent.contracts, "f"),
            "px": format(intent.stop_loss, "f"),
            "clOrdId": state["close_client_order_id"],
            "reduceOnly": True,
        }
    )
    return canonical(
        {
            **_metadata(state),
            "schema": spec.SCHEMA,
            "execution_authority": False,
            "close_intent_id": state["close_intent_id"],
            "reservation_id": consent.reservation_id,
            "original_intent_sha256": state["protected_journal"].intent_sha256,
            "consent_sha256": consent.consent_sha256,
            "operator_roundtrip_id": consent.operator_roundtrip_id,
            "consent_received": consent.consent_received.model_dump(),
            "consent_expires_at": consent.expires_at.isoformat(),
            "consent_expiry_monotonic_ns": consent.expiry_monotonic_ns,
            "maximum_close_submissions": consent.maximum_close_submissions,
            "original_credential_session_sha256": intent.credential_session_sha256,
            "credential_session_sha256": intent.credential_session_sha256,
            "original_process_sha256": intent.process_sha256,
            "process_sha256": consent.process_sha256,
            "candidate_sha256": intent.candidate_sha256,
            "report_sha256": intent.report_sha256,
            "policy_sha256": intent.policy_sha256,
            "position_side": "net",
            "expected_control_revision": state["disarm_successor"]["after_revision"],
            "protected_proof_sha256": sha(state["protected_journal"].proofs[-1]),
            "protection_algo_id": proof["protection_algo_id"],
            "protection_client_id": intent.protection_client_id,
            "entry_order_id": proof["entry_order_id"],
            "raw_custody_sha256": state["protected_capture"].raw_custody_sha256,
            "public_quote_sha256": state["public_quote_sha256"],
            "observation": observation.model_dump(),
            "observed_at": utc_from_ns(observation.utc_ns).isoformat(),
            "close_deadline": state["close_deadline"].isoformat(),
            "close_deadline_monotonic_ns": state["close_deadline_monotonic_ns"],
            "close_client_order_id": state["close_client_order_id"],
            "request_json": request.decode("ascii"),
            "request_sha256": sha(request),
        }
    )


def _claim_document(state):
    from app.domain import native_live_controlled_close_schema_v1 as spec

    parent = decode(state["intent_json"], maximum=65536)
    observation = state["claim_observation"]
    document = {
        name: parent[name]
        for name in (
            "environment",
            "account_uid",
            "main_uid",
            "origin",
            "database_oid",
            "owner_oid",
            "reconciler_oid",
            "key_id",
            "reservation_id",
            "close_intent_id",
            "original_intent_sha256",
            "consent_sha256",
            "operator_roundtrip_id",
            "original_credential_session_sha256",
            "credential_session_sha256",
            "original_process_sha256",
            "process_sha256",
            "candidate_sha256",
            "report_sha256",
            "policy_sha256",
            "expected_control_revision",
        )
    }
    document.update(
        schema=spec.CLAIM_SCHEMA,
        execution_authority=False,
        action=state["action"],
        close_intent_sha256=sha(state["intent_json"]),
        observation=observation.model_dump(),
        observed_at=utc_from_ns(observation.utc_ns).isoformat(),
        request_json=state["request_json"].decode("ascii"),
        request_sha256=sha(state["request_json"]),
        request_wire_sha256=state["request_wire_sha256"],
        request_deadline=state["request_deadline"].isoformat(),
        request_deadline_monotonic_ns=state["request_deadline_monotonic_ns"],
        raw_custody_sha256=state["action_raw_custody_sha256"],
    )
    if state["action"] == "close":
        document["public_quote_sha256"] = state["public_quote_sha256"]
    else:
        document.update(state["cancel_verified_facts"])
    return canonical(document)


def _verify_source_for_signing_v1(owner, *, key_owner):
    from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1

    state = _record(owner)
    _need(state["key_owner"] is key_owner)
    _need(state["phase"] in ("intent_ready", "claim_ready", "terminal_ready"))
    _source_checks(state)
    if (
        state["phase"] in ("intent_ready", "claim_ready")
        and state.get("action", "close") == "close"
    ):
        from app.okx_live import controlled_close_capture_v1 as custody
        from app.okx_live import owned_lifecycle_v1 as lifecycle
        from app.okx_live.reconciliation_facts_v1 import assess_live_postsubmit_facts_v1

        custody.verify_owned_capture_pages_v1(
            state["protected_owner"], state["protected_capture"]
        )
        original = lifecycle._record(state["protected_owner"])
        observed = assess_live_postsubmit_facts_v1(
            state["intent"],
            **lifecycle._capture_arguments(original, state["protected_capture"]),
        )
        _need(
            observed == state["protected_facts"]
            and observed.outcome == "FULL_FILL_PROTECTED"
        )
    elif (
        state["phase"] == "claim_ready"
        and state["action"] == "cancel_original_protection"
    ):
        from app.okx_live.controlled_close_capture_v1 import (
            verify_owned_cancel_result_v1,
        )

        verify_owned_cancel_result_v1(state)
    if state["phase"] == "intent_ready":
        raw, expected = state["intent_json"], _intent_document(state)
        maximum = 65536
    elif state["phase"] == "claim_ready":
        raw, expected = state["claim_json"], _claim_document(state)
        maximum = 32768
    else:
        raw, expected, maximum = (
            state["terminal_json"],
            _terminal_document(state),
            65536,
        )
    _need(type(raw) is bytes and 0 < len(raw) <= maximum and raw == expected)
    _need(
        read_live_lifecycle_blob_v1(state["evidence_root"], sha(raw), domain="proofs")
        == raw
    )


def _consume_owned_live_close_intent_v1(owner, *, key):
    from app.database.repositories import native_live_controlled_close_v1 as repo
    from app.domain.native_live_controlled_close_schema_v1 import INTENT_DOMAIN
    from app.okx_live import lifecycle_key_v1 as keys

    _need(key is repo._INTENT_SOURCE_KEY_V1)
    state = _record(owner)
    _need(state["phase"] == "intent_ready")
    material = keys._material_for_owned_controlled_close_v1(state["key_owner"], owner)
    raw = state["intent_json"]
    state["phase"] = "intent_spent"
    return (
        raw,
        hmac.digest(material, INTENT_DOMAIN + raw, hashlib.sha256).hex(),
        state["key_owner"],
    )


def _consume_owned_live_close_claim_v1(owner, *, action, key):
    from app.database.repositories import native_live_controlled_close_v1 as repo
    from app.domain.native_live_controlled_close_schema_v1 import CLAIM_DOMAIN
    from app.okx_live import lifecycle_key_v1 as keys

    _need(key is repo._CLAIM_SOURCE_KEY_V1)
    state = _record(owner)
    _need(state["phase"] == "claim_ready" and action == state["action"])
    material = keys._material_for_owned_controlled_close_v1(state["key_owner"], owner)
    raw = state["claim_json"]
    state["phase"] = "claim_spent"
    return (
        raw,
        hmac.digest(material, CLAIM_DOMAIN + raw, hashlib.sha256).hex(),
        state["key_owner"],
    )


async def _renew_protected_source(state):
    """Fresh complete account after public collection; fixed economics only."""
    from app.exchange.okx.live_account_config_observation_v1 import _stamp
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import reconciliation_facts_v1 as facts
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1
    from app.okx_live.postfill_economics_v1 import assess_fixed_live_fill_economics_v1

    original = _original(state)
    journal = await original["repository"].read_journal_v1(
        state["key_owner"], state["consent"].reservation_id
    )
    _need(journal.reservation_state != "reconciled")
    history = _close_submission_history_v1(original, journal)
    owner = lifecycle._new_owner(
        **{
            name: original[name]
            for name in (
                "runtime_owner",
                "key_owner",
                "repository",
                "source_owner",
                "intent",
                "handoff",
                "evidence_root",
                "report_id",
                "submission_sha256",
                "submission_attempted",
            )
        },
        journal=journal,
        submission_started=None,
        controlled_close_history=history,
        controlled_close_owner=state["self"],
        capture_id=secrets.token_hex(16),
        capture_barrier=_stamp(),
    )
    state["protected_owner"] = owner
    capture = await lifecycle._capture_owned_live_postsubmit_v1(owner)
    current = lifecycle._record(owner)
    assessed = facts.assess_live_postsubmit_facts_v1(
        current["intent"], **lifecycle._capture_arguments(current, capture)
    )
    _need(
        assessed.outcome == "FULL_FILL_PROTECTED",
        "native_live_close_current_protection_changed",
    )
    economics = assess_fixed_live_fill_economics_v1(
        current["intent"],
        assessed,
        capture,
        policy_json=current["handoff"].policy_json,
        recheck_math_json=current["handoff"].recheck_math_json,
    )
    _need(economics["passed"] is True)
    raw = lifecycle._proof_document(
        current, capture=capture, facts=assessed, economics=economics
    )
    current.update(
        proof_json=raw,
        proof_path=publish_live_lifecycle_blob_v1(
            state["evidence_root"], raw, domain="proofs"
        ),
        source_verified_sha256=sha(raw),
        phase="source_verified",
    )
    accepted = await current["repository"].append_owned_proof_v1(owner)
    _need(accepted.proofs[-1] == raw)
    state.update(
        protected_owner=owner,
        protected_capture=capture,
        protected_facts=assessed,
        protected_journal=accepted,
        lease=current["lease"],
        client=current["client"],
        source_root=current["source_root"],
    )
    return current


def _close_submission_history_v1(original, journal):
    """Retain actual POST UTC separately; never replay an old monotonic batch."""
    from app.domain.source_primitives import ClockStamp
    from app.okx_live.reconciliation_facts_v1 import LiveReconciliationHistoryV1

    started = original["submission_started"]
    _need(
        type(started) is ClockStamp
        and type(journal.intent_recorded_at) is datetime
        and journal.intent_recorded_at.tzinfo is UTC
    )
    observed = utc_from_ns(started.utc_ns)
    _need(journal.intent_recorded_at <= observed)
    return LiveReconciliationHistoryV1(journal.intent_recorded_at, observed)


def _verify_owned_close_history_v1(owner, *, key):
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(key is lifecycle._SOURCE_LEASE_KEY_V1)
    child = lifecycle._record(owner)
    state = _record(child["controlled_close_owner"])
    original = _original(state)
    _need(state["protected_owner"] is owner)
    for name in ("runtime_owner", "key_owner", "source_owner", "intent", "handoff"):
        _need(child[name] is original[name])
    _need(child["submission_started"] is None)
    _need(
        child["controlled_close_history"]
        == _close_submission_history_v1(original, child["journal"])
    )
    _clock(state)


def _require_complete_close_flow_v1():
    """All source paths must exist before lifecycle preflight can admit Arm."""
    _NATIVE_GUARD()
    from app.exchange.okx import native_live_close_write_v1 as physical
    from app.okx_live import controlled_close_capture_v1 as capture

    _need(callable(physical.submit_owned_live_controlled_action_v1))
    _need(callable(capture.capture_owned_live_controlled_result_v1))
    _need(callable(_terminal_document))


async def run_owned_live_controlled_close_v1(lifecycle_owner):
    """At most one reduce-only FOK and one exact post-flat algo cleanup."""
    from app.database.repositories.native_live_controlled_close_v1 import (
        NativeLiveControlledCloseRepositoryV1,
    )
    from app.exchange.okx import native_live_write_v1 as entry
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_qualification_v1 as qualification
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1
    from app.trade_qualification import native_live_public_source_v1 as public
    from app.trade_qualification.executable_quote_live_v1 import (
        inspect_live_executable_quote_v1,
    )

    _require_complete_close_flow_v1()
    original = lifecycle._record(lifecycle_owner)
    _need(
        original["outcome"] == "PROTECTED_HOLD" and original["phase"] == "proof_spent"
    )
    _need(original["handoff"].controlled_exit_authorized is True)
    consent = qualification._consume_owned_live_close_permission_v1(
        original["qualified_owner"],
        original["handoff"].physical_handle,
        key=lifecycle._CLOSE_PERMISSION_KEY_V1,
    )
    successor = entry._replay_owned_live_disarm_successor_v1(
        original["handoff"].physical_handle, key=_PHYSICAL_SUCCESSOR_KEY_V1
    )
    owner = OwnedLiveControlledCloseV1(key=_OWNER_KEY)
    cfg = original["repository"]
    _OWNED[owner] = state = {
        "self": owner,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "lifecycle_owner": lifecycle_owner,
        "runtime_owner": original["runtime_owner"],
        "key_owner": original["key_owner"],
        "factory": cfg.factory,
        "pins": cfg.pins,
        "intent": original["intent"],
        "evidence_root": original["evidence_root"],
        "consent": consent,
        "disarm_successor": successor,
        "lease": original["lease"],
        "phase": "created",
        "physical_consumed": set(),
        "action": "close",
        "last_clock": None,
    }
    try:
        (
            public_owner,
            evaluation,
        ) = await qualification._capture_owned_live_close_public_v1(
            original["qualified_owner"],
            original["handoff"].physical_handle,
            key=lifecycle._CLOSE_PERMISSION_KEY_V1,
        )
        state["public_owner"] = public_owner
        await _renew_protected_source(state)
        # Freshly replay the exact new source, preserving original geometry.
        evaluation = public._replay_owned_live_controlled_quote_v1(
            public_owner, key=qualification._PUBLIC_SOURCE_KEY_V1
        )
        now = _clock(state)
        inspection = inspect_live_executable_quote_v1(
            evaluation.quote, current_time=utc_from_ns(now.utc_ns)
        )
        inspected = decode(inspection.receipt_json, maximum=32768)
        _need(inspected["profile_satisfied"] is True)
        state["public_quote_sha256"] = inspected["quote_sha256"]
        quote, intent = evaluation.quote.ticker, state["intent"]
        _need(
            (
                quote.bid >= intent.stop_loss
                and quote.bid_size_contracts >= intent.contracts
            )
            if intent.direction == "long"
            else (
                quote.ask <= intent.stop_loss
                and quote.ask_size_contracts >= intent.contracts
            ),
            "native_live_close_original_boundary_not_executable",
        )
        control = await _current_control(state)
        observation = _clock(state)
        deadline, mono = _deadline(state, observation, control["lease_until"])
        state.update(
            intent_observation=observation,
            close_deadline=deadline,
            close_deadline_monotonic_ns=mono,
            close_client_order_id="CTCCX" + secrets.token_hex(13),
            close_intent_id=secrets.token_hex(32),
        )
        state["intent_json"] = raw = _intent_document(state)
        publish_live_lifecycle_blob_v1(state["evidence_root"], raw, domain="proofs")
        state["phase"] = "intent_ready"
        repository = NativeLiveControlledCloseRepositoryV1(
            state["factory"], state["pins"]
        )
        state["close_repository"] = repository
        intent_readback = await repository.prepare_owned_intent_v1(owner)
        _need(intent_readback.document_json == raw)
        state["intent_readback"] = intent_readback
        state["request_json"] = intent_readback.request_json
        state["request_deadline"] = deadline
        state["request_deadline_monotonic_ns"] = mono
        state["action_raw_custody_sha256"] = state[
            "protected_capture"
        ].raw_custody_sha256
        await _prepare_and_submit_action_v1(owner)
        return await _complete_controlled_result_v1(owner)
    except BaseException:
        # No exception is retryable. The original intent/claim, raw response and
        # protection survive; a negative stop cannot authorize another write.
        try:
            from app.okx_live.controlled_close_capture_v1 import (
                retain_owned_controlled_responses_v1,
            )

            retain_owned_controlled_responses_v1(state)
        except Exception:  # noqa: BLE001 -- retention failure must never block the durable negative stop.
            state["response_retention_incomplete"] = True
        try:
            await original["repository"].inhibit_for_recovery_v1(state["key_owner"])
        finally:
            state["phase"] = "uncertain"
        raise


async def _prepare_and_submit_action_v1(owner):
    from app.exchange.okx import native_live_close_write_v1 as physical
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1

    state = _record(owner)
    _source_checks(state)
    await _current_control(state)
    observation = _clock(state)
    state["claim_observation"] = observation
    physical.prepare_owned_live_controlled_request_v1(
        owner, key=physical._ACTOR_SOURCE_KEY_V1
    )
    raw = state["claim_json"] = _claim_document(state)
    publish_live_lifecycle_blob_v1(state["evidence_root"], raw, domain="proofs")
    state["phase"] = "claim_ready"
    readback = await state["close_repository"].claim_owned_submission_v1(
        owner, action=state["action"]
    )
    _need(readback.document_json == raw)
    state["claim_readback"] = readback
    state.setdefault("claims", {})[state["action"]] = readback
    state["phase"] = "dispatch_ready"
    response = await physical.submit_owned_live_controlled_action_v1(owner)
    state.setdefault("responses", {})[state["action"]] = response
    from app.okx_live.controlled_close_capture_v1 import (
        retain_owned_controlled_responses_v1,
    )

    retain_owned_controlled_responses_v1(state)
    return response


def _replay_owned_live_close_dispatch_v1(owner, *, key, consume=False):
    from app.exchange.okx import native_live_close_write_v1 as physical

    _need(key is physical._ACTOR_SOURCE_KEY_V1)
    state = _record(owner)
    _need(state["phase"] in ("dispatch_ready", "dispatch_spent"))
    _source_checks(state)
    action = state["action"]
    _need(state["claim_readback"].document_json == state["claim_json"])
    _need(state["claim_readback"].request_wire_sha256 == state["request_wire_sha256"])
    now = _clock(state)
    _need(
        now.monotonic_ns < state["request_deadline_monotonic_ns"]
        and utc_from_ns(now.utc_ns) < state["request_deadline"]
    )
    if consume:
        _need(
            action not in state["physical_consumed"]
            and state["phase"] == "dispatch_ready"
        )
        state["physical_consumed"].add(action)
        state["phase"] = "dispatch_spent"
    return state


async def _complete_controlled_result_v1(owner):
    from app.okx_live import controlled_close_capture_v1 as capture

    state = _record(owner)
    result = await capture.capture_owned_live_controlled_result_v1(
        owner, allow_original_algo=True
    )
    _need(result.close_filled is True, "native_live_close_fill_unproven")
    if result.original_algo_pending is True:
        # Closing has already consumed its claim. Cleanup uses this NEW
        # measured full-flat result, never the pre-close protection/quote age.
        state.update(
            action="cancel_original_protection",
            cancel_capture=result,
            cancel_verified_facts=result.cancel_facts,
        )
        control = await _current_control(state)
        observation = _clock(state)
        deadline, mono = _deadline(state, observation, control["lease_until"])
        state.update(
            action="cancel_original_protection",
            request_deadline=deadline,
            request_deadline_monotonic_ns=mono,
            request_json=canonical(
                [
                    {
                        "instId": state["intent"].instrument_id,
                        "algoId": state["protected_facts"].protection_algo_id,
                    }
                ]
            ),
            cancel_verified_facts=result.cancel_facts,
            cancel_capture=result,
            action_raw_custody_sha256=result.raw_custody_sha256,
        )
        await _prepare_and_submit_action_v1(owner)
        result = await capture.capture_owned_live_controlled_result_v1(
            owner, allow_original_algo=False
        )
    _need(result.flat_verified is True and result.original_algo_pending is False)
    state["terminal_capture"] = result
    return await _publish_terminal_v1(owner)


def _terminal_document(state):
    from app.okx_live import controlled_close_capture_v1 as capture

    return capture.controlled_close_terminal_document_v1(state)


async def _publish_terminal_v1(owner):
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1

    state = _record(owner)
    original = _original(state)
    state["terminal_journal"] = await original["repository"].read_journal_v1(
        state["key_owner"], state["consent"].reservation_id
    )
    raw = state["terminal_json"] = _terminal_document(state)
    publish_live_lifecycle_blob_v1(state["evidence_root"], raw, domain="proofs")
    state["phase"] = "terminal_ready"
    accepted = await original["repository"].append_owned_proof_v1(owner)
    control = await original["repository"].read_control_v1(state["key_owner"])
    state["phase"] = "complete"
    return lifecycle._new_receipt(accepted, control)


def _consume_owned_live_close_terminal_proof_v1(owner, *, key):
    from app.database.repositories import native_live_lifecycle_v1 as repo
    from app.domain.native_live_lifecycle_schema_v1 import DOMAIN
    from app.okx_live import lifecycle_key_v1 as keys

    _need(key is repo._PROOF_CONSUME_KEY_V1)
    state = _record(owner)
    _need(state["phase"] == "terminal_ready")
    material = keys._material_for_owned_controlled_close_v1(state["key_owner"], owner)
    raw = state["terminal_json"]
    state["phase"] = "terminal_spent"
    return (
        raw,
        hmac.digest(material, DOMAIN + raw, hashlib.sha256).hex(),
        state["key_owner"],
    )


from app.database.repositories import (
    native_live_controlled_close_v1 as _close_repository,
)
from app.database.repositories import native_live_lifecycle_v1 as _lifecycle_repository
from app.domain import native_live_controlled_close_schema_v1 as _close_spec
from app.exchange.okx import native_live_close_write_v1 as _physical
from app.exchange.okx import native_live_write_v1 as _entry_physical
from app.okx_live import controlled_close_capture_v1 as _capture
from app.okx_live import lifecycle_key_v1 as _keys
from app.okx_live import owned_lifecycle_v1 as _lifecycle_owner
from app.okx_live import owned_qualification_v1 as _qualification
from app.okx_live import postfill_economics_v1 as _postfill
from app.okx_live import postsubmit_capture_v1 as _postsubmit
from app.okx_live import reconciliation_facts_v1 as _facts
from app.okx_live import trusted_source_bridge_v1 as _account_source
from app.trade_qualification import executable_quote_live_v1 as _quote
from app.trade_qualification import native_live_public_source_v1 as _public_source
from app.trade_qualification.native_continuation_guard_v1 import (
    install_native_guards_v1 as _install,
)


def _require_original_owner_callbacks_v1():
    """A replacement present before this module loads is never adopted."""
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    callbacks = (
        (
            _lifecycle_owner,
            (
                "_record",
                "_new_owner",
                "_capture_arguments",
                "_capture_owned_live_postsubmit_v1",
            ),
        ),
        (
            _qualification,
            (
                "_consume_owned_live_close_permission_v1",
                "_replay_spent_owned_live_close_permission_v1",
                "_capture_owned_live_close_public_v1",
            ),
        ),
        (_entry_physical, ("_record", "_replay_owned_live_disarm_successor_v1")),
        (_account_source, ("_retained_source_identity_v1",)),
        (_public_source, ("_replay_owned_live_controlled_quote_v1",)),
    )
    for module, names in callbacks:
        path = Path(module.__file__).resolve()
        raw = path.read_bytes()
        for name in names:
            function = vars(module).get(name)
            _need(
                type(function) is FunctionType
                and function.__module__ == module.__name__
                and Path(function.__code__.co_filename).resolve() == path
            )
            _source_code(function, raw, str(path))
    # The qualification's already-installed owner guard is also replayed; a
    # foreign module's valid guard cannot stand in for this exact owner.
    guard = _qualification._NATIVE_GUARD
    _need(type(guard) is FunctionType)
    closure = dict(
        zip(
            guard.__code__.co_freevars,
            (cell.cell_contents for cell in guard.__closure__ or ()),
            strict=True,
        )
    )
    _need(closure.get("module") is _qualification)
    guard()


_require_original_owner_callbacks_v1()

_install(
    sys.modules[__name__],
    ("run_owned_live_controlled_close_v1",),
    dependencies=(
        _close_repository,
        _lifecycle_repository,
        _close_spec,
        _physical,
        _entry_physical,
        _capture,
        _keys,
        _physical.transport,
        _lifecycle_owner,
        _qualification,
        _postfill,
        _postsubmit,
        _facts,
        _account_source,
        _quote,
        _public_source,
    ),
    bindings=((_qualification, "_NATIVE_GUARD", _qualification._NATIVE_GUARD),),
)
