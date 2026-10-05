"""Owned acquisition into the B3 audit index; no execution capability issuer."""

import asyncio
import json

from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
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
