"""Exact prospective Live epoch arithmetic; DTOs and saved JSON grant nothing."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

from app.domain import native_live_financial_epoch_schema_v1 as spec
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.okx_live import financial_rows_v1 as rows_source
from app.okx_live import trusted_source_bridge_v1 as bridge


def _need(condition, code="live_financial_epoch_data_refused"):
    if condition is not True:
        raise ValueError(code) from None


def ratio(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def read_ratio(value):
    _need(type(value) is dict and set(value) == {"numerator", "denominator"})
    _need(all(type(item) is str and 1 <= len(item) <= 256 for item in value.values()))
    result = Fraction(int(value["numerator"]), int(value["denominator"]))
    _need(ratio(result) == value)
    return result


def epoch_document(rows):
    """Only the repository's stopped/empty DB transaction may adopt this DATA."""
    _need(type(rows) is rows_source.LiveFinancialRowsDataV1)
    document = {
        "schema": spec.SCHEMA,
        "kind": "prospective_controlled_genesis",
        "environment": "live",
        "epoch_id": sha(canonical([spec.SCHEMA, "live", rows.account_uid])),
        "account_uid": rows.account_uid,
        "main_uid": rows.main_uid,
        "policy_sha256": spec.POLICY_SHA256,
        "started_at": rows.observed_at.isoformat(),
        "genesis_source_revision_sha256": rows.source_revision_sha256,
        "genesis_financial_rows_sha256": sha(rows.receipt_json),
        "prospective_seed": 0,
        "prior_7d_cash_losses_reset": False,
        "peak_scope": "all_durable_original_samples_in_this_prospective_epoch_not_all_time",
        "account_complete": False,
        "qualification_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return canonical(document)


def _outcome_v1(
    position, members, *, observed_at, cutoff, source_revision, financial_receipt
):
    _need(
        all(
            item.instrument_id == position.instrument_id
            and item.position_side == position.position_side
            and position.created_ms <= item.execution_ms <= position.updated_ms
            for item in members
        )
    )
    _need(
        members and len({item.execution_ms for item in members}) == len(members),
        "live_financial_intramillisecond_order_unknown",
    )
    opening, closing = ("3", "5") if position.direction == "long" else ("4", "6")
    quantity = Fraction(0)
    for item in members:
        _need(item.subtype in (opening, closing))
        quantity += item.contracts if item.subtype == opening else -item.contracts
        _need(quantity >= 0)
    _need(
        quantity == 0
        and members[0].subtype == opening
        and members[-1].subtype == closing
    )
    _need(
        sum((item.gross_pnl for item in members), Fraction(0)) == position.gross_pnl
        and sum((item.signed_fee for item in members), Fraction(0))
        == position.signed_fee,
        "live_financial_closed_fill_aggregate_conflict",
    )
    receipt = decode(financial_receipt, maximum=2097152)
    _need(
        canonical(receipt) == financial_receipt
        and receipt["source_revision_sha256"] == source_revision
        and sha(position.source_row_json) in receipt["closed_positions"]
    )
    _need(
        all(
            sha(item.source_row_json) in receipt["fills"]
            and sha(item.bill_row_json) in receipt["trade_bill_mirrors"]
            for item in members
        )
    )
    identifiers = tuple(item.bill_id for item in members)
    return {
        "identity_sha256": sha(canonical([position.identity_sha256, identifiers])),
        "instrument_id": position.instrument_id,
        "closed_ms": members[-1].execution_ms,
        "position_row_sha256": sha(position.source_row_json),
        "position_source_json": position.source_row_json.decode("ascii"),
        "position_source_locators": position.source_locators,
        "fill_bill_ids": identifiers,
        "fill_row_sha256": [sha(item.source_row_json) for item in members],
        "original_fill_bill_pairs": [
            {
                "fill_json": item.source_row_json.decode("ascii"),
                "fill_locators": item.source_locators,
                "bill_json": item.bill_row_json.decode("ascii"),
                "bill_locators": item.bill_locators,
            }
            for item in members
        ],
        "source_observed_at": observed_at.isoformat(),
        "source_cutoff": cutoff.isoformat(),
        "source_revision_sha256": source_revision,
        "financial_rows_receipt_sha256": sha(financial_receipt),
        "net_pnl": ratio(position.net_pnl),
        "settled_funding": ratio(position.settled_funding),
    }


def _replay_prior_outcome_v1(item, receipts):
    observed = datetime.fromisoformat(item["source_observed_at"])
    cutoff = datetime.fromisoformat(item["source_cutoff"])
    _need(observed.tzinfo is UTC and cutoff.tzinfo is UTC and cutoff <= observed)
    end_ms = bridge._utc_ns(cutoff) // 1_000_000
    position = rows_source._replay_closed_position_v1(
        item["position_source_json"].encode("ascii"),
        item["position_source_locators"],
        observed_at=observed,
        cutoff_ms=end_ms,
    )
    members = tuple(
        rows_source._replay_fill_pair_v1(
            pair["fill_json"].encode("ascii"),
            pair["fill_locators"],
            pair["bill_json"].encode("ascii"),
            pair["bill_locators"],
            cutoff_ms=end_ms,
        )
        for pair in item["original_fill_bill_pairs"]
    )
    _need(members == tuple(sorted(members, key=lambda fill: fill.execution_ms)))
    actual = _outcome_v1(
        position,
        members,
        observed_at=observed,
        cutoff=cutoff,
        source_revision=item["source_revision_sha256"],
        financial_receipt=receipts[item["financial_rows_receipt_sha256"]],
    )
    _need(
        canonical(actual) == canonical(item),
        "live_financial_prior_raw_outcome_conflict",
    )
    return actual


def _replay_prior_payment_v1(item, receipts):
    cutoff = datetime.fromisoformat(item["source_cutoff"])
    _need(cutoff.tzinfo is UTC)
    payment = rows_source._replay_funding_payment_v1(
        item["source_row_json"].encode("ascii"),
        item["source_locators"],
        cutoff_ms=bridge._utc_ns(cutoff) // 1_000_000,
    )
    receipt_raw = receipts[item["financial_rows_receipt_sha256"]]
    receipt = decode(receipt_raw, maximum=2097152)
    _need(
        canonical(receipt) == receipt_raw
        and receipt["source_revision_sha256"] == item["source_revision_sha256"]
        and sha(payment.source_row_json) in receipt["funding_payments"]
    )
    _need(
        (
            payment.bill_id,
            payment.instrument_id,
            payment.balance_updated_ms,
            payment.signed_payment,
            sha(payment.source_row_json),
        )
        == (
            item["bill_id"],
            item["instrument_id"],
            item["balance_updated_ms"],
            read_ratio(item["signed_payment"]),
            item["source_row_sha256"],
        ),
        "live_financial_prior_raw_payment_conflict",
    )
    return item


def _new_outcomes(rows, epoch_ms, previous, settled_payments, receipts):
    previous = tuple(_replay_prior_outcome_v1(item, receipts) for item in previous)
    outcomes = list(previous)
    _need(type(previous) is tuple and len(previous) <= 2048)
    _need(all(item["closed_ms"] >= epoch_ms for item in previous))
    used = {identity for outcome in previous for identity in outcome["fill_bill_ids"]}
    _need(len(used) == sum(len(outcome["fill_bill_ids"]) for outcome in previous))
    remaining = {
        item.bill_id: item
        for item in rows.fills
        if item.execution_ms >= epoch_ms and item.bill_id not in used
    }
    # Raw pages remain in original order. This separate event chronology uses
    # the documented execution time, never ordId/cTime or bill generation time.
    for position in sorted(rows.closed_positions, key=lambda item: item.updated_ms):
        members = tuple(
            sorted(
                (
                    item
                    for item in remaining.values()
                    if item.instrument_id == position.instrument_id
                    and item.position_side == position.position_side
                    and position.created_ms <= item.execution_ms <= position.updated_ms
                ),
                key=lambda item: item.execution_ms,
            )
        )
        if not members:
            overlapping = [
                item
                for item in previous
                if item["instrument_id"] == position.instrument_id
                and position.created_ms <= item["closed_ms"] <= position.updated_ms
            ]
            _need(
                not overlapping
                or any(
                    item["position_row_sha256"] == sha(position.source_row_json)
                    for item in overlapping
                ),
                "live_financial_closed_outcome_revision_unresolved",
            )
            continue
        outcome = _outcome_v1(
            position,
            members,
            observed_at=rows.observed_at,
            cutoff=rows.cutoff,
            source_revision=rows.source_revision_sha256,
            financial_receipt=rows.receipt_json,
        )
        identifiers = tuple(item.bill_id for item in members)
        outcomes.append(outcome)
        for identity in identifiers:
            remaining.pop(identity)
    _need(not remaining, "live_financial_completed_lifecycle_source_missing")
    _need(len(outcomes) <= 2048)
    ordered = tuple(sorted(outcomes, key=lambda item: item["closed_ms"]))
    _need(
        len({item["closed_ms"] for item in ordered}) == len(ordered),
        "live_financial_closed_outcome_order_unknown",
    )
    for instrument in {item["instrument_id"] for item in ordered} | {
        item["instrument_id"] for item in settled_payments
    }:
        expected = sum(
            (
                read_ratio(item["settled_funding"])
                for item in ordered
                if item["instrument_id"] == instrument
            ),
            Fraction(0),
        )
        actual = sum(
            (
                read_ratio(item["signed_payment"])
                for item in settled_payments
                if item["instrument_id"] == instrument
            ),
            Fraction(0),
        )
        _need(expected == actual, "live_financial_epoch_funding_unresolved")
    return ordered


def sample_document(rows, *, original_epoch_json, original_prefix):
    """Replay the entire durable prefix. No caller peak/seed/complete is input."""
    _need(
        type(rows) is rows_source.LiveFinancialRowsDataV1
        and type(original_prefix) is tuple
    )
    epoch = decode(original_epoch_json, maximum=65536)
    _need(
        canonical(epoch) == original_epoch_json
        and epoch["schema"] == spec.SCHEMA
        and epoch["policy_sha256"] == spec.POLICY_SHA256
        and epoch["account_uid"] == rows.account_uid
        and epoch["main_uid"] == rows.main_uid
        and epoch["environment"] == "live"
        and type(epoch["prospective_seed"]) is int
        and epoch["prospective_seed"] == 0
    )
    started = datetime.fromisoformat(epoch["started_at"])
    _need(started.tzinfo is UTC and started <= rows.observed_at)
    peak, peak_at, previous_sha, previous_outcomes, last_at = (
        Fraction(rows.equity),
        rows.observed_at,
        None,
        (),
        None,
    )
    previous_payments = ()
    receipts = {sha(rows.receipt_json): rows.receipt_json}
    for sequence, raw in enumerate(original_prefix, 1):
        data = decode(raw, maximum=2097152)
        financial_receipt = data["financial_rows_receipt_json"].encode("ascii")
        _need(sha(financial_receipt) == data["financial_rows_sha256"])
        receipts[data["financial_rows_sha256"]] = financial_receipt
        at = datetime.fromisoformat(data["observed_at"])
        _need(
            canonical(data) == raw
            and data["schema"] == spec.SCHEMA
            and data["epoch_sha256"] == sha(original_epoch_json)
            and data["epoch_id"] == epoch["epoch_id"]
            and type(data["sequence"]) is int
            and data["sequence"] == sequence
            and data["previous_sha256"] == previous_sha
            and at.tzinfo is UTC
            and (at == started if sequence == 1 else at > last_at)
            and at < rows.observed_at
        )
        equity = rows_source._number(data["equity"])
        _need(equity > 0)
        if equity > peak or (equity == peak and at < peak_at):
            peak, peak_at = equity, at
        incoming_outcomes = tuple(data["controlled_outcomes"])
        incoming_payments = tuple(data["controlled_funding_payments"])
        _need(
            all(
                any(canonical(old) == canonical(new) for new in incoming_outcomes)
                for old in previous_outcomes
            ),
            "live_financial_outcome_prefix_omitted",
        )
        _need(
            all(
                any(canonical(old) == canonical(new) for new in incoming_payments)
                for old in previous_payments
            ),
            "live_financial_funding_prefix_omitted",
        )
        previous_sha, previous_outcomes, last_at = (
            sha(raw),
            incoming_outcomes,
            at,
        )
        previous_payments = tuple(
            _replay_prior_payment_v1(item, receipts) for item in incoming_payments
        )
    if not original_prefix:
        _need(rows.observed_at == started)
    epoch_ms = bridge._utc_ns(started) // 1_000_000
    payments = {item["bill_id"]: item for item in previous_payments}
    for item in rows.funding_payments:
        if item.balance_updated_ms < epoch_ms:
            continue
        payment = {
            "bill_id": item.bill_id,
            "instrument_id": item.instrument_id,
            "balance_updated_ms": item.balance_updated_ms,
            "signed_payment": ratio(item.signed_payment),
            "source_row_sha256": sha(item.source_row_json),
            "source_row_json": item.source_row_json.decode("ascii"),
            "source_locators": item.source_locators,
            "source_cutoff": rows.cutoff.isoformat(),
            "source_revision_sha256": rows.source_revision_sha256,
            "financial_rows_receipt_sha256": sha(rows.receipt_json),
        }
        old = payments.get(item.bill_id)
        _need(
            old is None
            or (
                old["source_row_json"] == payment["source_row_json"]
                and old["signed_payment"] == payment["signed_payment"]
            ),
            "live_financial_funding_revision_unresolved",
        )
        if old is None:
            payments[item.bill_id] = payment
    _need(len(payments) <= 4096)
    payments = tuple(payments.values())
    outcomes = _new_outcomes(rows, epoch_ms, previous_outcomes, payments, receipts)
    day_start = rows.observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = rows.observed_at - timedelta(days=7)

    def total(start, *, inclusive):
        begin = bridge._utc_ns(start) // 1_000_000
        return sum(
            (
                item.gross_pnl + item.signed_fee
                for item in rows.fills
                if item.execution_ms >= begin
                if inclusive or item.execution_ms > begin
            ),
            Fraction(0),
        ) + sum(
            (
                item.signed_payment
                for item in rows.funding_payments
                if item.balance_updated_ms >= begin
                if inclusive or item.balance_updated_ms > begin
            ),
            Fraction(0),
        )

    streak = 0  # Known empty prospective controlled population at actual genesis.
    for outcome in outcomes:
        net = read_ratio(outcome["net_pnl"])
        streak = streak + 1 if net < 0 else 0 if net > 0 else streak
    _need(streak <= 2048)
    document = {
        "schema": spec.SCHEMA,
        "epoch_id": epoch["epoch_id"],
        "epoch_sha256": sha(original_epoch_json),
        "sequence": len(original_prefix) + 1,
        "previous_sha256": previous_sha,
        "observed_at": rows.observed_at.isoformat(),
        "source_revision_sha256": rows.source_revision_sha256,
        "financial_rows_sha256": sha(rows.receipt_json),
        "financial_rows_receipt_json": rows.receipt_json.decode("ascii"),
        "account_cashflows": [
            {
                "at": utc_from_ns(item.execution_ms * 1_000_000).isoformat(),
                "net": ratio(item.gross_pnl + item.signed_fee),
                "kind": "fill",
                "source_row_sha256": sha(item.source_row_json),
            }
            for item in rows.fills
        ]
        + [
            {
                "at": utc_from_ns(item.balance_updated_ms * 1_000_000).isoformat(),
                "net": ratio(item.signed_payment),
                "kind": "funding_booked_payment",
                "source_row_sha256": sha(item.source_row_json),
            }
            for item in rows.funding_payments
        ],
        "equity": format(rows.equity, "f"),
        "available_margin": format(rows.available_margin, "f"),
        "history_start": rows.history_start.isoformat(),
        "history_end": rows.observed_at.isoformat(),
        "record_generation_cutoff": rows.cutoff.isoformat(),
        "daily_net_pnl": ratio(total(day_start, inclusive=True)),
        "weekly_net_pnl": ratio(total(week_start, inclusive=False)),
        "loss_streak": streak,
        "controlled_outcomes": outcomes,
        "controlled_funding_payments": payments,
        "measured_peak": ratio(peak),
        "peak_observed_at": peak_at.isoformat(),
        "peak_window_started_at": started.isoformat(),
        "funding_time_basis": "booked_payment_not_accrual",
        "missing_obligations": [],
        "account_complete": False,
        "qualification_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    raw = canonical(document)
    _need(len(raw) <= 2097152)
    return raw
