"""A bounded original CURRENT B1/B3 bundle, never a replacement genesis.

This container reuses the existing native V1 acquisition/positions proof. It
retains the CURRENT's original B3 sequence and every declared original witness.
Only a separate full-prefix DB0052 replay establishes a prefix observation.
Saved bytes issue neither a native owner nor an account revision.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.database.repositories.account_observation_index import ObservationBatchReadback
from app.domain import account_history_partition_v1 as partition
from app.domain.source_primitives import sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_history_source_bundle_v1 as original
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import (
    account_positions_history_collector_v1 as position_collector,
)
from app.trade_qualification import account_positions_history_v1 as position_pages
from app.trade_qualification.reservations import LedgerScope

SCHEMA = "ctcc.native_history_current_source_bundle.v2"
POLICY_SHA256 = sha(
    journal.canonical(
        {
            "schema_version": SCHEMA,
            "original_native_policy_sha256": original.POLICY_SHA256,
            "original_b3_policy_sha256": observed.POLICY_SHA256,
            "current": "exact_original_sequence_reference_and_joint_receipt",
            "witnesses": "all_original_declared_witnesses_in_original_order",
            "maximum_sources": observed.MAX_CAPTURES,
            "maximum_bundle_bytes": original.MAX_BUNDLE_BYTES,
            "prefix": "not_attested_by_a_current_bundle",
            "missing_native_registration": "never_backfill_or_reissue",
            "account_complete": False,
            "execution_authority": False,
        }
    )
)


class CurrentHistoryBundleError(ValueError):
    """Closed local errors, never source or database text."""


def _deny(code="current_history_bundle_invalid"):
    raise CurrentHistoryBundleError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class CurrentHistorySourceFactsV2:
    bundle_raw: bytes = field(repr=False)
    bundle_sha256: str
    proof_sha256: str
    scope: LedgerScope = field(repr=False)
    sources: tuple = field(repr=False)
    batches: tuple = field(repr=False)
    reference: observed.CaptureReference = field(repr=False)
    packet: capture.DemoAccountPacket = field(repr=False)
    positions: position_collector._NativePositionArchiveV1 = field(repr=False)
    frame: stream.OriginalHistoryFrameV1 = field(repr=False)
    native_history_prefix_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision: None = field(default=None, init=False)
    funding_source_status: str = field(default="NOT_ACQUIRED", init=False)
    funding_accrual_events: None = field(default=None, init=False)
    registration_site: str = field(default="UNKNOWN", init=False)
    execution_authority: bool = field(default=False, init=False)
    predictive_oos_eligible: bool = field(default=False, init=False)
    admission: str = field(default="DENY", init=False)


def _batch(value):
    if type(value) is not dict or set(value) != {
        "sequence",
        "head_sha256",
        "document_hex",
        "receipt_hex",
    }:
        _deny()
    return ObservationBatchReadback(
        value["sequence"],
        value["head_sha256"],
        original._hex(value["document_hex"], observed.MAX_SOURCE_BYTES),
        observed.ObservationReplay(
            original._hex(value["receipt_hex"], observed.MAX_RECEIPT_BYTES)
        ),
    )


def _checked_sources(scope, sources, batches):
    """Check an original support set; its last member is CURRENT, not genesis."""
    if (
        type(scope) is not LedgerScope
        or type(sources) is not tuple
        or type(batches) is not tuple
        or not 1 <= len(sources) <= observed.MAX_CAPTURES
        or len(sources) != len(batches)
    ):
        _deny("current_history_source_bound")
    # Bound all original bytes before any full query/native proof replay.
    # This identity helper validates raw/readback types, not source authority.
    original.lifecycle.source_set_sha256(sources)
    total = 0
    for (chain, _), batch in zip(sources, batches, strict=True):
        if (
            type(batch) is not ObservationBatchReadback
            or type(batch.replay) is not observed.ObservationReplay
            or type(batch.document_json) is not bytes
            or type(batch.replay.receipt_json) is not bytes
        ):
            _deny("current_history_batch_identity")
        total += len(batch.document_json) + len(batch.replay.receipt_json)
        for point in chain:
            total += sum(
                len(raw)
                for raw in (
                    point.event.event_json,
                    point.event.raw_body,
                    point.event.packet_payload,
                )
                if raw is not None
            )
            if total > observed.MAX_SOURCE_BYTES:
                _deny("current_history_source_bound")
    scope_doc = [scope.environment, scope.account_id, scope.settlement_currency]
    index, references, packets, joins, records = {}, [], [], [], []
    last = 0
    for (chain, declared), batch in zip(sources, batches, strict=True):
        if (
            type(batch) is not ObservationBatchReadback
            or type(batch.replay) is not observed.ObservationReplay
            or type(batch.document_json) is not bytes
            or type(batch.replay.receipt_json) is not bytes
            or type(batch.sequence) is not int
            or not last < batch.sequence
            or batch.event_sha256 != journal.digest(batch.document_json)
        ):
            _deny("current_history_batch_identity")
        document, _ = partition._b3(
            batch.document_json, scope=scope_doc, source_policy=observed.POLICY_SHA256
        )
        reference, packet, source_records, source_joins = proof._source(chain, scope)
        if (
            reference != declared
            or document["sequence"] != batch.sequence
            or document["reference"] != observed.reference_document(reference)
            or document["receipt_sha256"] != batch.replay.receipt_sha256
        ):
            _deny("current_history_original_source_changed")
        # Each old B3 support record retains its own original bytes and pins.
        # The final CURRENT joint replay below independently checks membership.
        index[batch.sequence] = {
            **document,
            "event_sha256": batch.event_sha256,
            "chain_sha256": stream._chain_measure(chain)[1],
        }
        references.append(reference)
        packets.append(packet)
        records.append(source_records)
        joins.append(source_joins)
        last = batch.sequence
    current = batches[-1]
    witnesses = tuple(
        stream.OriginalHistoryWitnessV1(batch.sequence, source[0])
        for source, batch in zip(sources[:-1], batches[:-1], strict=True)
    )
    frame = stream.OriginalHistoryFrameV1(sources[-1][0], current, witnesses)
    document = index[current.sequence]
    stream._original_receipt(frame, document, scope, index)
    return frame, references[-1], packets[-1], records[-1], joins[-1]


def freeze_current_history_source_bundle_v2(
    *, scope, sources, batches, position_plan, identity, position_archive, temporal
):
    """One bounded original support set; never truncate or renumber B3 rows."""
    if (
        type(position_plan) is not position_pages.PositionHistoryPlanV1
        or type(identity) is not position_collector._NativePositionIdentityV1
    ):
        _deny()
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
        "native_history_prefix_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
    # The existing byte preflight requires only a bounded source set with one
    # batch per source, not sequence=1..N; it charges every byte before .hex().
    expected_size = original._preflight_freeze(
        value, sources, batches, position_archive
    )
    _checked_sources(scope, sources, batches)
    value["sources"] = [original.chain_document(chain) for chain, _ in sources]
    value["batches"] = [original.batch_document(batch) for batch in batches]
    value["position_archive_hex"] = position_archive.raw_archive.hex()
    raw = journal.canonical(value)
    if len(raw) != expected_size or len(raw) > original.MAX_BUNDLE_BYTES:
        _deny("current_history_bundle_bound")
    replay_current_history_source_bundle_v2(raw, expected_sha256=sha(raw))
    return raw


def replay_current_history_source_bundle_v2(raw, *, expected_sha256):
    """Pure replay is always an inert DTO, including valid capture33 bytes."""
    try:
        if type(expected_sha256) is not str or sha(raw) != expected_sha256:
            _deny("current_history_bundle_pin")
        value = original._json(raw)
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
            "native_history_prefix_verified",
            "account_complete",
            "execution_authority",
        }:
            _deny()
        if (
            value["schema_version"] != SCHEMA
            or value["policy_sha256"] != POLICY_SHA256
            or any(
                value[key] is not False
                for key in (
                    "native_history_prefix_verified",
                    "account_complete",
                    "execution_authority",
                )
            )
            or type(value["scope"]) is not list
            or len(value["scope"]) != 3
            or value["scope"][0] != "demo"
            or value["scope"][2] != "USDT"
            or type(value["sources"]) is not list
            or type(value["batches"]) is not list
            or not 1 <= len(value["sources"]) <= observed.MAX_CAPTURES
            or len(value["sources"]) != len(value["batches"])
        ):
            _deny("current_history_bundle_contract")
        scope = LedgerScope(account_id=value["scope"][1], settlement_currency="USDT")
        chains = tuple(original._chain(item) for item in value["sources"])
        sources = tuple((chain, observed.source_reference(chain)) for chain in chains)
        batches = tuple(_batch(item) for item in value["batches"])
        frame, reference, packet, records, joins = _checked_sources(
            scope, sources, batches
        )
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
        plan = position_pages.PositionHistoryPlanV1(
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
            _deny("current_history_position_identity")
        identity = position_collector._NativePositionIdentityV1(
            **{
                key: item
                for key, item in identity_doc.items()
                if key != "declared_scope_currency"
            }
        )
        if (
            identity.uid != scope.account_id
            or identity.main_uid != packet.plan.expected_main_uid
            or plan.session_binding_sha256 != reference.session_binding_sha256
            or plan.registration_region != "global"
            or plan.origin != packet.plan.origin
            or plan.cutoff_u_time_ms
            != position_pages._milliseconds(packet.plan.history_start)
        ):
            _deny("current_history_position_identity")
        configs = [
            page
            for page in packet.observations
            if page.request.endpoint == position_collector.CONFIG_ENDPOINT
        ]
        if len(configs) != 2:
            _deny("current_history_position_identity")
        expected_config = {
            "uid": identity.uid,
            "mainUid": identity.main_uid,
            "acctLv": identity.account_level,
            "posMode": identity.position_mode,
        }
        for page in configs:
            row = capture._decode_json(
                page.response_body, limit=position_collector.MAX_CONFIG_BYTES, wire=True
            )[0]["data"][0]
            if any(
                type(row.get(key)) is not str or row[key] != item
                for key, item in expected_config.items()
            ):
                _deny("current_history_position_identity")
        archive = original._hex(
            value["position_archive_hex"], position_collector.MAX_ARCHIVE_BYTES
        )
        position = position_collector._verify_native_source_archive(
            plan, identity, archive, expected_sha256=sha(archive)
        )
        original._temporal(
            value["temporal"],
            packet=packet,
            records=records,
            joins=joins,
            scope=scope,
            position=position,
            position_plan=plan,
        )
        return CurrentHistorySourceFactsV2(
            raw,
            expected_sha256,
            sha(journal.canonical(value["temporal"])),
            scope,
            sources,
            batches,
            reference,
            packet,
            position,
            frame,
        )
    except CurrentHistoryBundleError:
        raise
    except Exception:  # noqa: BLE001 -- private original-source errors never escape.
        _deny()
