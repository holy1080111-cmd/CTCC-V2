"""Legacy service readiness is not native write authority.

All preflight values come from existing synthetic legacy test fixtures. They
never become a native source owner, financial claim or qualification receipt.
The actual REST client and every production guard remain unchanged. Its
MockTransport records any unexpected HTTP operation without exchange IO.
"""

import asyncio

import httpx
import pytest

from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation.service import SafeDemoAutomation
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from tests.unit.test_demo_automation import (
    FakeClient,
    FakeDemo,
    FakeHub,
    FakePublic,
    FakeStrategy,
    MemoryAutomationRepository,
    MemoryLocalCheckpointRepository,
    candidate,
    configured_settings,
)


class _LegacyStrategySelection:
    async def disabled_strategies(self):
        return set()


class _LegacyPreflightToActualTransport(FakeDemo):
    def __init__(self):
        super().__init__()
        self.transport = None
        self.loop_stop = None
        self.transport_denials = []

    async def set_leverage(self, request):
        # This proves the real automation service passed its legacy readiness,
        # account, candidate and sizing checks. No stubbed write acknowledgement
        # lets it advance toward order creation.
        self.leverage_calls.append(request)
        if self.loop_stop is not None:
            # Stop only this service's own loop, after reaching the write call.
            # Event.wait then returns immediately; no production sleep is patched.
            self.loop_stop.set()
        try:
            return await self.transport.set_leverage(
                {
                    "instId": request.instrument_id,
                    "lever": str(request.leverage),
                    "mgnMode": request.margin_mode,
                }
            )
        except OkxPrivateApiError as error:
            self.transport_denials.append(error.code)
            raise

    async def place_order(self, request, *, before_submit=None):
        # An unexpected advance is a test failure, never a synthetic fill.
        self.place_calls.append(request)
        pytest.fail("legacy automation advanced beyond denied leverage")


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", (False, True), ids=("run-once", "actual-loop"))
async def test_direct_ready_armed_automation_cannot_reach_private_http(scheduled):
    observed = []

    def exchange_handler(request):
        observed.append(request)
        pytest.fail("unqualified direct legacy automation reached private HTTP")

    legacy = _LegacyPreflightToActualTransport()
    service = SafeDemoAutomation(
        settings=configured_settings(
            environment="development",
            okx_demo_read_max_retries=5,
            okx_demo_scan_initial_delay_seconds=0,
        ),
        strategy_service=FakeStrategy(candidate()),
        demo_service=legacy,
        public_client=FakePublic(),
        market_hub=FakeHub(),
        market_client=FakeClient(),
        repository=MemoryAutomationRepository(),
        local_checkpoint_repository=MemoryLocalCheckpointRepository(),
        # All storage dependencies are explicit inert legacy fixtures. A
        # development environment must not select a real default DB repository.
        strategy_control_repository=_LegacyStrategySelection(),
    )
    # Explicit synthetic configuration, not an environment/test-mode bypass.
    assert service.settings.environment == "development"
    assert service.settings.okx_demo_allow_order_writes is True
    assert service.settings.okx_demo_auto_execution is True
    assert service.settings.okx_ws_enabled is True
    assert service.settings.live_trading is False
    assert service.settings.paper_auto_execution is False

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exchange_handler),
        base_url="https://www.okx.com",
    ) as http:
        legacy.transport = OkxDemoPrivateRestClient(http, settings=service.settings)
        try:
            await service.recover()
            await service.arm()
            ready = await service.status()
            assert ready.recovered is True
            assert ready.armed is True
            assert ready.emergency_stop is False

            async with asyncio.timeout(10):
                if scheduled:
                    legacy.loop_stop = service._stop
                    assert not service._stop.is_set()
                    # Exercise the actual production loop rather than a route
                    # that rejects before creating the loop or calling run_once.
                    await service._loop()
                    assert len(service._history) == 1
                    run = service._history[-1]
                    assert run.trigger == "scheduled"
                else:
                    run = await service.run_once(execute=True)
                    assert run.trigger == "manual"

            assert run.execute is True
            assert len(legacy.leverage_calls) == 1
            assert legacy.transport_denials == [
                "demo_qualification_authority_unavailable"
            ]
            assert legacy.place_calls == []
            assert observed == []
            assert all(item.outcome != "submitted" for item in run.results)
            stopped = await service.status()
            assert stopped.emergency_stop is True
            assert stopped.armed is False

            # Neither a direct retry nor configured read retries may bypass the
            # latched stop or turn an unowned write into an HTTP attempt.
            with pytest.raises(DemoAutomationSafetyError):
                await service.run_once(execute=True)
            assert len(legacy.leverage_calls) == 1
            assert legacy.transport_denials == [
                "demo_qualification_authority_unavailable"
            ]
            assert observed == []
        finally:
            service._stop.set()
            await service.stop()
