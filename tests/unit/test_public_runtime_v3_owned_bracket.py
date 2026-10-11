"""Synthetic native-owner V3 path; no account-region or trading authority."""

import asyncio
import os
from datetime import timedelta
from threading import get_ident

import pytest

from app.domain.source_primitives import PublicReceiptError, decode, sha
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import qualification_runtime as initial
from tests.unit.research.test_owned_public_runtime_p1 import (
    resign_journal,
    synthetic_runtime,
)
from tests.unit.test_public_time_bracket_v3_contract import _plan, _policy
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import ms


@pytest.fixture(scope="module")
def source():
    return v2_engine_source("long")


def _owned_scope(clock):
    """Only the test constructs the private issuer state; production cannot."""
    invocation = object()
    plan = _plan()
    started = clock.stamp()
    expires = clock.last + timedelta(seconds=30)
    plan["invocation_started"] = started
    plan["expires_at"] = expires.isoformat()
    claim_plan = {
        key: value
        for key, value in plan.items()
        if key not in {"schema_version", "exchange_time_policy_sha256"}
    }
    scope = initial._InitialScopeV2(initial._ISSUER)
    initial._INITIAL_SCOPES[scope] = {
        "invocation": invocation,
        "parent": asyncio.current_task(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "plan": claim_plan,
        "report_id": plan["report_id"],
        "instrument_id": plan["instrument_id"],
        "invocation_started": started,
        "publication_completed_at": None,
        "expires_at": expires,
    }
    return scope, invocation


@pytest.mark.asyncio
async def test_v3_owner_still_refuses_caller_region_before_clock_journal_or_io(
    source, tmp_path, monkeypatch
):
    clock, directory, harness, _ = synthetic_runtime(
        monkeypatch,
        (source, None, None),
        rest_hostname="openapi.okx.com",
        ws_hostname="wspap.okx.com",
    )
    scope, _ = _owned_scope(clock)

    def forbidden(*_args, **_kwargs):
        pytest.fail("v3_unverified_region_reached_clock_journal_or_io")

    monkeypatch.setattr(runtime, "_runtime_attempt", forbidden)
    with pytest.raises(
        runtime.PublicSourceRuntimeError,
        match="trusted_demo_public_origin_profile_unavailable",
    ):
        await runtime._capture_initial_public_v3(scope, _policy(), tmp_path)
    assert not directory.content and not harness.requests
    assert not runtime._SOURCES and not runtime._INITIAL_RESULTS


@pytest.mark.asyncio
async def test_v3_owner_brackets_full_market_then_seals_and_replays(
    source, tmp_path, monkeypatch
):
    clock, directory, harness, _ = synthetic_runtime(
        monkeypatch,
        (source, None, None),
        rest_hostname="openapi.okx.com",
        ws_hostname="wspap.okx.com",
    )
    # This test supplies only synthetic transport. The production region fence
    # remains unchanged and never accepts the plan's claimed account region.
    monkeypatch.setattr(
        runtime, "_require_trusted_v2_demo_origin_profile", lambda _: None
    )
    original_public = harness.public

    def public(request):
        if request.url.path.endswith("/time"):
            return [{"ts": ms(clock.last)}]
        rows = original_public(request)
        if request.url.path.endswith("/funding-rate"):
            rows[0].update(
                ts=ms(clock.last - timedelta(seconds=2)),
                fundingTime=ms(clock.last + timedelta(hours=1)),
                nextFundingTime=ms(clock.last + timedelta(hours=9)),
                method="current_period",
                formulaType="withRate",
                minFundingRate="-0.00375",
                maxFundingRate="0.00375",
                settFundingRate="-0.0002",
                settState="settled",
            )
        return rows

    harness.public = public
    scope, invocation = _owned_scope(clock)
    carrier = await runtime._capture_initial_public_v3(scope, _policy(), tmp_path)
    assert type(carrier) is runtime._CapturedInitialPublicV3
    with pytest.raises(
        runtime.PublicSourceRuntimeError, match="owned_public_capture_required"
    ):
        runtime._consume_initial_public_capture_v2(carrier, invocation)
    packet, journal_sha256, _ = runtime._consume_initial_public_capture_v3(
        carrier, invocation
    )
    assert packet.admission == "DENY" and packet.execution_authority is False
    assert journal_sha256 == sha(directory.content["summary.json"])
    assert decode(directory.content["plan.json"])["schema_version"] == (
        "ctcc.public.initial_runtime_plan.v3"
    )
    summary, events, replayed = runtime.replay_public_runtime(
        directory, expected_plan_sha256=sha(directory.content["plan.json"])
    )
    assert summary["disposition"] == "captured"
    assert replayed.packet_json == packet.packet_json
    roles = [
        event["metadata"]["role"] for event in events if event["kind"] == "request"
    ]
    assert roles[0] == "time_pre" and roles[-1] == "time_post"
    time_requests = [
        event["metadata"]
        for event in events
        if event["kind"] == "request" and event["metadata"]["role"].startswith("time_")
    ]
    assert [request["query"] for request in time_requests] == [[], []]
    assert set(roles[1:-1]) == {"candles", "quote", "market_aux"}
    assert len([role for role in roles if role.startswith("time_")]) == 2
    assert all(request.method == "GET" for request in harness.requests)
    assert all(
        request.headers["x-simulated-trading"] == "1" for request in harness.requests
    )
    assert not any(
        request.url.path.startswith(("/api/v5/account/", "/api/v5/trade/"))
        for request in harness.requests
    )
    assert all(client.is_closed for client in harness.clients)
    assert not runtime._SOURCES and not runtime._INITIAL_RESULTS

    # Re-signing an altered journal cannot turn a nonempty time query into a
    # valid no-query OKX system-time observation.
    def alter(event, _directory):
        if event["kind"] == "request" and event["metadata"]["role"] == "time_pre":
            event["metadata"]["query"] = [["instId", "BTC-USDT-SWAP"]]

    resign_journal(directory, alter)
    with pytest.raises(PublicReceiptError, match="runtime_request_invalid"):
        runtime.replay_public_runtime(
            directory, expected_plan_sha256=sha(directory.content["plan.json"])
        )
