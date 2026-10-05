"""B5 source-derived components; complete lifecycle history remains separate.

Historical replay can verify a measured value. It cannot mint current native
ownership, which is held only in account_portfolio_runtime's invocation registry.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_consistency as consistency
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.portfolio_components.v1",
        "environment": "demo",
        "registration_region": "global",
        "account_level": "2",
        "settlement_currency": "USDT",
        "current_scope": "complete_observed_flat_exchange_inventory_and_all_currency_local_storage",
        "hwm": "ctcc.measured_balance_hwm.v1",
        "hwm_genesis": "balance_body_completion_of_immutable_scope_sequence_one",
        "hwm_population": "every_accepted_measurement_through_pinned_sequence_no_prefix_omission",
        "hwm_time": "measured_body_completion_original_uTime_retained",
        "hwm_intermediate_unsampled_peak": "unknown",
        "history": "source_verified_completed_lifecycles_and_actual_seed_required",
        "maximum_owned_seconds": 30,
        "publish_account_revision": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class PortfolioComponentError(ValueError):
    """Static failure codes only."""


def deny(code):
    raise PortfolioComponentError(code)


@dataclass(frozen=True, slots=True, repr=False)
class BalanceMeasurement:
    source_json: bytes


def observe_balance(chain, *, reference, scope):
    """Replay the original B1 chain, never accept a caller balance or stamp."""
    checked_bootstrap(scope, LedgerScope)
    history.verify_history_query_chain(chain, **observed._pins(reference, scope))
    if observed.source_reference(chain) != reference:
        deny("component_balance_reference_mismatch")
    payloads = [
        point.event.packet_payload for point in chain if point.event.packet_payload
    ]
    if len(payloads) != 1:
        deny("component_balance_packet_missing")
    packet = capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    if (
        type(packet.plan)
        not in (
            capture.RegionalDemoAccountCapturePlan,
            capture.AllProductDemoAccountCapturePlan,
        )
        or packet.plan.registration_region != "global"
        or scope.settlement_currency != "USDT"
    ):
        deny("component_balance_scope_unsupported")
    config = json.loads(
        next(
            page
            for page in packet.observations
            if page.request.stream == "config_after"
        )
        .rows[0]
        .canonical_json
    )
    if config["acctLv"] != "2":
        deny("component_balance_mode_unsupported")
    cross = consistency._reconcile_verified_packet(packet, reference.packet_sha256)
    if cross.blocking_reasons:
        deny("component_balance_source_inconsistent")
    pages = [
        (index, page)
        for index, page in enumerate(packet.observations)
        if page.request.stream == "balance"
    ]
    if len(pages) != 1 or len(pages[0][1].rows) != 1:
        deny("component_balance_cardinality_invalid")
    index, page = pages[0]
    row = json.loads(page.rows[0].canonical_json)
    equity, available, gap = mapping._balance(row, scope.settlement_currency)
    if gap:
        deny("component_balance_value_unknown")
    stamps = (row.get("uTime"), row["details"][0].get("uTime"))
    if any(
        mapping._time(item) is None or mapping._time(item) > page.body_completed_at
        for item in stamps
    ):
        deny("component_balance_update_time_invalid")
    finalized = [
        journal.checked_event(point.event)["data"]
        for point in chain
        if journal.checked_event(point.event)["kind"] == "raw_finalized"
    ]
    if len(finalized) != len(packet.observations):
        deny("component_balance_transport_inventory_missing")
    native = all(
        item.get("tls_provenance") == "owned_signed_verified_tls"
        and type(item.get("tls_certificate_sha256")) is str
        and re.fullmatch(r"[0-9a-f]{64}", item["tls_certificate_sha256"]) is not None
        for item in finalized
    )
    return BalanceMeasurement(
        journal.canonical(
            {
                "schema_version": "ctcc.measured_balance.v1",
                "source_reference": observed.reference_document(reference),
                "environment": scope.environment,
                "account_id": scope.account_id,
                "main_uid": config["mainUid"],
                "settlement_currency": scope.settlement_currency,
                "equity": {
                    "numerator": str(equity.numerator),
                    "denominator": str(equity.denominator),
                },
                "available_margin": {
                    "numerator": str(available.numerator),
                    "denominator": str(available.denominator),
                },
                "request_index": index,
                "row_ordinal": 0,
                "raw_row_sha256": journal.digest(page.rows[0].canonical_json.encode()),
                "raw_row_json": page.rows[0].canonical_json,
                "page_receipt_sha256": page.receipt_sha256,
                "body_sha256": page.body_sha256,
                "request_started_at": page.request_started_at.isoformat(),
                "headers_received_at": page.headers_received_at.isoformat(),
                "body_completed_at": page.body_completed_at.isoformat(),
                "account_uTime": stamps[0],
                "settlement_uTime": stamps[1],
                "recorded_native_transport": native,
                "current_source_authority": False,
            }
        )
    )


class MeasuredHWMFold:
    """Streaming maximum with exact sequence/prefix membership; no lifetime cap.

    Only the owned repository/runtime can establish the population was actually
    read. Calling this reducer with constructed measurements grants no ownership.
    """

    def __init__(self, scope, through_sequence, head_sha256):
        checked_bootstrap(scope, LedgerScope)
        if type(through_sequence) is not int or through_sequence < 1:
            deny("component_hwm_sequence_invalid")
        journal._sha(head_sha256)
        self.scope, self.through, self.head = scope, through_sequence, head_sha256
        self.sequence, self.previous, self.first, self.last = 0, None, None, None
        self.peak, self.peak_at, self.peak_source = None, None, None
        self.native = True
        self.membership = hashlib.sha256()

    def add(self, sequence, previous_sha256, event_sha256, measurement):
        if (
            type(sequence) is not int
            or sequence != self.sequence + 1
            or sequence > self.through
            or previous_sha256 != self.previous
            or type(measurement) is not BalanceMeasurement
        ):
            deny("component_hwm_prefix_missing_or_reordered")
        journal._sha(event_sha256)
        data = json.loads(measurement.source_json)
        if (data["environment"], data["account_id"], data["settlement_currency"]) != (
            self.scope.environment,
            self.scope.account_id,
            self.scope.settlement_currency,
        ):
            deny("component_hwm_scope_mismatch")
        measured = capture._utc(datetime.fromisoformat(data["body_completed_at"]))
        if self.last is not None and measured < self.last:
            deny("component_hwm_measurement_reversed")
        equity = Fraction(
            int(data["equity"]["numerator"]), int(data["equity"]["denominator"])
        )
        if equity <= 0 or type(data["recorded_native_transport"]) is not bool:
            deny("component_hwm_measurement_invalid")
        self.first = measured if self.first is None else self.first
        self.last = measured
        if self.peak is None or equity > self.peak:
            self.peak, self.peak_at, self.peak_source = (
                equity,
                measured,
                journal.digest(measurement.source_json),
            )
        self.native &= data["recorded_native_transport"]
        self.membership.update(
            journal.canonical(
                [
                    sequence,
                    previous_sha256,
                    event_sha256,
                    journal.digest(measurement.source_json),
                ]
            )
            + b"\n"
        )
        self.sequence, self.previous = sequence, event_sha256

    def finish(self, *, required_window_started_at=None):
        if (
            self.sequence != self.through
            or self.previous != self.head
            or self.peak is None
        ):
            deny("component_hwm_population_incomplete")
        required = (
            None
            if required_window_started_at is None
            else capture._utc(required_window_started_at)
        )
        return journal.canonical(
            {
                "schema_version": "ctcc.measured_balance_hwm.v1",
                "policy_sha256": POLICY_SHA256,
                "sample_count": self.sequence,
                "through_sequence": self.through,
                "head_sha256": self.head,
                "window_started_at": self.first.isoformat(),
                "last_measured_at": self.last.isoformat(),
                "peak_equity": {
                    "numerator": str(self.peak.numerator),
                    "denominator": str(self.peak.denominator),
                },
                "peak_observed_at": self.peak_at.isoformat(),
                "peak_source_sha256": self.peak_source,
                "membership_sha256": self.membership.hexdigest(),
                "measured_population_complete": True,
                "all_sources_recorded_native": self.native,
                "required_window_started_at": None
                if required is None
                else required.isoformat(),
                "risk_window_matches": None
                if required is None
                else required == self.first,
                "unsampled_intermediate_peak": "unknown",
                "execution_authority": False,
            }
        )
