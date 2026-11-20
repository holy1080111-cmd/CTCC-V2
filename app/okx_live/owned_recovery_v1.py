"""Explicit stopped Live GET recovery; no old Arm, lease or submit authority.

The durable intent's recorded UTC is a historical lower bound. It is never
labelled a POST start, and no old process monotonic clock is made current.
"""

import asyncio
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from weakref import WeakKeyDictionary

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns

_SCOPES = WeakKeyDictionary()


class LiveRecoveryError(ValueError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveRecoveryError("native_live_readonly_recovery_unavailable") from None


@dataclass(frozen=True, slots=True)
class _RecoveryScope:
    runtime_owner: object
    key_owner: object
    journal: object
    settings: object
    source_root: object
    evidence_root: object
    region: str


def _historical_times(journal):
    from app.okx_live.reconciliation_facts_v1 import LiveReconciliationHistoryV1

    recorded = journal.intent_recorded_at
    _need(type(recorded) is datetime and recorded.tzinfo is UTC)
    _need(recorded <= journal.intent.deadline)
    original = None
    for raw in journal.proofs:
        document = decode(raw, maximum=65536)
        value = document.get("submission_started")
        if value is None:
            continue
        _need(type(value) is dict and set(value) == {"utc_ns", "monotonic_ns"})
        _need(type(value["utc_ns"]) is int and value["utc_ns"] > 0)
        _need(type(value["monotonic_ns"]) is int and value["monotonic_ns"] > 0)
        observed = utc_from_ns(value["utc_ns"])
        _need(recorded <= observed <= journal.intent.deadline)
        _need(original is None or original == observed)
        original = observed
    return LiveReconciliationHistoryV1(recorded, original)


def _retained_report_id(journal):
    result = None
    for raw in journal.proofs:
        report = decode(raw, maximum=65536).get("report_id")
        _need(report is None or type(report) is str and 0 < len(report) <= 128)
        if report is not None:
            _need(result is None or result == report)
            result = report
    return result


def _settings(region, intent):
    from app.config.settings import Settings, get_settings
    from app.okx_live.micro_live_readonly_preflight_v1 import _off_settings

    settings = get_settings()
    _need(type(settings) is Settings and settings.trading_mode == "live")
    off = _off_settings(settings)
    _need(region in ("global", "eea"))
    _need(
        off.origin
        == {"global": "https://openapi.okx.com", "eea": "https://eea.okx.com"}[region]
    )
    _need(settings.okx_live_read_max_retries == 0)
    _need(settings.okx_live_expected_uid.get_secret_value() == intent.account_uid)
    _need(settings.okx_live_expected_main_uid.get_secret_value() == intent.main_uid)
    _need(intent.instrument_id in off.instruments)
    return settings


def _require_stopped(control):
    _need(
        type(control) is dict
        and control.get("armed") is False
        and control.get("disarmed") is True
        and control.get("emergency_stop") is True
        and type(control.get("revision")) is int
        and control["revision"] > 0
    )


def _scope(owner):
    from app.okx_live import lifecycle_key_v1 as keys
    from app.okx_live import native_runtime_v1 as runtime
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    state = lifecycle._record(owner)
    scope = _SCOPES.get(owner)
    _need(type(scope) is _RecoveryScope)
    cfg = runtime._replay_live_runtime_v1(scope.runtime_owner, key=runtime._KEY)
    _need(
        state["runtime_owner"] is scope.runtime_owner
        and state["key_owner"] is scope.key_owner
    )
    _need(state["journal"] is scope.journal and state["intent"] is scope.journal.intent)
    _need(state["source_root"] == scope.source_root == cfg["source_root"])
    _need(state["evidence_root"] == scope.evidence_root == cfg["evidence_root"])
    _need(cfg["region"] == scope.region)
    metadata = keys.lifecycle_key_metadata_v1(scope.key_owner)
    _need(
        metadata["environment"] == "live"
        and metadata["account_uid"] == state["intent"].account_uid
        and metadata["main_uid"] == state["intent"].main_uid
        and metadata["origin"]
        == {"global": "https://openapi.okx.com", "eea": "https://eea.okx.com"}[
            scope.region
        ]
    )
    _need(_settings(scope.region, state["intent"]) is scope.settings)
    _need(state["recovery_history"] == _historical_times(scope.journal))
    _need(state["report_id"] == _retained_report_id(scope.journal))
    _require_stopped(state["recovery_control"])
    return state, scope


def _require_recovery_for_signing_v1(owner, *, key):
    from app.exchange.okx.live_account_config_observation_v1 import (
        verify_live_account_config_readback_v1,
    )
    from app.exchange.okx.live_account_current_chain_v1 import _check_config
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1

    _need(key is lifecycle._SOURCE_LEASE_KEY_V1)
    state, scope = _scope(owner)
    lease = state["lease"]
    lease.verify_current()
    _need(lease._settings is scope.settings and state["client"] is lease._owner)
    first = state["recovery_config"]
    verify_live_account_config_readback_v1(first, lease=lease)
    _need(first.synthetic_mock is False)
    identity = _check_config(first.raw_body)
    _need(
        identity.uid == state["intent"].account_uid
        and identity.main_uid == state["intent"].main_uid
    )
    _need(
        first.origin == lease._pin.origin and first.lease_binding_id == lease.binding_id
    )
    retained = [
        decode(
            read_live_lifecycle_blob_v1(state["evidence_root"], item[1], domain="raw"),
            maximum=12 * 1024 * 1024,
        )
        for item in state["raw_captures"]
    ]
    _need(
        any(
            item["raw_body_sha256"] == first.raw_sha256
            and item["request_signature_sha256"] == first.request_signature_sha256
            and item["stamps"] == [stamp.model_dump() for stamp in first.stamps[:5]]
            and item["origin"] == first.origin
            and item["lease_binding_id"] == lease.binding_id
            for item in retained
        )
    )
    _need(
        state["credential_session_sha256"]
        == sha(
            canonical(
                [
                    "live",
                    lease.binding_id,
                    identity.uid,
                    identity.main_uid,
                    lease._pin.origin,
                ]
            )
        )
    )
    _need(state["submission_started"] is None)
    _need(state["credential_continuity"] == "fresh_recovery_lease_uid_reverified")


def _submission_observation(state, capture, facts):
    """Truthful evidence of an existing order, never a reconstructed response."""
    from app.domain.native_live_intent_v1 import exact_live_intent_document_v1

    rid, _, digest = exact_live_intent_document_v1(state["intent"])
    return canonical(
        {
            "schema": "ctcc.native_live.recovery_submission_observation.v1",
            "semantics": "fresh_exact_exchange_order_not_original_http_submission",
            "reservation_id": rid,
            "intent_sha256": digest,
            "order_receipt_sha256": capture.order.receipt_sha256,
            "order_raw_sha256": capture.order.raw_sha256,
            "order_id": facts.order_id,
            "intent_recorded_at": state[
                "recovery_history"
            ].intent_recorded_at.isoformat(),
            "original_submission_started": None,
            "execution_authority": False,
        }
    )


def _verify_recovery_observation_v1(owner, *, key):
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1

    _need(key is lifecycle._SOURCE_LEASE_KEY_V1)
    _require_recovery_for_signing_v1(owner, key=key)
    state, _ = _scope(owner)
    capture, facts = state["capture"], state["assessed_facts"]
    if capture is None:
        _need(
            state["outcome"] == "UNCERTAIN"
            and state["submission_attempted"] is False
            and state["submission_sha256"] is None
        )
        return
    raw = _submission_observation(state, capture, facts)
    _need(sha(raw) == state["submission_sha256"])
    _need(
        read_live_lifecycle_blob_v1(
            state["evidence_root"], sha(raw), domain="submissions"
        )
        == raw
    )
    _need(state["submission_attempted"] is (facts.order_id is not None))


async def _recover_with_scope(owner):
    from app.exchange.okx.live_account_config_observation_v1 import (
        _stamp,
        observe_live_account_config_v1,
        verify_live_account_config_readback_v1,
    )
    from app.exchange.okx.live_account_current_chain_v1 import _check_config
    from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import postsubmit_capture_v1 as capture_source
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1
    from app.okx_live.reconciliation_facts_v1 import assess_live_postsubmit_facts_v1

    state, scope = _scope(owner)
    client = OkxLivePinnedGetClientV1(settings=scope.settings)
    with client.pinned_get_lease() as lease:
        state.update(
            client=client, lease=lease, phase="capturing", capture_barrier=_stamp()
        )
        token = lifecycle._ACTIVE_CAPTURE.set(owner)
        try:
            # Verify actual UID/mainUid, permissions and IP before order reads.
            first = await observe_live_account_config_v1(client, lease)
            verify_live_account_config_readback_v1(first, lease=lease)
            identity = _check_config(first.raw_body)
            _need(
                first.synthetic_mock is False
                and identity.uid == state["intent"].account_uid
                and identity.main_uid == state["intent"].main_uid
            )
            state.update(
                recovery_config=first,
                credential_session_sha256=sha(
                    canonical(
                        [
                            "live",
                            lease.binding_id,
                            identity.uid,
                            identity.main_uid,
                            lease._pin.origin,
                        ]
                    )
                ),
            )
            capture = assessed = None
            failure = None
            try:
                capture = await capture_source.capture_owned_live_postsubmit_pages_v1(
                    owner, key=capture_source._CAPTURE_KEY_V1
                )
                assessed = assess_live_postsubmit_facts_v1(
                    state["intent"], **lifecycle._capture_arguments(state, capture)
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- no private response or exception text escapes.
                failure = "native_live_fresh_recovery_incomplete"
            if capture is not None:
                observation = _submission_observation(state, capture, assessed)
                publish_live_lifecycle_blob_v1(
                    scope.evidence_root, observation, domain="submissions"
                )
                state.update(
                    submission_sha256=sha(observation),
                    submission_attempted=assessed.order_id is not None,
                )
            raw = lifecycle._proof_document(
                state,
                capture=capture,
                facts=assessed,
                economics=None,
                failure_code=failure,
            )
            state.update(
                proof_path=publish_live_lifecycle_blob_v1(
                    scope.evidence_root, raw, domain="proofs"
                ),
                proof_json=raw,
                source_verified_sha256=sha(raw),
                phase="source_verified",
            )
            accepted = await state["repository"].append_owned_proof_v1(owner)
            control = await state["repository"].read_control_v1(scope.key_owner)
            _require_stopped(control)
            return lifecycle._new_receipt(
                accepted,
                control,
                current_protection=state["outcome"] == "PROTECTED_HOLD",
            )
        finally:
            lifecycle._ACTIVE_CAPTURE.reset(token)


async def _reconcile_pending_v1(reservation_id):
    from app.okx_live import native_runtime_v1 as runtime
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(
        type(reservation_id) is str
        and re.fullmatch(r"[a-f0-9]{64}", reservation_id) is not None
    )
    owner = None
    async with runtime.configured_live_runtime_v1() as runtime_owner:
        cfg, key_owner, repository = lifecycle._runtime_repository(runtime_owner)
        await repository.preflight(key_owner)
        await repository.inhibit_for_recovery_v1(key_owner)
        # Independent committed readback precedes creating the HTTP client.
        control = await repository.read_control_v1(key_owner)
        _require_stopped(control)
        journal = await repository.read_journal_v1(key_owner, reservation_id)
        _need(journal.reservation_id == reservation_id)
        if journal.reservation_state == "reconciled":
            return lifecycle._new_receipt(journal, control)
        _need(journal.reservation_state in ("reserved", "uncertain"))
        settings = _settings(cfg["region"], journal.intent)
        history = _historical_times(journal)
        try:
            owner = lifecycle._new_owner(
                runtime_owner=runtime_owner,
                key_owner=key_owner,
                repository=repository,
                source_owner=None,
                intent=journal.intent,
                journal=journal,
                handoff=None,
                evidence_root=cfg["evidence_root"],
                source_root=cfg["source_root"],
                report_id=_retained_report_id(journal),
                capture_id=secrets.token_hex(16),
                submission_started=None,
                submission_sha256=None,
                submission_attempted=False,
                credential_continuity="fresh_recovery_lease_uid_reverified",
                recovery_history=history,
                recovery_control=control,
            )
            _SCOPES[owner] = _RecoveryScope(
                runtime_owner,
                key_owner,
                journal,
                settings,
                cfg["source_root"],
                cfg["evidence_root"],
                cfg["region"],
            )
            return await _recover_with_scope(owner)
        finally:
            if owner is not None:
                _SCOPES.pop(owner, None)
                lifecycle._OWNERS.pop(owner, None)


async def reconcile_pending_owned_live_intent_v1(reservation_id):
    """An explicit operator read/reconcile action; never an entry or retry."""
    try:
        return await _reconcile_pending_v1(reservation_id)
    except asyncio.CancelledError:
        raise
    except LiveRecoveryError:
        raise
    except Exception:  # noqa: BLE001 -- SQL/provider/private paths never escape.
        raise LiveRecoveryError("native_live_readonly_recovery_unavailable") from None
