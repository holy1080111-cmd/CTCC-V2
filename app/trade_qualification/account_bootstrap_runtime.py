"""One-use raw account capture from an explicitly unknown DB0017 scope.

No empty portfolio is manufactured, no complete account revision is issued, and
neither an existing nor a freshly created scope grants submission authority.
The response packet is private account evidence; its public receipt omits UIDs.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.database.repositories.qualification_ledger import (
    LedgerBootstrapCheckpoint,
    QualificationLedgerRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_runtime as runtime
from app.trade_qualification import reservations


@dataclass(frozen=True, slots=True, repr=False)
class DemoBootstrapCaptureResult:
    packet: capture.DemoAccountPacket
    ledger_checkpoint: LedgerBootstrapCheckpoint
    completed_at: datetime
    transport_provenance: Literal["owned_signed_verified_tls", "synthetic_transport"]
    blocking_reasons: tuple[str, ...]
    receipt_json: bytes
    receipt_sha256: str

    @property
    def account_complete(self) -> Literal[False]:
        return False

    @property
    def execution_authority(self) -> Literal[False]:
        return False

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"


async def collect_bootstrap(session, *, repository, clock, barrier_completed_at):
    """Fail closed and redact SQL, transport and credential-bearing exceptions."""
    error = "account_runtime_invalid"
    try:
        return await _collect(session, repository, clock, barrier_completed_at)
    except asyncio.CancelledError:
        raise
    except runtime.AccountRuntimeError as exc:
        if (
            type(exc) is runtime.AccountRuntimeError
            and len(exc.args) == 1
            and type(exc.args[0]) is str
            and exc.args[0] in runtime._ERRORS
        ):
            error = exc.args[0]
    except Exception:  # noqa: BLE001, S110 -- never emit a private exception
        pass
    raise runtime.AccountRuntimeError(error)


async def _collect(
    session,
    repository,
    clock,
    barrier_completed_at,
    *,
    _journal_repository=None,
    _journal_slot=None,
    _native_observer=None,
):
    if type(session) is not runtime.ControlledDemoAccountSession:
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    if session._used:
        raise runtime.AccountRuntimeError("account_session_already_used")
    session._used = True
    if type(repository) is not QualificationLedgerRepository:
        raise runtime.AccountRuntimeError("owned_qualification_repository_required")
    selected = capture._checked_plan(session._plan, session._pin)
    barrier = capture._utc(barrier_completed_at)
    start = collector._read_clock(clock)
    if start <= barrier or start < selected.created_at:
        raise runtime.AccountRuntimeError("runtime_clock_invalid")
    scope = reservations.LedgerScope(
        account_id=selected.expected_uid,
        settlement_currency=selected.settlement_currency,
    )
    before = await repository.initialize_capture_scope(scope)
    session._check_bootstrap_checkpoint(before, scope)
    if before.observed_at < start:
        raise runtime.AccountRuntimeError("runtime_clock_invalid")
    journal = None
    if _journal_repository is not None:
        from app.trade_qualification.account_capture_journal import start_owned_journal

        journal = await start_owned_journal(
            repository=_journal_repository,
            scope=scope,
            plan=selected,
            checkpoint=before.state_sha256,
            clock=clock,
            tokens=collector._credential_values(session._credentials)[:3],
        )
        _journal_slot.append(journal)
    # Unlike the execution materializer, raw bootstrap/reconciliation reads must
    # remain available when known local holds exist. They never retire those holds.
    arguments = {
        "credentials": session._credentials,
        "clock": clock,
        "plan": selected,
        "expected_plan_sha256": session._pin,
        "barrier_completed_at": barrier,
    }
    if journal is not None:
        arguments["_journal"] = journal
    if _native_observer is not None:
        arguments["_native_observer"] = _native_observer
    owned = await collector._collect_owned_demo_account_records(**arguments)
    if type(owned) is not collector._OwnedAccountCapture:
        raise runtime.AccountRuntimeError("owned_capture_required")
    frozen = capture.freeze_demo_account_packet(
        owned.packet, expected_plan_sha256=session._pin
    )
    packet = capture.verify_demo_account_packet(
        frozen.payload,
        expected_sha256=frozen.sha256,
        expected_plan_sha256=session._pin,
    )
    after = await repository.read_bootstrap_checkpoint(scope)
    session._check_bootstrap_checkpoint(after, scope)
    if before.state_sha256 != after.state_sha256:
        raise runtime.AccountRuntimeError("ledger_revision_changed_during_capture")
    if not (
        before.received_at
        <= packet.observations[0].request_started_at
        <= packet.completed_at
        <= owned.completed_at
        <= after.observed_at
    ):
        raise runtime.AccountRuntimeError("runtime_clock_invalid")
    proofs = owned.peer_certificate_sha256
    if (
        type(proofs) is not tuple
        or len(proofs) != len(packet.observations)
        or any(
            item is not None
            and (
                type(item) is not str
                or len(item) != 64
                or any(char not in "0123456789abcdef" for char in item)
            )
            for item in proofs
        )
    ):
        raise runtime.AccountRuntimeError("owned_capture_provenance_invalid")
    transport = (
        "owned_signed_verified_tls"
        if all(item is not None for item in proofs)
        else "synthetic_transport"
    )
    gaps = {
        "bootstrap_source_verification_required",
        "registration_provenance_unverified",
    }
    if not before.account_initialized:
        gaps.add("account_claims_not_initialized")
    if before.state.active:
        gaps.add("unresolved_local_holds")
    if transport == "synthetic_transport":
        gaps.add("synthetic_transport_not_authenticated")
    finished = collector._read_clock(clock)
    collector._check_batch(
        finished, start, after.received_at, selected.max_batch_seconds
    )
    receipt = capture._canonical(
        {
            "schema_version": "ctcc.demo_account_bootstrap_capture.v1",
            "environment": "demo",
            "plan_sha256": session._pin,
            "packet_sha256": frozen.sha256,
            "ledger_checkpoint_sha256": before.state_sha256,
            "account_revision": before.state.account_revision,
            "ledger_revision": before.state.ledger_revision,
            "local_account_initialized": before.account_initialized,
            "scope_active_hold_count": len(before.state.active),
            "transport_provenance": transport,
            "peer_certificate_sha256": proofs,
            "completed_at": finished.isoformat(),
            "blocking_reasons": sorted(gaps),
            "account_complete": False,
            "account_revision_published": False,
            "admission": "DENY",
            "execution_authority": False,
        }
    ).encode("utf-8")
    return DemoBootstrapCaptureResult(
        packet,
        before,
        finished,
        transport,
        tuple(sorted(gaps)),
        receipt,
        hashlib.sha256(receipt).hexdigest(),
    )


@dataclass(frozen=True, slots=True, repr=False)
class RecordedBootstrapCaptureResult:
    """Private bootstrap result plus a public-safe audit persistence receipt."""

    bootstrap: DemoBootstrapCaptureResult
    journal_receipt_json: bytes

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


async def collect_bootstrap_recorded(
    session, *, repository, journal_repository, clock, barrier_completed_at
):
    """Same one-use owned acquisition; no packet/transport flag accepted as input."""
    result, _ = await _collect_recorded(
        session,
        repository=repository,
        journal_repository=journal_repository,
        clock=clock,
        barrier_completed_at=barrier_completed_at,
    )
    return result


async def _collect_recorded(
    session,
    *,
    repository,
    journal_repository,
    clock,
    barrier_completed_at,
    _native_observer=None,
):
    """Internal owner survives finalization; legacy public result stays unchanged."""
    from app.database.repositories.account_capture_journal import (
        AccountCaptureJournalRepository,
    )
    from app.trade_qualification.account_capture_journal import bounded_finalization

    if type(journal_repository) is not AccountCaptureJournalRepository:
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    journals, result, failure, cancelled = [], None, None, False
    try:
        result = await _collect(
            session,
            repository,
            clock,
            barrier_completed_at,
            _journal_repository=journal_repository,
            _journal_slot=journals,
            **(
                {}
                if _native_observer is None
                else {"_native_observer": _native_observer}
            ),
        )
    except asyncio.CancelledError:
        cancelled = True
        failure = asyncio.CancelledError()
    except Exception:  # noqa: BLE001 -- no private SQL/HTTP/credential exception escapes
        failure = RuntimeError("capture_failed")
    receipt = None
    if journals:
        try:
            finalization = journals[0].finish(result=result, error=failure)
            if _native_observer is not None:
                from app.trade_qualification.account_native_clock import (
                    _finalization_awaitable,
                )

                finalization = _finalization_awaitable(
                    _native_observer, journals[0], finalization
                )
            receipt = await bounded_finalization(finalization)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- durable events survive failed final receipt
            result = None
    if cancelled:
        raise asyncio.CancelledError
    if result is None or receipt is None:
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    public_receipt = None
    try:
        public_receipt = receipt.receipt_json
    except Exception:  # noqa: BLE001, S110 -- late rendering cannot leak private state
        pass
    if public_receipt is None:
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    return RecordedBootstrapCaptureResult(result, public_receipt), journals[0]


@dataclass(frozen=True, slots=True, repr=False)
class QueryVerifiedBootstrapCaptureResult:
    """New private observation result; historical replay never becomes authority."""

    recorded: RecordedBootstrapCaptureResult
    query_verification: object
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


async def collect_bootstrap_query_verified(
    session, *, repository, journal_repository, clock, barrier_completed_at
):
    """Owned acquisition -> B1 finalization -> separate read -> B2a byte replay.

    Accepts the original controlled session only. No BootstrapArtifact, packet,
    caller coverage boolean, result DTO or current-authority claim is an input.
    """
    from app.trade_qualification import account_capture_journal as journal
    from app.trade_qualification import account_history_query_verifier as verifier

    if type(session) is not runtime.ControlledDemoAccountSession:
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    if session._used:
        raise runtime.AccountRuntimeError("account_session_already_used")
    selected = capture._checked_plan(session._plan, session._pin)
    if (
        type(selected)
        not in {
            capture.RegionalDemoAccountCapturePlan,
            capture.AllProductDemoAccountCapturePlan,
        }
        or selected.registration_region != "global"
    ):
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    recorded, owner = await _collect_recorded(
        session,
        repository=repository,
        journal_repository=journal_repository,
        clock=clock,
        barrier_completed_at=barrier_completed_at,
    )
    result = None
    try:
        # This carrier was bound to the actual collector before the old DTO was
        # constructed. Readback cannot create a new live owner or session.
        if (
            type(owner) is not journal._OwnedAccountJournal
            or not owner.finished
            or not owner.acquisition_ok
            or owner.repository is not journal_repository
        ):
            raise runtime.AccountRuntimeError("account_runtime_invalid")
        chain = await journal_repository.read_chain(owner.scope, owner.capture_id)
        verified = verifier.verify_history_query_chain(
            chain,
            expected_head_sha256=owner.previous,
            expected_plan_sha256=session._pin,
            expected_packet_sha256=owner.owned_packet_sha256,
            expected_account_id=selected.expected_uid,
            expected_settlement_currency=selected.settlement_currency,
        )
        receipt = journal.canonical(
            {
                "schema_version": "ctcc.demo_bootstrap_query_observation.v1",
                "capture_id": owner.capture_id,
                "journal_head_sha256": owner.previous,
                "plan_sha256": session._pin,
                "packet_sha256": owner.owned_packet_sha256,
                "query_verification_sha256": verified.receipt_sha256,
                "policy_sha256": verifier.POLICY_SHA256,
                "state": "recorded_generation_time_query_verified",
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        collector._no_secrets(receipt, owner.tokens)
        collector._no_secret_json(receipt, owner.tokens)
        result = QueryVerifiedBootstrapCaptureResult(recorded, verified, receipt)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001, S110 -- preserve journal; no private error logging
        pass
    if result is None:
        raise runtime.AccountRuntimeError("account_runtime_invalid")
    return result
