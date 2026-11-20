"""Private Live lifecycle ownership, separate from entry/Arm authority.

Only the fixed spent qualification and its genuine physical response can start
a postsubmit capture. Constructors, saved receipts and pure fact results never
own a credential lease, signer, SQL resolution or controlled close permission.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import threading
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from types import FunctionType
from weakref import WeakKeyDictionary

from app.domain.source_primitives import canonical, decode, sha

_POSTSUBMIT_HANDOFF_KEY_V1 = object()
_SOURCE_LEASE_KEY_V1 = object()
_CLOSE_PERMISSION_KEY_V1 = object()
_OWNER_KEY_V1 = object()
_OWNERS = WeakKeyDictionary()
_ACTIVE_CAPTURE = ContextVar("ctcc_owned_live_lifecycle_capture_v1", default=None)
_RECEIPTS = WeakKeyDictionary()
_PUBLIC_KEYS = frozenset(
    (
        "schema",
        "status",
        "reservation_id",
        "report_id",
        "risk_hold_retained",
        "protection_verified",
        "emergency_stop",
        "disarmed",
        "execution_authority",
    )
)


class NativeLiveLifecycleError(ValueError):
    pass


def _need(condition, code="native_live_lifecycle_unavailable"):
    if condition is not True:
        raise NativeLiveLifecycleError(code) from None


class OwnedLiveLifecycleV1:
    __slots__ = ("__weakref__",)

    def __init__(self, key=None):
        _need(key is _OWNER_KEY_V1)

    @property
    def execution_authority(self):
        return False


def _record(owner):
    _need(type(owner) is OwnedLiveLifecycleV1)
    state = _OWNERS.get(owner)
    _need(
        type(state) is dict
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    return state


def _require_active_source_context_v1(source_owner, *, key):
    """Negative-only bridge check; returns no lease, credential or authority."""
    _need(key is _SOURCE_LEASE_KEY_V1)
    state = _record(_ACTIVE_CAPTURE.get())
    _need(state["source_owner"] is source_owner and state["phase"] == "capturing")


def _retain_owned_live_get_raw_v1(
    *,
    lease,
    raw,
    headers,
    stamps,
    request_path,
    signed_at,
    request_signature_sha256,
    certificate_sha256,
    synthetic_mock,
):
    """Fixed transport-only custody hook; inactive callers obtain no result."""
    owner = _ACTIVE_CAPTURE.get()
    if owner is None:
        return
    from app.domain.source_primitives import validate_stamps
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1

    state = _record(owner)
    _need(
        state["phase"] == "capturing"
        and state["lease"] is lease
        and synthetic_mock is False
    )
    lease.verify_current()
    barrier = state["capture_barrier"]
    validate_stamps((barrier.model_dump(), *(item.model_dump() for item in stamps)))
    _need(
        stamps[0].utc_ns > barrier.utc_ns
        and stamps[0].monotonic_ns > barrier.monotonic_ns
    )
    _need(len(state["raw_captures"]) < 512 and type(raw) is bytes)
    _need(state["raw_total"] + len(raw) <= 64 * 1024 * 1024)
    document = canonical(
        {
            "schema": "ctcc.native_live.lifecycle_raw_get.v1",
            "environment": "live",
            "origin": lease._pin.origin,
            "lease_binding_id": lease.binding_id,
            "request_path": request_path,
            "signed_at": signed_at,
            "request_signature_sha256": request_signature_sha256,
            "tls_certificate_sha256": certificate_sha256,
            "stamps": [item.model_dump() for item in stamps],
            "raw_body_base64": base64.b64encode(raw).decode("ascii"),
            "raw_headers_base64": [
                [base64.b64encode(part).decode("ascii") for part in pair]
                for pair in headers
            ],
            "raw_body_sha256": sha(raw),
            "execution_authority": False,
        }
    )
    path = publish_live_lifecycle_blob_v1(
        state["evidence_root"], document, domain="raw"
    )
    state["raw_captures"].append((path, sha(document), sha(raw), stamps[0], stamps[-1]))
    state["raw_total"] += len(raw)
    return


def _runtime_repository(runtime_owner):
    from app.database.repositories.native_live_lifecycle_v1 import (
        NativeLiveLifecycleRepositoryV1,
    )
    from app.okx_live import lifecycle_key_v1 as keys
    from app.okx_live import native_runtime_v1 as runtime

    cfg = runtime._replay_live_runtime_v1(runtime_owner, key=runtime._KEY)
    key_owner = keys.load_owned_live_lifecycle_key_v1(runtime_owner)
    repository = NativeLiveLifecycleRepositoryV1(
        cfg["reconciler_factory"], cfg["reconciler_pins"]
    )
    return cfg, key_owner, repository


def _require_complete_entry_flow_v1():
    """An additive SQL foundation alone cannot unlock a positive Live Arm."""
    path = Path(__file__).resolve()
    source = path.read_bytes()
    functions = []
    for name in (
        "reconcile_owned_live_postsubmit_v1",
        "reconcile_pending_owned_live_intent_v1",
        "_capture_owned_live_postsubmit_v1",
        "_verify_source_for_signing_v1",
        "_controlled_exit_owned_live_v1",
    ):
        function = globals().get(name)
        _need(
            type(function) is FunctionType and function.__module__ == __name__,
            "native_live_lifecycle_entry_flow_unavailable",
        )
        functions.append(function)
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    for function in functions:
        _source_code(function, source, str(path))
    _require_lifecycle_source_modules_v1()
    from app.okx_live.owned_close_v1 import _require_complete_close_flow_v1

    _require_complete_close_flow_v1()


def _require_lifecycle_source_modules_v1():
    from app.okx_live import (
        lifecycle_custody_v1,
        original_oco_recovery_v1,
        owned_recovery_v1,
        postfill_economics_v1,
        postsubmit_capture_v1,
        reconciliation_facts_v1,
    )
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    root = Path(__file__).resolve().parent
    for module in (
        lifecycle_custody_v1,
        owned_recovery_v1,
        original_oco_recovery_v1,
        postfill_economics_v1,
        postsubmit_capture_v1,
        reconciliation_facts_v1,
    ):
        path = root / (module.__name__.rsplit(".", 1)[1] + ".py")
        _need(Path(module.__file__).resolve() == path)
        raw = path.read_bytes()
        for value in vars(module).values():
            if type(value) is FunctionType and value.__module__ == module.__name__:
                _source_code(value, raw, str(path))


async def preflight_owned_live_lifecycle_runtime_v1(runtime_owner):
    """No venue calls or Arm: verify original runtime, key and dedicated actor."""
    _require_complete_entry_flow_v1()
    _, key_owner, repository = _runtime_repository(runtime_owner)
    return await repository.preflight(key_owner)


def _verify_source_for_signing_v1(owner, *, key_owner):
    """The HMAC key is available only for one privately captured, retained proof."""
    from app.okx_live.lifecycle_key_v1 import _private_file

    state = _record(owner)
    _need(state["phase"] == "source_verified" and state["key_owner"] is key_owner)
    recovery = state.get("recovery_history") is not None
    if recovery:
        from app.okx_live.owned_recovery_v1 import _verify_recovery_observation_v1

        _verify_recovery_observation_v1(owner, key=_SOURCE_LEASE_KEY_V1)
    elif state.get("controlled_close_history") is not None:
        from app.okx_live.owned_close_v1 import _verify_owned_close_history_v1

        _verify_owned_close_history_v1(owner, key=_SOURCE_LEASE_KEY_V1)
    raw = state["proof_json"]
    _need(type(raw) is bytes and canonical(decode(raw, maximum=65536)) == raw)
    # A real capture retains these exact bytes before the signer can run. The
    # content-addressed, private file is re-read; a caller receipt is insufficient.
    path = state["proof_path"]
    _private_file(path)
    _need(
        type(path) is type(Path())
        and path.is_absolute()
        and path.is_file()
        and path.read_bytes() == raw
        and path.name == sha(raw) + ".json"
    )
    _need(state["source_verified_sha256"] == sha(raw))
    _require_lifecycle_source_modules_v1()
    capture = state["capture"]
    if capture is not None:
        from app.domain.source_primitives import validate_stamps
        from app.exchange.okx.live_account_config_observation_v1 import (
            _stamp,
            verify_live_account_config_readback_v1,
        )
        from app.exchange.okx.live_account_raw_page_v1 import (
            verify_live_account_raw_page_readback_v1,
        )
        from app.exchange.okx.live_trade_read_page_v1 import (
            verify_live_trade_read_page_v1,
        )
        from app.okx_live import original_oco_recovery_v1 as original_oco
        from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1
        from app.okx_live.postfill_economics_v1 import (
            assess_fixed_live_fill_economics_v1,
        )
        from app.okx_live.reconciliation_facts_v1 import assess_live_postsubmit_facts_v1

        extra = original_oco.extra_terminal_pages_v1(capture) if recovery else ()

        lease = state["lease"]
        lease.verify_current()
        now = _stamp()
        validate_stamps((capture.validated_at.model_dump(), now.model_dump()))
        _need(now.monotonic_ns - capture.validated_at.monotonic_ns <= 5_000_000_000)
        retained = []
        for _, digest, _, _, _ in state["raw_captures"]:
            retained.append(
                decode(
                    read_live_lifecycle_blob_v1(
                        state["evidence_root"], digest, domain="raw"
                    ),
                    maximum=12 * 1024 * 1024,
                )
            )
        for page in (
            capture.config_before,
            capture.order,
            *capture.fills,
            *capture.current_pages,
            *(() if capture.protection is None else (capture.protection,)),
            capture.config_after,
            *extra,
        ):
            _need(page.synthetic_mock is False)
            _need(
                any(
                    item["raw_body_sha256"] == page.raw_sha256
                    and item["request_signature_sha256"]
                    == page.request_signature_sha256
                    and item["stamps"]
                    == [stamp.model_dump() for stamp in page.stamps[:5]]
                    for item in retained
                )
            )
        verify_live_account_config_readback_v1(capture.config_before, lease=lease)
        verify_live_account_config_readback_v1(capture.config_after, lease=lease)
        for page in capture.current_pages:
            verify_live_account_raw_page_readback_v1(page, lease=lease)
        for page in (
            capture.order,
            *capture.fills,
            *(() if capture.protection is None else (capture.protection,)),
            *extra,
        ):
            verify_live_trade_read_page_v1(page, lease=lease)
        observed = (
            original_oco.assess_recovery_capture_v1(state, capture)
            if recovery
            else assess_live_postsubmit_facts_v1(
                state["intent"], **_capture_arguments(state, capture)
            )
        )
        _need(observed == state["assessed_facts"])
        handoff = state["handoff"]
        economics = (
            assess_fixed_live_fill_economics_v1(
                state["intent"],
                observed,
                capture,
                policy_json=handoff.policy_json,
                recheck_math_json=handoff.recheck_math_json,
            )
            if observed.outcome == "FULL_FILL_PROTECTED" and not recovery
            else None
        )
        _need(economics == state["assessed_economics"])
    rebuilt = _proof_document(
        state,
        capture=capture,
        facts=state["assessed_facts"],
        economics=state["assessed_economics"],
        failure_code=decode(raw, maximum=65536)["failure_code"],
    )
    _need(rebuilt == raw)


def _consume_owned_live_proof_v1(owner, *, key):
    from app.database.repositories import native_live_lifecycle_v1 as repository
    from app.domain.native_live_lifecycle_schema_v1 import DOMAIN
    from app.okx_live import lifecycle_key_v1 as keys

    _need(key is repository._PROOF_CONSUME_KEY_V1)
    from app.okx_live import owned_close_v1 as close

    if type(owner) is close.OwnedLiveControlledCloseV1:
        return close._consume_owned_live_close_terminal_proof_v1(owner, key=key)
    state = _record(owner)
    key_owner = state["key_owner"]
    material = keys._material_for_owned_lifecycle_v1(key_owner, owner)
    raw = state["proof_json"]
    signature = hmac.digest(material, DOMAIN + raw, hashlib.sha256).hex()
    state["phase"] = "proof_spent"
    return raw, signature, key_owner


class OwnedLiveLifecycleReceiptV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False)


def _projection(document):
    _need(type(document) is dict and set(document) == _PUBLIC_KEYS)
    _need(
        document["schema"] == "ctcc.native_live.lifecycle_receipt.v1"
        and document["status"]
        in ("ZERO_FILL_FLAT", "PROTECTED_HOLD", "UNCERTAIN", "RECONCILED_FLAT")
        and document["execution_authority"] is False
    )
    import re

    _need(
        type(document["reservation_id"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", document["reservation_id"]) is not None
    )
    _need(
        document["report_id"] is None
        or type(document["report_id"]) is str
        and 0 < len(document["report_id"]) <= 128
    )
    _need(
        all(
            document[name] is None or type(document[name]) is bool
            for name in ("risk_hold_retained", "emergency_stop", "disarmed")
        )
    )
    _need(
        document["protection_verified"] is None
        or document["protection_verified"] is True
    )
    return deepcopy(document)


def _journal_projection(journal, control, *, current_protection=False):
    proof = decode(journal.proofs[-1], maximum=65536) if journal.proofs else None
    terminal = journal.reservation_state == "reconciled"
    return _projection(
        {
            "schema": "ctcc.native_live.lifecycle_receipt.v1",
            "status": proof["outcome"] if proof else "UNCERTAIN",
            "reservation_id": journal.reservation_id,
            "report_id": proof.get("report_id") if proof else None,
            "risk_hold_retained": not terminal,
            "protection_verified": True
            if current_protection is True
            and proof
            and proof["outcome"] == "PROTECTED_HOLD"
            else None,
            "emergency_stop": control["emergency_stop"],
            "disarmed": control["disarmed"],
            "execution_authority": False,
        }
    )


def _new_receipt(journal, control, *, current_protection=False):
    value = _journal_projection(journal, control, current_protection=current_protection)
    receipt = object.__new__(OwnedLiveLifecycleReceiptV1)
    _RECEIPTS[receipt] = canonical(value)
    return receipt


def live_lifecycle_public_projection_v1(receipt):
    _need(type(receipt) is OwnedLiveLifecycleReceiptV1 and receipt in _RECEIPTS)
    return _projection(decode(_RECEIPTS[receipt], maximum=4096))


async def observe_live_lifecycle_tasks_v1():
    """A bounded, complete pending-native snapshot; no legacy intent adoption."""
    from app.okx_live.native_runtime_v1 import configured_live_runtime_v1

    async with configured_live_runtime_v1() as runtime_owner:
        _, key_owner, repository = _runtime_repository(runtime_owner)
        await repository.preflight(key_owner)
        rows = await repository.observe_pending_v1(key_owner)
        journals = tuple(
            [await repository.read_journal_v1(key_owner, row[0]) for row in rows]
        )
        control = await repository.read_control_v1(key_owner)
        return tuple(_journal_projection(item, control) for item in journals)


def _new_owner(**values):
    owner = OwnedLiveLifecycleV1(_OWNER_KEY_V1)
    _OWNERS[owner] = {
        **values,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "phase": "created",
        "raw_captures": [],
        "raw_total": 0,
    }
    return owner


def _submission_bytes(response, *, intent_sha256):
    document = {
        name: value
        for name, value in response.items()
        if name not in ("raw_body", "raw_headers")
    }
    document["raw_body_base64"] = (
        base64.b64encode(response["raw_body"]).decode("ascii")
        if response["raw_body"] is not None
        else None
    )
    document["raw_headers_base64"] = (
        [
            [base64.b64encode(item).decode("ascii") for item in pair]
            for pair in response["raw_headers"]
        ]
        if response["raw_headers"] is not None
        else None
    )
    document["intent_sha256"] = intent_sha256
    return canonical(document)


def _capture_arguments(state, capture):
    arguments = {
        "order_page": capture.order,
        "fill_pages": capture.fills,
        "protection_page": capture.protection,
        "config_before": capture.config_before,
        "config_after": capture.config_after,
        "current_pages": capture.current_pages,
        "submission_started": state["submission_started"],
        "submission_barrier": state["capture_barrier"],
        "validated_at": capture.validated_at,
        "tick_size": capture.tick_size,
    }
    if state.get("recovery_history") is not None:
        arguments["history"] = state["recovery_history"]
    elif state.get("controlled_close_history") is not None:
        arguments["history"] = state["controlled_close_history"]
    return arguments


async def _capture_owned_live_postsubmit_v1(owner):
    from app.okx_live import postsubmit_capture_v1 as capture_source
    from app.okx_live import trusted_source_bridge_v1 as source

    state = _record(owner)
    _need(state["phase"] == "created")
    state["phase"] = "capturing"
    token = _ACTIVE_CAPTURE.set(owner)
    try:
        client, lease, root, uid, main, credential = source._live_postsubmit_lease_v1(
            state["source_owner"], key=_SOURCE_LEASE_KEY_V1
        )
        _need(uid == state["intent"].account_uid and main == state["intent"].main_uid)
        _need(credential == state["intent"].credential_session_sha256)
        state.update(
            client=client,
            lease=lease,
            source_root=root,
            credential_session_sha256=credential,
            credential_continuity="same_original_lease",
        )
        return await capture_source.capture_owned_live_postsubmit_pages_v1(
            owner, key=capture_source._CAPTURE_KEY_V1
        )
    finally:
        _ACTIVE_CAPTURE.reset(token)


def _proof_document(state, *, capture, facts, economics, failure_code=None):
    from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
    from app.domain.native_live_lifecycle_schema_v1 import SCHEMA
    from app.domain.source_primitives import utc_from_ns
    from app.exchange.okx.live_account_config_observation_v1 import _stamp
    from app.okx_live.lifecycle_key_v1 import lifecycle_key_metadata_v1

    intent = state["intent"]
    _, intent_json, intent_digest = exact_live_intent_document_v1(intent)
    previous = state["journal"]
    metadata = lifecycle_key_metadata_v1(state["key_owner"])
    protected = (
        facts is not None
        and facts.outcome == "FULL_FILL_PROTECTED"
        and (
            state.get("recovery_history") is not None
            or economics is not None
            and economics["passed"] is True
        )
    )
    zero = facts is not None and facts.outcome == "ZERO_FILL_FLAT"
    terminal = (
        facts is not None
        and facts.outcome == "RECONCILED_FLAT"
        and facts.exit_kind == "original_oco"
        and facts.exit_lineage_verified is True
    )
    flat = zero or terminal
    outcome = (
        "ZERO_FILL_FLAT"
        if zero
        else "RECONCILED_FLAT"
        if terminal
        else "PROTECTED_HOLD"
        if protected
        else "UNCERTAIN"
    )
    if "proof_observed" not in state:
        state["proof_observed"] = (
            capture.validated_at if capture is not None else _stamp()
        )
    observed = state["proof_observed"]
    proof = {
        **{
            name: value
            for name, value in metadata.items()
            if name != "key_material_sha256"
        },
        "schema": SCHEMA,
        "execution_authority": False,
        "reservation_id": previous.reservation_id,
        "sequence": len(previous.proofs) + 1,
        "previous_sha256": sha(previous.proofs[-1]) if previous.proofs else None,
        "intent_sha256": intent_digest,
        "event_key": intent.event_key,
        "candidate_sha256": intent.candidate_sha256,
        "report_sha256": intent.report_sha256,
        "report_id": state["report_id"],
        "policy_sha256": intent.policy_sha256,
        "original_credential_session_sha256": intent.credential_session_sha256,
        "credential_session_sha256": state.get(
            "credential_session_sha256", intent.credential_session_sha256
        ),
        "credential_continuity": state.get(
            "credential_continuity", "same_original_lease"
        ),
        "credential_current_verified": capture is not None,
        "request_sha256": decode(intent_json, maximum=65536)["request_sha256"],
        "outcome": outcome,
        "observed_at": utc_from_ns(observed.utc_ns).isoformat(),
        "reconciliation_barrier": state["capture_barrier"].model_dump(),
        "submission_started": state["submission_started"].model_dump()
        if state["submission_started"] is not None
        else None,
        "submission_sha256": state["submission_sha256"],
        "submission_attempted": state["submission_attempted"],
        "raw_bank_sha256": facts.raw_bank_sha256 if facts else None,
        "raw_custody_sha256": capture.raw_custody_sha256
        if capture
        else sha(canonical([item[1] for item in state["raw_captures"]])),
        "current_custody_sha256": capture.current_custody_sha256 if capture else None,
        "account_config_before_sha256": capture.config_before.receipt_sha256
        if capture
        else None,
        "account_config_after_sha256": capture.config_after.receipt_sha256
        if capture
        else None,
        "full_account_verified": capture is not None and (flat or protected),
        "fills_complete": flat or protected,
        "flat_verified": flat,
        "unknown_exposure": not (flat or protected),
        "position_count": 0 if flat else None,
        "position_count_semantics": "nonzero_global_position_rows",
        "pending_order_count": 0 if flat else None,
        "pending_algo_count": 0 if flat else None,
        "entry_order_id": facts.order_id if facts else None,
        "entry_order_state": "canceled"
        if zero
        else "filled"
        if protected or terminal
        else None,
        "entry_filled_contracts": str(facts.filled_contracts)
        if facts and facts.filled_contracts is not None
        else None,
        "protection_verified": protected,
        "protection_algo_id": facts.protection_algo_id if protected else None,
        "protection_client_id": intent.protection_client_id,
        "fixed_fill_economics": economics,
        "failure_code": failure_code,
        "source_fact_codes": list(facts.codes) if facts else None,
    }
    if terminal:
        proof.update(
            exit_kind="original_oco",
            exit_lineage_verified=True,
            exit_order_id=facts.exit_order_id,
            exit_filled_contracts=str(facts.exit_filled_contracts),
            exit_source_sha256=facts.exit_source_sha256,
            protection_algo_id=facts.protection_algo_id,
        )
    if state.get("recovery_history") is not None:
        history = state["recovery_history"]
        proof["recovery_history"] = {
            "intent_recorded_at": history.intent_recorded_at.isoformat(),
            "retained_original_submission_started_utc": history.submission_started_utc.isoformat()
            if history.submission_started_utc is not None
            else None,
            "semantics": "historical_utc_not_current_monotonic_or_post_start",
            "economic_qualification_reestablished": False,
        }
    elif state.get("controlled_close_history") is not None:
        history = state["controlled_close_history"]
        proof["controlled_close_history"] = {
            "intent_recorded_at": history.intent_recorded_at.isoformat(),
            "retained_original_submission_started_utc": history.submission_started_utc.isoformat(),
            "semantics": "original_submission_utc_history_new_close_get_barrier",
        }
    # Frozen dataclass fields are replayed at signing; not a caller passed flag.
    state["assessed_facts"] = facts
    state["assessed_economics"] = economics
    state["capture"] = capture
    state["outcome"] = outcome
    return canonical(proof)


async def reconcile_pending_owned_live_intent_v1(reservation_id):
    """Fixed read-only restart delegate; no Arm/entry/controlled-close action."""
    from app.okx_live import owned_recovery_v1 as recovery

    return await recovery.reconcile_pending_owned_live_intent_v1(reservation_id)


async def reconcile_owned_live_postsubmit_v1(qualified_owner, physical_handle):
    """Consume one genuine spent handoff and reconcile; never resubmit an entry."""
    import secrets

    from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
    from app.domain.source_primitives import ClockStamp
    from app.exchange.okx import native_live_write_v1 as physical
    from app.exchange.okx.live_account_config_observation_v1 import _stamp
    from app.okx_live import owned_qualification_v1 as qualification
    from app.okx_live import reconciliation_facts_v1 as fact_source
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1
    from app.okx_live.postfill_economics_v1 import assess_fixed_live_fill_economics_v1

    handoff = qualification._consume_owned_live_postsubmit_v1(
        qualified_owner, physical_handle, key=_POSTSUBMIT_HANDOFF_KEY_V1
    )
    _need(type(handoff) is qualification.LivePostsubmitHandoffFactsV1)
    facts = handoff.execution_facts
    response = physical._replay_owned_live_response_v1(
        physical_handle, key=physical._QUALIFICATION_SOURCE_KEY_V1
    )
    rid, intent_json, intent_sha = exact_live_intent_document_v1(facts.intent_data)
    _need(rid == facts.reservation_id == response["reservation_id"])
    receipt = _qualified_intent_receipt_v1(facts, rid=rid, intent_sha256=intent_sha)
    _need(
        response["request_sha256"] == facts.request_sha256
        and response["credential_session_sha256"] == facts.credential_session_sha256
    )
    submission = _submission_bytes(response, intent_sha256=intent_sha)
    publish_live_lifecycle_blob_v1(
        handoff.evidence_root, submission, domain="submissions"
    )
    cfg, key_owner, repository = _runtime_repository(handoff.runtime_owner)
    await repository.preflight(key_owner)
    from app.database.repositories.native_live_ledger_v1 import (
        NativeLiveLedgerDataRepositoryV1,
    )

    actual_receipt = await NativeLiveLedgerDataRepositoryV1(
        facts.session_factory, facts.role_pins
    ).read_intent_data_v1(rid, expected_sha256=intent_sha)
    _need(
        actual_receipt == receipt and canonical(actual_receipt) == facts.intent_readback
    )
    journal = await repository.read_journal_v1(key_owner, rid)
    _need(
        journal.intent_json == intent_json and journal.reservation_state != "reconciled"
    )
    _need(
        canonical(decode(handoff.policy_json, maximum=65536)) == handoff.policy_json
        and sha(handoff.policy_json) == facts.intent_data.policy_sha256
    )
    _need(cfg["evidence_root"] == handoff.evidence_root)
    owner = _new_owner(
        runtime_owner=handoff.runtime_owner,
        key_owner=key_owner,
        repository=repository,
        source_owner=facts.source_owner,
        intent=facts.intent_data,
        journal=journal,
        handoff=handoff,
        qualified_owner=qualified_owner,
        evidence_root=handoff.evidence_root,
        report_id=handoff.report_id,
        capture_id=secrets.token_hex(16),
        capture_barrier=_stamp(),
        submission_started=ClockStamp.model_validate(response["started_at"])
        if response["started_at"] is not None
        else None,
        submission_sha256=sha(submission),
        submission_attempted=response["request_attempted"],
    )
    state = _record(owner)
    capture = assessed = economics = None
    failure_code = None
    try:
        _need(
            state["submission_started"] is not None
            and state["submission_attempted"] is True
        )
        capture = await _capture_owned_live_postsubmit_v1(owner)
        assessed = fact_source.assess_live_postsubmit_facts_v1(
            state["intent"], **_capture_arguments(state, capture)
        )
        if assessed.outcome == "FULL_FILL_PROTECTED":
            economics = assess_fixed_live_fill_economics_v1(
                state["intent"],
                assessed,
                capture,
                policy_json=handoff.policy_json,
                recheck_math_json=handoff.recheck_math_json,
            )
    except asyncio.CancelledError:
        await repository.inhibit_for_recovery_v1(key_owner)
        raise
    except Exception:  # noqa: BLE001 -- raw faults remain private; fixed code cannot echo credentials.
        failure_code = "native_live_fresh_reconciliation_incomplete"
    raw = _proof_document(
        state,
        capture=capture,
        facts=assessed,
        economics=economics,
        failure_code=failure_code,
    )
    state["proof_path"] = publish_live_lifecycle_blob_v1(
        handoff.evidence_root, raw, domain="proofs"
    )
    state.update(
        proof_json=raw, source_verified_sha256=sha(raw), phase="source_verified"
    )
    if state["outcome"] == "UNCERTAIN":
        await repository.inhibit_for_recovery_v1(key_owner)
    accepted = await repository.append_owned_proof_v1(owner)
    control = await repository.read_control_v1(key_owner)
    if (
        state["outcome"] == "PROTECTED_HOLD"
        and handoff.controlled_exit_authorized is True
    ):
        return await _controlled_exit_owned_live_v1(owner)
    return _new_receipt(
        accepted, control, current_protection=state["outcome"] == "PROTECTED_HOLD"
    )


async def _controlled_exit_owned_live_v1(owner):
    from app.okx_live.owned_close_v1 import run_owned_live_controlled_close_v1

    return await run_owned_live_controlled_close_v1(owner)


def _qualified_intent_receipt_v1(facts, *, rid, intent_sha256):
    """The inert committed readback receipt is distinct from full intent JSON."""
    import re

    raw = facts.intent_readback
    _need(type(raw) is bytes and 0 < len(raw) <= 4096)
    document = decode(raw, maximum=4096)
    _need(type(document) is dict and canonical(document) == raw)
    _need(
        set(document)
        == {
            "schema",
            "reservation_id",
            "intent_sha256",
            "state",
            "journal_sha256",
            "qualification_verified",
            "execution_authority",
            "admission",
        }
    )
    _need(document["schema"] == "ctcc.native_live.intent_readback_data.v1")
    _need(document["reservation_id"] == rid == facts.reservation_id)
    _need(
        document["intent_sha256"] == intent_sha256 and document["state"] == "reserved"
    )
    _need(
        type(document["journal_sha256"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", document["journal_sha256"]) is not None
    )
    _need(
        document["qualification_verified"] is document["execution_authority"] is False
        and document["admission"] == "DENY"
    )
    return document
