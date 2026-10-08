"""A native-origin route match remains a DENY-only offline diagnostic."""

import asyncio
from dataclasses import replace

import pytest

from app.trade_qualification import account_native_runtime as native_account
from app.trade_qualification import demo_public_route_binding_v2 as binding
from app.trade_qualification import public_source_runtime
from tests.unit.test_demo_account_origin_observation import mint, prepared


@pytest.mark.asyncio
async def test_native_one_use_origin_matches_reviewed_route_without_public_authority(
    monkeypatch,
):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    session._used = True
    diagnostic = native_account.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    monkeypatch.setattr(
        public_source_runtime.httpx.AsyncClient,
        "send",
        lambda *_args, **_kwargs: pytest.fail("public HTTP IO attempted"),
    )
    matched = binding.consume_native_demo_route_binding(diagnostic, session)
    assert matched.observed_private_origin == packet.plan.origin
    assert matched.observed_tls_hostname == "openapi.okx.com"
    assert matched.reviewed_route.registration_region == "global"
    assert matched.reviewed_route.rest_origin == packet.plan.origin
    assert matched.reviewed_route.ws_origin == "wss://wspap.okx.com:443/ws/v5/public"
    assert matched.account_uid == packet.plan.expected_uid
    assert matched.account_main_uid == packet.plan.expected_main_uid
    assert matched.session_binding_id == packet.plan.session_binding_id
    assert (
        matched.claimed_registration_evidence_sha256
        == packet.plan.registration_evidence_sha256
    )
    assert matched.account_packet_sha256 == reference.packet_sha256
    assert matched.native_proof_sha256 == arguments["proof_sha256"]
    assert matched.native_readback_sha256 == arguments["readback_sha256"]
    assert matched.observed_at < matched.expires_at
    assert matched.admission == "DENY"
    assert matched.registration_region_verified is False
    assert matched.public_source_authenticity_verified is False
    assert matched.execution_authority is False
    with pytest.raises(
        public_source_runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        public_source_runtime._require_trusted_v2_demo_origin_profile(
            {
                "environment": "demo",
                "registration_region": matched.reviewed_route.registration_region,
                "rest_origin": matched.reviewed_route.rest_origin,
                "ws_origin": matched.reviewed_route.ws_origin,
                "native_demo_route_binding": matched,
            }
        )
    with pytest.raises(binding.DemoPublicRouteBindingError):
        binding.consume_native_demo_route_binding(diagnostic, session)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("private_origin", "https://www.okx.com"),
        ("tls_hostname", "www.okx.com"),
        ("simulated_trading_header", "0"),
        ("registration_region_verified", True),
        ("uid", "other-uid"),
        ("main_uid", "other-main-uid"),
        ("session_binding_id", "other-session"),
        ("claimed_registration_evidence_sha256", "0" * 64),
    ],
)
async def test_source_mismatch_burns_lease_without_route_binding(
    monkeypatch, field, value
):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    native_account._ORIGIN_LEASES[lease]["origin"] = replace(
        native_account._ORIGIN_LEASES[lease]["origin"], **{field: value}
    )
    session._used = True
    diagnostic = native_account.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    with pytest.raises(binding.DemoPublicRouteBindingError):
        binding.consume_native_demo_route_binding(diagnostic, session)
    with pytest.raises(binding.DemoPublicRouteBindingError):
        binding.consume_native_demo_route_binding(diagnostic, session)


@pytest.mark.asyncio
async def test_caller_region_or_constructed_observation_cannot_replace_native_lease(
    monkeypatch,
):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    session._used = True
    diagnostic = native_account.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    with pytest.raises(TypeError):
        binding.consume_native_demo_route_binding(
            diagnostic, session, registration_region="global"
        )
    with pytest.raises(binding.DemoPublicRouteBindingError):
        binding.consume_native_demo_route_binding(
            native_account._ORIGIN_LEASES[lease]["origin"], session
        )
    # The failed direct-object attempt did not consume the real lease.
    assert binding.consume_native_demo_route_binding(diagnostic, session).admission == (
        "DENY"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_scope", ["session", "task"])
async def test_wrong_session_or_task_burns_lease(monkeypatch, wrong_scope):
    session, packet, reference, joins, arguments = await prepared(monkeypatch)
    lease = mint(session, packet, reference, joins, arguments)
    session._used = True
    diagnostic = native_account.InitialNativeAccountDiagnostic(
        arguments["receipt_json"], lease
    )
    if wrong_scope == "session":
        with pytest.raises(binding.DemoPublicRouteBindingError):
            binding.consume_native_demo_route_binding(diagnostic, object())
    else:

        async def foreign_task():
            with pytest.raises(binding.DemoPublicRouteBindingError):
                binding.consume_native_demo_route_binding(diagnostic, session)

        await asyncio.create_task(foreign_task())
    with pytest.raises(binding.DemoPublicRouteBindingError):
        binding.consume_native_demo_route_binding(diagnostic, session)
