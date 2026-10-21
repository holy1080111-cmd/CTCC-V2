"""Synthetic full V4 owner tests; no native acceptance or exchange order I/O."""

import asyncio
import copy
import pickle
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from weakref import ref

import pytest

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.source_primitives import (
    PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
    PublicReceiptError,
    canonical,
    decode,
    sha,
)
from app.public_market_source import public_runtime_custody_v4 as bundle
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import demo_market_source_v4 as source_v4
from app.trade_qualification import demo_operational_route_v4 as route_v4
from app.trade_qualification import public_source_runtime as runtime
from tests.unit.research.test_demo_operational_route_v4 import _config, _synthetic_lease
from tests.unit.research.test_demo_session_route_preflight import _client, _session
from tests.unit.research.test_owned_public_runtime_p1 import (
    resign_journal,
    synthetic_runtime,
)
from tests.unit.research.test_owned_public_runtime_v2 import policy
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import ms


@pytest.fixture(scope="module")
def market():
    return v2_engine_source("long")


def setup(monkeypatch, market):
    clock, directory, harness, _ = synthetic_runtime(
        monkeypatch,
        (market, None, None),
        rest_hostname="openapi.okx.com",
        ws_hostname="wspap.okx.com",
    )
    monkeypatch.setattr(source_v4, "native_stamp", clock.stamp)
    monkeypatch.setattr(route_v4, "native_stamp", clock.stamp)
    private_proof = MemoryDirectory({})

    @contextmanager
    def proof_root(_root):
        yield private_proof

    monkeypatch.setattr(runtime, "_native_recheck_root", proof_root)
    monkeypatch.setattr(
        route_v4, "_native_attempt_directory", lambda directory: directory
    )
    session, private = _session(), _client()
    original = harness.public

    def public(request):
        if request.url.path.endswith("/time"):
            return [{"ts": ms(clock.last)}]
        rows = original(request)
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
    calls = []

    async def account_probe(received_session, received_client):
        assert received_session is session and received_client is private
        assert calls == ["deployment"] and not harness.requests
        calls.append("account_config")
        observation = _synthetic_lease(session, private)
        state = route_v4._LEASES[observation._lease]
        state["raw"] = _config(
            uid=session._plan.expected_uid, main_uid=session._plan.expected_main_uid
        )
        for name in (
            "request_start",
            "headers_received",
            "body_completed",
            "validated",
        ):
            state[name] = clock.stamp()
        values = {
            name + "_ns": state[key]["utc_ns"]
            for name, key in (
                ("request_start", "request_start"),
                ("headers_received", "headers_received"),
                ("body_completed", "body_completed"),
                ("validated", "validated"),
            )
        }
        state.update(values)
        attempt = route_v4._PRIVATE_ATTEMPTS.get(session)
        if attempt is not None:
            route_v4._retain_attempt_chunk(attempt, state["raw"])
            attempt.update(
                status=200,
                body_stream_exhausted=True,
                request_start=state["request_start"],
                headers_received=state["headers_received"],
                body_completed=state["body_completed"],
                tls_peer_sha256=state["peer_sha256"],
            )
        observation = replace(
            observation, config_raw_sha256=sha(state["raw"]), **values
        )
        state["observation"] = ref(observation)
        return observation

    monkeypatch.setattr(
        route_v4, "probe_initial_demo_operational_route_v4", account_probe
    )
    repository = object.__new__(custody.RuntimeBundleCustodyRepository)

    async def deployment(_self):
        assert not harness.requests
        calls.append("deployment")

    async def retain(_self, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        assert sha(bundle_raw) == expected_sha256
        _, proof, _, _ = bundle.replay_runtime_bundle_v4(
            bundle_raw, expected_sha256=expected_sha256
        )
        assert sha(canonical(proof)) == proof_envelope_sha256
        calls.append("custody_commit_and_readback")
        # This fixture injects a synthetic DB observation, never a native claim.
        readback = custody.RetainedRuntimeBundleReadback(
            "a" * 32,
            expected_sha256,
            proof_envelope_sha256,
            clock.last,
            clock.last,
            bundle_raw,
        )
        receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
        custody._RECEIPTS[receipt] = (
            readback,
            custody._owner(),
            custody.time.monotonic_ns(),
        )
        return receipt

    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "verify_deployment", deployment
    )
    monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", retain)
    return clock, directory, harness, session, private, repository, calls, private_proof


async def acquire(tmp_path, state):
    return await source_v4.acquire_initial_demo_market_source_v4(
        tmp_path / "public",
        account_proof_root=tmp_path / "private-account-proof",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=state[3],
        private_client=state[4],
        custody_repository=state[5],
    )


@pytest.mark.asyncio
async def test_full_native_owner_control_flow_reaches_positive_current_source_only(
    monkeypatch, market, tmp_path
):
    state = setup(monkeypatch, market)
    owner = await acquire(tmp_path, state)
    result = source_v4.evaluate_owned_current_market_v4(owner)
    assert result.source_authenticity_verified is True
    assert result.durable_raw_readback_verified is True
    assert result.market.instrument_id == "BTC-USDT-SWAP"
    assert result.market.ticker.schema_version == "okx-swap-ticker-v2"
    assert result.market.ticker.volume_quote_24h is None
    assert result.account_complete is False and result.execution_authority is False
    assert (
        result.historical_availability_verified is False
        and result.registration_site == "UNKNOWN"
    )
    receipt = decode(result.receipt_json)
    assert (
        receipt["current_source_policy_sha256"]
        == PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256
    )
    assert state[6] == ["deployment", "account_config", "custody_commit_and_readback"]
    plan = decode(state[1].content["plan.json"])
    assert plan["schema_version"] == "ctcc.public.initial_runtime_plan.v4"
    assert plan["registration_region_authenticated"] is False
    assert all(request.method == "GET" for request in state[2].requests)
    assert all(
        request.headers["x-simulated-trading"] == "1" for request in state[2].requests
    )
    with pytest.raises(
        source_v4.CurrentMarketSourceV4Error, match="source_unavailable"
    ):
        source_v4.evaluate_owned_current_market_v4(owner)
    assert not source_v4._CURRENT and not route_v4._OWNED_ROUTES


@pytest.mark.asyncio
async def test_missing_custody_fails_before_account_or_public(
    monkeypatch, market, tmp_path
):
    state = setup(monkeypatch, market)

    async def absent(_self):
        raise custody.RuntimeBundleCustodyError("runtime_bundle_deployment_unavailable")

    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "verify_deployment", absent
    )
    with pytest.raises(
        source_v4.CurrentMarketSourceV4Error, match="custody_deployment_unavailable"
    ):
        await acquire(tmp_path, state)
    assert not state[2].requests and not state[1].content and not state[6]


@pytest.mark.asyncio
async def test_uncertain_custody_retains_raw_but_never_issues_source(
    monkeypatch, market, tmp_path
):
    state = setup(monkeypatch, market)
    attempts = 0

    async def uncertain(_self, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise custody.RuntimeBundleCustodyError("runtime_bundle_commit_uncertain")

    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "seal_bundle", uncertain
    )
    with pytest.raises(
        source_v4.CurrentMarketSourceV4Error,
        match="custody_commit_or_readback_unavailable",
    ):
        await acquire(tmp_path, state)
    assert attempts == 1 and "summary.json" in state[1].content
    assert "operational-route-proof.v4.json" in state[7].content
    assert not source_v4._CURRENT and not route_v4._OWNED_ROUTES


@pytest.mark.asyncio
async def test_wrong_task_burns_source_and_saved_bundle_cannot_restore_it(
    monkeypatch, market, tmp_path
):
    state = setup(monkeypatch, market)
    owner = await acquire(tmp_path, state)
    raw = source_v4._CURRENT[owner]["bundle_raw"]
    bundle.replay_runtime_bundle_v4(raw, expected_sha256=sha(raw))
    for clone in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(source_v4.CurrentMarketSourceV4Error):
            clone(owner)

    async def wrong_task():
        with pytest.raises(source_v4.CurrentMarketSourceV4Error):
            source_v4.evaluate_owned_current_market_v4(owner)

    await asyncio.create_task(wrong_task())
    with pytest.raises(
        source_v4.CurrentMarketSourceV4Error, match="source_unavailable"
    ):
        source_v4.evaluate_owned_current_market_v4(owner)
    assert not source_v4._CURRENT and not route_v4._OWNED_ROUTES


@pytest.mark.asyncio
async def test_rehashed_bundle_proof_and_inventory_tampering_are_denied(
    monkeypatch, market, tmp_path
):
    state = setup(monkeypatch, market)
    owner = await acquire(tmp_path, state)
    raw = source_v4._CURRENT[owner]["bundle_raw"]
    original = decode(raw, bundle.MAX_BUNDLE_BYTES)
    for field, value in (
        ("registration_site", "www.okx.com"),
        ("account_plan_sha256", "0" * 64),
    ):
        changed = copy.deepcopy(original)
        changed["operational_route_proof"][field] = value
        encoded = canonical(changed)
        with pytest.raises(PublicReceiptError, match="raw_bundle_invalid"):
            bundle.replay_runtime_bundle_v4(encoded, expected_sha256=sha(encoded))
    changed = copy.deepcopy(original)
    changed["files"].append(copy.deepcopy(changed["files"][0]))
    encoded = canonical(changed)
    with pytest.raises(PublicReceiptError, match="raw_bundle_invalid"):
        bundle.replay_runtime_bundle_v4(encoded, expected_sha256=sha(encoded))
    source_v4.evaluate_owned_current_market_v4(owner)


def test_v4_plan_cannot_be_relabelled_into_old_version_or_caller_bool():
    from tests.unit.test_public_time_bracket_v3_contract import _plan

    plan = _plan()
    plan.update(
        schema_version="ctcc.public.initial_runtime_plan.v4",
        registration_site="UNKNOWN",
        operational_route_proof_sha256="a" * 64,
        credential_session_sha256="b" * 64,
        current_source_policy_sha256=PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
    )
    journal._validate_plan(plan)
    for changed in (
        {**plan, "schema_version": "ctcc.public.initial_runtime_plan.v3"},
        {**plan, "registration_region_authenticated": True},
        {**plan, "source_authenticity_verified": True},
    ):
        with pytest.raises(PublicReceiptError):
            journal._validate_plan(changed)
    with pytest.raises(source_v4.CurrentMarketSourceV4Error):
        source_v4.OwnedCurrentMarketSourceV4()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before_freeze", "after_freeze_before_custody"])
async def test_rehashed_transport_journal_cannot_replace_native_pin(
    monkeypatch, market, tmp_path, boundary
):
    state = setup(monkeypatch, market)
    original_consume = runtime._consume_initial_public_capture_v4
    original_freeze = source_v4.freeze_runtime_bundle_v4
    captured = {}

    def alter_tls(directory):
        original_packet = directory.content[
            max(
                (name for name in directory.content if name.endswith(".raw")),
                key=lambda name: int(name.split("-")[1].split(".")[0]),
            )
        ]

        def mutate(event, _directory):
            if event["kind"] == "headers":
                event["metadata"]["tls"]["peer_sha256"] = "9" * 64

        old_pin = sha(directory.content["summary.json"])
        resign_journal(directory, mutate)
        assert sha(directory.content["summary.json"]) != old_pin
        _, _, replayed = runtime.replay_public_runtime(
            directory, expected_plan_sha256=sha(directory.content["plan.json"])
        )
        # Self-consistent replay and unchanged prices/volumes do not identify
        # the native owner's original TLS/clock journal.
        assert replayed.packet_json == original_packet

    def consume(carrier, invocation):
        result = original_consume(carrier, invocation)
        captured["native_pin"] = result[1]
        if boundary == "before_freeze":
            alter_tls(state[1])
        return result

    def freeze(directory, proof):
        raw = original_freeze(directory, proof)
        if boundary == "after_freeze_before_custody":
            document = decode(raw, bundle.MAX_BUNDLE_BYTES)
            view = MemoryDirectory(
                {
                    entry["name"]: bytes.fromhex(entry["raw_hex"])
                    for entry in document["files"]
                }
            )
            alter_tls(view)
            for entry in document["files"]:
                entry["raw_hex"] = view.content[entry["name"]].hex()
            raw = canonical(document)
            bundle.replay_runtime_bundle_v4(raw, expected_sha256=sha(raw))
        return raw

    monkeypatch.setattr(runtime, "_consume_initial_public_capture_v4", consume)
    monkeypatch.setattr(source_v4, "freeze_runtime_bundle_v4", freeze)
    expected = (
        "native_journal_changed"
        if boundary == "before_freeze"
        else "custody_journal_changed"
    )
    with pytest.raises(source_v4.CurrentMarketSourceV4Error, match=expected):
        await acquire(tmp_path, state)
    assert captured["native_pin"]
    assert ("custody_commit_and_readback" in state[6]) == (boundary != "before_freeze")
    assert not source_v4._CURRENT and not route_v4._OWNED_ROUTES
