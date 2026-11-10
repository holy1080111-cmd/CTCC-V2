"""One due D/V minute through the original V6 owner and one-use consumer."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from app.domain import native_clock
from app.domain.source_primitives import canonical, decode, sha, validate_stamps
from app.mie.validation import development_validation_source_collection_v6 as collection
from app.mie.validation import native_availability_v6 as native
from app.mie.validation import native_devval_dataset_v6 as dataset
from app.mie.validation.development_validation_source_collection_v1 import (
    _context,
    _pin,
    _root_context,
)
from app.mie.validation.post_publication_availability_v4 import utc_nanoseconds
from app.mie.validation.post_publication_availability_v6 import (
    FutureAvailabilityDecisionV6,
    replay_native_availability_v6,
)
from app.mie.validation.prospective_development_validation_plan import (
    build_prospective_devval_source_plan,
)

_SLOT_FILES = {"claim.json", "decision.json", "receipt.json"}
_MAX_SLOT_FILE = 16 * 1024
_MAX_EVIDENCE_BASE64 = 4 * 1024 * 1024
_READBACK_WINDOW_NS = 60_000_000_000
_RECEIPT_SCHEMA = "ctcc.mie.devval_slot_readback.v6"
_RECEIPT_FIELDS = {
    "schema",
    "ordinal",
    "collection_identity_sha256",
    "claim_sha256",
    "native_receipt_sha256",
    "decision_sha256",
    "evidence_sha256",
    "bundle_sha256",
    "journal_replay_sha256",
    "public_readback_microsecond_utc_ns",
    "decision_cutoff_utc_ns",
    "predictive_oos_eligible",
    "execution_authority",
}


class ProspectiveDevvalSlotError(ValueError):
    """Safe outcome codes without local paths or provider payloads."""


def _slot(ordinal: int) -> str:
    return f"slot-{ordinal:08d}"


def _publish(directory, name: str, payload: bytes) -> None:
    if not 0 < len(payload) <= _MAX_SLOT_FILE:
        raise ProspectiveDevvalSlotError("devval_slot_payload_budget")
    directory.publish(name, payload)
    if directory.read(name, _MAX_SLOT_FILE) != payload:
        raise ProspectiveDevvalSlotError("devval_slot_readback_failed")


def _claim(base, coordinate, previous_slot_pin):
    return canonical(
        {
            **base,
            "schema": "ctcc.mie.devval_slot_claim.v6",
            "capture_plan_sha256": coordinate.capture_plan_sha256,
            "previous_slot_sha256": previous_slot_pin,
            "ordinal": coordinate.ordinal,
            "deadline_utc_ns": coordinate.capture_plan.end_ns + _READBACK_WINDOW_NS,
        }
    )


def _verify_slot(child, claim, coordinate, collection_pin, native_pin, slot_pin):
    if set(child.names()) != _SLOT_FILES:
        raise ProspectiveDevvalSlotError("devval_slot_readback_inventory")
    raw = child.read("receipt.json", _MAX_SLOT_FILE)
    decision_raw = child.read("decision.json", _MAX_SLOT_FILE)
    receipt = decode(raw, _MAX_SLOT_FILE)
    decision = FutureAvailabilityDecisionV6.model_validate_json(decision_raw)
    public_readback = receipt["public_readback_microsecond_utc_ns"]
    close = coordinate.capture_plan.end_ns
    deadline = close + _READBACK_WINDOW_NS
    if (
        sha(raw) != slot_pin
        or set(receipt) != _RECEIPT_FIELDS
        or canonical(receipt) != raw
        or receipt["schema"] != _RECEIPT_SCHEMA
        or type(receipt["ordinal"]) is not int
        or receipt["ordinal"] != coordinate.ordinal
        or receipt["collection_identity_sha256"] != collection_pin
        or receipt["native_receipt_sha256"] != native_pin
        or receipt["predictive_oos_eligible"] is not False
        or receipt["execution_authority"] is not False
        or child.read("claim.json", _MAX_SLOT_FILE) != claim
        or receipt["claim_sha256"] != sha(claim)
        or decision.canonical_json_bytes() != decision_raw
        or receipt["decision_sha256"] != sha(decision_raw)
        or receipt["evidence_sha256"] != decision.evidence_sha256
        or receipt["bundle_sha256"] != decision.bundle_sha256
        or receipt["decision_cutoff_utc_ns"] != decision.cutoff.utc_ns
        or decision.row_count != 1
        or type(public_readback) is not int
        or not close <= public_readback < deadline
        or not public_readback <= decision.cutoff.utc_ns <= deadline
    ):
        raise ProspectiveDevvalSlotError("devval_slot_readback_changed")
    _pin(receipt["journal_replay_sha256"])


def _verify_prefix(directory, prior, plan, base):
    if set(directory.names()) != {_slot(i) for i in range(len(prior))}:
        raise ProspectiveDevvalSlotError("devval_slot_prefix_inventory")
    previous_slot_pin = None
    for ordinal, (native_pin, slot_pin) in enumerate(prior):
        coordinate = plan.coordinates[ordinal]
        with directory.child(_slot(ordinal)) as child:
            _verify_slot(
                child,
                _claim(base, coordinate, previous_slot_pin),
                coordinate,
                base["collection_identity_sha256"],
                native_pin,
                slot_pin,
            )
        previous_slot_pin = slot_pin


def _check_original(data, coordinate, capture_started, deadline):
    decision = data["decision"]
    encoded = data["evidence_base64"]
    if type(encoded) is not str or not 0 < len(encoded) <= _MAX_EVIDENCE_BASE64:
        raise ProspectiveDevvalSlotError("devval_slot_evidence_budget")
    evidence = replay_native_availability_v6(
        base64.b64decode(encoded, validate=True),
        expected_sha256=data["evidence_sha256"],
    )
    source = evidence.source_clock_v4.source_v3.v2_capture
    if (
        type(decision) is not FutureAvailabilityDecisionV6
        or decision.canonical_sha256() != data["decision_sha256"]
        or decision.evidence_sha256 != data["evidence_sha256"]
        or decision.bundle_sha256 != data["bundle_sha256"]
        or decision.bundle_sha256 != evidence.custody.bundle_sha256
        or decision.ordinal_claim_sha256 != evidence.ordinal_claim_sha256
        or decision.row_count != 1
        or data["ordinal"] != coordinate.ordinal
        or data["capture_plan"].canonical_sha256() != coordinate.capture_plan_sha256
        or source.plan_sha256 != coordinate.capture_plan_sha256
    ):
        raise ProspectiveDevvalSlotError("devval_slot_original_changed")
    validate_stamps(
        [
            capture_started,
            evidence.final_ready.model_dump(),
            decision.cutoff.model_dump(),
        ]
    )
    # V2 records this source time at microsecond resolution. Require it to
    # precede the exact deadline to avoid rounding a late readback into bounds.
    public_readback_ns = utc_nanoseconds(source.payload_readback_at)
    if not (
        capture_started["utc_ns"] <= public_readback_ns < deadline
        and decision.cutoff.utc_ns <= deadline
    ):
        raise ProspectiveDevvalSlotError("devval_slot_original_late")
    return decision, public_readback_ns


async def capture_prospective_devval_slot_v6(
    *,
    owner: collection.DevelopmentValidationAvailabilityOwnerV6,
    pilot_payload: bytes,
    expected_pilot_sha256: str,
    expected_collection_identity_sha256: str,
    result_root: Path,
    ordinal: int,
    prior_receipts: tuple[tuple[str, str], ...],
) -> tuple[str, str]:
    """Return the original and slot receipt digests after one native replay.

    Each prior pair contains the separately retained native and slot digest in
    ordinal order. A partial slot is a durable refusal of further advancement.
    A saved decision is only a computational record of its original cutoff.
    """
    state = None
    stop_on_error = False
    eligibility = binding = None
    try:
        task = asyncio.current_task()
        if (
            type(owner) is not collection.DevelopmentValidationAvailabilityOwnerV6
            or task is None
            or task.cancelling()
            or type(result_root) is not type(Path())
            or not result_root.is_absolute()
        ):
            raise ProspectiveDevvalSlotError("devval_slot_owner_invalid")
        state = collection._OWNERS.get(owner)
        if state is None or state.context != _context() or state.busy or state.stopped:
            raise ProspectiveDevvalSlotError("devval_slot_owner_unavailable")
        dataset._check_code()
        plan = build_prospective_devval_source_plan(
            pilot_payload, expected_plan_sha256=expected_pilot_sha256
        )
        plan_pin = plan.canonical_sha256()
        collection_pin = _pin(expected_collection_identity_sha256)
        reader = state.reader
        if (
            reader.identity.canonical_sha256() != collection_pin
            or reader.identity.plan_sha256 != plan_pin
            or reader.plan.canonical_sha256() != plan_pin
            or type(ordinal) is not int
            or not 0 <= ordinal < len(plan.coordinates)
            or type(prior_receipts) is not tuple
            or len(prior_receipts) != ordinal
            or any(type(pair) is not tuple or len(pair) != 2 for pair in prior_receipts)
        ):
            raise ProspectiveDevvalSlotError("devval_slot_plan_or_prefix_changed")
        prior = tuple((_pin(first), _pin(second)) for first, second in prior_receipts)
        resolved_result = result_root.resolve(strict=True)
        for source_root in (reader.root, state.journal._root):
            resolved_source = source_root.resolve(strict=True)
            if (
                resolved_result == resolved_source
                or resolved_result in resolved_source.parents
                or resolved_source in resolved_result.parents
            ):
                raise ProspectiveDevvalSlotError("devval_slot_root_overlaps_source")
        coordinate = plan.coordinates[ordinal]
        close = coordinate.capture_plan.end_ns
        deadline = close + _READBACK_WINDOW_NS
        inventory = dataset._source_inventory()
        base = {
            "collection_identity_sha256": collection_pin,
            "pilot_sha256": expected_pilot_sha256,
            "source_plan_sha256": plan_pin,
            "source_inventory_sha256": sha(canonical(inventory)),
        }
        claim = _claim(base, coordinate, prior[-1][1] if prior else None)
        with _root_context(result_root) as directory:
            stop_on_error = True
            _verify_prefix(directory, prior, plan, base)
            due = native_clock.native_stamp()
            if due["utc_ns"] < close:
                stop_on_error = False
                raise ProspectiveDevvalSlotError("devval_slot_not_due")
            if due["utc_ns"] >= deadline:
                raise ProspectiveDevvalSlotError("devval_slot_expired")
            name = _slot(ordinal)
            directory.mkdir(name)
            with directory.child(name) as child:
                _publish(child, "claim.json", claim)
                capture_started = native_clock.native_stamp()
                validate_stamps([due, capture_started])
                if capture_started["utc_ns"] >= deadline:
                    raise ProspectiveDevvalSlotError("devval_slot_expired")
                eligibility = await owner.capture_once(
                    ordinal=ordinal,
                    expected_previous_receipt_sha256s=tuple(
                        first for first, _ in prior
                    ),
                )
                dataset._check_code()
                binding, data = native.consume_native_devval_group_v6(
                    eligibility, expected_source_plan_sha256=plan_pin
                )
                decision, public_readback_ns = _check_original(
                    data, coordinate, capture_started, deadline
                )
                journal_pin = _pin(await native.replay_native_devval_group_v6(binding))
                dataset._check_code()
                if (
                    asyncio.current_task() is not task
                    or task.cancelling()
                    or dataset._source_inventory() != inventory
                ):
                    raise ProspectiveDevvalSlotError("devval_slot_runtime_changed")
                native.finish_native_devval_groups_v6((binding,))
                _publish(child, "decision.json", decision.canonical_json_bytes())
                original_pin = _pin(data["receipt_sha256"])
                native_receipt = reader.read(
                    ordinal, expected_receipt_sha256=original_pin
                )
                if (
                    native_receipt.status != "NATIVE_DURABLE_AVAILABLE"
                    or native_receipt.canonical_sha256() != original_pin
                ):
                    raise ProspectiveDevvalSlotError(
                        "devval_slot_native_readback_changed"
                    )
                receipt = canonical(
                    {
                        "schema": _RECEIPT_SCHEMA,
                        "ordinal": ordinal,
                        "collection_identity_sha256": collection_pin,
                        "claim_sha256": sha(claim),
                        "native_receipt_sha256": original_pin,
                        "decision_sha256": data["decision_sha256"],
                        "evidence_sha256": data["evidence_sha256"],
                        "bundle_sha256": data["bundle_sha256"],
                        "journal_replay_sha256": journal_pin,
                        "public_readback_microsecond_utc_ns": public_readback_ns,
                        "decision_cutoff_utc_ns": decision.cutoff.utc_ns,
                        "predictive_oos_eligible": False,
                        "execution_authority": False,
                    }
                )
                _publish(child, "receipt.json", receipt)
                slot_pin = sha(receipt)
                _verify_slot(
                    child, claim, coordinate, collection_pin, original_pin, slot_pin
                )
                return original_pin, slot_pin
    except (ProspectiveDevvalSlotError, asyncio.CancelledError):
        if stop_on_error:
            state.stopped = True
        raise
    except Exception:  # noqa: BLE001 - No native paths or provider payloads escape.
        if stop_on_error:
            state.stopped = True
        raise ProspectiveDevvalSlotError("devval_slot_unresolved") from None
    finally:
        for token in (eligibility, binding):
            if token is not None:
                native.discard_native_devval_group_v6(token)
