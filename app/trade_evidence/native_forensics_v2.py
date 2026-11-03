"""Native retained entry rows -> existing forensic DATA, never closed-trade truth.

The pure mapper replays actual native request/intent/GET semantics, then calls
existing FIFO and execution-cost engines. The owned entry uses the fixed native
read-only producer; it never consumes a close MOVE or releases any hold.
Receipts contain private account/order evidence and require private custody.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_evidence import execution_forensics as execution
from app.trade_evidence import forensics as core
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_reconciliation_facts_v1 as facts

_SOURCE_KEY = object()
_MAX_RECEIPT = 4 * 1024 * 1024


class NativeForensicsError(ValueError):
    """Static errors only; private raw values never enter exception text."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeEntryForensicsSourceV2:
    """Inert view; only the genuine native producer can authenticate it."""

    target: object = field(repr=False)
    readback: tuple = field(repr=False)
    response_source: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeEntryForensicsReplayV2:
    analysis: core.TradeForensicsResult = field(repr=False)
    execution: execution.ExecutionForensicsReplay = field(repr=False)
    receipt_json: bytes = field(repr=False)
    receipt_sha256: str
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    closed_trade_verified: Literal[False] = field(default=False, init=False)
    report_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _need(condition):
    if condition is not True:
        raise NativeForensicsError("native_entry_forensics_source_invalid") from None


def _entry_mapping(target, observations):
    _need(type(target) is facts.NativeReconciliationTargetV1)
    _need(type(observations) is tuple and 1 <= len(observations) <= read.MAX_REQUESTS)
    _need(
        all(
            type(item) is tuple
            and len(item) == 2
            and type(item[0]) is str
            and type(item[1]) is read.DemoReconciliationObservationV1
            and type(item[1].raw_body) is bytes
            for item in observations
        )
    )
    _need(sum(len(page.raw_body) for _, page in observations) <= read.MAX_BATCH_BYTES)
    assessment = facts.assess_native_reconciliation_v1(target, observations)
    _need(
        assessment.classification == "filled_and_protected_hold_retained"
        and assessment.filled_and_protected is True
        and assessment.terminal_zero_fill_flat is False
    )
    request = wire.checked_request(target.request)
    qualified = request.origin.candidate
    specification = request.risk_inputs.instrument
    _need(
        specification.contract_kind == "linear_base"
        and specification.contract_value_currency == specification.base_currency
        and specification.settlement_currency == request.scope.settlement_currency
    )
    # NativeOriginalCandidateV2 retains the exact qualified result, not a
    # separate created_at field. Reuse its validated original observation.
    created = qualified.evaluated_at
    observed = max(page.eof_at for _, page in observations)
    candidate = core.TradeCandidate(
        report_id=qualified.report_id,
        instrument_id=specification.instrument_id,
        account_id=request.scope.account_id,
        environment=request.scope.environment,
        direction=qualified.direction,
        settlement_currency=request.scope.settlement_currency,
        contract_value_base=specification.contract_value,
        planned_contracts=request.risk_inputs.requested_contracts,
        entry=qualified.candidate_entry,
        stop_loss=qualified.stop_loss,
        take_profit=qualified.take_profit,
        recorded_at=created,
        original_evidence_sha256=request.origin.evidence_sha256,
        original_source_sha256=qualified.entry_zone.source_sha256,
        entry_window_start=max(created, qualified.trigger.trigger_time),
        entry_deadline=request.origin.deadline,
        entry_zone_low=qualified.entry_zone.zone_low,
        entry_zone_high=qualified.entry_zone.zone_high,
    )
    candidate_pin = core.candidate_sha256(candidate)
    assessed = decode(assessment.receipt_json, _MAX_RECEIPT)
    order_id = assessed["order_id"]
    query = {
        "instType": "SWAP",
        "instId": specification.instrument_id,
        "ordId": order_id,
    }
    row_pages = {}
    for role, endpoint in (
        ("fills_recent", "/api/v5/trade/fills"),
        ("fills_history", "/api/v5/trade/fills-history"),
    ):
        # Real terminal cursor chains are replayed; short or empty nonterminal
        # pages never become an unverified empty fill set.
        facts.chain(observations, role, endpoint, query, "billId")
        for name, page in observations:
            if name == role:
                for row in facts.rows(page):
                    identity = row["billId"]
                    prior = row_pages.get(identity)
                    _need(prior is None or prior[0] == row)
                    if prior is None:
                        row_pages[identity] = (row, page, [])
                    row_pages[identity][2].append(
                        {"role": role, "raw_body_sha256": sha(page.raw_body)}
                    )
    _need(1 <= len(row_pages) <= 512)
    fills, fees, links = [], [], []
    for bill_id, (row, page, sources) in row_pages.items():
        at = facts.source_time(row["fillTime"], page)
        # Decimal construction preserves the exact source lexeme. The existing
        # forensic domain rejects unsupported precision; it is never rounded.
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
            role="entry",
            side=row["side"],
            contracts=Decimal(row["fillSz"]),
            price=Decimal(row["fillPx"]),
        )
        fills.append(fill)
        fees.append(
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
                "bill_id": bill_id,
                "trade_id": row["tradeId"],
                "order_id": order_id,
                "source_pages": sources,
            }
        )
    fills.sort(key=lambda item: (item.occurred_at, item.evidence_id))
    fees.sort(key=lambda item: (item.occurred_at, item.evidence_id))
    reasons = {
        "fills": "terminal_close_and_all_trade_fill_membership_not_produced",
        "fees": "only_observed_entry_fill_fees_not_full_trade_fee_membership",
        "funding": "posted_funding_membership_and_effective_accrual_not_produced",
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
            source_sha256=sha(assessment.receipt_json),
            recorded_at=observed,
            stream=stream,
            status="partial",
            started_at=created,
            ended_at=observed,
            reason=reasons[stream],
        )
        for stream in ("fills", "fees", "funding", "path")
    )
    packet = core.ForensicsInput(
        candidate=candidate,
        fills=tuple(fills),
        cashflows=tuple(fees),
        path=(),
        coverage=coverage,
        observed_at=observed,
        purpose="observed",
    )
    analysis = core.analyze_trade(packet, expected_candidate_sha256=candidate_pin)
    costs = execution.analyze_execution_forensics(
        analysis,
        expected_candidate_sha256=candidate_pin,
        policy=execution.ExecutionForensicsPolicy(
            policy_id="native_entry_partial_references_unknown_v1",
            maximum_reference_age_ms=5000,
        ),
        references=(),
    )
    receipt = {
        "schema_version": "ctcc.native_entry_forensics_data.v2",
        "report_id": candidate.report_id,
        "native_original_candidate_sha256": request.origin.original_candidate_sha256,
        "forensic_projection_candidate_sha256": candidate_pin,
        "reservation_id": target.reservation_id,
        "request_sha256": sha(target.request_json.encode()),
        "intent_sha256": target.intent_sha256,
        "durable_lineage_sha256": target.durable_lineage_sha256,
        "original_event_key": request.origin.original_event_key,
        "original_policy_sha256": request.origin.original_policy_sha256,
        "g12_evidence_sha256": request.origin.evidence_sha256,
        "publication_sha256": request.origin.publication_sha256,
        "entry_order_id": order_id,
        "entry_client_order_id": assessed["client_order_id"],
        "protection_client_order_id": assessed["protection_client_order_id"],
        "assessment_json": assessment.receipt_json.decode(),
        "assessment_sha256": sha(assessment.receipt_json),
        "source_links": sorted(links, key=lambda item: item["fill_evidence_id"]),
        "analysis": analysis.model_dump(mode="json", round_trip=True),
        "execution_costs": decode(costs.canonical_payload, execution.MAX_BYTES),
        "original_recorded_at_basis": "original_qualification_evaluated_at",
        "original_R_basis": "planned_contracts_times_ctVal_times_abs_entry_minus_stop_price_cash",
        "native_reservation_cost_inclusive_coverage": request.coverage.model_dump(
            mode="json", round_trip=True
        ),
        "actual_fill_pnl_already_contains_price_friction": True,
        "unobserved_close_rows": None,
        "unobserved_funding_rows": None,
        "unobserved_holding_path": None,
        "unobserved_prefill_quote_references": None,
        "source_authenticity_verified": False,
        "account_complete": False,
        "closed_trade_verified": False,
        "report_eligible": False,
        "execution_authority": False,
    }
    return analysis, costs, receipt


def _result(analysis, costs, receipt):
    raw = canonical(receipt)
    _need(len(raw) <= _MAX_RECEIPT)
    return NativeEntryForensicsReplayV2(analysis, costs, raw, sha(raw))


def reconstruct_native_entry_forensics_v2(target, observations):
    """Pure original-row replay only; supplied DATA never becomes ownership."""
    try:
        return _result(*_entry_mapping(target, observations))
    except Exception:  # noqa: BLE001 -- redact all private source/errors.
        raise NativeForensicsError(
            "native_entry_forensics_reconstruction_invalid"
        ) from None


def analyze_owned_native_entry_v2(result):
    """Read genuine retained same-task entry facts; no close MOVE or release."""
    from app.database.repositories import native_postsubmit_v1 as repository
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    try:
        source = native._replay_native_forensics_entry_v1(result, key=_SOURCE_KEY)
        _need(
            type(source) is NativeEntryForensicsSourceV2
            and type(source.target) is repository.NativeTargetReadbackV1
            and type(source.response_source) is native.NativeSubmissionResponseDataV1
        )
        target, actual = source.target, source.readback
        _need(
            type(actual) is tuple
            and len(actual) == 4
            and type(actual[1]) is facts.NativeReconciliationAssessmentV1
            and type(actual[2]) is tuple
            and type(actual[3]) is tuple
            and len(actual[3]) == 10
            and target.facts.request_json
            == source.response_source.transport_facts.request_json
        )
        analysis, costs, receipt = _entry_mapping(target.facts, actual[2])
        _need(receipt["assessment_json"].encode() == actual[1].receipt_json)
        receipt["native_retained_lineage"] = {
            "readback_outcome_id": actual[0],
            "readback_receipt_sha256": sha(actual[1].receipt_json),
            "durable_core_sha256": sha(target.core_json),
            "issued_row_sha256": sha(target.issued_row_json),
            "lineage_row_sha256": sha(target.lineage_row_json),
            "original_capture_id": target.current_capture_id,
            "original_capture_head_sha256": target.current_head_sha256,
            "database_role_oid": actual[3][3],
            "database_backend_pid": actual[3][7],
            "database_xid": actual[3][8],
            "database_recorded_at": actual[3][9].isoformat(),
        }
        again = native._replay_native_forensics_entry_v1(result, key=_SOURCE_KEY)
        _need(
            type(again) is NativeEntryForensicsSourceV2
            and again.target is target
            and again.readback is actual
            and again.response_source is source.response_source
        )
        return _result(analysis, costs, receipt)
    except Exception:  # noqa: BLE001 -- never leak private registry/row errors.
        raise NativeForensicsError(
            "native_entry_forensics_owned_source_unavailable"
        ) from None
