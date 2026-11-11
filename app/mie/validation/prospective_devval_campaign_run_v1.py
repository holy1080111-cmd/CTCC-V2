"""Run the published D/V campaign through the original bounded minute owner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.demo_automation.native_private_root_v2 import (
    mkdir_private_evidence_child_v2,
    require_private_evidence_base_v2,
)
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import ClockStamp, canonical, sha, validate_stamps
from app.mie.validation import development_validation_source_collection_v6 as collection
from app.mie.validation import native_devval_dataset_v6 as dataset
from app.mie.validation.development_validation_source_collection_v1 import (
    _pin,
    _root_context,
    _root_identity,
)
from app.mie.validation.development_validation_source_plan_v1 import (
    DevelopmentValidationSourcePlanV1,
)
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_custody_v1 import (
    DevvalCampaignCustodyRepositoryV1,
    DevvalCampaignDispatchClaimV1,
    DevvalCampaignPublicationV1,
    _dispatch_document,
    _require_dispatch_claim,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    _source_plan,
    verify_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_slot_v6 import (
    _MAX_SLOT_FILE,
    _READBACK_WINDOW_NS,
    _slot,
    capture_prospective_devval_campaign_slot_v6,
)
from app.mie.validation.public_checkpoint_service import PublicCheckpointCaptureService

_ROOTS = ("journal", "native", "slots")
_ROSTER_MAX = 1024 * 1024
_RECEIPT_MAX = 128 * 1024
_WAIT_SECONDS = 15.0


class DevvalCampaignRunError(ValueError):
    """Safe refusal without database, provider, or local path text."""


@dataclass(frozen=True, slots=True)
class DevvalCampaignCaptureV1:
    campaign_sha256: str
    dispatch_receipt_sha256: str
    block_receipt_sha256s: tuple[str, ...]
    row_count: int
    receipt_sha256: str
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


@dataclass(frozen=True, slots=True)
class _Block:
    name: str
    plan: DevelopmentValidationSourcePlanV1
    owner: collection.DevelopmentValidationAvailabilityOwnerV6
    identities: tuple[tuple[int, int], ...]
    collection_sha256: str
    journal_id: str
    genesis_sha256: str


def _deny(reason):
    raise DevvalCampaignRunError(reason) from None


def _tick(previous: ClockStamp | None = None) -> ClockStamp:
    value = ClockStamp.model_validate(native_stamp())
    if previous is not None:
        validate_stamps((previous.model_dump(), value.model_dump()))
    return value


def _early(value: ClockStamp, first_open: int) -> None:
    if value.utc_ns >= first_open:
        _deny("devval_campaign_pre_window_deadline")


def _private(path: Path) -> tuple[int, int]:
    require_private_evidence_base_v2(str(path))
    with _root_context(path):
        return _root_identity(path)


def _retain(directory, name, raw: bytes, maximum: int) -> str:
    if not 0 < len(raw) <= maximum:
        _deny("devval_campaign_evidence_size")
    directory.publish(name, raw)
    if directory.read(name, maximum) != raw:
        _deny("devval_campaign_evidence_readback")
    return sha(raw)


def _observation(publication: DevvalCampaignPublicationV1) -> dict:
    return {
        "campaign_sha256": publication.campaign_sha256,
        "plan_sha256": publication.plan_sha256,
        "campaign_id": publication.campaign_id,
        "database_oid": publication.database_oid,
        "publisher_role_oid": publication.publisher_role_oid,
        "observer_role_oid": publication.observer_role_oid,
        "publication_xid": publication.publication_xid,
        "acknowledgement_xid": publication.acknowledgement_xid,
        "recorded_at": publication.recorded_at.isoformat(),
        "acknowledged_at": publication.acknowledged_at.isoformat(),
        "database_read_at": publication.database_read_at.isoformat(),
        "readback_xid": publication.readback_xid,
    }


async def _prepare_block(
    root, plan, ordinal, witness, observer, custody_factory
) -> _Block:
    name = f"block-{ordinal:04d}"
    block_path = root / name
    with _root_context(root) as directory:
        mkdir_private_evidence_child_v2(directory, name)
        with directory.child(name) as block:
            for child in _ROOTS:
                mkdir_private_evidence_child_v2(block, child)
    identities = tuple(_private(block_path / child) for child in _ROOTS)
    service = await PublicCheckpointCaptureService.initialize(
        root=block_path / "journal", repository=witness
    )
    owner = collection.DevelopmentValidationAvailabilityOwnerV6.initialize(
        root=block_path / "native",
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
        capture_service=service,
        observation_repository=observer,
        custody_session_factory=custody_factory,
    )
    state = collection._OWNERS[owner]
    identity = state.reader.identity
    if (
        identity.journal_id != service.journal_id
        or identity.journal_genesis_sha256 != service.genesis_sha256
        or (identity.root_device, identity.root_inode) != identities[1]
    ):
        _deny("devval_campaign_block_identity_changed")
    return _Block(
        name,
        plan,
        owner,
        identities,
        identity.canonical_sha256(),
        service.journal_id,
        service.genesis_sha256,
    )


def _block_pins(root, block: _Block) -> None:
    current = tuple(_private(root / block.name / child) for child in _ROOTS)
    state = collection._OWNERS.get(block.owner)
    if (
        current != block.identities
        or state is None
        or state.reader.identity.canonical_sha256() != block.collection_sha256
        or state.reader.identity.plan_sha256 != block.plan.canonical_sha256()
    ):
        _deny("devval_campaign_block_identity_changed")


async def _wait_due(close: int, previous: ClockStamp) -> ClockStamp:
    now = _tick(previous)
    while now.utc_ns < close:
        await asyncio.sleep(min(_WAIT_SECONDS, (close - now.utc_ns) / 1_000_000_000))
        now = _tick(now)
    if now.utc_ns >= close + _READBACK_WINDOW_NS:
        _deny("devval_campaign_slot_expired")
    return now


def _seal_block(root, block, prior, dispatch_sha, previous_sha) -> str:
    with _root_context(root) as directory, directory.child(block.name) as block_dir:
        if set(block_dir.names()) != set(_ROOTS):
            _deny("devval_campaign_block_inventory_changed")
        with block_dir.child("slots") as slots:
            if set(slots.names()) != {_slot(ordinal) for ordinal in range(len(prior))}:
                _deny("devval_campaign_slot_inventory_changed")
            for ordinal, (_, expected) in enumerate(prior):
                with slots.child(_slot(ordinal)) as child:
                    if sha(child.read("receipt.json", _MAX_SLOT_FILE)) != expected:
                        _deny("devval_campaign_slot_readback_changed")
        return _retain(
            block_dir,
            "complete.json",
            canonical(
                {
                    "schema": "ctcc.mie.devval_campaign_block.v1",
                    "dispatch_receipt_sha256": dispatch_sha,
                    "collection_identity_sha256": block.collection_sha256,
                    "previous_block_sha256": previous_sha,
                    "receipt_pairs": prior,
                    "predictive_oos_eligible": False,
                    "execution_authority": False,
                }
            ),
            _RECEIPT_MAX,
        )


async def run_prospective_devval_campaign_v1(
    *,
    root: Path,
    campaign_payload: bytes,
    expected_campaign_sha256: str,
    expected_source_commit: str,
    expected_source_tree: str,
    expected_source_manifest_sha256: str,
    publication_repository: DevvalCampaignCustodyRepositoryV1,
    witness_repository: PublicReceiptWitnessRepository,
    observation_repository: PublicReceiptPostReadRepository,
    custody_session_factory: async_sessionmaker[AsyncSession],
) -> DevvalCampaignCaptureV1:
    """Start one explicit run before its first window, with no writable resume."""
    try:
        if (
            type(root) is not type(Path())
            or not root.is_absolute()
            or type(publication_repository) is not DevvalCampaignCustodyRepositoryV1
            or publication_repository._role != "collector"
            or type(witness_repository) is not PublicReceiptWitnessRepository
            or type(observation_repository) is not PublicReceiptPostReadRepository
            or observation_repository.witness_repository is not witness_repository
        ):
            _deny("devval_campaign_runtime_invalid")
        source_checkpoint = {
            "expected_source_commit": expected_source_commit,
            "expected_source_tree": expected_source_tree,
            "expected_source_manifest_sha256": expected_source_manifest_sha256,
        }
        campaign = verify_prospective_devval_campaign_v1(
            campaign_payload,
            expected_campaign_sha256=expected_campaign_sha256,
            **source_checkpoint,
        )
        dataset._check_code()
        inventory = dataset._source_inventory()
        if inventory[0] != expected_source_manifest_sha256:
            _deny("devval_campaign_source_changed")
        first_open = _ns(campaign.plan.development.start_at)
        started = _tick()
        _early(started, first_open)
        root_id = _private(root)
        with _root_context(root) as directory:
            if directory.names():
                _deny("devval_campaign_already_claimed")
            _retain(directory, "campaign.json", campaign_payload, _RECEIPT_MAX)
        publication = await publication_repository.read(
            campaign_payload,
            expected_campaign_sha256=expected_campaign_sha256,
            **source_checkpoint,
        )
        now = _tick(started)
        _early(now, first_open)
        if (
            type(publication) is not DevvalCampaignPublicationV1
            or publication.acknowledged_at is None
            or publication.acknowledgement_xid is None
            or publication.database_read_at >= campaign.plan.development.start_at
            or publication.campaign_sha256 != expected_campaign_sha256
            or publication.plan_sha256 != campaign.plan_sha256
            or _private(root) != root_id
        ):
            _deny("devval_campaign_publication_not_admitted")
        blocks = []
        for ordinal in range(campaign.plan.block_count):
            plan = _source_plan(campaign.plan, campaign.plan_sha256, ordinal)
            if plan.canonical_sha256() != campaign.source_plan_sha256s[ordinal]:
                _deny("devval_campaign_source_changed")
            block = await _prepare_block(
                root,
                plan,
                ordinal,
                witness_repository,
                observation_repository,
                custody_session_factory,
            )
            blocks.append(block)
            now = _tick(now)
            _early(now, first_open)
        root_ids = {item for block in blocks for item in block.identities}
        if (
            len(root_ids) != len(blocks) * len(_ROOTS)
            or root_id in root_ids
            or len({block.journal_id for block in blocks}) != len(blocks)
            or len({block.genesis_sha256 for block in blocks}) != len(blocks)
            or _private(root) != root_id
            or dataset._source_inventory() != inventory
        ):
            _deny("devval_campaign_roster_conflict")
        for block in blocks:
            _block_pins(root, block)
        binding = canonical(
            {
                "schema": "ctcc.mie.devval_campaign_root_roster.v1",
                "publication": _observation(publication),
                "root_identity": root_id,
                "source_inventory_sha256": sha(canonical(inventory)),
                "blocks": [
                    {
                        "block": block.name,
                        "source_plan_sha256": campaign.source_plan_sha256s[ordinal],
                        "collection_identity_sha256": block.collection_sha256,
                        "journal_id": block.journal_id,
                        "journal_genesis_sha256": block.genesis_sha256,
                        "directory_identities": dict(
                            zip(_ROOTS, block.identities, strict=True)
                        ),
                    }
                    for ordinal, block in enumerate(blocks)
                ],
            }
        )
        with _root_context(root) as directory:
            if set(directory.names()) != {
                "campaign.json",
                *(block.name for block in blocks),
            }:
                _deny("devval_campaign_roster_inventory_changed")
            binding_sha = _retain(directory, "roots.json", binding, _ROSTER_MAX)
        now = _tick(now)
        _early(now, first_open)
        claim = await publication_repository.claim_dispatch(
            campaign_payload,
            expected_campaign_sha256=expected_campaign_sha256,
            root_binding_sha256=binding_sha,
            expected_publication=publication,
            **source_checkpoint,
        )
        now = _tick(now)
        _early(now, first_open)
        if (
            type(claim) is not DevvalCampaignDispatchClaimV1
            or claim.campaign_sha256 != expected_campaign_sha256
            or claim.root_binding_sha256 != binding_sha
            or claim.database_read_at >= campaign.plan.development.start_at
            or _private(root) != root_id
        ):
            _deny("devval_campaign_dispatch_unresolved")
        _require_dispatch_claim(claim, expected_campaign_sha256)
        dispatch_raw = _dispatch_document(claim)
        with _root_context(root) as directory:
            if (
                directory.read("campaign.json", _RECEIPT_MAX) != campaign_payload
                or directory.read("roots.json", _ROSTER_MAX) != binding
            ):
                _deny("devval_campaign_roster_readback_changed")
            dispatch_sha = _retain(
                directory, "dispatch.json", dispatch_raw, _RECEIPT_MAX
            )
        now = _tick(now)
        _early(now, first_open)
        block_pins = []
        rows = 0
        for ordinal, block in enumerate(blocks):
            prior = []
            for coordinate in block.plan.coordinates:
                now = await _wait_due(coordinate.capture_plan.end_ns, now)
                if _private(root) != root_id:
                    _deny("devval_campaign_root_changed")
                _block_pins(root, block)
                with _root_context(root) as directory:
                    if directory.read("dispatch.json", _RECEIPT_MAX) != dispatch_raw:
                        _deny("devval_campaign_dispatch_changed")
                (
                    native_pin,
                    slot_pin,
                ) = await capture_prospective_devval_campaign_slot_v6(
                    owner=block.owner,
                    campaign_payload=campaign_payload,
                    expected_campaign_sha256=expected_campaign_sha256,
                    block_ordinal=ordinal,
                    expected_collection_identity_sha256=block.collection_sha256,
                    result_root=root / block.name / "slots",
                    ordinal=coordinate.ordinal,
                    prior_receipts=tuple(prior),
                    dispatch_claim=claim,
                )
                now = _tick(now)
                prior.append((_pin(native_pin), _pin(slot_pin)))
            _block_pins(root, block)
            previous = block_pins[-1] if block_pins else None
            block_pins.append(_seal_block(root, block, prior, dispatch_sha, previous))
            rows += len(prior)
        if (
            rows != campaign.plan.expected_rows
            or dataset._source_inventory() != inventory
        ):
            _deny("devval_campaign_incomplete")
        with _root_context(root) as directory:
            if (
                _root_identity(root) != root_id
                or directory.read("dispatch.json", _RECEIPT_MAX) != dispatch_raw
                or directory.read("roots.json", _ROSTER_MAX) != binding
                or directory.read("campaign.json", _RECEIPT_MAX) != campaign_payload
                or set(directory.names())
                != {
                    "campaign.json",
                    "roots.json",
                    "dispatch.json",
                    *(block.name for block in blocks),
                }
            ):
                _deny("devval_campaign_final_readback_changed")
            for block, expected in zip(blocks, block_pins, strict=True):
                with directory.child(block.name) as block_dir:
                    if sha(block_dir.read("complete.json", _RECEIPT_MAX)) != expected:
                        _deny("devval_campaign_final_readback_changed")
            receipt = _retain(
                directory,
                "complete.json",
                canonical(
                    {
                        "schema": "ctcc.mie.devval_campaign_capture.v1",
                        "campaign_sha256": expected_campaign_sha256,
                        "dispatch_receipt_sha256": dispatch_sha,
                        "block_receipt_sha256s": block_pins,
                        "row_count": rows,
                        "predictive_oos_eligible": False,
                        "execution_authority": False,
                    }
                ),
                _RECEIPT_MAX,
            )
        return DevvalCampaignCaptureV1(
            expected_campaign_sha256, dispatch_sha, tuple(block_pins), rows, receipt
        )
    except asyncio.CancelledError:
        raise
    except DevvalCampaignRunError:
        raise
    except Exception:  # noqa: BLE001 - a partial run remains claimed and private.
        _deny("devval_campaign_unresolved")
