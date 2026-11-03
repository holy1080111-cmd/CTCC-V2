"""Offline only: no Control owner, database lease or OKX I/O."""

import httpx
import pytest

from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation.native_requests_v2 import (
    CONFIGURE_LEVERAGE_CONFIRMATION_V1,
    NativeDemoConfigureLeverageRequestV1,
    NativeDemoConfigureLeverageResultV1,
)


def request_fields():
    return {
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "contracts": "1",
        "leverage_ceiling": 5,
        "confirmation": CONFIGURE_LEVERAGE_CONFIRMATION_V1,
    }


def test_real_configuration_source_bootstrap_root_identity_and_explicit_route():
    from app.api.routers import demo_automation
    from app.exchange.okx import native_demo_source_identity_v2 as identity
    from app.trade_qualification import native_controlled_account_source_v6 as source
    from app.trade_qualification import (
        native_leverage_configuration_v1 as configuration,
    )

    assert source._source_bootstrap_marker_v6() is None
    configuration._NATIVE_GUARD()
    assert len(identity.read_native_demo_source_contract_v2()) == 5
    matches = [
        route
        for route in demo_automation.router.routes
        if route.path == "/api/demo-automation/native/configure-leverage"
    ]
    assert len(matches) == 1 and matches[0].methods == {"POST"}
    assert matches[0].response_model is NativeDemoConfigureLeverageResultV1


def test_configuration_request_and_result_have_no_order_or_arm_fields():
    request = NativeDemoConfigureLeverageRequestV1(**request_fields())
    selected = caller._freeze_request_v2(request)
    assert selected.action == "configure_leverage" and selected.leverage == 5
    assert set(request.model_dump()) == set(request_fields())
    data = NativeDemoConfigureLeverageResultV1(
        invocation_id="synthetic",
        classification="no_change",
        configuration_outcome_id="1" * 64,
        receipt_sha256="2" * 64,
    )
    assert data.execution_authority is False and data.order_retry_authority is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("leverage_ceiling", True),
        ("leverage_ceiling", 4),
        ("contracts", "0"),
        ("confirmation", ""),
        ("arm_confirmation", "untrusted"),
        ("uid", "untrusted"),
    ],
)
def test_configuration_caller_rejects_constructed_or_injected_fields(field, value):
    request = NativeDemoConfigureLeverageRequestV1.model_construct(**request_fields())
    request.__dict__[field] = value
    with pytest.raises(DemoAutomationSafetyError):
        caller._freeze_request_v2(request)


@pytest.mark.asyncio
async def test_forged_configuration_gate_and_legacy_leverage_reach_zero_http():
    from app.exchange.okx import native_demo_write_v2 as root
    from app.exchange.okx.errors import OkxPrivateApiError
    from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
    from tests.unit.test_demo_automation import configured_settings

    def reject_io(_request):
        pytest.fail("unowned configuration reached HTTP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reject_io), trust_env=False
    ) as http:
        client = OkxDemoPrivateRestClient(http, settings=configured_settings())
        payload = {"instId": "BTC-USDT-SWAP", "lever": "5", "mgnMode": "isolated"}
        with pytest.raises(root.NativeDemoWriteError):
            await client._submit_native_demo_leverage_configuration_v1(
                payload, native_write_gate=object()
            )
        with pytest.raises(OkxPrivateApiError):
            await client.set_leverage(payload)
