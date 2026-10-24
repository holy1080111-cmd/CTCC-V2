"""Canonical archival facts and pure replay. Saved bytes never issue an owner."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise

from app.database.repositories.account_observation_index import ObservationBatchReadback
from app.database.repositories.public_runtime_bundle_custody import MAX_BUNDLE_BYTES
from app.domain.source_primitives import (
    MAX_TIMESTAMP_NS,
    canonical,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_positions_history_collector_v1 as positions
from app.trade_qualification import account_positions_history_v1 as position_pages
from app.trade_qualification.reservations import LedgerScope

SCHEMA = "ctcc.native_history_source_bundle.v1"
PROOF_SCHEMA = "ctcc.native_history_acquisition.v1"
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 262144
POLICY_SHA256 = sha(
    canonical(
        {
            "schema_version": PROOF_SCHEMA,
            "source_plan": "ctcc.demo_account_plan.v5",
            "native_anchor_ns": 60_000_000_000,
            "native_drift_ns": 5_000_000,
            "custody_consume_ns": 5_000_000_000,
            "max_json_depth": MAX_JSON_DEPTH,
            "max_json_nodes": MAX_JSON_NODES,
            "max_position_pages": 18,
            "max_position_raw_bytes": 4 * 1024 * 1024 + 2 * 65536,
            "max_position_phase_events": 4096,
            "max_empty_chunks_per_response": 8,
            "positions_contract": "ctcc.native_positions_history_raw.v1",
            "position_identity": "four_source_keys_and_declared_USDT_scope",
            "position_ccy": "only_original_row_or_unknown",
            "max_raw_root_bytes": MAX_BUNDLE_BYTES
            + 4 * 1024 * 1024
            + 2 * 65536
            + 2 * 1024 * 1024,
            "max_companion_root_bytes": 9 * 512 * 1024,
            "file_readback": "exact_produced_names_sizes_hashes_with_native_checks",
            "funding": "NOT_ACQUIRED",
            "registration_site": "UNKNOWN",
            "account_complete": False,
            "execution_authority": False,
        }
    )
)


class HistorySourceBundleError(ValueError):
    """Fixed rejection codes, never raw source or database exception text."""


def _deny(code="history_source_bundle_invalid"):
    raise HistorySourceBundleError(code) from None


def _json(raw, maximum=MAX_BUNDLE_BYTES):
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        _deny("history_source_byte_limit")
    # Bound depth/structural allocation before json.loads creates a large tree.
    depth = tokens = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (123, 91, 44, 58):
            tokens += 1
            if byte in (123, 91):
                depth += 1
            if depth > MAX_JSON_DEPTH or tokens > MAX_JSON_NODES:
                _deny("history_source_json_structure_limit")
        elif byte in (125, 93):
            depth -= 1

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                _deny("history_source_duplicate_json")
            result[key] = item
        return result

    def forbidden(_):
        _deny("history_source_json_number_invalid")

    def integer(token):
        if len(token) > len(str(MAX_TIMESTAMP_NS)) + 1:
            _deny("history_source_json_number_invalid")
        number = int(token)
        if abs(number) > MAX_TIMESTAMP_NS:
            _deny("history_source_json_number_invalid")
        return number

    try:
        value = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_int=integer,
            parse_float=forbidden,
            parse_constant=forbidden,
        )
    except HistorySourceBundleError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        _deny("history_source_json_invalid")
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _deny("history_source_json_structure_limit")
        if type(item) is dict:
            if len(item) > 128 or any(
                type(key) is not str or len(key) > 128 for key in item
            ):
                _deny("history_source_json_structure_limit")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            if len(item) > journal.MAX_EVENTS:
                _deny("history_source_json_structure_limit")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            if abs(item) > MAX_TIMESTAMP_NS:
                _deny("history_source_json_number_invalid")
        elif type(item) is str:
            if len(item) > maximum:
                _deny("history_source_json_string_limit")
        elif type(item) is not bool and item is not None:
            _deny("history_source_json_type_invalid")
    if journal.canonical(value) != raw:
        _deny("history_source_not_canonical")
    return value


def _encoded_size(value):
    """Exact ensure_ascii canonical size without hex/JSON payload allocation."""
    pending, total, nodes = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _deny("history_source_json_structure_limit")
        if type(item) is dict:
            if len(item) > 128 or any(
                type(key) is not str or len(key) > 128 for key in item
            ):
                _deny("history_source_json_structure_limit")
            total += 2 + max(0, len(item) - 1) + len(item)
            for key, child in item.items():
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))
        elif type(item) in (list, tuple):
            if len(item) > journal.MAX_EVENTS:
                _deny("history_source_json_structure_limit")
            total += 2 + max(0, len(item) - 1)
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if (
                item.isascii()
                and item.isprintable()
                and '"' not in item
                and "\\" not in item
            ):
                total += len(item) + 2
            else:
                total += 2
                for char in item:
                    n = ord(char)
                    total += (
                        2
                        if char in '\\"\b\f\n\r\t'
                        else 6
                        if n < 32 or 127 <= n <= 65535
                        else 12
                        if n > 65535
                        else 1
                    )
        elif type(item) is bool:
            total += 4 if item else 5
        elif type(item) is int and abs(item) <= MAX_TIMESTAMP_NS:
            total += len(str(item))
        elif item is None:
            total += 4
        else:
            _deny("history_source_json_type_invalid")
        if total > MAX_BUNDLE_BYTES:
            _deny("history_source_byte_limit")
    return total


def _hex(value, maximum):
    if type(value) is not str or not value or len(value) > maximum * 2:
        _deny("history_source_bytes_invalid")
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        _deny("history_source_bytes_invalid")
    if raw.hex() != value:
        _deny("history_source_bytes_invalid")
    return raw


def chain_document(chain):
    if type(chain) is not tuple or not 1 <= len(chain) <= journal.MAX_EVENTS:
        _deny("history_source_chain_invalid")
    rows = []
    for point in chain:
        if type(point) is not journal.JournalReadback:
            _deny("history_source_chain_invalid")
        point.__post_init__()
        rows.append(
            {
                "event_hex": point.event.event_json.hex(),
                "raw_hex": None
                if point.event.raw_body is None
                else point.event.raw_body.hex(),
                "packet_hex": None
                if point.event.packet_payload is None
                else point.event.packet_payload.hex(),
                "recorded_at": capture._utc(point.db_recorded_at).isoformat(),
                "readback_at": capture._utc(point.readback_at).isoformat(),
            }
        )
    return rows


def _row_skeleton(point):
    return {
        "event_hex": "",
        "raw_hex": None if point.event.raw_body is None else "",
        "packet_hex": None if point.event.packet_payload is None else "",
        "recorded_at": capture._utc(point.db_recorded_at).isoformat(),
        "readback_at": capture._utc(point.readback_at).isoformat(),
    }


def _preflight_freeze(skeleton, sources, batches, position_archive):
    # This validates aggregate original bytes BEFORE any population .hex().
    lifecycle.source_set_sha256(sources)
    if (
        type(batches) is not tuple
        or len(batches) != len(sources)
        or type(position_archive) is not positions._NativePositionArchiveV1
        or type(position_archive.raw_archive) is not bytes
        or not 0 < len(position_archive.raw_archive) <= positions.MAX_ARCHIVE_BYTES
    ):
        _deny("history_source_preflight_invalid")
    total = _encoded_size(skeleton) + len(position_archive.raw_archive) * 2
    source_size = 2 + max(0, len(sources) - 1)
    for chain, _ in sources:
        source_size += 2 + max(0, len(chain) - 1)
        for point in chain:
            source_size += _encoded_size(_row_skeleton(point)) + 2 * sum(
                len(raw)
                for raw in (
                    point.event.event_json,
                    point.event.raw_body,
                    point.event.packet_payload,
                )
                if raw is not None
            )
            if total + source_size - 2 > MAX_BUNDLE_BYTES:
                _deny("history_source_byte_limit")
    batch_size = 2 + max(0, len(batches) - 1)
    for batch in batches:
        if (
            type(batch) is not ObservationBatchReadback
            or type(batch.replay) is not observed.ObservationReplay
            or type(batch.document_json) is not bytes
            or type(batch.replay.receipt_json) is not bytes
        ):
            _deny("history_source_preflight_invalid")
        batch_size += _encoded_size(
            {
                "sequence": batch.sequence,
                "head_sha256": batch.event_sha256,
                "document_hex": "",
                "receipt_hex": "",
            }
        ) + 2 * (len(batch.document_json) + len(batch.replay.receipt_json))
        if total + source_size + batch_size - 4 > MAX_BUNDLE_BYTES:
            _deny("history_source_byte_limit")
    return total + source_size + batch_size - 4


def _chain(value):
    if type(value) is not list or not 1 <= len(value) <= journal.MAX_EVENTS:
        _deny("history_source_chain_invalid")
    result = []
    for row in value:
        if type(row) is not dict or set(row) != {
            "event_hex",
            "raw_hex",
            "packet_hex",
            "recorded_at",
            "readback_at",
        }:
            _deny("history_source_chain_invalid")
        event = journal._JournalEvent(
            journal._ISSUER,
            _hex(row["event_hex"], journal.MAX_CHAIN_BYTES),
            None
            if row["raw_hex"] is None
            else _hex(row["raw_hex"], journal.PAGE_BYTES),
            None
            if row["packet_hex"] is None
            else _hex(row["packet_hex"], capture.MAX_PACKET_BYTES),
        )
        result.append(
            journal.JournalReadback(
                event,
                datetime.fromisoformat(row["recorded_at"]),
                datetime.fromisoformat(row["readback_at"]),
            )
        )
    return tuple(result)


def batch_document(batch):
    if (
        type(batch) is not ObservationBatchReadback
        or type(batch.replay) is not observed.ObservationReplay
    ):
        _deny("history_source_batch_invalid")
    return {
        "sequence": batch.sequence,
        "head_sha256": batch.event_sha256,
        "document_hex": batch.document_json.hex(),
        "receipt_hex": batch.replay.receipt_json.hex(),
    }


@dataclass(frozen=True, slots=True, repr=False)
class HistorySourceFactsV1:
    """Observed original bytes/locators; no full-history or trading authority."""

    bundle_raw: bytes = field(repr=False)
    bundle_sha256: str
    proof_sha256: str
    scope: LedgerScope = field(repr=False)
    sources: tuple = field(repr=False)
    batches: tuple = field(repr=False)
    reference: observed.CaptureReference = field(repr=False)
    packet: capture.DemoAccountPacket = field(repr=False)
    positions: positions._NativePositionArchiveV1 = field(repr=False)
    native_history_prefix_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision: None = field(default=None, init=False)
    funding_source_status: str = field(default="NOT_ACQUIRED", init=False)
    funding_accrual_events: None = field(default=None, init=False)
    registration_site: str = field(default="UNKNOWN", init=False)
    execution_authority: bool = field(default=False, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    admission: str = field(default="DENY", init=False)


def freeze_history_source_bundle_v1(
    *, scope, sources, batches, position_plan, identity, position_archive, temporal
):
    """Encode then independently replay all original bytes before custody."""
    if (
        type(scope) is not LedgerScope
        or type(position_plan) is not position_pages.PositionHistoryPlanV1
        or type(identity) is not positions._NativePositionIdentityV1
    ):
        _deny("history_source_preflight_invalid")
    position_plan.__post_init__()
    identity.__post_init__()
    value = {
        "schema_version": SCHEMA,
        "policy_sha256": POLICY_SHA256,
        "scope": [scope.environment, scope.account_id, scope.settlement_currency],
        "sources": [],
        "batches": [],
        "position_plan": {
            "registration_region": position_plan.registration_region,
            "origin": position_plan.origin,
            "account_binding_sha256": position_plan.account_binding_sha256,
            "session_binding_sha256": position_plan.session_binding_sha256,
            "created_at": position_plan.created_at.isoformat(),
            "cutoff_u_time_ms": position_plan.cutoff_u_time_ms,
        },
        "position_identity": {
            "uid": identity.uid,
            "main_uid": identity.main_uid,
            "account_level": identity.account_level,
            "position_mode": identity.position_mode,
            "declared_scope_currency": identity.declared_scope_currency,
        },
        "position_archive_hex": "",
        "temporal": temporal,
        "funding_source_status": "NOT_ACQUIRED",
        "native_history_prefix_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
    expected_size = _preflight_freeze(value, sources, batches, position_archive)
    value["sources"] = [chain_document(chain) for chain, _ in sources]
    value["batches"] = [batch_document(batch) for batch in batches]
    value["position_archive_hex"] = position_archive.raw_archive.hex()
    raw = journal.canonical(value)
    if len(raw) != expected_size or len(raw) > MAX_BUNDLE_BYTES:
        _deny("history_source_byte_limit")
    replay_history_source_bundle_v1(raw, expected_sha256=sha(raw))
    return raw


def replay_history_source_bundle_v1(raw, *, expected_sha256):
    """Pure audit replay; even exact durable bytes return only an inert DTO."""
    try:
        if type(expected_sha256) is not str or sha(raw) != expected_sha256:
            _deny("history_source_pin_mismatch")
        value = _json(raw)
        if type(value) is not dict or set(value) != {
            "schema_version",
            "policy_sha256",
            "scope",
            "sources",
            "batches",
            "position_plan",
            "position_identity",
            "position_archive_hex",
            "temporal",
            "funding_source_status",
            "native_history_prefix_verified",
            "account_complete",
            "execution_authority",
        }:
            _deny()
        if (
            value["schema_version"] != SCHEMA
            or value["policy_sha256"] != POLICY_SHA256
            or value["funding_source_status"] != "NOT_ACQUIRED"
            or any(
                value[key] is not False
                for key in (
                    "native_history_prefix_verified",
                    "account_complete",
                    "execution_authority",
                )
            )
        ):
            _deny("history_source_authority_forbidden")
        if type(value["scope"]) is not list or len(value["scope"]) != 3:
            _deny()
        environment, uid, ccy = value["scope"]
        scope = LedgerScope(account_id=uid, settlement_currency=ccy)
        if environment != "demo" or ccy != "USDT":
            _deny("history_source_scope_invalid")
        if (
            type(value["sources"]) is not list
            or not 1 <= len(value["sources"]) <= observed.MAX_CAPTURES
        ):
            _deny("history_source_population_bound")
        sources, packets, joins, records = [], [], [], []
        for raw_chain in value["sources"]:
            chain = _chain(raw_chain)
            reference, packet, source_records, source_joins = proof._source(
                chain, scope
            )
            sources.append((chain, reference))
            packets.append(packet)
            records.append(source_records)
            joins.append(source_joins)
        sources = tuple(sources)
        if type(value["batches"]) is not list or len(value["batches"]) != len(sources):
            _deny("history_source_prefix_missing")
        batches = []
        for item in value["batches"]:
            if type(item) is not dict or set(item) != {
                "sequence",
                "head_sha256",
                "document_hex",
                "receipt_hex",
            }:
                _deny()
            batches.append(
                ObservationBatchReadback(
                    item["sequence"],
                    item["head_sha256"],
                    _hex(item["document_hex"], observed.MAX_SOURCE_BYTES),
                    observed.ObservationReplay(
                        _hex(item["receipt_hex"], observed.MAX_RECEIPT_BYTES)
                    ),
                )
            )
        batches = tuple(batches)
        lifecycle.source_set_sha256(sources)
        lifecycle._index(sources, batches, scope, batches[-1].event_sha256)
        plan_doc = value["position_plan"]
        if type(plan_doc) is not dict or set(plan_doc) != {
            "registration_region",
            "origin",
            "account_binding_sha256",
            "session_binding_sha256",
            "created_at",
            "cutoff_u_time_ms",
        }:
            _deny()
        position_plan = position_pages.PositionHistoryPlanV1(
            **{**plan_doc, "created_at": datetime.fromisoformat(plan_doc["created_at"])}
        )
        identity_doc = value["position_identity"]
        if (
            type(identity_doc) is not dict
            or set(identity_doc)
            != {
                "uid",
                "main_uid",
                "account_level",
                "position_mode",
                "declared_scope_currency",
            }
            or identity_doc["declared_scope_currency"] != "USDT"
        ):
            _deny("history_source_identity_changed")
        identity = positions._NativePositionIdentityV1(
            **{
                key: item
                for key, item in identity_doc.items()
                if key != "declared_scope_currency"
            }
        )
        packet = packets[-1]
        if (
            identity.uid != scope.account_id
            or identity.main_uid != packet.plan.expected_main_uid
            or position_plan.session_binding_sha256
            != sources[-1][1].session_binding_sha256
        ):
            _deny("history_source_identity_changed")
        configs = [
            page
            for page in packet.observations
            if page.request.endpoint == positions.CONFIG_ENDPOINT
        ]
        if len(configs) != 2:
            _deny("history_source_identity_changed")
        expected_config = {
            "uid": identity.uid,
            "mainUid": identity.main_uid,
            "acctLv": identity.account_level,
            "posMode": identity.position_mode,
        }
        for page in configs:
            config = capture._decode_json(
                page.response_body, limit=positions.MAX_CONFIG_BYTES, wire=True
            )[0]["data"][0]
            if any(
                type(config.get(key)) is not str or config[key] != item
                for key, item in expected_config.items()
            ):
                _deny("history_source_identity_changed")
        if (
            position_plan.registration_region != "global"
            or position_plan.origin != packet.plan.origin
            or position_plan.cutoff_u_time_ms
            != position_pages._milliseconds(packet.plan.history_start)
        ):
            _deny("history_source_position_cutoff_changed")
        archive = _hex(value["position_archive_hex"], positions.MAX_ARCHIVE_BYTES)
        position = positions._verify_native_source_archive(
            position_plan, identity, archive, expected_sha256=sha(archive)
        )
        _temporal(
            value["temporal"],
            packet=packet,
            records=records[-1],
            joins=joins[-1],
            scope=scope,
            position=position,
            position_plan=position_plan,
        )
        return HistorySourceFactsV1(
            raw,
            expected_sha256,
            sha(journal.canonical(value["temporal"])),
            scope,
            sources,
            batches,
            sources[-1][1],
            packet,
            position,
        )
    except HistorySourceBundleError:
        raise
    except Exception:  # noqa: BLE001 -- pure malformed private evidence is redacted
        _deny()


def _positions_trace(trace, position, plan):
    if type(trace) is not dict or set(trace) != {"events", "pages"}:
        _deny("history_positions_trace_invalid")
    archive = _json(position.raw_archive, positions.MAX_ARCHIVE_BYTES)
    raw_pages = [positions._read_record(archive["config_before"])]
    pages = positions._unfreeze_pages(plan, archive["history_archive"])
    raw_pages.extend(
        positions._RawPage(
            page.raw_body,
            page.request_started_at,
            page.headers_received_at,
            page.body_completed_at,
            cert,
        )
        for page, cert in zip(pages, position.peer_certificate_sha256, strict=True)
    )
    raw_pages.append(positions._read_record(archive["config_after"]))
    if (
        type(trace["pages"]) is not list
        or len(trace["pages"]) != len(raw_pages)
        or type(trace["events"]) is not list
        or len(trace["events"]) > 4096
    ):
        _deny("history_positions_trace_invalid")
    events, cursor, prior, stamps = trace["events"], 0, None, []
    for index, raw in enumerate(raw_pages):
        if type(raw.certificate_sha256) is not str:
            _deny("history_native_tls_required")
        marker = trace["pages"][index]
        expected = {
            "schema_version": "ctcc.history_positions_retention.v1",
            "request_index": index,
            "observed_bytes": len(raw.raw),
            "raw_sha256": sha(raw.raw),
            "retention": "durable_secret_checked",
            "complete": True,
            "certificate_sha256": raw.certificate_sha256,
            "execution_authority": False,
        }
        if journal.canonical(marker) != journal.canonical(expected):
            _deny("history_positions_retention_changed")
        phases = [
            "request_start",
            "request_dispatch",
            "headers_received",
            "tls_verified",
        ]
        position_index = index - 1
        if index in (0, len(raw_pages) - 1):
            spec = positions._GetSpec(plan.origin, positions.CONFIG_ENDPOINT, ())
        else:
            _, origin, endpoint, parameters = position_pages.request_for_page(
                plan,
                None if not position_index else pages[position_index - 1].next_after,
            )
            spec = positions._GetSpec(origin, endpoint, parameters)
        nbytes = 0
        body_hash = hashlib.sha256()
        for phase in (
            *phases,
            "body",
            "body_exhausted",
            "response_closed",
            "validated",
        ):
            if phase == "body":
                while (
                    cursor < len(events) and events[cursor].get("phase") == "body_chunk"
                ):
                    event = events[cursor]
                    count = event.get("observed_bytes")
                    if type(count) is not int or not nbytes < count <= len(raw.raw):
                        _deny("history_positions_chunk_changed")
                    body_hash.update(raw.raw[nbytes:count])
                    if event.get("prefix_sha256") != body_hash.hexdigest():
                        _deny("history_positions_chunk_changed")
                    prior = _trace_event(
                        event,
                        cursor,
                        index,
                        "body_chunk",
                        prior,
                        {"observed_bytes", "prefix_sha256"},
                    )
                    stamps.append(event["stamp"])
                    nbytes = count
                    cursor += 1
                if nbytes != len(raw.raw):
                    _deny("history_positions_chunk_missing")
                continue
            if cursor >= len(events):
                _deny("history_positions_phase_missing")
            event = events[cursor]
            extra = (
                {"request_sha256"}
                if phase == "request_start"
                else {"certificate_sha256"}
            )
            prior = _trace_event(event, cursor, index, phase, prior, extra)
            if phase == "request_start" and event["request_sha256"] != sha(
                canonical([spec.origin, spec.endpoint, spec.parameters])
            ):
                _deny("history_positions_request_changed")
            if phase != "request_start" and event["certificate_sha256"] != (
                raw.certificate_sha256 if phase == "tls_verified" else None
            ):
                _deny("history_positions_tls_changed")
            expected_at = {
                "request_start": raw.started,
                "headers_received": raw.headers,
                "body_exhausted": raw.completed,
            }.get(phase)
            if (
                expected_at is not None
                and utc_from_ns(event["stamp"]["utc_ns"]) != expected_at
            ):
                _deny("history_positions_timestamp_changed")
            stamps.append(event["stamp"])
            cursor += 1
    if len(events) != cursor + 1:
        _deny("history_positions_phase_inventory_invalid")
    _trace_event(
        events[cursor], cursor, len(raw_pages), "positions_closed", prior, set()
    )
    stamps.append(events[cursor]["stamp"])
    return stamps


def _trace_event(event, ordinal, request_index, phase, prior, extra):
    if (
        type(event) is not dict
        or set(event)
        != {"index", "request_index", "phase", "stamp", "previous_sha256", *extra}
        or type(event["index"]) is not int
        or event["index"] != ordinal
        or type(event["request_index"]) is not int
        or event["request_index"] != request_index
        or event["phase"] != phase
        or event["previous_sha256"] != prior
    ):
        _deny("history_positions_phase_binding_invalid")
    proof._stamp(event["stamp"])
    return sha(canonical(event))


def _temporal(value, *, packet, records, joins, scope, position, position_plan):
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "policy_sha256",
            "stage",
            "original_started",
            "witnesses",
            "finalization_witnesses",
            "positions_trace",
            "clock_files_hex",
            "source_readback_complete",
            "proof_persist_start",
        }
        or value["schema_version"] != PROOF_SCHEMA
        or value["policy_sha256"] != POLICY_SHA256
    ):
        _deny("history_temporal_contract_invalid")
    stage = value["stage"]
    if (
        type(stage) is not dict
        or set(stage) != {"kind", "invocation_id", "started"}
        or stage["kind"] != "native_archival_acquisition_v1"
        or type(stage["invocation_id"]) is not str
        or len(stage["invocation_id"]) != 32
        or packet.barrier_completed_at != utc_from_ns(stage["started"]["utc_ns"])
    ):
        _deny("history_temporal_stage_invalid")
    files = value["clock_files_hex"]
    if type(files) is not dict or set(files) != set(proof.FILES):
        _deny("history_temporal_files_invalid")
    files = {name: _hex(raw, 512 * 1024) for name, raw in files.items()}
    host = proof.public_clock.replay_clock_observation(files["host-before.json"])
    profile = host["observation"]["diagnostic"]["profile_id"]
    hosts, _, traces = proof._clocks(
        files,
        stage=stage,
        scope_sha=proof.scope_sha256(scope),
        expected_profile=profile,
    )
    position_stamps = _positions_trace(
        value["positions_trace"], position, position_plan
    )
    _b1_temporal(
        value,
        stage=stage,
        packet=packet,
        records=records,
        joins=joins,
        hosts=hosts,
        traces=traces,
        position_stamps=position_stamps,
    )


def _b1_temporal(
    value, *, stage, packet, records, joins, hosts, traces, position_stamps
):
    witnesses = value["witnesses"]
    if (
        type(witnesses) is not list
        or len(witnesses) != len(joins) * len(native.PAGE_PHASES) + 1
    ):
        raise HistorySourceBundleError("native_account_phase_inventory_invalid")
    previous, stamps = (
        None,
        [
            value["original_started"],
            stage["started"],
            hosts[0]["diagnostic"]["host_before"]["request_start"],
            hosts[0]["sample"],
            *traces[0],
        ],
    )
    for index, witness in enumerate(witnesses):
        if type(witness) is not dict or set(witness) != {
            "index",
            "phase",
            "request_index",
            "stream",
            "page_index",
            "stamp",
            "source_utc",
            "previous_sha256",
        }:
            raise HistorySourceBundleError("native_account_phase_contract_invalid")
        request_index, phase_index = divmod(index, len(native.PAGE_PHASES))
        source_close = index == len(witnesses) - 1
        page = None if source_close else packet.observations[request_index]
        phase = "source_closed" if source_close else native.PAGE_PHASES[phase_index]
        proof._stamp(witness["stamp"])
        if (
            type(witness["index"]) is not int
            or witness["index"] != index
            or type(witness["request_index"]) is not int
            or witness["request_index"] != request_index
            or type(witness["page_index"]) is not int
            or witness["phase"] != phase
            or witness["previous_sha256"] != previous
            or witness["stream"]
            != ("account_source" if source_close else page.request.stream)
            or witness["page_index"] != (0 if source_close else page.page_index)
            or witness["source_utc"]
            != utc_from_ns(witness["stamp"]["utc_ns"]).isoformat()
        ):
            raise HistorySourceBundleError("native_account_phase_binding_invalid")
        expected_utc = (
            None
            if source_close or phase == "request_dispatch"
            else {
                "request_start": page.request_started_at.isoformat(),
                "headers_received": page.headers_received_at.isoformat(),
                "body_exhausted": joins[request_index]["body_exhausted_at"],
                "response_closed": page.body_completed_at.isoformat(),
            }[phase]
        )
        if expected_utc is not None and witness["source_utc"] != expected_utc:
            raise HistorySourceBundleError("native_account_original_utc_changed")
        if phase in ("request_start", "request_dispatch") and (
            witness["stamp"]["utc_ns"] <= stage["started"]["utc_ns"]
            or witness["stamp"]["monotonic_ns"] <= stage["started"]["monotonic_ns"]
        ):
            raise HistorySourceBundleError("native_account_initial_barrier_not_crossed")
        previous = sha(canonical(witness))
        stamps.append(witness["stamp"])
    finalizers = value["finalization_witnesses"]
    if type(finalizers) is not list or len(finalizers) != 2:
        raise HistorySourceBundleError("native_account_finalization_inventory_invalid")
    for finalizer, purpose in zip(finalizers, ("source", "journal"), strict=True):
        if (
            type(finalizer) is not dict
            or set(finalizer)
            != {
                "kind",
                "finalizer_id",
                "invocation_binding_sha256",
                "started",
                "completed",
            }
            or finalizer["kind"] != f"original_b1_bounded_{purpose}_finalization"
            or type(finalizer["finalizer_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", finalizer["finalizer_id"]) is None
            or finalizer["invocation_binding_sha256"]
            != sha(canonical([stage["invocation_id"], finalizer["finalizer_id"]]))
        ):
            raise HistorySourceBundleError(
                "native_account_finalization_inventory_invalid"
            )
        proof._stamp(finalizer["started"])
        proof._stamp(finalizer["completed"])
    if finalizers[0]["finalizer_id"] == finalizers[1]["finalizer_id"]:
        raise HistorySourceBundleError("native_account_finalization_inventory_invalid")
    terminal = datetime.fromisoformat(records[-1]["observed_at"])
    if (
        not utc_from_ns(finalizers[1]["started"]["utc_ns"])
        <= terminal
        <= utc_from_ns(finalizers[1]["completed"]["utc_ns"])
    ):
        raise HistorySourceBundleError("native_account_finalization_clock_invalid")
    stamps.extend(
        [
            finalizers[0]["started"],
            finalizers[0]["completed"],
            finalizers[1]["started"],
            finalizers[1]["completed"],
            *position_stamps,
            hosts[1]["diagnostic"]["host_before"]["request_start"],
            hosts[1]["sample"],
            *traces[1],
            value["source_readback_complete"],
            value["proof_persist_start"],
        ]
    )
    proof._stamp(value["source_readback_complete"])
    proof._stamp(value["proof_persist_start"])
    # Each sample is compared against the same original anchor. This preserves
    # the existing 5ms/60s bounds even for >512 page witnesses; no relaxed policy.
    for prior, stamp in pairwise(stamps):
        validate_stamps((stamps[0], prior, stamp))
