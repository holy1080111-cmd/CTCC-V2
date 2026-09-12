"""Explicit single-pass orchestration with no automatic startup.

HTTP IO is delegated to the injected adapter. The worker neither creates nor
closes an HTTP client: the adapter owns responses and the caller owns the client.

Only caller-listed report IDs are considered, once each. This layer neither
discovers jobs nor retries, reconciles, deletes, schedules or submits orders.
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

from app.trade_evidence import outbox, storage

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
