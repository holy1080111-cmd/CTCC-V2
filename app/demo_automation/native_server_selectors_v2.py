"""Configured Demo identity/role selectors for the same-task native caller.

The preliminary signed config is a selector only. Formal original CURRENT
independently replays identity and all raw pages. No registered site, financial
completeness, Arm, reservation, intent or order authority is asserted here.
"""

import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from weakref import WeakKeyDictionary

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.private_rest import (
    DemoConfigSelectorObservationV2,
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime

_SELECTIONS = WeakKeyDictionary()
_SELECTING = {}
_MAX_SELECTIONS = 16
_ROUTE_ARTIFACT_NAME = "configured_demo_official_route_evidence_v2.json"
_ROUTE_ARTIFACT_SHA = "426a0fe117bc756e2c8c2c850f5658134966e8be785d1f2aca46e4ed0e0d7544"
_ORIGIN = "https://openapi.okx.com"
_WS = "wss://wspap.okx.com"
_UID = re.compile(r"[1-9][0-9]{0,39}\Z", re.ASCII)


class NativeDemoCallerReadinessErrorV2(ValueError):
    """Closed code only: no DSN, UID, headers, credentials or remote message."""


def _need(value, code="native_demo_caller_not_ready"):
    if value is not True:
        raise NativeDemoCallerReadinessErrorV2(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class ConfiguredDemoRouteEvidenceV2:
    scope_config: history.NativeHistoryScopeV1
    current_plan: capture.CurrentDemoAccountCapturePlanV7
    selector_json: bytes = field(repr=False)
    selector_raw_body: bytes = field(repr=False)
    route_artifact_json: bytes = field(repr=False)
    route_artifact_sha256: str
    session_binding_id: str
    configured_origin: str
    selector_started_at: datetime
    selector_received_at: datetime
    selector_started_monotonic_ns: int
    selector_received_monotonic_ns: int
    binding_sha256: str
    purpose: str = field(
        default="same_configured_official_route_authenticated_selector_v2", init=False
    )
    registered_site: None = field(default=None, init=False)
    registered_site_proven: bool = field(default=False, init=False)
    registration_claim: None = field(default=None, init=False)
    account_complete: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


class ConfiguredDemoIdentitySelectionV2:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise NativeDemoCallerReadinessErrorV2("native_demo_selector_not_constructible")

    @property
    def scope_config(self):
        return _record(self)["evidence"].scope_config

    @property
    def current_plan(self):
        return _record(self)["evidence"].current_plan

    @property
    def route_evidence(self):
        return _record(self)["record"].route_evidence

    @property
    def factories(self):
        return _record(self)["factories"]

    def close(self):
        _need(type(self) is ConfiguredDemoIdentitySelectionV2)
        _SELECTIONS.pop(self, None)

    def __copy__(self):
        raise NativeDemoCallerReadinessErrorV2("native_demo_selector_not_transferable")

    def __deepcopy__(self, memo):
        raise NativeDemoCallerReadinessErrorV2("native_demo_selector_not_transferable")

    def __reduce_ex__(self, protocol):
        raise NativeDemoCallerReadinessErrorV2("native_demo_selector_not_transferable")


def _route_artifact():
    root = Path(__file__).parent
    _need(root.is_absolute(), "native_demo_route_artifact_unavailable")
    with storage._root_context(root) as directory:
        raw = directory.read(_ROUTE_ARTIFACT_NAME, 65536)
    _need(
        type(raw) is bytes
        and 0 < len(raw) <= 65536
        and sha(raw) == _ROUTE_ARTIFACT_SHA,
        "native_demo_route_artifact_changed",
    )
    value = decode(raw, 65536)
    _need(
        type(value) is dict
        and value["schema"] == "ctcc.root.configured_demo_route_evidence.v2"
        and value["environment"] == "demo"
        and value["configured_rest_origin"] == _ORIGIN
        and value["configured_public_ws_origin"] == _WS
        and value["registered_site"] is None
        and value["registered_site_proven"] is False
        and value["registration_claim"] is None
        and value["historical_diagnostic_can_grant_permission"] is False
        and value["fresh_same_configured_client_signed_GET_selector_required"] is True
        and value["formal_full_raw_native_CURRENT_independent_identity_replay_required"]
        is True
        and value["no_automatic_region_fallback"] is True
        and value["account_complete"] is False
        and value["execution_authority"] is False,
        "native_demo_route_purpose_invalid",
    )
    return raw


def _configured_inputs(private_client, read_session, instruments):
    settings = get_settings()
    _need(
        type(private_client) is OkxDemoPrivateRestClient
        and type(read_session) is DemoPinnedReadSession
        and private_client.settings is settings
        and read_session._owner is private_client
        and read_session._settings is settings
        and private_client._external_client is None
        and private_client._clock_injected is False,
        "native_demo_original_configured_client_required",
    )
    read_session.verify_current()
    _need(
        settings.environment != "test"
        and settings.okx_demo_enabled is True
        and read_session._origin == _ORIGIN
        and settings.okx_demo_rest_base_url == _ORIGIN
        and read_session._read_max_retries == 0,
        "native_demo_configured_route_or_retries_invalid",
    )
    _need(
        type(instruments) is tuple
        and 0 < len(instruments) <= 20
        and all(
            type(item) is str and item in settings.okx_demo_allowed_symbol_list
            for item in instruments
        )
        and len(set(instruments)) == len(instruments),
        "native_demo_configured_instruments_invalid",
    )
    return settings


def _record(selection):
    state = (
        _SELECTIONS.get(selection)
        if type(selection) is ConfiguredDemoIdentitySelectionV2
        else None
    )
    _need(
        type(state) is dict
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop(),
        "native_demo_selector_origin_changed",
    )
    _need(
        _configured_inputs(
            state["private_client"], state["read_session"], state["instruments"]
        )
        is state["settings"],
        "native_demo_selector_settings_changed",
    )
    _need(
        _server_settings_pin(state["settings"]) == state["server_settings_pin"],
        "native_demo_selector_server_database_settings_changed",
    )
    _need(
        _route_artifact() == state["evidence"].route_artifact_json,
        "native_demo_route_artifact_changed",
    )
    stamp = native_stamp()
    validate_stamps((state["received_stamp"], stamp))
    _need(
        stamp["monotonic_ns"] < state["deadline_ns"]
        and utc_from_ns(stamp["utc_ns"]) < state["deadline_utc"],
        "native_demo_selector_expired",
    )
    _need(
        history._factory_pin(state["factories"].custody_factory)
        == state["factory_pins"][0]
        and history._factory_pin(state["factories"].frame_reader_factory)
        == state["factory_pins"][1]
        and state["record"].custody_factory is state["factories"].custody_factory
        and state["record"].raw_reader_factory
        is state["factories"].frame_reader_factory
        and state["record"].writer_factory is state["factories"].writer_factory,
        "native_demo_selector_factories_changed",
    )
    _need(
        capture.plan_sha256(state["evidence"].current_plan) == state["plan_sha256"],
        "native_demo_selector_plan_changed",
    )
    return state


def _server_settings_pin(settings):
    custody_url = settings.okx_demo_native_custody_database_url.get_secret_value()
    reader_url = settings.okx_demo_native_frame_reader_database_url.get_secret_value()
    _need(
        all(
            type(value) is str and 0 < len(value) <= 8192
            for value in (settings.database_url, custody_url, reader_url)
        ),
        "native_demo_restricted_database_settings_missing",
    )
    # Private in-memory pin only; never returned through evidence or reports.
    return sha(canonical([settings.database_url, custody_url, reader_url]))


def _parse_selector_rows_v2(rows):
    _need(
        type(rows) is list and len(rows) == 1 and type(rows[0]) is dict,
        "native_demo_selector_config_cardinality",
    )
    row = rows[0]
    uid, main_uid = row.get("uid"), row.get("mainUid")
    _need(
        type(uid) is str
        and _UID.fullmatch(uid) is not None
        and type(main_uid) is str
        and _UID.fullmatch(main_uid) is not None,
        "native_demo_selector_exact_identity_missing",
    )
    parsed = canonical(rows)
    _need(len(parsed) <= 65536, "native_demo_selector_response_bound")
    return uid, main_uid, parsed


async def select_configured_demo_identity_v2(
    *,
    private_client,
    read_session,
    instrument_ids,
    retain_read_transport=False,
    source_only_role_preflight=False,
):
    """One real configured signed GET; no HTTP UID/mainUID/route proof input."""
    token = object()
    factories = result = None
    returned = False
    try:
        _need(
            len(_SELECTIONS) + len(_SELECTING) < _MAX_SELECTIONS,
            "native_demo_selector_capacity",
        )
        _SELECTING[token] = asyncio.current_task()
        _need(type(retain_read_transport) is bool)
        _need(type(source_only_role_preflight) is bool)
        settings = _configured_inputs(private_client, read_session, instrument_ids)
        server_pin = _server_settings_pin(settings)
        artifact = _route_artifact()
        factories = select_configured_native_demo_factories_v2()
        if source_only_role_preflight:
            # This opt-in source diagnostic proves the actual selector-owned
            # factory set before its first signed GET. Existing callers keep
            # their original behavior and receipts.
            from app.core.native_role_preflight_v1 import (
                verify_selected_native_roles_v1,
            )

            async with asyncio.timeout(45):
                roles = await verify_selected_native_roles_v1(factories)
            _need(
                type(roles) is dict
                and roles.get("database_head") == "0055"
                and roles.get("account_complete") is False
                and roles.get("execution_authority") is False,
                "native_demo_source_only_pre_get_role_preflight_failed",
            )
        before = native_stamp()
        async with asyncio.timeout(5):
            if retain_read_transport:
                observation = await private_client.account_config_selector_v2(
                    retain_network=True
                )
            else:
                observation = await private_client.account_config_selector_v2()
        after = native_stamp()
        validate_stamps((before, after))
        _need(
            _configured_inputs(private_client, read_session, instrument_ids)
            is settings,
            "native_demo_selector_settings_changed",
        )
        _need(
            _server_settings_pin(settings) == server_pin,
            "native_demo_selector_server_database_settings_changed",
        )
        _need(
            type(observation) is DemoConfigSelectorObservationV2
            and type(observation.data) is tuple
            and type(observation.raw_body) is bytes
            and 0 < len(observation.raw_body) <= 65536
            and type(observation.request_signature) is str
            and re.fullmatch(r"[A-Za-z0-9+/]{43}=", observation.request_signature)
            is not None,
            "native_demo_selector_original_observation_required",
        )
        uid, main_uid, parsed = _parse_selector_rows_v2(list(observation.data))
        scope = history.NativeHistoryScopeV1(
            uid, main_uid, _ROUTE_ARTIFACT_SHA, instrument_ids
        )
        at = utc_from_ns(after["utc_ns"])
        end = at.replace(microsecond=(at.microsecond // 1000) * 1000)
        plan = capture.CurrentDemoAccountCapturePlanV7(
            contract_version="ctcc.demo_current_account_plan.v7",
            plan_id="configured-current55-" + uuid.uuid4().hex,
            created_at=at,
            expected_uid=uid,
            expected_main_uid=main_uid,
            session_binding_id=read_session.binding_id,
            settlement_currency="USDT",
            leverage_instrument_ids=instrument_ids,
            history_start=end - timedelta(days=7),
            history_end=end,
            registration_region="global",
            origin=_ORIGIN,
            registration_evidence_sha256=_ROUTE_ARTIFACT_SHA,
        )
        binding = sha(
            canonical(
                {
                    "purpose": "configured_demo_route_selector_v2",
                    "route_artifact_sha256": _ROUTE_ARTIFACT_SHA,
                    "parsed_selector_sha256": sha(parsed),
                    "raw_selector_sha256": sha(observation.raw_body),
                    "uid_sha256": sha(uid.encode("ascii")),
                    "main_uid_sha256": sha(main_uid.encode("ascii")),
                    "session_binding_id": read_session.binding_id,
                    "origin": _ORIGIN,
                    "scope_sha256": sha(
                        canonical(
                            {
                                "uid": uid,
                                "main_uid": main_uid,
                                "instruments": list(instrument_ids),
                            }
                        )
                    ),
                    "plan_sha256": capture.plan_sha256(plan),
                    "registered_site_proven": False,
                }
            )
        )
        evidence = ConfiguredDemoRouteEvidenceV2(
            scope,
            plan,
            parsed,
            observation.raw_body,
            artifact,
            _ROUTE_ARTIFACT_SHA,
            read_session.binding_id,
            _ORIGIN,
            utc_from_ns(before["utc_ns"]),
            at,
            before["monotonic_ns"],
            after["monotonic_ns"],
            binding,
        )
        record = ConfiguredDemoIdentityRecordV2(
            read_session,
            private_client,
            plan,
            scope,
            factories.custody_factory,
            factories.frame_reader_factory,
            factories.writer_factory,
            evidence,
        )
        result = object.__new__(ConfiguredDemoIdentitySelectionV2)
        _SELECTIONS[result] = {
            "task": asyncio.current_task(),
            "loop": asyncio.get_running_loop(),
            "private_client": private_client,
            "read_session": read_session,
            "settings": settings,
            "server_settings_pin": server_pin,
            "instruments": instrument_ids,
            "evidence": evidence,
            "record": record,
            "selector_signature": observation.request_signature,
            "factories": factories,
            "factory_pins": (
                history._factory_pin(factories.custody_factory),
                history._factory_pin(factories.frame_reader_factory),
            ),
            "plan_sha256": capture.plan_sha256(plan),
            "received_stamp": after,
            "deadline_ns": after["monotonic_ns"]
            + plan.max_batch_seconds * 1_000_000_000,
            "deadline_utc": at + timedelta(seconds=plan.max_batch_seconds),
        }
        _SELECTING.pop(token)
        _record(result)
        returned = True
        return result
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except NativeDemoCallerReadinessErrorV2:
        raise
    except Exception:  # noqa: BLE001 -- remote messages, UID, credentials and source errors remain private.
        raise NativeDemoCallerReadinessErrorV2(
            "native_demo_identity_selector_failed"
        ) from None
    finally:
        _SELECTING.pop(token, None)
        if not returned:
            if result is not None:
                _SELECTIONS.pop(result, None)
            if factories is not None:
                await _dispose_failed_selection_factories_v2(factories)


async def _dispose_failed_selection_factories_v2(factories):
    """Failed signed GETs cannot leave a hidden restricted engine behind."""
    pending = asyncio.gather(
        factories.frame_reader_engine.dispose(),
        factories.custody_engine.dispose(),
        return_exceptions=True,
    )
    try:
        async with asyncio.timeout(5):
            results = await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(pending)
        except BaseException:  # noqa: BLE001 -- cancellation remains authoritative
            pending.cancel()
        raise asyncio.CancelledError() from None
    except Exception:  # noqa: BLE001 -- no DSN or transport detail escapes
        pending.cancel()
        raise NativeDemoCallerReadinessErrorV2(
            "native_demo_selector_cleanup_uncertain"
        ) from None
    if any(isinstance(item, BaseException) for item in results):
        raise NativeDemoCallerReadinessErrorV2(
            "native_demo_selector_cleanup_uncertain"
        ) from None


def _replay_configured_demo_identity_selection_v2(selection, *, key):
    """Only the original configured Control owner consumes the genuine record."""
    owner_module = sys.modules.get(
        "app.trade_qualification.native_demo_control_owner_v2"
    )
    _need(type(owner_module) is ModuleType, "native_demo_selector_source_key_required")
    original_key = vars(owner_module).get("_SOURCE_KEY")
    _need(
        type(original_key) is object and key is original_key,
        "native_demo_selector_source_key_required",
    )
    return _record(selection)["record"]


@dataclass(frozen=True, slots=True, repr=False)
class ConfiguredDemoIdentityRecordV2:
    read_session: DemoPinnedReadSession = field(repr=False)
    private_client: OkxDemoPrivateRestClient = field(repr=False)
    current_plan: capture.CurrentDemoAccountCapturePlanV7
    scope_config: history.NativeHistoryScopeV1
    custody_factory: object = field(repr=False)
    raw_reader_factory: object = field(repr=False)
    writer_factory: object = field(repr=False)
    route_evidence: ConfiguredDemoRouteEvidenceV2


@dataclass(frozen=True, slots=True, repr=False)
class ConfiguredNativeDemoFactoriesV2:
    writer_factory: object
    custody_factory: object
    frame_reader_factory: object
    custody_engine: object = field(repr=False)
    frame_reader_engine: object = field(repr=False)


def _database_route(url):
    _need(
        type(url) is URL
        and url.drivername == "postgresql+asyncpg"
        and type(url.username) is str
        and len(url.username) > 0
        and type(url.host) is str
        and len(url.host) > 0
        and type(url.database) is str
        and len(url.database) > 0
        and not url.query,
        "native_demo_restricted_database_route_invalid",
    )
    _need(
        url.port is None or (type(url.port) is int and 1 <= url.port <= 65535),
        "native_demo_restricted_database_route_invalid",
    )
    return (
        url.drivername,
        url.host,
        5432 if url.port is None else url.port,
        url.database,
    )


def select_configured_native_demo_factories_v2():
    """Actual server-private DSNs only; native consumers verify real role/ACL."""
    try:
        from app.database.session import AsyncSessionFactory

        settings = get_settings()
        _need(
            runtime._configured_factory(AsyncSessionFactory),
            "native_demo_configured_writer_factory_required",
        )
        custody_raw = settings.okx_demo_native_custody_database_url.get_secret_value()
        reader_raw = (
            settings.okx_demo_native_frame_reader_database_url.get_secret_value()
        )
        _need(
            type(custody_raw) is str
            and len(custody_raw) > 0
            and type(reader_raw) is str
            and len(reader_raw) > 0,
            "native_demo_restricted_database_settings_missing",
        )
        writer_url = AsyncSessionFactory.kw["bind"].url
        custody_url, reader_url = make_url(custody_raw), make_url(reader_raw)
        _need(
            _database_route(writer_url)
            == _database_route(custody_url)
            == _database_route(reader_url)
            and len({writer_url.username, custody_url.username, reader_url.username})
            == 3,
            "native_demo_distinct_restricted_database_roles_required",
        )
        custody_engine = create_async_engine(
            custody_url, poolclass=NullPool, pool_pre_ping=True
        )
        reader_engine = create_async_engine(
            reader_url, poolclass=NullPool, pool_pre_ping=True
        )
        _need(
            custody._native_engine(custody_engine)
            and custody._native_engine(reader_engine),
            "native_demo_restricted_native_engines_required",
        )
        return ConfiguredNativeDemoFactoriesV2(
            AsyncSessionFactory,
            async_sessionmaker(
                bind=custody_engine, autoflush=False, expire_on_commit=False
            ),
            async_sessionmaker(
                bind=reader_engine, autoflush=False, expire_on_commit=False
            ),
            custody_engine,
            reader_engine,
        )
    except NativeDemoCallerReadinessErrorV2:
        raise
    except Exception:  # noqa: BLE001 -- never serialize a private DSN/URL or role error.
        raise NativeDemoCallerReadinessErrorV2(
            "native_demo_restricted_factory_selection_failed"
        ) from None


def _configured_demo_selector_scan_tokens_v2(selection, *, key):
    """Actual earlier signature only to the private native scanner, even expired.

    This negative/evidence purpose does not replay a positive selection, renew
    anything or emit tokens through the consumer record, API or public report.
    """
    source_module = sys.modules.get(
        "app.trade_qualification.native_controlled_account_source_v6"
    )
    _need(type(source_module) is ModuleType, "native_demo_selector_scan_key_required")
    original_key = vars(source_module).get("_SCAN_KEY_V6")
    _need(
        type(original_key) is object and key is original_key,
        "native_demo_selector_scan_key_required",
    )
    state = (
        _SELECTIONS.get(selection)
        if type(selection) is ConfiguredDemoIdentitySelectionV2
        else None
    )
    _need(type(state) is dict, "native_demo_selector_scan_source_missing")
    signature = state.get("selector_signature")
    _need(
        type(signature) is str
        and re.fullmatch(r"[A-Za-z0-9+/]{43}=", signature) is not None,
        "native_demo_selector_scan_source_missing",
    )
    return (signature,)
