"""HELD bounded global original-row accumulator; no account/risk authority.

The native stream hook is NOT installed by this proposal. Its intended seam is
after original_stream._original_receipt has verified CURRENT and every witness.
This core independently replays the entire single CURRENT membership: callers
cannot supply filtered rows, totals, a packet substitute, or a passed flag.
Original B3/witness/checkpoint/native ownership acceptance remains external.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.database.repositories.account_observation_index import (
    ObservationBatchReadback,
    _membership,
)
from app.domain import account_history_partition_v1 as partition
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_financial_operands_v2 as financial
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_history_risk_v8 as risk
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_COMPACT_BYTES = 8 * 1024 * 1024
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.global_original_financial_facts.policy.v1",
        "observed_source_policy_sha256": observed.POLICY_SHA256,
        "lifecycle_policy_sha256": lifecycle.POLICY_SHA256,
        "risk_arithmetic_policy_sha256": risk.POLICY_SHA256,
        "financial_operands_policy_sha256": financial.POLICY_SHA256,
        "source": "every_original_CURRENT_from_genesis_no_witness_booking",
        "deduplication": "identity_requires_equal_original_raw_and_row_digest",
        "fill_order": "new_late_or_equal_execution_time_denied_no_timestamp_rewrite",
        "canonical_state_plus_output_max_bytes": MAX_COMPACT_BYTES,
        "position_total_reuse": "denied",
        "original_stream_prefix": "separate_real_stream_join_required",
        "original_receipt": "real_CURRENT_and_exact_original_witness_query_replay",
        "coverage": "all_CURRENT_interval_union_with_last_original_cutoff_and_completion",
        "transient_obligations": "recomputed_for_arithmetic_original_blockers_retained",
        "aggregate_window": "last_original_B3_window_bound_by_expected_final_head",
        "supported_rolling_order": "nondecreasing_original_window_endpoints_cutoff_and_completion",
        "native_profile": "not_inferred_by_inert_replay_or_plan_identifier",
        "funding_windows_seed_hwm_finality_and_native_owner": "unknown",
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


def _component_policies():
    return {
        "observed_source": observed.POLICY_SHA256,
        "lifecycle": lifecycle.POLICY_SHA256,
        "risk_arithmetic": risk.POLICY_SHA256,
        "financial_operands": financial.POLICY_SHA256,
    }


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class GlobalFinancialHistoryError(ValueError):
    """Fixed source-safe codes; never source exception text."""


def _deny(code):
    raise GlobalFinancialHistoryError(code) from None


def _wire(value):
    if type(value) is datetime:
        if value.tzinfo is not UTC:
            _deny("global_financial_UTC_required")
        return value.isoformat()
    if type(value) is dict:
        return {key: _wire(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_wire(item) for item in value]
    return value


def _canonical_size(value):
    return len(journal.canonical(_wire(value)))


def _bounded(value, extra=b""):
    # This measures canonical retained state, not Python allocator/RSS usage.
    if _canonical_size(value) + len(extra) > MAX_COMPACT_BYTES:
        _deny("global_financial_compact_byte_bound")


_TRANSIENT_COVERAGE = frozenset(
    {
        "generation_window_gap",
        "generation_observation_tail_below_policy",
        "generation_query_cutoff_stale",
    }
)


def _joint_receipt(chain, reference, batch, document, anchor, scope, index, witnesses):
    """Reopen original query/packet bytes; never claim native source ownership."""
    expected_witnesses = anchor["verified_witnesses"]
    if (
        type(witnesses) is not tuple
        or len(witnesses) >= observed.MAX_CAPTURES
        or len(witnesses) != len(expected_witnesses)
    ):
        _deny("global_financial_original_witness_required")
    prior, last = [], 0
    for witness, expected in zip(witnesses, expected_witnesses, strict=True):
        if type(witness) is not stream.OriginalHistoryWitnessV1:
            _deny("global_financial_original_witness_required")
        number = witness.sequence
        if type(number) is not int or not last < number < document["sequence"]:
            _deny("global_financial_original_witness_changed")
        old = index.get(number)
        expected_pin = (
            None
            if old is None
            else {
                "sequence": number,
                "event_sha256": old["event_sha256"],
                "source_membership_sha256": old["source_membership_sha256"],
                "coverage_membership_sha256": old["coverage_membership_sha256"],
            }
        )
        if old is None or journal.canonical(expected) != journal.canonical(
            expected_pin
        ):
            _deny("global_financial_original_witness_changed")
        actual = observed.source_reference(witness.chain)
        if (
            observed.reference_document(actual) != old["reference"]
            or stream._chain_measure(witness.chain)[1] != old["chain_sha256"]
        ):
            _deny("global_financial_original_witness_changed")
        prior.append((witness.chain, actual))
        last = number
    replayed = lifecycle._json(
        observed._replay(
            (*prior, (chain, reference)),
            scope,
            observed.ExecutionWindow(
                *(
                    datetime.fromisoformat(document["window"][name])
                    for name in ("started_at", "ended_at")
                )
            ),
            observed.POLICY_SHA256,
            adjacent=False,
        ).receipt_json
    )
    receipt = lifecycle._json(batch.replay.receipt_json)
    for name in (
        "captures",
        "source_index",
        "coverage",
        "cashflows",
        "generation_observation_cutoff",
        "observed_completed_at",
    ):
        if journal.canonical(receipt.get(name)) != journal.canonical(replayed[name]):
            _deny("global_financial_original_joint_receipt_changed")
    findings = receipt["findings"]
    if journal.canonical(findings[: len(replayed["findings"])]) != journal.canonical(
        replayed["findings"]
    ) or not set(stream._blocker_codes(replayed["blocking_reasons"])).issubset(
        stream._blocker_codes(receipt["blocking_reasons"])
    ):
        _deny("global_financial_original_joint_receipt_changed")


def _aggregate_coverage(state):
    """Apply the existing observed replay coverage/tail/stale formulas once."""
    window, latest = state["window"], state["latest_observation"]
    if type(window) is not dict or type(latest) is not dict:
        _deny("global_financial_coverage_source_required")
    start = observed.query._time(window["started_at"])
    end = observed.query._time(window["ended_at"])
    cutoff = observed.query._time(latest["generation_observation_cutoff"])
    completed = observed.query._time(latest["observed_completed_at"])
    blockers, coverages = set(), []
    if end > cutoff - timedelta(seconds=300):
        blockers.add("generation_observation_tail_below_policy")
    if completed - cutoff > timedelta(seconds=120):
        blockers.add("generation_query_cutoff_stale")
    intervals_by_domain = {}
    for item in state["coverage"]:
        key = (item["family"], item["product"])
        intervals_by_domain.setdefault(key, []).append(
            (
                observed.query._time(item["started_at"]),
                observed.query._time(item["ended_at"]),
            )
        )
    for family, product in latest["domains"]:
        intervals = observed.query._union(
            intervals_by_domain.get((family, product), [])
        )
        covered = any(left <= start and right >= cutoff for left, right in intervals)
        if not covered:
            blockers.add("generation_window_gap")
        coverages.append(
            {
                "family": family,
                "product": product,
                "generation_window_covered": covered,
                "intervals": [
                    [left.isoformat(), right.isoformat()] for left, right in intervals
                ],
            }
        )
    return blockers, coverages


def _cancelled():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None and task.cancelling():
        _deny("global_financial_cancelled")


def _position_totals_once(runs):
    used = [
        run["position_history_row"]["row_sha256"]
        for run in runs
        if run["net_outcome_arithmetic_reconciled"]
    ]
    if len(used) != len(set(used)):
        _deny("global_financial_position_total_reused")
    return used


def _position_rows_with_locators(chain, packet, reference):
    raw_events = {}
    for point in chain:
        record = journal.checked_event(point.event)
        if record["kind"] == "raw_finalized":
            index = record["data"].get("request_index")
            if type(index) is not int or index in raw_events:
                _deny("global_financial_position_locator_invalid")
            raw_events[index] = record["sequence"]
    rows = risk._position_observation_rows([packet])
    locators = {}
    for index, page in enumerate(packet.observations):
        if page.request.stream != "positions":
            continue
        if index not in raw_events:
            _deny("global_financial_position_locator_invalid")
        for ordinal, row in enumerate(page.rows):
            key = (journal.digest(row.canonical_json.encode()), page.receipt_sha256)
            locators.setdefault(key, []).append(
                {
                    "capture_id": reference.capture_id,
                    "request_index": index,
                    "row_ordinal": ordinal,
                    "raw_event_sequence": raw_events[index],
                    "page_receipt_sha256": page.receipt_sha256,
                    "raw_sha256": journal.digest(page.response_body),
                    "stream": page.request.stream,
                }
            )
    for item in rows:
        item["locators"] = locators[(item["row_sha256"], item["page_receipt_sha256"])]
    return rows


@dataclass(frozen=True, slots=True, repr=False)
class GlobalFinancialHistoryFactsV1:
    receipt_json: bytes

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


class OriginalHistoryFinancialAccumulatorV1:
    """One genesis-to-pinned-head accumulation; failure destroys all state.

    Only CURRENT is ingested. Witnesses inside batch.replay are never booked;
    their original validation remains the unchanged stream's responsibility.
    No intermediate receipt or partial/failing state is exposed.
    """

    def __init__(self, *, scope, expected_policy_sha256=POLICY_SHA256):
        checked_bootstrap(scope, LedgerScope)
        if (
            type(expected_policy_sha256) is not str
            or expected_policy_sha256 != POLICY_SHA256
        ):
            _deny("global_financial_policy_changed")
        self._scope = LedgerScope(
            environment=scope.environment,
            account_id=scope.account_id,
            settlement_currency=scope.settlement_currency,
        )
        self._scope_sha256 = journal.digest(
            journal.canonical(
                [
                    self._scope.environment,
                    self._scope.account_id,
                    self._scope.settlement_currency,
                ]
            )
        )
        self._policy_sha256, self._components, self._task = (
            POLICY_SHA256,
            _component_policies(),
            _task(),
        )
        self._known, self._captures, self._source_index = {}, set(), {}
        self._state = {
            "sequence": 0,
            "previous": None,
            "anchor": None,
            "counts": [0, 0, 0],
            "identity": None,
            "orders": {},
            "specs": {},
            "anchors": [],
            "accounts": [],
            "positions": [],
            "members": [],
            "sources": [],
            "blockers": [],
            "latest_fill": {},
            "available_at": None,
            "coverage": [],
            "window": None,
            "latest_observation": None,
        }
        self._state_bytes = _canonical_size(self._state)
        _bounded(self._state)

    def cancel(self):
        self._state = None
        known, captures = (
            getattr(self, "_known", None),
            getattr(self, "_captures", None),
        )
        if type(known) is dict:
            known.clear()
        if type(captures) is set:
            captures.clear()
        index = getattr(self, "_source_index", None)
        if type(index) is dict:
            index.clear()
        self._state_bytes = 0

    def _open(self):
        if self._state is None:
            _deny("global_financial_closed_or_failed")
        _cancelled()
        current = _task()
        if self._task is not None and current is not self._task:
            _deny("global_financial_task_changed")
        if self._task is None:
            self._task = current
        checked_bootstrap(self._scope, LedgerScope)
        actual_scope = journal.digest(
            journal.canonical(
                [
                    self._scope.environment,
                    self._scope.account_id,
                    self._scope.settlement_currency,
                ]
            )
        )
        if (
            actual_scope != self._scope_sha256
            or self._policy_sha256 != POLICY_SHA256
            or self._components != _component_policies()
        ):
            _deny("global_financial_scope_or_policy_changed")
        self._charge(0)

    def _charge(self, delta):
        # Only the logical canonical retained state is counted, not Python RSS.
        total = self._state_bytes + delta
        if total < 0 or total > MAX_COMPACT_BYTES:
            _deny("global_financial_compact_byte_bound")
        self._state_bytes = total

    def _replace(self, name, value):
        self._charge(_canonical_size(value) - _canonical_size(self._state[name]))
        self._state[name] = value

    def _append(self, name, values):
        old = self._state[name]
        if values:
            self._charge(
                sum(_canonical_size(value) for value in values)
                + len(values)
                - (0 if old else 1)
            )
            old.extend(values)

    def _extend_map(self, name, key, values):
        mapping = self._state[name]
        if key in mapping:
            old = mapping[key]
            if values:
                self._charge(
                    sum(_canonical_size(value) for value in values)
                    + len(values)
                    - (0 if old else 1)
                )
                old.extend(values)
        else:
            self._charge(
                _canonical_size(key)
                + 1
                + _canonical_size(values)
                + (1 if mapping else 0)
            )
            mapping[key] = list(values)

    def _set_map(self, name, key, value):
        mapping = self._state[name]
        delta = (
            _canonical_size(value) - _canonical_size(mapping[key])
            if key in mapping
            else _canonical_size(key)
            + 1
            + _canonical_size(value)
            + (1 if mapping else 0)
        )
        self._charge(delta)
        mapping[key] = value

    def consume_current(self, *, chain, reference, batch, witnesses=()):
        """Reopen exact original CURRENT, with its whole B3 membership pin.

        No caller-supplied raw row, membership list, balance or complete flag is
        accepted. Single-source replay retains the original cap32 unchanged.
        """
        try:
            self._open()
            if (
                type(batch) is not ObservationBatchReadback
                or type(batch.replay) is not observed.ObservationReplay
                or type(batch.event_sha256) is not str
                or type(batch.document_json) is not bytes
                or type(batch.replay.receipt_json) is not bytes
            ):
                _deny("global_financial_original_batch_required")
            # No rollback copy: every failure/cancellation destroys the accumulator.
            state = self._state
            journal._sha(batch.event_sha256)
            sequence = state["sequence"] + 1
            scope_doc = [
                self._scope.environment,
                self._scope.account_id,
                self._scope.settlement_currency,
            ]
            document, anchor = partition._b3(
                batch.document_json,
                scope=scope_doc,
                source_policy=observed.POLICY_SHA256,
            )
            if (
                type(batch.sequence) is not int
                or batch.sequence != sequence
                or document["sequence"] != sequence
                or batch.event_sha256 != journal.digest(batch.document_json)
                or document["previous_sha256"] != state["previous"]
                or anchor["previous_anchor_sha256"] != state["anchor"]
                or document["receipt_sha256"] != batch.replay.receipt_sha256
                or document["reference"] != observed.reference_document(reference)
            ):
                _deny("global_financial_original_chain_changed")
            if reference.capture_id in self._captures:
                _deny("global_financial_duplicate_current")
            window = observed.ExecutionWindow(
                *(
                    datetime.fromisoformat(document["window"][name])
                    for name in ("started_at", "ended_at")
                )
            )
            if state["window"] is not None and any(
                observed.query._time(document["window"][name])
                < observed.query._time(state["window"][name])
                for name in ("started_at", "ended_at")
            ):
                _deny("global_financial_window_regressed")
            current = observed.replay_observation_index(
                ((chain, reference),), scope=self._scope, window=window
            )
            facts, coverage = _membership(current)
            for name, values in (("source", facts), ("coverage", coverage)):
                if document[name + "_membership_count"] != len(values) or document[
                    name + "_membership_sha256"
                ] != journal.digest(journal.canonical(values)):
                    _deny("global_financial_whole_current_membership_changed")
            receipt = lifecycle._json(batch.replay.receipt_json)
            current_data = lifecycle._json(current.receipt_json)
            original_blockers = stream._blocker_codes(receipt.get("blocking_reasons"))
            current_blockers = stream._blocker_codes(
                current_data.get("blocking_reasons")
            )
            findings = receipt.get("findings")
            if (
                receipt.get("account_complete") is not False
                or receipt.get("execution_authority") is not False
                or receipt.get("admission") != "DENY"
                or receipt.get("scope_sha256") != document["scope_sha256"]
                or receipt.get("policy_sha256") != observed.POLICY_SHA256
                or receipt.get("window") != document["window"]
                or journal.canonical(receipt.get("continuation_anchor")).decode()
                != document["anchor_json"]
                or type(findings) is not list
                or document["finding_membership_count"] != len(findings)
                or document["finding_membership_sha256"]
                != journal.digest(journal.canonical(findings))
            ):
                _deny("global_financial_original_receipt_changed")
            if state["latest_observation"] is not None and any(
                observed.query._time(current_data[name])
                < observed.query._time(state["latest_observation"][name])
                for name in ("generation_observation_cutoff", "observed_completed_at")
            ):
                _deny("global_financial_source_clock_regressed")
            _joint_receipt(
                chain,
                reference,
                batch,
                document,
                anchor,
                self._scope,
                self._source_index,
                witnesses,
            )
            self._replace("window", document["window"])
            self._append("coverage", coverage)
            self._replace(
                "latest_observation",
                {
                    "generation_observation_cutoff": current_data[
                        "generation_observation_cutoff"
                    ],
                    "observed_completed_at": current_data["observed_completed_at"],
                    "domains": [
                        [item["family"], item["product"]]
                        for item in current_data["coverage"]
                    ],
                },
            )
            counts = [
                old + document[name + "_membership_count"]
                for old, name in zip(
                    state["counts"], ("source", "coverage", "finding"), strict=True
                )
            ]
            if counts != [anchor[name] for name in partition._COUNTS]:
                _deny("global_financial_cumulative_membership_changed")
            payloads = [
                point.event.packet_payload
                for point in chain
                if point.event.packet_payload
            ]
            if len(payloads) != 1:
                _deny("global_financial_original_packet_required")
            packet = capture._verify_original_page_packet(
                payloads[0],
                expected_sha256=reference.packet_sha256,
                expected_plan_sha256=reference.plan_sha256,
            )
            reopened = lifecycle._source_rows(facts, [packet], ((chain, reference),))
            blockers = (
                set(state["blockers"]) | set(original_blockers) | set(current_blockers)
            )
            if receipt.get("findings") or anchor["required_proof_budget_exceeded"]:
                blockers.add("history_persistent_index_invalidation")
            identity, orders, specs, anchors = lifecycle._lifecycle_context(
                [packet], ((chain, reference),), self._scope, blockers
            )
            if state["identity"] is not None and tuple(state["identity"]) != identity:
                _deny("global_financial_account_session_or_mode_changed")
            self._replace("identity", identity)
            for destination, incoming in (("orders", orders), ("specs", specs)):
                for key, values in incoming.items():
                    self._extend_map(destination, key, values)
            self._append("anchors", anchors)
            self._append(
                "accounts",
                financial._native_account_observations([packet], [reference]),
            )
            self._append(
                "positions", _position_rows_with_locators(chain, packet, reference)
            )
            known = self._known
            new_times = {}
            for member in reopened:
                prior = known.get(member["identity_sha256"])
                if prior is not None:
                    if (
                        prior["row_sha256"] != member["row_sha256"]
                        or prior["raw"] != member["raw"]
                    ):
                        _deny("global_financial_conflicting_row_identity")
                    before_size = _canonical_size(prior)
                    for key in ("locators", "metadata_observations"):
                        if key in member:
                            retained = prior.setdefault(key, [])
                            for item in member[key]:
                                if item not in retained:
                                    retained.append(item)
                    prior["latest_observed_at"] = member["latest_observed_at"]
                    self._charge(_canonical_size(prior) - before_size)
                else:
                    if member["family"] == "fills":
                        at = lifecycle._utc(datetime.fromisoformat(member["fill_at"]))
                        raw = member["raw"]
                        key = journal.canonical(
                            [raw.get("instId"), raw.get("posSide")]
                        ).decode()
                        if (
                            key in state["latest_fill"]
                            and at <= state["latest_fill"][key]
                        ):
                            _deny("global_financial_late_or_equal_time_fill")
                        if at in new_times.setdefault(key, set()):
                            _deny("global_financial_equal_time_fill")
                        new_times[key].add(at)
                    self._append("members", [member])
                    known[member["identity_sha256"]] = member
            for key, values in new_times.items():
                self._set_map("latest_fill", key, max(values))
            self._append(
                "sources",
                [
                    {
                        "sequence": sequence,
                        "window": document["window"],
                        "reference": observed.reference_document(reference),
                        "event_sha256": batch.event_sha256,
                        "chain_sha256": stream._chain_measure(chain)[1],
                        "source_membership_sha256": document[
                            "source_membership_sha256"
                        ],
                        "coverage_membership_sha256": document[
                            "coverage_membership_sha256"
                        ],
                    }
                ],
            )
            self._source_index[sequence] = state["sources"][-1]
            self._captures.add(reference.capture_id)
            self._replace(
                "available_at",
                max(
                    filter(
                        None,
                        (
                            state["available_at"],
                            packet.completed_at,
                            *(point.readback_at for point in chain),
                        ),
                    )
                ),
            )
            for name, value in (
                ("sequence", sequence),
                ("previous", batch.event_sha256),
                ("anchor", document["anchor_sha256"]),
                ("counts", counts),
                ("blockers", sorted(blockers)),
            ):
                self._replace(name, value)
            if self._state is not state:
                _deny("global_financial_closed_or_failed")
            _cancelled()
        except BaseException as error:
            self.cancel()
            if type(error) is GlobalFinancialHistoryError:
                raise
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            _deny("global_financial_original_source_invalid")

    def finish(
        self,
        *,
        expected_through,
        expected_head,
        checked_at,
        position_source=None,
        expected_policy_sha256=POLICY_SHA256,
    ):
        try:
            self._open()
            state = self._state
            if (
                type(expected_policy_sha256) is not str
                or expected_policy_sha256 != self._policy_sha256
            ):
                _deny("global_financial_policy_changed")
            if type(expected_head) is not str:
                _deny("global_financial_final_pin_or_cutoff_changed")
            journal._sha(expected_head)
            if (
                type(expected_through) is not int
                or expected_through <= 0
                or state["sequence"] != expected_through
                or state["previous"] != expected_head
                or type(checked_at) is not datetime
                or checked_at.tzinfo is not UTC
                or state["available_at"] is None
                or state["available_at"] > checked_at
            ):
                _deny("global_financial_final_pin_or_cutoff_changed")
            aggregate_blockers, aggregate_coverage = _aggregate_coverage(state)
            original_blockers = set(state["blockers"])
            blockers = (original_blockers - _TRANSIENT_COVERAGE) | aggregate_blockers
            parts = lifecycle._lifecycle_from_context(
                state["members"],
                self._scope,
                blockers,
                orders=state["orders"],
                specs=state["specs"],
                anchors=state["anchors"],
            )
            position_rows, position_gaps = [], {"native_position_source_missing"}
            position_archive = None
            if position_source is not None:
                replay, position_rows, position_gaps = (
                    financial._native_position_rows_from_accounts(
                        position_source, state["accounts"], self._scope, checked_at
                    )
                )
                position_archive = replay.archive_sha256
                for item in position_rows:
                    identifier = item["raw"].get("posId")
                    if (
                        type(identifier) is not str
                        or not identifier.isascii()
                        or not identifier.isdigit()
                        or int(identifier) <= 0
                    ):
                        _deny("global_financial_nonzero_position_identity_required")
            # One final exact walk checks the incremental accounting invariant.
            if _canonical_size(state) != self._state_bytes:
                _deny("global_financial_compact_accounting_changed")
            if self._state_bytes + _canonical_size(position_rows) > MAX_COMPACT_BYTES:
                _deny("global_financial_compact_byte_bound")
            runs = [
                risk._run_with_position_observations(
                    part,
                    state["members"],
                    state["positions"],
                    position_rows,
                    {},
                    self._scope.settlement_currency,
                )
                for part in parts
            ]
            used = _position_totals_once(runs)
            unjoined = [
                {"row_sha256": item["row_sha256"], "locator": item["locator"]}
                for item in position_rows
                if item["row_sha256"] not in used
            ]
            if unjoined:
                position_gaps = set(position_gaps) | {
                    "account_wide_position_history_rows_unjoined"
                }
            output = {
                "schema_version": "ctcc.global_original_financial_facts.v1",
                "policy_sha256": self._policy_sha256,
                "component_policy_sha256": self._components,
                "scope_sha256": self._scope_sha256,
                "checked_at": checked_at.isoformat(),
                "checked_at_semantics": "DECLARED_VALIDATION_CUTOFF_NOT_NATIVE_OWNERSHIP",
                "source_available_at": state["available_at"].isoformat(),
                "through_sequence": state["sequence"],
                "index_head_sha256": state["previous"],
                "original_current_sources": state["sources"],
                "aggregate_generation_window": state["window"],
                "aggregate_generation_observation_cutoff": state["latest_observation"][
                    "generation_observation_cutoff"
                ],
                "aggregate_generation_coverage": aggregate_coverage,
                "aggregate_generation_blocking_reasons": sorted(aggregate_blockers),
                "original_source_blocking_reasons": sorted(original_blockers),
                "retained_source_index": [
                    {key: value for key, value in item.items() if key != "raw"}
                    for item in state["members"]
                ],
                "retained_position_observations": [
                    {key: value for key, value in item.items() if key != "raw"}
                    for item in state["positions"]
                ],
                "observed_lifecycles": parts,
                "closed_runs": runs,
                "native_position_archive_sha256": position_archive,
                "unjoined_position_rows": unjoined,
                "funding_accrual_events": None,
                "daily_net_pnl": None,
                "weekly_net_pnl": None,
                "loss_streak_seed": None,
                "loss_streak": None,
                "historical_native_hwm": None,
                "current_history_tail_finality": None,
                "portfolio_revision": None,
                "original_stream_prefix_verified": False,
                "native_owner": None,
                "source_authenticity_verified": False,
                "account_revision_published": False,
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
                "missing_obligations": sorted(
                    blockers
                    | original_blockers
                    | set(financial._MISSING)
                    | position_gaps
                    | {
                        "original_stream_replay_not_bound",
                        "native_acquisition_not_owned",
                        "account_funding_accrual_attribution_unproven",
                        "future_late_arrival_finality_unproven",
                    }
                ),
            }
            raw = journal.canonical(_wire(output))
            if self._state_bytes + len(raw) > MAX_COMPACT_BYTES:
                _deny("global_financial_compact_byte_bound")
            _cancelled()
            self.cancel()
            return GlobalFinancialHistoryFactsV1(raw)
        except BaseException as error:
            self.cancel()
            if type(error) is GlobalFinancialHistoryError:
                raise
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            _deny("global_financial_finish_invalid")
