"""Loopback manual Live tool. Only the original worker can own a trade.

HTTP tasks authenticate and signal an exact human challenge. They never accept
a source, factory, risk result or execution permit. There is no scheduler,
automatic Arm, order retry or legacy Live endpoint in this application.
"""

import asyncio
import ipaddress
import json
import re
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from app.api.security import require_ctcc_token
from app.domain.source_primitives import decode
from app.trade_qualification.event_models import StrategyName


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PrepareRequest(_Request):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    strategy: StrategyName
    direction: Literal["long", "short"]
    controlled_exit_requested: bool


class ApprovalRequest(_Request):
    operation_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    challenge_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    arm_phrase: Annotated[str, Field(min_length=1, max_length=160)]
    clear_stop_phrase: Annotated[str, Field(min_length=1, max_length=160)]
    controlled_exit_phrase: Annotated[str, Field(max_length=160)]


class OperationRequest(_Request):
    operation_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class ReconcileRequest(_Request):
    reservation_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def _modules():
    # Fixed genuine module paths, loaded only for explicitly requested private
    # setup/actions. No injected client/factory/callback comes from HTTP.
    from app.okx_live import native_runtime_v1 as runtime
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_qualification_v1 as qualification

    return runtime, qualification, lifecycle


def _runtime():
    # Missing private setup is decided before optional lifecycle imports. It
    # must remain a usable default-OFF page even during an incomplete install.
    from app.okx_live import native_runtime_v1 as runtime

    return runtime


def _need(condition):
    if condition is not True:
        raise HTTPException(409, "live_tool_operation_unavailable")


def _display_caps(value):
    _need(type(value) is list and len(value) == 4)
    parsed = []
    for raw, ceiling in zip(
        value, (Decimal("0.25"), Decimal("0.25"), Decimal(25), Decimal(25)), strict=True
    ):
        _need(
            type(raw) is str
            and len(raw) <= 64
            and re.fullmatch(
                r"(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,20})?(?:[eE][+-]?[0-9]{1,2})?", raw
            )
            is not None
        )
        number = Decimal(raw)
        _need(
            number.is_finite()
            and 0 < number <= ceiling
            and -20 <= number.as_tuple().exponent <= 20
        )
        parsed.append(number)
    _need(parsed[1] >= parsed[0])
    # This is a display projection only. No source receipt, control value or
    # qualification operand is rewritten into this normalized representation.
    result = [format(number, "f") for number in parsed]
    return [item.rstrip("0").rstrip(".") if "." in item else item for item in result]


def _challenge(raw):
    try:
        value = decode(raw, maximum=16384)
    except ValueError:
        raise HTTPException(409, "live_tool_challenge_unavailable") from None
    _need(
        type(value) is dict
        and value.get("schema") == "ctcc.native_live.operator_challenge.v1"
    )
    _need(
        value.get("candidate_created") is False
        and value.get("execution_authority") is False
    )
    _need(
        value.get("controlled_exit_authorized") is False
        and type(value.get("maximum_submissions")) is int
        and value.get("maximum_submissions") == 1
    )
    # Explicit public projection. No credential material, UID, route, private
    # path or source owner may reach the browser through this boundary.
    names = (
        "challenge_id",
        "instrument_id",
        "strategy",
        "direction",
        "account_label",
        "arm_phrase",
        "clear_stop_phrase",
        "controlled_exit_phrase",
        "caps",
        "existing_leverage",
        "controlled_exit_requested",
        "controlled_exit_seconds",
        "challenge_expires_at",
        "arm_seconds",
        "maximum_submissions",
    )
    _need(all(name in value for name in names))
    projected = {name: value[name] for name in names}
    _need(
        type(projected["challenge_id"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", projected["challenge_id"]) is not None
        and projected["instrument_id"] in {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
        and projected["direction"] in {"long", "short"}
        and type(projected["arm_seconds"]) is int
        and projected["arm_seconds"] == 30
        and type(projected["existing_leverage"]) is int
        and 1 <= projected["existing_leverage"] <= 3
        and type(projected["controlled_exit_requested"]) is bool
        and type(projected["controlled_exit_seconds"]) is int
        and projected["controlled_exit_seconds"]
        == (120 if projected["controlled_exit_requested"] else 0)
        and type(projected["account_label"]) is str
        and re.fullmatch(r"…[0-9]{4}", projected["account_label"]) is not None
    )
    _need(type(projected["arm_phrase"]) is str)
    matched = re.fullmatch(
        r"ARM_OKX_LIVE_REAL_MONEY:([a-f0-9]{32})", projected["arm_phrase"]
    )
    _need(matched is not None)
    nonce = matched[1]
    _need(projected["clear_stop_phrase"] == "CLEAR_OKX_LIVE_STOP:" + nonce)
    _need(
        projected["controlled_exit_phrase"]
        == (
            "CLOSE_OKX_LIVE_MICRO_POSITION:" + nonce
            if projected["controlled_exit_requested"]
            else ""
        )
    )
    projected["caps"] = _display_caps(projected["caps"])
    _need(
        type(projected["challenge_expires_at"]) is str
        and len(projected["challenge_expires_at"]) <= 40
    )
    _need(len(json.dumps(projected)) <= 8192)
    return projected


def _lifecycle_data(value):
    _need(type(value) is dict)
    expected = {
        "schema",
        "status",
        "reservation_id",
        "report_id",
        "risk_hold_retained",
        "protection_verified",
        "emergency_stop",
        "disarmed",
        "execution_authority",
    }
    _need(set(value) == expected and value["execution_authority"] is False)
    _need(value["schema"] == "ctcc.native_live.lifecycle_receipt.v1")
    _need(
        type(value["reservation_id"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", value["reservation_id"]) is not None
    )
    _need(
        value["report_id"] is None
        or type(value["report_id"]) is str
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value["report_id"]) is not None
    )
    _need(
        value["status"]
        in {"ZERO_FILL_FLAT", "PROTECTED_HOLD", "UNCERTAIN", "RECONCILED_FLAT"}
    )
    for field in (
        "risk_hold_retained",
        "protection_verified",
        "emergency_stop",
        "disarmed",
    ):
        _need(value[field] is None or type(value[field]) is bool)
    return json.loads(json.dumps(value))


class _StoppedBeforeArm(Exception):
    pass


def _known_no_submit_failure(error, qualification):
    kind = getattr(qualification, "OwnedLiveQualificationError", None)
    if kind is None or type(error) is not kind:
        return None
    return {
        "native_live_initial_qualification_wait_or_fail": "WAIT",
        "native_live_post_g12_cancelled": "CANCELLED",
        "native_live_human_challenge_expired": "EXPIRED",
    }.get(str(error))


class LiveToolWorker:
    def __init__(self):
        self.ready = False
        self.setup_status = "NOT_CHECKED"
        self.job = None
        self.task = None
        self.session = None
        self.stop_requested = False
        self.approval_signaled = False

    def snapshot(self):
        return {
            "schema": "ctcc.native_live.manual_tool_data.v1",
            "setup_status": self.setup_status,
            "startup_inhibit_confirmed": self.ready,
            "job": None if self.job is None else json.loads(json.dumps(self.job)),
            "execution_authority": False,
            "micro_live_acceptance": "NOT_PERFORMED",
        }

    async def initialize(self):
        _need(self.task is None or self.task.done())
        self.ready = False
        self.setup_status = "CHECKING_PRIVATE_SETUP"
        try:
            runtime = _runtime()
            async with runtime.configured_live_runtime_v1() as owner:
                result = await runtime.inhibit_configured_live_runtime_v1(owner)
                _need(
                    type(result) is dict
                    and set(result)
                    == {
                        "schema",
                        "armed",
                        "emergency_stop",
                        "control_present",
                        "readback_sha256",
                        "account_complete",
                        "exchange_flat_verified",
                        "execution_authority",
                    }
                    and result["schema"] == "ctcc.native_live.startup_inhibit_data.v1"
                    and result["armed"] is False
                    and result["emergency_stop"] is True
                    and type(result["control_present"]) is bool
                    and (
                        (
                            result["control_present"] is False
                            and result["readback_sha256"] is None
                        )
                        or (
                            result["control_present"] is True
                            and type(result["readback_sha256"]) is str
                            and re.fullmatch(r"[a-f0-9]{64}", result["readback_sha256"])
                            is not None
                        )
                    )
                    and result["account_complete"] is False
                    and result["exchange_flat_verified"] is False
                    and result["execution_authority"] is False
                )
            self.ready = True
            self.setup_status = "STOPPED_DEFAULT_OFF"
        except Exception as error:  # noqa: BLE001 -- private configuration/SQL are never returned.
            self.setup_status = (
                "NOT_CONFIGURED"
                if type(error).__name__ == "LiveRuntimeNotConfigured"
                else "PRIVATE_SETUP_OR_STOP_UNCONFIRMED"
            )
        return self.snapshot()

    def start(self, request):
        _need(type(request) is PrepareRequest)
        _need(self.task is None or self.task.done())
        if self.ready is not True:
            raise HTTPException(503, "live_tool_setup_unavailable")
        self.job = {
            "operation_id": uuid4().hex,
            "status": "PREPARING_READ_ONLY",
            "challenge": None,
            "result": None,
            "error": None,
        }
        self.stop_requested = False
        self.approval_signaled = False
        self.ready = False
        self.task = asyncio.create_task(
            self._run(request), name="ctcc-native-live-one-shot"
        )
        return self.snapshot()

    async def _run(self, request):
        pending = None
        qualification = None
        try:
            if self.stop_requested:
                raise _StoppedBeforeArm
            runtime, qualification, lifecycle = _modules()
            async with (
                runtime.configured_live_runtime_v1() as owner,
                qualification.open_live_operator_session_v1(
                    owner,
                    instrument_id=request.instrument_id,
                    strategy=request.strategy,
                    direction=request.direction,
                    controlled_exit_authorized=request.controlled_exit_requested,
                ) as session,
            ):
                self.session = session
                if self.stop_requested:
                    qualification.revoke_live_operator_session_v1(session)
                    raise _StoppedBeforeArm
                self.job["challenge"] = _challenge(session.challenge_data)
                self.job["status"] = "AWAITING_HUMAN_ARM"
                (
                    qualified,
                    physical,
                ) = await qualification.run_owned_live_operator_once_v1(session)
                receipt = await lifecycle.reconcile_owned_live_postsubmit_v1(
                    qualified, physical
                )
                pending = _lifecycle_data(
                    lifecycle.live_lifecycle_public_projection_v1(receipt)
                )
            # Publish success only AFTER the genuine source context has
            # durably stopped/disarmed and all runtime resources have closed.
            self.job["result"] = pending
            self.job["status"] = pending["status"]
        except _StoppedBeforeArm:
            self.ready = False
            self.job["status"] = "STOPPED_BEFORE_ARM"
            self.job["error"] = None
        except asyncio.CancelledError:
            self.ready = False
            if self.stop_requested and not self.approval_signaled:
                # Reaching this handler proves context finally/readback did
                # not replace cancellation with an unconfirmed cleanup error.
                self.job["status"] = "STOPPED_BEFORE_ARM"
                self.job["error"] = None
                return
            self.job["status"] = "UNCERTAIN"
            self.job["error"] = "stopped_reconciliation_required"
            raise
        except Exception as error:  # noqa: BLE001 -- retain core journal; never echo provider/configuration details.
            self.ready = False
            known = _known_no_submit_failure(error, qualification)
            self.job["status"] = known or "UNCERTAIN"
            self.job["error"] = (
                None
                if known is not None
                else "qualification_or_reconciliation_unconfirmed"
            )
        finally:
            self.session = None
            self.job["challenge"] = None

    def approve(self, request):
        _need(type(request) is ApprovalRequest and self.job is not None)
        _need(self.task is not None and not self.task.done())
        _need(
            self.job["operation_id"] == request.operation_id
            and self.job["status"] == "AWAITING_HUMAN_ARM"
        )
        _need(self.job["challenge"]["challenge_id"] == request.challenge_id)
        _, qualification, _ = _modules()
        try:
            qualification.signal_live_operator_approval_v1(
                challenge_id=request.challenge_id,
                arm_phrase=request.arm_phrase,
                clear_stop_phrase=request.clear_stop_phrase,
                controlled_exit_phrase=request.controlled_exit_phrase,
            )
        except Exception:  # noqa: BLE001 -- fixed rejection; no phrase/configuration echo.
            raise HTTPException(409, "live_operator_approval_refused") from None
        self.job["status"] = "RUNNING_ONCE"
        self.approval_signaled = True
        return self.snapshot()

    async def journal(self):
        try:
            _, _, lifecycle = _modules()
            rows = await lifecycle.observe_live_lifecycle_tasks_v1()
            _need(type(rows) is tuple and len(rows) <= 100)
            return {
                "schema": "ctcc.native_live.manual_journal_data.v1",
                "items": [_lifecycle_data(row) for row in rows],
                "execution_authority": False,
            }
        except Exception:  # noqa: BLE001 -- missing/unknown journal is never an empty list.
            raise HTTPException(409, "live_journal_unconfirmed") from None

    def reconcile(self, request):
        _need(type(request) is ReconcileRequest)
        _need(self.task is None or self.task.done())
        self.ready = False
        self.job = {
            "operation_id": uuid4().hex,
            "status": "RECONCILING_READ_ONLY",
            "challenge": None,
            "result": None,
            "error": None,
        }
        self.stop_requested = False
        self.task = asyncio.create_task(
            self._reconcile(request), name="ctcc-live-reconcile-only"
        )
        return self.snapshot()

    async def _reconcile(self, request):
        try:
            _, _, lifecycle = _modules()
            receipt = await lifecycle.reconcile_pending_owned_live_intent_v1(
                request.reservation_id
            )
            result = _lifecycle_data(
                lifecycle.live_lifecycle_public_projection_v1(receipt)
            )
            self.job["result"] = result
            self.job["status"] = result["status"]
        except asyncio.CancelledError:
            self.job["status"] = "UNCERTAIN"
            self.job["error"] = "stopped_reconciliation_required"
            raise
        except Exception:  # noqa: BLE001 -- keep reservation/intent and source journal intact.
            self.job["status"] = "UNCERTAIN"
            self.job["error"] = "reconciliation_unconfirmed"

    def stop(self, operation_id):
        _need(self.job is not None and self.job["operation_id"] == operation_id)
        self.ready = False
        self.stop_requested = True
        if self.session is not None:
            # Revoke synchronously BEFORE any subsequent DB/cleanup await.
            _, qualification, _ = _modules()
            qualification.revoke_live_operator_session_v1(self.session)
        self.job["status"] = "STOP_REQUESTED"
        self.job["challenge"] = None
        return self.snapshot()

    async def shutdown(self):
        if self.job is not None and self.task is not None and not self.task.done():
            self.stop(self.job["operation_id"])
            # Core network/SQL calls have their own bounded timeouts. Never
            # erase the worker, intent or risk hold just to close the UI.
            try:
                await self.task
            except asyncio.CancelledError:
                # The core context has completed its finally block. Its durable
                # outcome stays unknown until explicit read-only reconciliation.
                self.ready = False


def _build_app(worker=None):
    chosen = LiveToolWorker() if worker is None else worker

    @asynccontextmanager
    async def lifespan(_app):
        await chosen.initialize()
        try:
            yield
        finally:
            await chosen.shutdown()

    application = FastAPI(
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
    )
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"]
    )

    @application.exception_handler(RequestValidationError)
    async def private_validation(_request, _error):
        # FastAPI's default input echo may contain a mistyped private token or
        # confirmation. Only a fixed code crosses the authenticated boundary.
        return JSONResponse({"detail": "live_tool_request_invalid"}, status_code=422)

    @application.middleware("http")
    async def no_cache(request, call_next):
        try:
            loopback = (
                request.client is not None
                and ipaddress.ip_address(request.client.host).is_loopback
            )
        except ValueError:
            loopback = False
        result = (
            await call_next(request)
            if loopback
            else JSONResponse(
                {"detail": "live_tool_loopback_required"}, status_code=403
            )
        )
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "no-referrer"
        return result

    @application.get("/", include_in_schema=False)
    async def dashboard():
        from app.dashboard.native_live_manual_v1 import render_live_manual_dashboard_v1

        return render_live_manual_dashboard_v1()

    @application.get("/liveness")
    async def liveness():
        return {"status": "LIVE_MANUAL_TOOL_ALIVE", "execution_authority": False}

    @application.get(
        "/api/live-tool/status", dependencies=[Depends(require_ctcc_token)]
    )
    async def status():
        return chosen.snapshot()

    @application.post(
        "/api/live-tool/check-setup", dependencies=[Depends(require_ctcc_token)]
    )
    async def check_setup():
        return await chosen.initialize()

    @application.post(
        "/api/live-tool/prepare",
        status_code=202,
        dependencies=[Depends(require_ctcc_token)],
    )
    async def prepare(body: PrepareRequest):
        return chosen.start(body)

    @application.post(
        "/api/live-tool/approve", dependencies=[Depends(require_ctcc_token)]
    )
    async def approve(body: ApprovalRequest):
        return chosen.approve(body)

    @application.post("/api/live-tool/stop", dependencies=[Depends(require_ctcc_token)])
    async def stop(body: OperationRequest):
        return chosen.stop(body.operation_id)

    @application.get(
        "/api/live-tool/journal", dependencies=[Depends(require_ctcc_token)]
    )
    async def journal():
        return await chosen.journal()

    @application.post(
        "/api/live-tool/reconcile",
        status_code=202,
        dependencies=[Depends(require_ctcc_token)],
    )
    async def reconcile(body: ReconcileRequest):
        return chosen.reconcile(body)

    return application


app = _build_app()
