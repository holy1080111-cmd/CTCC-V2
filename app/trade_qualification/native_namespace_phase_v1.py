"""Join a genuine historical cutover to successive fresh native account phases.

The append-only private owner can survive candle waits, while each current
source retains its original deadline. A new append burns the prior live binding
and MOVEs a separately acquired fresh V4/Control/coverage owner. This module
does not claim exchange namespace closure, complete account history, G6 or any
order authority. A new native read lease requires a new flat cutover.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Coroutine
from types import CoroutineType
from weakref import WeakKeyDictionary

from app.database.repositories import controlled_writer_coverage as coverage_rows
from app.database.repositories import native_namespace_phase_v1 as repository
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import controlled_writer_coverage_catalog_v1 as coverage_policy
from app.domain import prospective_event_cutover_v2 as records
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import demo_control as control
from app.trade_qualification import prospective_event_cutover_v2 as cutover

_HOOK_KEY = object()
_OWNERS = WeakKeyDictionary()
MAX_HOPS = 1024
MAX_CHAIN_BYTES = 4 * 1024 * 1024


class OwnedNativeNamespacePhaseV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        records.deny("namespace_phase_owner_not_constructible")

    def __copy__(self):
        records.deny("namespace_phase_owner_not_transferable")

    def __deepcopy__(self, memo):
        records.deny("namespace_phase_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        records.deny("namespace_phase_owner_not_transferable")

    def close(self):
        _release(_OWNERS.pop(self, None))


def _release(state):
    if state is not None:
        binding = state.pop("binding", None)
        if binding is not None:
            coverage._release_namespace_phase_v1(binding, key=_HOOK_KEY)


def _take_previous(owner):
    if type(owner) is cutover.OwnedProspectiveDemoCutoverV2:
        historical = cutover._take_native_namespace_phase_v1(owner, key=_HOOK_KEY)
        return {"historical": historical, "hops": (), "binding": None}
    state = (
        _OWNERS.pop(owner, None) if type(owner) is OwnedNativeNamespacePhaseV1 else None
    )
    if state is None:
        records.deny("namespace_phase_previous_owner_unavailable")
    # Historical succession survives old TTL expiry. No stale source replay is
    # invoked here: the old CURRENT binding is destroyed, never renewed.
    _release(state)
    _historical_context(state["historical"])
    return state


def _historical_context(historical):
    cutover._check_context(historical["context"])
    if any(getattr(module, name) is not function for module, name, function in _PINS):
        records.deny("namespace_phase_function_changed")
    if _policy_digest() != _POLICY_SHA256:
        records.deny("namespace_phase_policy_changed")


def _same_native_lease(historical, state):
    _historical_context(historical)
    original = historical["phase_source"]
    current = state["account"]
    lease = original["read_session"]
    lease.verify_current()
    if (
        current["read_session"] is not lease
        or current["private_client"] is not original["private_client"]
        or current["private_client"]._active_read_session() is not lease
        or state["factory"] is not historical["factory"]
        or state["custody"] is not original["custody"]
        or current["plan_sha256"] == original["source_plan_sha256"]
    ):
        records.deny("namespace_phase_new_lease_requires_new_cutover")
    # Session binding and phase-plan binding are separate facts. The actual
    # credential-session hash is phase-specific; it is NOT a key fingerprint.
    route = json.loads(current["bundle_raw"])["route_proof"]
    original_route = json.loads(original["bundle_raw"])["route_proof"]
    if route["account_identity"] != original_route["account_identity"]:
        records.deny("namespace_phase_exact_account_identity_changed")
    if route["credential_session_sha256"] != records.digest(
        records.canonical(
            {
                "session_binding_id": lease.binding_id,
                "account_plan_sha256": current["plan_sha256"],
            }
        )
    ):
        records.deny("namespace_phase_session_plan_binding_changed")


async def _replay_historical_custody(binding, historical):
    original = historical["phase_source"]
    retained = await coverage._checked_await(
        binding,
        custody.RuntimeBundleCustodyRepository.read_retained_bundle(
            original["custody"],
            bundle_id=original["source_custody_id"],
            expected_sha256=original["source_bundle_sha256"],
            proof_envelope_sha256=original["source_proof_envelope_sha256"],
        ),
    )
    if (
        type(retained) is not custody.RetainedRuntimeBundleReadback
        or retained.bundle_raw != original["bundle_raw"]
        or retained.bundle_id != original["source_custody_id"]
        or retained.bundle_sha256 != original["source_bundle_sha256"]
        or retained.proof_envelope_sha256 != original["source_proof_envelope_sha256"]
    ):
        records.deny("namespace_phase_historical_source_custody_changed")
    # This replay checks what was measured within the ORIGINAL capture window;
    # _check_current(..., now) is deliberately not applied to historical data.
    replay = account.replay_current_account_bundle_v4(
        retained.bundle_raw, expected_sha256=retained.bundle_sha256
    )
    if replay.reference.plan_sha256 != original["source_plan_sha256"]:
        records.deny("namespace_phase_historical_source_plan_changed")


def _join(previous, state, observed):
    historical, hops = previous["historical"], previous["hops"]
    if type(hops) is not tuple or len(hops) >= MAX_HOPS:
        records.deny("namespace_phase_chain_bound")
    old = historical["active"] if not hops else hops[-1]["active"]
    fresh = observed.coverage
    if (
        fresh.state != "active"
        or fresh.scope != old.scope
        or fresh.generation != old.generation + 1
        or fresh.journal_sequence != old.journal_sequence + 3
        or fresh.records_json != state["active"].records_json
    ):
        records.deny("namespace_phase_missing_or_duplicate_coverage_generation")
    snapshot = json.loads(fresh.records_json)
    anchor = snapshot["previous_terminal_anchor"]
    if anchor is None:
        records.deny("namespace_phase_previous_terminal_anchor_missing")
    revoked = json.loads(anchor["state_json"])
    old_state, new_state = old.state_document, fresh.state_document
    if (
        anchor["journal_sequence"] != old.journal_sequence + 1
        or anchor["previous_sha256"] != old.journal_sha256
        or anchor["action"] != "revoked"
        or anchor["epoch_id"] != old.epoch_id
        or anchor["generation"] != old.generation
        or anchor["audit_json"] != old.audit_json
        or any(
            records.canonical(revoked[name]) != records.canonical(value)
            for name, value in old_state.items()
            if name not in coverage_policy._MUTABLE_TRANSITION_FIELDS
        )
        or revoked["transition_stamp"] != old_state["transition_stamp"]
        or any(
            old_state[name] != new_state[name]
            for name in (
                "account_id",
                "identity_sha256",
                "deployed_manifest_sha256",
                "writer_inventory_sha256",
            )
        )
        or old_state["source_plan_sha256"] == new_state["source_plan_sha256"]
    ):
        records.deny("namespace_phase_coverage_predecessor_changed")
    prior_keys = historical["event_keys"] if not hops else hops[-1]["scan_keys"]
    scan_document = json.loads(observed.scan.document_json)
    if (
        type(observed.scan.consumed_event_keys) is not frozenset
        or not prior_keys <= observed.scan.consumed_event_keys
        or scan_document["event_count"] != len(observed.scan.consumed_event_keys)
        or scan_document["event_keys_sha256"]
        != records.digest(records.canonical(sorted(observed.scan.consumed_event_keys)))
    ):
        records.deny("namespace_phase_permanent_event_tombstone_missing")
    lineage = observed.lineage
    if type(lineage) is not control.ControlLineageV1:
        records.deny("namespace_phase_original_control_lineage_required")
    rows = lineage.source_rows
    old_revision = old_state["control_revision"]
    if (
        lineage.scope != fresh.scope
        or lineage.state.revision != new_state["control_revision"]
        or lineage.head_sha256 != new_state["control_event_sha256"]
        or old_revision >= len(rows)
        or rows[old_revision - 1].event_sha256 != old_state["control_event_sha256"]
        or rows[old_revision - 1].state_sha256 != old_state["control_state_sha256"]
    ):
        records.deny("namespace_phase_control_coverage_binding_changed")
    if (
        hops
        and tuple(row.event_json for row in rows[: len(hops[-1]["control_rows"])])
        != hops[-1]["control_rows"]
    ):
        records.deny("namespace_phase_control_prefix_changed")
    for row in rows[old_revision - 1 :]:
        value = control.decode(control.canonical(json.loads(row.event_json)["state"]))
        if not value.emergency_stop or value.arm_request_id is not None:
            records.deny("namespace_phase_non_stopped_control_succession")
    revocation_control = revoked["revocation_observed_control"]
    revision = revocation_control["revision"]
    if type(revision) is not int or not old_revision <= revision <= len(rows):
        records.deny("namespace_phase_revocation_control_missing")
    row = rows[revision - 1]
    value = control.decode(control.canonical(json.loads(row.event_json)["state"]))
    if records.canonical(revocation_control) != records.canonical(
        {
            "revision": value.revision,
            "owner_epoch": value.owner_epoch,
            "event_sha256": row.event_sha256,
            "state_sha256": row.state_sha256,
            "emergency_stop": value.emergency_stop,
            "arm_request_id": value.arm_request_id,
        }
    ):
        records.deny("namespace_phase_revocation_control_conflict")
    next_hop = {
        "active": fresh,
        "control_rows": tuple(row.event_json for row in rows),
        "scan_json": observed.scan.document_json,
        "scan_keys": observed.scan.consumed_event_keys,
        "source_plan_sha256": state["template"]["source_plan_sha256"],
        "credential_session_sha256": state["template"]["credential_session_sha256"],
    }
    total = sum(
        len(hop["active"].records_json)
        + len(hop["scan_json"])
        + sum(map(len, hop["control_rows"]))
        for hop in (*hops, next_hop)
    )
    if total > MAX_CHAIN_BYTES:
        records.deny("namespace_phase_chain_bound")
    return (*hops, next_hop)


class _PhaseAttempt(Coroutine):
    __slots__ = ("_binding", "_closed", "_inner", "_outer")

    def __init__(self, outer, inner, binding):
        self._outer, self._inner, self._binding, self._closed = (
            outer,
            inner,
            binding,
            False,
        )

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self._outer.send(value)
        except BaseException:
            self.close()
            raise

    def throw(self, typ, val=None, tb=None):
        try:
            return self._outer.throw(typ, val, tb)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._outer.close()
            finally:
                try:
                    self._inner.close()
                finally:
                    if self._binding is not None:
                        coverage._release_namespace_phase_v1(
                            self._binding, key=_HOOK_KEY
                        )

    def __del__(self):
        try:
            self.close()
        except BaseException:  # noqa: BLE001, S110 -- interpreter cleanup cannot log.
            pass


def join_owned_native_namespace_phase_v1(previous_owner, current_coverage_owner):
    """MOVE both genuine owners at CALL TIME, then read actual native state once.

    Obtain current_coverage_owner using the existing native V4 coverage issuer,
    after exact native safety revocation of its predecessor. This entry never
    guesses a source, modifies account settings, renews old authority, or sends
    exchange writes. A successful append can itself be MOVEd into another append.
    """
    if any(getattr(module, name) is not function for module, name, function in _PINS):
        records.deny("namespace_phase_function_changed")
    previous = _take_previous(previous_owner)
    binding = None
    try:
        binding = coverage._take_namespace_phase_v1(
            current_coverage_owner, key=_HOOK_KEY
        )
        state, _ = coverage._namespace_phase_state_v1(binding, key=_HOOK_KEY)
        _same_native_lease(previous["historical"], state)
        native = repository.NativeNamespacePhaseRepositoryV1(state["factory"])
        inner = native.read_native(binding, previous["historical"], key=_HOOK_KEY)
        if type(inner) is not CoroutineType:
            records.deny("namespace_phase_fixed_native_awaitable_required")
    except BaseException:
        if binding is not None:
            coverage._release_namespace_phase_v1(binding, key=_HOOK_KEY)
        raise

    async def finish():
        issued, owner = False, None
        try:
            await _replay_historical_custody(binding, previous["historical"])
            observed = await inner
            if any(
                getattr(module, name) is not function
                for module, name, function in _PINS
            ):
                records.deny("namespace_phase_function_changed")
            state, _ = coverage._namespace_phase_state_v1(binding, key=_HOOK_KEY)
            _same_native_lease(previous["historical"], state)
            if type(observed) is not repository._NativePhaseReadback:
                records.deny("namespace_phase_original_native_readback_required")
            hops = _join(previous, state, observed)
            owner = object.__new__(OwnedNativeNamespacePhaseV1)
            _OWNERS[owner] = {
                "historical": previous["historical"],
                "hops": hops,
                "binding": binding,
            }
            coverage._namespace_phase_state_v1(binding, key=_HOOK_KEY)
            _same_native_lease(previous["historical"], state)
            # The returned owner is historical custody plus an unextended fresh
            # source binding, not execution authority. A future actual namespace
            # acquisition must MOVE that binding and complete its own finality.
            if any(
                getattr(module, name) is not function
                for module, name, function in _PINS
            ):
                records.deny("namespace_phase_function_changed")
            issued = True
            wrapper._binding = None
            return owner
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- redact account/source/native driver details.
            records.deny("namespace_phase_join_failed")
        finally:
            inner.close()
            if not issued:
                if owner is not None:
                    _OWNERS.pop(owner, None)
                coverage._release_namespace_phase_v1(binding, key=_HOOK_KEY)

    wrapper = _PhaseAttempt(finish(), inner, binding)
    # Successful StopIteration must not release the binding now owned by result.
    # finish sets this only after its final synchronous original-source fence.
    return wrapper


def _policy_digest():
    return records.digest(
        records.canonical(
            {
                "version": "ctcc.native_namespace_phase.v1",
                "max_hops": MAX_HOPS,
                "max_chain_bytes": MAX_CHAIN_BYTES,
                "mutable_fields": sorted(coverage_policy._MUTABLE_TRANSITION_FIELDS),
                "scan_stable_fields": repository._STABLE_SCAN_FIELDS,
                "new_read_lease": "new_cutover_required",
                "exchange_history_complete": False,
                "g6_authority": False,
                "execution_authority": False,
            }
        )
    )


_POLICY_SHA256 = _policy_digest()
_PINS = (
    (sys.modules[__name__], "_historical_context", _historical_context),
    (sys.modules[__name__], "_take_previous", _take_previous),
    (sys.modules[__name__], "_release", _release),
    (
        cutover,
        "_take_native_namespace_phase_v1",
        cutover._take_native_namespace_phase_v1,
    ),
    (sys.modules[__name__], "_policy_digest", _policy_digest),
    (
        sys.modules[__name__],
        "join_owned_native_namespace_phase_v1",
        join_owned_native_namespace_phase_v1,
    ),
    (_PhaseAttempt, "send", _PhaseAttempt.send),
    (_PhaseAttempt, "throw", _PhaseAttempt.throw),
    (_PhaseAttempt, "close", _PhaseAttempt.close),
    (sys.modules[__name__], "_join", _join),
    (sys.modules[__name__], "_same_native_lease", _same_native_lease),
    (sys.modules[__name__], "_replay_historical_custody", _replay_historical_custody),
    (coverage, "_take_namespace_phase_v1", coverage._take_namespace_phase_v1),
    (coverage, "_namespace_phase_state_v1", coverage._namespace_phase_state_v1),
    (coverage, "_release_namespace_phase_v1", coverage._release_namespace_phase_v1),
    (coverage, "_checked_await", coverage._checked_await),
    (
        coverage_rows.ControlledWriterCoverageRepository,
        "_native_locked",
        coverage_rows.ControlledWriterCoverageRepository._native_locked,
    ),
    (coverage_rows, "_observation", coverage_rows._observation),
    (
        DemoControlRepository,
        "_read_lineage_prefix",
        DemoControlRepository._read_lineage_prefix,
    ),
    (
        QualificationLedgerRepository,
        "_inspect_prospective_event_journal_locked",
        QualificationLedgerRepository._inspect_prospective_event_journal_locked,
    ),
    (
        repository.NativeNamespacePhaseRepositoryV1,
        "read_native",
        repository.NativeNamespacePhaseRepositoryV1.read_native,
    ),
    (
        repository.NativeNamespacePhaseRepositoryV1,
        "_read_once",
        repository.NativeNamespacePhaseRepositoryV1._read_once,
    ),
    (
        account,
        "replay_current_account_bundle_v4",
        account.replay_current_account_bundle_v4,
    ),
    (
        custody.RuntimeBundleCustodyRepository,
        "read_retained_bundle",
        custody.RuntimeBundleCustodyRepository.read_retained_bundle,
    ),
)
