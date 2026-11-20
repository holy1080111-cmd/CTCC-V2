"""Dashboard contracts only; no process startup, database or exchange requests."""

import base64
import hashlib
import json
import re
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import get_args

import httpx
import pytest
from fastapi import FastAPI

from app.dashboard import native_manual_demo_0056_v1 as page
from app.demo_automation.native_requests_v2 import (
    NativeDemoArmExecuteRequestV2,
    NativeDemoClearStopRequestV2,
    NativeDemoConfigureLeverageRequestV1,
    NativeDemoCounterInitializationRequestV2,
    NativeDemoStrategyV2,
)


def settings(**changes):
    return SimpleNamespace(
        **(
            {
                "okx_demo_allowed_symbol_list": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
                "okx_demo_max_leverage": 20,
                "okx_demo_max_order_size_contracts": Decimal(1),
                "okx_demo_allow_order_writes": True,
                "okx_demo_auto_execution": True,
                "okx_ws_enabled": True,
                "api_token_value": "never-render-private-runtime-token-00000",
                "okx_demo_api_secret": "never-render-exchange-secret",
            }
            | changes
        )
    )


def rendered(config=None):
    response = page.render_manual_demo_dashboard_0056_v1(config or settings())
    body = response.body.decode("utf-8")
    script = re.search(r"<script>(.*?)</script>", body, re.DOTALL)[1]
    style = re.search(r"<style>(.*?)</style>", body, re.DOTALL)[1]
    data = json.loads(re.search(r"const config = (\{[^\n]+\});", script)[1])
    return response, body, script, style, data


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}
        self.ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "input":
            self.inputs[values["id"]] = values


def test_html_is_local_unknown_and_contains_no_private_setting_values():
    response, body, script, _, config = rendered()
    assert response.status_code == 200
    assert 'lang="zh-Hant"' in body
    assert "<h1>Demo 手動控制</h1>" in body
    assert body.count(">未知</strong>") == 4
    assert "never-render" not in body
    assert "https://" not in body
    assert not re.search(r"\b(src|href|onclick|onsubmit)=", body)
    assert not re.search(
        r"localStorage|sessionStorage|indexedDB|document\.cookie|sendBeacon|setInterval|innerHTML",
        script,
    )
    assert config["symbols"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert config["strategies"] == list(get_args(NativeDemoStrategyV2))
    assert config["leverage"] == [3, 5, 8, 10, 20]
    parsed = Controls()
    parsed.feed(body)
    assert len(parsed.ids) == len(set(parsed.ids))
    assert parsed.inputs["token"]["type"] == "password"
    assert all("value" not in attrs for attrs in parsed.inputs.values())
    assert all(attrs.get("autocomplete") == "off" for attrs in parsed.inputs.values())


def test_csp_hashes_exact_inline_source_and_forbids_external_resources():
    response, _, script, style, _ = rendered()

    def digest(text):
        return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()

    csp = response.headers["content-security-policy"]
    assert f"script-src 'sha256-{digest(script)}'" in csp
    assert f"style-src 'sha256-{digest(style)}'" in csp
    for directive in (
        "default-src 'none'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ):
        assert directive in csp
    assert "unsafe-inline" not in csp
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_browser_payload_constants_validate_with_original_request_models():
    _, _, _, _, config = rendered()
    order = {
        "instrument_id": config["symbols"][0],
        "strategy": config["strategies"][0],
        "contracts": "1",
    }
    arm = NativeDemoArmExecuteRequestV2(
        **order,
        leverage=3,
        arm_confirmation=config["phrases"]["arm"],
        execute_confirmation=config["phrases"]["execute"],
    )
    assert arm.model_dump()["contracts"] == "1"
    assert NativeDemoClearStopRequestV2(confirmation=config["phrases"]["clear"])
    assert NativeDemoCounterInitializationRequestV2(
        confirmation=config["phrases"]["counter"]
    )
    for ceiling in config["leverage"]:
        assert (
            NativeDemoConfigureLeverageRequestV1(
                **order,
                leverage_ceiling=ceiling,
                confirmation=config["phrases"]["leverage"],
            ).leverage_ceiling
            == ceiling
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"okx_demo_allowed_symbol_list": []},
        {"okx_demo_allowed_symbol_list": ["BTC-USDT-SWAP"] * 2},
        {"okx_demo_allowed_symbol_list": ["<script>alert(1)</script>"]},
        {"okx_demo_allowed_symbol_list": ["UNREVIEWED-USDT-SWAP"]},
        {"okx_demo_max_leverage": True},
        {"okx_demo_max_leverage": 0},
        {"okx_demo_max_order_size_contracts": Decimal("NaN")},
        {"okx_demo_max_order_size_contracts": Decimal(0)},
        {"okx_demo_max_order_size_contracts": Decimal("1e-21")},
        {"okx_demo_allow_order_writes": 1},
    ],
)
def test_invalid_page_configuration_refuses_without_fallback(changes):
    with pytest.raises(ValueError, match="configuration_refused"):
        rendered(settings(**changes))


def test_configured_caps_are_present_and_do_not_enable_higher_leverage():
    _, body, script, _, config = rendered(
        settings(
            okx_demo_max_leverage=5, okx_demo_max_order_size_contracts=Decimal("0.01")
        )
    )
    assert 'value="20" disabled' in body
    assert config["max_contracts"] == "0.01"
    assert config["max_leverage"] == 5
    assert "BigInt" in script
    _, body, _, _, config = rendered(settings(okx_ws_enabled=False))
    assert config["execution_enabled"] is False
    assert "未開啟完整交易開關" in body


def test_synthetic_cli_reads_only_explicit_arguments_and_refuses_clobber(
    monkeypatch, tmp_path
):
    from scripts import render_native_manual_demo_dashboard as renderer

    def forbidden():
        raise AssertionError("Runtime settings must not be loaded")

    target = tmp_path / "synthetic.html"
    monkeypatch.setattr(page, "get_settings", forbidden)
    monkeypatch.setenv("API_TOKEN", "never-read-environment-token-000000")
    monkeypatch.setattr(
        "sys.argv",
        [
            "render",
            "--symbol",
            "BTC-USDT-SWAP",
            "--max-leverage",
            "20",
            "--max-contracts",
            "1",
            "--execution-enabled",
            "--output",
            str(target),
        ],
    )
    renderer.main()
    original = target.read_bytes()
    assert b"never-read" not in original
    metadata = json.loads(target.with_name(target.name + ".headers.json").read_text())
    assert metadata["runtime_settings_loaded"] is False
    assert metadata["synthetic_configuration"] is True
    assert metadata["html_sha256"] == hashlib.sha256(original).hexdigest()
    with pytest.raises(SystemExit):
        renderer.main()
    assert target.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_asgi_dashboard_is_guarded_by_actual_startup_flag(monkeypatch, ready):
    from app.api import native_manual_demo_http_0056_v1 as http

    monkeypatch.setattr(page, "get_settings", settings)
    instance = FastAPI()
    instance.include_router(http.router)
    instance.state.native_manual_demo_recovery_confirmed = ready
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=instance), base_url="http://test"
    ) as client:
        for path in ("/", "/dashboard"):
            response = await client.get(path)
            if ready:
                assert response.status_code == 200
                assert "Demo 手動控制" in response.text
                assert response.headers["cache-control"] == "no-store"
            else:
                assert response.status_code == 503
                assert (
                    response.json()["detail"]
                    == "native_manual_demo_recovery_unconfirmed"
                )
