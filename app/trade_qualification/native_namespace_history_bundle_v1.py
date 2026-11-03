"""Original native namespace history bytes: pure audit replay never issues owners."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from app.database.repositories import controlled_writer_coverage as coverage_rows
from app.database.repositories.public_runtime_bundle_custody import MAX_BUNDLE_BYTES
from app.database.repositories.qualification_ledger import (
    _ConsumedEventJournalInspection,
)
from app.domain import prospective_event_cutover_v2 as cutover
from app.domain.source_primitives import (
    MAX_CLOCK_BATCH_NS,
    MAX_CLOCK_DEVIATION_NS,
    canonical,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.public_market_source.public_runtime_custody_v4 import (
    replay_operational_proof_v4,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_history_source_bundle_v1 as archival
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import demo_control as control
from app.trade_qualification import history_json_lexical_guard_v1 as _json_lexical_guard
from app.trade_qualification import native_namespace_phase_v1 as phase
from app.trade_qualification.reservations import LedgerScope
from app.trade_qualification.reservations import digest as ledger_digest

SCHEMA = "ctcc.native_namespace_history_bundle.v1"
TEMPORAL_SCHEMA = "ctcc.native_namespace_history_acquisition.v1"
MAX_LOOKBACK_NS = 2 * 60 * 60 * 1_000_000_000
COLLECTOR_BATCH_SECONDS = 120
FALSE_FIELDS = (
    "current_account_complete",
    "current_risk_verified",
    "namespace_finality_verified",
    "universal_writer_exclusion_verified",
    "g6_qualified",
    "execution_authority",
)
POLICY = {
    "schema_version": SCHEMA,
    "capture_plan": "ctcc.demo_account_plan.v6",
    "capture_inventory": "unchanged_all62_V6_streams",
    "collector_max_batch_seconds": COLLECTOR_BATCH_SECONDS,
    "native_anchor_ns": MAX_CLOCK_BATCH_NS,
    "native_drift_ns": MAX_CLOCK_DEVIATION_NS,
    "max_lookback_ns": MAX_LOOKBACK_NS,
    "source": "same_active_native_read_lease_and_moved_fresh_phase_then_original_B1",
    "terminal": "explicit_empty_query_page_only",
    "history_time_domains": {
        "ordinary": "record_creation_cTime",
        "fills": "record_generation_ts_not_fillTime",
        "algo": "no_begin_end_filter_documented",
    },
    "namespace_finality": "UNKNOWN_NO_VENDOR_LATE_WATERMARK_OR_ALL_ROUTE_INTENT_CLOSURE",
    "registration_site": "UNKNOWN",
    "old_phase_current_authority": "released_before_new_stage_not_renewed",
    "post_history_fresh_current_recheck_required": True,
    **{name: False for name in FALSE_FIELDS},
}
POLICY_SHA256 = sha(canonical(POLICY))


class NamespaceHistoryBundleError(ValueError):
    """Fixed codes only; original bytes remain private."""


def deny(code="namespace_history_bundle_invalid"):
    raise NamespaceHistoryBundleError(code) from None


def _hex(
    value,
    maximum=MAX_BUNDLE_BYTES,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    return archival._hex(value, maximum)


def namespace_queries(packet):
    expected = tuple(
        stream
        for stream in capture.V6_ALL_PRODUCT_STREAMS
        if stream == "orders_pending"
        or stream.startswith(("orders_history_", "fills_", "algo_"))
    )
    groups = {name: [] for name in expected}
    for page in packet.observations:
        if page.request.stream in groups:
            groups[page.request.stream].append(page)
    result = []
    for name in expected:
        pages = groups[name]
        if not pages or not pages[-1].terminal or pages[-1].rows:
            deny("namespace_history_explicit_empty_terminal_required")
        result.append(
            {
                "stream": name,
                "cursor_type": capture._CURSOR_FIELDS[name],
                "row_count": sum(len(page.rows) for page in pages),
                "time_domain": "record_generation_ts_not_fillTime"
                if name.startswith("fills_")
                else "no_begin_end_filter_documented"
                if name.startswith("algo_history_")
                else "record_creation_cTime"
                if name.startswith("orders_history_")
                else "current_inventory_non_atomic",
                "pages": [
                    {
                        "page_index": page.page_index,
                        "request": capture._json_value(page.request),
                        "cursor_value": dict(page.request.parameters).get("after"),
                        "previous_page_sha256": page.previous_page_sha256,
                        "receipt_sha256": page.receipt_sha256,
                        "raw_sha256": page.body_sha256,
                        "body_size_bytes": page.body_size_bytes,
                        "request_started_at": page.request_started_at.isoformat(),
                        "headers_received_at": page.headers_received_at.isoformat(),
                        "body_completed_at": page.body_completed_at.isoformat(),
                        "terminal": page.terminal,
                        "first_row_id": None if not page.rows else page.rows[0].row_id,
                        "last_row_id": None if not page.rows else page.rows[-1].row_id,
                    }
                    for page in pages
                ],
            }
        )
    return result


def phase_document(moved, *, source_bundles):
    historical, account = moved["historical"], moved["current_account"]
    original = historical["record"]
    if type(source_bundles) is not tuple or len(source_bundles) != len(moved["hops"]):
        deny("namespace_history_hop_sources_required")
    replay = current.replay_current_account_bundle_v4(
        account["bundle_raw"], expected_sha256=account["bundle_sha256"]
    )
    return {
        "schema_version": "ctcc.namespace_history_moved_phase.v1",
        "cutover_record_hex": original.record_json.hex(),
        "cutover_record_sha256": original.record_sha256,
        "cutover_recorded_at": original.recorded_at.isoformat(),
        "cutover_event_keys": sorted(historical["event_keys"]),
        "original_account_bundle_hex": historical["phase_source"]["bundle_raw"].hex(),
        "current_account_bundle_hex": account["bundle_raw"].hex(),
        "current_account_bundle_sha256": account["bundle_sha256"],
        "original_current_expiry": replay.expires_at.isoformat(),
        "original_current_monotonic_deadline_ns": replay.monotonic_deadline_ns,
        "original_active_records_json": historical["active"].records_json,
        "active_records_json": moved["active"].records_json,
        "hops": [
            {
                "active_records_json": hop["active"].records_json,
                "control_rows": list(hop["control_rows"]),
                "account_bundle_hex": source_raw.hex(),
                "scan_json": hop["scan_json"].decode("ascii"),
                "scan_keys": sorted(hop["scan_keys"]),
                "source_plan_sha256": hop["source_plan_sha256"],
                "credential_session_sha256": hop["credential_session_sha256"],
            }
            for hop, source_raw in zip(moved["hops"], source_bundles, strict=True)
        ],
        "phase_checked": moved["phase_checked"],
        "historical_only": True,
        "current_authority_released_before_acquisition": True,
    }


def _control_lineage(
    raw_rows,
    scope,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if (
        type(raw_rows) is not list
        or not 1 <= len(raw_rows) <= control.MAX_CONTROL_LINEAGE_ROWS
    ):
        deny("namespace_history_control_prefix_required")
    rows = []
    for raw in raw_rows:
        if type(raw) is not str:
            deny("namespace_history_control_row_shape")
        document = archival._json(raw.encode("ascii"), control.MAX_CONTROL_EVENT_BYTES)
        state = control.decode(control.canonical(document["state"]))
        rows.append(
            control.ControlJournalSourceRowV1(
                scope=scope,
                revision=state.revision,
                command_id=document["command_id"],
                action=document["action"],
                previous_sha256=document["previous_sha256"],
                event_sha256=control.digest(raw),
                state_sha256=control.digest(control.canonical(document["state"])),
                event_json=raw,
                occurred_at=state.updated_at,
            )
        )
    return control.replay_control_journal_lineage_v1(
        tuple(rows),
        scope=scope,
        target_state=state,
        expected_head_sha256=rows[-1].event_sha256,
    )


def _coverage_source(
    active,
    raw,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    value = current.replay_current_account_bundle_v4(raw, expected_sha256=sha(raw))
    document = archival._json(raw)
    state = active.state_document
    pins = {
        "source_bundle_sha256": sha(raw),
        "source_plan_sha256": value.reference.plan_sha256,
        "source_chain_head_sha256": value.reference.head_sha256,
        "source_proof_envelope_sha256": value.proof_envelope_sha256,
        "source_current_receipt_sha256": sha(value.current_receipt_json),
        "source_native_proof_sha256": value.native_proof_sha256,
        "source_native_readback_sha256": value.native_readback_sha256,
        "source_current_expires_at": value.expires_at.isoformat(),
        "source_current_monotonic_deadline_ns": value.monotonic_deadline_ns,
        "credential_session_sha256": document["route_proof"][
            "credential_session_sha256"
        ],
        "identity_sha256": sha(canonical(document["route_proof"]["account_identity"])),
    }
    if any((state[name] != expected for name, expected in pins.items())):
        deny("namespace_history_coverage_source_binding_changed")
    return (value, document["route_proof"]["account_identity"])


def _replay_succession(
    scope,
    original_active,
    original_keys,
    hops,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if type(hops) is not list or not 1 <= len(hops) <= phase.MAX_HOPS:
        deny("namespace_history_succession_required")
    previous = {
        "historical": {"active": original_active, "event_keys": original_keys},
        "hops": (),
    }
    for hop in hops:
        if type(hop) is not dict or set(hop) != {
            "active_records_json",
            "control_rows",
            "scan_json",
            "scan_keys",
            "source_plan_sha256",
            "credential_session_sha256",
            "account_bundle_hex",
        }:
            deny("namespace_history_succession_shape")
        active = coverage_rows._observation(
            scope,
            archival._json(
                hop["active_records_json"].encode("ascii"), phase.MAX_CHAIN_BYTES
            ),
        )
        if any(
            hop[name] != active.state_document[name]
            for name in ("source_plan_sha256", "credential_session_sha256")
        ):
            deny("namespace_history_hop_source_pin_changed")
        keys = hop["scan_keys"]
        if (
            type(keys) is not list
            or keys != sorted(set(keys))
            or any(
                type(key) is not str or cutover._SHA.fullmatch(key) is None
                for key in keys
            )
        ):
            deny("namespace_history_permanent_tombstone_missing")
        scan_raw = hop["scan_json"].encode("ascii")
        scan = archival._json(scan_raw, 4096)
        if (
            scan.get("schema_version") != "ctcc.consumed_event_journal_inspection.v1"
            or scan.get("scope_sha256")
            != ledger_digest(
                LedgerScope(account_id=scope.account_id, settlement_currency="USDT")
            )
            or scan.get("db_journal_row_chain_verified") is not True
            or (scan.get("admission") != "DENY")
            or any(
                scan.get(name) is not False
                for name in (
                    "db_journal_complete",
                    "external_event_history_complete",
                    "source_authenticity_verified",
                    "execution_authority",
                )
            )
        ):
            deny("namespace_history_scan_semantics_changed")
        inspected = _ConsumedEventJournalInspection(
            frozenset(keys),
            scan_raw,
            datetime.fromisoformat(scan["observed_at"]),
            datetime.fromisoformat(scan["received_at"]),
        )
        if inspected.observed_at > inspected.received_at:
            deny("namespace_history_scan_chronology")
        lineage = _control_lineage(hop["control_rows"], scope)
        observed_hop = SimpleNamespace(coverage=active, lineage=lineage, scan=inspected)
        joined = phase._join(
            previous,
            {"active": active, "template": active.state_document},
            observed_hop,
        )
        previous = {**previous, "hops": joined}
    return previous["hops"]


def _phase(
    value,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "cutover_record_hex",
            "cutover_record_sha256",
            "cutover_recorded_at",
            "cutover_event_keys",
            "original_account_bundle_hex",
            "current_account_bundle_hex",
            "current_account_bundle_sha256",
            "original_current_expiry",
            "original_current_monotonic_deadline_ns",
            "original_active_records_json",
            "active_records_json",
            "hops",
            "phase_checked",
            "historical_only",
            "current_authority_released_before_acquisition",
        }
        or value["schema_version"] != "ctcc.namespace_history_moved_phase.v1"
        or (value["historical_only"] is not True)
        or (value["current_authority_released_before_acquisition"] is not True)
    ):
        deny("namespace_history_moved_phase_shape")
    raw = _hex(value["cutover_record_hex"], cutover.MAX_BYTES)
    record = cutover.decode(raw)
    keys = value["cutover_event_keys"]
    if (
        sha(raw) != value["cutover_record_sha256"]
        or type(keys) is not list
        or keys != sorted(set(keys))
        or (len(keys) != json.loads(record["journal_scan_json"])["event_count"])
        or any(
            type(key) is not str or cutover._SHA.fullmatch(key) is None for key in keys
        )
        or (sha(canonical(keys)) != record["event_keys_sha256"])
    ):
        deny("namespace_history_original_tombstones_changed")
    scope = control.ControlScope("demo", record["account_id"])
    active = coverage_rows._observation(
        scope,
        archival._json(
            value["original_active_records_json"].encode("ascii"), phase.MAX_CHAIN_BYTES
        ),
    )
    state = active.state_document
    pins = {
        "epoch_id": active.epoch_id,
        "generation": active.generation,
        "active_journal_sequence": active.journal_sequence,
        "active_event_sha256": active.journal_sha256,
        "active_state_sha256": active.state_sha256,
        "audit_sha256": active.audit_sha256,
        "manifest_sha256": state["deployed_manifest_sha256"],
    }
    if any((record[name] != expected for name, expected in pins.items())) or any(
        record[name] != state[name]
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
    ):
        deny("namespace_history_original_coverage_changed")
    original_raw = _hex(value["original_account_bundle_hex"])
    original, identity = _coverage_source(active, original_raw)
    hops = _replay_succession(scope, active, frozenset(keys), value["hops"])
    for saved, verified in zip(value["hops"], hops, strict=True):
        source_raw = _hex(saved["account_bundle_hex"])
        source, source_identity = _coverage_source(verified["active"], source_raw)
        if (
            source_identity != identity
            or source.packet.plan.session_binding_id
            != original.packet.plan.session_binding_id
        ):
            deny("namespace_history_historical_identity_changed")
    raw = _hex(value["current_account_bundle_hex"])
    latest, after = _coverage_source(hops[-1]["active"], raw)
    if (
        sha(raw) != value["current_account_bundle_sha256"]
        or raw != _hex(value["hops"][-1]["account_bundle_hex"])
        or latest.expires_at.isoformat() != value["original_current_expiry"]
        or (
            latest.monotonic_deadline_ns
            != value["original_current_monotonic_deadline_ns"]
        )
        or (hops[-1]["active"].records_json != value["active_records_json"])
        or (after != identity)
        or (identity["uid"] != record["account_id"])
    ):
        deny("namespace_history_original_phase_window_changed")
    checked = cutover.stamp(value["phase_checked"])
    if (
        not cutover.stamp_utc(record["coverage_start"])
        <= current._time(value["cutover_recorded_at"])
        <= utc_from_ns(checked["utc_ns"])
    ):
        deny("namespace_history_historical_record_chronology")
    return (record, latest, identity, hops[-1]["scan_keys"])


@dataclass(frozen=True, slots=True, repr=False)
class RecordedNamespaceHistoryFactsV1:
    bundle_raw: bytes
    bundle_sha256: str
    reference: object
    packet: object
    consumed_event_keys: frozenset[str]
    query_receipt_json: bytes

    @property
    def execution_authority(self):
        return False

    @property
    def namespace_finality(self):
        return "UNKNOWN"


def replay_native_namespace_history_bundle_v1(
    raw,
    *,
    expected_sha256,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if any(
        (
            getattr(module, name) is not original
            for module, name, original in _REPLAY_PINS
        )
    ):
        deny("namespace_history_original_replay_function_changed")
    try:
        if sha(raw) != expected_sha256:
            deny("namespace_history_bundle_digest")
        value = archival._json(raw)
        if (
            set(value)
            != {
                "schema_version",
                "policy_sha256",
                "phase",
                "route_proof",
                "chain",
                "temporal",
                "source_reference",
                "query_inventory",
                "query_receipt_hex",
                "cutoff_ns",
                "requested_start_ms",
                "requested_end_ms",
                "namespace_finality",
                *FALSE_FIELDS,
            }
            or value["schema_version"] != SCHEMA
            or value["policy_sha256"] != POLICY_SHA256
            or (value["namespace_finality"] != "UNKNOWN")
            or any(value[name] is not False for name in FALSE_FIELDS)
        ):
            deny("namespace_history_bundle_contract")
        record, historical_current, identity, event_keys = _phase(value["phase"])
        scope = LedgerScope(account_id=record["account_id"], settlement_currency="USDT")
        chain = current._chain_replay(value["chain"])
        reference, packet, records, joins = proof._source(
            chain, scope, proof_schema=proof.ALGO_V6_SCHEMA
        )
        plan = packet.plan
        if (
            observed.reference_document(reference) != value["source_reference"]
            or type(plan) is not capture.AllProductDemoAccountCapturePlanV6
            or plan.max_batch_seconds != COLLECTOR_BATCH_SECONDS
            or (plan.expected_uid != identity["uid"])
            or (plan.expected_main_uid != identity["mainUid"])
            or (
                plan.session_binding_id
                != historical_current.packet.plan.session_binding_id
            )
            or (plan == historical_current.packet.plan)
            or (plan.max_response_bytes != capture.MAX_RESPONSE_BYTES)
        ):
            deny("namespace_history_new_phase_plan_binding")
        temporal = value["temporal"]
        original = cutover.stamp(temporal["original_started"])
        cutoff = original["utc_ns"] // 1000000 * 1000000
        start = record["coverage_start"]["utc_ns"]
        if (
            original["utc_ns"] - start > MAX_LOOKBACK_NS
            or not start < cutoff
            or value["cutoff_ns"] != cutoff
            or (value["requested_start_ms"] != str(start // 1000000))
            or (value["requested_end_ms"] != str(cutoff // 1000000))
            or (
                capture._milliseconds(plan.history_start) != value["requested_start_ms"]
            )
            or (capture._milliseconds(plan.history_end) != value["requested_end_ms"])
            or (plan.created_at != utc_from_ns(original["utc_ns"]))
        ):
            deny("namespace_history_original_cutoff_or_start_changed")
        route = value["route_proof"]
        replay_operational_proof_v4(route)
        if (
            route["account_identity"] != identity
            or route["account_plan_sha256"] != reference.plan_sha256
            or route["rest_origin"] != plan.origin
            or (
                route["credential_session_sha256"]
                != sha(
                    canonical(
                        {
                            "session_binding_id": plan.session_binding_id,
                            "account_plan_sha256": reference.plan_sha256,
                        }
                    )
                )
            )
        ):
            deny("namespace_history_fresh_route_identity_changed")
        if (
            type(temporal) is not dict
            or set(temporal)
            != {
                "schema_version",
                "policy_sha256",
                "stage",
                "original_started",
                "witnesses",
                "finalization_witnesses",
                "clock_files_hex",
                "source_readback_complete",
                "proof_persist_start",
            }
            or temporal["schema_version"] != TEMPORAL_SCHEMA
            or (temporal["policy_sha256"] != POLICY_SHA256)
        ):
            deny("namespace_history_temporal_contract")
        stage = temporal["stage"]
        if (
            type(stage) is not dict
            or set(stage) != {"kind", "invocation_id", "started"}
            or stage["kind"] != "native_namespace_history_only"
            or (len(stage["invocation_id"]) != 32)
            or (packet.barrier_completed_at != utc_from_ns(stage["started"]["utc_ns"]))
        ):
            deny("namespace_history_original_barrier_changed")
        validate_stamps(
            (
                value["phase"]["phase_checked"],
                original,
                route["request_start"],
                route["consumed"],
                stage["started"],
            )
        )
        if (
            route["consumed"]["monotonic_ns"] >= stage["started"]["monotonic_ns"]
            or route["consumed"]["utc_ns"] >= stage["started"]["utc_ns"]
        ):
            deny("namespace_history_route_stage_order")
        files = temporal["clock_files_hex"]
        if type(files) is not dict or set(files) != set(proof.FILES):
            deny("namespace_history_clock_inventory")
        files = {name: _hex(body, storage.MAX_PART) for name, body in files.items()}
        host = proof.public_clock.replay_clock_observation(files["host-before.json"])
        hosts, _, traces = proof._clocks(
            files,
            stage=stage,
            scope_sha=proof.scope_sha256(scope),
            expected_profile=host["observation"]["diagnostic"]["profile_id"],
        )
        archival._b1_temporal(
            temporal,
            stage=stage,
            packet=packet,
            records=records,
            joins=joins,
            hosts=hosts,
            traces=traces,
            position_stamps=(),
        )
        from app.trade_qualification import account_history_query_verifier as verifier

        query = verifier.verify_algo_history_query_chain_v6(
            chain, **observed._pins(reference, scope)
        )
        if query.receipt_json != _hex(
            value["query_receipt_hex"], capture.MAX_PACKET_BYTES
        ) or value["query_inventory"] != namespace_queries(packet):
            deny("namespace_history_query_readback_changed")
        if any(
            (
                getattr(module, name) is not original
                for module, name, original in _REPLAY_PINS
            )
        ):
            deny("namespace_history_original_replay_function_changed")
        return RecordedNamespaceHistoryFactsV1(
            raw, sha(raw), reference, packet, event_keys, query.receipt_json
        )
    except NamespaceHistoryBundleError:
        raise
    except Exception:  # noqa: BLE001 -- retain fixed private-source rejection only.
        deny()


_REPLAY_PINS = (
    (sys.modules[__name__], "sha", sha),
    (sys.modules[__name__], "canonical", canonical),
    (sys.modules[__name__], "utc_from_ns", utc_from_ns),
    (sys.modules[__name__], "validate_stamps", validate_stamps),
    (sys.modules[__name__], "ledger_digest", ledger_digest),
    (sys.modules[__name__], "_hex", _hex),
    (sys.modules[__name__], "_phase", _phase),
    (sys.modules[__name__], "_control_lineage", _control_lineage),
    (sys.modules[__name__], "_coverage_source", _coverage_source),
    (sys.modules[__name__], "_replay_succession", _replay_succession),
    (sys.modules[__name__], "namespace_queries", namespace_queries),
    (
        current,
        "replay_current_account_bundle_v4",
        current.replay_current_account_bundle_v4,
    ),
    (
        control,
        "replay_control_journal_lineage_v1",
        control.replay_control_journal_lineage_v1,
    ),
    (coverage_rows, "_observation", coverage_rows._observation),
    (phase, "_join", phase._join),
)
