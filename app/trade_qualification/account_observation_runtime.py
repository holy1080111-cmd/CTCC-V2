"""Owned acquisition into the B3 audit index; no execution capability issuer."""

import asyncio
import json

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.database.repositories.account_observation_index_typed import (
    MAX_REVISION,
    AccountObservationTypedIndexRepository,
    TypedObservationBatchReadback,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_query_verifier as history_query
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope


async def collect_observed_execution_window(
    session,
    *,
    repository,
    journal_repository,
    observation_repository,
    clock,
    barrier_completed_at,
    window,
    expected_revision,
):
    """New route preserves every old B1 byte and never accepts replay as owner."""
    try:
        if (
            type(session) is not ControlledDemoAccountSession
            or type(session._plan) is capture.AllProductDemoAccountCapturePlanV6
            or type(observation_repository) is not AccountObservationIndexRepository
            or observation_repository.session_factory
            is not journal_repository.session_factory
        ):
            observed.deny("observation_owned_session_required")
        observed.window_document(window)
        recorded, owner = await bootstrap._collect_recorded(
            session,
            repository=repository,
            journal_repository=journal_repository,
            clock=clock,
            barrier_completed_at=barrier_completed_at,
        )
        if (
            type(owner) is not journal._OwnedAccountJournal
            or not owner.finished
            or not owner.acquisition_ok
            or owner.repository is not journal_repository
        ):
            observed.deny("observation_owned_session_required")
        scope = LedgerScope(
            account_id=session._plan.expected_uid,
            settlement_currency=session._plan.settlement_currency,
        )
        capture_id = json.loads(recorded.journal_receipt_json)["capture_id"]
        chain = await journal_repository.read_chain(scope, capture_id)
        reference = observed.CaptureReference(
            capture_id,
            journal.digest(chain[-1].event.event_json),
            session._pin,
            capture.freeze_demo_account_packet(
                recorded.bootstrap.packet, expected_plan_sha256=session._pin
            ).sha256,
            journal.digest(session._plan.session_binding_id.encode("ascii")),
        )
        return await observation_repository.append(
            scope,
            reference,
            window,
            expected_revision=expected_revision,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- credential, SQL and transport errors stay private
        raise observed.AccountObservationError("observation_runtime_failed") from None


async def collect_observed_execution_window_typed_v1(
    session,
    *,
    repository,
    journal_repository,
    observation_repository,
    clock,
    barrier_completed_at,
    window,
    expected_revision,
):
    """Own one signed B1 history capture and persist its separate typed B3 epoch.

    B1 is durably finalized before a fresh journal read and B2a V2 replay.
    DB0040 independently replays the same original chain and reads its append
    back in another session. A late rejection keeps the B1/DB0040 evidence; it
    never retries the consumed account session or issues account authority.
    """
    try:
        if (
            type(session) is not ControlledDemoAccountSession
            or session._used
            or type(repository) is not QualificationLedgerRepository
            or type(journal_repository) is not AccountCaptureJournalRepository
            or type(observation_repository)
            is not AccountObservationTypedIndexRepository
            or repository.session_factory is not journal_repository.session_factory
            or observation_repository.session_factory
            is not journal_repository.session_factory
            or type(expected_revision) is not int
            or not 0 <= expected_revision < MAX_REVISION
        ):
            observed.deny("observation_typed_owned_session_required")
        selected = capture._checked_plan(session._plan, session._pin)
        if (
            type(selected)
            not in {
                capture.RegionalDemoAccountCapturePlan,
                capture.AllProductDemoAccountCapturePlan,
            }
            or selected.registration_region != "global"
        ):
            observed.deny("observation_typed_history_scope_unsupported")
        observed.window_document(window)
        recorded, owner = await bootstrap._collect_recorded(
            session,
            repository=repository,
            journal_repository=journal_repository,
            clock=clock,
            barrier_completed_at=barrier_completed_at,
        )
        scope = LedgerScope(
            account_id=selected.expected_uid,
            settlement_currency=selected.settlement_currency,
        )
        if (
            type(owner) is not journal._OwnedAccountJournal
            or owner.repository is not journal_repository
            or owner.scope != scope
            or owner.plan != selected
            or not owner.finished
            or not owner.acquisition_ok
            or not owner.closed_safe
            or not owner.finalization_complete
            or not session._used
        ):
            observed.deny("observation_typed_original_owner_required")
        chain = await journal_repository.read_chain(scope, owner.capture_id)
        reference = observed.source_reference(chain)
        if (
            reference.capture_id != owner.capture_id
            or reference.head_sha256 != owner.previous
            or reference.plan_sha256 != session._pin
            or reference.packet_sha256 != owner.owned_packet_sha256
            or reference.session_binding_sha256
            != journal.digest(selected.session_binding_id.encode("ascii"))
            or len(chain) != owner.sequence
            or capture.freeze_demo_account_packet(
                recorded.bootstrap.packet, expected_plan_sha256=session._pin
            ).sha256
            != reference.packet_sha256
        ):
            observed.deny("observation_typed_original_source_mismatch")
        # This is a new DB read after B1 finalization, not a saved verifier
        # result, caller-provided receipt or claim that its queried tail is final.
        history_query.verify_history_query_chain_v2(
            chain, **observed._pins(reference, scope)
        )
        result = await observation_repository.append(
            scope, reference, window, expected_revision=expected_revision
        )
        if type(result) is not TypedObservationBatchReadback:
            observed.deny("observation_typed_commit_readback_required")
        return result
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- B1 remains durable; redact private failures
        raise observed.AccountObservationError(
            "observation_typed_runtime_failed"
        ) from None
