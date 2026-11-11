"""Read back a completed D/V campaign without creating capture authority."""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
)
from app.demo_automation.native_private_root_v2 import require_private_evidence_base_v2
from app.domain.source_primitives import canonical, decode, sha, validate_stamps
from app.mie.validation.artifact import FrozenGate3Artifact
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.batch_replay import BoundMinute
from app.mie.validation.development_validation_source_collection_v1 import (
    MAX_RECORD_BYTES,
    _pin,
    _record,
    _root_context,
    _root_identity,
)
from app.mie.validation.development_validation_source_collection_v6 import (
    _MAX_RETAINED,
    CollectionIdentityV6,
    CollectionReadOnlyV6,
    OrdinalClaimV6,
)
from app.mie.validation.native_availability_bundle_v5 import MAX_FILE_BYTES
from app.mie.validation.native_availability_bundle_v6 import (
    NativeAvailabilityBundleV6,
    replay_native_availability_bundle_v6,
)
from app.mie.validation.post_publication_availability_v3 import (
    _observation as _post_read,
)
from app.mie.validation.post_publication_availability_v3 import _pins
from app.mie.validation.post_publication_availability_v4 import (
    health_fresh_at,
    utc_nanoseconds,
)
from app.mie.validation.post_publication_availability_v6 import (
    CONSUMER_LEASE_NS,
    ceil_native_utc,
    replay_native_availability_v6,
)
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    DevvalCampaignBatchV1,
    _build_batch,
)
from app.mie.validation.prospective_devval_campaign_custody_v1 import (
    DevvalCampaignCustodyRepositoryV1,
    DevvalCampaignDispatchClaimV1,
    DevvalCampaignPublicationV1,
    _xid,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    _source_plan,
    verify_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_run_v1 import (
    _RECEIPT_MAX,
    _ROOTS,
    _ROSTER_MAX,
    _observation,
)
from app.mie.validation.prospective_devval_slot_v6 import (
    _claim,
    _slot,
    _verify_prefix,
    _verify_slot,
)
from app.mie.validation.replay import PointInTimeBar
from app.public_market_source.public_receipt_storage import _replay_directory
from app.trade_evidence.storage import _root_path, _WindowsAPI, _WindowsDirectory


class DevvalCampaignReplayError(ValueError):
    """A fixed archival refusal, with no writes or capability issuance."""


@dataclass(frozen=True, slots=True)
class DevvalCampaignReplayV1:
    campaign_sha256: str
    completion_sha256: str
    root_binding_sha256: str
    rows: tuple[BoundMinute, ...]
    development_rows: int
    validation_rows: int
    development_cutoff_utc_ns: int
    validation_cutoff_utc_ns: int
    row_inventory_sha256: str
    batch: FrozenGate3Artifact[DevvalCampaignBatchV1] | None = None
    trusted_clock_verified: ClassVar[Literal[False]] = False
    independently_protected: ClassVar[Literal[False]] = False
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


def _need(condition, reason="devval_campaign_replay_changed"):
    if not condition:
        raise DevvalCampaignReplayError(reason) from None


@contextmanager
def _read_root(path: Path):
    if os.name != "nt":
        with _root_context(path) as directory:
            yield directory
        return
    path = _root_path(path)
    api = _WindowsAPI()
    with ExitStack() as stack:
        for parent in (*reversed(path.parents), path):
            handle = api.open_directory(parent)
            stack.callback(api.close, handle)
        yield _WindowsDirectory(path, api)


def _private_identity(path: Path) -> tuple[int, int]:
    require_private_evidence_base_v2(str(path))
    with _read_root(path):
        return _root_identity(path)


def _json(raw, maximum, *, schema, fields):
    value = decode(raw, maximum)
    _need(
        type(value) is dict
        and set(value) == fields | {"schema"}
        and value["schema"] == schema
        and canonical(value) == raw
    )
    return value


def _clock_bracket(stamps, database_time, first_open):
    before, after = validate_stamps(stamps)
    observed = datetime.fromisoformat(database_time)
    _need(
        observed.tzinfo is not None
        and observed.utcoffset() == UTC.utcoffset(observed)
        and observed.isoformat() == database_time
        and before.utc_ns <= _ns(observed) <= after.utc_ns < first_open,
        "devval_campaign_original_pre_window_clock_unproven",
    )
    return observed


def _check_dispatch(publication, claim, roster, document, binding_sha, first_open):
    _need(type(publication) is DevvalCampaignPublicationV1)
    _need(type(claim) is DevvalCampaignDispatchClaimV1)
    retained = roster["publication"]
    expected = _observation(publication)
    fresh_publication_read = expected.pop("database_read_at")
    expected.pop("readback_xid")
    expected.pop("native_readback")
    original_fields = {"database_read_at", "readback_xid", "native_readback"}
    _need(
        type(retained) is dict
        and set(retained) == set(expected) | original_fields
        and all(retained[key] == value for key, value in expected.items())
        and document["campaign_sha256"]
        == publication.campaign_sha256
        == claim.campaign_sha256
        and document["root_binding_sha256"] == claim.root_binding_sha256 == binding_sha
        and document["collector_role_oid"] == claim.collector_role_oid
        and document["dispatch_xid"] == claim.dispatch_xid
        and document["claimed_at"] == claim.claimed_at.isoformat()
        and document["predictive_oos_eligible"] is False
        and document["execution_authority"] is False,
        "devval_campaign_committed_dispatch_changed",
    )
    seen_publication = _clock_bracket(
        retained["native_readback"], retained["database_read_at"], first_open
    )
    seen_dispatch = _clock_bracket(
        document["native_readback"], document["database_read_at"], first_open
    )
    original_xids = (
        publication.publication_xid,
        publication.acknowledgement_xid,
        retained["readback_xid"],
        claim.dispatch_xid,
        document["readback_xid"],
    )
    _need(
        publication.acknowledged_at is not None
        and publication.recorded_at
        <= publication.acknowledged_at
        <= seen_publication
        <= claim.claimed_at
        <= seen_dispatch
        <= claim.database_read_at
        and seen_publication <= datetime.fromisoformat(fresh_publication_read)
        and len(set(original_xids)) == 5
        and all(_xid(item) for item in original_xids)
        and len(
            {
                publication.publisher_role_oid,
                publication.observer_role_oid,
                claim.collector_role_oid,
            }
        )
        == 3,
        "devval_campaign_committed_dispatch_changed",
    )


def _verify_minute(
    coordinate, native_receipt, slot_receipt, decision, bundle_raw, evidence_raw
):
    bundle_pin, evidence_pin = (
        slot_receipt["bundle_sha256"],
        slot_receipt["evidence_sha256"],
    )
    bundle = replay_native_availability_bundle_v6(
        bundle_raw, expected_sha256=bundle_pin
    )
    evidence = replay_native_availability_v6(evidence_raw, expected_sha256=evidence_pin)
    source = bundle.clock_v4.source_v3.v2_capture
    claim_pin = native_receipt.claim_sha256
    hashes = {item.name: item.sha256 for item in native_receipt.files}
    validate_stamps((evidence.final_ready.model_dump(), decision.cutoff.model_dump()))
    health_fresh_at(evidence.final_health.raw_bytes(), decision.cutoff)
    _need(
        native_receipt.status == "NATIVE_DURABLE_AVAILABLE"
        and native_receipt.reason == "captured"
        and native_receipt.forensic_status == "completed"
        and native_receipt.stage1_forensic_sha256 == bundle.stage1_forensic_sha256
        and hashes
        == {
            "bundle-v6.json": bundle_pin,
            "evidence-v6.json": evidence_pin,
            "final-health.json": evidence.final_health.raw_sha256,
        }
        and claim_pin
        == evidence.ordinal_claim_sha256
        == bundle.ordinal_claim_sha256
        == decision.ordinal_claim_sha256
        and decision.stage1_forensic_sha256 == bundle.stage1_forensic_sha256
        and evidence.source_clock_v4 == bundle.clock_v4
        and evidence.custody.bundle_sha256 == bundle_pin
        and decision.source_rows_sha256 == source.source_rows_sha256
        and decision.cutoff.monotonic_ns - evidence.final_ready.monotonic_ns
        <= CONSUMER_LEASE_NS
        and slot_receipt["public_readback_microsecond_utc_ns"]
        == utc_nanoseconds(source.payload_readback_at)
        and source.plan_sha256 == coordinate.capture_plan_sha256
        and source.instrument_id == coordinate.capture_plan.instrument_id
        and len(source.rows) == 1,
        "devval_campaign_native_minute_changed",
    )
    source_row = source.rows[0]
    available = ceil_native_utc(evidence.final_ready.utc_ns)
    _need(
        utc_nanoseconds(source_row.bar.closed_at) == coordinate.capture_plan.end_ns
        and utc_nanoseconds(available) <= decision.cutoff.utc_ns,
        "devval_campaign_native_minute_changed",
    )
    bound = BoundMinute(
        row=PointInTimeBar(
            source_row_id="okx-public-minute:" + source_row.source_row_identity,
            source_row_sha256=source_row.source_row_sha256,
            instrument_id=source.instrument_id,
            available_at=available,
            bar=source_row.bar,
        ),
        availability=AvailabilityProvenance(
            basis=AvailabilityBasis.MEASURED_ROW_RECEIPT,
            source_row_sha256=source_row.source_row_sha256,
            receipt_sha256=evidence_pin,
            bar_closed_at=source_row.bar.closed_at,
            observed_at=available,
            retrieved_at=available,
            available_at=available,
        ),
    )
    journal_pin = sha(
        canonical(
            {
                "checkpoint": bundle.checkpoint.model_dump(mode="json"),
                "files": [(item.path, item.raw_sha256) for item in bundle.files],
            }
        )
    )
    _need(
        journal_pin == slot_receipt["journal_replay_sha256"],
        "devval_campaign_original_journal_pin_changed",
    )
    return bound, bundle, evidence


def _journal_files(
    root: Path, checkpoint, file_pins: tuple[tuple[str, str], ...]
) -> None:
    with _read_root(root) as directory:
        _replay_directory(directory, checkpoint)
        for path, digest in file_pins:
            *parents, name = path.split("/")
            with ExitStack() as stack:
                child = directory
                for parent in parents:
                    child = stack.enter_context(child.child(parent))
                _need(
                    sha(child.read(name, MAX_FILE_BYTES)) == digest,
                    "devval_campaign_journal_readback_changed",
                )


def _check_journal_prefix(
    bundle: NativeAvailabilityBundleV6,
    final_bundle: NativeAvailabilityBundleV6,
    ordinal: int,
) -> None:
    end = final_bundle.witness_rows
    expected_files = {item.path: item.raw_sha256 for item in final_bundle.files}
    _need(
        bundle.checkpoint.sequence == ordinal + 1
        and bundle.checkpoint.genesis_sha256 == final_bundle.checkpoint.genesis_sha256
        and bundle.witness_rows == end[: len(bundle.witness_rows)]
        and all(
            expected_files.get(item.path) == item.raw_sha256 for item in bundle.files
        ),
        "devval_campaign_original_journal_prefix_changed",
    )


async def _verify_source_custody(
    bundle, bundle_raw, evidence, custody_repository, observation_repository
):
    source = bundle.clock_v4.source_v3
    retained = await custody_repository.read_retained_bundle_by_pins(
        expected_sha256=evidence.custody.bundle_sha256,
        proof_envelope_sha256=evidence.custody.proof_sha256,
    )
    _need(retained.bundle_raw == bundle_raw, "devval_campaign_source_custody_changed")
    observed = await observation_repository.read(
        **_pins(source.v2_capture, source.v2_capture_sha256)
    )
    _need(
        _post_read(observed) == source.post_read_observation,
        "devval_campaign_post_read_changed",
    )


def _native_claim(reader, coordinate):
    return OrdinalClaimV6(
        identity_sha256=reader.identity.canonical_sha256(),
        ordinal=coordinate.ordinal,
        capture_plan_sha256=coordinate.capture_plan_sha256,
    )


def _native_receipt(directory, reader, coordinate, native_pin):
    claim = _native_claim(reader, coordinate)
    receipt = reader._read_claimed_slot(
        directory, claim, expected_receipt_sha256=native_pin
    )
    with directory.child(f"batch-{coordinate.ordinal:08d}") as child:
        bundle_raw = child.read("bundle-v6.json", _MAX_RETAINED)
        evidence_raw = child.read("evidence-v6.json", _MAX_RETAINED)
    return receipt, bundle_raw, evidence_raw


async def _replay_block(
    block_root,
    *,
    plan,
    pairs,
    base,
    roster,
    witness,
    observation,
    custody,
    validation_open,
):
    native_root = block_root / "native"
    journal_root = block_root / "journal"
    result_root = block_root / "slots"
    identity_fields = roster["directory_identities"]
    _need(
        set(identity_fields) == set(_ROOTS)
        and all(
            list(_private_identity(block_root / name)) == identity_fields[name]
            for name in _ROOTS
        ),
        "devval_campaign_block_root_changed",
    )
    expected_ordinals = tuple(range(len(plan.coordinates)))
    _need(len(pairs) == len(expected_ordinals))
    with _read_root(native_root) as directory:
        identity = _record(
            directory.read("identity.json", MAX_RECORD_BYTES), CollectionIdentityV6
        )
        reader = CollectionReadOnlyV6(native_root, plan, identity)
        _need(
            identity.canonical_sha256() == roster["collection_identity_sha256"]
            and identity.journal_id == roster["journal_id"]
            and identity.journal_genesis_sha256 == roster["journal_genesis_sha256"]
            and reader.inventory(directory) == expected_ordinals
        )
        last_receipt, last_raw, _ = _native_receipt(
            directory, reader, plan.coordinates[-1], pairs[-1][0]
        )
    bundle_pin = next(
        item.sha256 for item in last_receipt.files if item.name == "bundle-v6.json"
    )
    final_bundle = replay_native_availability_bundle_v6(
        last_raw, expected_sha256=bundle_pin
    )
    latest = await witness.read_latest(identity.journal_genesis_sha256)
    _need(
        latest is not None
        and latest.journal_id == identity.journal_id
        and latest.state == "idle"
        and latest.checkpoint == final_bundle.checkpoint
        and latest.checkpoint.sequence == len(expected_ordinals)
        and final_bundle.witness_rows[-1].record_sha256 == latest.record_sha256,
        "devval_campaign_final_witness_changed",
    )
    journal_pins = tuple((item.path, item.raw_sha256) for item in final_bundle.files)
    _journal_files(journal_root, latest.checkpoint, journal_pins)
    expected_slots = {_slot(ordinal) for ordinal in expected_ordinals}
    previous = None
    rows = []
    for coordinate, (native_pin, slot_pin) in zip(plan.coordinates, pairs, strict=True):
        with _read_root(result_root) as result:
            _need(set(result.names()) == expected_slots)
            with result.child(_slot(coordinate.ordinal)) as child:
                slot_receipt, decision = _verify_slot(
                    child,
                    _claim(base, coordinate, previous),
                    coordinate,
                    identity.canonical_sha256(),
                    native_pin,
                    slot_pin,
                )
        with _read_root(native_root) as directory:
            native_receipt, bundle_raw, evidence_raw = _native_receipt(
                directory, reader, coordinate, native_pin
            )
        bound, bundle, evidence = _verify_minute(
            coordinate, native_receipt, slot_receipt, decision, bundle_raw, evidence_raw
        )
        _check_journal_prefix(bundle, final_bundle, coordinate.ordinal)
        cutoff = decision.cutoff.utc_ns
        _need(
            coordinate.partition == "validation" or cutoff < validation_open,
            "devval_campaign_development_cutoff_crosses_validation",
        )
        await _verify_source_custody(bundle, bundle_raw, evidence, custody, observation)
        rows.append((coordinate.partition, bound, cutoff, native_pin, slot_pin))
        previous = slot_pin
    with _read_root(native_root) as directory:
        _need(reader.inventory(directory) == expected_ordinals)
    _need(await witness.read_latest(identity.journal_genesis_sha256) == latest)
    return tuple(rows), latest.checkpoint, journal_pins


def _final_block_readback(
    block_root, *, plan, pairs, base, roster, checkpoint, journal_pins
):
    """Recheck all original bytes after the last async dependency returns."""
    _need(
        all(
            list(_private_identity(block_root / name))
            == roster["directory_identities"][name]
            for name in _ROOTS
        ),
        "devval_campaign_block_root_changed",
    )
    native_root = block_root / "native"
    with _read_root(native_root) as directory:
        identity = _record(
            directory.read("identity.json", MAX_RECORD_BYTES), CollectionIdentityV6
        )
        reader = CollectionReadOnlyV6(native_root, plan, identity)
        _need(
            identity.canonical_sha256() == roster["collection_identity_sha256"]
            and reader.inventory(directory) == tuple(range(len(pairs)))
        )
        for coordinate, (native_pin, _) in zip(plan.coordinates, pairs, strict=True):
            receipt = reader._read_claimed_slot(
                directory,
                _native_claim(reader, coordinate),
                expected_receipt_sha256=native_pin,
            )
            _need(receipt.status == "NATIVE_DURABLE_AVAILABLE")
    with _read_root(block_root / "slots") as result:
        _verify_prefix(result, pairs, plan, base)
    _journal_files(block_root / "journal", checkpoint, journal_pins)


async def replay_prospective_devval_campaign_v1(
    *,
    root: Path,
    expected_campaign_sha256: str,
    expected_completion_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
    expected_source_manifest_sha256: str,
    publication_repository: DevvalCampaignCustodyRepositoryV1,
    witness_repository: PublicReceiptWitnessRepository,
    observation_repository: PublicReceiptPostReadRepository,
    custody_repository: RuntimeBundleCustodyRepository,
    include_batch: bool = False,
) -> DevvalCampaignReplayV1:
    """Replay independently retained bytes and live DB readbacks without writes."""
    try:
        _need(
            type(root) is type(Path())
            and root.is_absolute()
            and type(include_batch) is bool
            and type(publication_repository) is DevvalCampaignCustodyRepositoryV1
            and type(witness_repository) is PublicReceiptWitnessRepository
            and type(observation_repository) is PublicReceiptPostReadRepository
            and observation_repository.witness_repository is witness_repository
            and type(custody_repository) is RuntimeBundleCustodyRepository,
            "devval_campaign_replay_inputs_invalid",
        )
        root_id = _private_identity(root)
        with _read_root(root) as directory:
            _need("complete.json" in directory.names(), "devval_campaign_incomplete")
            final_raw = directory.read("complete.json", _RECEIPT_MAX)
            _need(sha(final_raw) == _pin(expected_completion_sha256))
            payload = directory.read("campaign.json", _RECEIPT_MAX)
            roots = directory.read("roots.json", _ROSTER_MAX)
            dispatch_raw = directory.read("dispatch.json", _RECEIPT_MAX)
        source_checkpoint = {
            "expected_source_commit": expected_source_commit,
            "expected_source_tree": expected_source_tree,
            "expected_source_manifest_sha256": expected_source_manifest_sha256,
        }
        campaign = verify_prospective_devval_campaign_v1(
            payload,
            expected_campaign_sha256=expected_campaign_sha256,
            **source_checkpoint,
        )
        final = _json(
            final_raw,
            _RECEIPT_MAX,
            schema="ctcc.mie.devval_campaign_capture.v1",
            fields={
                "campaign_sha256",
                "dispatch_receipt_sha256",
                "block_receipt_sha256s",
                "row_count",
                "predictive_oos_eligible",
                "execution_authority",
            },
        )
        dispatch_pin, binding_sha = sha(dispatch_raw), sha(roots)
        _need(
            final["campaign_sha256"] == expected_campaign_sha256
            and final["dispatch_receipt_sha256"] == dispatch_pin
            and final["row_count"] == campaign.plan.expected_rows
            and final["predictive_oos_eligible"] is False
            and final["execution_authority"] is False
            and type(final["block_receipt_sha256s"]) is list
            and len(final["block_receipt_sha256s"]) == campaign.plan.block_count,
        )
        roster = _json(
            roots,
            _ROSTER_MAX,
            schema="ctcc.mie.devval_campaign_root_roster.v2",
            fields={
                "publication",
                "root_identity",
                "source_inventory_sha256",
                "blocks",
            },
        )
        _need(
            roster["root_identity"] == list(root_id)
            and type(roster["blocks"]) is list
            and len(roster["blocks"]) == campaign.plan.block_count
        )
        source_inventory_pin = _pin(roster["source_inventory_sha256"])
        dispatch = _json(
            dispatch_raw,
            _RECEIPT_MAX,
            schema="ctcc.mie.devval_campaign_dispatch.v2",
            fields={
                "campaign_sha256",
                "root_binding_sha256",
                "collector_role_oid",
                "dispatch_xid",
                "claimed_at",
                "database_read_at",
                "readback_xid",
                "native_readback",
                "predictive_oos_eligible",
                "execution_authority",
            },
        )
        first_open, validation_open = (
            _ns(campaign.plan.development.start_at),
            _ns(campaign.plan.validation.start_at),
        )
        db_args = {
            "expected_campaign_sha256": expected_campaign_sha256,
            "root_binding_sha256": binding_sha,
            **source_checkpoint,
        }
        publication, claim = await publication_repository.observe_dispatch(
            payload, **db_args
        )
        _check_dispatch(publication, claim, roster, dispatch, binding_sha, first_open)
        await custody_repository.verify_deployment()
        rows, seen, seen_roots = [], set(), {root_id}
        latest = {"development": 0, "validation": 0}
        totals = {"development": 0, "validation": 0}
        inventory = hashlib.sha256()
        previous = None
        names = {"campaign.json", "complete.json", "roots.json", "dispatch.json"}
        seen_journals, seen_geneses = set(), set()
        retention = []
        for ordinal, (record, block_pin) in enumerate(
            zip(roster["blocks"], final["block_receipt_sha256s"], strict=True)
        ):
            block_name = f"block-{ordinal:04d}"
            names.add(block_name)
            plan = _source_plan(campaign.plan, campaign.plan_sha256, ordinal)
            plan_pin = plan.canonical_sha256()
            _need(
                type(record) is dict
                and set(record)
                == {
                    "block",
                    "source_plan_sha256",
                    "collection_identity_sha256",
                    "journal_id",
                    "journal_genesis_sha256",
                    "directory_identities",
                }
                and record["block"] == block_name
                and record["source_plan_sha256"]
                == plan_pin
                == campaign.source_plan_sha256s[ordinal]
                and record["journal_id"] not in seen_journals
                and record["journal_genesis_sha256"] not in seen_geneses
            )
            seen_journals.add(record["journal_id"])
            seen_geneses.add(record["journal_genesis_sha256"])
            identities = record["directory_identities"]
            for identity in identities.values():
                item = tuple(identity)
                _need(item not in seen_roots)
                seen_roots.add(item)
            collection_pin = _pin(record["collection_identity_sha256"])
            with _read_root(root) as directory, directory.child(block_name) as block:
                _need(set(block.names()) == {*_ROOTS, "complete.json"})
                raw = block.read("complete.json", _RECEIPT_MAX)
            _need(sha(raw) == _pin(block_pin))
            sealed = _json(
                raw,
                _RECEIPT_MAX,
                schema="ctcc.mie.devval_campaign_block.v1",
                fields={
                    "dispatch_receipt_sha256",
                    "collection_identity_sha256",
                    "previous_block_sha256",
                    "receipt_pairs",
                    "predictive_oos_eligible",
                    "execution_authority",
                },
            )
            _need(
                sealed["dispatch_receipt_sha256"] == dispatch_pin
                and sealed["collection_identity_sha256"] == collection_pin
                and sealed["previous_block_sha256"] == previous
                and sealed["predictive_oos_eligible"] is False
                and sealed["execution_authority"] is False
                and type(sealed["receipt_pairs"]) is list
                and all(
                    type(pair) is list and len(pair) == 2
                    for pair in sealed["receipt_pairs"]
                )
            )
            pairs = tuple(
                (_pin(native), _pin(slot)) for native, slot in sealed["receipt_pairs"]
            )
            base = {
                "collection_identity_sha256": collection_pin,
                "campaign_manifest_sha256": expected_campaign_sha256,
                "campaign_block_ordinal": ordinal,
                "campaign_dispatch_receipt_sha256": dispatch_pin,
                "source_plan_sha256": plan_pin,
                "source_inventory_sha256": source_inventory_pin,
            }
            results, checkpoint, journal_pins = await _replay_block(
                root / block_name,
                plan=plan,
                pairs=pairs,
                base=base,
                roster=record,
                witness=witness_repository,
                observation=observation_repository,
                custody=custody_repository,
                validation_open=validation_open,
            )
            retention.append((record, pairs, base, checkpoint, journal_pins))
            for partition, row, cutoff, native_pin, slot_pin in results:
                identity = (row.row.instrument_id, row.row.bar.closed_at)
                _need(identity not in seen, "devval_campaign_duplicate_minute")
                seen.add(identity)
                rows.append(row)
                latest[partition] = max(latest[partition], cutoff)
                totals[partition] += 1
                encoded = canonical(
                    (partition, row.canonical_sha256(), cutoff, native_pin, slot_pin)
                )
                inventory.update(len(encoded).to_bytes(8, "big"))
                inventory.update(encoded)
            previous = block_pin
        _need(
            len(rows) == campaign.plan.expected_rows
            and 0 < latest["development"] < validation_open <= latest["validation"]
        )
        publication, claim = await publication_repository.observe_dispatch(
            payload, **db_args
        )
        _check_dispatch(publication, claim, roster, dispatch, binding_sha, first_open)
        with _read_root(root) as directory:
            _need(
                _root_identity(root) == root_id
                and set(directory.names()) == names
                and directory.read("complete.json", _RECEIPT_MAX) == final_raw
                and directory.read("campaign.json", _RECEIPT_MAX) == payload
                and directory.read("roots.json", _ROSTER_MAX) == roots
                and directory.read("dispatch.json", _RECEIPT_MAX) == dispatch_raw,
            )
            for ordinal, block_pin in enumerate(final["block_receipt_sha256s"]):
                with directory.child(f"block-{ordinal:04d}") as block:
                    _need(sha(block.read("complete.json", _RECEIPT_MAX)) == block_pin)
        for ordinal, (record, pairs, base, checkpoint, journal_pins) in enumerate(
            retention
        ):
            plan = _source_plan(campaign.plan, campaign.plan_sha256, ordinal)
            _need(plan.canonical_sha256() == record["source_plan_sha256"])
            _final_block_readback(
                root / record["block"],
                plan=plan,
                pairs=pairs,
                base=base,
                roster=record,
                checkpoint=checkpoint,
                journal_pins=journal_pins,
            )
        minutes = tuple(rows)
        inventory_sha256 = inventory.hexdigest()
        batch = (
            _build_batch(
                campaign,
                minutes,
                completion_sha256=expected_completion_sha256,
                root_binding_sha256=binding_sha,
                row_inventory_sha256=inventory_sha256,
                development_cutoff_utc_ns=latest["development"],
                validation_cutoff_utc_ns=latest["validation"],
            )
            if include_batch
            else None
        )
        return DevvalCampaignReplayV1(
            expected_campaign_sha256,
            expected_completion_sha256,
            binding_sha,
            minutes,
            totals["development"],
            totals["validation"],
            latest["development"],
            latest["validation"],
            inventory_sha256,
            batch,
        )
    except asyncio.CancelledError:
        raise
    except DevvalCampaignReplayError:
        raise
    except Exception:  # noqa: BLE001 - Preserve the original evidence and redact all private paths/data.
        raise DevvalCampaignReplayError("devval_campaign_readback_incomplete") from None
