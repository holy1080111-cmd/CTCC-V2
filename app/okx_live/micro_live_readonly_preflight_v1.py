"""Read retained native Live custody without issuing account or order authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.config.settings import Settings
from app.domain.source_primitives import ClockStamp, canonical, sha, validate_stamps
from app.exchange.okx import live_account_current_chain_v1 as current
from app.exchange.okx import live_account_history_chain_v1 as history
from app.exchange.okx.live_account_config_custody_v1 import (
    _check_name,
    _read_custodied_live_account_config,
)
from app.exchange.okx.live_account_history_page_v1 import (
    LiveAccountHistorySelectorV1,
    select_live_account_history_page_v1,
)
from app.exchange.okx.live_read_lease_v1 import _LIVE_HOSTS
from app.exchange.okx.private_rest import _validated_origin
from app.exchange.okx.symbols import LIVE_BOUNDARY_INSTRUMENT_IDS
from app.trade_qualification.models import QualificationModel

_DIGEST = re.compile(r"[0-9a-f]{64}")
_UID = re.compile(r"[1-9][0-9]{0,39}")
_MAX_BYTES = 8192
_FAMILIES = (
    "fills_recent",
    "fills_history",
    "bills_recent",
    "bills_archive",
    "orders_recent",
    "orders_archive",
)
_Family = Literal[
    "fills_recent",
    "fills_history",
    "bills_recent",
    "bills_archive",
    "orders_recent",
    "orders_archive",
]
_Pin = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_PositiveDecimalText = Annotated[
    str, Field(min_length=1, max_length=32, pattern=r"^[0-9]+(?:\.[0-9]+)?$")
]


class MicroLiveReadbackError(ValueError):
    """A fixed refusal without private identifiers, paths or credentials."""


@dataclass(frozen=True, slots=True, repr=False)
class LiveHistoryCustodyPinV1:
    attempt_id: str
    custody_sha256: str
    selector: LiveAccountHistorySelectorV1


class LiveHistoryReadbackV1(QualificationModel):
    family: _Family
    custody_sha256: _Pin
    selector_plan_sha256: _Pin
    page_count: int = Field(ge=1, le=16)
    row_count: int = Field(ge=0, le=1600)
    capture_completed: ClockStamp
    terminal_page_verified: Literal[True] = True


class LiveOffSettingsV1(QualificationModel):
    origin: Literal["https://openapi.okx.com", "https://eea.okx.com"]
    instruments: tuple[Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"], ...] = Field(
        min_length=1, max_length=2
    )
    live_trading: Literal[False] = False
    order_writes: Literal[False] = False
    automation: Literal[False] = False
    auto_reconcile_on_start: Literal[False] = False
    require_protection: Literal[True] = True
    require_ip_bound_key: Literal[True] = True
    forbid_withdraw: Literal[True] = True
    require_flat_start: Literal[True] = True
    auto_disarm: Literal[True] = True
    configured_submissions_per_arm: Literal[1] = 1
    configured_max_open_positions: Literal[1] = 1
    configured_max_leverage: int = Field(ge=1, le=3)
    configured_max_order_size_contracts: _PositiveDecimalText
    configured_max_notional_usdt: _PositiveDecimalText
    configured_session_loss_limit_pct: _PositiveDecimalText
    configured_arm_ttl_seconds: int = Field(ge=60, le=900)

    @model_validator(mode="after")
    def exact_configured_limits(self) -> LiveOffSettingsV1:
        if (
            tuple(sorted(set(self.instruments))) != self.instruments
            or not 0 < Decimal(self.configured_max_order_size_contracts) <= 10
            or not 0 < Decimal(self.configured_max_notional_usdt) <= 10_000
            or not 0
            < Decimal(self.configured_session_loss_limit_pct)
            <= Decimal("0.01")
        ):
            raise ValueError("Micro Live configured limit differs")
        return self


class MicroLiveReadonlyPreflightV1(QualificationModel):
    schema_version: Literal["ctcc.live.micro_readonly_preflight.v1"] = (
        "ctcc.live.micro_readonly_preflight.v1"
    )
    environment: Literal["live"] = "live"
    status: Literal["retained_read_only_blocked"] = "retained_read_only_blocked"
    settings: LiveOffSettingsV1
    current_custody_sha256: _Pin
    current_capture_completed: ClockStamp
    history: tuple[LiveHistoryReadbackV1, ...] = Field(min_length=6, max_length=6)
    same_retained_lease: Literal[True] = True
    exact_configured_uid_and_main_uid_match: Literal[True] = True
    account_mode: Literal["2", "3"]
    position_mode: Literal["net_mode", "long_short_mode"]
    reviewed_instrument_settlement_currency: Literal["USDT"] = "USDT"
    read_and_trade_permissions_observed: Literal[True] = True
    withdrawal_permission_absent: Literal[True] = True
    nonempty_ip_binding_observed: Literal[True] = True
    dedicated_credential_proven: Literal[False] = False
    exchange_flat_observed: bool
    exchange_position_count: Literal[0] | None
    exchange_pending_order_count: Literal[0] | None
    exchange_pending_algo_count: Literal[0] | None
    available_usdt_equity: None = None
    tracked_exposure_count: None = None
    local_uncertain_count: None = None
    active_risk_reservation_count: None = None
    unresolved_intent_count: None = None
    durable_safety_latch_clear: None = None
    current_process_arm_state: None = None
    freshness_verified: Literal[False] = False
    account_complete: Literal[False] = False
    history_complete: Literal[False] = False
    account_reconciliation_complete: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    micro_live_caps_verified: Literal[False] = False
    live_entry_authority_available: Literal[False] = False
    live_maintenance_authority_available: Literal[False] = False
    micro_live_ready_awaiting_operator_arm: Literal[False] = False
    execution_authority: Literal[False] = False
    account_requests: Literal[0] = 0
    order_writes: Literal[0] = 0

    @property
    def blockers(self) -> tuple[str, ...]:
        return (
            "live_current_freshness_unverified",
            "live_dedicated_credential_unverified",
            "live_account_history_and_reconciliation_incomplete",
            "live_local_exposure_reservation_intent_and_latch_unverified",
            "micro_live_caps_unverified",
            "live_entry_authority_unavailable",
            "live_maintenance_authority_unavailable",
        )

    @model_validator(mode="after")
    def exact_readback_scope(self) -> MicroLiveReadonlyPreflightV1:
        count = 0 if self.exchange_flat_observed else None
        if tuple(item.family for item in self.history) != _FAMILIES or (
            self.exchange_position_count,
            self.exchange_pending_order_count,
            self.exchange_pending_algo_count,
        ) != (count, count, count):
            raise ValueError("Micro Live retained scope differs")
        return self


def _off_settings(settings: Settings) -> LiveOffSettingsV1:
    if (
        type(settings) is not Settings
        or settings.environment != "production"
        or settings.okx_live_enabled is not True
        or settings.okx_live_credentials_configured is not True
        or any(
            value is not False
            for value in (
                settings.live_trading,
                settings.okx_live_allow_order_writes,
                settings.okx_live_auto_execution,
                settings.okx_live_auto_reconcile_on_start,
                settings.auto_trade,
                settings.paper_auto_execution,
                settings.okx_demo_allow_order_writes,
                settings.okx_demo_auto_execution,
            )
        )
        or any(
            value is not True
            for value in (
                settings.okx_live_require_protection,
                settings.okx_live_require_ip_bound_key,
                settings.okx_live_forbid_withdraw_permission,
                settings.okx_live_require_flat_start,
                settings.okx_live_auto_disarm,
            )
        )
        or type(settings.okx_live_max_submissions_per_arm) is not int
        or settings.okx_live_max_submissions_per_arm != 1
        or type(settings.okx_live_max_open_positions) is not int
        or settings.okx_live_max_open_positions != 1
    ):
        raise MicroLiveReadbackError("micro_live_get_only_settings_required")
    instruments = current._instruments(sorted(settings.okx_live_allowed_symbol_list))
    if not set(instruments).issubset(LIVE_BOUNDARY_INSTRUMENT_IDS):
        raise MicroLiveReadbackError("micro_live_instrument_scope_changed")
    return LiveOffSettingsV1(
        origin=_validated_origin(settings.okx_live_rest_base_url, _LIVE_HOSTS),
        instruments=instruments,
        configured_max_leverage=settings.okx_live_max_leverage,
        configured_max_order_size_contracts=format(
            settings.okx_live_max_order_size_contracts, "f"
        ),
        configured_max_notional_usdt=format(settings.okx_live_max_notional_usdt, "f"),
        configured_session_loss_limit_pct=format(
            settings.okx_live_session_loss_limit_pct, "f"
        ),
        configured_arm_ttl_seconds=settings.okx_live_arm_ttl_seconds,
    )


def _both_configs(root, attempt, receipt, lease_id, origin, uid, main_uid):
    configs = []
    first_observation = None
    for child, key in (
        (current._CONFIG_BEFORE, "config_before_custody_sha256"),
        (current._CONFIG_AFTER, "config_after_custody_sha256"),
    ):
        _, observation = _read_custodied_live_account_config(
            root / attempt, attempt_id=child, expected_custody_sha256=receipt[key]
        )
        config = current._check_config(observation.raw_body)
        if (
            observation.synthetic_mock is not False
            or observation.lease_binding_id != lease_id
            or observation.origin != origin
            or config.uid != uid
            or config.main_uid != main_uid
        ):
            raise MicroLiveReadbackError("micro_live_account_or_lease_changed")
        if first_observation is None:
            first_observation = observation.stamps[0]
        configs.append(config)
    if configs[0] != configs[1]:
        raise MicroLiveReadbackError("micro_live_config_changed")
    return configs[0], first_observation


def _history_selectors(pins):
    if (
        type(pins) is not tuple
        or len(pins) != len(_FAMILIES)
        or any(type(pin) is not LiveHistoryCustodyPinV1 for pin in pins)
    ):
        raise MicroLiveReadbackError("micro_live_history_inventory_changed")
    selectors = []
    for family, pin in zip(_FAMILIES, pins, strict=True):
        if type(pin.selector) is not LiveAccountHistorySelectorV1:
            raise MicroLiveReadbackError("micro_live_history_selector_changed")
        selector = select_live_account_history_page_v1(
            family,
            begin_ms=pin.selector.begin_ms,
            end_ms=pin.selector.end_ms,
            inst_type=None if family.startswith("bills_") else "SWAP",
        )
        if selector != pin.selector:
            raise MicroLiveReadbackError("micro_live_history_selector_changed")
        selectors.append(selector)
    return tuple(selectors)


def build_micro_live_readonly_preflight_v1(
    *,
    settings: Settings,
    private_root: Path,
    current_attempt_id: str,
    expected_current_custody_sha256: str,
    history_pins: tuple[LiveHistoryCustodyPinV1, ...],
    expected_retained_lease_binding_id: str,
) -> MicroLiveReadonlyPreflightV1:
    """Replay independently pinned local custody. Do not contact the exchange."""
    try:
        off = _off_settings(settings)
        if (
            type(private_root) is not type(Path())
            or not private_root.is_absolute()
            or type(expected_retained_lease_binding_id) is not str
            or _DIGEST.fullmatch(expected_retained_lease_binding_id) is None
        ):
            raise MicroLiveReadbackError("micro_live_private_scope_invalid")
        uid = settings.okx_live_expected_uid.get_secret_value()
        main_uid = settings.okx_live_expected_main_uid.get_secret_value()
        if _UID.fullmatch(uid) is None or _UID.fullmatch(main_uid) is None:
            raise MicroLiveReadbackError("micro_live_account_identity_unpinned")
        selectors = _history_selectors(history_pins)
        ids = (current_attempt_id, *(pin.attempt_id for pin in history_pins))
        digests = (
            expected_current_custody_sha256,
            *(pin.custody_sha256 for pin in history_pins),
        )
        for attempt in ids:
            _check_name(attempt)
        if (
            len(set(ids)) != len(ids)
            or any(
                type(pin) is not str or _DIGEST.fullmatch(pin) is None
                for pin in digests
            )
            or len(set(digests)) != len(digests)
        ):
            raise MicroLiveReadbackError("micro_live_custody_inventory_changed")
        lease = expected_retained_lease_binding_id
        receipt = current.read_live_current_account_v1(
            private_root,
            attempt_id=current_attempt_id,
            expected_custody_sha256=expected_current_custody_sha256,
        )
        if receipt["synthetic_mock"] is not False or receipt["plan_sha256"] != sha(
            current._plan(
                current_attempt_id,
                off.instruments,
                lease_binding_id=lease,
                origin=off.origin,
            )
        ):
            raise MicroLiveReadbackError("micro_live_current_plan_changed")
        account, current_started = _both_configs(
            private_root, current_attempt_id, receipt, lease, off.origin, uid, main_uid
        )
        current_completed = ClockStamp.model_validate(receipt["capture_completed"])
        spans = [(current_started, current_completed)]
        checked_history = []
        for pin, selector in zip(history_pins, selectors, strict=True):
            observed = history.read_live_account_history_v1(
                private_root,
                attempt_id=pin.attempt_id,
                expected_custody_sha256=pin.custody_sha256,
            )
            expected_plan = sha(
                history._plan(
                    pin.attempt_id, selector, lease_binding_id=lease, origin=off.origin
                )
            )
            if (
                observed["synthetic_mock"] is not False
                or observed["explicit_terminal_page_observed"] is not True
                or observed["plan_sha256"] != expected_plan
            ):
                raise MicroLiveReadbackError("micro_live_history_plan_changed")
            identity, started = _both_configs(
                private_root, pin.attempt_id, observed, lease, off.origin, uid, main_uid
            )
            if identity != account:
                raise MicroLiveReadbackError("micro_live_config_changed")
            completed = ClockStamp.model_validate(observed["capture_completed"])
            spans.append((started, completed))
            checked_history.append(
                LiveHistoryReadbackV1(
                    family=selector.kind,
                    custody_sha256=pin.custody_sha256,
                    selector_plan_sha256=expected_plan,
                    page_count=observed["page_count"],
                    row_count=observed["row_count"],
                    capture_completed=completed,
                )
            )
        spans.sort(key=lambda span: span[0].monotonic_ns)
        if any(start.monotonic_ns >= end.monotonic_ns for start, end in spans):
            raise MicroLiveReadbackError("micro_live_clock_scope_changed")
        validate_stamps(tuple(stamp.model_dump() for span in spans for stamp in span))
        if off != _off_settings(settings) or (
            uid != settings.okx_live_expected_uid.get_secret_value()
            or main_uid != settings.okx_live_expected_main_uid.get_secret_value()
        ):
            raise MicroLiveReadbackError("micro_live_settings_changed")
        flat = receipt["no_open_positions_or_pending_orders_observed"]
        if type(flat) is not bool:
            raise MicroLiveReadbackError("micro_live_exchange_flat_unknown")
        count = 0 if flat else None
        return MicroLiveReadonlyPreflightV1(
            settings=off,
            current_custody_sha256=expected_current_custody_sha256,
            current_capture_completed=current_completed,
            history=tuple(checked_history),
            account_mode=account.account_mode,
            position_mode=account.position_mode,
            exchange_flat_observed=flat,
            exchange_position_count=count,
            exchange_pending_order_count=count,
            exchange_pending_algo_count=count,
        )
    except MicroLiveReadbackError:
        raise
    except (ArithmeticError, AttributeError, KeyError, OSError, TypeError, ValueError):
        raise MicroLiveReadbackError("micro_live_retained_readback_refused") from None


def micro_live_readonly_preflight_bytes_v1(
    record: MicroLiveReadonlyPreflightV1,
) -> bytes:
    if type(record) is not MicroLiveReadonlyPreflightV1:
        raise MicroLiveReadbackError("micro_live_artifact_invalid")
    checked = MicroLiveReadonlyPreflightV1.model_validate(record)
    payload = canonical(checked.model_dump(mode="json"))
    if len(payload) > _MAX_BYTES:
        raise MicroLiveReadbackError("micro_live_artifact_invalid")
    return payload
