"""Original-source closed-run arithmetic for the V8 account assembler.

This reducer reopens B1/B3, source-bound position history and public settled
funding pages. A reconciled net amount is a bounded financial fact, never an
account-wide watermark, loss-streak seed, snapshot or execution capability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_positions_history_collector_v1 as positions
from app.trade_qualification import account_positions_history_v1 as position_pages
from app.trade_qualification import settled_funding_history_source as funding

POLICY_BYTES = canonical(
    {
        "version": "ctcc.account_history_risk_operands.v8",
        "lifecycle_policy_sha256": lifecycle.POLICY_SHA256,
        "typed_observation_policy_sha256": observed.TYPED_POLICY_SHA256,
        "position_history_contract": positions.VERSION,
        "funding_history_policy_sha256": funding.POLICY_SHA256,
        "financial_run": "original_FIFO_fill_gross_and_fees_plus_normal_full_close_position_totals",
        "position_join": "unique_original_nonzero_posId_witness_inside_same_fill_holding",
        "close_time": "last_zeroing_fillTime_never_position_uTime",
        "funding_total": "position_fundingFee_not_a_bill_accrual_timestamp",
        "public_funding_time": "settlement_event_candidate_not_account_payment_attribution",
        "account_wide_movements": "retain_every_original_unmapped_bill_and_run",
        "streak_and_HWM": "not_issued_without_separate_complete_persisted_source_proof",
        "maximum_position_sources": 1,
        "maximum_funding_sources": 128,
        "maximum_total_public_funding_bytes": observed.MAX_SOURCE_BYTES,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)


class AccountHistoryRiskError(ValueError):
    """Fixed codes only; private source rows never enter exception text."""


def _deny(code: str) -> None:
    raise AccountHistoryRiskError(code)


@dataclass(frozen=True, slots=True, repr=False)
class PositionHistorySourceV8:
    plan: position_pages.PositionHistoryPlanV1
    identity: positions.DemoPositionIdentityV1
    archive: bytes
    expected_archive_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class SettledFundingSourceV8:
    scope: funding.FundingHistoryScope
    pages: tuple[funding.RawFundingHistoryPage, ...]
    expected_source_pages_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class AccountHistoryRiskOperandsV8:
    """Replayable operands only. There is deliberately no owner/snapshot API."""

    receipt_json: bytes

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)


def _number(value):
    return lifecycle._number(value)


def _fraction(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _amount(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"numerator", "denominator"}:
        _deny("history_risk_replayed_amount_invalid")
    return Fraction(int(value["numerator"]), int(value["denominator"]))


def _time(value):
    at = capture._time_record("time", value, "source_update").value
    if at is None:
        _deny("history_risk_source_time_missing")
    return at


def _position_rows(source, packets, references, scope):
    if type(source) is not PositionHistorySourceV8:
        _deny("history_risk_original_position_source_required")
    replay = positions.verify_source_bound_archive(
        source.plan,
        source.identity,
        source.archive,
        expected_sha256=source.expected_archive_sha256,
    )
    identity, plan = source.identity, source.plan
    if identity.account_level != "2" or identity.position_mode != "net_mode":
        _deny("history_risk_account_mode_unsupported")
    if (
        identity.uid != scope.account_id
        or identity.settlement_currency != scope.settlement_currency
    ):
        _deny("history_risk_position_account_mismatch")
    for packet, reference in zip(packets, references, strict=True):
        config = json.loads(
            next(
                page
                for page in packet.observations
                if page.request.stream == "config_after"
            )
            .rows[0]
            .canonical_json
        )
        if (
            (
                config.get("uid"),
                config.get("mainUid"),
                config.get("acctLv"),
                config.get("posMode"),
            )
            != (
                identity.uid,
                identity.main_uid,
                identity.account_level,
                identity.position_mode,
            )
            or plan.session_binding_sha256 != reference.session_binding_sha256
            or plan.registration_region != packet.plan.registration_region
            or plan.origin != packet.plan.origin
        ):
            _deny("history_risk_position_session_or_mode_mismatch")
    archive = json.loads(source.archive)
    pages = positions._unfreeze_pages(plan, archive["history_archive"])
    rows = []
    for index, page in enumerate(pages):
        raw = json.loads(page.raw_body)["data"]
        for ordinal, row in enumerate(raw):
            rows.append(
                {
                    "raw": row,
                    "row_sha256": page.rows[ordinal].row_sha256,
                    "locator": {
                        "archive_sha256": replay.archive_sha256,
                        "page_index": index,
                        "row_ordinal": ordinal,
                        "page_sha256": page.page_sha256,
                    },
                }
            )
    return replay, rows


def _funding_sources(sources, region):
    if type(sources) is not tuple or len(sources) > 128:
        _deny("history_risk_funding_source_bound")
    result, source_bytes = {}, 0
    for source in sources:
        if (
            type(source) is not SettledFundingSourceV8
            or type(source.scope) is not funding.FundingHistoryScope
        ):
            _deny("history_risk_original_funding_source_required")
        if source.scope.registration_region != region:
            _deny("history_risk_funding_region_mismatch")
        if type(source.pages) is not tuple or any(
            type(page) is not funding.RawFundingHistoryPage
            or type(page.raw_body) is not bytes
            for page in source.pages
        ):
            _deny("history_risk_original_funding_source_required")
        source_bytes += sum(len(page.raw_body) for page in source.pages)
        if source_bytes > observed.MAX_SOURCE_BYTES:
            _deny("history_risk_funding_source_byte_bound")
        name = source.scope.instrument_id
        if name in result:
            _deny("history_risk_duplicate_funding_source")
        replay = funding.replay_settled_funding_history_pages(
            source.scope,
            source.pages,
            expected_source_pages_sha256=source.expected_source_pages_sha256,
        )
        result[name] = (source, replay, json.loads(replay.receipt_json))
    return result


def _witnesses(packets, name, side, opened, closed):
    witnesses = []
    for packet in packets:
        for page in packet.observations:
            if (
                page.request.stream != "positions"
                or not opened < page.body_completed_at < closed
            ):
                continue
            for row in page.rows:
                raw = json.loads(row.canonical_json)
                if raw.get("instId") != name or raw.get("posSide") != side:
                    continue
                if _number(raw.get("pos")) == 0:
                    continue
                witnesses.append(
                    {
                        "raw": raw,
                        "row_sha256": sha(row.canonical_json.encode()),
                        "page_receipt_sha256": page.receipt_sha256,
                        "body_completed_at": page.body_completed_at,
                    }
                )
    return witnesses


def _run(part, members, packets, position_rows, funding_sources, currency):
    # These remain global obligations. They do not prevent independently
    # corroborating aggregate arithmetic, and are never cleared on old B6.
    reasons = set(part["reasons"]) - {
        "funding_accrual_provenance_missing",
        "future_late_arrival_finality_unproven",
    }
    original_ids = {item["identity_sha256"] for item in part["original_source_rows"]}
    fills = [
        item
        for item in members
        if item["family"] == "fills" and item["identity_sha256"] in original_ids
    ]
    fills.sort(key=lambda item: _time(item["raw"]["fillTime"]))
    opened = _time(fills[0]["raw"]["fillTime"]) if fills else None
    closed = (
        datetime.fromisoformat(part["source_observed_zeroing_fill_at"])
        if part["source_observed_zeroing_fill_at"]
        else None
    )
    gross = _amount(part["source_observed_closed_gross"])
    fees = _amount(part["source_observed_signed_fill_fee_subtotal"])
    name, side = part["instrument_id"], part["position_side"]
    selected = None
    witness_pins = []
    if (
        opened is not None
        and closed is not None
        and gross is not None
        and fees is not None
        and not reasons
    ):
        witnesses = _witnesses(packets, name, side, opened, closed)
        identities = {
            (
                item["raw"].get("posId"),
                item["raw"].get("cTime"),
                item["raw"].get("mgnMode"),
                item["raw"].get("ccy"),
            )
            for item in witnesses
        }
        if len(identities) != 1:
            reasons.add("position_lifecycle_identity_witness_missing_or_ambiguous")
        else:
            pos_id, created, margin, ccy = next(iter(identities))
            if _time(created) != opened or ccy != currency:
                reasons.add("position_lifecycle_witness_scope_or_creation_mismatch")
            opening_side = fills[0]["raw"]["side"]
            sign = 1 if opening_side == "buy" else -1
            for witness in witnesses:
                raw = witness["raw"]
                update = _time(raw.get("uTime"))
                known_quantity = sum(
                    (
                        _number(item["raw"]["fillSz"])
                        * (1 if item["raw"]["side"] == opening_side else -1)
                        for item in fills
                        if _time(item["raw"]["fillTime"]) < update
                    ),
                    Fraction(0),
                )
                if (
                    raw.get("instType") != "SWAP"
                    or not opened < update <= witness["body_completed_at"]
                    or any(_time(item["raw"]["fillTime"]) == update for item in fills)
                    or _number(raw.get("pos")) != sign * known_quantity
                ):
                    reasons.add("position_lifecycle_witness_inventory_conflict")
            matches = [
                item
                for item in position_rows
                if (
                    item["raw"].get("posId"),
                    item["raw"].get("instId"),
                    item["raw"].get("posSide"),
                    item["raw"].get("mgnMode"),
                    item["raw"].get("ccy"),
                    item["raw"].get("cTime"),
                )
                == (pos_id, name, side, margin, ccy, created)
            ]
            if len(matches) != 1:
                reasons.add("position_full_close_source_missing_or_ambiguous")
            else:
                selected = matches[0]
                witness_pins = [
                    {
                        "row_sha256": item["row_sha256"],
                        "page_receipt_sha256": item["page_receipt_sha256"],
                    }
                    for item in witnesses
                ]
    else:
        reasons.add("original_closed_fill_run_incomplete")
    net = position_funding = None
    if selected is not None:
        raw = selected["raw"]
        if (
            raw.get("instType") != "SWAP"
            or raw.get("type") != "2"
            or _time(raw["uTime"]) < closed
        ):
            reasons.add("position_normal_full_close_required")
        opening_side = fills[0]["raw"]["side"]
        direction = "long" if opening_side == "buy" else "short"
        inventory = maximum = entries = exits = entry_value = exit_value = Fraction(0)
        for item in fills:
            fill = item["raw"]
            size, price = _number(fill["fillSz"]), _number(fill["fillPx"])
            if fill["side"] == opening_side:
                entries += size
                entry_value += size * price
                inventory += size
            else:
                exits += size
                exit_value += size * price
                inventory -= size
            maximum = max(maximum, inventory)
        if (
            raw.get("direction") != direction
            or any(
                raw.get(key) in (None, "")
                for key in ("closeTotalPos", "openMaxPos", "openAvgPx", "closeAvgPx")
            )
            or _number(raw["closeTotalPos"]) != exits
            or _number(raw["openMaxPos"]) != maximum
            or _number(raw["openAvgPx"]) != entry_value / entries
            or _number(raw["closeAvgPx"]) != exit_value / exits
        ):
            reasons.add("position_fill_quantity_price_or_direction_conflict")
        required = ("realizedPnl", "pnl", "fee", "fundingFee", "liqPenalty")
        if any(raw.get(key) in (None, "") for key in required):
            reasons.add("position_financial_component_missing")
        else:
            total, pnl, fee, paid, penalty = (_number(raw[key]) for key in required)
            # settledPnl applies only to cross FUTURES. An explicit empty SWAP
            # field is inapplicable; absent/null is still unknown, not zero.
            settled = raw.get("settledPnl")
            if settled is None:
                reasons.add("position_settled_pnl_applicability_unknown")
            elif settled != "" and _number(settled) != 0:
                reasons.add("position_swap_settled_pnl_conflict")
            if penalty != 0:
                reasons.add("position_normal_close_liquidation_penalty_conflict")
            if pnl != gross or fee != fees or total != pnl + fee + paid + penalty:
                reasons.add("position_fill_financial_totals_conflict")
            if not reasons:
                net, position_funding = total, paid
    event_candidates = None
    public = funding_sources.get(name)
    if public is not None and opened is not None and closed is not None:
        source, replay, data = public
        if source.scope.required_from_at > opened:
            reasons.add("funding_history_required_start_after_entry")
        event_candidates = []
        fill_times = {_time(item["raw"]["fillTime"]) for item in fills}
        for event in data["settled_public_events"]:
            at = datetime.fromisoformat(event["settlement_at"])
            if opened <= at <= closed:
                if at in fill_times:
                    reasons.add("funding_at_fill_time_ambiguous")
                event_candidates.append(
                    {**event, "source_receipt_sha256": replay.receipt_sha256}
                )
    # A funding candidate collision affects accrual matching, but does not erase
    # a separately source-reconciled aggregate closed-position net amount.
    return {
        "lifecycle_source_sha256": part["lifecycle_source_sha256"],
        "instrument_id": name,
        "opened_at_execution": None if opened is None else opened.isoformat(),
        "closed_at_execution": None if closed is None else closed.isoformat(),
        "fill_gross_pnl": None if gross is None else _fraction(gross),
        "signed_fill_fees": None if fees is None else _fraction(fees),
        "position_funding_total": None
        if position_funding is None
        else _fraction(position_funding),
        "reconciled_closed_run_net_pnl": None if net is None else _fraction(net),
        "net_outcome_arithmetic_reconciled": net is not None,
        "observed_positive_net": None if net is None else net > 0,
        "position_history_row": None
        if selected is None
        else {
            "row_sha256": selected["row_sha256"],
            "locator": selected["locator"],
            "position_updated_at": _time(selected["raw"]["uTime"]).isoformat(),
        },
        "position_identity_witnesses": witness_pins,
        "original_fill_rows": part["original_source_rows"],
        "public_settlement_event_candidates": event_candidates,
        "account_funding_accrual_events": None,
        "missing_obligations": sorted(
            reasons | {"account_funding_accrual_attribution_unproven"}
        ),
    }


def replay_account_history_risk_v8(
    sources,
    *,
    scope,
    window,
    index_batches,
    expected_index_head_sha256,
    expected_source_set_sha256,
    position_source,
    funding_sources=(),
    expected_policy_sha256=POLICY_SHA256,
):
    """Derive financial operands only from pinned original source populations.

    `observed_positive_net` records arithmetic, never a complete account-wide
    positive reset. No caller complete, seed, cashflow, outcome or peak is read.
    """
    try:
        if (
            type(expected_policy_sha256) is not str
            or expected_policy_sha256 != POLICY_SHA256
        ):
            _deny("history_risk_policy_mismatch")
        history = lifecycle.replay_account_lifecycle_history(
            sources,
            scope=scope,
            window=window,
            index_batches=index_batches,
            expected_index_head_sha256=expected_index_head_sha256,
            expected_source_set_sha256=expected_source_set_sha256,
        )
        old = json.loads(history.receipt_json)
        index = observed.replay_observation_index(sources, scope=scope, window=window)
        if index.receipt_sha256 != old["observation_receipt_sha256"]:
            _deny("history_risk_original_index_mismatch")
        typed_index = observed.replay_observation_index_typed_v1(
            sources, scope=scope, window=window
        )
        typed = json.loads(typed_index.receipt_json)
        packets = [lifecycle._packet(chain, reference) for chain, reference in sources]
        members = lifecycle._source_rows(typed["source_index"], packets, sources)
        position_replay, raw_positions = _position_rows(
            position_source, packets, [ref for _, ref in sources], scope
        )
        public = _funding_sources(funding_sources, packets[0].plan.registration_region)
        runs = [
            _run(
                part, members, packets, raw_positions, public, scope.settlement_currency
            )
            for part in old["observed_lifecycles"]
        ]
        used = [
            run["position_history_row"]["row_sha256"]
            for run in runs
            if run["net_outcome_arithmetic_reconciled"]
        ]
        if len(used) != len(set(used)):
            _deny("history_risk_position_total_reused_across_runs")
        unjoined = [
            {"row_sha256": item["row_sha256"], "locator": item["locator"]}
            for item in raw_positions
            if item["row_sha256"] not in used
        ]
        bills = []
        old_bills = {
            (item["identity_sha256"], item["row_sha256"]): item for item in old["bills"]
        }
        for member in members:
            if member["family"] != "bills":
                continue
            raw = member["raw"]
            prior = old_bills[(member["identity_sha256"], member["row_sha256"])]
            bills.append(
                {
                    "identity_sha256": member["identity_sha256"],
                    "row_sha256": member["row_sha256"],
                    "locators": member["locators"],
                    "record_posted_at": _time(raw["ts"]).isoformat(),
                    "funding_accrual_at": None,
                    "kind": prior["kind"],
                    "currency_matches": raw.get("ccy") == scope.settlement_currency,
                    "signed_posted_balance_change": _fraction(_number(raw["balChg"]))
                    if raw.get("ccy") == scope.settlement_currency
                    and raw.get("balChg") not in (None, "")
                    else None,
                }
            )
        obligations = (
            set(old["blocking_reasons"])
            | set(typed["blocking_reasons"])
            | {
                "original_native_source_ownership_required",
                "account_wide_history_tail_and_retention_unproven",
                "loss_streak_seed_or_complete_positive_reset_unproven",
                "preserved_risk_window_and_native_high_water_mark_required",
            }
        )
        if unjoined:
            obligations.add("account_wide_position_history_rows_unjoined")
        if any(
            item["kind"] != "reconciled_fee_pnl_mirror_not_summed" for item in bills
        ):
            obligations.add("account_wide_cashflow_accrual_or_classification_unjoined")
        if any(not item["currency_matches"] for item in bills):
            obligations.add("account_wide_bill_currency_unmapped")
        raw = canonical(
            {
                "schema_version": "ctcc.account_history_risk_operands.v8",
                "policy_sha256": POLICY_SHA256,
                "source_set_sha256": expected_source_set_sha256,
                "index_head_sha256": expected_index_head_sha256,
                "lifecycle_receipt_sha256": history.receipt_sha256,
                "typed_source_receipt_sha256": typed_index.receipt_sha256,
                "typed_source_time_coverage": typed["coverage"],
                "fill_execution_cashflows": typed["cashflows"],
                "position_archive_sha256": position_replay.archive_sha256,
                "position_chain_sha256": position_replay.chain.chain_sha256,
                "public_funding_receipt_sha256": [
                    value[1].receipt_sha256 for value in public.values()
                ],
                "window": observed.window_document(window),
                "closed_runs": runs,
                "reconciled_run_count": sum(
                    item["net_outcome_arithmetic_reconciled"] for item in runs
                ),
                "unjoined_position_history_rows": unjoined,
                "account_bills": bills,
                "loss_streak_at_history_start": None,
                "missing_obligations": sorted(obligations),
            }
        )
        if len(raw) > observed.MAX_RECEIPT_BYTES:
            _deny("history_risk_receipt_bound")
        return AccountHistoryRiskOperandsV8(raw)
    except AccountHistoryRiskError:
        raise
    except Exception:  # noqa: BLE001 -- original rows, private values and credentials stay private
        raise AccountHistoryRiskError(
            "history_risk_original_source_replay_invalid"
        ) from None
