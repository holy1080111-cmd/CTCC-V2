"""Genuine source imports and default-OFF ASGI wiring, no DB/provider/Arm.

Only the HTTP access-token seam is synthetic. The runtime, coordinator and
missing-configuration behavior use their actual canonical source modules.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.api import native_live_manual_v1 as subject
from app.api import security


@pytest.mark.asyncio
async def test_genuine_core_imports_and_unconfigured_http_never_create_source_or_arm(
    monkeypatch,
):
    from app.okx_live import native_runtime_v1 as runtime
    from app.okx_live import owned_qualification_v1 as qualification

    monkeypatch.delenv(runtime.ENVIRONMENT_NAME, raising=False)
    root = Path(__file__).resolve().parents[2]
    assert Path(runtime.__file__).resolve().is_relative_to(root)
    assert Path(qualification.__file__).resolve().is_relative_to(root)
    qualification._NATIVE_GUARD()
    # An incomplete lifecycle install is an explicit refusal. Missing private
    # configuration is still correctly recognized before that dependency is
    # needed. After integration the same test verifies the real module tuple.
    if importlib.util.find_spec("app.okx_live.owned_lifecycle_v1") is None:
        with pytest.raises(ImportError, match="owned_lifecycle_v1"):
            subject._modules()
    else:
        actual_runtime, actual_qualification, lifecycle = subject._modules()
        assert actual_runtime is runtime and actual_qualification is qualification
        assert Path(lifecycle.__file__).resolve().is_relative_to(root)
        assert callable(lifecycle.preflight_owned_live_lifecycle_runtime_v1)
    token = "synthetic-genuine-import-http-token-000000000000000"
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_token_is_safe=True, api_token_value=token),
    )
    worker = subject.LiveToolWorker()
    app = subject._build_app(worker)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        status = await client.get(
            "/api/live-tool/status", headers={"X-CTCC-Token": token}
        )
        assert status.status_code == 200
        assert status.json()["setup_status"] == "NOT_CONFIGURED"
        assert status.json()["startup_inhibit_confirmed"] is False
        assert status.json()["execution_authority"] is False
        denied = await client.post(
            "/api/live-tool/prepare",
            headers={"X-CTCC-Token": token},
            json={
                "instrument_id": "BTC-USDT-SWAP",
                "strategy": "trend_pullback",
                "direction": "long",
                "controlled_exit_requested": False,
            },
        )
        assert denied.status_code == 503 and worker.task is None
    assert not runtime._OWNED and not qualification._OWNED
    assert not qualification._QUALIFIED and not qualification.source._SOURCES
