"""Initial native Demo account source, current-only; no portfolio/order authority."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import PosixPath, WindowsPath
from weakref import WeakKeyDictionary

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
    LockedAccountSourceJoinReadback,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import (
    canonical,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_time_probe as time_source
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope

_CAPTURES = WeakKeyDictionary()


class _TimeProbeTrace:
    """Original existing-public-fetch phase sink, never an issuer or ledger."""

    def __init__(self, stage, plan, phase):
        state = native._state(stage)
        if type(phase) is not str or phase not in {"before", "after"}:
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        self.stage, self.plan, self.phase = stage, plan, phase
        self.invocation_sha256 = sha(canonical([state["invocation_id"], phase]))
        self.scope_sha256 = state["scope_sha256"]
        self.events, self.body = [], bytearray()
        self.observed_bytes, self.receipt_sha256 = 0, None
        self.accepted = self.closed = self.finished = self.truncated = False

    def _append(self, kind, stamp, data, *, cleanup=False):
        state = native._state(self.stage, _durable_cleanup=cleanup)
        if len(self.events) >= 500:
            raise proof.NativeAccountProofError("native_account_time_trace_bound")
        if stamp is not None:
            validate_stamps((state["started"], state["last"], stamp))
            state["last"] = stamp
        event = {
            "index": len(self.events),
            "kind": kind,
            "request_index": 0,
            "stamp": stamp,
            "data": data,
            "previous_sha256": None
            if not self.events
            else sha(canonical(self.events[-1])),
        }
        self.events.append(event)

    def begin_request(self, *, endpoint, query, stamp):
        state = native._state(self.stage)
        proof._stamp(stamp)
        if (
            self.events
            or type(endpoint) is not str
            or endpoint != "/api/v5/public/time"
            or type(query) is not tuple
            or query
        ):
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        if state["clock_results"].get(self.phase) != "accepted":
            raise proof.NativeAccountProofError("native_account_host_clock_rejected")
        if "current_deadline" in state and (
            stamp["monotonic_ns"] >= state["current_deadline"]
            or utc_from_ns(stamp["utc_ns"]) >= state["current_expiry"]
        ):
            raise proof.NativeAccountProofError("native_account_source_expired")
        self._append("request_start", stamp, {"endpoint": endpoint, "query": []})

    def headers(self, *, stamp, status, headers, tls, truncated):
        if (
            len(self.events) != 1
            or type(status) is not int
            or type(headers) is not tuple
            or type(tls) is not dict
            or type(truncated) is not bool
        ):
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        if any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(value) is not str for value in pair)
            or pair[0]
            not in {"content-type", "content-length", "content-encoding", "date"}
            for pair in headers
        ):
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        canonical(tls)  # rejects opaque callback/serializer-bearing input
        self.truncated |= truncated
        self._append(
            "headers_received",
            stamp,
            {
                "status": status,
                "safe_headers": headers,
                "tls": tls,
                "truncated": truncated,
            },
        )

    def chunk(self, chunk, stamp):
        if (
            not self.events
            or self.events[-1]["kind"] not in {"headers_received", "chunk"}
            or type(chunk) is not bytes
        ):
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        kept = chunk[: max(0, self.plan.max_response_bytes - len(self.body))]
        self.observed_bytes += len(chunk)
        self.truncated |= len(kept) != len(chunk)
        self.body.extend(kept)
        self._append(
            "chunk",
            stamp,
            {
                "chunk_index": sum(event["kind"] == "chunk" for event in self.events),
                "observed_bytes": len(chunk),
                "observed_sha256": sha(chunk),
                "retained_bytes": len(kept),
                "retained_sha256": sha(kept),
                "truncated": len(kept) != len(chunk),
            },
        )

    def body_complete(self, stamp):
        if not self.events or self.events[-1]["kind"] not in {
            "headers_received",
            "chunk",
        }:
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        self._append(
            "body_complete",
            stamp,
            {"body_bytes": len(self.body), "body_sha256": sha(bytes(self.body))},
        )

    def finish_request(self, *, accepted, validation_complete, error_code, cleanup):
        if (
            self.finished
            or type(accepted) is not bool
            or type(error_code) is not str
            or re.fullmatch(r"[a-z0-9_]{1,96}", error_code) is None
            or type(cleanup) is not str
            or cleanup not in {"closed", "failed", "not_returned"}
        ):
            raise proof.NativeAccountProofError("native_account_time_trace_invalid")
        self.finished = True
        if validation_complete is not None:
            self._append("validation_complete", validation_complete, {}, cleanup=True)
        sample = None
        try:
            sample = native._sample(self.stage)
        except Exception:  # noqa: BLE001 -- absent closure clock remains absent
            accepted = False
        self._append(
            "response_closed" if cleanup == "closed" else "request_failed",
            sample,
            {"accepted": accepted, "cleanup": cleanup, "error_code": error_code},
            cleanup=True,
        )
        self.accepted = accepted and cleanup == "closed" and not self.truncated

    def client_closed(self, successful):
        sample = None
        try:
            sample = native._sample(self.stage)
        except Exception:  # noqa: BLE001 -- never fabricate a missing sample
            successful = False
        self.closed = successful
        self._append("client_closed", sample, {"successful": successful}, cleanup=True)

    def encoded(self, retention):
        return canonical(
            {
                "schema_version": "ctcc.demo_account_exchange_time_trace.v2",
                "phase": self.phase,
                "plan_sha256": self.plan.canonical_sha256(),
                "invocation_sha256": self.invocation_sha256,
                "scope_sha256": self.scope_sha256,
                "events": self.events,
                "observed_bytes": self.observed_bytes,
                "retained_bytes": len(self.body),
                "raw_sha256": sha(bytes(self.body)),
                "raw_retention": retention,
                "probe_receipt_sha256": self.receipt_sha256,
                "complete": self.accepted
                and self.closed
                and retention == "durable_secret_checked",
                "execution_authority": False,
            }
        )


def _configured_factory(factory):
    if (
        type(factory) is not async_sessionmaker
        or factory.class_ is not AsyncSession
        or type(factory.kw) is not dict
    ):
        return False
    values = factory.kw
    engine = values.get("bind")
    if (
        set(values) != {"bind", "autoflush", "expire_on_commit"}
        or values["autoflush"] is not False
        or values["expire_on_commit"] is not False
        or type(engine) is not AsyncEngine
        or engine.url.drivername != "postgresql+asyncpg"
    ):
        return False
    from app.database.session import AsyncSessionFactory
    from app.database.session import engine as configured_engine

    return factory is AsyncSessionFactory and engine is configured_engine


class _CapturedCurrentNativeAccount(native._InitialAccountStage):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class _CurrentNativeHandoff:
    packet: object
    reference: object
    receipt_json: bytes
    boundary: object
    invocation: object


@dataclass(frozen=True, slots=True, repr=False)
class InitialNativeAccountDiagnostic:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def owner(self):
        return None

    @property
    def snapshot(self):
        return None

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class NativeCurrentHistoryJoinDiagnostic:
    """One native current source and one locked recorded history read, no claims."""

    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def owner(self):
        return None

    @property
    def snapshot(self):
        return None

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False


async def _time_probe(stage, plan, *, directory, phase, tokens):
    """Original fixed native GET/bytes/time semantics; no fallback or retries."""
    native._state(stage)
    trace = _TimeProbeTrace(stage, plan, phase)
    client = time_source._new_client()
    cancelled, failure, receipt, raw = False, None, None, None
    try:
        # Existing guard checks verify, no mounts/proxy/redirect/hooks/credentials.
        if not time_source._client_guard(client, first=True):
            raise proof.NativeAccountProofError("native_account_time_tls_required")
        receipt, raw = await time_source._fetch_time(client, plan, attempt=trace)
        if receipt.transport_origin != "owned_native_tls":
            raise proof.NativeAccountProofError("native_account_time_tls_required")
        if raw != bytes(trace.body):
            raise proof.NativeAccountProofError("native_account_time_trace_raw_changed")
        trace.receipt_sha256 = sha(receipt.canonical_bytes())
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- preserve safe observed bytes before denial
        cancelled = isinstance(exc, asyncio.CancelledError)
        failure = exc
    finally:
        closed = False
        if type(client) is httpx.AsyncClient:
            try:
                await time_source._close(client, cancelled=cancelled)
                closed = client.is_closed
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- retain earlier evidence
                failure = failure or exc
        trace.client_closed(closed)
    retained = bytes(trace.body)
    retention = "not_received" if not retained else "withheld_unverifiable_partial"
    if retained:
        try:
            _secret_checked(retained, tokens)
            storage._publish_part(directory, f"exchange-{phase}.raw", retained)
            retention = "durable_secret_checked"
        except collector.AccountCollectionError:
            retention = "withheld_secret"
        except (json.JSONDecodeError, UnicodeError):
            retention = "withheld_unverifiable_partial"
    if receipt is not None and failure is None:
        _secret_checked(receipt.canonical_bytes(), tokens)
        storage._publish_part(
            directory, f"exchange-{phase}.json", receipt.canonical_bytes()
        )
    encoded = trace.encoded(retention)
    _secret_checked(encoded, tokens)
    storage._publish_part(directory, f"exchange-{phase}-trace.json", encoded)
    if failure is not None:
        if cancelled or isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError
        raise proof.NativeAccountProofError(
            "native_account_time_probe_rejected"
        ) from None
    if not trace.accepted or not trace.closed or retention != "durable_secret_checked":
        raise proof.NativeAccountProofError("native_account_time_probe_incomplete")
    native._state(stage)
    return receipt.canonical_bytes(), raw, encoded


def _secret_checked(raw, tokens):
    collector._no_secrets(raw, tokens)
    collector._no_secret_json(raw, tokens)


async def _recheck_original_db_chain(
    repository, scope, original, reference, packet, *, proof_schema
):
    """Reread the original journal after file readback, before carrier issuance.

    The repository takes the exact-UID lock in a fresh read-only transaction.
    Receipt timestamps may advance, but original DB timestamps and all event,
    raw-body and packet bytes must remain identical. This is only an original
    source continuity check; it does not prove a complete account snapshot.
    """
    if (
        type(repository) is not AccountCaptureJournalRepository
        or type(original) is not tuple
        or not original
        or type(reference) is not observed.CaptureReference
        or type(packet) is not capture.DemoAccountPacket
    ):
        raise proof.NativeAccountProofError(
            "native_account_original_source_recheck_invalid"
        )
    confirmed = await repository.read_chain(scope, reference.capture_id)
    if type(confirmed) is not tuple or len(confirmed) != len(original):
        raise proof.NativeAccountProofError(
            "native_account_original_source_recheck_changed"
        )
    for earlier, later in zip(original, confirmed, strict=True):
        if (
            type(earlier) is not journal.JournalReadback
            or type(later) is not journal.JournalReadback
        ):
            raise proof.NativeAccountProofError(
                "native_account_original_source_recheck_changed"
            )
        journal.JournalReadback(later.event, later.db_recorded_at, later.readback_at)
        if (
            earlier.event.event_json != later.event.event_json
            or earlier.event.raw_body != later.event.raw_body
            or earlier.event.packet_payload != later.event.packet_payload
            or earlier.db_recorded_at != later.db_recorded_at
            or later.readback_at < earlier.readback_at
        ):
            raise proof.NativeAccountProofError(
                "native_account_original_source_recheck_changed"
            )
    reread_reference, reread_packet, _, _ = proof._source(
        confirmed, scope, proof_schema=proof_schema
    )
    if reread_reference != reference or reread_packet != packet:
        raise proof.NativeAccountProofError(
            "native_account_original_source_recheck_changed"
        )


async def _capture_initial_current(stage, session, session_factory, root):
    """Only an active exact initial issuer reaches this actual acquisition."""
    state = native._state(stage)
    owned_clock = state["clock"]
    repository = QualificationLedgerRepository(session_factory, clock=owned_clock)
    journal_repository = AccountCaptureJournalRepository(
        session_factory, clock=owned_clock
    )
    selected = capture._checked_plan(session._plan, session._pin)
    proof_schema, proof_policy_sha256 = proof.contract_for_plan(selected)
    scope = LedgerScope(
        account_id=selected.expected_uid,
        settlement_currency=selected.settlement_currency,
    )
    if state["plan_sha256"] != session._pin or state[
        "scope_sha256"
    ] != proof.scope_sha256(scope):
        raise proof.NativeAccountProofError("native_account_initial_session_mismatch")
    plan = proof._exchange_plan(state["started"])
    files = {}
    with storage._companion_attempt(root) as directory:
        files["host-before.json"] = native._host_observation(stage, "before")
        before_tokens = collector._credential_values(session._credentials)[:3]
        _secret_checked(files["host-before.json"], before_tokens)
        storage._publish_part(directory, "host-before.json", files["host-before.json"])
        if state["clock_results"].get("before") != "accepted":
            raise proof.NativeAccountProofError("native_account_host_clock_rejected")
        files["exchange-plan.json"] = plan.canonical_bytes()
        _secret_checked(files["exchange-plan.json"], before_tokens)
        storage._publish_part(
            directory, "exchange-plan.json", files["exchange-plan.json"]
        )
        (
            files["exchange-before.json"],
            files["exchange-before.raw"],
            files["exchange-before-trace.json"],
        ) = await _time_probe(
            stage, plan, directory=directory, phase="before", tokens=before_tokens
        )
        recorded, owner = await bootstrap._collect_recorded(
            session,
            repository=repository,
            journal_repository=journal_repository,
            clock=owned_clock,
            barrier_completed_at=utc_from_ns(state["started"]["utc_ns"]),
            _native_observer=state["observer"],
        )
        native._state(stage)
        if (
            type(owner) is not journal._OwnedAccountJournal
            or not owner.finished
            or not owner.acquisition_ok
            or not owner.closed_safe
            or owner.repository is not journal_repository
            or owner is not state.get("journal")
            or recorded.bootstrap.transport_provenance != "owned_signed_verified_tls"
            or not state["closed"]
        ):
            raise proof.NativeAccountProofError(
                "native_account_original_source_required"
            )
        first_current = next(
            w["stamp"]
            for w in state["witnesses"]
            if w["phase"] == "response_closed"
            and w["stream"] in current.CURRENT_STREAMS
        )
        deadline = first_current["monotonic_ns"] + boundary.MAX_LIFETIME_NS
        expires = utc_from_ns(first_current["utc_ns"] + boundary.MAX_LIFETIME_NS)
        state["current_deadline"], state["current_expiry"] = deadline, expires
        # Clock probes after the source closes must also finish within its
        # original current-data freshness, never issue a later renewed lease.
        files["host-after.json"] = native._host_observation(stage, "after")
        _secret_checked(files["host-after.json"], owner.tokens)
        storage._publish_part(directory, "host-after.json", files["host-after.json"])
        if state["clock_results"].get("after") != "accepted":
            raise proof.NativeAccountProofError("native_account_host_clock_rejected")
        (
            files["exchange-after.json"],
            files["exchange-after.raw"],
            files["exchange-after-trace.json"],
        ) = await _time_probe(
            stage, plan, directory=directory, phase="after", tokens=owner.tokens
        )
        chain = await journal_repository.read_chain(owner.scope, owner.capture_id)
        reference, packet, records, joins = proof._source(
            chain, scope, proof_schema=proof_schema
        )
        if (
            reference.head_sha256 != owner.previous
            or reference.packet_sha256 != owner.owned_packet_sha256
            or reference.capture_id != owner.capture_id
            or reference.plan_sha256 != session._pin
        ):
            raise proof.NativeAccountProofError(
                "native_account_original_source_readback_changed"
            )
        readback = native._sample(stage)
        persisted = native._sample(stage)
        if (
            persisted["monotonic_ns"] >= deadline
            or utc_from_ns(persisted["utc_ns"]) >= expires
        ):
            raise proof.NativeAccountProofError("native_account_source_expired")
        document = {
            "schema_version": proof_schema,
            "policy_sha256": proof_policy_sha256,
            "scope_sha256": proof.scope_sha256(scope),
            "source_reference": observed.reference_document(reference),
            "journal_terminal_sequence": len(records),
            "journal_owner_sha256": owner.owner_sha,
            "local_checkpoint_sha256": owner.checkpoint,
            "stage": {
                "kind": "initial_account_only",
                "invocation_id": state["invocation_id"],
                "started": state["started"],
            },
            "witnesses": state["witnesses"],
            "source_joins": joins,
            "clock_file_sha256": {name: sha(files[name]) for name in proof.FILES},
            "finalization_witnesses": state["finalization_witnesses"],
            "source_readback_complete": readback,
            "proof_persist_start": persisted,
            "expires_at": expires.isoformat(),
            "monotonic_deadline_ns": deadline,
            "historical_hwm_clock_verified": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        raw = canonical(document)
        _secret_checked(raw, owner.tokens)
        readback_pin = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    # New root handle, original B1 separate DB chain, full raw/source/proof replay.
    replay, readback_receipt = storage.read_native_account_companion(
        root,
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(raw),
        expected_readback_sha256=readback_pin,
    )
    await _recheck_original_db_chain(
        journal_repository,
        scope,
        chain,
        reference,
        packet,
        proof_schema=proof_schema,
    )
    issue = native._sample(stage)
    validate_stamps(
        (proof._canonical_document(readback_receipt)["readback_complete"], issue)
    )
    if (
        issue["monotonic_ns"] >= deadline
        or utc_from_ns(issue["utc_ns"]) >= expires
        or replay.packet != packet
    ):
        raise proof.NativeAccountProofError("native_account_source_expired")
    receipt = canonical(
        {
            "schema_version": (
                "ctcc.initial_native_account_diagnostic.v1"
                if proof_schema == proof.SCHEMA
                else "ctcc.initial_native_account_diagnostic.v2"
            ),
            "policy_sha256": proof_policy_sha256,
            "source_reference": observed.reference_document(reference),
            "proof_sha256": sha(raw),
            "proof_readback_sha256": readback_pin,
            "current_source_receipt_sha256": sha(replay.current_source_receipt_json),
            "current_native_source_observed": True,
            "native_sampled_hwm_verified": False,
            "observed_at": utc_from_ns(issue["utc_ns"]).isoformat(),
            "expires_at": expires.isoformat(),
            "snapshot": None,
            "account_complete": False,
            "account_revision_published": False,
            "flat_start_permission": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    _secret_checked(receipt, owner.tokens)
    # This is the sole minting path, reachable only after actual private source,
    # original phase completeness, current admission and durable separate reads.
    fence = object.__new__(boundary._AccountClockBoundary)
    boundary._BOUNDARIES[fence] = boundary._NativeClockRegistration(
        boundary._NATIVE_ISSUER,
        state["invocation"],
        state["parent"],
        state["loop"],
        state["pid"],
        state["thread"],
        sha(receipt),
        proof_schema,
        sha(raw),
        canonical(issue),
        expires,
        deadline,
    )
    carrier = object.__new__(_CapturedCurrentNativeAccount)
    _CAPTURES[carrier] = _CurrentNativeHandoff(
        packet, reference, receipt, fence, state["invocation"]
    )
    return carrier


def _consume_current_native_capture(carrier, *, invocation, expected_receipt_sha256):
    if type(carrier) is not _CapturedCurrentNativeAccount:
        raise proof.NativeAccountProofError("native_account_current_carrier_required")
    handoff = _CAPTURES.pop(carrier, None)
    if type(handoff) is not _CurrentNativeHandoff:
        raise proof.NativeAccountProofError(
            "native_account_current_carrier_unavailable"
        )
    try:
        if (
            type(expected_receipt_sha256) is not str
            or sha(handoff.receipt_json) != expected_receipt_sha256
            or handoff.invocation is not invocation
        ):
            raise proof.NativeAccountProofError(
                "native_account_current_carrier_binding_invalid"
            )
        boundary._consume_boundary(
            handoff.boundary,
            invocation=invocation,
            receipt_sha256=expected_receipt_sha256,
        )
        return handoff
    except proof.NativeAccountProofError:
        raise
    except Exception:  # noqa: BLE001 -- fixed source/current boundary rejection
        raise proof.NativeAccountProofError(
            "native_account_current_carrier_denied"
        ) from None
    finally:
        boundary._discard_boundary(handoff.boundary)


async def capture_initial_native_account(session, *, session_factory, proof_root):
    """Read-only initial diagnostic. No clock, publication barrier or DTO input."""
    if (
        type(session) is not ControlledDemoAccountSession
        or session._used
        or not _configured_factory(session_factory)
        or type(proof_root) not in (PosixPath, WindowsPath)
        or not proof_root.is_absolute()
    ):
        raise proof.NativeAccountProofError("native_account_initial_inputs_invalid")
    selected = capture._checked_plan(session._plan, session._pin)
    if (
        type(selected)
        not in {
            capture.RegionalDemoAccountCapturePlan,
            capture.AllProductDemoAccountCapturePlan,
            capture.CurrentDemoAccountCapturePlanV6,
        }
        or selected.registration_region != "global"
        or selected.settlement_currency != "USDT"
    ):
        raise proof.NativeAccountProofError("native_account_initial_scope_unsupported")
    scope = LedgerScope(
        account_id=selected.expected_uid,
        settlement_currency=selected.settlement_currency,
    )
    carrier = None
    try:
        with native._initial_stage(
            plan_sha256=session._pin, scope_sha256=proof.scope_sha256(scope)
        ) as stage:
            state = native._state(stage)
            carrier = await _capture_initial_current(
                stage, session, session_factory, proof_root
            )
            pin = sha(_CAPTURES[carrier].receipt_json)
            handoff = _consume_current_native_capture(
                carrier, invocation=state["invocation"], expected_receipt_sha256=pin
            )
            return InitialNativeAccountDiagnostic(handoff.receipt_json)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- preserve all original evidence, no private errors
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from None
        return InitialNativeAccountDiagnostic(
            canonical(
                {
                    "schema_version": (
                        "ctcc.initial_native_account_diagnostic.v2"
                        if type(selected) is capture.CurrentDemoAccountCapturePlanV6
                        else "ctcc.initial_native_account_diagnostic.v1"
                    ),
                    "policy_sha256": (
                        proof.V3_POLICY_SHA256
                        if type(selected) is capture.CurrentDemoAccountCapturePlanV6
                        else proof.POLICY_SHA256
                    ),
                    "code": "native_account_initial_denied",
                    "current_native_source_observed": False,
                    "native_sampled_hwm_verified": False,
                    "snapshot": None,
                    "account_complete": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
        )
    finally:
        session._used = True
        if carrier is not None:
            stale = _CAPTURES.pop(carrier, None)
            if stale is not None:
                boundary._discard_boundary(stale.boundary)


async def capture_native_current_history_join(
    session, *, session_factory, proof_root, history_capture_id
):
    """Join a native v6 current capture to a recorded v5 history under one call.

    The history ID is only a locator. The original B1 chains, exact session and
    local checkpoint are reread under the UID lock. The native carrier is burned
    in the same task after that read, within its original current-data lease.
    This diagnostic cannot publish a risk snapshot or execution authority.
    """
    if (
        type(session) is not ControlledDemoAccountSession
        or session._used
        or not _configured_factory(session_factory)
        or type(proof_root) not in (PosixPath, WindowsPath)
        or not proof_root.is_absolute()
        or type(history_capture_id) is not str
        or re.fullmatch(r"[a-f0-9]{32}", history_capture_id) is None
    ):
        raise proof.NativeAccountProofError(
            "native_account_history_join_inputs_invalid"
        )
    selected = capture._checked_plan(session._plan, session._pin)
    if (
        type(selected) is not capture.CurrentDemoAccountCapturePlanV6
        or selected.registration_region != "global"
        or selected.settlement_currency != "USDT"
    ):
        raise proof.NativeAccountProofError(
            "native_account_history_join_scope_unsupported"
        )
    scope = LedgerScope(
        account_id=selected.expected_uid,
        settlement_currency=selected.settlement_currency,
    )
    carrier = None
    try:
        with native._initial_stage(
            plan_sha256=session._pin, scope_sha256=proof.scope_sha256(scope)
        ) as stage:
            state = native._state(stage)
            carrier = await _capture_initial_current(
                stage, session, session_factory, proof_root
            )
            pending = _CAPTURES.get(carrier)
            if (
                type(pending) is not _CurrentNativeHandoff
                or pending.invocation is not state["invocation"]
            ):
                raise proof.NativeAccountProofError(
                    "native_account_history_join_carrier_unavailable"
                )
            repository = AccountCaptureJournalRepository(
                session_factory, clock=state["clock"]
            )
            before_lock = native._sample(stage)
            deadline_ns = state.get("current_deadline")
            if (
                type(deadline_ns) is not int
                or deadline_ns <= before_lock["monotonic_ns"]
            ):
                raise proof.NativeAccountProofError(
                    "native_account_history_join_lease_expired"
                )
            remaining = (deadline_ns - before_lock["monotonic_ns"]) / 1_000_000_000
            async with asyncio.timeout(remaining):
                locked = await repository.read_locked_current_history_join(
                    scope,
                    history_capture_id=history_capture_id,
                    current_capture_id=pending.reference.capture_id,
                )
            if type(locked) is not LockedAccountSourceJoinReadback:
                raise proof.NativeAccountProofError(
                    "native_account_history_join_readback_invalid"
                )
            locked_value = json.loads(locked.receipt_json)
            native_value = json.loads(pending.receipt_json)
            current_reference = observed.reference_document(pending.reference)
            if (
                locked_value.get("schema_version")
                != "ctcc.demo_account_locked_source_join.v2"
                or locked_value.get("current_source_reference") != current_reference
                or native_value.get("source_reference") != current_reference
                or native_value.get("schema_version")
                != "ctcc.initial_native_account_diagnostic.v2"
                or locked_value.get("admission") != "DENY"
                or locked_value.get("snapshot") is not None
                or locked_value.get("account_complete") is not False
                or locked_value.get("execution_authority") is not False
                or native_value.get("admission") != "DENY"
                or native_value.get("snapshot") is not None
                or native_value.get("account_complete") is not False
                or native_value.get("execution_authority") is not False
            ):
                raise proof.NativeAccountProofError(
                    "native_account_history_join_source_mismatch"
                )
            consumed = _consume_current_native_capture(
                carrier,
                invocation=state["invocation"],
                expected_receipt_sha256=sha(pending.receipt_json),
            )
            if consumed is not pending:
                raise proof.NativeAccountProofError(
                    "native_account_history_join_carrier_mismatch"
                )
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_current_history_join_diagnostic.v1",
                    "native_current_receipt_sha256": sha(consumed.receipt_json),
                    "locked_history_join_receipt_sha256": locked.receipt_sha256,
                    "history_source_reference": locked_value[
                        "history_source_reference"
                    ],
                    "current_source_reference": current_reference,
                    "locked_readback_blocking_reasons": locked_value[
                        "locked_readback_blocking_reasons"
                    ],
                    "history_tail_closed": False,
                    "native_current_source_observed": True,
                    "historical_native_source_observed": False,
                    "snapshot": None,
                    "account_complete": False,
                    "account_revision_published": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
            return NativeCurrentHistoryJoinDiagnostic(receipt)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- source, SQL and credential details stay private
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from None
        return NativeCurrentHistoryJoinDiagnostic(
            canonical(
                {
                    "schema_version": "ctcc.native_current_history_join_diagnostic.v1",
                    "code": "native_account_history_join_denied",
                    "native_current_source_observed": False,
                    "historical_native_source_observed": False,
                    "snapshot": None,
                    "account_complete": False,
                    "account_revision_published": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
        )
    finally:
        session._used = True
        if carrier is not None:
            stale = _CAPTURES.pop(carrier, None)
            if stale is not None:
                boundary._discard_boundary(stale.boundary)
