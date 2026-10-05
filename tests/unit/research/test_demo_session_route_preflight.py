"""Offline equality checks only; no OKX access or execution authority."""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest

from app.config.settings import Settings
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_capture as capture
from app.trade_qualification.account_collector import DemoAccountCredentials
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_session_route_preflight import (
    DemoSessionRoutePreflightError,
    preflight_demo_session_route,
)
from tests.unit.test_qualification_account_capture import plan as base_plan

KEY = "synthetic-only-route-api-key"
SECRET = "synthetic-only-route-api-secret"
PASSPHRASE = "synthetic-only-route-passphrase"
REGION_ORIGINS = {
    "global": "https://openapi.okx.com",
    "us_au": "https://us.okx.com",
    "eea": "https://eea.okx.com",
    "tr": "https://tr.okx.com",
}


def _session(*, region="global", binding="caller-picked-session"):
    selected = capture.RegionalDemoAccountCapturePlan(
        **{
            **capture._plain(base_plan()),
            "registration_region": region,
            "origin": REGION_ORIGINS[region],
            "registration_evidence_sha256": hashlib.sha256(
                b"caller-picked-unverified-registration"
            ).hexdigest(),
            "session_binding_id": binding,
        }
    )
    credentials = DemoAccountCredentials(KEY, SECRET, PASSPHRASE, binding)
    return ControlledDemoAccountSession(
        credentials=credentials,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )


def _client(*, origin="https://openapi.okx.com", **changes):
    values = {
        "environment": "test",
        "trading_mode": "okx_demo",
        "okx_demo_enabled": True,
        "okx_demo_rest_base_url": origin,
        "okx_demo_api_key": KEY,
        "okx_demo_api_secret": SECRET,
        "okx_demo_api_passphrase": PASSPHRASE,
    }
    values.update(changes)
    return OkxDemoPrivateRestClient(settings=Settings(_env_file=None, **values))


@pytest.mark.parametrize("region", ["global", "us_au", "eea"])
def test_local_match_remains_unverified_and_denied(region):
    result = preflight_demo_session_route(
        _session(region=region), _client(origin=REGION_ORIGINS[region])
    )
    assert result.credential_route_aligned is True
    assert result.source_authenticated is False
    assert result.registration_region_verified is False
    assert result.same_task_session_verified is False
    assert result.execution_authority is False
    assert result.admission == "DENY"
    assert "trusted" not in vars(type(result))
    assert all(value not in repr(result) for value in (KEY, SECRET, PASSPHRASE))


@pytest.mark.parametrize(
    "change",
    [
        {"okx_demo_api_key": "synthetic-only-different-key"},
        {"okx_demo_api_secret": "synthetic-only-different-secret"},
        {"okx_demo_api_passphrase": "synthetic-only-different-passphrase"},
    ],
)
def test_any_credential_mismatch_fails_without_echo(change):
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_session_credential_mismatch"
    ) as caught:
        preflight_demo_session_route(_session(), _client(**change))
    assert all(value not in str(caught.value) for value in (KEY, SECRET, PASSPHRASE))


def test_mismatched_private_rest_region_is_rejected():
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_session_route_mismatch"
    ):
        preflight_demo_session_route(_session(region="eea"), _client())


def test_unreviewed_demo_public_region_is_rejected_even_with_matching_rest():
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_public_region_unreviewed"
    ):
        preflight_demo_session_route(
            _session(region="tr"), _client(origin=REGION_ORIGINS["tr"])
        )


def test_external_test_transport_and_caller_objects_cannot_become_binding():
    session = _session()
    client = _client()
    client._external_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: None)
    )
    try:
        with pytest.raises(
            DemoSessionRoutePreflightError, match="demo_session_route_not_fresh_owned"
        ):
            preflight_demo_session_route(session, client)
    finally:
        # No request is sent; close the injected test client in the test.
        asyncio.run(client._external_client.aclose())
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_session_route_objects_required"
    ):
        preflight_demo_session_route({"region": "global", "trusted": True}, _client())
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_session_route_objects_required"
    ):
        preflight_demo_session_route(session, {"session": "caller-picked"})


def test_preflight_readback_cannot_survive_session_use_or_configuration_change():
    session = _session()
    client = _client()
    old = preflight_demo_session_route(session, client)
    assert old.admission == "DENY"
    session._used = True
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_session_route_not_fresh_owned"
    ):
        preflight_demo_session_route(session, client)
    assert old.source_authenticated is False
    assert old.execution_authority is False

    fresh = _session()
    client.settings = _client(origin=REGION_ORIGINS["eea"]).settings
    with pytest.raises(
        DemoSessionRoutePreflightError, match="demo_session_route_mismatch"
    ):
        preflight_demo_session_route(fresh, client)
