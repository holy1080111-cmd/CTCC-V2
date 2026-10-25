"""Explicitly synthetic prospective cutover bytes; never native authority."""

from datetime import UTC, datetime, timedelta

from app.domain import prospective_event_cutover_v2 as records
from app.trade_qualification.reservations import LedgerScope, digest


def stamp(at, monotonic_ns):
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    return {
        "utc_ns": (delta.days * 86400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1000,
        "monotonic_ns": monotonic_ns,
    }


def empty_scan_document(uid, at):
    empty = records.digest(records.canonical([]))
    return {
        "schema_version": "ctcc.consumed_event_journal_inspection.v1",
        "scope_sha256": digest(LedgerScope(account_id=uid, settlement_currency="USDT")),
        "account_revision": 0,
        "ledger_revision": 0,
        "claims_sha256": None,
        "observed_at": at.isoformat(),
        "received_at": at.isoformat(),
        "event_count": 0,
        "transition_count": 0,
        "state_counts": {
            name: 0 for name in ("reserved", "consumed", "uncertain", "reconciled_flat")
        },
        "event_keys_sha256": empty,
        "rows_sha256": empty,
        "transitions_sha256": empty,
        "db_journal_row_chain_verified": True,
        "db_journal_complete": False,
        "external_event_history_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def cutover_record_fixture(
    *, template=None, active=None, post_active=None, start=None, scan=None
):
    """No opaque owner or private registry is constructed by this helper."""
    now = datetime(2026, 10, 25, tzinfo=UTC)
    post_active = stamp(now, 1_000_000_000) if post_active is None else post_active
    start = (
        stamp(now + timedelta(microseconds=2), 1_000_002_000)
        if start is None
        else start
    )
    if template is None:
        template = {
            "account_id": "730051",
            "deployed_manifest_sha256": "1" * 64,
            **{
                key: "2" * 64
                for key in (
                    "writer_inventory_sha256",
                    "credential_session_sha256",
                    "source_bundle_sha256",
                    "source_current_receipt_sha256",
                    "source_native_proof_sha256",
                    "source_native_readback_sha256",
                    "control_event_sha256",
                    "control_state_sha256",
                )
            },
            "control_owner_epoch": 1,
            "control_revision": 2,
        }
    if active is None:
        from types import SimpleNamespace

        active = SimpleNamespace(
            epoch_id="a" * 32,
            generation=1,
            journal_sequence=2,
            journal_sha256="3" * 64,
            state_sha256="4" * 64,
            audit_sha256="5" * 64,
        )
    scan = (
        empty_scan_document(template["account_id"], records.stamp_utc(post_active))
        if scan is None
        else scan
    )
    scan_raw = records.canonical(scan)
    result = {
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
        **{
            key: template[key]
            for key in (
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
            key: scan[key]
            for key in (
                "account_revision",
                "ledger_revision",
                "event_keys_sha256",
                "rows_sha256",
                "transitions_sha256",
            )
        },
        "journal_scan_json": scan_raw.decode("ascii"),
        "journal_scan_sha256": records.digest(scan_raw),
        "post_active_readback": post_active,
        "coverage_start": start,
        "namespace_policy": records.NAMESPACE_POLICY,
        "scope_rule": "original_trigger_candle_open_strictly_after_start",
        "record_kind": "prospective_cutover_not_g6_or_order_authority",
        **{key: False for key in records.FALSE_FIELDS},
    }
    raw = records.canonical(result)
    records.decode(raw)
    return raw
