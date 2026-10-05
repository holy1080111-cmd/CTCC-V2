"""B6-A original-source financial/inventory diagnostics; no risk authority.

DB0021 membership is replayed against B1 bytes. Its observed coverage is not
exchange finality. Funding, net loss, streak seed, HWM and current tail remain
unknown; no source DTO, computed total or diagnostic can issue a current owner.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise

from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
    _membership,
)
from app.trade_evidence.inventory_math import InventoryMathError, reduce_inventory
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.account_lifecycle_history_diagnostic.v1",
        "index_policy_sha256": observed.POLICY_SHA256,
        "index_prefix": "original_sequence_one_through_pinned_head_bounded_or_deny",
        "financial_time": "actual_fillTime_signed_fillPnl_and_fee_once",
        "inventory": "original_forensics_exact_FIFO_supported_linear_USDT_net_mode",
        "flat_anchor": "original_observed_empty_inventory_before_first_fill",
        "orders": "exact_original_row_role_join_not_order_retention_completeness",
        "funding": "unknown_no_original_settled_event_ingress",
        "net_loss": "unknown_not_observed_fill_subtotal",
        "streak_seed": "unknown_no_genesis_or_positive_complete_net_reset",
        "current_tail": "unknown_not_generation_maturity",
        "hwm": "unknown_no_historical_native_clock_upgrade",
        "owner": None,
        "snapshot": None,
        "account_revision_published": False,
        "execution_authority": False,
        "admission": "DENY",
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class AccountLifecycleHistoryError(ValueError):
    """Fixed private-source-safe codes."""


def deny(code):
    raise AccountLifecycleHistoryError(code)


@dataclass(frozen=True, slots=True, repr=False)
class AccountLifecycleHistoryReplay:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def execution_authority(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def admission(self):
        return "DENY"


def _json(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= observed.MAX_RECEIPT_BYTES:
        deny("history_original_document_required")
    value, encoded = capture._decode_json(
        raw, limit=observed.MAX_RECEIPT_BYTES, wire=False
    )
    if encoded.encode() != raw or type(value) is not dict:
        deny("history_original_document_required")
    return value


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        deny("history_exact_UTC_required")
    return value


def _number(value, *, positive=False):
    if type(value) is not str or len(value) > 128:
        deny("history_operand_unknown")
    decimal = Decimal(value)
    if not decimal.is_finite() or abs(decimal.as_tuple().exponent) > 100:
        deny("history_operand_unknown")
    result = Fraction(decimal)
    if positive and result <= 0:
        deny("history_operand_unknown")
    return result


def _fraction(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def source_set_sha256(sources):
    """Identity helper only; guarded full replay remains mandatory."""
    if type(sources) is not tuple or not 1 <= len(sources) <= observed.MAX_CAPTURES:
        deny("history_source_population_bound")
    refs, source_bytes = [], 0
    for pair in sources:
        if type(pair) is not tuple or len(pair) != 2:
            deny("history_original_source_pair_required")
        chain, reference = pair
        if type(chain) is not tuple or not 1 <= len(chain) <= journal.MAX_EVENTS:
            deny("history_original_chain_required")
        for item in chain:
            if type(item) is not journal.JournalReadback:
                deny("history_original_readback_required")
            if type(item.event) is not journal._JournalEvent:
                deny("history_original_readback_required")
            for raw in (
                item.event.event_json,
                item.event.raw_body,
                item.event.packet_payload,
            ):
                if raw is not None:
                    if type(raw) is not bytes:
                        deny("history_original_readback_required")
                    source_bytes += len(raw)
                    if source_bytes > observed.MAX_SOURCE_BYTES:
                        deny("history_original_source_byte_bound")
            _utc(item.db_recorded_at)
            _utc(item.readback_at)
            item.__post_init__()
        refs.append(observed.reference_document(reference))
    return journal.digest(journal.canonical(refs))


def _index(sources, batches, scope, expected_head):
    if type(batches) is not tuple or len(batches) != len(sources):
        deny("history_index_prefix_missing")
    journal._sha(expected_head)
    previous = previous_anchor = None
    facts_count = coverage_count = findings_count = 0
    documents, document_bytes = [], 0
    scope_sha = journal.digest(
        journal.canonical(
            [scope.environment, scope.account_id, scope.settlement_currency]
        )
    )
    for sequence, (batch, (chain, reference)) in enumerate(
        zip(batches, sources, strict=True), 1
    ):
        if (
            type(batch) is not ObservationBatchReadback
            or type(batch.sequence) is not int
            or batch.sequence != sequence
            or type(batch.event_sha256) is not str
            or type(batch.replay) is not observed.ObservationReplay
        ):
            deny("history_index_prefix_invalid")
        if (
            type(batch.document_json) is not bytes
            or type(batch.replay.receipt_json) is not bytes
        ):
            deny("history_original_document_required")
        document_bytes += len(batch.document_json) + len(batch.replay.receipt_json)
        if document_bytes > observed.MAX_SOURCE_BYTES:
            deny("history_original_index_byte_bound")
        data, receipt = _json(batch.document_json), _json(batch.replay.receipt_json)
        if journal.digest(batch.document_json) != batch.event_sha256:
            deny("history_index_document_pin_mismatch")
        if (
            data.get("schema_version") != "ctcc.account_observation_batch.v1"
            or data.get("policy_sha256") != observed.POLICY_SHA256
            or data.get("scope_sha256") != scope_sha
            or type(data.get("sequence")) is not int
            or data["sequence"] != sequence
            or data.get("previous_sha256") != previous
            or data.get("receipt_sha256") != batch.replay.receipt_sha256
            or data.get("account_complete") is not False
            or data.get("execution_authority") is not False
            or data.get("admission") != "DENY"
            or journal.canonical(data.get("reference"))
            != journal.canonical(observed.reference_document(reference))
        ):
            deny("history_index_document_binding_invalid")
        window_doc = data.get("window")
        if type(window_doc) is not dict or set(window_doc) != {
            "started_at",
            "ended_at",
        }:
            deny("history_index_window_invalid")
        original_window = observed.ExecutionWindow(
            *(
                _utc(datetime.fromisoformat(window_doc[name]))
                for name in ("started_at", "ended_at")
            )
        )
        single = observed.replay_observation_index(
            ((chain, reference),), scope=scope, window=original_window
        )
        facts, coverage = _membership(single)
        findings = receipt.get("findings")
        anchor = receipt.get("continuation_anchor")
        if type(findings) is not list or type(anchor) is not dict:
            deny("history_index_continuation_missing")
        if (
            receipt.get("scope_sha256") != scope_sha
            or receipt.get("policy_sha256") != observed.POLICY_SHA256
            or journal.canonical(receipt.get("window")) != journal.canonical(window_doc)
            or receipt.get("account_complete") is not False
            or receipt.get("execution_authority") is not False
            or receipt.get("admission") != "DENY"
            or anchor.get("schema_version")
            != "ctcc.account_observation_continuation.v1"
        ):
            deny("history_index_receipt_binding_invalid")
        witnesses = anchor.get("verified_witnesses")
        if type(witnesses) is not list or len(witnesses) >= sequence:
            deny("history_index_witness_invalid")
        prior_sources, seen, previous_witness = [], set(), 0
        for witness in witnesses:
            if (
                type(witness) is not dict
                or set(witness)
                != {
                    "sequence",
                    "event_sha256",
                    "source_membership_sha256",
                    "coverage_membership_sha256",
                }
                or type(witness["sequence"]) is not int
                or not 1 <= witness["sequence"] < sequence
                or witness["sequence"] in seen
                or witness["sequence"] <= previous_witness
            ):
                deny("history_index_witness_invalid")
            number = witness["sequence"]
            original_document = documents[number - 1][0]
            expected = {
                "sequence": number,
                "event_sha256": batches[number - 1].event_sha256,
                "source_membership_sha256": original_document[
                    "source_membership_sha256"
                ],
                "coverage_membership_sha256": original_document[
                    "coverage_membership_sha256"
                ],
            }
            if journal.canonical(witness) != journal.canonical(expected):
                deny("history_index_witness_pin_mismatch")
            seen.add(number)
            previous_witness = number
            prior_sources.append(sources[number - 1])
        original_replay = _json(
            observed._replay(
                (*prior_sources, (chain, reference)),
                scope,
                original_window,
                observed.POLICY_SHA256,
                adjacent=False,
            ).receipt_json
        )
        for name in (
            "captures",
            "source_index",
            "coverage",
            "cashflows",
            "generation_observation_cutoff",
            "observed_completed_at",
        ):
            if journal.canonical(receipt.get(name)) != journal.canonical(
                original_replay[name]
            ):
                deny("history_index_row_query_replay_mismatch")
        required_findings = original_replay["findings"]
        if journal.canonical(findings[: len(required_findings)]) != journal.canonical(
            required_findings
        ):
            deny("history_index_source_findings_removed")
        if type(receipt.get("blocking_reasons")) is not list or not set(
            original_replay["blocking_reasons"]
        ).issubset(receipt["blocking_reasons"]):
            deny("history_index_source_blocker_removed")
        for name, values in (
            ("source", facts),
            ("coverage", coverage),
            ("finding", findings),
        ):
            if (
                type(data.get(name + "_membership_count")) is not int
                or data[name + "_membership_count"] != len(values)
                or data.get(name + "_membership_sha256")
                != journal.digest(journal.canonical(values))
            ):
                deny("history_index_original_membership_mismatch")
        facts_count += len(facts)
        coverage_count += len(coverage)
        findings_count += len(findings)
        integers = {
            "through_sequence": sequence,
            "previous_sequence": sequence - 1,
            "source_facts_through_sequence": facts_count,
            "coverage_through_sequence": coverage_count,
            "unresolved_findings_through_sequence": findings_count,
        }
        if any(
            type(anchor.get(k)) is not int or anchor[k] != v
            for k, v in integers.items()
        ):
            deny("history_index_continuation_prefix_invalid")
        if (
            anchor.get("policy_sha256") != observed.POLICY_SHA256
            or anchor.get("scope_sha256") != scope_sha
            or anchor.get("previous_event_sha256") != previous
            or anchor.get("previous_anchor_sha256") != previous_anchor
            or anchor.get("current_source_membership_sha256")
            != data["source_membership_sha256"]
            or anchor.get("current_coverage_membership_sha256")
            != data["coverage_membership_sha256"]
            or type(anchor.get("required_proof_budget_exceeded")) is not bool
            or journal.canonical(anchor).decode() != data.get("anchor_json")
            or journal.digest(journal.canonical(anchor)) != data.get("anchor_sha256")
        ):
            deny("history_index_continuation_binding_invalid")
        previous, previous_anchor = batch.event_sha256, data["anchor_sha256"]
        documents.append((data, receipt))
    if previous != expected_head:
        deny("history_index_head_mismatch")
    return documents


def _packet(chain, reference):
    payloads = [
        item.event.packet_payload for item in chain if item.event.packet_payload
    ]
    if len(payloads) != 1:
        deny("history_original_packet_required")
    return capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )


def _source_rows(rows, packets, sources):
    """Recover original raw bytes through verified B3 locators, never a DTO raw."""
    original_packets = {
        reference.capture_id: packet
        for packet, (_, reference) in zip(packets, sources, strict=True)
    }
    results = []
    for member in rows:
        locators, selected = member["locators"], None
        if type(locators) is not list or not locators:
            deny("history_original_row_locator_required")
        for locator in locators:
            if type(locator) is not dict or set(locator) != {
                "capture_id",
                "request_index",
                "row_ordinal",
                "raw_event_sequence",
                "page_receipt_sha256",
                "raw_sha256",
                "stream",
            }:
                deny("history_original_row_locator_required")
            for name in ("request_index", "row_ordinal", "raw_event_sequence"):
                if type(locator[name]) is not int or locator[name] < (
                    1 if name == "raw_event_sequence" else 0
                ):
                    deny("history_original_row_locator_required")
            packet = original_packets.get(locator["capture_id"])
            if packet is None or locator["request_index"] >= len(packet.observations):
                deny("history_original_row_locator_required")
            page = packet.observations[locator["request_index"]]
            if (
                locator["row_ordinal"] >= len(page.rows)
                or locator["stream"] != page.request.stream
                or locator["page_receipt_sha256"] != page.receipt_sha256
                or locator["raw_sha256"] != journal.digest(page.response_body)
            ):
                deny("history_original_row_locator_mismatch")
            raw = page.rows[locator["row_ordinal"]].canonical_json.encode()
            if journal.digest(raw) != member["row_sha256"] or (
                selected is not None and selected != raw
            ):
                deny("history_original_row_hash_mismatch")
            selected = raw
        results.append({**member, "raw": json.loads(selected)})
    return results


def _lifecycle(rows, packets, sources, scope, blockers):
    orders, specs, anchors = {}, {}, []
    identity = None
    for packet, (_, reference) in zip(packets, sources, strict=True):
        pages = packet.observations
        config = json.loads(
            next(p for p in pages if p.request.stream == "config_after")
            .rows[0]
            .canonical_json
        )
        current_identity = (
            config["uid"],
            config["mainUid"],
            config["acctLv"],
            config["posMode"],
            packet.plan.session_binding_id,
        )
        if identity is not None and current_identity != identity:
            deny("history_account_session_or_mode_changed")
        identity = current_identity
        if config["acctLv"] != "2" or config["posMode"] != "net_mode":
            blockers.add("history_account_mode_unsupported")
        for page in pages:
            for row in page.rows:
                raw = json.loads(row.canonical_json)
                if page.request.stream == "account_instruments":
                    specs.setdefault(row.instrument_id, []).append(raw)
                elif capture.stream_family(page.request.stream).startswith(
                    "orders_history"
                ):
                    orders.setdefault(raw["ordId"], []).append(raw)
        inventory = [
            p
            for p in pages
            if p.request.stream == "positions"
            or p.request.stream == "orders_pending"
            or p.request.stream.startswith("algo_")
        ]
        anchor_page = next(
            p for p in pages if p.request.stream == "account_position_risk"
        )
        anchor = json.loads(anchor_page.rows[0].canonical_json)
        if not anchor["posData"] and not any(p.rows for p in inventory):
            anchors.append(
                {
                    "observed_at": max(
                        p.body_completed_at for p in (anchor_page, *inventory)
                    ),
                    "capture_id": reference.capture_id,
                    "packet_sha256": reference.packet_sha256,
                    "anchor_page_receipt_sha256": anchor_page.receipt_sha256,
                    "inventory_page_receipts": [p.receipt_sha256 for p in inventory],
                }
            )
    groups = {}
    for row in rows:
        if row["family"] == "fills":
            raw = row["raw"]
            groups.setdefault((raw.get("instId"), raw.get("posSide")), []).append(row)
    results = []
    for key, members in groups.items():
        reasons = set(blockers)
        name, pos_side = key
        actual = []
        metadata = specs.get(name, [])
        unit = None
        if not metadata or any(
            journal.canonical(m) != journal.canonical(metadata[0]) for m in metadata
        ):
            reasons.add("history_contract_metadata_unknown_or_changed")
        else:
            spec = metadata[0]
            if (
                spec.get("ctType") != "linear"
                or spec.get("settleCcy") != scope.settlement_currency
                or spec.get("ctValCcy") != name.split("-")[0]
                or spec.get("ctMult") != "1"
            ):
                reasons.add("history_contract_unit_unsupported")
            else:
                unit = _number(spec.get("ctVal"), positive=True)
        if pos_side != "net" or "history_account_mode_unsupported" in blockers:
            reasons.add("history_inventory_mode_unsupported")
        for member in members:
            raw = member["raw"]
            if raw.get("instType") != "SWAP":
                reasons.add("history_fill_product_unsupported")
            choices = orders.get(raw.get("ordId"), [])
            role = None
            if choices:
                signatures = [
                    (
                        c.get("instId"),
                        c.get("side"),
                        c.get("posSide"),
                        c.get("clOrdId"),
                        c.get("reduceOnly"),
                        c.get("tdMode"),
                    )
                    for c in choices
                ]
                if (
                    all(type(c.get("reduceOnly")) is bool for c in choices)
                    and all(
                        journal.canonical(s) == journal.canonical(signatures[0])
                        for s in signatures
                    )
                    and signatures[0][:4]
                    == (name, raw.get("side"), pos_side, raw.get("clOrdId"))
                ):
                    role = "exit" if choices[0]["reduceOnly"] else "entry"
            if role is None or member["fill_at"] is None:
                reasons.add("history_fill_role_or_time_unknown")
                continue
            at = _utc(datetime.fromisoformat(member["fill_at"]))
            actual.append(
                (
                    role,
                    raw.get("side"),
                    _number(raw.get("fillSz"), positive=True),
                    _number(raw.get("fillPx"), positive=True),
                    at,
                )
            )
        actual.sort(key=lambda fill: fill[4])  # Derived view; original pages unchanged.
        if any(a[4] == b[4] for a, b in pairwise(actual)):
            reasons.add("history_equal_time_inventory_ambiguous")
        earlier = [a for a in anchors if actual and a["observed_at"] < actual[0][4]]
        if not earlier:
            reasons.add("history_starting_flat_anchor_missing")
        if len(actual) != len(members):
            reasons.add("history_fill_inventory_unmapped")
        values = None
        if not reasons:
            direction = (
                "long"
                if (actual[0][0] == "entry") == (actual[0][1] == "buy")
                else "short"
            )
            try:
                values = reduce_inventory(
                    direction=direction,
                    unit=unit,
                    fills=tuple(actual),
                    funding_times=(),
                )
            except InventoryMathError as exc:
                reasons.add(exc.args[0])
        closed_at = None
        gross = None
        if values is not None:
            entries, exits, _, _, calculated, _ = values
            if entries > 0 and entries == exits:
                exchange_gross = sum(
                    (_number(m["raw"].get("fillPnl")) for m in members), Fraction(0)
                )
                if calculated == exchange_gross:
                    closed_at, gross = actual[-1][4].isoformat(), _fraction(calculated)
                else:
                    reasons.add("history_closed_gross_reconciliation_mismatch")
            else:
                reasons.add("history_inventory_remains_open")
        results.append(
            {
                "lifecycle_source_sha256": journal.digest(
                    journal.canonical(
                        [
                            scope.environment,
                            scope.account_id,
                            key,
                            [m["identity_sha256"] for m in members],
                        ]
                    )
                ),
                "instrument_id": name,
                "position_side": pos_side,
                "source_observed_zeroing_fill_at": closed_at,
                "source_observed_closed_gross": gross,
                "source_observed_starting_inventory": {
                    **earlier[-1],
                    "observed_at": earlier[-1]["observed_at"].isoformat(),
                    "meaning": "bounded_observed_flat_not_exchange_atomic_or_account_genesis",
                }
                if earlier
                else None,
                "original_source_rows": [
                    {
                        "identity_sha256": m["identity_sha256"],
                        "row_sha256": m["row_sha256"],
                        "locators": m["locators"],
                    }
                    for m in members
                ],
                "net_pnl": None,
                "funding_amount": None,
                "positive_net_reset_proven": False,
                "reasons": sorted(
                    reasons
                    | {
                        "funding_accrual_provenance_missing",
                        "future_late_arrival_finality_unproven",
                    }
                ),
            }
        )
    return results


def replay_account_lifecycle_history(
    sources,
    *,
    scope,
    window,
    index_batches,
    expected_index_head_sha256,
    expected_source_set_sha256,
    expected_policy_sha256=POLICY_SHA256,
):
    """Source-derived diagnostic, not a PortfolioRiskSnapshot or current owner."""
    try:
        checked_bootstrap(scope, LedgerScope)
        _utc(window.started_at) if type(window) is observed.ExecutionWindow else deny(
            "history_exact_window_required"
        )
        _utc(window.ended_at)
        if (
            type(expected_policy_sha256) is not str
            or expected_policy_sha256 != POLICY_SHA256
        ):
            deny("history_policy_mismatch")
        source_pin = source_set_sha256(sources)
        if (
            type(expected_source_set_sha256) is not str
            or source_pin != expected_source_set_sha256
        ):
            deny("history_source_set_pin_mismatch")
        documents = _index(sources, index_batches, scope, expected_index_head_sha256)
        replay = observed.replay_observation_index(sources, scope=scope, window=window)
        data = _json(replay.receipt_json)
        packets = [_packet(chain, reference) for chain, reference in sources]
        original_rows = _source_rows(data["source_index"], packets, sources)
        blockers = set(data["blocking_reasons"])
        if any(
            r["findings"] or r["continuation_anchor"]["required_proof_budget_exceeded"]
            for _, r in documents
        ):
            blockers.add("history_persistent_index_invalidation")
        lifecycle = _lifecycle(original_rows, packets, sources, scope, blockers)
        bills = []
        for member in original_rows:
            if member["family"] != "bills":
                continue
            raw = member["raw"]
            kind = (
                "funding_unknown" if raw.get("type") == "8" else "unclassified_unknown"
            )
            if raw.get("type") == "2":
                matched = [
                    m
                    for m in original_rows
                    if m["family"] == "fills"
                    and m["raw"].get("billId") == raw.get("billId")
                ]
                if (
                    len(matched) == 1
                    and raw.get("ccy") == scope.settlement_currency
                    and raw.get("fee") == matched[0]["raw"].get("fee")
                    and raw.get("pnl") == matched[0]["raw"].get("fillPnl")
                    and all(
                        raw.get(field) in (None, "")
                        or raw.get(field) == matched[0]["raw"].get(field)
                        for field in ("instId", "ordId", "tradeId")
                    )
                ):
                    kind = "reconciled_fee_pnl_mirror_not_summed"
                else:
                    blockers.add("history_fee_mirror_unreconciled")
            elif raw.get("type") != "8":
                blockers.add("history_account_movement_unclassified")
            bills.append(
                {
                    "identity_sha256": member["identity_sha256"],
                    "row_sha256": member["row_sha256"],
                    "locators": member["locators"],
                    "generation_at": member["generation_at"],
                    "effective_accrual_at": None,
                    "kind": kind,
                }
            )
        output = {
            "schema_version": "ctcc.account_lifecycle_history_diagnostic.v1",
            "policy_sha256": POLICY_SHA256,
            "source_set_sha256": source_pin,
            "index_head_sha256": expected_index_head_sha256,
            "index_through_sequence": len(index_batches),
            "observation_receipt_sha256": replay.receipt_sha256,
            "observed_window": data["window"],
            "generation_observation_cutoff": data["generation_observation_cutoff"],
            "observed_fill_movements": data["cashflows"],
            "observed_fill_cashflow_total": data["observed_fill_cashflow_total"]
            if not blockers
            else None,
            "observed_lifecycles": lifecycle,
            "bills": bills,
            "findings": data["findings"],
            "blocking_reasons": sorted(
                blockers
                | {
                    "funding_accrual_provenance_missing",
                    "complete_net_loss_window_unproven",
                    "loss_streak_seed_unknown",
                    "current_history_tail_unproven",
                    "historical_native_hwm_unknown",
                }
            ),
            "net_loss_window": None,
            "loss_streak_at_history_start": None,
            "historical_native_hwm_verified": False,
            "current_tail_complete": False,
            "source_authenticity_verified": False,
            "owner": None,
            "snapshot": None,
            "account_revision_published": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        raw = journal.canonical(output)
        if len(raw) > observed.MAX_RECEIPT_BYTES:
            deny("history_receipt_bound")
        return AccountLifecycleHistoryReplay(raw)
    except AccountLifecycleHistoryError:
        raise
    except Exception:  # noqa: BLE001 -- no private source/SQL details escape
        raise AccountLifecycleHistoryError("history_source_replay_invalid") from None
