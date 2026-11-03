"""Retain exact original source rows before the existing financial fold clears.

This additive producer uses the original complete CURRENT/witness/B3 replay;
it accepts no filtered rows, totals, missing-as-zero values or complete flags.
Its values remain inert until joined to the actual native PG owner and prefix.
Raw source bytes belong in restricted custody, never logs/reports or Notion.
"""

import asyncio
from dataclasses import dataclass, field

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_history_global_financial_v2 as financial
from app.trade_qualification import (
    account_history_original_stream_v1 as original_stream,
)

MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_COMBINED_BYTES = 16 * 1024 * 1024
SCHEMA = "ctcc.original_account_raw_proof_bank.v1"
POLICY_BYTES = canonical(
    {
        "version": "ctcc.original_account_raw_proof_bank.policy.v1",
        "financial_policy_sha256": financial.POLICY_SHA256,
        "source": "whole_original_CURRENT_after_existing_B3_witness_query_replay",
        "rows": "exact_raw_members_and_original_locators_no_new_sort_dedup_or_filter",
        "population": "genesis_to_exact_original_head_not_bounded_support_subset",
        "anchor_source": "reopen_whole_original_packet_after_original_CURRENT_validation",
        "anchor_bytes": "fixed_balance_positions_risk_config_response_bodies_rows_queries_and_receipts",
        "raw_custody": "private_native_owner_only_never_public_report",
        "maximum_raw_bytes": MAX_RAW_BYTES,
        "maximum_combined_retained_and_result_bytes": MAX_COMBINED_BYTES,
        "funding_seed_peak_tail": "not_inferred_from_raw_retention",
        "account_complete": False,
        "account_revision_issued": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)


class OriginalRawProofBankError(ValueError):
    """Fixed source-safe rejection codes only."""


def _deny(code):
    raise OriginalRawProofBankError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class OriginalAccountRawProofBankValuesV1:
    raw_source_json: bytes = field(repr=False)
    raw_source_sha256: str
    financial_facts: financial.GlobalFinancialHistoryFactsV2 = field(repr=False)
    receipt_json: bytes = field(repr=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


class OriginalAccountRawProofBankAccumulatorV1:
    """One finite fold; every failure destroys both source and result state."""

    def __init__(self, *, scope):
        self._fold = financial.OriginalHistoryFinancialAccumulatorV2(scope=scope)
        self._policy_sha256 = POLICY_SHA256
        self._frozen_raw = None
        self._current_sections = []
        self._sections_bytes = 2
        self._section_pins = []

    def _open(self):
        if self._fold is None or self._policy_sha256 != POLICY_SHA256:
            _deny("original_raw_proof_bank_closed_or_changed")
        if type(self._fold) is not financial.OriginalHistoryFinancialAccumulatorV2:
            _deny("original_raw_proof_bank_original_fold_required")
        self._fold._open()
        return self._fold

    def cancel(self):
        fold, self._fold = self._fold, None
        self._frozen_raw = None
        self._current_sections.clear()
        self._sections_bytes = 2
        self._section_pins.clear()
        if type(fold) is financial.OriginalHistoryFinancialAccumulatorV2:
            fold.cancel()

    def consume_current(self, *, chain, reference, batch, witnesses=()):
        """Reopen entire original packets and all original witnesses as before."""
        try:
            fold = self._open()
            fold.consume_current(
                chain=chain, reference=reference, batch=batch, witnesses=witnesses
            )
            self._capture_verified_current_v1(
                chain=chain, reference=reference, batch=batch
            )
        except (
            BaseException
        ) as error:  # erase partial raw state before sanitized rethrow.
            self.cancel()
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            if type(error) is OriginalRawProofBankError:
                raise
            _deny("original_raw_proof_bank_consume_invalid")

    def _capture_verified_current_v1(self, *, chain, reference, batch):
        """Fixed stream hook AFTER that exact CURRENT passed the original fold.

        The entire original packet is reopened; no caller row projection is
        accepted. Keep the original balance/position/config response bytes
        required for actual equity-path anchors, not just sampled values.
        """
        fold = self._open()
        state = fold._state
        number = len(self._current_sections) + 1
        if (
            state["sequence"] != number
            or batch.sequence != number
            or not state["sources"]
            or state["sources"][-1]["reference"]
            != financial.observed.reference_document(reference)
            or state["sources"][-1]["chain_sha256"]
            != original_stream._chain_measure(chain)[1]
            or state["sources"][-1]["event_sha256"] != batch.event_sha256
        ):
            _deny("original_raw_proof_bank_original_CURRENT_changed")
        payloads = [
            point.event.packet_payload for point in chain if point.event.packet_payload
        ]
        if len(payloads) != 1:
            _deny("original_raw_proof_bank_original_packet_required")
        packet = capture._verify_original_page_packet(
            payloads[0],
            expected_sha256=reference.packet_sha256,
            expected_plan_sha256=reference.plan_sha256,
        )
        pages = []
        required = {
            "config_before",
            "config_after",
            "balance",
            "positions",
            "account_position_risk",
        }
        found = set()
        for index, page in enumerate(packet.observations):
            if page.request.stream not in required:
                continue
            found.add(page.request.stream)
            pages.append(
                {
                    "request_index": index,
                    "stream": page.request.stream,
                    "request": capture._json_value(page.request),
                    "response_body_hex": page.response_body.hex(),
                    "body_sha256": page.body_sha256,
                    "page_receipt_sha256": page.receipt_sha256,
                    "request_started_at": page.request_started_at.isoformat(),
                    "headers_received_at": page.headers_received_at.isoformat(),
                    "body_completed_at": page.body_completed_at.isoformat(),
                    "original_rows": [row.canonical_json for row in page.rows],
                }
            )
        if found != required:
            _deny("original_raw_proof_bank_current_anchor_population_missing")
        section = {
            "sequence": number,
            "source_reference": financial.observed.reference_document(reference),
            "batch_sha256": batch.event_sha256,
            "original_packet_sha256": sha(payloads[0]),
            "packet_completed_at": packet.completed_at.isoformat(),
            "pages": pages,
        }
        section_raw = canonical(section)
        section_size = len(section_raw)
        candidate_size = (
            self._sections_bytes + section_size + (1 if self._current_sections else 0)
        )
        pin_bytes = 2 + number * 66 + (number - 1)
        if (
            candidate_size > MAX_RAW_BYTES
            or fold._state_bytes + candidate_size + pin_bytes > MAX_COMBINED_BYTES
        ):
            _deny("original_raw_proof_bank_byte_bound")
        self._current_sections.append(section)
        self._section_pins.append(sha(section_raw))
        self._sections_bytes = candidate_size

    def _freeze_verified_raw_state_v1(self):
        fold = self._open()
        state = fold._state
        if (
            self._frozen_raw is not None
            or financial._canonical_size(state) != fold._state_bytes
        ):
            _deny("original_raw_proof_bank_source_changed")
        if (
            len(self._current_sections) != state["sequence"]
            or not self._current_sections
            or len(self._section_pins) != state["sequence"]
            or len(canonical(self._current_sections)) != self._sections_bytes
        ):
            _deny("original_raw_proof_bank_current_anchor_population_missing")
        for number, (section, pin, source) in enumerate(
            zip(
                self._current_sections,
                self._section_pins,
                state["sources"],
                strict=True,
            ),
            1,
        ):
            if (
                section["sequence"] != number
                or section["source_reference"] != source["reference"]
                or section["batch_sha256"] != source["event_sha256"]
                or sha(canonical(section)) != pin
            ):
                _deny("original_raw_proof_bank_source_changed")
        # This exact state exists only after the original verifier consumed
        # every CURRENT. Retain rows before old finish() clears private state.
        raw = canonical(
            financial._wire(
                {
                    "schema_version": SCHEMA,
                    "policy_sha256": POLICY_SHA256,
                    "scope_sha256": fold._scope_sha256,
                    "scope": [
                        fold._scope.environment,
                        fold._scope.account_id,
                        fold._scope.settlement_currency,
                    ],
                    "through_sequence": state["sequence"],
                    "index_head_sha256": state["previous"],
                    "identity": state["identity"],
                    "members": state["members"],
                    "position_observations": state["positions"],
                    "account_observations": state["accounts"],
                    "balance_measurements": state["balance_measurements"],
                    "original_CURRENT_sections": self._current_sections,
                    "original_current_sources": state["sources"],
                    "original_instrument_specs": state["specs"],
                    "original_order_context": state["orders"],
                    "original_anchors": state["anchors"],
                    "generation_coverage": state["coverage"],
                    "aggregate_window": state["window"],
                    "latest_observation": state["latest_observation"],
                    "original_blockers": state["blockers"],
                    "source_available_at": state["available_at"],
                    "source_authenticity_verified": False,
                    "account_complete": False,
                    "account_revision_issued": False,
                    "execution_authority": False,
                }
            )
        )
        retained_sections = self._sections_bytes + len(canonical(self._section_pins))
        if (
            not 0 < len(raw) <= MAX_RAW_BYTES
            or fold._state_bytes + retained_sections + len(raw) > MAX_COMBINED_BYTES
        ):
            _deny("original_raw_proof_bank_byte_bound")
        self._frozen_raw = raw

    def _finish_after_original_stream_v1(
        self, facts, *, expected_through, expected_head, checked_at
    ):
        try:
            if (
                self._policy_sha256 != POLICY_SHA256
                or type(self._frozen_raw) is not bytes
                or type(self._fold)
                is not financial.OriginalHistoryFinancialAccumulatorV2
                or self._fold._task is None
                or financial._task() is not self._fold._task
            ):
                _deny("original_raw_proof_bank_original_context_changed")
            raw = self._frozen_raw
            if type(facts) is not financial.GlobalFinancialHistoryFactsV2:
                _deny("original_raw_proof_bank_original_facts_required")
            if len(raw) + len(facts.receipt_json) > MAX_COMBINED_BYTES:
                _deny("original_raw_proof_bank_byte_bound")
            core = decode(facts.receipt_json, financial.MAX_COMPACT_BYTES)
            bank = decode(raw, MAX_RAW_BYTES)
            if (
                core["through_sequence"] != bank["through_sequence"]
                or core["through_sequence"] != expected_through
                or core["index_head_sha256"] != bank["index_head_sha256"]
                or core["index_head_sha256"] != expected_head
                or core["scope_sha256"] != bank["scope_sha256"]
                or sha(canonical(bank["scope"])) != bank["scope_sha256"]
                or core["policy_sha256"] != financial.POLICY_SHA256
                or core["checked_at"] != checked_at.isoformat()
                or canonical(core["original_current_sources"])
                != canonical(bank["original_current_sources"])
            ):
                _deny("original_raw_proof_bank_original_head_changed")
            receipt = canonical(
                {
                    "schema_version": "ctcc.original_account_raw_proof_bank_receipt.v1",
                    "policy_sha256": POLICY_SHA256,
                    "scope_sha256": bank["scope_sha256"],
                    "through_sequence": bank["through_sequence"],
                    "index_head_sha256": bank["index_head_sha256"],
                    "raw_source_sha256": sha(raw),
                    "raw_source_bytes": len(raw),
                    "financial_facts_sha256": sha(facts.receipt_json),
                    "checked_at": checked_at.isoformat(),
                    "source_authenticity_verified": False,
                    "account_complete": False,
                    "account_revision_issued": False,
                    "execution_authority": False,
                }
            )
            retained_sections = self._sections_bytes + len(
                canonical(self._section_pins)
            )
            if (
                len(raw) + len(facts.receipt_json) + len(receipt) + retained_sections
                > MAX_COMBINED_BYTES
            ):
                _deny("original_raw_proof_bank_byte_bound")
            financial._cancelled()
            self.cancel()
            return OriginalAccountRawProofBankValuesV1(raw, sha(raw), facts, receipt)
        except (
            BaseException
        ) as error:  # no partial result or private source error escapes.
            self.cancel()
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            if type(error) is OriginalRawProofBankError:
                raise
            _deny("original_raw_proof_bank_source_or_finish_invalid")

    def finish(
        self, *, expected_through, expected_head, checked_at, position_source=None
    ):
        try:
            self._freeze_verified_raw_state_v1()
            facts = self._fold.finish(
                expected_through=expected_through,
                expected_head=expected_head,
                checked_at=checked_at,
                position_source=position_source,
            )
            return self._finish_after_original_stream_v1(
                facts,
                expected_through=expected_through,
                expected_head=expected_head,
                checked_at=checked_at,
            )
        except (
            BaseException
        ) as error:  # preserve cancellation while erasing incomplete source.
            self.cancel()
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            if type(error) is OriginalRawProofBankError:
                raise
            _deny("original_raw_proof_bank_source_or_finish_invalid")


# V2 is an additive source-fact producer. It does not reinterpret the V1
# genesis-to-local-head population, return value, policy or finality flags.
API_POPULATION_SCHEMA_V2 = "ctcc.original_account_api_population_values.v2"
MAX_API_PACKETS_V2 = 128
MAX_API_RETAINED_BYTES_V2 = 16 * 1024 * 1024
API_POPULATION_POLICY_BYTES_V2 = canonical(
    {
        "version": "ctcc.original_account_api_population.policy.v2",
        "original_raw_bank_policy_sha256": POLICY_SHA256,
        "source": "same_original_chain_CURRENT_B3_witness_replay_and_whole_packet_verifier",
        "population": "each_original_requested_endpoint_filter_cursor_page_and_row",
        "raw_bytes": "every_original_packet_retained_without_source_rewriting",
        "anchor": "single_original_account_position_risk_equity_and_contract_population",
        "separate_balance": "corroboration_and_available_margin_not_same_time_relabel",
        "source_time": "original_update_time_and_original_receipt_interval_separate",
        "prefix_genesis": "local_source_index_only_not_exchange_account_genesis",
        "tail_seed_funding_and_continuous_peak": "separate_unproven_obligations",
        "source_authentication": "same_retained_native_owner_still_required",
        "resource_bounds": [MAX_API_PACKETS_V2, MAX_API_RETAINED_BYTES_V2],
    }
)
API_POPULATION_POLICY_SHA256_V2 = sha(API_POPULATION_POLICY_BYTES_V2)


@dataclass(frozen=True, slots=True, repr=False)
class OriginalAccountApiPopulationValuesV2:
    original_raw_bank: OriginalAccountRawProofBankValuesV1 = field(repr=False)
    coupled_anchors: tuple = field(repr=False)
    receipt_json: bytes = field(repr=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


class OriginalAccountApiPopulationAccumulatorV2:
    """Consume actual original chains once; invoke the coupled producer now.

    The old V1 accumulator remains the real history/prefix validator. Every
    positive source fact below additionally reopens the original full packet;
    callers cannot provide rows, selected pages, totals or a passed flag.
    """

    def __init__(self, *, scope):
        from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

        checked_bootstrap(scope, LedgerScope)
        self._scope = LedgerScope(
            environment=scope.environment,
            account_id=scope.account_id,
            settlement_currency=scope.settlement_currency,
        )
        self._scope_sha256 = sha(
            canonical(
                [
                    self._scope.environment,
                    self._scope.account_id,
                    self._scope.settlement_currency,
                ]
            )
        )
        self._original = OriginalAccountRawProofBankAccumulatorV1(scope=self._scope)
        self._policy_sha256 = API_POPULATION_POLICY_SHA256_V2
        self._anchors = []
        self._capture_ids = set()
        self._retained_bytes = 0

    def _context_v2(self):
        if (
            type(self._original) is not OriginalAccountRawProofBankAccumulatorV1
            or self._policy_sha256 != API_POPULATION_POLICY_SHA256_V2
            or sha(API_POPULATION_POLICY_BYTES_V2) != self._policy_sha256
            or sha(
                canonical(
                    [
                        self._scope.environment,
                        self._scope.account_id,
                        self._scope.settlement_currency,
                    ]
                )
            )
            != self._scope_sha256
        ):
            _deny("original_api_population_closed_or_changed")
        return self._original

    def _open(self):
        original = self._context_v2()
        original._open()
        return original

    def cancel(self):
        original, self._original = self._original, None
        self._anchors.clear()
        self._capture_ids.clear()
        self._retained_bytes = 0
        if type(original) is OriginalAccountRawProofBankAccumulatorV1:
            original.cancel()

    def _failure(self, error):
        self.cancel()
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        if type(error) is OriginalRawProofBankError:
            raise error from None
        _deny("original_api_population_source_invalid")

    def consume_current(self, *, chain, reference, batch, witnesses=()):
        try:
            original = self._open()
            if len(self._anchors) >= MAX_API_PACKETS_V2:
                _deny("original_api_population_packet_bound")
            original.consume_current(
                chain=chain, reference=reference, batch=batch, witnesses=witnesses
            )
            self._capture_after_verified_current_v2(
                chain=chain, reference=reference, batch=batch
            )
        except BaseException as error:  # noqa: BLE001 -- erase partial source and preserve cancellation.
            self._failure(error)

    def _capture_after_verified_current_v2(self, *, chain, reference, batch):
        """Fixed stream hook after the SAME V1 fold and raw capture, never a second consume."""
        from datetime import datetime

        from app.trade_qualification import (
            account_original_position_path_v1 as position,
        )

        try:
            original = self._open()
            sequence = len(self._anchors) + 1
            if (
                sequence > MAX_API_PACKETS_V2
                or original._fold._state["sequence"] != sequence
                or len(original._current_sections) != sequence
                or batch.sequence != sequence
            ):
                _deny("original_api_population_original_CURRENT_changed")
            section = original._current_sections[-1]
            if (
                section["source_reference"]
                != financial.observed.reference_document(reference)
                or section["batch_sha256"] != batch.event_sha256
            ):
                _deny("original_api_population_original_CURRENT_changed")
            capture_id = section["source_reference"]["capture_id"]
            if capture_id in self._capture_ids:
                _deny("original_api_population_capture_duplicate")
            # Completion is lifted from that original verified packet. It is
            # availability of a historical value, never a fresh CURRENT stamp.
            anchor = position.derive_original_coupled_risk_anchor_v2(
                chain,
                reference=reference,
                scope=self._scope,
                checked_at=datetime.fromisoformat(section["packet_completed_at"]),
            )
            if (
                type(anchor) is not position.OriginalCoupledRiskAnchorValuesV2
                or anchor.source_reference_json
                != canonical(section["source_reference"])
                or sha(anchor.original_packet) != section["original_packet_sha256"]
            ):
                _deny("original_api_population_original_packet_changed")
            size = (
                len(anchor.original_packet)
                + len(anchor.source_reference_json)
                + len(anchor.separate_balance_measurement_json)
                + len(anchor.receipt_json)
            )
            original_retained = (
                original._fold._state_bytes
                + original._sections_bytes
                + len(canonical(original._section_pins))
            )
            if (
                self._retained_bytes + size + original_retained
                > MAX_API_RETAINED_BYTES_V2
            ):
                _deny("original_api_population_retained_byte_bound")
            self._anchors.append(anchor)
            self._capture_ids.add(capture_id)
            self._retained_bytes += size
        except BaseException as error:  # noqa: BLE001 -- erase partial source and preserve cancellation.
            self._failure(error)

    def finish(
        self, *, expected_through, expected_head, checked_at, position_source=None
    ):
        try:
            original = self._open()
            if (
                type(expected_through) is not int
                or expected_through < 1
                or len(self._anchors) != expected_through
            ):
                _deny("original_api_population_prefix_incomplete")
            raw_bank = original.finish(
                expected_through=expected_through,
                expected_head=expected_head,
                checked_at=checked_at,
                position_source=position_source,
            )
            return self._finish_after_original_stream_v2(
                raw_bank,
                expected_through=expected_through,
                expected_head=expected_head,
                checked_at=checked_at,
            )
        except BaseException as error:  # noqa: BLE001 -- no partial bank escapes on failure.
            self._failure(error)

    def _finish_after_original_stream_v2(
        self, raw_bank, *, expected_through, expected_head, checked_at
    ):
        """Join after the real original stream/fold has finished; no new source query."""
        try:
            original = self._context_v2()
            if (
                original._fold is not None
                or original._current_sections
                or type(raw_bank) is not OriginalAccountRawProofBankValuesV1
                or type(raw_bank.financial_facts)
                is not financial.GlobalFinancialHistoryFactsV2
                or type(expected_through) is not int
                or expected_through < 1
                or len(self._anchors) != expected_through
                or sha(raw_bank.raw_source_json) != raw_bank.raw_source_sha256
            ):
                _deny("original_api_population_original_stream_not_finished")
            document = decode(raw_bank.raw_source_json, MAX_RAW_BYTES)
            old_receipt = decode(raw_bank.receipt_json, financial.MAX_COMPACT_BYTES)
            if (
                document["schema_version"] != SCHEMA
                or document["policy_sha256"] != POLICY_SHA256
                or document["scope_sha256"] != self._scope_sha256
                or document["through_sequence"] != expected_through
                or document["index_head_sha256"] != expected_head
                or old_receipt["schema_version"]
                != "ctcc.original_account_raw_proof_bank_receipt.v1"
                or old_receipt["policy_sha256"] != POLICY_SHA256
                or old_receipt["scope_sha256"] != self._scope_sha256
                or old_receipt["raw_source_sha256"] != raw_bank.raw_source_sha256
                or old_receipt["financial_facts_sha256"]
                != sha(raw_bank.financial_facts.receipt_json)
                or old_receipt["through_sequence"] != expected_through
                or old_receipt["index_head_sha256"] != expected_head
                or old_receipt["checked_at"] != checked_at.isoformat()
            ):
                _deny("original_api_population_original_bank_binding_changed")
            sections = document["original_CURRENT_sections"]
            if len(sections) != len(self._anchors):
                _deny("original_api_population_prefix_incomplete")
            membership = []
            for sequence, (anchor, section) in enumerate(
                zip(self._anchors, sections, strict=True), 1
            ):
                if (
                    section["sequence"] != sequence
                    or canonical(section["source_reference"])
                    != anchor.source_reference_json
                    or section["original_packet_sha256"] != sha(anchor.original_packet)
                    or anchor.original_packet_completed_at > checked_at
                ):
                    _deny("original_api_population_prefix_or_availability_changed")
                membership.append(
                    {
                        "sequence": sequence,
                        "batch_sha256": section["batch_sha256"],
                        "source_reference_sha256": sha(anchor.source_reference_json),
                        "original_packet_sha256": sha(anchor.original_packet),
                        "coupled_anchor_receipt_sha256": sha(anchor.receipt_json),
                    }
                )
            receipt = canonical(
                {
                    "schema_version": API_POPULATION_SCHEMA_V2,
                    "policy_sha256": API_POPULATION_POLICY_SHA256_V2,
                    "scope_sha256": self._scope_sha256,
                    "through_sequence": expected_through,
                    "index_head_sha256": expected_head,
                    "original_raw_bank_sha256": raw_bank.raw_source_sha256,
                    "original_raw_bank_receipt_sha256": sha(raw_bank.receipt_json),
                    "original_packet_population": membership,
                    "original_packet_population_sha256": sha(canonical(membership)),
                    "original_source_blockers": document["original_blockers"],
                    "checked_at": checked_at.isoformat(),
                    "source_fact_kinds": [
                        "requested_packet_api_population",
                        "coupled_risk_anchor",
                    ],
                    "original_V1_finality_meaning_changed": False,
                    "current_history_tail_finality": None,
                    "loss_streak_seed": None,
                    "cashflow_and_funding_effective_time_finality": None,
                    "continuous_peak": None,
                    "source_authenticity_verified": False,
                    "account_complete": False,
                    "account_revision_issued": False,
                    "execution_authority": False,
                }
            )
            retained = (
                self._retained_bytes
                + len(raw_bank.raw_source_json)
                + len(raw_bank.financial_facts.receipt_json)
                + len(raw_bank.receipt_json)
                + len(receipt)
            )
            if retained > MAX_API_RETAINED_BYTES_V2:
                _deny("original_api_population_retained_byte_bound")
            result = OriginalAccountApiPopulationValuesV2(
                raw_bank, tuple(self._anchors), receipt
            )
            self.cancel()
            return result
        except BaseException as error:  # noqa: BLE001 -- no partial bank escapes on failure.
            self._failure(error)

    def finish_window_operands_v3(
        self, *, expected_through, expected_head, checked_at, position_source=None
    ):
        """Run the real original fold, then derive the finite financial slice.

        This is an explicit versioned result; the V1/V2 results and stronger
        finality meanings are unchanged. No caller totals or complete flags
        enter either the original fold or the new arithmetic.
        """
        scope = self._scope
        population = self.finish(
            expected_through=expected_through,
            expected_head=expected_head,
            checked_at=checked_at,
            position_source=position_source,
        )
        return derive_original_api_window_operands_v3(
            population,
            scope=scope,
            checked_at=checked_at,
            _original_position_source=position_source,
        )


WINDOW_OPERANDS_SCHEMA_V3 = "ctcc.original_api_window_operands.v3"
WINDOW_OPERANDS_POLICY_BYTES_V3 = canonical(
    {
        "version": "ctcc.original_api_window_operands.policy.v3.CURRENT_asof_closed_runs_r2",
        "population": "same_original_V2_whole_prefix_and_exact_raw_members",
        "as_of": "same_held_CURRENT_v7_or_observed_last_history_anchor_never_restamped",
        "fill_arithmetic": "fillTime_UTC_window_fillPnl_and_signed_USDT_fee",
        "posted_debit": "sum_negative_original_bill_balChg_at_balance_completion_ts",
        "posted_debit_is_accrual_PnL_or_global_loss_bound": False,
        "reset": "positive_reconciled_full_close_with_exact_complete_observed_fill_membership",
        "reset_limit": "observed_population_only_until_API_asof_tail_is_proved",
        "complete_window": "every_fill_in_one_reconciled_closed_run_and_no_holding_crosses_window",
        "funding_window": "cumulative_full_close_funding_wholly_inside_holding_window_else_unknown",
        "funding_assessment": "official_global_assessment_up_to_60_seconds_strict_source_order_after_close_plus_cap",
        "funding_assessment_source": {
            "official_URL": "https://www.okx.com/help/perps-funding-fee-mechanism",
            "source_audit_sha256": "107174f8f188885e708f335f6c1d3c35bde2657a3dd4dc5e98854653aea447f9",
            "supported_contract": "global_official_mechanism_reviewed_2026_11_02",
        },
        "observed_posted_net_is_economic_risk_bound": False,
        "seed_at_original_start": "unknown_is_preserved",
        "future_global_watermark_or_external_writer_lease_required": False,
        "account_authority": "none",
        "maximum_members_and_runs": 4096,
        "maximum_numeric_bits": 4096,
        "maximum_total_retained_bytes_including_new_receipt": MAX_API_RETAINED_BYTES_V2,
    }
)
WINDOW_OPERANDS_POLICY_SHA256_V3 = sha(WINDOW_OPERANDS_POLICY_BYTES_V3)


@dataclass(frozen=True, slots=True, repr=False)
class OriginalAccountWindowOperandsValuesV3:
    original_population: OriginalAccountApiPopulationValuesV2 = field(repr=False)
    receipt_json: bytes = field(repr=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _generation_coverage_operands_v3(coverage, *, started_at, as_of):
    """Measure the existing validated query domain, not future event finality."""
    from app.trade_qualification import account_original_position_path_v1 as path

    path._utc(started_at)
    path._utc(as_of)
    if started_at > as_of:
        _deny("original_window_generation_window_invalid")
    if type(coverage) is not list or len(coverage) > 16:
        _deny("original_window_generation_domains_invalid")
    expected = {("fills", product) for product in capture.INSTRUMENT_TYPES} | {
        ("bills", "*")
    }
    seen, output, total = set(), [], 0
    for item in coverage:
        if type(item) is not dict:
            _deny("original_window_generation_domains_invalid")
        key = item.get("family"), item.get("product")
        intervals = item.get("intervals")
        if key not in expected or key in seen or type(intervals) is not list:
            _deny("original_window_generation_domains_invalid")
        seen.add(key)
        total += len(intervals)
        if total > 8192:
            _deny("original_window_generation_interval_bound")
        prior, covered, latest = None, False, None
        for interval in intervals:
            if type(interval) is not list or len(interval) != 2:
                _deny("original_window_generation_interval_invalid")
            left, right = (path._time(value) for value in interval)
            if left > right or (prior is not None and left <= prior):
                _deny("original_window_generation_interval_invalid")
            covered = covered or left <= started_at <= as_of <= right
            prior, latest = right, right
        output.append(
            {
                "family": key[0],
                "product": key[1],
                "requested_generation_window_covered": covered,
                "latest_covered_generation_at": None
                if latest is None
                else latest.isoformat(),
                "original_intervals_sha256": sha(canonical(intervals)),
            }
        )
    missing_domains = sorted(expected - seen)
    return {
        "started_at": started_at.isoformat(),
        "ended_at": as_of.isoformat(),
        "domains": output,
        "missing_domains": [list(key) for key in missing_domains],
        "all_requested_API_generation_domains_covered": not missing_domains
        and all(item["requested_generation_window_covered"] for item in output),
        "generation_coverage_is_execution_or_accrual_finality": False,
    }


def _observed_outcome_suffix_v3(runs, fill_pins, as_of):
    """Bound the observed suffix only; no exchange genesis/seed is fabricated."""
    from fractions import Fraction

    from app.trade_qualification import account_original_position_path_v1 as path

    if type(runs) is not list or len(runs) > 4096:
        _deny("original_window_run_population_bound")
    used, outcomes, missing = set(), [], set()
    for run in runs:
        if type(run) is not dict or type(run.get("original_fill_rows")) is not list:
            _deny("original_window_run_membership_invalid")
        rows = run["original_fill_rows"]
        if not rows or len(rows) > 4096:
            _deny("original_window_run_membership_invalid")
        for row in rows:
            if type(row) is not dict:
                _deny("original_window_run_membership_invalid")
            identity, pin = row.get("identity_sha256"), row.get("row_sha256")
            if (
                identity not in fill_pins
                or fill_pins[identity] != pin
                or identity in used
            ):
                _deny("original_window_run_membership_conflict")
            used.add(identity)
        if run.get("net_outcome_arithmetic_reconciled") is not True:
            missing.add("closed_run_net_after_all_costs_unknown")
            continue
        value = run.get("reconciled_closed_run_net_pnl")
        if type(value) is not dict or set(value) != {"numerator", "denominator"}:
            _deny("original_window_run_net_invalid")
        numbers = value["numerator"], value["denominator"]
        if any(type(item) is not str or len(item) > 1235 for item in numbers):
            _deny("original_window_run_net_invalid")
        # Bound before Fraction construction and check canonical representation.
        n, d = int(numbers[0]), int(numbers[1])
        if (
            str(n) != numbers[0]
            or str(d) != numbers[1]
            or d <= 0
            or max(n.bit_length(), d.bit_length()) > 4096
        ):
            _deny("original_window_run_net_invalid")
        net = Fraction(n, d)
        if net.numerator != n or net.denominator != d:
            _deny("original_window_run_net_invalid")
        at = path._time(run.get("closed_at_execution"))
        if at > as_of:
            missing.add("closed_run_after_original_current_asof")
        else:
            outcomes.append((at, net, path._digest(run.get("lifecycle_source_sha256"))))
    if used != set(fill_pins):
        missing.add("original_fill_population_not_fully_closed_reconciled")
    positives = [at for at, net, _pin in outcomes if net > 0]
    if not positives:
        missing.add("positive_reset_or_original_streak_seed_missing")
    reset = max(positives) if positives else None
    # With ambiguous cross-symbol equal times, place every loss after the
    # positive reset. A zero result neither increments nor resets the core.
    count = (
        None if missing else sum(at >= reset and net < 0 for at, net, _pin in outcomes)
    )
    return {
        "positive_reset_at": None if reset is None else reset.isoformat(),
        "observed_suffix_loss_streak_upper_bound": count,
        "original_start_loss_streak_seed": None,
        "account_current_loss_streak_upper_bound": None,
        "outcomes_sha256": sha(
            canonical(
                [
                    [at.isoformat(), path._fraction(net), pin]
                    for at, net, pin in outcomes
                ]
            )
        ),
        "missing_obligations": sorted(missing),
        "API_asof_tail_coverage_still_required": True,
    }


def _closed_run_window_net_v3(
    runs,
    members,
    *,
    started_at,
    as_of,
    joined_current,
    current_flat,
    later_flat,
    coverage_complete,
    activity_after_asof,
    unjoined_position_rows=False,
    position_archive_complete=False,
):
    """Sufficient finite branch; crossing holdings never get funding guessed.

    A full-close cumulative net includes funding without a per-settlement
    timestamp. It belongs wholly in a window only if that entire actual
    holding lies inside the window. Every fill must be in one exact run.
    """
    from datetime import timedelta
    from fractions import Fraction

    from app.trade_qualification import account_materializer as mapping
    from app.trade_qualification import account_original_position_path_v1 as path

    missing, fill_rows, trade_bills, used = set(), {}, {}, set()
    if not joined_current:
        missing.add("original_held_CURRENT_asof_join_missing")
    if not current_flat or not later_flat:
        missing.add(
            "complete_closed_run_window_requires_flat_current_and_history_snapshot"
        )
    if not coverage_complete:
        missing.add("original_API_generation_window_not_covered")
    if activity_after_asof:
        missing.add("activity_after_original_CURRENT_requires_new_capture")
    if unjoined_position_rows:
        missing.add("original_position_history_population_not_fully_joined")
    if not position_archive_complete:
        missing.add("original_position_history_archive_missing")
    for member in members:
        if member["family"] == "fills":
            fill_rows[member["identity_sha256"]] = member
        elif member["raw"].get("type") == "2":
            bill_id = path._id(member["raw"].get("billId"))
            if bill_id in trade_bills:
                _deny("original_window_trade_bill_identity_conflict")
            trade_bills[bill_id] = member["raw"]
        elif not (
            member["raw"].get("type") == "8"
            and member["raw"].get("subType") in {"173", "174"}
        ):
            missing.add("nontrade_nonfunding_cashflow_cost_classification_unknown")
    matched_bills = set()
    for member in fill_rows.values():
        fill = member["raw"]
        bill_id = path._id(fill.get("billId"))
        if (
            bill_id not in trade_bills
            or bill_id in matched_bills
            or not mapping._trade_bill_matches_fill(trade_bills[bill_id], fill)
        ):
            missing.add("original_trade_bill_fill_bidirectional_reconciliation_missing")
        else:
            matched_bills.add(bill_id)
    if matched_bills != set(trade_bills):
        missing.add("original_trade_bill_population_not_fully_joined")
    total, observed_total, run_pins = Fraction(0), Fraction(0), []
    for run in runs:
        identities = [row["identity_sha256"] for row in run["original_fill_rows"]]
        if not identities or len(set(identities)) != len(identities):
            _deny("original_window_closed_run_membership_invalid")
        if any(
            identity not in fill_rows or identity in used for identity in identities
        ):
            _deny("original_window_closed_run_population_conflict")
        used.update(identities)
        if run.get("net_outcome_arithmetic_reconciled") is not True:
            missing.add("complete_closed_run_net_after_costs_unknown")
            continue
        times = [
            path._ms(fill_rows[identity]["raw"]["fillTime"]) for identity in identities
        ]
        opened, closed = (
            path._time(run.get("opened_at_execution")),
            path._time(run.get("closed_at_execution")),
        )
        if opened != min(times) or closed != max(times) or opened > closed:
            _deny("original_window_closed_run_execution_bounds_changed")
        value = run["reconciled_closed_run_net_pnl"]
        net = Fraction(int(value["numerator"]), int(value["denominator"]))
        if started_at <= opened <= closed <= as_of:
            observed_total = path._checked(observed_total + net)
        if closed > as_of:
            missing.add("closed_run_after_original_CURRENT")
        elif as_of <= closed + timedelta(seconds=60):
            # The documented assessment can continue after a recent close.
            # Equality has no causal ordering proof. This is not an invented
            # bill-posting-delay or a permanent future-finality requirement.
            missing.add("recent_close_funding_assessment_completion_or_bound_unknown")
        elif closed < started_at <= closed + timedelta(seconds=60):
            missing.add("prior_holding_funding_assessment_may_cross_window_start")
        elif opened < started_at <= closed:
            missing.add("holding_crosses_window_funding_allocation_unknown")
        elif started_at <= opened <= started_at + timedelta(seconds=60):
            missing.add("opening_may_overlap_prior_window_funding_assessment")
        elif started_at <= opened <= closed <= as_of:
            # The suffix checker already validated canonical finite fractions.
            total = path._checked(total + net)
            run_pins.append(path._digest(run["lifecycle_source_sha256"]))
    if used != set(fill_rows):
        missing.add("original_fill_population_not_fully_closed_reconciled")
    return {
        "contract": "ctcc.finite_API_asof_complete_closed_run_window.v3",
        "exact_net_PnL": None if missing else path._fraction(total),
        "observed_selected_closed_run_net_subtotal": path._fraction(observed_total),
        "included_lifecycle_population_sha256": sha(canonical(run_pins)),
        "individual_funding_accrual_events": None,
        "observed_position_history_net_is_economic_risk_bound_without_assessment_check": False,
        "bill_ts_used_as_accrual": False,
        "missing_obligations": sorted(missing),
    }


def _unjoined_position_window_v3(unjoined, original_rows, *, started_at):
    """Exclude only source-proven normal closures entirely before the window.

    uTime is an upper bound on the already completed close, never a fabricated
    fill time. The documented assessment interval must also end strictly
    before the unchanged window. Recent/unknown rows remain unresolved.
    """
    from datetime import timedelta

    from app.trade_qualification import account_capture_journal as journal
    from app.trade_qualification import account_original_position_path_v1 as path

    if type(unjoined) is not list or len(unjoined) > 4096:
        _deny("original_window_position_population_bound")
    if original_rows is None:
        return {"unresolved_count": len(unjoined), "excluded_original_row_sha256": []}
    if type(original_rows) is not list or len(original_rows) > 4096:
        _deny("original_window_position_population_bound")
    indexed = {}
    for item in original_rows:
        if type(item) is not dict or type(item.get("raw")) is not dict:
            _deny("original_window_position_original_row_invalid")
        pin = path._digest(item.get("row_sha256"))
        if journal.digest(journal.canonical(item["raw"])) != pin:
            _deny("original_window_position_original_bytes_changed")
        if pin in indexed:
            _deny("original_window_position_duplicate_original_row")
        indexed[pin] = item["raw"]
    excluded, unresolved, seen = [], 0, set()
    for item in unjoined:
        pin = path._digest(item.get("row_sha256"))
        if pin in seen or pin not in indexed:
            _deny("original_window_position_original_membership_changed")
        seen.add(pin)
        row = indexed[pin]
        updated, created = path._ms(row.get("uTime")), path._ms(row.get("cTime"))
        normal = (
            row.get("type") == "2"
            and row.get("instType") == "SWAP"
            and row.get("ccy") == "USDT"
            and row.get("posSide") == "net"
            and type(row.get("instId")) is str
            and path._SYMBOL.fullmatch(row["instId"]) is not None
            and created <= updated
            and path._number(row.get("closeTotalPos"), positive=True) > 0
        )
        if normal and updated + timedelta(seconds=60) < started_at:
            excluded.append(pin)
        else:
            unresolved += 1
    return {"unresolved_count": unresolved, "excluded_original_row_sha256": excluded}


def _derive_original_api_window_operands_v3(
    population,
    *,
    scope,
    checked_at,
    _held_current_record=None,
    _original_position_source=None,
    _evaluation_at=None,
):
    from datetime import timedelta
    from fractions import Fraction

    from app.trade_qualification import account_capture_journal as journal
    from app.trade_qualification import account_original_position_path_v1 as path
    from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

    path._utc(checked_at)
    checked_bootstrap(scope, LedgerScope)
    if (
        type(population) is not OriginalAccountApiPopulationValuesV2
        or type(population.original_raw_bank) is not OriginalAccountRawProofBankValuesV1
        or type(population.coupled_anchors) is not tuple
        or not 1 <= len(population.coupled_anchors) <= MAX_API_PACKETS_V2
    ):
        _deny("original_window_original_population_required")
    raw_bank = population.original_raw_bank
    if type(raw_bank.financial_facts) is not financial.GlobalFinancialHistoryFactsV2:
        _deny("original_window_original_bank_changed")
    for raw, limit in (
        (raw_bank.raw_source_json, MAX_RAW_BYTES),
        (raw_bank.receipt_json, financial.MAX_COMPACT_BYTES),
        (raw_bank.financial_facts.receipt_json, financial.MAX_COMPACT_BYTES),
        (population.receipt_json, MAX_RAW_BYTES),
    ):
        if type(raw) is not bytes or not 0 < len(raw) <= limit:
            _deny("original_window_original_bank_byte_bound")
    if sha(raw_bank.raw_source_json) != path._digest(raw_bank.raw_source_sha256):
        _deny("original_window_original_bank_changed")
    retained = (
        len(raw_bank.raw_source_json)
        + len(raw_bank.receipt_json)
        + len(raw_bank.financial_facts.receipt_json)
        + len(population.receipt_json)
    )
    for source in population.coupled_anchors:
        if type(source) is not path.OriginalCoupledRiskAnchorValuesV2:
            _deny("original_window_original_anchor_required")
        for stamp in (
            source.account_source_updated_at,
            source.risk_request_started_at,
            source.risk_headers_received_at,
            source.risk_body_completed_at,
            source.original_packet_completed_at,
        ):
            path._utc(stamp)
        for raw in (
            source.original_packet,
            source.source_reference_json,
            source.separate_balance_measurement_json,
            source.receipt_json,
        ):
            if type(raw) is not bytes:
                _deny("original_window_retained_source_type_changed")
            retained += len(raw)
            if retained > MAX_API_RETAINED_BYTES_V2:
                _deny("original_window_total_retained_byte_bound")
    document = decode(raw_bank.raw_source_json, MAX_RAW_BYTES)
    receipt = decode(population.receipt_json, MAX_RAW_BYTES)
    old_receipt = decode(raw_bank.receipt_json, MAX_RAW_BYTES)
    core = decode(raw_bank.financial_facts.receipt_json, financial.MAX_COMPACT_BYTES)
    scope_pin = sha(
        canonical([scope.environment, scope.account_id, scope.settlement_currency])
    )
    if (
        scope.settlement_currency != "USDT"
        or scope.environment != "demo"
        or receipt["schema_version"] != API_POPULATION_SCHEMA_V2
        or receipt["policy_sha256"] != API_POPULATION_POLICY_SHA256_V2
        or document["schema_version"] != SCHEMA
        or document["policy_sha256"] != POLICY_SHA256
        or receipt["scope_sha256"] != scope_pin
        or document["scope_sha256"] != scope_pin
        or receipt["original_raw_bank_sha256"] != raw_bank.raw_source_sha256
        or receipt["original_raw_bank_receipt_sha256"] != sha(raw_bank.receipt_json)
        or old_receipt["financial_facts_sha256"]
        != sha(raw_bank.financial_facts.receipt_json)
        or receipt["checked_at"] != checked_at.isoformat()
        or old_receipt["checked_at"] != checked_at.isoformat()
        or core["through_sequence"] != document["through_sequence"]
        or core["index_head_sha256"] != document["index_head_sha256"]
        or len(population.coupled_anchors) != document["through_sequence"]
    ):
        _deny("original_window_population_binding_changed")
    anchor = population.coupled_anchors[-1]
    if type(anchor) is not path.OriginalCoupledRiskAnchorValuesV2:
        _deny("original_window_original_anchor_required")
    member_receipts = receipt["original_packet_population"]
    if (
        type(member_receipts) is not list
        or len(member_receipts) != len(population.coupled_anchors)
        or receipt["original_packet_population_sha256"]
        != sha(canonical(member_receipts))
    ):
        _deny("original_window_anchor_population_changed")
    for index, (source, member) in enumerate(
        zip(population.coupled_anchors, member_receipts, strict=True), 1
    ):
        if (
            type(source) is not path.OriginalCoupledRiskAnchorValuesV2
            or member["sequence"] != index
            or member["original_packet_sha256"] != sha(source.original_packet)
            or member["coupled_anchor_receipt_sha256"] != sha(source.receipt_json)
            or source.original_packet_completed_at > checked_at
        ):
            _deny("original_window_anchor_population_changed")
    asof_join = None
    current_history_state_changed = False
    if _held_current_record is not None:
        current_anchor = path._coupled_anchor_from_held_current_record_v3(
            _held_current_record, scope=scope, checked_at=checked_at
        )
        latest = population.coupled_anchors[-1]
        history_packet = capture._verify_original_page_packet(
            latest.original_packet,
            expected_sha256=sha(latest.original_packet),
            expected_plan_sha256=decode(latest.receipt_json)["packet_plan_sha256"],
        )
        original_current = _held_current_record.packet
        if (
            not original_current.completed_at
            < min(page.request_started_at for page in history_packet.observations)
            or history_packet.plan.history_end < original_current.completed_at
            or not capture.is_all_product_plan(history_packet.plan)
            or any(
                getattr(original_current.plan, name)
                != getattr(history_packet.plan, name)
                for name in (
                    "expected_uid",
                    "expected_main_uid",
                    "environment",
                    "settlement_currency",
                    "origin",
                    "registration_region",
                )
            )
        ):
            _deny("original_window_CURRENT_first_history_scope_or_cutoff_changed")
        retained += sum(
            len(raw)
            for raw in (
                current_anchor.original_packet,
                current_anchor.source_reference_json,
                current_anchor.separate_balance_measurement_json,
                current_anchor.receipt_json,
            )
        )
        if retained > MAX_API_RETAINED_BYTES_V2:
            _deny("original_window_total_retained_byte_bound")
        asof_join = {
            "contract": "ctcc.original_API_asof_CURRENT_history_join.v3",
            "CURRENT_packet_sha256": sha(current_anchor.original_packet),
            "CURRENT_completed_at": original_current.completed_at.isoformat(),
            "history_packet_sha256": sha(latest.original_packet),
            "first_history_request_at": min(
                page.request_started_at for page in history_packet.observations
            ).isoformat(),
            "history_requested_cutoff": history_packet.plan.history_end.isoformat(),
            "later_history_risk_time_is_CURRENT_time": False,
            "permanent_future_arrival_finality_claimed": False,
        }

        def inventory(value):
            return {
                item.position_id: (
                    item.instrument_id,
                    item.position_side,
                    item.margin_mode,
                    item.contracts,
                )
                for item in value.positions
            }

        def ordinary_pending(packet):
            result = {}
            for page in packet.observations:
                if page.request.stream != "orders_pending":
                    continue
                for row in page.rows:
                    raw = decode(
                        row.canonical_json.encode("utf-8"), capture.MAX_RESPONSE_BYTES
                    )
                    order_id = path._id(raw.get("ordId"))
                    if order_id in result:
                        _deny("original_window_ordinary_pending_duplicate")
                    result[order_id] = row.canonical_json
            return result

        current_history_state_changed = (
            current_anchor.account_equity_usdt != latest.account_equity_usdt
            or current_anchor.separately_observed_available_margin_usdt
            != latest.separately_observed_available_margin_usdt
            or inventory(current_anchor) != inventory(latest)
            or ordinary_pending(original_current) != ordinary_pending(history_packet)
        )
        asof_join["observed_account_state_changed_after_CURRENT"] = (
            current_history_state_changed
        )
        asof_join["later_pending_comparison_domain"] = (
            "ordinary_pending_only_other_CURRENT_domains_not_restamped"
        )
        anchor = current_anchor
    as_of = path._utc(anchor.account_source_updated_at)
    if not as_of <= anchor.original_packet_completed_at <= checked_at:
        _deny("original_window_original_clock_invalid")
    evaluated_at = checked_at if _evaluation_at is not None else as_of
    if _evaluation_at is not None:
        evaluated_at = path._utc(_evaluation_at)
        if evaluated_at < checked_at:
            _deny("original_window_evaluation_before_original_validation")
    day = evaluated_at.replace(hour=0, minute=0, second=0, microsecond=0)
    week = evaluated_at - timedelta(days=7)
    windows = {"UTC_day": day, "rolling_7d": week}
    totals = {
        name: {
            "fill_gross": Fraction(0),
            "signed_fill_fee": Fraction(0),
            "posted_bill_debit": Fraction(0),
        }
        for name in windows
    }
    members = document["members"]
    if type(members) is not list or len(members) > 4096:
        _deny("original_window_member_population_bound")
    names = set()
    for member in members:
        if type(member) is dict and member.get("family") == "fills":
            row = member.get("raw")
            if (
                type(row) is not dict
                or type(row.get("instId")) is not str
                or path._SYMBOL.fullmatch(row["instId"]) is None
            ):
                _deny("original_window_gross_settlement_unit_unknown")
            names.add(row["instId"])
    # feeCcy alone cannot establish the gross-PnL settlement currency.
    path._specifications(document, names)
    pins, fill_pins, after_asof, funding_pins = set(), {}, [], []
    for original_index, member in enumerate(members):
        if (
            type(member) is not dict
            or member.get("family") not in {"fills", "bills"}
            or type(member.get("raw")) is not dict
            or journal.digest(journal.canonical(member["raw"]))
            != path._digest(member.get("row_sha256"))
            or path._digest(member.get("identity_sha256")) in pins
            or type(member.get("locators")) is not list
            or not member["locators"]
        ):
            _deny("original_window_member_changed_or_duplicate")
        pins.add(member["identity_sha256"])
        row = member["raw"]
        if member["family"] == "fills":
            if row.get("instType") != "SWAP" or row.get("feeCcy") != "USDT":
                _deny("original_window_fill_currency_or_product_unknown")
            at = path._ms(row.get("fillTime"))
            if member.get("fill_at") != at.isoformat():
                _deny("original_window_fill_time_binding_changed")
            gross, fee = path._number(row.get("fillPnl")), path._number(row.get("fee"))
            fill_pins[member["identity_sha256"]] = member["row_sha256"]
            for name, left in windows.items():
                if left <= at <= as_of:
                    totals[name]["fill_gross"] = path._checked(
                        totals[name]["fill_gross"] + gross
                    )
                    totals[name]["signed_fill_fee"] = path._checked(
                        totals[name]["signed_fill_fee"] + fee
                    )
        else:
            if row.get("ccy") != "USDT":
                _deny("original_window_bill_currency_unknown")
            at = path._ms(row.get("ts"))
            change = path._number(row.get("balChg"))
            for name, left in windows.items():
                if left <= at <= as_of:
                    totals[name]["posted_bill_debit"] = path._checked(
                        totals[name]["posted_bill_debit"] + max(-change, Fraction(0))
                    )
            if row.get("type") == "8" or row.get("subType") in {"173", "174"}:
                funding_pins.append(member["row_sha256"])
        if at > as_of:
            after_asof.append(original_index)
    reset = _observed_outcome_suffix_v3(core["closed_runs"], fill_pins, as_of)
    coverage = core["aggregate_generation_coverage"]
    coverage_operands = {
        name: _generation_coverage_operands_v3(
            coverage, started_at=left - timedelta(seconds=60), as_of=as_of
        )
        for name, left in windows.items()
    }
    position_archive = core["native_position_archive_sha256"]
    if position_archive is not None:
        path._digest(position_archive)
    position_rows = None
    position_source_gaps = {"original_position_archive_not_replayed_in_this_derivation"}
    if _original_position_source is not None:
        replay, position_rows, position_source_gaps = (
            financial.position_sessions.native_position_rows_for_original_prefix_v3(
                _original_position_source,
                document["account_observations"],
                scope,
                checked_at,
            )
        )
        if replay.archive_sha256 != position_archive:
            _deny("original_window_position_archive_binding_changed")
    unjoined_positions = {
        name: _unjoined_position_window_v3(
            core["unjoined_position_rows"], position_rows, started_at=left
        )
        for name, left in windows.items()
    }
    complete_windows = {
        name: _closed_run_window_net_v3(
            core["closed_runs"],
            members,
            started_at=left,
            as_of=as_of,
            joined_current=asof_join is not None,
            current_flat=all(item.contracts == 0 for item in anchor.positions),
            later_flat=all(
                item.contracts == 0 for item in population.coupled_anchors[-1].positions
            ),
            coverage_complete=coverage_operands[name][
                "all_requested_API_generation_domains_covered"
            ],
            activity_after_asof=bool(after_asof) or current_history_state_changed,
            unjoined_position_rows=unjoined_positions[name]["unresolved_count"] != 0,
            position_archive_complete=position_archive is not None
            and not position_source_gaps,
        )
        for name, left in windows.items()
    }
    reset_at = (
        None
        if reset["positive_reset_at"] is None
        else path._time(reset["positive_reset_at"])
    )
    reset_coverage = (
        None
        if reset_at is None
        else _generation_coverage_operands_v3(
            coverage, started_at=reset_at, as_of=as_of
        )
    )
    finite_streak = None
    if (
        asof_join is not None
        and reset_at is not None
        and week <= reset_at
        and reset_at + timedelta(seconds=60) < as_of
        and reset["observed_suffix_loss_streak_upper_bound"] is not None
        and reset_coverage["all_requested_API_generation_domains_covered"]
        and complete_windows["rolling_7d"]["exact_net_PnL"] is not None
    ):
        finite_streak = reset["observed_suffix_loss_streak_upper_bound"]
    missing = {
        "continuous_peak_or_dominating_source_bound_missing",
    }
    if not all(
        value["all_requested_API_generation_domains_covered"]
        for value in coverage_operands.values()
    ):
        missing.add("API_generation_population_does_not_cover_original_CURRENT_windows")
    # Even covered generation filters are not an event-time funding allocation
    # or proof that every lifecycle up to CURRENT is closed/reconciled.
    if any(value["missing_obligations"] for value in complete_windows.values()):
        missing.add("API_generation_to_execution_tail_or_cost_reconciliation_missing")
    if any(value["exact_net_PnL"] is None for value in complete_windows.values()):
        missing.add(
            "effective_funding_window_allocation_or_finite_adverse_bound_missing"
        )
    if after_asof:
        missing.add("observed_activity_after_original_CURRENT_requires_fresh_capture")
    if current_history_state_changed:
        missing.add(
            "observed_account_state_after_original_CURRENT_changed_requires_fresh_capture"
        )
    missing.update(reset["missing_obligations"])
    if finite_streak is None:
        missing.add("finite_API_asof_complete_reset_or_original_seed_missing")
    output = canonical(
        {
            "schema_version": (
                WINDOW_OPERANDS_SCHEMA_V3
                if _evaluation_at is None
                else FINITE_EVALUATED_WINDOWS_SCHEMA_V1
            ),
            "policy_sha256": (
                WINDOW_OPERANDS_POLICY_SHA256_V3
                if _evaluation_at is None
                else FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1
            ),
            **(
                {}
                if _evaluation_at is None
                else {"window_evaluated_at": evaluated_at.isoformat()}
            ),
            "scope_sha256": scope_pin,
            "original_population_receipt_sha256": sha(population.receipt_json),
            "original_raw_bank_sha256": raw_bank.raw_source_sha256,
            "as_of_original_risk_source_at": as_of.isoformat(),
            "available_at": document["source_available_at"],
            "checked_at": checked_at.isoformat(),
            "same_original_CURRENT_history_asof_join": asof_join,
            "original_member_count": len(members),
            "derivation_retained_original_bytes_before_receipt": retained,
            "source_windows": {
                name: {
                    "started_at": left.isoformat(),
                    "ended_at": as_of.isoformat(),
                    **{
                        key: path._fraction(value)
                        for key, value in totals[name].items()
                    },
                    "original_API_generation_coverage": coverage_operands[name],
                    "complete_closed_run_window": complete_windows[name],
                    "unjoined_position_window": unjoined_positions[name],
                    "exact_full_net_PnL": complete_windows[name]["exact_net_PnL"],
                    "account_loss_upper_bound": None,
                }
                for name, left in windows.items()
            },
            "posted_bill_ts_semantics": "balance_update_completion_not_funding_accrual",
            "generation_coverage_sha256": sha(canonical(coverage)),
            "funding_bill_population_sha256": sha(canonical(funding_pins)),
            "original_position_source_gaps": sorted(position_source_gaps),
            "observed_activity_after_asof_original_indices": after_asof,
            "observed_closed_run_reset": reset,
            "finite_API_asof_streak": {
                "contract": "ctcc.finite_API_asof_complete_positive_reset_streak.v3",
                "current_loss_streak_upper_bound": finite_streak,
                "original_start_loss_streak_seed": None,
                "reset_coverage": reset_coverage,
                "individual_funding_accrual_events": None,
            },
            "actual_continuous_peak": None,
            "missing_obligations": sorted(missing),
            "source_authenticity_verified": False,
            "account_complete": False,
            "account_revision_issued": False,
            "execution_authority": False,
        }
    )
    if (
        len(output) > financial.MAX_COMPACT_BYTES
        or retained + len(output) > MAX_API_RETAINED_BYTES_V2
    ):
        _deny("original_window_result_byte_bound")
    return OriginalAccountWindowOperandsValuesV3(population, output)


def derive_original_api_window_operands_v3(
    population, *, scope, checked_at, _original_position_source=None
):
    """Derive real finite operands, preserving each genuinely missing component."""
    try:
        return _derive_original_api_window_operands_v3(
            population,
            scope=scope,
            checked_at=checked_at,
            _original_position_source=_original_position_source,
        )
    except Exception as error:  # Original private source details never escape.
        if type(error) is OriginalRawProofBankError:
            raise
        _deny("original_window_source_or_arithmetic_invalid")


# The original V3 receipt above remains an observation at CURRENT's source time.
# This separate contract changes only the day/week left boundaries at G11 time;
# original packet receipt/validation times and source cutoff remain unchanged.
FINITE_EVALUATED_WINDOWS_SCHEMA_V1 = "ctcc.original_API_asof_evaluated_windows.v1"
FINITE_EVALUATED_WINDOWS_POLICY_BYTES_V1 = canonical(
    {
        "schema_version": FINITE_EVALUATED_WINDOWS_SCHEMA_V1,
        "parent_policy_sha256": WINDOW_OPERANDS_POLICY_SHA256_V3,
        "daily_left": "UTC_midnight_of_actual_G11_evaluation",
        "weekly_left": "actual_G11_evaluation_minus_exact_7_days",
        "right": "unchanged_original_CURRENT_source_timestamp",
        "funding": "original_closed_run_allocation_and_maturity_checks_unchanged",
        "streak": "original_complete_positive_reset_upper_bound_not_initial_seed",
        "original_validation_timestamp": "preserved_never_restamped",
    }
)
FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1 = sha(
    FINITE_EVALUATED_WINDOWS_POLICY_BYTES_V1
)


def _derive_evaluated_api_asof_windows_v1(
    population,
    *,
    scope,
    original_validated_at,
    evaluated_at,
    held_current_record,
    original_position_source,
):
    """Same original rows and finite reconciliation at the actual G11 window.

    This is a data reducer. Only its native original owner can give the returned
    observations source authority; no receipt, DTO or successful arithmetic does.
    """
    return _derive_original_api_window_operands_v3(
        population,
        scope=scope,
        checked_at=original_validated_at,
        _held_current_record=held_current_record,
        _original_position_source=original_position_source,
        _evaluation_at=evaluated_at,
    )


POLICY_COUNTER_STREAK_SCHEMA_V1 = "ctcc.original_API_asof_policy_counter_streak.v1"


def _derive_original_policy_counter_bound_v1(
    evaluated_windows,
    *,
    scope,
    original_validated_at,
    held_current_record,
    original_position_source,
    initialized_at,
    initialization_sha256,
):
    """Count actual complete post-epoch outcomes; never an initial/global seed.

    The native caller authenticates the immutable unique initialization row.
    This reducer neither initializes a counter nor trusts a passed/complete flag.
    Every pre-epoch day/week amount remains in the unchanged window receipt.
    """
    from datetime import timedelta
    from fractions import Fraction

    from app.trade_qualification import account_original_position_path_v1 as path
    from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

    checked_bootstrap(scope, LedgerScope)
    if type(evaluated_windows) is not OriginalAccountWindowOperandsValuesV3:
        _deny("policy_counter_original_evaluated_windows_required")
    initialized_at = path._utc(initialized_at)
    path._digest(initialization_sha256)
    receipt = decode(evaluated_windows.receipt_json, financial.MAX_COMPACT_BYTES)
    if (
        receipt["schema_version"] != FINITE_EVALUATED_WINDOWS_SCHEMA_V1
        or receipt["policy_sha256"] != FINITE_EVALUATED_WINDOWS_POLICY_SHA256_V1
        or receipt["checked_at"] != path._utc(original_validated_at).isoformat()
    ):
        _deny("policy_counter_original_window_binding_changed")
    as_of = path._time(receipt["as_of_original_risk_source_at"])
    if initialized_at > as_of:
        _deny("policy_counter_epoch_after_original_CURRENT")
    population = evaluated_windows.original_population
    raw_bank = population.original_raw_bank
    document = decode(raw_bank.raw_source_json, MAX_RAW_BYTES)
    core = decode(raw_bank.financial_facts.receipt_json, financial.MAX_COMPACT_BYTES)
    members = document["members"]
    fill_pins = {
        item["identity_sha256"]: item["row_sha256"]
        for item in members
        if item["family"] == "fills"
    }
    suffix = _observed_outcome_suffix_v3(core["closed_runs"], fill_pins, as_of)
    coverage = _generation_coverage_operands_v3(
        core["aggregate_generation_coverage"],
        started_at=initialized_at - timedelta(seconds=60),
        as_of=as_of,
    )
    current = path._coupled_anchor_from_held_current_record_v3(
        held_current_record, scope=scope, checked_at=original_validated_at
    )
    position_replay, position_rows, position_gaps = (
        financial.position_sessions.native_position_rows_for_original_prefix_v3(
            original_position_source,
            document["account_observations"],
            scope,
            original_validated_at,
        )
    )
    if position_replay.archive_sha256 != core["native_position_archive_sha256"]:
        _deny("policy_counter_original_position_archive_changed")
    unjoined = _unjoined_position_window_v3(
        core["unjoined_position_rows"], position_rows, started_at=initialized_at
    )
    join = receipt["same_original_CURRENT_history_asof_join"]
    window = _closed_run_window_net_v3(
        core["closed_runs"],
        members,
        started_at=initialized_at,
        as_of=as_of,
        joined_current=join is not None,
        current_flat=all(item.contracts == 0 for item in current.positions),
        later_flat=all(
            item.contracts == 0 for item in population.coupled_anchors[-1].positions
        ),
        coverage_complete=coverage["all_requested_API_generation_domains_covered"],
        activity_after_asof=bool(
            receipt["observed_activity_after_asof_original_indices"]
        )
        or join is None
        or join["observed_account_state_changed_after_CURRENT"],
        unjoined_position_rows=unjoined["unresolved_count"] != 0,
        position_archive_complete=core["native_position_archive_sha256"] is not None
        and not position_gaps,
    )
    missing = set(suffix["missing_obligations"])
    # This distinct operator-selected policy counts since its durable epoch;
    # the original/global positive-reset/seed statement remains in old receipts.
    missing.discard("positive_reset_or_original_streak_seed_missing")
    missing.update(window["missing_obligations"])
    if receipt["source_windows"]["rolling_7d"]["exact_full_net_PnL"] is None:
        missing.add("policy_counter_pre_epoch_weekly_net_unknown")
    selected, positive, count = [], [], None
    if not missing:
        for run in core["closed_runs"]:
            closed = path._time(run["closed_at_execution"])
            if initialized_at <= closed <= as_of:
                raw = run["reconciled_closed_run_net_pnl"]
                net = Fraction(int(raw["numerator"]), int(raw["denominator"]))
                selected.append(
                    (closed, net, path._digest(run["lifecycle_source_sha256"]))
                )
                if net > 0:
                    positive.append(closed)
        reset = max(positive) if positive else initialized_at
        # Equal-time negative outcomes are placed after a positive reset.
        # Zero outcomes neither increment nor reset.
        count = sum(closed >= reset and net < 0 for closed, net, _ in selected)
        if not 0 <= count <= 4096:
            _deny("policy_counter_outcome_population_bound")
    output = canonical(
        {
            "schema_version": POLICY_COUNTER_STREAK_SCHEMA_V1,
            "initialization_sha256": initialization_sha256,
            "initialized_at": initialized_at.isoformat(),
            "as_of_original_risk_source_at": as_of.isoformat(),
            "original_evaluated_windows_sha256": sha(evaluated_windows.receipt_json),
            "original_index_head_sha256": core["index_head_sha256"],
            "generation_coverage": coverage,
            "complete_closed_run_window": window,
            "unjoined_position_window": unjoined,
            "selected_outcomes_sha256": sha(
                canonical(
                    [
                        [at.isoformat(), path._fraction(net), pin]
                        for at, net, pin in selected
                    ]
                )
            ),
            "current_loss_streak_upper_bound": count,
            "original_start_loss_streak_seed": None,
            "daily_or_weekly_amounts_reset": False,
            "drawdown_window_reset": False,
            "missing_obligations": sorted(missing),
            "source_authenticity_verified": False,
            "account_complete": False,
            "execution_authority": False,
        }
    )
    if not 0 < len(output) <= financial.MAX_COMPACT_BYTES:
        _deny("policy_counter_receipt_byte_bound")
    return count, output, tuple(sorted(missing))
