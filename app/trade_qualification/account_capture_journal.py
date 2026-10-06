"""Private B1 ingestion observations. Never account claims or source authority.

Raw pages stay in bounded memory until the invocation stops issuing signatures.
Only terminal whole-buffer raw AND decoded-JSON scans allow plaintext persistence.
Sparse progress receipts prove a prefix, not survival of the unobserved tail.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from app.trade_qualification import account_capture as capture

PAGE_BYTES = 262144
TOTAL_BYTES = 4194304
PROGRESS_BYTES = 4096
MAX_EVENTS = 8192
MAX_CHAIN_BYTES = 64 * 1024 * 1024
FINALIZE_SECONDS = 10
_ISSUER = object()
KINDS = frozenset(
    {
        "capture_start",
        "request_start",
        "headers_received",
        "body_progress",
        "body_complete",
        "page_validated",
        "raw_finalized",
        "acquisition_closed",
        "packet_recorded",
        "terminal",
        "recovery",
    }
)
OUTCOMES = frozenset(
    {
        "in_progress",
        "complete_recorded",
        "failed",
        "cancelled",
        "interrupted_process_loss",
        "interrupted_owner_unknown",
    }
)
RETENTION = frozenset(
    {
        "not_read",
        "buffered_not_durable",
        "durable_secret_checked",
        "withheld_secret",
        "withheld_unverifiable_partial",
        "lost_on_process_death",
        "commit_outcome_unknown",
        "unavailable_owner_unverified",
    }
)


class AccountJournalError(ValueError):
    """Fixed public-safe local codes; no SQL, body or exception interpolation."""


def digest(raw):
    if type(raw) is not bytes:
        raise AccountJournalError("journal_bytes_invalid")
    return hashlib.sha256(raw).hexdigest()


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise AccountJournalError("journal_digest_invalid")
    return value


def canonical(value):
    return capture._canonical(value).encode("utf-8")


@dataclass(frozen=True, slots=True, repr=False, init=False)
class _JournalEvent:
    event_json: bytes
    raw_body: bytes | None
    packet_payload: bytes | None

    def __init__(self, issuer, event_json, raw_body=None, packet_payload=None):
        if issuer is not _ISSUER:
            raise AccountJournalError("owned_journal_event_required")
        object.__setattr__(self, "event_json", event_json)
        object.__setattr__(self, "raw_body", raw_body)
        object.__setattr__(self, "packet_payload", packet_payload)
        checked_event(self)


def checked_event(value):
    if type(value) is not _JournalEvent:
        raise AccountJournalError("owned_journal_event_required")
    raw, body, packet = value.event_json, value.raw_body, value.packet_payload
    if type(raw) is not bytes or not 0 < len(raw) <= 262144:
        raise AccountJournalError("journal_event_bound")
    try:
        record, encoded = capture._decode_json(raw, limit=262144, wire=False)
        if encoded.encode("utf-8") != raw or set(record) != {
            "version",
            "capture_id",
            "sequence",
            "previous_sha256",
            "kind",
            "outcome",
            "observed_at",
            "data",
            "raw_sha256",
            "packet_sha256",
            "account_complete",
            "execution_authority",
            "admission",
        }:
            raise ValueError
        if (
            record["version"] != "ctcc.demo_account_ingestion_event.v1"
            or type(record["capture_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", record["capture_id"]) is None
            or type(record["sequence"]) is not int
            or not 1 <= record["sequence"] <= MAX_EVENTS
            or record["kind"] not in KINDS
            or record["outcome"] not in OUTCOMES
            or type(record["data"]) is not dict
            or record["account_complete"] is not False
            or record["execution_authority"] is not False
            or record["admission"] != "DENY"
        ):
            raise ValueError
        if record["sequence"] == 1:
            if (
                record["kind"] != "capture_start"
                or record["previous_sha256"] is not None
            ):
                raise ValueError
        else:
            _sha(record["previous_sha256"])
        data = record["data"]
        if "raw_retention" in data and data["raw_retention"] not in RETENTION:
            raise ValueError
        if "source_state" in data and data["source_state"] not in {
            "not_requested",
            "request_unanswered",
            "headers_only",
            "body_partial",
            "complete_page",
            "complete_requested_chain",
            "incomplete",
        }:
            raise ValueError
        for key, maximum in (
            ("observed_bytes", capture.MAX_PACKET_BYTES),
            ("request_index", 255),
            ("page_index", 63),
        ):
            if key in data and (
                type(data[key]) is not int or not 0 <= data[key] <= maximum
            ):
                raise ValueError
        if "prefix_sha256" in data:
            _sha(data["prefix_sha256"])
        if record["observed_at"] is not None:
            from datetime import datetime

            stamp = capture._utc(datetime.fromisoformat(record["observed_at"]))
            if stamp.isoformat() != record["observed_at"]:
                raise ValueError
        for payload, name, maximum in (
            (body, "raw_sha256", PAGE_BYTES),
            (packet, "packet_sha256", capture.MAX_PACKET_BYTES),
        ):
            if payload is None:
                if record[name] is not None:
                    raise ValueError
            elif (
                type(payload) is not bytes
                or not 0 < len(payload) <= maximum
                or digest(payload) != record[name]
            ):
                raise ValueError
        if body is not None and (
            record["kind"] != "raw_finalized"
            or record["data"].get("raw_retention") != "durable_secret_checked"
        ):
            raise ValueError
        if packet is not None and record["kind"] != "packet_recorded":
            raise ValueError
        if record["kind"] in {"terminal", "recovery"}:
            if record["outcome"] == "in_progress":
                raise ValueError
        elif record["outcome"] != "in_progress":
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AccountJournalError("journal_event_invalid") from None
    return record


@dataclass(frozen=True, slots=True, repr=False)
class JournalReadback:
    event: _JournalEvent
    db_recorded_at: object
    readback_at: object

    def __post_init__(self):
        from datetime import datetime

        record = checked_event(self.event)
        recorded = capture._utc(self.db_recorded_at)
        readback = capture._utc(self.readback_at)
        clocks = [record["observed_at"]]
        clocks.extend(
            record["data"].get(key)
            for key in (
                "request_started_at",
                "headers_received_at",
                "body_completed_at",
                "source_observed_at",
                "previous_source_observed_at",
            )
        )
        if recorded > readback or any(
            capture._utc(datetime.fromisoformat(value)) > recorded
            for value in clocks
            if value is not None
        ):
            raise AccountJournalError("journal_receipt_clock_order")

    @property
    def receipt_json(self):
        record = checked_event(self.event)
        return canonical(
            {
                "schema_version": "ctcc.demo_account_ingestion_receipt.v1",
                "capture_id": record["capture_id"],
                "event_sequence": record["sequence"],
                "event_sha256": digest(self.event.event_json),
                "stage": record["kind"],
                "outcome": record["outcome"],
                "raw_retention": record["data"].get("raw_retention"),
                "observed_bytes": record["data"].get("observed_bytes"),
                "raw_sha256": record["raw_sha256"],
                "packet_sha256": record["packet_sha256"],
                "db_recorded_at": capture._utc(self.db_recorded_at).isoformat(),
                "readback_at": capture._utc(self.readback_at).isoformat(),
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )


def _failure(exc):
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "acquisition_failed"


class _OwnedAccountJournal:
    """Same-invocation hook; neither a packet nor a runtime DTO can construct it."""

    def __init__(
        self, issuer, repository, scope, plan, checkpoint, owner_sha, clock, tokens
    ):
        if issuer is not _ISSUER:
            raise AccountJournalError("owned_journal_required")
        self.repository, self.scope, self.plan, self.clock = (
            repository,
            scope,
            plan,
            clock,
        )
        self.capture_id = uuid.uuid4().hex
        self.checkpoint, self.owner_sha = checkpoint, owner_sha
        self.sequence, self.previous = 0, None
        self.pages, self.current = [], None
        self.total = 0
        self.closed = self.finished = False
        self.tokens = list(tokens)
        self.last = None
        self.closed_safe = False
        self.finalization_complete = False
        self.acquisition_ok = False
        self.owned_packet_sha256 = None
        self.retention = "not_read"
        self.row_lineage = {}
        self.conflicting_rows = False
        self.source_clock_previous = None
        self.source_clock_invalid = False

    async def emit(
        self, kind, data, *, observed=None, outcome="in_progress", raw=None, packet=None
    ):
        from app.trade_qualification.account_collector import (
            _no_secret_json,
            _no_secrets,
        )

        if self.finished or self.sequence >= MAX_EVENTS:
            raise AccountJournalError("journal_closed_or_bound")
        document = {
            "version": "ctcc.demo_account_ingestion_event.v1",
            "capture_id": self.capture_id,
            "sequence": self.sequence + 1,
            "previous_sha256": self.previous,
            "kind": kind,
            "outcome": outcome,
            "observed_at": None
            if observed is None
            else capture._utc(observed).isoformat(),
            "data": data,
            "raw_sha256": None if raw is None else digest(raw),
            "packet_sha256": None if packet is None else digest(packet),
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        encoded = canonical(document)
        _no_secrets(encoded, self.tokens)
        _no_secret_json(encoded, self.tokens)
        event = _JournalEvent(_ISSUER, encoded, raw, packet)
        receipt = await self.repository._append(self.scope, event)
        if type(receipt) is not JournalReadback or receipt.event != event:
            raise AccountJournalError("journal_readback_invalid")
        self.sequence, self.previous, self.last = (
            document["sequence"],
            digest(encoded),
            receipt,
        )
        if outcome != "in_progress":
            self.finished = True
        return receipt

    async def start(self):
        now = capture._utc(self.clock())
        await self.emit(
            "capture_start",
            {
                "environment": "demo",
                "account_id": self.scope.account_id,
                "settlement_currency": self.scope.settlement_currency,
                "plan_sha256": capture.plan_sha256(self.plan),
                "plan": capture._json_value(self.plan),
                "session_binding_sha256": digest(
                    self.plan.session_binding_id.encode("ascii")
                ),
                "invocation_owner_sha256": self.owner_sha,
                "local_checkpoint_sha256": self.checkpoint,
                "requested_streams": list(capture.streams_for_plan(self.plan)),
                "raw_retention": "not_read",
                "source_state": "not_requested",
                "recovery_not_before": (
                    now
                    + timedelta(
                        seconds=self.plan.max_batch_seconds + 2 * FINALIZE_SECONDS
                    )
                ).isoformat(),
                "progress_prefix_only": True,
                "unobserved_tail_possible": True,
            },
            observed=now,
        )

    def bind_tokens(self, tokens):
        if type(tokens) is not list or self.closed:
            raise AccountJournalError("journal_token_binding_invalid")
        self.tokens = tokens  # owned collector list; includes all later signatures

    def observe_source_clock(self, observed):
        """Record actual clock disorder without replacing or suppressing a sample."""
        valid = observed is not None and not self.source_clock_invalid
        if observed is not None:
            observed = capture._utc(observed)
            valid = valid and (
                self.source_clock_previous is None
                or observed >= self.source_clock_previous
            )
        self.current["metadata"]["previous_source_observed_at"] = (
            None
            if self.source_clock_previous is None
            else self.source_clock_previous.isoformat()
        )
        self.current["metadata"]["source_observed_at"] = (
            None if observed is None else observed.isoformat()
        )
        if not valid:
            self.source_clock_invalid = True
            self.current["metadata"]["clock_order"] = (
                "missing_sample" if observed is None else "reversed"
            )
        self.source_clock_previous = observed
        return valid

    async def request(
        self, *, stream, page_index, after, previous_sha, identity, spec, started
    ):
        if self.closed or len(self.pages) >= self.plan.max_total_pages:
            raise AccountJournalError("journal_request_bound")
        private_query = {"after": after, "query": list(spec.parameters)}
        page = {
            "request_index": len(self.pages),
            "stream": stream,
            "page_index": page_index,
            "after_sha256": None if after is None else digest(after.encode("utf-8")),
            "previous_page_sha256": previous_sha,
            "identity_receipt_sha256": identity,
            "endpoint": spec.endpoint,
            "query_sha256": digest(canonical(private_query)),
            "query_retention": "pending_terminal_secret_scan",
            "request_started_at": started.isoformat(),
            "headers_received_at": None,
            "body_completed_at": None,
            "source_state": "request_unanswered",
            "raw_retention": "not_read",
            "observed_bytes": 0,
            "prefix_sha256": digest(b""),
            "buffer_complete": True,
            "tls_provenance": "unobserved",
            "tls_hostname": None,
            "clock_order": "observed",
        }
        self.pages.append(
            {
                "metadata": page,
                "private_query": private_query,
                "body": bytearray(),
                "hasher": hashlib.sha256(),
                "emitted": 0,
                "observation": None,
            }
        )
        self.current = self.pages[-1]
        clock_ok = self.observe_source_clock(started)
        await self.emit("request_start", page.copy(), observed=started)
        if not clock_ok:
            raise AccountJournalError("journal_source_clock_order")

    async def headers(self, received, status):
        page = self.current["metadata"]
        page.update(
            headers_received_at=None if received is None else received.isoformat(),
            source_state="headers_only",
            http_status=status,
        )
        clock_ok = self.observe_source_clock(received)
        await self.emit("headers_received", page.copy(), observed=received)
        if not clock_ok:
            raise AccountJournalError("journal_source_clock_order")

    def transport(self, proof, *, tls_hostname=None):
        if proof is None:
            if tls_hostname is not None:
                raise AccountJournalError("journal_tls_hostname_invalid")
        elif (
            type(proof) is not str
            or re.fullmatch(r"[a-f0-9]{64}", proof) is None
            or type(tls_hostname) is not str
            or tls_hostname != urlsplit(self.plan.origin).hostname
        ):
            raise AccountJournalError("journal_tls_hostname_invalid")
        self.current["metadata"]["tls_certificate_sha256"] = proof
        self.current["metadata"]["tls_hostname"] = tls_hostname
        self.current["metadata"]["tls_provenance"] = (
            "synthetic_transport" if proof is None else "owned_signed_verified_tls"
        )

    async def chunk(self, chunk, received):
        if type(chunk) is not bytes or self.closed:
            raise AccountJournalError("journal_chunk_invalid")
        current, page = self.current, self.current["metadata"]
        current["hasher"].update(chunk)
        page["observed_bytes"] += len(chunk)
        page["prefix_sha256"] = current["hasher"].hexdigest()
        page.update(source_state="body_partial", raw_retention="buffered_not_durable")
        clock_ok = self.observe_source_clock(received)
        if len(current["body"]) + len(chunk) > min(
            PAGE_BYTES, self.plan.max_response_bytes
        ) or self.total + len(chunk) > min(TOTAL_BYTES, self.plan.max_total_bytes):
            page["buffer_complete"] = False
            await self.emit("body_progress", page.copy(), observed=received)
            raise AccountJournalError("journal_buffer_limit")
        current["body"].extend(chunk)
        self.total += len(chunk)
        if (
            not clock_ok
            or current["emitted"] == 0
            or page["observed_bytes"] - current["emitted"] >= PROGRESS_BYTES
        ):
            await self.progress(received)
        if not clock_ok:
            raise AccountJournalError("journal_source_clock_order")

    async def progress(self, received=None):
        if self.current is not None:
            await self.emit(
                "body_progress", self.current["metadata"].copy(), observed=received
            )
            self.current["emitted"] = self.current["metadata"]["observed_bytes"]

    async def body_complete(self, completed):
        page = self.current["metadata"]
        page.update(
            body_completed_at=None if completed is None else completed.isoformat(),
            source_state="complete_page",
            body_exhaustion_observed=True,
        )
        clock_ok = self.observe_source_clock(completed)
        await self.emit("body_complete", page.copy(), observed=completed)
        if not clock_ok:
            raise AccountJournalError("journal_source_clock_order")

    async def validated(self, observation):
        self.current["observation"] = observation
        page = self.current["metadata"]
        page["receipt_sha256"] = observation.receipt_sha256
        stream = page["stream"]
        family = (
            "fills"
            if stream in capture._FILL_STREAMS
            else "bills"
            if stream in capture._BILL_STREAMS
            else "orders_history"
            if stream in capture._ORDER_HISTORY_STREAMS
            else stream
        )
        lineage = []
        for ordinal, row in enumerate(observation.rows):
            row_sha = digest(row.canonical_json.encode("utf-8"))
            key = (family, row.instrument_id, row.row_id)
            previous = self.row_lineage.get(key)
            status = (
                "first_observation"
                if previous is None
                else "matching_overlap"
                if previous[2] == row_sha
                else "conflicting_overlap"
            )
            self.conflicting_rows |= status == "conflicting_overlap"
            lineage.append(
                {
                    "ordinal": ordinal,
                    "row_identity_sha256": digest(canonical(key)),
                    "row_sha256": row_sha,
                    "overlap": status,
                    "prior_request_index": None if previous is None else previous[0],
                    "prior_row_ordinal": None if previous is None else previous[1],
                }
            )
            self.row_lineage.setdefault(key, (page["request_index"], ordinal, row_sha))
        page["rows"] = lineage
        clock_ok = self.observe_source_clock(observation.body_completed_at)
        await self.emit(
            "page_validated", page.copy(), observed=observation.body_completed_at
        )
        if not clock_ok:
            raise AccountJournalError("journal_source_clock_order")

    def bind_capture(self, owned):
        """Bind the actual collector carrier before reduction to a runtime DTO."""
        from app.trade_qualification import account_collector as collector

        if type(owned) is not collector._OwnedAccountCapture or self.closed:
            raise AccountJournalError("journal_owned_capture_required")
        if type(owned.packet) is not capture.DemoAccountPacket:
            raise AccountJournalError("journal_owned_packet_required")
        observations = owned.packet.observations
        if len(observations) != len(self.pages) or len(
            owned.peer_certificate_sha256
        ) != len(self.pages):
            raise AccountJournalError("journal_owned_capture_mismatch")
        for current, observation, proof in zip(
            self.pages, observations, owned.peer_certificate_sha256, strict=True
        ):
            if (
                current["observation"] != observation
                or bytes(current["body"]) != observation.response_body
                or current["metadata"].get("tls_certificate_sha256") != proof
            ):
                raise AccountJournalError("journal_owned_capture_mismatch")
        self.owned_packet_sha256 = capture.freeze_demo_account_packet(
            owned.packet, expected_plan_sha256=capture.plan_sha256(self.plan)
        ).sha256

    async def close_acquisition(self, *, successful):
        from app.trade_qualification import account_collector as collector

        self.closed = True  # no new signature/request after terminal set closes
        self.tokens = tuple(self.tokens)
        all_safe = True
        retained_states = set()
        for current in self.pages:
            page, raw = current["metadata"].copy(), bytes(current["body"])
            # Source-derived cursors and identities can contain a signature that
            # the same invocation has not issued yet. Never persist them early.
            private_query = current["private_query"]
            try:
                collector._no_secrets(canonical(private_query), self.tokens)
                collector._no_secret_json(canonical(private_query), self.tokens)
                page.update(private_query)
                page["query_retention"] = "durable_secret_checked"
            except collector.AccountCollectionError:
                page["query_retention"] = "withheld_secret"
                retained_states.add("withheld_secret")
                all_safe = False
            retention = (
                "not_read"
                if not page["observed_bytes"]
                else "withheld_unverifiable_partial"
            )
            accepted = None
            if raw and page["buffer_complete"]:
                try:
                    collector._no_secrets(raw, self.tokens)
                    _, decoded = capture._decode_json(raw, limit=PAGE_BYTES, wire=True)
                    collector._no_secret_json(decoded, self.tokens)
                    retention, accepted = "durable_secret_checked", raw
                except collector.AccountCollectionError:
                    retention = "withheld_secret"
                except capture.AccountCaptureError as exc:
                    if type(exc) is capture.AccountCaptureError and exc.args == (
                        "secret_field_forbidden",
                    ):
                        retention = "withheld_secret"
            if retention in {"withheld_secret", "withheld_unverifiable_partial"}:
                all_safe = False
            retained_states.add(retention)
            page["raw_retention"] = retention
            page["retained_bytes"] = 0 if accepted is None else len(accepted)
            page["terminal_secret_set_closed"] = True
            page["known_secret_count"] = len(self.tokens)
            await self.emit(
                "raw_finalized", page, observed=capture._utc(self.clock()), raw=accepted
            )
            current["body"].clear()
        self.closed_safe = all_safe
        self.retention = next(
            (
                state
                for state in (
                    "withheld_secret",
                    "withheld_unverifiable_partial",
                    "durable_secret_checked",
                )
                if state in retained_states
            ),
            "not_read",
        )
        self.acquisition_ok = (
            successful
            and all_safe
            and not self.conflicting_rows
            and not self.source_clock_invalid
        )
        await self.emit(
            "acquisition_closed",
            {
                "requested_pages": len(self.pages),
                "all_observed_buffers_secret_checked": all_safe,
                "signatures_closed": True,
                "source_clock_order_valid": not self.source_clock_invalid,
                "source_state": "complete_requested_chain"
                if self.acquisition_ok
                else "incomplete",
                "raw_retention": self.retention,
            },
        )
        self.finalization_complete = True
        if successful and self.source_clock_invalid:
            raise AccountJournalError("journal_source_clock_order")
        if successful and not all_safe:
            raise AccountJournalError("journal_secret_scan_incomplete")
        if successful and self.conflicting_rows:
            raise AccountJournalError("journal_conflicting_source_rows")

    async def finish(self, *, result=None, error=None):
        if result is not None:
            if not self.closed or not self.acquisition_ok:
                raise AccountJournalError("journal_acquisition_incomplete")
            frozen = capture.freeze_demo_account_packet(
                result.packet, expected_plan_sha256=capture.plan_sha256(self.plan)
            )
            if (
                self.owned_packet_sha256 is None
                or frozen.sha256 != self.owned_packet_sha256
            ):
                raise AccountJournalError("journal_original_owned_packet_required")
            from app.trade_qualification import account_collector as collector

            collector._no_secrets(frozen.payload, self.tokens)
            collector._no_secret_json(frozen.payload, self.tokens)
            await self.emit(
                "packet_recorded",
                {
                    "plan_sha256": capture.plan_sha256(self.plan),
                    "local_checkpoint_sha256": self.checkpoint,
                    "source_state": "complete_requested_chain",
                },
                packet=frozen.payload,
            )
        outcome = (
            "complete_recorded"
            if result is not None
            else "cancelled"
            if isinstance(error, asyncio.CancelledError)
            else "failed"
        )
        return await self.emit(
            "terminal",
            {
                "reason": "recorded_incomplete_account"
                if result is not None
                else _failure(error),
                "raw_retention": self.retention
                if self.finalization_complete
                else "commit_outcome_unknown",
                "observed_pages": len(self.pages),
            },
            observed=capture._utc(self.clock()),
            outcome=outcome,
        )


async def bounded_finalization(awaitable):
    """A fixed deadline; caller cancellation survives persistence cleanup."""

    async def bounded():
        async with asyncio.timeout(FINALIZE_SECONDS):
            return await awaitable

    task = asyncio.create_task(bounded())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- propagate bounded task result below
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def start_owned_journal(*, repository, scope, plan, checkpoint, clock, tokens):
    from app.database.repositories.account_capture_journal import (
        AccountCaptureJournalRepository,
    )

    if type(repository) is not AccountCaptureJournalRepository:
        raise AccountJournalError("owned_journal_repository_required")
    from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

    checked_bootstrap(scope, LedgerScope)
    plan = capture._checked_plan(plan, capture.plan_sha256(plan))
    if (
        type(tokens) not in (list, tuple)
        or len(tokens) != 3
        or any(type(item) is not str for item in tokens)
    ):
        raise AccountJournalError("journal_token_binding_invalid")
    _sha(checkpoint)
    journal = _OwnedAccountJournal(
        _ISSUER,
        repository,
        scope,
        plan,
        checkpoint,
        digest(uuid.uuid4().bytes + uuid.uuid4().bytes),
        clock,
        tokens,
    )
    await journal.start()
    return journal
