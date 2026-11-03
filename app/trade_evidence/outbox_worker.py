"""Explicit single-pass orchestration with no automatic startup.

HTTP IO is delegated to the injected adapter. The worker neither creates nor
closes an HTTP client: the adapter owns responses and the caller owns the client.

Only caller-listed report IDs are considered, once each. This layer neither
retries, reconciles, deletes or submits orders. Its reporting-cycle wrapper adds queue discovery, fairness and persisted cooldown scheduling as DATA only.
Recovery and dispatch always use the existing verified append-only outbox API.
The asyncio deadline bounds cooperative async work, not blocking synchronous
filesystem calls or an adapter that refuses cancellation. A deadline stops all
later jobs; durable dispatch uncertainty remains the outbox's responsibility.
Results describe local processing only, never authenticated remote delivery or
permission to trade. Injected clocks and adapters remain trusted boundaries.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.trade_evidence import outbox, reporting_control, storage

Code = Literal[
    "outbox_attempt_recorded",
    "outbox_terminal",
    "outbox_uncertain",
    "outbox_lease_active",
    "outbox_retry_not_due",
    "outbox_storage_permission_denied",
    "outbox_storage_unavailable",
    "outbox_job_failed",
    "outbox_dispatch_failed",
    "outbox_view_invalid",
    "outbox_pass_deadline",
    "outbox_not_started_deadline",
]


class OutboxWorkerError(ValueError):
    """Static local contract error, with no arbitrary exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _exact_model(value, expected):
    if (
        type(value) is not expected
        or value.__pydantic_extra__ is not None
        or value.__pydantic_private__ is not None
        or set(value.__dict__) != set(expected.model_fields)
    ):
        raise ValueError("outbox_worker_model_invalid")
    return value


def _ids(value):
    if type(value) is not tuple or not 1 <= len(value) <= 16:
        raise ValueError("outbox_worker_report_ids_invalid")
    for report_id in value:
        if (
            type(report_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", report_id) is None
        ):
            raise ValueError("outbox_worker_report_ids_invalid")
        outbox._name(report_id)
    if len({item.casefold() for item in value}) != len(value):
        raise ValueError("outbox_worker_report_ids_invalid")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        str_strip_whitespace=False,
    )

    @classmethod
    def model_validate(cls, obj, **kwargs):
        # Pydantic may unpack an instance before model-level validators run.
        # Reject hidden instance state at this public reconstruction boundary.
        if isinstance(obj, BaseModel):
            _exact_model(obj, cls)
        return super().model_validate(obj, **kwargs)

    @model_validator(mode="before")
    @classmethod
    def exact_record(cls, value):
        if isinstance(value, BaseModel):
            _exact_model(value, cls)
            return dict(value.__dict__)
        return value


class OutboxWorkerPolicy(_Model):
    pass_timeout_seconds: int = Field(default=30, ge=1, le=300)


class OutboxWorkItem(_Model):
    report_id: outbox.ReportId
    status: Literal["skipped", "processed", "failed", "not_started"]
    code: Code
    outbox_status: outbox.Status | None
    attempts: int | None = Field(ge=0, le=10)
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    @field_validator("report_id")
    @classmethod
    def report_name(cls, value):
        return _ids((value,))[0]

    _authority = field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )(outbox.OutboxPayload.no_authority.__func__)

    @model_validator(mode="after")
    def consistent(self):
        if self.status in {"failed", "not_started"}:
            if self.outbox_status is not None or self.attempts is not None:
                raise ValueError("outbox_worker_item_invalid")
            allowed = (
                {"outbox_not_started_deadline"}
                if self.status == "not_started"
                else {
                    "outbox_storage_permission_denied",
                    "outbox_storage_unavailable",
                    "outbox_job_failed",
                    "outbox_dispatch_failed",
                    "outbox_view_invalid",
                    "outbox_pass_deadline",
                }
            )
        else:
            if self.attempts is None:
                raise ValueError("outbox_worker_item_invalid")
            if self.status == "processed":
                if self.outbox_status not in {
                    "delivered",
                    "retry_wait",
                    "exhausted",
                    "uncertain",
                }:
                    raise ValueError("outbox_worker_item_invalid")
                allowed = {"outbox_attempt_recorded"}
            else:
                allowed = {
                    "delivered": {"outbox_terminal"},
                    "exhausted": {"outbox_terminal"},
                    "uncertain": {"outbox_uncertain"},
                    "claimed": {"outbox_lease_active"},
                    "dispatching": {"outbox_lease_active"},
                    "queued": {"outbox_retry_not_due"},
                    "retry_wait": {"outbox_retry_not_due"},
                }.get(self.outbox_status, set())
        if self.code not in allowed:
            raise ValueError("outbox_worker_item_invalid")
        return self


class OutboxPassResult(_Model):
    worker_id: outbox.Identifier
    policy: OutboxWorkerPolicy
    started_at: datetime
    completed_at: datetime
    status: Literal["completed", "deadline_reached"]
    items: tuple[OutboxWorkItem, ...] = Field(min_length=1, max_length=16)
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def json_transport(cls, value, info: ValidationInfo):
        # A model-level Python guard changes Pydantic's nested strict JSON path.
        # Restore only JSON's datetime/array representations, never Python input.
        if info.mode == "json" and type(value) is dict:
            value = dict(value)
            for name in ("started_at", "completed_at"):
                if name in value:
                    value[name] = TypeAdapter(datetime).validate_json(
                        json.dumps(value[name]), strict=True
                    )
            if type(value.get("items")) is list:
                value["items"] = tuple(value["items"])
        return value

    _times = field_validator("started_at", "completed_at")(outbox._utc)
    _authority = field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )(outbox.OutboxPayload.no_authority.__func__)

    @field_validator("policy", mode="before")
    @classmethod
    def exact_policy(cls, value):
        if isinstance(value, BaseModel):
            _exact_model(value, OutboxWorkerPolicy)
        return value

    @field_validator("items", mode="before")
    @classmethod
    def exact_items(cls, value):
        if type(value) in {tuple, list}:
            for item in value:
                if isinstance(item, BaseModel):
                    _exact_model(item, OutboxWorkItem)
        return value

    @model_validator(mode="after")
    def consistent(self):
        _ids(tuple(item.report_id for item in self.items))
        if self.completed_at < self.started_at:
            raise ValueError("outbox_worker_clock_reversed")
        deadline_items = [
            item
            for item in self.items
            if item.code in {"outbox_pass_deadline", "outbox_not_started_deadline"}
        ]
        if bool(deadline_items) != (self.status == "deadline_reached"):
            raise ValueError("outbox_worker_result_invalid")
        return self


def _item(report_id, status, code, view=None):
    return OutboxWorkItem(
        report_id=report_id,
        status=status,
        code=code,
        outbox_status=None if view is None else view.status,
        attempts=None if view is None else view.attempts,
    )


def _verified(value, report_id):
    try:
        view = outbox.validate_outbox_view(value)
        if view.envelope.payload.report_id != report_id:
            raise ValueError("outbox_worker_view_identity_invalid")
        return view
    except Exception:  # noqa: BLE001 - Do not serialize injected result details.
        raise OutboxWorkerError("outbox_view_invalid") from None


def _skip(view):
    if view.status in {"delivered", "exhausted"}:
        return "outbox_terminal"
    if view.status == "uncertain":
        return "outbox_uncertain"
    if view.status in {"claimed", "dispatching"}:
        return "outbox_lease_active"
    if view.head.next_attempt_at > view.verified_at:
        return "outbox_retry_not_due"
    return None


async def run_outbox_pass(
    root: Path,
    *,
    report_ids: tuple[str, ...],
    worker_id: str,
    adapter: Callable[
        [outbox.OutboxPayload, outbox.ClaimToken], Awaitable[outbox.DeliveryOutcome]
    ],
    policy: OutboxWorkerPolicy,
    clock: Callable[[], datetime] = outbox.actual_utc,
) -> OutboxPassResult:
    """One ordered pass; static errors and false authority, never a retry loop."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    try:
        _ids(report_ids)
        _exact_model(policy, OutboxWorkerPolicy)
        checked_policy = OutboxWorkerPolicy.model_validate(policy, strict=True)
        if (
            type(worker_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", worker_id) is None
        ):
            raise ValueError("outbox_worker_id_invalid")
        if not callable(adapter) or not callable(clock):
            raise TypeError("outbox_worker_callable_invalid")
        checked_root = storage._root_path(root)
    except Exception:  # noqa: BLE001 - Contract errors cannot expose input text.
        raise OutboxWorkerError("outbox_worker_input_invalid") from None

    last_at = None
    clock_failed = False

    def checked_clock():
        nonlocal last_at, clock_failed
        try:
            if clock_failed:
                raise ValueError("outbox_worker_clock_invalid")
            at = outbox._now(clock)
            if last_at is not None and at < last_at:
                raise ValueError("outbox_worker_clock_reversed")
            last_at = at
            return at
        except Exception:  # noqa: BLE001 - Clock failures are a static boundary.
            clock_failed = True
            raise OutboxWorkerError("outbox_worker_clock_invalid") from None

    started_at = checked_clock()
    loop = asyncio.get_running_loop()
    deadline_at = loop.time() + checked_policy.pass_timeout_seconds
    deadline = asyncio.timeout_at(deadline_at)
    items = []
    active = None
    expired = False
    try:
        async with deadline:
            for report_id in report_ids:
                await asyncio.sleep(0)
                if loop.time() >= deadline_at:
                    expired = True
                    break
                active = report_id
                stage = "outbox_job_failed"
                try:
                    # recover_job includes a locked, hash-chain-verified read.
                    view = _verified(
                        outbox.recover_job(
                            checked_root, report_id, clock=checked_clock
                        ),
                        report_id,
                    )
                    if loop.time() >= deadline_at:
                        expired = True
                        break
                    skip_code = _skip(view)
                    if skip_code is not None:
                        item = _item(report_id, "skipped", skip_code, view)
                    else:
                        stage = "outbox_dispatch_failed"
                        view = _verified(
                            await outbox.dispatch_once(
                                checked_root,
                                report_id,
                                worker_id=worker_id,
                                adapter=adapter,
                                clock=checked_clock,
                            ),
                            report_id,
                        )
                        item = _item(
                            report_id, "processed", "outbox_attempt_recorded", view
                        )
                    if deadline.expired() or loop.time() >= deadline_at:
                        expired = True
                        break
                    items.append(item)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - Explicit, non-text error mapping only.
                    if task is not None and task.cancelling():
                        raise asyncio.CancelledError from None
                    if clock_failed:
                        raise OutboxWorkerError("outbox_worker_clock_invalid") from None
                    if deadline.expired() or loop.time() >= deadline_at:
                        expired = True
                        break
                    code = stage
                    if (
                        type(error) is OutboxWorkerError
                        and type(error.code) is str
                        and error.code == "outbox_view_invalid"
                    ):
                        code = "outbox_view_invalid"
                    elif (
                        type(error) is outbox.OutboxError
                        and type(error.code) is str
                        and error.code
                        in {
                            "outbox_storage_permission_denied",
                            "outbox_storage_unavailable",
                        }
                    ):
                        code = error.code
                    items.append(_item(report_id, "failed", code))
                active = None
    except TimeoutError:
        if not deadline.expired():
            raise OutboxWorkerError("outbox_worker_timeout_invalid") from None
        expired = True
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    if expired:
        if active is not None:
            items.append(_item(active, "failed", "outbox_pass_deadline"))
        for report_id in report_ids[len(items) :]:
            items.append(_item(report_id, "not_started", "outbox_not_started_deadline"))
    return OutboxPassResult(
        worker_id=worker_id,
        policy=checked_policy,
        started_at=started_at,
        completed_at=checked_clock(),
        status="deadline_reached" if expired else "completed",
        items=tuple(items),
    )


@dataclass(frozen=True, slots=True)
class ReportingCycleData:
    """Scheduler/queue DATA only, never a native source or remote permit."""

    status: str
    code: str
    delay: int | None
    after_id: str
    last_result: object = None
    failures: int = 0
    execution_authority: Literal[False] = field(default=False, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)


@dataclass(slots=True)
class ReportingCycleProgress:
    """Local cursor/result DATA retained even if a later queue read fails."""

    after_id: str
    last_result: object = None
    failures: int = 0


class ReportingPreparationUnavailable(ValueError):
    """A trusted preparation callback found no authorized source for this ID."""

    def __init__(self, report_id):
        outbox._name(report_id)
        self.report_id = report_id
        super().__init__("notion_outbox_submission_source_authority_unavailable")


def reporting_error_data(error, *, poll_seconds, failures, after_id, safe_codes):
    """Existing fixed-code/backoff mapping; no exception text enters DATA."""
    from app.trade_evidence.notion_binding import NotionBindingError

    failures += 1
    delay = min(300, poll_seconds * 2 ** min(failures - 1, 6))
    status, code = "pending", "notion_outbox_configuration_or_delivery_pending"
    if (
        type(error) is NotionBindingError
        and type(error.code) is str
        and error.code in safe_codes
    ):
        code = error.code
        if error.retry_after_seconds is None:
            status, delay = "paused", None
        elif (
            type(error.retry_after_seconds) is int
            and 0 <= error.retry_after_seconds <= 99999
        ):
            delay = max(delay, error.retry_after_seconds)
    return ReportingCycleData(status, code, delay, after_id, failures=failures)


async def run_reporting_delivery_cycle(
    root,
    *,
    worker_id,
    clock,
    poll_seconds,
    batch_size,
    after_id,
    client_factory,
    discover,
    prepare_batch,
    verify_binding,
    make_adapter,
    stop_requested,
    timeout_seconds,
    progress=None,
):
    """Shared lower DATA orchestration; supplied callbacks grant no source truth.

    Production callbacks must enforce their own genuine source/transport owner.
    Synthetic callers can exercise queue/protocol mechanics, never native
    admission. Existing outbox remains the sole durable dispatch/retry engine.
    """
    from app.trade_evidence.notion_binding import NotionBindingError

    if progress is None:
        progress = ReportingCycleProgress(after_id)
    if type(progress) is not ReportingCycleProgress or progress.after_id != after_id:
        raise OutboxWorkerError("outbox_worker_input_invalid")
    cursor, last = after_id, None
    with reporting_control.reporting_lease(root) as control:
        cooldown = reporting_control.retry_delay(control, clock())
        if cooldown is None or cooldown > 0:
            return ReportingCycleData(
                "paused",
                "notion_outbox_rate_limit_pending",
                cooldown,
                cursor,
                progress.last_result,
                progress.failures,
            )
        ids = discover(root)
        for report_id in ids:
            view = outbox.recover_job(root, report_id, clock=clock)
            if view.status in {"uncertain", "dispatching", "claimed"}:
                return ReportingCycleData(
                    "paused",
                    "notion_outbox_reconciliation_required",
                    None,
                    cursor,
                    progress.last_result,
                    progress.failures,
                )
        later = tuple(name for name in ids if name > after_id)
        ordered = later + tuple(name for name in ids if name <= after_id)
        try:
            batch = await prepare_batch(ordered[:batch_size])
        except ReportingPreparationUnavailable as unavailable:
            return ReportingCycleData(
                "pending",
                "notion_outbox_submission_source_authority_unavailable",
                poll_seconds,
                unavailable.report_id,
                progress.last_result,
                progress.failures,
            )
        if batch is None:
            return ReportingCycleData(
                "running",
                "notion_outbox_pass_complete",
                poll_seconds,
                cursor,
                progress.last_result,
                progress.failures,
            )
        if type(batch) is not tuple or any(item not in ids for item in batch):
            raise OutboxWorkerError("outbox_worker_report_ids_invalid")
        progress.last_result = None
        async with client_factory() as client, asyncio.timeout(timeout_seconds):
            try:
                await verify_binding(client)
            except NotionBindingError as error:
                if error.code == "notion_binding_rate_limited":
                    reporting_control.record_rate_limit(
                        control, clock(), error.retry_after_seconds
                    )
                raise
            if stop_requested():
                raise asyncio.CancelledError
            discover(root)

            def check_queue_pairs():
                discover(root)

            deliver, monitor = make_adapter(client, check_queue_pairs)
            for report_id in batch:
                discover(root)
                last = await run_outbox_pass(
                    root,
                    report_ids=(report_id,),
                    worker_id=worker_id,
                    adapter=deliver,
                    policy=OutboxWorkerPolicy(pass_timeout_seconds=timeout_seconds),
                    clock=clock,
                )
                cursor = report_id
                progress.after_id, progress.last_result = cursor, last
                if monitor.rate_limited:
                    reporting_control.record_rate_limit(
                        control, clock(), monitor.rate_limit_retry_after_seconds
                    )
                discover(root)
                if monitor.pre_create_check_failed:
                    return ReportingCycleData(
                        "pending",
                        "notion_outbox_pre_create_check_failed",
                        poll_seconds,
                        cursor,
                        last,
                        progress.failures,
                    )
                if monitor.rate_limited or any(
                    item.outbox_status in {"uncertain", "dispatching"}
                    or item.status == "failed"
                    for item in last.items
                ):
                    return ReportingCycleData(
                        "paused",
                        "notion_outbox_reconciliation_required",
                        None,
                        cursor,
                        last,
                        progress.failures,
                    )
    pending = last is not None and any(
        item.outbox_status != "delivered" for item in last.items
    )
    return ReportingCycleData(
        "pending" if pending else "running",
        "notion_outbox_jobs_pending" if pending else "notion_outbox_pass_complete",
        poll_seconds,
        cursor,
        last,
    )
