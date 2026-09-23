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


async def _collect(session, repository, clock, barrier_completed_at):
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
    # Unlike the execution materializer, raw bootstrap/reconciliation reads must
    # remain available when known local holds exist. They never retire those holds.
    owned = await collector._collect_owned_demo_account_records(
        credentials=session._credentials,
        clock=clock,
        plan=selected,
        expected_plan_sha256=session._pin,
        barrier_completed_at=barrier,
    )
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
