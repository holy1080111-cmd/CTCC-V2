"""B3: immutable observed fill cashflows; no full loss or execution authority.

Every replay starts with externally pinned original B1 chains, never a supplied
coverage/status/result object. The policy is an explicit bounded observation
definition, not a guarantee of exchange publication latency or future finality.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as query
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_CAPTURES = 32
MAX_ROWS = 16384
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.observed_execution_cashflow_policy.v2",
        "environment": "demo",
        "region": "global",
        "query_policy_sha256": query.POLICY_SHA256,
        "maximum_window_days": 7,
        "minimum_generation_tail_seconds": 300,
        "maximum_query_end_age_seconds": 120,
        "maximum_inter_capture_gap_seconds": 3600,
        "overlap": "each_previous_query_end_must_be_covered_by_next_start",
        "late_conflict_missing": "append_evidence_and_invalidate_all_prior_proofs",
        "cashflow": "signed_fillPnl_plus_signed_fee_at_actual_fillTime",
        "cashflow_scope": "linear_SWAP_settlement_fee_currency_only",
        "funding_accrual": "unknown_not_bill_ts",
        "window_endpoints": "inclusive_start_and_end",
        "future_late_arrival_finality": False,
        "maximum_captures_per_proof": MAX_CAPTURES,
        "durable_sequence": "positive_bigint_no_lifetime_capture_cap",
        "index_continuation": "persistent_source_facts_coverage_and_findings",
        "maximum_rows": MAX_ROWS,
        "maximum_source_bytes": MAX_SOURCE_BYTES,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class AccountObservationError(ValueError):
    """Static private-data-safe failure codes only."""


def deny(code):
    raise AccountObservationError(code)


@dataclass(frozen=True, slots=True, repr=False)
class CaptureReference:
    capture_id: str
    head_sha256: str
    plan_sha256: str
    packet_sha256: str
    session_binding_sha256: str

    def __post_init__(self):
        if (
            type(self.capture_id) is not str
            or len(self.capture_id) != 32
            or any(c not in "0123456789abcdef" for c in self.capture_id)
        ):
            deny("observation_capture_identity_invalid")
        for value in (
            self.head_sha256,
            self.plan_sha256,
            self.packet_sha256,
            self.session_binding_sha256,
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                deny("observation_pin_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionWindow:
    started_at: datetime
    ended_at: datetime

    def __post_init__(self):
        start, end = capture._utc(self.started_at), capture._utc(self.ended_at)
        if not start <= end or end - start > timedelta(days=7):
            deny("observation_window_invalid")
        if any(value.microsecond % 1000 for value in (start, end)):
            deny("observation_window_milliseconds_required")


@dataclass(frozen=True, slots=True, repr=False)
class ObservationReplay:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def reference_document(value):
    if type(value) is not CaptureReference:
        deny("observation_exact_reference_required")
    value.__post_init__()
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def window_document(value):
    if type(value) is not ExecutionWindow:
        deny("observation_exact_window_required")
    value.__post_init__()
    return {
        "started_at": capture._utc(value.started_at).isoformat(),
        "ended_at": capture._utc(value.ended_at).isoformat(),
    }


def source_reference(chain):
    """Extract pins for storage; callers still must independently replay B1/B2a."""
    first = journal.checked_event(chain[0].event)
    payloads = [
        item.event.packet_payload for item in chain if item.event.packet_payload
    ]
    if len(payloads) != 1:
        deny("observation_original_packet_required")
    return CaptureReference(
        first["capture_id"],
        journal.digest(chain[-1].event.event_json),
        first["data"]["plan_sha256"],
        journal.digest(payloads[0]),
        first["data"]["session_binding_sha256"],
    )


def _pins(reference, scope):
    return {
        "expected_head_sha256": reference.head_sha256,
        "expected_plan_sha256": reference.plan_sha256,
        "expected_packet_sha256": reference.packet_sha256,
        "expected_account_id": scope.account_id,
        "expected_settlement_currency": scope.settlement_currency,
    }


def _amount(value):
    if type(value) is not str:
        deny("observation_cashflow_operand_missing")
    number = Decimal(value)
    if not number.is_finite():
        deny("observation_cashflow_operand_invalid")
    return Fraction(number)


def _fraction(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _compare_verified(old, new):
    """Private comparison of receipts verified in this invocation only.

    Preserves B2a comparison bytes without replaying every raw chain per pair.
    This does not accept, create or cache any current account authority.
    """
    if (
        old["scope_sha256"] != new["scope_sha256"]
        or old["policy_sha256"] != new["policy_sha256"]
    ):
        query._deny("history_query_comparison_scope_mismatch")
    if query._time(new["observed_completed_at"]) < query._time(
        old["observed_completed_at"]
    ):
        query._deny("history_query_comparison_clock_reversed")
    prior = {row["identity_sha256"]: row for row in old["rows"]}
    current = {row["identity_sha256"]: row for row in new["rows"]}
    previous_domains = {(item["family"], item["product"]) for item in old["coverage"]}
    current_domains = {(item["family"], item["product"]) for item in new["coverage"]}
    uncompared = [
        {
            "family": family,
            "product": product,
            "observed_only_in": "previous"
            if (family, product) in previous_domains
            else "current",
        }
        for family, product in sorted(previous_domains ^ current_domains)
    ]
    findings = []
    for row in new["rows"]:
        earlier = prior.get(row["identity_sha256"])
        if earlier is not None:
            kind = (
                "matching_overlap"
                if earlier["row_sha256"] == row["row_sha256"]
                else "conflicting_overlap"
            )
        else:
            at = query._time(row["generation_at"])
            covered_before = any(
                coverage_item["family"] == row["family"]
                and coverage_item["product"] in {"*", row["product"]}
                and any(
                    query._time(a) <= at <= query._time(b)
                    for a, b in coverage_item["covered_intervals"]
                )
                for coverage_item in old["coverage"]
            )
            kind = (
                "late_observation_in_prior_query"
                if covered_before
                else "new_observation"
            )
        findings.append(
            {
                "identity_sha256": row["identity_sha256"],
                "kind": kind,
                "previous_locators": [] if earlier is None else earlier["locators"],
                "current_locators": row["locators"],
            }
        )
    for key, row in prior.items():
        if key in current:
            continue
        at = query._time(row["generation_at"])
        if any(
            coverage_item["family"] == row["family"]
            and coverage_item["product"] in {"*", row["product"]}
            and any(
                query._time(a) <= at <= query._time(b)
                for a, b in coverage_item["covered_intervals"]
            )
            for coverage_item in new["coverage"]
        ):
            findings.append(
                {
                    "identity_sha256": key,
                    "kind": "previous_row_missing_in_current_query",
                    "previous_locators": row["locators"],
                    "current_locators": [],
                }
            )
    return journal.canonical(
        {
            "schema_version": "ctcc.observed_history_query_comparison.v1",
            "previous_head_sha256": old["journal_head_sha256"],
            "current_head_sha256": new["journal_head_sha256"],
            "findings": findings,
            "uncompared_query_domains": uncompared,
            "dependent_current_claims_require_reconciliation": bool(uncompared)
            or any(
                item["kind"]
                in {
                    "conflicting_overlap",
                    "late_observation_in_prior_query",
                    "previous_row_missing_in_current_query",
                }
                for item in findings
            ),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )


def replay_observation_index(
    sources, *, scope, window, expected_policy_sha256=POLICY_SHA256
):
    """Recompute bounded history and actual-time cashflows, without any IO."""
    try:
        return _replay(sources, scope, window, expected_policy_sha256)
    except AccountObservationError:
        raise
    except Exception:  # noqa: BLE001 -- never expose private raw replay errors
        raise AccountObservationError("observation_source_replay_invalid") from None


def _replay(sources, scope, window, policy_pin, *, adjacent=True):
    checked_bootstrap(scope, LedgerScope)
    window_data = window_document(window)
    if type(policy_pin) is not str or policy_pin != POLICY_SHA256:
        deny("observation_policy_mismatch")
    if type(sources) is not tuple or not 1 <= len(sources) <= MAX_CAPTURES:
        deny("observation_capture_bound")
    rows, captures, findings, proofs = {}, [], [], []
    seen, total_bytes, previous, prior_sources = set(), 0, None, []
    for chain, reference in sources:
        reference_document(reference)
        if type(chain) is not tuple or reference.capture_id in seen:
            deny("observation_duplicate_or_invalid_capture")
        seen.add(reference.capture_id)
        total_bytes += sum(
            len(item.event.event_json)
            + len(item.event.raw_body or b"")
            + len(item.event.packet_payload or b"")
            for item in chain
        )
        if total_bytes > MAX_SOURCE_BYTES:
            deny("observation_source_byte_bound")
        pins = _pins(reference, scope)
        verified = query.verify_history_query_chain(chain, **pins)
        value = json.loads(verified.receipt_json)
        actual_reference = source_reference(chain)
        if reference_document(actual_reference) != reference_document(reference):
            deny("observation_source_session_or_pin_mismatch")
        if previous is not None:
            _, _, prior_value = previous
            if query._time(value["requested_end"]) < query._time(
                prior_value["requested_end"]
            ):
                findings.append(
                    {
                        "kind": "generation_cutoff_regressed",
                        "capture_id": reference.capture_id,
                    }
                )
            for proof_index, (_, prior_ref) in enumerate(prior_sources):
                comparison = json.loads(_compare_verified(proofs[proof_index], value))
                for item in comparison["findings"]:
                    if item["kind"] not in {"matching_overlap", "new_observation"}:
                        findings.append(
                            {
                                **item,
                                "discovered_capture_id": reference.capture_id,
                                "compared_capture_id": prior_ref.capture_id,
                                "invalidates_capture_ids": [
                                    item["capture_id"] for item in captures
                                ],
                            }
                        )
                    elif item["kind"] == "new_observation":
                        new_row = next(
                            row
                            for row in value["rows"]
                            if row["identity_sha256"] == item["identity_sha256"]
                        )
                        prior_proof = proofs[
                            [ref.capture_id for _, ref in prior_sources].index(
                                prior_ref.capture_id
                            )
                        ]
                        if (
                            new_row["family"] == "fills"
                            and new_row["fill_at"] is not None
                            and query._time(prior_proof["requested_start"])
                            <= query._time(new_row["fill_at"])
                            <= query._time(prior_proof["requested_end"])
                            - timedelta(seconds=300)
                        ):
                            findings.append(
                                {
                                    **item,
                                    "kind": "late_execution_discovered",
                                    "discovered_capture_id": reference.capture_id,
                                    "compared_capture_id": prior_ref.capture_id,
                                    "invalidates_capture_ids": [
                                        saved["capture_id"] for saved in captures
                                    ],
                                }
                            )
                if comparison["uncompared_query_domains"]:
                    findings.append(
                        {
                            "kind": "product_domain_changed",
                            "capture_id": reference.capture_id,
                        }
                    )
            gap = query._time(value["observed_completed_at"]) - query._time(
                prior_value["observed_completed_at"]
            )
            if adjacent and gap > timedelta(seconds=3600):
                findings.append(
                    {"kind": "measurement_gap", "capture_id": reference.capture_id}
                )
            if adjacent and query._time(value["requested_start"]) > query._time(
                prior_value["requested_end"]
            ):
                findings.append(
                    {
                        "kind": "generation_overlap_gap",
                        "capture_id": reference.capture_id,
                    }
                )
        packet_payload = next(
            item.event.packet_payload for item in chain if item.event.packet_payload
        )
        packet = capture.verify_demo_account_packet(
            packet_payload,
            expected_sha256=reference.packet_sha256,
            expected_plan_sha256=reference.plan_sha256,
        )
        specs = {
            row.instrument_id: json.loads(row.canonical_json)
            for observation in packet.observations
            if observation.request.stream == "account_instruments"
            for row in observation.rows
        }
        for item in value["rows"]:
            identity, row_sha = item["identity_sha256"], item["row_sha256"]
            key = (identity, row_sha)
            first_locator = item["locators"][0]
            raw = (
                packet.observations[first_locator["request_index"]]
                .rows[first_locator["row_ordinal"]]
                .canonical_json
            )
            if journal.digest(raw.encode("utf-8")) != row_sha:
                deny("observation_source_row_mismatch")
            locators = [
                {**loc, "capture_id": reference.capture_id} for loc in item["locators"]
            ]
            instrument_raw = specs.get(json.loads(raw).get("instId"))
            metadata = {
                "capture_id": reference.capture_id,
                "row_sha256": None
                if instrument_raw is None
                else journal.digest(journal.canonical(instrument_raw)),
                "supported": (
                    instrument_raw is not None
                    and instrument_raw.get("ctType") == "linear"
                    and instrument_raw.get("settleCcy") == scope.settlement_currency
                ),
            }
            if key not in rows:
                rows[key] = {
                    **{
                        name: item[name]
                        for name in (
                            "family",
                            "product",
                            "identity_sha256",
                            "row_sha256",
                            "generation_at",
                            "fill_at",
                        )
                    },
                    "first_observed_at": value["observed_completed_at"],
                    "latest_observed_at": value["observed_completed_at"],
                    "locators": [],
                    "raw": json.loads(raw),
                    "metadata_observations": [],
                }
            rows[key]["metadata_observations"].append(metadata)
            rows[key]["latest_observed_at"] = value["observed_completed_at"]
            rows[key]["locators"].extend(locators)
            if len(rows) > MAX_ROWS:
                deny("observation_row_bound")
        captures.append(
            {
                **reference_document(reference),
                "query_receipt_sha256": verified.receipt_sha256,
                "observed_completed_at": value["observed_completed_at"],
                "requested_start": value["requested_start"],
                "requested_end": value["requested_end"],
            }
        )
        proofs.append(value)
        previous = chain, reference, value
        prior_sources.append((chain, reference))
    latest = proofs[-1]
    generated_end = query._time(latest["requested_end"])
    blockers = set()
    if window.ended_at > generated_end - timedelta(seconds=300):
        blockers.add("generation_observation_tail_below_policy")
    if query._time(latest["observed_completed_at"]) - generated_end > timedelta(
        seconds=120
    ):
        blockers.add("generation_query_cutoff_stale")
    domains = {(item["family"], item["product"]) for item in latest["coverage"]}
    coverages = []
    for family, product in sorted(domains):
        intervals = query._union(
            [
                (query._time(start), query._time(end))
                for proof in proofs
                for item in proof["coverage"]
                if (item["family"], item["product"]) == (family, product)
                for start, end in item["covered_intervals"]
            ]
        )
        covered = any(
            start <= window.started_at and end >= generated_end
            for start, end in intervals
        )
        coverages.append(
            {
                "family": family,
                "product": product,
                "generation_window_covered": covered,
                "intervals": [[a.isoformat(), b.isoformat()] for a, b in intervals],
            }
        )
        if not covered:
            blockers.add("generation_window_gap")
    if findings:
        blockers.add("source_observations_require_reconciliation")
    cashflows, total = [], Fraction(0)
    for (identity, row_sha), item in sorted(rows.items()):
        raw = item.pop("raw")
        if item["family"] != "fills":
            continue
        if item["fill_at"] is None:
            blockers.add("execution_time_unknown")
            continue
        metadata_supported = all(
            value["supported"] is True for value in item["metadata_observations"]
        )
        if len({value["row_sha256"] for value in item["metadata_observations"]}) != 1:
            blockers.add("instrument_metadata_observation_conflict")
        matched = query._time(item["fill_at"])
        if not window.started_at <= matched <= window.ended_at:
            continue
        if (
            raw.get("instType") != "SWAP"
            or raw.get("feeCcy") != scope.settlement_currency
            or not metadata_supported
        ):
            blockers.add("cashflow_product_or_currency_unsupported")
            continue
        if any(raw.get(name) in (None, "") for name in ("fillPnl", "fee")):
            blockers.add("cashflow_operand_missing")
            continue
        gross, fee = _amount(raw.get("fillPnl")), _amount(raw.get("fee"))
        total += gross + fee
        cashflows.append(
            {
                "identity_sha256": identity,
                "row_sha256": row_sha,
                "instrument_id": raw["instId"],
                "execution_at": item["fill_at"],
                "generation_at": item["generation_at"],
                "currency": scope.settlement_currency,
                "gross_fill_pnl": _fraction(gross),
                "signed_fee": _fraction(fee),
                "observed_fill_cashflow": _fraction(gross + fee),
                "lifecycle_completion": "unknown",
                "locators": item["locators"],
            }
        )
    # Conflicting variants remain in the private index, but never get summed as
    # two independent financial events. The aggregate is unavailable on any gap.
    value = {
        "schema_version": "ctcc.observed_account_execution_window.v1",
        "policy_sha256": POLICY_SHA256,
        "scope_sha256": journal.digest(
            journal.canonical(
                [scope.environment, scope.account_id, scope.settlement_currency]
            )
        ),
        "window": window_data,
        "generation_observation_cutoff": generated_end.isoformat(),
        "observed_completed_at": latest["observed_completed_at"],
        "captures": captures,
        "source_index": list(rows.values()),
        "findings": findings,
        "coverage": coverages,
        "cashflows": cashflows,
        "observed_fill_cashflow_total": None if blockers else _fraction(total),
        "window_state": "incomplete_observation"
        if blockers
        else "observed_fill_cashflows",
        "blocking_reasons": sorted(blockers),
        "unverified": [
            "complete_net_loss_window",
            "funding_accrual",
            "lifecycle",
            "streak_reset_or_seed",
            "measured_hwm",
            "inventory",
            "current_owned_session_authority",
            "future_late_arrival_finality",
        ],
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    encoded = journal.canonical(value)
    if len(encoded) > MAX_RECEIPT_BYTES:
        deny("observation_receipt_byte_bound")
    return ObservationReplay(encoded)
