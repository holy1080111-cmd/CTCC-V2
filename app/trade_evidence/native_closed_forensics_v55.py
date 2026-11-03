"""Actual full owned close -> replayed forensics; missing facts remain unknown.

This pure mapper grants no source ownership, release or report permission. The
native repository must bind all three independently retained raw read groups,
the unique close intent, terminal closure and final resource cleanup readback.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence import execution_forensics as execution
from app.trade_evidence import forensics as core
from app.trade_evidence import native_forensics_v2 as entry
from app.trade_qualification import native_close_final_facts_v1 as closing
from app.trade_qualification import native_reconciliation_facts_v1 as facts


@dataclass(frozen=True, slots=True, repr=False)
class NativeClosedForensicsDataV55:
    receipt_json: bytes = field(repr=False)
    analysis: core.TradeForensicsResult = field(repr=False)
    execution_costs: execution.ExecutionForensicsReplay = field(repr=False)
    report_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def reconstruct_native_closed_forensics_v55(
    target, original, preflight, close_intent_json, final
):
    """Recompute terminal facts and existing FIFO/cost engines from actual rows.

    Fill prices already contain spread/slippage. Execution diagnostics are
    retained separately and are never deducted a second time from realized PnL.
    Bill generation time never supplies a funding effective-accrual timestamp.
    """
    try:
        actual = closing.assess_native_closed_trade_v1(
            target, original, preflight, close_intent_json, final
        )
        initial_analysis, _, initial_receipt = entry._entry_mapping(target, original)
        candidate = initial_analysis.inputs.candidate
        candidate_pin = core.candidate_sha256(candidate)
        terminal = decode(actual.receipt_json)
        observed = max(page.eof_at for _, page in final)
        fills, cashflows, links = [], [], []
        for prefix, order_id, role in (
            ("entry", terminal["entry_order_id"], "entry"),
            ("close", terminal["close_order_id"], "exit"),
        ):
            row_pages = {}
            query = {
                "instType": "SWAP",
                "instId": candidate.instrument_id,
                "ordId": order_id,
            }
            for suffix, path in (
                ("recent", "/api/v5/trade/fills"),
                ("history", "/api/v5/trade/fills-history"),
            ):
                name = prefix + "_fills_" + suffix
                facts.chain(final, name, path, query, "billId")
                for current, page in final:
                    if current != name:
                        continue
                    for row in facts.rows(page):
                        previous = row_pages.get(row["billId"])
                        facts.need(
                            previous is None or previous[0] == row,
                            "native_closed_forensics_conflicting_fill_rows",
                        )
                        if previous is None:
                            row_pages[row["billId"]] = (row, page, [])
                        row_pages[row["billId"]][2].append(
                            {"role": name, "raw_body_sha256": sha(page.raw_body)}
                        )
            for bill_id, (row, page, sources) in row_pages.items():
                at = facts.source_time(row["fillTime"], page)
                base = {
                    "evidence_id": "fill_" + bill_id,
                    "report_id": candidate.report_id,
                    "candidate_sha256": candidate_pin,
                    "instrument_id": candidate.instrument_id,
                    "account_id": candidate.account_id,
                    "environment": "demo",
                    "source_sha256": sha(page.raw_body),
                    "recorded_at": page.eof_at,
                }
                fill = core.FillEvent(
                    **base,
                    order_id=order_id,
                    occurred_at=at,
                    role=role,
                    side=row["side"],
                    contracts=Decimal(row["fillSz"]),
                    price=Decimal(row["fillPx"]),
                    exit_reason="other" if role == "exit" else None,
                )
                fills.append(fill)
                cashflows.append(
                    core.CashFlowEvent(
                        **(base | {"evidence_id": "fee_" + bill_id}),
                        occurred_at=at,
                        kind="fee",
                        amount=Decimal(row["fee"]),
                        currency=row["feeCcy"],
                        fill_id=fill.evidence_id,
                    )
                )
                links.append(
                    {
                        "fill_evidence_id": fill.evidence_id,
                        "fee_evidence_id": "fee_" + bill_id,
                        "bill_id": bill_id,
                        "trade_id": row["tradeId"],
                        "order_id": order_id,
                        "source_pages": sources,
                    }
                )
        facts.need(
            1 <= len(fills) <= 512
            and len({item.evidence_id for item in fills}) == len(fills),
            "native_closed_forensics_fill_inventory_invalid",
        )
        fills.sort(key=lambda item: (item.occurred_at, item.evidence_id))
        cashflows.sort(key=lambda item: (item.occurred_at, item.evidence_id))
        start, end = candidate.recorded_at, observed
        reasons = {
            "funding": "posted_bills_do_not_prove_effective_accrual_or_payment_finality",
            "path": "holding_price_path_not_observed",
        }
        coverage = tuple(
            core.StreamCoverage(
                evidence_id="coverage_" + stream,
                report_id=candidate.report_id,
                candidate_sha256=candidate_pin,
                instrument_id=candidate.instrument_id,
                account_id=candidate.account_id,
                environment="demo",
                source_sha256=sha(actual.receipt_json),
                recorded_at=observed,
                stream=stream,
                status="complete" if stream in {"fills", "fees"} else "unknown",
                started_at=start,
                ended_at=end,
                reason=reasons.get(stream),
            )
            for stream in ("fills", "fees", "funding", "path")
        )
        packet = core.ForensicsInput(
            candidate=candidate,
            fills=tuple(fills),
            cashflows=tuple(cashflows),
            path=(),
            coverage=coverage,
            observed_at=observed,
            purpose="observed",
        )
        analysis = core.analyze_trade(packet, expected_candidate_sha256=candidate_pin)
        core.validate_forensics(analysis, expected_candidate_sha256=candidate_pin)
        costs = execution.analyze_execution_forensics(
            analysis,
            expected_candidate_sha256=candidate_pin,
            policy=execution.ExecutionForensicsPolicy(
                policy_id="native_owned_closed_references_unknown_v55",
                maximum_reference_age_ms=5000,
            ),
            references=(),
        )
        receipt = {
            "version": "ctcc.native_closed_trade_forensics.v1",
            "reservation_id": target.reservation_id,
            "request_sha256": sha(target.request_json.encode()),
            "intent_sha256": target.intent_sha256,
            "original_event_key": target.request.origin.original_event_key,
            "native_original_candidate_sha256": target.request.origin.original_candidate_sha256,
            "forensic_projection_candidate_sha256": candidate_pin,
            "original_policy_sha256": target.request.origin.original_policy_sha256,
            "g12_evidence_sha256": target.request.origin.evidence_sha256,
            "publication_sha256": target.request.origin.publication_sha256,
            "terminal_assessment_json": actual.receipt_json.decode(),
            "terminal_assessment_sha256": sha(actual.receipt_json),
            "original_entry_forensics_sha256": sha(canonical(initial_receipt)),
            "source_links": sorted(links, key=lambda item: item["fill_evidence_id"]),
            "analysis": analysis.model_dump(mode="json", round_trip=True),
            "execution_costs": decode(costs.canonical_payload, execution.MAX_BYTES),
            "actual_fill_pnl_already_contains_price_friction": True,
            "unknown_funding_effective_accrual": None,
            "unknown_holding_price_path": None,
            "unknown_prefill_and_preclose_quote_references": None,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
        raw = canonical(receipt)
        facts.need(0 < len(raw) <= 140000, "native_closed_forensics_receipt_capacity")
        return NativeClosedForensicsDataV55(raw, analysis, costs)
    except Exception:  # noqa: BLE001 -- keep all private rows and identifiers redacted.
        raise entry.NativeForensicsError(
            "native_closed_forensics_reconstruction_invalid"
        ) from None
