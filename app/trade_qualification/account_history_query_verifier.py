"""B2a: replay private B1 bytes and observed Global fills/bills query coverage.

This proves a bounded, generation-time query as recorded, never account history,
native transport ownership, an execution-time loss window or trading authority.
No IO, credential, settings or caller completeness assertion is accepted here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.global_observed_history_query_policy.v1",
        "region": "global",
        "environment": "demo",
        "documentation": "https://www.okx.com/docs-v5/en/",
        "documentation_reviewed": "2026-09-23",
        "maximum_implementation_lookback_days": 28,
        "retention_days": {"fills_recent": 3, "bills_recent": 7},
        "archive_documented_retention": "3 calendar months",
        "archive_supported_days": 28,
        "time_domain": "source_record_generation_ts",
        "cursor": "billId",
        "terminal": "explicit_empty_page",
        "orders_retention": "not_verified",
        "late_arrival_finality": "not_claimed",
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
_HISTORY_FAMILIES = frozenset(
    {"fills_recent", "fills_history", "bills_recent", "bills_archive"}
)
_PAGE_KINDS = frozenset(
    {
        "request_start",
        "headers_received",
        "body_progress",
        "body_complete",
        "page_validated",
    }
)


class HistoryQueryVerificationError(ValueError):
    """Static, public-safe codes; source contents and SQL exceptions stay private."""


def _deny(code):
    raise HistoryQueryVerificationError(code)


def _same_json(actual, expected):
    """Compare exact JSON types, including booleans versus integer locators."""
    return journal.canonical(actual) == journal.canonical(expected)


def _time(value):
    if type(value) is not str:
        _deny("history_query_clock_invalid")
    result = capture._utc(datetime.fromisoformat(value))
    if result.isoformat() != value:
        _deny("history_query_clock_invalid")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class HistoryQueryVerification:
    """Private derived diagnostic. Even direct construction grants no authority."""

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


def _chain(chain, expected_head):
    journal._sha(expected_head)
    if type(chain) is not tuple or not 1 <= len(chain) <= journal.MAX_EVENTS:
        _deny("history_query_chain_invalid")
    records, previous, total, db_previous, observed_previous = [], None, 0, None, None
    capture_id = None
    for sequence, item in enumerate(chain, 1):
        if type(item) is not journal.JournalReadback:
            _deny("history_query_readback_required")
        # Revalidate, including mutated/fabricated frozen objects, before reading.
        journal.JournalReadback(item.event, item.db_recorded_at, item.readback_at)
        record = journal.checked_event(item.event)
        if capture_id is None:
            capture_id = record["capture_id"]
        if (
            record["capture_id"] != capture_id
            or record["sequence"] != sequence
            or record["previous_sha256"] != previous
            or (db_previous is not None and item.db_recorded_at < db_previous)
        ):
            _deny("history_query_chain_invalid")
        observed = record["observed_at"]
        if observed is not None:
            observed = _time(observed)
            if observed_previous is not None and observed < observed_previous:
                _deny("history_query_clock_invalid")
            observed_previous = observed
        total += sum(
            len(value or b"")
            for value in (
                item.event.event_json,
                item.event.raw_body,
                item.event.packet_payload,
            )
        )
        if total > journal.MAX_CHAIN_BYTES:
            _deny("history_query_chain_bound")
        db_previous, previous = (
            item.db_recorded_at,
            journal.digest(item.event.event_json),
        )
        records.append(record)
    if previous != expected_head:
        _deny("history_query_head_mismatch")
    if (
        records[-1]["kind"] != "terminal"
        or records[-1]["outcome"] != "complete_recorded"
        or records[-1]["data"].get("raw_retention") != "durable_secret_checked"
        or any(record["outcome"] != "in_progress" for record in records[:-1])
    ):
        _deny("history_query_capture_incomplete")
    return records


def _family(stream):
    if stream in capture._FILL_STREAMS:
        return "fills"
    if stream in capture._BILL_STREAMS:
        return "bills"
    if stream in capture._ORDER_HISTORY_STREAMS:
        return "orders_history"
    return stream


def _lineages(observations):
    seen, result = {}, []
    for request_index, observation in enumerate(observations):
        rows = []
        for ordinal, row in enumerate(observation.rows):
            key = (_family(observation.request.stream), row.instrument_id, row.row_id)
            row_sha = journal.digest(row.canonical_json.encode("utf-8"))
            prior = seen.get(key)
            if prior is not None and prior[2] != row_sha:
                _deny("history_query_overlap_conflict")
            rows.append(
                {
                    "ordinal": ordinal,
                    "row_identity_sha256": journal.digest(journal.canonical(key)),
                    "row_sha256": row_sha,
                    "overlap": "first_observation"
                    if prior is None
                    else "matching_overlap",
                    "prior_request_index": None if prior is None else prior[0],
                    "prior_row_ordinal": None if prior is None else prior[1],
                }
            )
            seen.setdefault(key, (request_index, ordinal, row_sha))
        result.append(rows)
    return result


def _page_metadata(record, observation, index):
    data = record["data"]
    query = {"after": observation.after, "query": list(observation.request.parameters)}
    required = {
        "request_index": index,
        "stream": observation.request.stream,
        "page_index": observation.page_index,
        "after_sha256": None
        if observation.after is None
        else journal.digest(observation.after.encode("utf-8")),
        "previous_page_sha256": observation.previous_page_sha256,
        "identity_receipt_sha256": observation.identity_receipt_sha256,
        "endpoint": observation.request.endpoint,
        "query_sha256": journal.digest(journal.canonical(query)),
        "request_started_at": observation.request_started_at.isoformat(),
        "buffer_complete": True,
        "clock_order": "observed",
    }
    if any(
        type(data.get(key)) is not type(value) or data[key] != value
        for key, value in required.items()
    ):
        _deny("history_query_page_join_mismatch")
    count = data.get("observed_bytes")
    if type(count) is not int or not 0 <= count <= len(observation.response_body):
        _deny("history_query_prefix_invalid")
    if data.get("prefix_sha256") != journal.digest(observation.response_body[:count]):
        _deny("history_query_prefix_invalid")
    if record["kind"] != "request_start" and (
        data.get("headers_received_at") != observation.headers_received_at.isoformat()
        or type(data.get("http_status")) is not int
        or data["http_status"] != 200
    ):
        _deny("history_query_headers_mismatch")
    return data, query


def _joined_pages(records, chain, packet):
    observations, position = packet.observations, 1
    lineages = _lineages(observations)
    completion_metadata = []
    for index, observation in enumerate(observations):
        expected, prefix, source_time = (
            "request_start",
            0,
            observation.request_started_at,
        )
        eof = None
        while position < len(records) and records[position]["kind"] in _PAGE_KINDS:
            record = records[position]
            kind = record["kind"]
            if expected == "done":
                break
            if kind != expected and not (
                expected == "body_progress" and kind == "body_complete"
            ):
                _deny("history_query_stage_order_invalid")
            data, _ = _page_metadata(record, observation, index)
            stamp = _time(record["observed_at"])
            if (
                stamp < source_time
                or data["observed_bytes"] < prefix
                or data.get("source_observed_at") != stamp.isoformat()
            ):
                _deny("history_query_clock_or_prefix_reversed")
            if kind in {"request_start", "headers_received", "page_validated"}:
                previous_source = (
                    None
                    if kind == "request_start" and index == 0
                    else observations[index - 1].body_completed_at
                    if kind == "request_start"
                    else observation.request_started_at
                    if kind == "headers_received"
                    else eof
                )
                if data.get("previous_source_observed_at") != (
                    None if previous_source is None else previous_source.isoformat()
                ):
                    _deny("history_query_source_clock_join_mismatch")
            elif (
                not source_time
                <= _time(data.get("previous_source_observed_at"))
                <= stamp
            ):
                # B1 may suppress intermediate chunk progress events. Preserve
                # their measured interval instead of inventing an exact sample.
                _deny("history_query_source_clock_join_mismatch")
            source_time, prefix = stamp, data["observed_bytes"]
            if kind == "request_start":
                if stamp != observation.request_started_at or prefix != 0:
                    _deny("history_query_request_mismatch")
                expected = "headers_received"
            elif kind == "headers_received":
                if stamp != observation.headers_received_at or prefix != 0:
                    _deny("history_query_headers_mismatch")
                expected = "body_progress"
            elif kind == "body_complete":
                eof = _time(data.get("body_completed_at"))
                if (
                    stamp != eof
                    or eof > observation.body_completed_at
                    or data.get("body_exhaustion_observed") is not True
                    or prefix != len(observation.response_body)
                ):
                    _deny("history_query_body_incomplete")
                expected = "page_validated"
            elif kind == "page_validated":
                if (
                    stamp != observation.body_completed_at
                    or data.get("body_exhaustion_observed") is not True
                    or data.get("body_completed_at") != eof.isoformat()
                    or data.get("receipt_sha256") != observation.receipt_sha256
                    or not _same_json(data.get("rows"), lineages[index])
                ):
                    _deny("history_query_validated_join_mismatch")
                completion_metadata.append(data)
                expected = "done"
            position += 1
        if expected != "done" or eof is None:
            _deny("history_query_page_incomplete")
    raw_sequences = []
    for index, observation in enumerate(observations):
        record, item = records[position], chain[position]
        if record["kind"] != "raw_finalized":
            _deny("history_query_raw_missing")
        data, query = _page_metadata(record, observation, index)
        validated = completion_metadata[index]
        if (
            item.event.raw_body != observation.response_body
            or data.get("raw_retention") != "durable_secret_checked"
            or data.get("query_retention") != "durable_secret_checked"
            or data.get("source_state") != "complete_page"
            or data.get("terminal_secret_set_closed") is not True
            or not _same_json(
                data.get("retained_bytes"), len(observation.response_body)
            )
            or data.get("observed_bytes") != len(observation.response_body)
            or data.get("receipt_sha256") != observation.receipt_sha256
            or not _same_json(data.get("rows"), lineages[index])
            or any(
                not _same_json(data.get(key), validated.get(key))
                for key in (
                    "body_completed_at",
                    "body_exhaustion_observed",
                    "source_observed_at",
                    "previous_source_observed_at",
                    "tls_provenance",
                    "tls_certificate_sha256",
                )
            )
            or data.get("after") != query["after"]
            or data.get("query") != [list(pair) for pair in query["query"]]
            or _time(record["observed_at"]) < observation.body_completed_at
        ):
            _deny("history_query_raw_join_mismatch")
        raw_sequences.append(record["sequence"])
        position += 1
    if position + 3 != len(records):
        _deny("history_query_finalization_order_invalid")
    closed, saved, terminal = records[position:]
    if (
        closed["kind"] != "acquisition_closed"
        or closed["data"].get("source_state") != "complete_requested_chain"
        or closed["data"].get("all_observed_buffers_secret_checked") is not True
        or closed["data"].get("source_clock_order_valid") is not True
        or closed["data"].get("signatures_closed") is not True
        or not _same_json(closed["data"].get("requested_pages"), len(observations))
        or saved["kind"] != "packet_recorded"
        or saved["data"].get("source_state") != "complete_requested_chain"
        or not _same_json(terminal["data"].get("observed_pages"), len(observations))
    ):
        _deny("history_query_finalization_incomplete")
    return raw_sequences


def _union(intervals):
    """Only derived time intervals are sorted; source rows/pages stay untouched."""
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _coverage(packet, raw_sequences):
    groups, rows = {}, {}
    for index, observation in enumerate(packet.observations):
        stream = observation.request.stream
        family = capture.stream_family(stream)
        if family not in _HISTORY_FAMILIES:
            continue
        groups.setdefault(stream, []).append(observation)
        for ordinal, row in enumerate(observation.rows):
            raw = json.loads(row.canonical_json)
            key = journal.digest(journal.canonical((_family(stream), row.row_id)))
            row_sha = journal.digest(row.canonical_json.encode("utf-8"))
            locator = {
                "request_index": index,
                "row_ordinal": ordinal,
                "raw_event_sequence": raw_sequences[index],
                "page_receipt_sha256": observation.receipt_sha256,
                "raw_sha256": journal.digest(observation.response_body),
                "stream": stream,
            }
            if key in rows:
                if rows[key]["row_sha256"] != row_sha:
                    _deny("history_query_overlap_conflict")
                rows[key]["locators"].append(locator)
            else:
                # These source times are diagnostic facts, not an accrual claim.
                times = {item.path: item.value for item in row.source_times}
                stamp = times.get("ts")
                if stamp is None:
                    _deny("history_query_generation_time_missing")
                rows[key] = {
                    "family": _family(stream),
                    "product": raw.get("instType"),
                    "identity_sha256": key,
                    "row_sha256": row_sha,
                    "generation_at": stamp.isoformat(),
                    "fill_at": None
                    if times.get("fillTime") is None
                    else times["fillTime"].isoformat(),
                    "locators": [locator],
                }
    queries = []
    intervals = {}
    products = (
        capture.INSTRUMENT_TYPES
        if capture.is_all_product_plan(packet.plan)
        else ("SWAP",)
    )
    for stream, pages in groups.items():
        family = capture.stream_family(stream)
        days = 3 if family == "fills_recent" else 7 if family == "bills_recent" else 28
        retained_from = pages[-1].body_completed_at - timedelta(days=days)
        start = max(packet.plan.history_start, retained_from)
        end = packet.plan.history_end
        product = dict(pages[0].request.parameters).get("instType", "*")
        domain = "fills" if family.startswith("fills") else "bills"
        covered = [] if start > end else [(start, end)]
        for target in products if domain == "fills" and product == "*" else (product,):
            intervals.setdefault((domain, target), []).extend(covered)
        queries.append(
            {
                "stream": stream,
                "endpoint": pages[0].request.endpoint,
                "product": product,
                "cursor": "billId",
                "time_domain": "source_record_generation_ts",
                "supported_days": days,
                "retained_from": retained_from.isoformat(),
                "covered_intervals": [
                    [a.isoformat(), b.isoformat()] for a, b in covered
                ],
                "page_receipts": [page.receipt_sha256 for page in pages],
                "terminal_receipt": pages[-1].receipt_sha256,
                "row_count": sum(len(page.rows) for page in pages),
            }
        )
    coverage = []
    for family, product in (
        *(("fills", product) for product in products),
        ("bills", "*"),
    ):
        merged = _union(intervals.get((family, product), ()))
        covers = any(
            a <= packet.plan.history_start and b >= packet.plan.history_end
            for a, b in merged
        )
        coverage.append(
            {
                "family": family,
                "product": product,
                "covered_intervals": [
                    [a.isoformat(), b.isoformat()] for a, b in merged
                ],
                "requested_generation_window_covered": covers,
            }
        )
    return queries, coverage, list(rows.values())


def verify_history_query_chain(
    chain,
    *,
    expected_head_sha256,
    expected_plan_sha256,
    expected_packet_sha256,
    expected_account_id,
    expected_settlement_currency,
    expected_policy_sha256=POLICY_SHA256,
):
    """Recompute a private audit result from exact B1 readbacks, not claim DTOs."""
    try:
        return _verify(
            chain,
            expected_head_sha256,
            expected_plan_sha256,
            expected_packet_sha256,
            expected_account_id,
            expected_settlement_currency,
            expected_policy_sha256,
        )
    except HistoryQueryVerificationError:
        raise
    except Exception:  # noqa: BLE001, S110 -- no private parser exception logging
        pass
    raise HistoryQueryVerificationError("history_query_verification_invalid")


def _verify(chain, head, plan_pin, packet_pin, account_id, currency, policy_pin):
    if type(policy_pin) is not str or policy_pin != POLICY_SHA256:
        _deny("history_query_policy_mismatch")
    if (
        type(account_id) is not str
        or capture._ID.fullmatch(account_id) is None
        or type(currency) is not str
        or re.fullmatch(r"[A-Z0-9]{1,20}", currency) is None
    ):
        _deny("history_query_scope_unsupported")
    records = _chain(chain, head)
    saved = [
        item.event.packet_payload
        for item in chain
        if item.event.packet_payload is not None
    ]
    if len(saved) != 1:
        _deny("history_query_original_packet_required")
    packet = capture.verify_demo_account_packet(
        saved[0], expected_sha256=packet_pin, expected_plan_sha256=plan_pin
    )
    plan = packet.plan
    if (
        type(plan)
        not in {
            capture.RegionalDemoAccountCapturePlan,
            capture.AllProductDemoAccountCapturePlan,
        }
        or plan.registration_region != "global"
        or plan.expected_uid != account_id
        or plan.settlement_currency != currency
    ):
        _deny("history_query_scope_unsupported")
    if packet.completed_at - plan.history_start > timedelta(days=28):
        _deny("history_query_lookback_unsupported")
    initial = records[0]["data"]
    if (
        initial.get("environment") != "demo"
        or initial.get("account_id") != account_id
        or initial.get("settlement_currency") != currency
        or initial.get("plan_sha256") != plan_pin
        or not _same_json(initial.get("plan"), capture._json_value(plan))
        or initial.get("session_binding_sha256")
        != journal.digest(plan.session_binding_id.encode("ascii"))
        or initial.get("requested_streams") != list(capture.streams_for_plan(plan))
    ):
        _deny("history_query_capture_binding_mismatch")
    journal._sha(initial.get("local_checkpoint_sha256"))
    raw_sequences = _joined_pages(records, chain, packet)
    if (
        records[-2]["data"].get("plan_sha256") != plan_pin
        or records[-2]["data"].get("local_checkpoint_sha256")
        != initial["local_checkpoint_sha256"]
    ):
        _deny("history_query_checkpoint_mismatch")
    queries, coverage, rows = _coverage(packet, raw_sequences)
    value = {
        "schema_version": "ctcc.observed_account_history_query.v1",
        "policy_sha256": POLICY_SHA256,
        "scope_sha256": journal.digest(
            journal.canonical(["demo", account_id, currency])
        ),
        "capture_id": records[0]["capture_id"],
        "journal_head_sha256": head,
        "plan_sha256": plan_pin,
        "packet_sha256": packet_pin,
        "requested_start": plan.history_start.isoformat(),
        "requested_end": plan.history_end.isoformat(),
        "observed_completed_at": packet.completed_at.isoformat(),
        "state": "retained_query_chain_verified",
        "queries": queries,
        "coverage": coverage,
        "rows": rows,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
        "unverified": [
            "execution_time_loss_window",
            "history_seed",
            "funding_accrual",
            "measured_high_water_mark",
            "order_history_retention",
            "future_late_arrival_finality",
            "complete_product_and_liability_scope",
            "current_owned_session_authority",
        ],
    }
    return HistoryQueryVerification(journal.canonical(value))


def compare_history_query_chains(
    *, previous_chain, previous_pins, current_chain, current_pins
):
    """Reverify both inputs; preserve late/conflicting discoveries as audit evidence.

    Pins are exact verifier keyword dictionaries, never coverage/authority flags.
    This comparison neither rewrites nor upgrades either immutable observation.
    """
    try:
        if any(
            type(pins) is not dict
            or any(
                type(key) is not str or type(value) is not str
                for key, value in pins.items()
            )
            for pins in (previous_pins, current_pins)
        ):
            _deny("history_query_comparison_invalid")
        old = json.loads(
            verify_history_query_chain(previous_chain, **previous_pins).receipt_json
        )
        new = json.loads(
            verify_history_query_chain(current_chain, **current_pins).receipt_json
        )
        if (
            old["scope_sha256"] != new["scope_sha256"]
            or old["policy_sha256"] != new["policy_sha256"]
        ):
            _deny("history_query_comparison_scope_mismatch")
        if _time(new["observed_completed_at"]) < _time(old["observed_completed_at"]):
            _deny("history_query_comparison_clock_reversed")
        prior = {row["identity_sha256"]: row for row in old["rows"]}
        current = {row["identity_sha256"]: row for row in new["rows"]}
        previous_domains = {
            (item["family"], item["product"]) for item in old["coverage"]
        }
        current_domains = {
            (item["family"], item["product"]) for item in new["coverage"]
        }
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
                at = _time(row["generation_at"])
                covered_before = any(
                    query["family"] == row["family"]
                    and query["product"] in {"*", row["product"]}
                    and any(
                        _time(a) <= at <= _time(b)
                        for a, b in query["covered_intervals"]
                    )
                    for query in old["coverage"]
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
            at = _time(row["generation_at"])
            if any(
                query["family"] == row["family"]
                and query["product"] in {"*", row["product"]}
                and any(
                    _time(a) <= at <= _time(b) for a, b in query["covered_intervals"]
                )
                for query in new["coverage"]
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
    except HistoryQueryVerificationError:
        raise
    except Exception:  # noqa: BLE001, S110 -- no private audit exception logging
        pass
    raise HistoryQueryVerificationError("history_query_comparison_invalid")
