"""B4 bounded current-source observation; never flat-start or risk authority.

Input is the original pinned B1 journal, not a materializer/result DTO. Last
exchange change times and measured response times remain separate quantities.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_consistency as consistency
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_materializer as materializer
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

CURRENT_STREAMS = (
    "config_before",
    "account_position_risk",
    "balance",
    "positions",
    "orders_pending",
    *(f"algo_{kind}" for kind in capture.ALGO_ORDER_TYPES),
    "account_instruments",
    "leverage_cross",
    "leverage_isolated",
    "config_after",
)
INVENTORY_STREAMS = (
    "positions",
    "orders_pending",
    *(stream for stream in CURRENT_STREAMS if stream.startswith("algo_")),
)
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.current_account_source_policy.v2",
        "environment": "demo",
        "region": "global",
        "settlement_currency": "USDT",
        "account_level": "2",
        "position_modes": ["net_mode", "long_short_mode"],
        "supported_exposure_product": "SWAP",
        "maximum_current_response_span_seconds": 120,
        "maximum_measured_receipt_age_seconds": 30,
        "source_update_time": "retained_last_exchange_change_not_read_freshness",
        "query_scope": "exact_unfiltered_current_inventory_requests_with_full_terminal_pages",
        "capture_plan_contract": "ctcc.demo_account_plan.v5",
        "current_streams": list(CURRENT_STREAMS),
        "inventory_streams": list(INVENTORY_STREAMS),
        "algo_order_types": list(capture.ALGO_ORDER_TYPES),
        "flat_definition": "no_rows_in_positions_ordinary_pending_or_any_current_algo",
        "flat_start_permission": False,
        "execution_authority": False,
        "history_policy_sha256": history.POLICY_SHA256,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
V6_LEGACY_POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.current_account_source_policy.v3",
        "prior_current_policy_sha256": POLICY_SHA256,
        "capture_plan_contract": "ctcc.demo_current_account_plan.v6",
        "current_streams": list(capture.V6_CURRENT_STREAMS),
        "original_journal": "complete_B1_page_chain_and_terminal_replay_required",
        "exact_identity": "environment_uid_main_uid_session_region_mode_currency",
        "history": "separate_original_source_join_required",
        "account_complete": False,
        "execution_authority": False,
    }
)
V6_LEGACY_POLICY_SHA256 = journal.digest(V6_LEGACY_POLICY_BYTES)
V6_POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.current_account_source_policy.v4",
        "prior_current_policy_sha256": V6_LEGACY_POLICY_SHA256,
        "capture_plan_contract": "ctcc.demo_current_account_plan.v6",
        "current_streams": list(capture.V6_CURRENT_STREAMS),
        "original_journal": "complete_B1_page_chain_and_terminal_replay_required",
        "maximum_measured_receipt_age_seconds": 30,
        "freshness_basis": "replay_verified_B1_body_complete_EOF_not_response_close",
        "exact_identity": "environment_uid_main_uid_session_region_mode_currency",
        "history": "separate_original_source_join_required",
        "account_complete": False,
        "execution_authority": False,
    }
)
V6_POLICY_SHA256 = journal.digest(V6_POLICY_BYTES)


class CurrentAccountSourceError(ValueError):
    """Static errors; no credentials, private records or SQL enter exceptions."""


@dataclass(frozen=True, slots=True, repr=False)
class CurrentAccountSourceVerification:
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
    def flat_start_permission(self):
        return False


def _deny(code):
    raise CurrentAccountSourceError(code)


def _number(value):
    return (
        None
        if value is None
        else {"numerator": str(value.numerator), "denominator": str(value.denominator)}
    )


def verify_current_account_sources(
    chain, *, reference, scope, validated_at, expected_policy_sha256=POLICY_SHA256
):
    """Verify recorded current reads. Receipt freshness is scoped to validated_at.

    This pure timestamp is a declared replay cutoff, not an owned current clock.
    A subsequent owned coordinator must independently obtain its live clock,
    source session, local exposure state and all remaining risk dependencies.
    """
    try:
        return _verify(chain, reference, scope, validated_at, expected_policy_sha256)
    except CurrentAccountSourceError:
        raise
    except Exception:  # noqa: BLE001 -- private parsing details are never emitted
        raise CurrentAccountSourceError("current_source_verification_invalid") from None


def _verify_current_only_chain(chain, reference, scope, *, with_eof=False):
    """Replay the complete B1 current-only journal, without inventing history."""
    records = history._chain(chain, reference.head_sha256)
    packets = [item.event.packet_payload for item in chain if item.event.packet_payload]
    if len(packets) != 1:
        _deny("current_source_original_packet_required")
    packet = capture.verify_demo_account_packet(
        packets[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    if type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV6:
        _deny("current_source_capture_plan_version_required")
    initial = records[0]["data"]
    if (
        packet.plan.environment != scope.environment
        or packet.plan.expected_uid != scope.account_id
        or packet.plan.settlement_currency != scope.settlement_currency
        or records[0]["kind"] != "capture_start"
        or initial.get("environment") != scope.environment
        or initial.get("account_id") != scope.account_id
        or initial.get("settlement_currency") != scope.settlement_currency
        or initial.get("plan_sha256") != reference.plan_sha256
        or journal.canonical(initial.get("plan"))
        != journal.canonical(capture._json_value(packet.plan))
        or initial.get("session_binding_sha256")
        != journal.digest(packet.plan.session_binding_id.encode("ascii"))
        or initial.get("requested_streams") != list(capture.V6_CURRENT_STREAMS)
    ):
        _deny("current_source_capture_binding_mismatch")
    journal._sha(initial.get("local_checkpoint_sha256"))
    history._joined_pages(records, chain, packet)
    eof_times = []
    if with_eof:
        # The packet's body_completed_at is the response-close upper bound.
        # B1's body_complete is measured EOF, already checked against original
        # page bytes, stage order and packet by _joined_pages.
        eof_records = [item for item in records if item["kind"] == "body_complete"]
        if len(eof_records) != len(packet.observations):
            _deny("current_source_eof_witness_missing")
        for index, (record, page) in enumerate(
            zip(eof_records, packet.observations, strict=True)
        ):
            data = record["data"]
            if (
                type(data.get("request_index")) is not int
                or data["request_index"] != index
                or data.get("stream") != page.request.stream
                or type(data.get("page_index")) is not int
                or data["page_index"] != page.page_index
                or data.get("body_exhaustion_observed") is not True
            ):
                _deny("current_source_eof_witness_mismatch")
            eof = history._time(record["observed_at"])
            if (
                data.get("body_completed_at") != eof.isoformat()
                or not page.headers_received_at <= eof <= page.body_completed_at
            ):
                _deny("current_source_eof_witness_mismatch")
            eof_times.append(eof)
    if (
        records[-2]["data"].get("plan_sha256") != reference.plan_sha256
        or records[-2]["data"].get("local_checkpoint_sha256")
        != initial["local_checkpoint_sha256"]
    ):
        _deny("current_source_checkpoint_mismatch")
    return tuple(eof_times) if with_eof else packet


def _verify(chain, reference, scope, validated_at, policy):
    checked_bootstrap(scope, LedgerScope)
    observed.reference_document(reference)
    if (
        type(validated_at) is not datetime
        or type(policy) is not str
        or policy not in {POLICY_SHA256, V6_LEGACY_POLICY_SHA256, V6_POLICY_SHA256}
    ):
        _deny("current_source_policy_or_validation_invalid")
    at = capture._utc(validated_at)
    if type(chain) is not tuple or not chain:
        _deny("current_source_original_chain_required")
    history_proof = (
        history.verify_history_query_chain(chain, **observed._pins(reference, scope))
        if policy == POLICY_SHA256
        else None
    )
    if observed.reference_document(
        observed.source_reference(chain)
    ) != observed.reference_document(reference):
        _deny("current_source_reference_mismatch")
    packets = [item.event.packet_payload for item in chain if item.event.packet_payload]
    if len(packets) != 1:
        _deny("current_source_original_packet_required")
    packet = capture.verify_demo_account_packet(
        packets[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    current_replay = (
        _verify_current_only_chain(
            chain, reference, scope, with_eof=policy == V6_POLICY_SHA256
        )
        if policy in {V6_LEGACY_POLICY_SHA256, V6_POLICY_SHA256}
        else None
    )
    eof_times = current_replay if policy == V6_POLICY_SHA256 else None
    if at < packet.completed_at:
        _deny("current_source_validation_precedes_capture")
    blocked = set()
    if type(packet.plan) is not (
        capture.AllProductDemoAccountCapturePlan
        if policy == POLICY_SHA256
        else capture.CurrentDemoAccountCapturePlanV6
    ):
        _deny("current_source_capture_plan_version_required")
    if packet.plan.registration_region != "global":
        blocked.add("current_source_region_unsupported")
    if scope.settlement_currency != "USDT":
        blocked.add("current_source_settlement_unsupported")
    grouped = {stream: [] for stream in CURRENT_STREAMS}
    pages, records = [], []
    for request_index, page in enumerate(packet.observations):
        stream = page.request.stream
        if stream not in grouped:
            continue
        grouped[stream].append(page)
        # Query replay already validates the complete page chain and exact
        # requests; explicitly retain its current-inventory request scope.
        if stream in INVENTORY_STREAMS and any(
            name in {"instId", "instType", "ccy"} for name, _ in page.request.parameters
        ):
            _deny("current_inventory_filtered_query")
        page_record = {
            "stream": stream,
            "request_index": request_index,
            "page_index": page.page_index,
            "request_query": list(page.request.parameters),
            "after": page.after,
            "previous_page_sha256": page.previous_page_sha256,
            "body_sha256": page.body_sha256,
            "receipt_sha256": page.receipt_sha256,
            "request_started_at": page.request_started_at.isoformat(),
            "headers_received_at": page.headers_received_at.isoformat(),
            "body_completed_at": page.body_completed_at.isoformat(),
            "terminal": page.terminal,
            "row_count": len(page.rows),
        }
        if policy == V6_POLICY_SHA256:
            page_record["body_exhausted_at"] = eof_times[request_index].isoformat()
        pages.append(page_record)
        for ordinal, row in enumerate(page.rows):
            raw = json.loads(row.canonical_json)
            if stream in INVENTORY_STREAMS and raw.get("instType") != "SWAP":
                blocked.add("current_exposure_product_unsupported")
            records.append(
                {
                    "stream": stream,
                    "row_id": row.row_id,
                    "instrument_id": row.instrument_id,
                    "row_sha256": journal.digest(row.canonical_json.encode()),
                    "request_index": request_index,
                    "row_ordinal": ordinal,
                    "page_receipt_sha256": page.receipt_sha256,
                    "source_times": [
                        {
                            "path": item.path,
                            "raw": item.raw,
                            "value": None
                            if item.value is None
                            else item.value.isoformat(),
                            "meaning": item.semantics,
                        }
                        for item in row.source_times
                    ],
                }
            )
    if any(not group or group[-1].terminal is not True for group in grouped.values()):
        _deny("current_inventory_terminal_page_missing")
    started = min(
        page.request_started_at for group in grouped.values() for page in group
    )
    finished = max(
        page.body_completed_at for group in grouped.values() for page in group
    )
    earliest_received = (
        min(eof_times)
        if policy == V6_POLICY_SHA256
        else min(page.body_completed_at for group in grouped.values() for page in group)
    )
    if finished - started > timedelta(seconds=120):
        blocked.add("current_response_interval_exceeds_policy")
    if at - earliest_received > timedelta(seconds=30):
        blocked.add("measured_current_receipt_stale")
    config = json.loads(grouped["config_after"][0].rows[0].canonical_json)
    # A packet-wide gap can come from a historical stream. The flat diagnostic
    # concerns current inventory, so inspect its own row fields. Futures-mode
    # inapplicable top-level equity fields remain visible in raw evidence.
    current_field_missing = any(
        set(row.missing_fields)
        - (
            {"availEq"}
            if config["acctLv"] == "2" and stream == "balance"
            else {"adjEq"}
            if config["acctLv"] == "2" and stream == "account_position_risk"
            else set()
        )
        for stream, stream_pages in grouped.items()
        for page in stream_pages
        for row in page.rows
    )
    if config.get("acctLv") != "2" or config.get("posMode") not in {
        "net_mode",
        "long_short_mode",
    }:
        blocked.add("current_account_mode_unsupported")
    balance_page = grouped["balance"][0]
    balance = json.loads(balance_page.rows[0].canonical_json)
    equity, available, balance_gap = materializer._balance(
        balance, scope.settlement_currency
    )
    if balance_gap:
        blocked.add(balance_gap)
    matched = [
        item
        for item in balance["details"]
        if item.get("ccy") == scope.settlement_currency
    ]
    detail = matched[0] if len(matched) == 1 else {}
    source_times = {}
    for name, raw in (
        ("account_uTime", balance.get("uTime")),
        ("settlement_uTime", detail.get("uTime")),
    ):
        value = capture._time_record(name, raw, "source_update").value
        source_times[name] = {
            "raw": raw,
            "value": None if value is None else value.isoformat(),
        }
        if value is None:
            blocked.add("balance_source_update_time_unknown")
        elif value > balance_page.body_completed_at:
            _deny("balance_update_after_observed_body")
    cross = consistency._reconcile_verified_packet(packet, reference.packet_sha256)
    blocked.update(cross.blocking_reasons)
    counts = {
        stream: sum(len(page.rows) for page in grouped[stream])
        for stream in INVENTORY_STREAMS
    }
    exchange_empty = not any(counts.values())
    if exchange_empty and current_field_missing:
        blocked.add("current_packet_source_fields_missing")
    if not exchange_empty:
        blocked.add("current_exposure_requires_protection_and_local_join")
    value = {
        "schema_version": (
            "ctcc.current_account_source_observation.v2"
            if policy == POLICY_SHA256
            else "ctcc.current_account_source_observation.v3"
            if policy == V6_LEGACY_POLICY_SHA256
            else "ctcc.current_account_source_observation.v4"
        ),
        "policy_sha256": policy,
        "scope_sha256": journal.digest(
            journal.canonical(
                [scope.environment, scope.account_id, scope.settlement_currency]
            )
        ),
        "source_reference": observed.reference_document(reference),
        "history_query_verifier_sha256": (
            None if history_proof is None else history_proof.receipt_sha256
        ),
        "capture_scope": packet.plan.capture_scope,
        "packet_schema_version": packet.schema_version,
        "packet_incomplete_reasons": list(packet.incomplete_reasons),
        "identity": {
            "environment": scope.environment,
            "uid": config["uid"],
            "main_uid": config["mainUid"],
            "account_level": config["acctLv"],
            "position_mode": config["posMode"],
            "settlement_currency": scope.settlement_currency,
        },
        "observation_interval": {
            "request_started_at": started.isoformat(),
            "body_completed_at": finished.isoformat(),
            "validated_at": at.isoformat(),
            "publication_barrier": packet.barrier_completed_at.isoformat(),
        },
        "balance": {
            "equity": _number(equity),
            "available_equity": _number(available),
            "currency": scope.settlement_currency,
            "source_update_times": source_times,
            "measured_stamp": {
                "request_started_at": balance_page.request_started_at.isoformat(),
                "headers_received_at": balance_page.headers_received_at.isoformat(),
                "body_completed_at": balance_page.body_completed_at.isoformat(),
                "receipt_sha256": balance_page.receipt_sha256,
            },
        },
        "inventory_row_counts": counts,
        "current_pages": pages,
        "current_rows": records,
        "consistency_report_sha256": journal.digest(
            journal.canonical(cross.model_dump(mode="json"))
        ),
        "consistency_findings": [
            item.model_dump(mode="json") for item in cross.findings
        ],
        "observed_flat": exchange_empty and not blocked,
        "observed_inventory_state": "observed_empty_supported_queries"
        if exchange_empty and not blocked
        else "incomplete_or_exposed",
        "blocking_reasons": sorted(blocked),
        "unverified": [
            "exchange_global_atomic_revision",
            "complete_product_and_liability_scope",
            "source_authenticity",
            "current_owned_session_authority",
            "local_reservations",
            "local_unresolved_intents",
            "local_uncertain_or_untracked_exposure",
            "exact_active_protection",
            "full_net_loss_window",
            "funding_accrual",
            "completed_lifecycle_and_streak",
            "measured_hwm",
        ],
        "account_complete": False,
        "source_authenticity_verified": False,
        "flat_start_permission": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    if policy in {V6_LEGACY_POLICY_SHA256, V6_POLICY_SHA256}:
        value["recorded_current_chain_head_sha256"] = reference.head_sha256
        value["history_join_state"] = "separate_original_history_required"
        value["unverified"].append("separate_history_source_join")
    if policy == V6_POLICY_SHA256:
        value["observation_interval"]["earliest_body_exhausted_at"] = (
            earliest_received.isoformat()
        )
        value["observation_interval"]["freshness_basis"] = (
            "replay_verified_B1_body_complete_EOF"
        )
        balance_index = packet.observations.index(balance_page)
        value["balance"]["measured_stamp"]["body_exhausted_at"] = eof_times[
            balance_index
        ].isoformat()
    return CurrentAccountSourceVerification(journal.canonical(value))
