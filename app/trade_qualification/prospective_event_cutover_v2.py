"""Move a genuine native coverage owner into durable prospective start custody.

The returned private owner carries only historical cutover custody. No G6
consumer exists until native CTCC namespace history can be proved complete.
No public DTO or supplied passed/flat/history flag can mint this owner.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Coroutine
from threading import get_ident
from types import CoroutineType
from weakref import WeakKeyDictionary, ref

from app.database.repositories import prospective_event_cutover_v2 as repository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.trade_qualification import controlled_writer_coverage_v1 as coverage

_HOOK_KEY = object()
_OWNERS = WeakKeyDictionary()


class OwnedProspectiveDemoCutoverV2:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        records.deny("prospective_cutover_owner_not_constructible")

    def __copy__(self):
        records.deny("prospective_cutover_owner_not_transferable")

    def __deepcopy__(self, memo):
        records.deny("prospective_cutover_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        records.deny("prospective_cutover_owner_not_transferable")

    def close(self):
        if type(self) is OwnedProspectiveDemoCutoverV2:
            _OWNERS.pop(self, None)


def _context():
    task = asyncio.current_task()
    if task is None or task.cancelling():
        records.deny("prospective_cutover_context")
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _check_context(expected):
    actual = _context()
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        records.deny("prospective_cutover_context_changed")
    if any(getattr(module, name) is not function for module, name, function in _PINS):
        records.deny("prospective_cutover_function_changed")
    if _policy_digest() != _POLICY_SHA256:
        records.deny("prospective_cutover_policy_changed")


class _CutoverAttempt(Coroutine):
    """Fixed awaitable owns cleanup even when discarded before first await."""

    __slots__ = ("_binding", "_closed", "_coroutine", "_inner")

    def __init__(self, coroutine, inner, binding):
        self._coroutine = coroutine
        self._inner = inner
        self._binding = binding
        self._closed = False

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self._coroutine.send(value)
        except BaseException:
            # This includes normal StopIteration and failures before finish's
            # first instruction. Terminal delivery closes both awaitables and
            # burns the binding immediately, even if this wrapper is retained.
            self.close()
            raise

    def throw(self, typ, val=None, tb=None):
        try:
            return self._coroutine.throw(typ, val, tb)
        except BaseException:
            # An unstarted coroutine never enters its own try/finally. The
            # wrapper, rather than GC or that unreachable finally, owns cleanup.
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._coroutine.close()
            finally:
                try:
                    self._inner.close()
                finally:
                    coverage._release_prospective_cutover_v2(
                        self._binding, key=_HOOK_KEY
                    )

    def __del__(self):
        try:
            self.close()
        except BaseException:  # noqa: BLE001, S110 -- interpreter cleanup cannot safely log.
            # No logging during interpreter/GC cleanup; no native request or DB
            # transaction starts from this finalizer.
            pass


def commit_prospective_event_cutover_v2(coverage_owner):
    """Burn the original native owner at CALL time, then commit/read back once."""
    # The original registry/type/context/source guards perform the MOVE. Passing
    # a saved observation or directly constructed object cannot satisfy it.
    binding = coverage._take_prospective_cutover_v2(coverage_owner, key=_HOOK_KEY)
    try:
        context = _context()
        expected = (*context[:3], ref(context[3]))
        _check_context(expected)
        state, _ = coverage._prospective_cutover_state_v2(binding, key=_HOOK_KEY)
        native_repository = repository.NativeProspectiveCutoverRepository(
            state["factory"]
        )
        attempt = native_repository.append_native(binding, key=_HOOK_KEY)
        if type(attempt) is not CoroutineType:
            records.deny("prospective_cutover_fixed_awaitable_required")
    except BaseException:
        coverage._release_prospective_cutover_v2(binding, key=_HOOK_KEY)
        raise

    async def finish():
        issued = False
        owner = None
        try:
            _check_context(expected)
            observed = await attempt
            _check_context(expected)
            state, _ = coverage._prospective_cutover_state_v2(binding, key=_HOOK_KEY)
            if type(observed) is not repository._CutoverReadback:
                records.deny("prospective_cutover_native_readback_required")
            value = records.decode(observed.record_json)
            active = state["active"]
            if (
                observed.record_sha256 != records.digest(observed.record_json)
                or value["active_event_sha256"] != active.journal_sha256
                or value["active_state_sha256"] != active.state_sha256
                or value["epoch_id"] != active.epoch_id
                or value["account_id"] != state["ledger_scope"].account_id
                or value["audit_sha256"] != active.audit_sha256
                or type(observed.event_keys) is not frozenset
                or records.digest(records.canonical(sorted(observed.event_keys)))
                != value["event_keys_sha256"]
                or len(observed.event_keys)
                != json.loads(value["journal_scan_json"])["event_count"]
            ):
                records.deny("prospective_cutover_native_lineage_changed")
            owner = object.__new__(OwnedProspectiveDemoCutoverV2)
            _OWNERS[owner] = {
                "context": expected,
                "factory": state["factory"],
                "record": observed,
                "epoch_id": active.epoch_id,
                "coverage_event_sha256": active.journal_sha256,
                "active": active,
                "event_keys": observed.event_keys,
                "phase_source": {
                    "read_session": state["account"]["read_session"],
                    "private_client": state["account"]["private_client"],
                    "custody": state["custody"],
                    "bundle_raw": state["account"]["bundle_raw"],
                    **{
                        name: state["template"][name]
                        for name in (
                            "source_plan_sha256",
                            "source_custody_id",
                            "source_bundle_sha256",
                            "source_proof_envelope_sha256",
                        )
                    },
                },
            }
            # No awaits after this source/function/context fence and issuance.
            coverage._prospective_cutover_state_v2(binding, key=_HOOK_KEY)
            _check_context(expected)
            issued = True
            return owner
        finally:
            # A native append may already have committed. Cleanup releases
            # only process ownership; immutable DB cutover rows remain.
            if not issued and owner is not None:
                _OWNERS.pop(owner, None)
            attempt.close()
            coverage._release_prospective_cutover_v2(binding, key=_HOOK_KEY)

    return _CutoverAttempt(finish(), attempt, binding)


def _policy_digest():
    return records.digest(
        records.canonical(
            {
                "ddl": catalog.ddl_identity(),
                "catalog_query": catalog.CATALOG_SQL,
                "native_baseline": catalog.PG17_DEPARSED_BASELINE,
                "native_baseline_status": catalog.PG17_DEPARSED_BASELINE_STATUS,
                "columns": catalog.COLUMNS,
                "constraints": catalog.CONSTRAINTS,
                "fields": sorted(records.FIELDS),
                "false_fields": records.FALSE_FIELDS,
                "namespace": records.NAMESPACE_POLICY,
                "max_bytes": records.MAX_BYTES,
            }
        )
    )


_POLICY_SHA256 = _policy_digest()


_PINS = (
    (coverage, "_take_prospective_cutover_v2", coverage._take_prospective_cutover_v2),
    (coverage, "_prospective_cutover_state_v2", coverage._prospective_cutover_state_v2),
    (
        coverage,
        "_release_prospective_cutover_v2",
        coverage._release_prospective_cutover_v2,
    ),
    (coverage, "_checked_await", coverage._checked_await),
    (
        repository.NativeProspectiveCutoverRepository,
        "append_native",
        repository.NativeProspectiveCutoverRepository.append_native,
    ),
    (
        repository.NativeProspectiveCutoverRepository,
        "_append_native",
        repository.NativeProspectiveCutoverRepository._append_native,
    ),
    (
        repository.NativeProspectiveCutoverRepository,
        "_settings",
        repository.NativeProspectiveCutoverRepository._settings,
    ),
    (
        repository.NativeProspectiveCutoverRepository,
        "_operands_locked",
        repository.NativeProspectiveCutoverRepository._operands_locked,
    ),
    (
        repository.NativeProspectiveCutoverRepository,
        "_record",
        repository.NativeProspectiveCutoverRepository._record,
    ),
    (
        QualificationLedgerRepository,
        "_inspect_prospective_event_journal_locked",
        QualificationLedgerRepository._inspect_prospective_event_journal_locked,
    ),
    (
        QualificationLedgerRepository,
        "_inspect_event_journal_rows_locked",
        QualificationLedgerRepository._inspect_event_journal_rows_locked,
    ),
    (
        QualificationLedgerRepository,
        "_bootstrap_checkpoint",
        QualificationLedgerRepository._bootstrap_checkpoint,
    ),
    (QualificationLedgerRepository, "_locked", QualificationLedgerRepository._locked),
    (catalog, "verify_catalog", catalog.verify_catalog),
    (catalog, "verify_runtime_role", catalog.verify_runtime_role),
    (records, "decode", records.decode),
    (records, "canonical", records.canonical),
    (records, "digest", records.digest),
)


def _take_native_namespace_phase_v1(owner, *, key):
    """Historical MOVE only; never revalidate or extend the old current TTL."""
    from app.trade_qualification.native_namespace_phase_v1 import _HOOK_KEY

    if key is not _HOOK_KEY:
        records.deny("prospective_cutover_namespace_phase_owner_required")
    held = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedProspectiveDemoCutoverV2
        else None
    )
    if held is None:
        records.deny("prospective_cutover_owner_unavailable")
    _check_context(held["context"])
    original = held["phase_source"]
    lease = original["read_session"]
    lease.verify_current()
    if original["private_client"]._active_read_session() is not lease:
        records.deny("prospective_cutover_native_read_lease_changed")
    return held
