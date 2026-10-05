"""B5 diagnostic components v2; injected UTC never issues a current owner."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from weakref import WeakKeyDictionary

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
    BalanceSourcePage,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_clock_boundary as clock_boundary
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_metadata as metadata
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.reservations import LedgerScope

_OWNERS = WeakKeyDictionary()
DIAGNOSTIC_POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.portfolio_components_diagnostic.v2",
        "base_component_policy_sha256": components.POLICY_SHA256,
        "injected_utc_clock": "diagnostic_dependency_not_native_attestation",
        "native_tls_transport": "distinct_from_native_clock_proof",
        "historical_hwm_clock": "unknown_without_original_companion_proofs",
        "maximum_diagnostic_seconds": 30,
        "current_native_owner_issuer": "unimplemented_durable_clock_proof_required",
        "publish_account_revision": False,
        "execution_authority": False,
    }
)
DIAGNOSTIC_POLICY_SHA256 = journal.digest(DIAGNOSTIC_POLICY_BYTES)


class OwnedAccountComponents:
    """Opaque registry identity. Constructed/copied instances have no entry."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise components.PortfolioComponentError("component_owner_not_constructible")

    def __copy__(self):
        components.deny("component_owner_not_transferable")

    def __deepcopy__(self, memo):
        components.deny("component_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        components.deny("component_owner_not_transferable")


@dataclass(frozen=True, slots=True, repr=False)
class ComponentHandoff:
    packet: capture.DemoAccountPacket
    reference: observed.CaptureReference
    receipt_json: bytes


@dataclass(frozen=True, slots=True, repr=False)
class _RegisteredComponents:
    handoff: ComponentHandoff
    clock_boundary: object


@dataclass(frozen=True, slots=True, repr=False)
class OwnedPortfolioComponentResult:
    receipt_json: bytes
    owner: OwnedAccountComponents | None

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def snapshot(self):
        return None

    @property
    def execution_authority(self):
        return False


def _consume_owned_components(value, *, expected_receipt_sha256, _invocation=None):
    if type(value) is not OwnedAccountComponents:
        components.deny("component_native_owner_required")
    found = _OWNERS.pop(value, None)
    if type(found) is not _RegisteredComponents:
        components.deny("component_native_owner_missing_or_consumed")
    try:
        if (
            type(expected_receipt_sha256) is not str
            or journal.digest(found.handoff.receipt_json) != expected_receipt_sha256
        ):
            components.deny("component_native_owner_missing_or_consumed")
        clock_boundary._consume_boundary(
            found.clock_boundary,
            invocation=_invocation,
            receipt_sha256=expected_receipt_sha256,
        )
    except clock_boundary.AccountClockBoundaryError:
        components.deny("component_native_clock_boundary_denied")
    finally:
        clock_boundary._discard_boundary(found.clock_boundary)
    return found.handoff


async def collect_owned_portfolio_components(
    session,
    *,
    repository,
    journal_repository,
    observation_repository,
    clock,
    barrier_completed_at,
    window,
    expected_revision,
    profile,
    expected_profile_sha256,
    required_drawdown_window_started_at=None,
    expected_policy_sha256=DIAGNOSTIC_POLICY_SHA256,
):
    """Injected-clock diagnostic entry; it always returns owner=None.

    `profile` is the old strictly copied configuration container, restricted to
    exact scope and preregistered correlation assignments; all source/evidence
    fields must be empty. No claim or revision is published. Original durable
    evidence remains if any later step fails, times out or is cancelled.
    """
    try:
        async with asyncio.timeout(30):
            return await _collect(
                session,
                repository,
                journal_repository,
                observation_repository,
                clock,
                barrier_completed_at,
                window,
                expected_revision,
                profile,
                expected_profile_sha256,
                required_drawdown_window_started_at,
                expected_policy_sha256,
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no credential/raw/SQL exception leakage
        raise components.PortfolioComponentError(
            "owned_portfolio_component_capture_failed"
        ) from None


async def _collect(
    session,
    repository,
    journal_repository,
    observation_repository,
    clock,
    barrier,
    window,
    revision,
    profile,
    profile_pin,
    required_window,
    policy_pin,
):
    if (
        type(session) is not ControlledDemoAccountSession
        or type(repository) is not QualificationLedgerRepository
        or type(journal_repository) is not AccountCaptureJournalRepository
        or type(observation_repository) is not AccountObservationIndexRepository
        or repository.session_factory is not journal_repository.session_factory
        or repository.session_factory is not observation_repository.session_factory
        or type(policy_pin) is not str
        or policy_pin != DIAGNOSTIC_POLICY_SHA256
    ):
        components.deny("component_owned_dependencies_invalid")
    profile = mapping.copy_materialization_inputs(profile)
    if (
        type(profile_pin) is not str
        or mapping.materialization_inputs_sha256(profile) != profile_pin
        or profile.instruments
        or profile.costs
        or profile.ledger is not None
        or profile.history is not None
        or profile.peak is not None
    ):
        components.deny("component_configuration_or_caller_evidence_invalid")
    plan = capture._checked_plan(session._plan, session._pin)
    if (profile.environment, profile.account_id, profile.settlement_currency) != (
        plan.environment,
        plan.expected_uid,
        plan.settlement_currency,
    ):
        components.deny("component_profile_scope_mismatch")
    if required_window is not None:
        required_window = capture._utc(required_window)
    observed.window_document(window)
    scope = LedgerScope(
        account_id=plan.expected_uid, settlement_currency=plan.settlement_currency
    )
    started = collector._read_clock(clock)
    await repository.initialize_capture_scope(scope)
    before = await repository.read_portfolio_checkpoint(scope)
    recorded, source_owner = await bootstrap._collect_recorded(
        session,
        repository=repository,
        journal_repository=journal_repository,
        clock=clock,
        barrier_completed_at=barrier,
    )
    if (
        type(source_owner) is not journal._OwnedAccountJournal
        or not session._used
        or source_owner.repository is not journal_repository
        or source_owner.scope != scope
        or source_owner.plan != plan
        or not source_owner.finished
        or not source_owner.acquisition_ok
        or not source_owner.closed_safe
        or not source_owner.finalization_complete
    ):
        components.deny("component_original_owned_capture_required")
    chain = await journal_repository.read_chain(scope, source_owner.capture_id)
    reference = observed.source_reference(chain)
    first = journal.checked_event(chain[0].event)
    if (
        reference.packet_sha256 != source_owner.owned_packet_sha256
        or reference.plan_sha256 != session._pin
        or reference.head_sha256 != source_owner.previous
        or len(chain) != source_owner.sequence
        or first["data"]["invocation_owner_sha256"] != source_owner.owner_sha
        or first["data"]["local_checkpoint_sha256"] != source_owner.checkpoint
    ):
        components.deny("component_owner_journal_binding_mismatch")
    indexed = await observation_repository.append(
        scope, reference, window, expected_revision=revision
    )
    fold = components.MeasuredHWMFold(scope, indexed.sequence, indexed.event_sha256)
    after_cursor = 0
    while after_cursor < indexed.sequence:
        page = await observation_repository.read_balance_source_page(
            scope,
            through_sequence=indexed.sequence,
            expected_head_sha256=indexed.event_sha256,
            after=after_cursor,
        )
        if (
            type(page) is not BalanceSourcePage
            or page.after != after_cursor
            or page.through_sequence != indexed.sequence
            or page.head_sha256 != indexed.event_sha256
            or not page.items
        ):
            components.deny("component_original_population_page_required")
        for item in page.items:
            fold.add(*item)
        after_cursor = fold.sequence
    hwm = json.loads(fold.finish(required_window_started_at=required_window))
    packet = recorded.bootstrap.packet
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=session._pin
    )
    if frozen.sha256 != reference.packet_sha256:
        components.deny("component_original_packet_mismatch")
    derived = metadata.derive_captured_instrument_metadata(
        packet,
        expected_plan_sha256=session._pin,
        expected_packet_sha256=frozen.sha256,
        inputs=profile,
        expected_inputs_sha256=profile_pin,
    )
    spec_gaps = set(derived.blocking_reasons)
    specs = mapping._instruments(derived.inputs, packet.completed_at, spec_gaps)
    rule_pins = {}
    for instrument_id in plan.leverage_instrument_ids:
        rules = derive_captured_instrument_rules(
            packet,
            instrument_id=instrument_id,
            expected_plan_sha256=session._pin,
            expected_packet_sha256=frozen.sha256,
        )
        rule_pins[instrument_id] = rules.receipt_sha256
        if instrument_id not in specs:
            spec_gaps.add("current_contract_risk_spec_missing")
    after = await repository.read_portfolio_checkpoint(scope)
    ended = collector._read_clock(clock)
    if not (
        started
        <= before.observed_at
        <= before.received_at
        <= packet.observations[0].request_started_at
        <= packet.completed_at
        <= after.observed_at
        <= after.received_at
        <= ended
        <= started + timedelta(seconds=30)
    ):
        components.deny("component_owned_clock_or_deadline_invalid")
    current_proof = current.verify_current_account_sources(
        chain, reference=reference, scope=scope, validated_at=ended
    )
    source = json.loads(current_proof.receipt_json)
    local_before, local_after = (
        json.loads(before.document_json),
        json.loads(after.document_json),
    )
    for value in (local_before, local_after):
        if (
            value["environment"],
            value["account_id"],
            value["requested_settlement_currency"],
        ) != (scope.environment, scope.account_id, scope.settlement_currency):
            components.deny("component_local_scope_mismatch")
    local_gaps = set(local_before["blocking_reasons"]) | set(
        local_after["blocking_reasons"]
    )
    if before.state_sha256 != after.state_sha256:
        local_gaps.add("local_storage_changed_during_observation")
    current_measurement = components.observe_balance(
        chain, reference=reference, scope=scope
    )
    measurement = json.loads(current_measurement.source_json)
    native = (
        recorded.bootstrap.transport_provenance == "owned_signed_verified_tls"
        and measurement["recorded_native_transport"]
    )
    hwm_gaps = ["hwm_original_native_clock_attestation_missing"]
    if not hwm["all_sources_recorded_native"]:
        hwm_gaps.append("hwm_original_native_genesis_or_source_missing")
    if hwm["risk_window_matches"] is False:
        hwm_gaps.append("required_drawdown_window_differs_from_measured_genesis")
    completed = {
        "balance_observation_verified": source["balance"]["equity"] is not None
        and source["balance"]["available_equity"] is not None
        and not source["blocking_reasons"],
        "exchange_flat_inventory_verified": source["observed_flat"],
        "local_storage_flat_verified": not local_gaps,
        "captured_contract_risk_specs_verified": not spec_gaps,
        "sampled_hwm_population_verified": hwm["measured_population_complete"],
        "sampled_hwm_original_tls_recorded": hwm["all_sources_recorded_native"],
        "native_sampled_hwm_verified": False,
        "native_current_clock_verified": False,
    }
    final_time = collector._read_clock(clock)
    first_received = min(
        capture._utc(datetime.fromisoformat(page["body_completed_at"]))
        for page in source["current_pages"]
    )
    expires_at = min(
        first_received + timedelta(seconds=30), started + timedelta(seconds=30)
    )
    if final_time < ended or final_time >= expires_at:
        components.deny("component_final_freshness_or_deadline_invalid")
    payload = journal.canonical(
        {
            "schema_version": "ctcc.portfolio_components_diagnostic.v2",
            "policy_sha256": policy_pin,
            "profile_sha256": profile_pin,
            "source_reference": observed.reference_document(reference),
            "current_proof_sha256": current_proof.receipt_sha256,
            "final_checked_at": final_time.isoformat(),
            "component_expires_at": expires_at.isoformat(),
            "local_before_sha256": before.state_sha256,
            "local_after_sha256": after.state_sha256,
            "local_observed_at": before.observed_at.isoformat(),
            "local_received_at": after.received_at.isoformat(),
            "current_balance": source["balance"],
            "measured_hwm": hwm,
            "instrument_rules": rule_pins,
            "instrument_specs": {
                key: value.model_dump(mode="json") for key, value in specs.items()
            },
            "completed_components": completed,
            "current_native_transport_observed": native,
            "current_native_source_observed": False,
            "clock_evidence": {
                "current_utc": "injected_diagnostic_dependency",
                "current_native_attestation": "unknown_not_recorded_in_B1_v1",
                "historical_hwm_native_attestation": "unknown_not_recorded_in_B1_v1",
                "native_clock_proof_sha256": None,
                "current_owner_issued": False,
            },
            "blocking_reasons": sorted(
                set(source["blocking_reasons"])
                | local_gaps
                | spec_gaps
                | set(hwm_gaps)
                | {"current_native_clock_attestation_missing"}
            ),
            "history_start": None,
            "history_end": None,
            "loss_history": None,
            "loss_streak_at_history_start": None,
            "snapshot": None,
            "account_complete": False,
            "account_revision_published": False,
            "unverified": [
                "complete_outcome_window",
                "funding_accrual_and_attribution",
                "actual_streak_seed",
                "durable_Demo_guards",
                "final_atomic_recheck",
                "future_writer_exclusion",
                "current_native_clock_attestation",
                "historical_hwm_native_clock_attestation",
                "same_invocation_native_account_owner",
            ],
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    # There is no reviewed durable native-clock companion issuer in B1/v1.
    # Native TLS and caller UTC ordering cannot supply the missing capability.
    return OwnedPortfolioComponentResult(payload, None)
