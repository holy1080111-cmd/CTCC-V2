"""Source-located public minutes from a controlled, checkpointed local journal.

Availability is the first complete capture validation, never candle close or
HTTP Date. The producer leaves dataset partition/candidate selection untouched.
"""

from __future__ import annotations

from decimal import Decimal

from app.mie.features import FeatureBar
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.batch_replay import BoundMinute
from app.mie.validation.replay import PointInTimeBar
from app.research.public_market_receipts import (
    MINUTE_NS,
    PublicReceiptError,
    parsed_rows,
    utc_from_ns,
)
from app.research.public_receipt_storage import ControlledPublicReceiptJournal


def measured_public_minutes(
    *, journal, capture_id, expected_receipt_sha256, expected_plan_sha256
):
    if type(journal) is not ControlledPublicReceiptJournal or any(
        type(value) is not str
        for value in (capture_id, expected_receipt_sha256, expected_plan_sha256)
    ):
        raise PublicReceiptError("controlled_journal_required")
    entries, first_rows = journal.read_all()
    selected = [entry for entry in entries if entry[1].capture_id == capture_id]
    if len(selected) != 1:
        raise PublicReceiptError("capture_identity_missing_or_duplicate")
    plan, receipt, _, selected_entry = selected[0]
    if selected_entry["disposition"] != "accepted":
        raise PublicReceiptError("source_revision_conflict")
    if (
        receipt.canonical_sha256() != expected_receipt_sha256
        or plan.canonical_sha256() != expected_plan_sha256
    ):
        raise PublicReceiptError("capture_pin_mismatch")
    minutes = []
    for locator in receipt.rows:
        _, entry_index, original_locator = first_rows[locator["identity"]]
        original_plan, original, files, journal_entry = entries[entry_index]
        if (
            original_plan.instrument_id != plan.instrument_id
            or original_plan.origin != plan.origin
            or original_plan.volume_unit != "contracts"
        ):
            raise PublicReceiptError("original_observation_source_mismatch")
        raw = files[f"page-{original_locator['page_index']:03d}.raw"]
        rows, _ = parsed_rows(raw)
        row = rows[original_locator["row_ordinal"]]
        close_at = utc_from_ns(int(row[0]) * 1_000_000 + MINUTE_NS)
        observed_at = utc_from_ns(original.validation_complete["utc_ns"])
        retrieved_at = utc_from_ns(journal_entry["payload_readback_complete"]["utc_ns"])
        source_sha = original_locator["content_sha256"]
        minutes.append(
            BoundMinute(
                row=PointInTimeBar(
                    source_row_id="okx-public-minute:" + locator["identity"],
                    source_row_sha256=source_sha,
                    instrument_id=plan.instrument_id,
                    available_at=observed_at,
                    bar=FeatureBar(
                        closed_at=close_at,
                        open=Decimal(row[1]),
                        high=Decimal(row[2]),
                        low=Decimal(row[3]),
                        close=Decimal(row[4]),
                        volume=Decimal(row[5]),
                    ),
                ),
                availability=AvailabilityProvenance(
                    basis=AvailabilityBasis.MEASURED_ROW_RECEIPT,
                    source_row_sha256=source_sha,
                    receipt_sha256=original.canonical_sha256(),
                    bar_closed_at=close_at,
                    observed_at=observed_at,
                    retrieved_at=retrieved_at,
                    available_at=observed_at,
                ),
            )
        )
    # The raw order was verified strictly descending; this is specified order
    # conversion, never sorting, dropping duplicates or repairing malformed data.
    return tuple(reversed(minutes))
