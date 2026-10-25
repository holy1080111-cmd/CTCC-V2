"""Native source-owned cutover commit and independent exact readback.

Reuses original DB0048/49 activation, B5 audit and DB0017/23 event rows. A
successful cutover is historical scope custody, never a G6/execution permit.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from app.domain import account_history_partition_catalog_v1 as history_catalog
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.domain.source_primitives import utc_from_ns, validate_stamps
from app.trade_qualification import controlled_writer_coverage_v1 as coverage

_STABLE_SCAN_FIELDS = (
    "scope_sha256",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "event_count",
    "transition_count",
    "state_counts",
    "event_keys_sha256",
    "rows_sha256",
    "transitions_sha256",
)


@dataclass(frozen=True, slots=True, repr=False)
class _CutoverReadback:
    """Internal exact DB read; constructing this record creates no owner."""

    record_json: bytes
    record_sha256: str
    recorded_at: datetime
    coverage_start_at: datetime
    event_keys: frozenset[str]


def _scan_document(scan):
    raw = scan.document_json
    if type(raw) is not bytes or not 1 <= len(raw) <= 4096:
        records.deny("prospective_cutover_scan_bound")
    value = json.loads(raw)
    if records.canonical(value) != raw:
        records.deny("prospective_cutover_scan_noncanonical")
    return value


class NativeProspectiveCutoverRepository:
    def __init__(self, factory):
        self._factory = factory
        self._attempted = False

    def append_native(self, binding, *, key):
        """Burn the repository attempt synchronously, before any await starts."""
        if self._attempted:
            records.deny("prospective_cutover_attempt_burned")
        self._attempted = True
        state, _ = coverage._prospective_cutover_state_v2(binding, key=key)
        if state["factory"] is not self._factory:
            records.deny("prospective_cutover_factory_changed")
        return self._append_native(binding, key=key)

    async def _settings(self, binding, session, key):
        state, _ = coverage._prospective_cutover_state_v2(binding, key=key)
        if state["factory"] is not self._factory:
            records.deny("prospective_cutover_factory_changed")
        for sql in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='1000ms'",
        ):
            await coverage._checked_await(binding, session.execute(text(sql)))
        # 0050 declarations and its existing genuine seal are not changed.
        parent = await coverage._checked_await(
            binding, session.scalar(text(history_catalog.CATALOG_SQL))
        )
        history_catalog.verify_account_history_partition_catalog(parent)
        observed = await coverage._checked_await(
            binding, session.scalar(text(catalog.CATALOG_SQL))
        )
        catalog.verify_catalog(observed)
        catalog.verify_runtime_role(observed)

    async def _operands_locked(self, binding, session, key):
        state, _ = coverage._prospective_cutover_state_v2(binding, key=key)
        active = state["active"]
        held, _, audit, _ = await coverage._checked_await(
            binding,
            state["repository"]._native_locked(
                session,
                binding,
                coverage._HOOK_KEY,
                (active.journal_sequence, active.journal_sha256),
            ),
        )
        if (
            audit.document_json.decode("ascii") != active.audit_json
            or audit.state_sha256 != active.audit_sha256
            or not audit.known_flat
        ):
            records.deny("prospective_cutover_local_audit_changed")
        selected = await coverage._checked_await(
            binding, held["ledger"]._locked(session, held["ledger_scope"])
        )
        scan = await coverage._checked_await(
            binding,
            held["ledger"]._inspect_prospective_event_journal_locked(
                session,
                held["ledger_scope"],
                selected,
            ),
        )
        state, stamp = coverage._prospective_cutover_state_v2(binding, key=key)
        return state, stamp, _scan_document(scan), scan

    @staticmethod
    def _record(state, stamp, scan, scan_document):
        template, active = state["template"], state["active"]
        previous = state["activation_complete"]
        validate_stamps((previous, stamp))
        post_readback = {key: previous[key] for key in ("utc_ns", "monotonic_ns")}
        start = {key: stamp[key] for key in ("utc_ns", "monotonic_ns")}
        value = {
            "schema_version": records.SCHEMA,
            "environment": "demo",
            "account_id": template["account_id"],
            "settlement_currency": "USDT",
            "epoch_id": active.epoch_id,
            "generation": active.generation,
            "active_journal_sequence": active.journal_sequence,
            "active_event_sha256": active.journal_sha256,
            "active_state_sha256": active.state_sha256,
            "audit_sha256": active.audit_sha256,
            "cutover_id": records.identity(
                "demo", template["account_id"], active.epoch_id, active.journal_sha256
            ),
            "manifest_sha256": template["deployed_manifest_sha256"],
            "account_revision": scan_document["account_revision"],
            "ledger_revision": scan_document["ledger_revision"],
            "journal_scan_sha256": records.digest(scan.document_json),
            "journal_scan_json": scan.document_json.decode("ascii"),
            "post_active_readback": post_readback,
            "coverage_start": start,
            "namespace_policy": records.NAMESPACE_POLICY,
            "scope_rule": "original_trigger_candle_open_strictly_after_start",
            "record_kind": "prospective_cutover_not_g6_or_order_authority",
            **{name: False for name in records.FALSE_FIELDS},
            **{
                name: template[name]
                for name in (
                    "writer_inventory_sha256",
                    "credential_session_sha256",
                    "source_bundle_sha256",
                    "source_current_receipt_sha256",
                    "source_native_proof_sha256",
                    "source_native_readback_sha256",
                    "control_owner_epoch",
                    "control_revision",
                    "control_event_sha256",
                    "control_state_sha256",
                )
            },
            **{
                name: scan_document[name]
                for name in ("event_keys_sha256", "rows_sha256", "transitions_sha256")
            },
        }
        raw = records.canonical(value)
        records.decode(raw)
        if scan.received_at > utc_from_ns(stamp["utc_ns"]):
            records.deny("prospective_cutover_scan_after_start")
        return raw

    async def _append_native(self, binding, *, key):
        try:
            async with self._factory() as first, first.begin():
                await self._settings(binding, first, key)
                state, stamp, scan_document, scan = await self._operands_locked(
                    binding, first, key
                )
                raw = self._record(state, stamp, scan, scan_document)
                value = records.decode(raw)
                result = await coverage._checked_await(
                    binding,
                    first.scalar(
                        text(
                            "SELECT public.controlled_writer_coverage_v2_cutover_append(:environment,:uid,:epoch,:raw)"
                        ),
                        {
                            "environment": "demo",
                            "uid": value["account_id"],
                            "epoch": value["epoch_id"],
                            "raw": raw,
                        },
                    ),
                )
                if result != records.digest(raw):
                    records.deny("prospective_cutover_append_mismatch")
                # Final source/control replay before leaving the transaction.
                coverage._prospective_cutover_state_v2(binding, key=key)
            # The first session and its transaction have closed. A commit
            # exception reaches the redacted failure path; it never triggers retry.
            coverage._prospective_cutover_state_v2(binding, key=key)
            async with self._factory() as second, second.begin():
                await self._settings(binding, second, key)
                _, _, second_document, second_scan = await self._operands_locked(
                    binding, second, key
                )
                if (
                    any(
                        scan_document[name] != second_document[name]
                        for name in _STABLE_SCAN_FIELDS
                    )
                    or scan.consumed_event_keys != second_scan.consumed_event_keys
                ):
                    records.deny("prospective_cutover_journal_changed")
                result = await coverage._checked_await(
                    binding,
                    second.scalar(
                        text(
                            "SELECT public.controlled_writer_coverage_v2_cutover_read(:environment,:uid,:epoch,:expected)"
                        ),
                        {
                            "environment": "demo",
                            "uid": value["account_id"],
                            "epoch": value["epoch_id"],
                            "expected": records.digest(raw),
                        },
                    ),
                )
                if (
                    type(result) is not dict
                    or set(result)
                    != {
                        "record_json",
                        "record_sha256",
                        "recorded_at",
                        "coverage_start_at",
                    }
                    or result["record_json"] != raw.decode("ascii")
                    or result["record_sha256"] != records.digest(raw)
                ):
                    records.deny("prospective_cutover_readback_mismatch")
                recorded = datetime.fromisoformat(result["recorded_at"])
                start = datetime.fromisoformat(result["coverage_start_at"])
                _, final_stamp = coverage._prospective_cutover_state_v2(
                    binding, key=key
                )
                if (
                    recorded.tzinfo is not UTC
                    or start.tzinfo is not UTC
                    or start != records.stamp_utc(value["coverage_start"])
                    or not start <= recorded <= utc_from_ns(final_stamp["utc_ns"])
                ):
                    records.deny("prospective_cutover_readback_clock")
                observed = _CutoverReadback(
                    raw,
                    records.digest(raw),
                    recorded,
                    start,
                    second_scan.consumed_event_keys,
                )
            # This last synchronous fence replays original source, function,
            # context and cached owner-lease checks. Fresh DB Control was read
            # under the second transaction's UID lock; it can advance after
            # that lock releases during session exit. This return carries only
            # historical cutover custody. Future G6/R6 must re-read current
            # Control/coverage and CAS their revisions under the UID lock.
            coverage._prospective_cutover_state_v2(binding, key=key)
            return observed
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except records.ProspectiveCutoverError:
            raise
        except Exception:  # noqa: BLE001 - retain committed marker; redact DB/source exceptions
            records.deny("prospective_cutover_commit_or_readback_unknown")
