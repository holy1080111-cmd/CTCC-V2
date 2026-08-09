from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import inspect

import pytest

from app.database.repositories.okx_live import (
    OkxLiveAccountIdentityError,
    OkxLiveRepository,
    fingerprint_account_identifier,
)
from app.domain.okx_live import (
    OkxLiveAccountConfig,
    OkxLiveAlgoOrderView,
    OkxLiveApiKeyCapability,
    OkxLiveOrderView,
    OkxLivePositionView,
)


NOW = datetime(2026, 8, 9, 1, 2, 3, tzinfo=timezone.utc)


def account_config(
    *,
    uid: str | None = "live-user-123",
    main_uid: str | None = "live-main-456",
) -> OkxLiveAccountConfig:
    return OkxLiveAccountConfig(
        uid=uid,
        main_uid=main_uid,
        is_sub_account=True,
        account_level="2",
        position_mode="net_mode",
        account_stp_mode="cancel_maker",
        account_type="1",
        capability=OkxLiveApiKeyCapability(
            permissions=["trade", "read_only", "trade"],
            unknown_permissions=["future_permission", "future_permission"],
            read_permission=True,
            trade_permission=True,
            withdraw_permission=False,
            ip_bound=True,
        ),
    )


def test_account_fingerprint_is_normalized_domain_separated_and_non_reversible() -> None:
    fingerprint = fingerprint_account_identifier("  live-user-123  ")

    assert fingerprint == fingerprint_account_identifier("live-user-123")
    assert fingerprint != fingerprint_account_identifier("live-user-124")
    assert fingerprint is not None
    assert len(fingerprint) == 64
    assert "live-user-123" not in fingerprint


def test_account_values_require_both_identifiers_and_exclude_raw_identity() -> None:
    values = OkxLiveRepository._account_values(account_config(), NOW)

    assert values["uid_fingerprint"] == fingerprint_account_identifier("live-user-123")
    assert values["main_uid_fingerprint"] == fingerprint_account_identifier("live-main-456")
    assert values["permissions"] == ["read_only", "trade"]
    assert values["unknown_permissions"] == ["future_permission"]
    assert {
        "uid",
        "main_uid",
        "ip",
        "api_key",
        "api_secret",
        "passphrase",
        "raw",
    }.isdisjoint(values)

    with pytest.raises(
        OkxLiveAccountIdentityError,
        match="^okx_live_account_identity_incomplete$",
    ):
        OkxLiveRepository._account_values(account_config(main_uid=None), NOW)


def test_checkpoint_details_exclude_identity_balance_and_credentials() -> None:
    details = OkxLiveRepository._checkpoint_details(account_config())

    assert details == {
        "account_level": "2",
        "position_mode": "net_mode",
        "is_sub_account": True,
        "read_permission": True,
        "trade_permission": True,
        "withdraw_permission": False,
        "ip_bound": True,
        "unknown_permission_count": 1,
    }
    serialized = repr(details).lower()
    for forbidden in (
        "live-user-123",
        "live-main-456",
        "balance",
        "equity",
        "api_key",
        "secret",
        "passphrase",
    ):
        assert forbidden not in serialized


def test_failure_checkpoint_accepts_only_explicit_safe_codes() -> None:
    assert (
        OkxLiveRepository._safe_failure_code(" OKX_LIVE_PRIVATE_API_UNAVAILABLE ")
        == "okx_live_private_api_unavailable"
    )
    assert (
        OkxLiveRepository._safe_failure_code("network failed api-key=live-secret")
        == "okx_live_reconcile_failed"
    )
    assert OkxLiveRepository._safe_failure_code("") == "okx_live_reconcile_failed"


def test_row_mappings_keep_exchange_primary_identifiers() -> None:
    position = OkxLivePositionView(
        position_id="position-123",
        instrument_id="BTC-USDT-SWAP",
        position_side="net",
        size=Decimal("1"),
        available_size=Decimal("1"),
        unrealized_pnl=Decimal("0.5"),
    )
    order = OkxLiveOrderView(
        order_id="order-123",
        client_order_id="reusable-client-id",
        instrument_id="BTC-USDT-SWAP",
        side="buy",
        order_type="market",
        state="filled",
        size=Decimal("1"),
        accumulated_fill_size=Decimal("1"),
    )
    algo = OkxLiveAlgoOrderView(
        algo_order_id="algo-123",
        client_algo_order_id="reusable-algo-id",
        instrument_id="BTC-USDT-SWAP",
        order_type="conditional",
        state="live",
        size=Decimal("1"),
    )

    position_row = OkxLiveRepository._position_row(position, NOW)
    order_values = OkxLiveRepository._order_values(order, NOW)
    algo_row = OkxLiveRepository._algo_row(algo, NOW)

    assert position_row.position_id == "position-123"
    assert not hasattr(position_row, "position_key")
    assert order_values["order_id"] == "order-123"
    assert order_values["client_order_id"] == "reusable-client-id"
    assert algo_row.algo_order_id == "algo-123"
    assert algo_row.client_algo_order_id == "reusable-algo-id"


def test_repository_public_api_has_no_exchange_write_operations() -> None:
    public_async_methods = {
        name
        for name, member in inspect.getmembers(
            OkxLiveRepository,
            predicate=inspect.iscoroutinefunction,
        )
        if not name.startswith("_")
    }

    assert public_async_methods == {"mark_failure", "mirror_status", "sync_snapshot"}
