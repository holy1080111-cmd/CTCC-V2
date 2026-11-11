"""Opt-in, one-task Live GET lease for a future read-only account preflight.

The existing Live client and every write authority remain unchanged. This
module pins transport settings and credentials, but does not claim a complete
account snapshot, an account identity match, or permission to submit orders.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import Settings
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    OkxLivePrivateRestClient,
    _validated_origin,
)

_LIVE_HOSTS = frozenset({"openapi.okx.com", "eea.okx.com"})
_UID = re.compile(r"[1-9][0-9]{0,39}\Z")


@dataclass(slots=True, repr=False)
class _LiveLeaseState:
    active: bool = True


@dataclass(frozen=True, slots=True, repr=False)
class _LiveGetPin:
    credentials: tuple[str, str, str] = field(repr=False)
    origin: str
    timeout_seconds: float
    configured_retries: int
    user_agent: str
    expected_uid: str = field(repr=False)
    expected_main_uid: str = field(repr=False)
    environment: str
    trading_mode: str
    live_trading: bool
    live_enabled: bool
    order_writes: bool
    auto_execution: bool
    auto_reconcile_on_start: bool
    auto_trade: bool


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class LivePinnedGetLeaseV1:
    """Private credential snapshot, bound to one client and one asyncio task."""

    _owner: OkxLivePinnedGetClientV1 = field(repr=False)
    _settings: Settings = field(repr=False)
    _task: asyncio.Task[Any] = field(repr=False)
    _pin: _LiveGetPin = field(repr=False)
    _state: _LiveLeaseState = field(repr=False)
    binding_id: str

    def verify_current(self) -> None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if (
            not self._state.active
            or _LIVE_GET_LEASE.get() is not self
            or task is not self._task
            or self._owner.settings is not self._settings
        ):
            raise OkxPrivateApiError(
                "Live GET lease is no longer active",
                code="live_get_lease_invalid",
            )
        try:
            current = self._owner._unbound_live_get_pin()
            self._owner._check_live_read_transport()
        except OkxPrivateApiError as exc:
            if exc.code == "live_read_transport_invalid":
                raise
            raise OkxPrivateApiError(
                "Live GET lease settings changed",
                code="live_get_lease_settings_changed",
            ) from None
        except Exception:  # noqa: BLE001 - never expose private setting values
            raise OkxPrivateApiError(
                "Live GET lease settings changed",
                code="live_get_lease_settings_changed",
            ) from None
        if current != self._pin:
            raise OkxPrivateApiError(
                "Live GET lease settings changed",
                code="live_get_lease_settings_changed",
            )


_LIVE_GET_LEASE: ContextVar[LivePinnedGetLeaseV1 | None] = ContextVar(
    "ctcc_okx_live_pinned_get_lease_v1", default=None
)


class OkxLivePinnedGetClientV1(OkxLivePrivateRestClient):
    """Dedicated Live reader: GET requires a current lease; writes stay denied."""

    def _check_live_read_transport(self) -> None:
        if type(self) is not OkxLivePinnedGetClientV1:
            raise OkxPrivateApiError(
                "Live GET transport is not owned",
                code="live_read_transport_invalid",
            )
        settings = self.settings
        if (
            settings.okx_live_enabled is not True
            or settings.live_trading is not False
            or settings.okx_live_allow_order_writes is not False
            or settings.okx_live_auto_execution is not False
            or settings.okx_live_auto_reconcile_on_start is not False
            or settings.auto_trade is not False
        ):
            raise OkxPrivateApiError(
                "Live GET-only configuration is not active",
                code="live_read_configuration_invalid",
            )
        if self.settings.environment == "test":
            client = self._external_client
            if client is None:
                raise OkxPrivateApiError(
                    "Live test GET requires a controlled transport",
                    code="live_read_transport_invalid",
                )
            self._validate_external_client(client)
            if client.follow_redirects:
                raise OkxPrivateApiError(
                    "Live test GET cannot follow redirects",
                    code="live_read_transport_invalid",
                )
        elif (
            self.settings.environment != "production"
            or self._external_client is not None
            or self._clock_injected
        ):
            raise OkxPrivateApiError(
                "Live GET transport is not runtime-owned",
                code="live_read_transport_invalid",
            )

    def _unbound_live_get_pin(self) -> _LiveGetPin:
        settings = self.settings
        if not settings.okx_live_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Live credentials are not configured", code="credentials_missing"
            )
        expected_uid = settings.okx_live_expected_uid.get_secret_value()
        expected_main_uid = settings.okx_live_expected_main_uid.get_secret_value()
        if (
            _UID.fullmatch(expected_uid) is None
            or _UID.fullmatch(expected_main_uid) is None
        ):
            raise OkxPrivateApiError(
                "Live account identity is not pinned",
                code="live_read_identity_unpinned",
            )
        return _LiveGetPin(
            credentials=(
                settings.okx_live_api_key.get_secret_value(),
                settings.okx_live_api_secret.get_secret_value(),
                settings.okx_live_api_passphrase.get_secret_value(),
            ),
            origin=_validated_origin(settings.okx_live_rest_base_url, _LIVE_HOSTS),
            timeout_seconds=settings.okx_live_timeout_seconds,
            configured_retries=settings.okx_live_read_max_retries,
            user_agent=f"CTCC-V2/{settings.app_version}",
            expected_uid=expected_uid,
            expected_main_uid=expected_main_uid,
            environment=settings.environment,
            trading_mode=settings.trading_mode,
            live_trading=settings.live_trading,
            live_enabled=settings.okx_live_enabled,
            order_writes=settings.okx_live_allow_order_writes,
            auto_execution=settings.okx_live_auto_execution,
            auto_reconcile_on_start=settings.okx_live_auto_reconcile_on_start,
            auto_trade=settings.auto_trade,
        )

    @contextmanager
    def pinned_get_lease(self) -> Iterator[LivePinnedGetLeaseV1]:
        """Open one Live GET lease; a detached task cannot inherit authority."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if (
            type(self) is not OkxLivePinnedGetClientV1
            or task is None
            or _LIVE_GET_LEASE.get() is not None
        ):
            raise OkxPrivateApiError(
                "Live GET lease cannot be nested or substituted",
                code="live_get_lease_invalid",
            )
        self._check_live_read_transport()
        pin = self._unbound_live_get_pin()
        state = _LiveLeaseState()
        lease = LivePinnedGetLeaseV1(
            _owner=self,
            _settings=self.settings,
            _task=task,
            _pin=pin,
            _state=state,
            binding_id=secrets.token_hex(32),
        )
        token = _LIVE_GET_LEASE.set(lease)
        try:
            lease.verify_current()
            yield lease
        finally:
            state.active = False
            _LIVE_GET_LEASE.reset(token)

    def _current_lease(self) -> LivePinnedGetLeaseV1:
        lease = _LIVE_GET_LEASE.get()
        if lease is None or lease._owner is not self:
            raise OkxPrivateApiError(
                "Live GET requires its own current lease",
                code="live_get_lease_invalid",
            )
        lease.verify_current()
        return lease

    @property
    def active_read_session_binding_id(self) -> str:
        return self._current_lease().binding_id

    def _before_send(self, *, method: str, path: str) -> None:
        super()._before_send(method=method, path=path)
        self._current_lease()

    def _final_dispatch_check(self, *, method: str, path: str) -> None:
        super()._before_send(method=method, path=path)
        self._current_lease()

    def _after_response_check(self, *, method: str, path: str) -> None:
        self._current_lease()

    def _credentials(self) -> tuple[str, str, str]:
        return self._current_lease()._pin.credentials

    def _rest_base_url(self) -> str:
        return self._current_lease()._pin.origin

    def _timeout_seconds(self) -> float:
        return self._current_lease()._pin.timeout_seconds

    def _read_max_retries(self) -> int:
        self._current_lease()
        return 0

    def _user_agent(self) -> str:
        return self._current_lease()._pin.user_agent

    def _extra_headers(self) -> dict[str, str]:
        return {}
