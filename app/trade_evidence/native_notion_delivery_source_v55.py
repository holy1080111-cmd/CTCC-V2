"""Genuine historical native55 report source; no trading or account permit.

Only actual configured DB sessions and final immutable native report lineage
can mint this task-bound, one-use Notion capability. DTOs, old reports, local
files, projection booleans and an expired trading owner never supply it.
"""

import asyncio
import sys as _native_sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from weakref import ReferenceType, WeakKeyDictionary, finalize, ref

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings as configured
from app.database.repositories import native_closed_reporting_v55 as reports
from app.domain import native_clock
from app.domain.source_primitives import decode, sha, utc_from_ns, validate_stamps
from app.trade_evidence import (
    delivery_source_authority,
    notion_binding,
    outbox,
    storage,
)
from app.trade_evidence import notion_adapter as notion
from app.trade_evidence.submission_reporting import pin, queue
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as guard

_REPOSITORY_KEY_V55 = object()
_INVOKE_KEY_V55 = object()
_ISSUING = {}
_CAPS = WeakKeyDictionary()
_UNKNOWN = {}
_MAX_CAPS = 16
_SECONDS = 30
_ADAPTER_CALL_V55 = notion.NotionDeliveryAdapter.__call__


class NativeNotionSourceUnavailable(ValueError):
    """Only a fixed code; no DSN, credential, SQL, account or path detail."""


def _deny():
    raise NativeNotionSourceUnavailable(
        "notion_native_historical_source_unavailable"
    ) from None


class OwnedNativeNotionDeliverySourceV55:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    @property
    def execution_authority(self):
        return False

    def close(self):
        state = _CAPS.pop(self, None)
        if state is not None:
            state["finalizer"].detach()
            _retire(state)


def _retire(state):
    if state.get("closed", False):
        return
    state["closed"] = True
    try:
        state["root_context"].__exit__(None, None, None)
    except BaseException as error:  # noqa: BLE001 -- Retain unknown source custody before rethrowing shutdown signals.
        _UNKNOWN[state["token"]] = state
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny()


def _check(state):
    guard.check_saved_native_guard_v1(state)
    stamp = native_clock.native_stamp()
    validate_stamps((state["last"], stamp))
    state["last"] = stamp
    if (
        state.get("closed", False)
        or asyncio.current_task() is not state["task"]()
        or account._context()[:3] != state["context"][:3]
        or configured.get_settings() is not state["settings"]
        or account._v3_data_pin(state["settings"].__dict__) != state["settings_pin"]
        or utc_from_ns(stamp["utc_ns"]) >= state["expires_at"]
        or stamp["monotonic_ns"] >= state["monotonic_deadline_ns"]
    ):
        _deny()
    return stamp


def _state(permit):
    value = (
        _CAPS.get(permit)
        if type(permit) is OwnedNativeNotionDeliverySourceV55
        else None
    )
    if type(value) is not dict:
        _deny()
    _check(value)
    return value


def _adapter_ref(state, name):
    saved = state.get(name)
    return saved() if type(saved) is ReferenceType else None


def _metadata(value):
    if (
        type(value) is not tuple
        or len(value) != 10
        or type(value[0]) is not str
        or type(value[1]) is not str
        or not value[0]
        or value[0] != value[1]
        or type(value[2]) is not str
        or value[2] != "none"
        or type(value[3]) is not int
        or value[3] <= 0
        or any(item is not False for item in value[4:7])
        or type(value[7]) is not int
        or not 0 < value[7] < 2**31
        or type(value[8]) is not str
        or not value[8].isascii()
        or not value[8].isdigit()
        or not 0 < len(value[8]) <= 20
        or int(value[8]) <= 0
        or type(value[9]) is not datetime
        or value[9].tzinfo is not UTC
    ):
        _deny()
    outbox._utc(value[9])
    return value


def _replay_actual_reporting_factory_v55(binding, *, key):
    if key is not reports._SOURCE_FACTORY_KEY_V55 or type(binding) is not object:
        _deny()
    state = _ISSUING.get(binding)
    if type(state) is not dict or state["phase"] != "READING":
        _deny()
    _check(state)
    if (
        type(state["factory"]) is not async_sessionmaker
        or state["factory"].class_ is not AsyncSession
        or state["factory"].kw.get("bind") is not state["engine"]
        or history._factory_pin(state["factory"]) != state["factory_pin"]
        or account._database_connection_pin_v4(state["factory"])
        != state["database_pin"]
    ):
        _deny()
    return (
        state["factory"],
        state["root"],
        state["report_id"],
        state["namespace"],
        state["envelope_sha256"],
    )


async def acquire_native_notion_delivery_source_v55(
    *, root, report_id, expected_envelope_sha256
):
    """No caller factory, clock, eligibility flag, token, callback or owner input."""
    if len(_CAPS) + len(_ISSUING) + len(_UNKNOWN) >= _MAX_CAPS:
        _deny()
    settings = configured.get_settings()
    if (
        type(settings) is not configured.Settings
        or settings.notion_outbox_enabled is not True
        or settings.submission_outbox_enabled is not True
        or not settings.notion_outbox_root
        or not settings.submission_outbox_root
    ):
        _deny()
    if type(root) is not type(Path()):
        _deny()
    checked_root = storage._root_path(root)
    if checked_root != storage._root_path(
        Path(settings.notion_outbox_root)
    ) or checked_root != storage._root_path(Path(settings.submission_outbox_root)):
        _deny()
    outbox._name(report_id)
    expected = pin(expected_envelope_sha256)
    namespace = queue(settings.submission_outbox_queue_namespace)
    stamp = native_clock.native_stamp()
    state = {
        "token": object(),
        "task": ref(asyncio.current_task()),
        "settings": settings,
        "settings_pin": account._v3_data_pin(settings.__dict__),
        "last": stamp,
        "context": account._context()[:3],
        "expires_at": utc_from_ns(stamp["utc_ns"]) + timedelta(seconds=_SECONDS),
        "monotonic_deadline_ns": stamp["monotonic_ns"] + _SECONDS * 1_000_000_000,
        "root": checked_root,
        "report_id": report_id,
        "namespace": namespace,
        "envelope_sha256": expected,
        "phase": "READING",
        "closed": False,
    }
    guard.save_native_guard_v1(state, _NATIVE_GUARD)
    context = outbox._root_context(checked_root)
    state["root_context"], state["directory"] = context, context.__enter__()
    engine = None
    binding = object()
    kept = False
    try:
        destination = notion_binding.load_destination(
            Path(settings.notion_outbox_destination_file),
            settings.notion_outbox_destination_sha256,
        )
        state["destination_sha256"] = notion.destination_sha256(destination)
        _check(state)
        # This worker owns this real engine on its own event loop. The issuer
        # cannot accept an injected sessionmaker, DB connection or mocked row.
        engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
            connect_args={"timeout": 5, "command_timeout": 5},
        )
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        state.update(
            engine=engine,
            factory=factory,
            factory_pin=history._factory_pin(factory),
            database_pin=account._database_connection_pin_v4(factory),
        )
        _ISSUING[binding] = state
        async with asyncio.timeout(_SECONDS):
            first = await reports.read_native_notion_projection_v55(
                binding, key=_REPOSITORY_KEY_V55
            )
            _check(state)
            second = await reports.read_native_notion_projection_v55(
                binding, key=_REPOSITORY_KEY_V55
            )
            _check(state)
            if (
                type(first) is not reports.NativeNotionProjectionReadbackV55
                or type(second) is not reports.NativeNotionProjectionReadbackV55
                or type(first.session) is not AsyncSession
                or type(second.session) is not AsyncSession
                or first.session is second.session
                or first.proof_json != second.proof_json
                or not 0 < len(first.proof_json) <= 65536
            ):
                _deny()
            a, b = (
                _metadata(first.database_metadata),
                _metadata(second.database_metadata),
            )
            if a[:7] != b[:7] or a[8] == b[8] or a[9] > b[9]:
                _deny()
            document = decode(second.proof_json, 65536)
            if (
                document["schema_version"]
                != "ctcc.native_historical_notion_projection.v1"
                or document["envelope_sha256"] != expected
                or document["report_id"] != report_id
            ):
                _deny()
            state.update(
                proof_json=second.proof_json,
                proof_sha256=sha(second.proof_json),
                metadata=(a, b),
                payload_json=document["payload_json"],
                phase="DISPOSING",
            )
            _ISSUING.pop(binding)
            async with asyncio.timeout(5):
                await engine.dispose()
            engine = None
            _check(state)
        state.pop("engine")
        state.pop("factory")
        state.pop("factory_pin")
        state.pop("database_pin")
        state["phase"] = "PREPARED"
        permit = object.__new__(OwnedNativeNotionDeliverySourceV55)
        _CAPS[permit] = state
        state["finalizer"] = finalize(permit, _retire, state)
        kept = True
        return permit
    except asyncio.CancelledError:
        raise
    except NativeNotionSourceUnavailable:
        raise
    except Exception:  # noqa: BLE001 -- Redact private source and database failures.
        _deny()
    finally:
        _ISSUING.pop(binding, None)
        try:
            if engine is not None:
                try:
                    async with asyncio.timeout(5):
                        await engine.dispose()
                except BaseException as error:  # noqa: BLE001 -- Retain custody on a failed close before handling shutdown.
                    _UNKNOWN[state["token"]] = state
                    if isinstance(error, asyncio.CancelledError):
                        raise asyncio.CancelledError() from None
                    if isinstance(error, (KeyboardInterrupt, SystemExit)):
                        raise type(error)() from None
                    _deny()
        finally:
            if not kept:
                _retire(state)


def require_prepared_native_notion_source_v55(permit, *, root, report_ids):
    state = _state(permit)
    if (
        state["phase"] not in ("PREPARED", "PREFLIGHT")
        or storage._root_path(root) != state["root"]
        or type(report_ids) is not tuple
        or report_ids != (state["report_id"],)
    ):
        _deny()


def require_native_notion_preflight_v55(permit, *, client, destination):
    state = _state(permit)
    target = notion._copy(destination, notion.NotionDestination)
    notion._client(client, initial=True)
    if (
        state["phase"] not in ("PREPARED", "PREFLIGHT")
        or type(client) is not httpx.AsyncClient
        or type(client._transport) is not httpx.AsyncHTTPTransport
        or "send" in vars(client)
        or "request" in vars(client)
        or notion.destination_sha256(target) != state["destination_sha256"]
        or (state.get("client") is not None and state["client"] is not client)
    ):
        _deny()
    state["client"], state["phase"] = client, "PREFLIGHT"


def consume_native_notion_dispatch_v55(permit, *, adapter, payload, claim):
    state = _state(permit)
    if (
        type(adapter) is not notion.NotionDeliveryAdapter
        or state.get("invoke_key") is not _INVOKE_KEY_V55
    ):
        _deny()
    require_native_notion_preflight_v55(
        permit, client=adapter._client, destination=adapter._destination
    )
    payload, claim = (
        outbox._copy(payload, outbox.OutboxPayload),
        outbox._copy(claim, outbox.ClaimToken),
    )
    if (
        state["phase"] != "PREFLIGHT"
        or claim.status != "dispatching"
        or payload.report_id != state["report_id"]
        or claim.report_id != state["report_id"]
        or claim.envelope_sha256 != state["envelope_sha256"]
        or payload.model_dump_json(round_trip=True) != state["payload_json"]
    ):
        _deny()
    # Spend before the first possible HTTP operation. A retry needs a new
    # genuine historical acquisition and the unchanged durable outbox policy.
    state.update(
        phase="DISPATCHING", adapter=ref(adapter), claim=claim, payload=payload
    )
    require_native_notion_dispatch_v55(permit, adapter=adapter)


def require_native_notion_dispatch_v55(permit, *, adapter):
    state = _state(permit)
    if (
        state["phase"] != "DISPATCHING"
        or _adapter_ref(state, "adapter") is not adapter
        or state["client"] is not adapter._client
        or "send" in vars(adapter._client)
        or "request" in vars(adapter._client)
        or type(adapter._client._transport) is not httpx.AsyncHTTPTransport
        or notion.destination_sha256(adapter._destination)
        != state["destination_sha256"]
    ):
        _deny()
    notion._client(adapter._client)
    claim = state["claim"]
    view = outbox.validate_outbox_view(
        outbox.read_job(state["root"], state["report_id"], clock=outbox.actual_utc)
    )
    head = view.head
    if (
        view.envelope.envelope_sha256 != state["envelope_sha256"]
        or view.envelope.payload != state["payload"]
        or view.status != "dispatching"
        or (
            head.revision,
            head.event_sha256,
            head.fence_token,
            head.worker_id,
            head.lease_expires_at,
        )
        != (
            claim.revision,
            claim.event_sha256,
            claim.fence_token,
            claim.worker_id,
            claim.lease_expires_at,
        )
        or outbox.actual_utc() >= claim.lease_expires_at
    ):
        _deny()


async def verify_native_notion_binding_v55(permit, *, client, token, destination):
    """Source guard remains active across every remote binding await."""
    require_native_notion_preflight_v55(permit, client=client, destination=destination)
    await notion_binding.verify_binding(client, token, destination)
    _state(permit)


async def _invoke_native_notion_adapter_v55(permit, adapter, payload, claim):
    state = _state(permit)
    if (
        _adapter_ref(state, "bound_adapter") is not adapter
        or adapter._source_permit is not permit
    ):
        _deny()
    if state.get("invoke_key") is not None:
        _deny()
    state["invoke_key"] = _INVOKE_KEY_V55
    try:
        # Capture the exact original body. The guarded coroutine checks the
        # whole source closure before EVERY resume, including all HTTP awaits.
        result = await _ADAPTER_CALL_V55(adapter, payload, claim)
        stamp = _check(state)
        if utc_from_ns(stamp["utc_ns"]) >= state["claim"].lease_expires_at:
            _deny()
        return result
    finally:
        permit.close()


def make_native_notion_adapter_v55(permit, *, adapter):
    state = _state(permit)
    if (
        state["phase"] not in ("PREPARED", "PREFLIGHT")
        or type(adapter) is not notion.NotionDeliveryAdapter
    ):
        _deny()
    if (
        _adapter_ref(state, "bound_adapter") is adapter
        and adapter._source_permit is permit
    ):
        return state["delivery_callable"]
    if adapter._source_permit is not None or state.get("bound_adapter") is not None:
        _deny()
    require_native_notion_preflight_v55(
        permit, client=adapter._client, destination=adapter._destination
    )
    state["bound_adapter"] = ref(adapter)
    adapter._source_permit = permit
    invoke = _invoke_native_notion_adapter_v55
    function_pin = (invoke, invoke.__code__, invoke.__closure__)
    permit_ref, adapter_ref = ref(permit), ref(adapter)

    def deliver(payload, claim):
        if (
            invoke is not function_pin[0]
            or invoke.__code__ is not function_pin[1]
            or invoke.__closure__ is not function_pin[2]
        ):
            _deny()
        actual_permit, actual_adapter = permit_ref(), adapter_ref()
        if actual_permit is None or actual_adapter is None:
            _deny()
        return invoke(actual_permit, actual_adapter, payload, claim)

    state["delivery_callable"] = deliver
    return deliver


_NATIVE_GUARD = guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    (
        "acquire_native_notion_delivery_source_v55",
        "verify_native_notion_binding_v55",
        "_invoke_native_notion_adapter_v55",
        "make_native_notion_adapter_v55",
    ),
    dependencies=(
        reports,
        reports.catalog,
        reports.report_schema,
        reports.forensic,
        reports.catalog.native_closure,
        reports.catalog.native_closure.zero,
        reports.catalog.native_terminal,
        reports.catalog.native_report,
        reports.catalog.native_report.schema,
        reports.catalog.native_report.helpers,
        reports.forensic.entry,
        reports.forensic.closing,
        reports.forensic.facts,
        reports.forensic.execution,
        reports.forensic.core,
        reports.forensics,
        reports.lineage55,
        notion,
        notion_binding,
        outbox,
        storage,
        delivery_source_authority,
        configured,
        account,
        history,
        native_clock,
    ),
    bindings=(
        (httpx.AsyncClient, "send", httpx.AsyncClient.send),
        (
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            httpx.AsyncHTTPTransport.handle_async_request,
        ),
        (AsyncSession, "execute", AsyncSession.execute),
        (AsyncSession, "scalar", AsyncSession.scalar),
        (AsyncSession, "scalars", AsyncSession.scalars),
        (AsyncSession, "get", AsyncSession.get),
        (AsyncSession, "__init__", AsyncSession.__init__),
        (async_sessionmaker, "__init__", async_sessionmaker.__init__),
        (async_sessionmaker, "__call__", async_sessionmaker.__call__),
    ),
)
