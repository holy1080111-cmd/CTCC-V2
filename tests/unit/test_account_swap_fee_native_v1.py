"""Synthetic transport and custody only; no OKX or PostgreSQL acceptance."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.source_primitives import sha
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_swap_fee_native_v1 as native_fee
from app.trade_qualification import account_swap_fee_source_v1 as fees
from tests.unit.test_account_current_source_v4 import setup
from tests.unit.test_account_swap_fee_source_v1 import fee_row
from tests.unit.test_qualification_account_capture import INSTRUMENT, wire


def _response(request, *, matching=True):
    started = datetime.fromisoformat(request.headers["ok-access-timestamp"])
    stamp = str(int(started.timestamp() * 1000))
    row = fee_row(
        ts=stamp,
        group=None
        if matching
        else [{"groupId": "3", "maker": "-0.0002", "taker": "-0.0005"}],
    )
    return httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=httpx.ByteStream(wire([row])),
    )


def _custody(monkeypatch, *, before_seal=None):
    saved = []

    async def seal(_self, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        if before_seal is not None:
            before_seal()
        assert sha(bundle_raw) == expected_sha256
        saved.append(bundle_raw)
        readback = custody.RetainedRuntimeBundleReadback(
            "b" * 32,
            expected_sha256,
            proof_envelope_sha256,
            datetime.now(UTC),
            datetime.now(UTC),
            bundle_raw,
        )
        receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
        custody._RECEIPTS[receipt] = (
            readback,
            custody._owner(),
            custody.time.monotonic_ns(),
        )
        return receipt

    monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)
    return saved


def _script_fee_instrument(fixture):
    # This is a newly declared synthetic response before any B1 request.
    fixture.harness.script = [
        (
            stream,
            [
                {
                    **item,
                    "tickSz": "0.1",
                    "instFamily": "BTC-USDT",
                    "groupId": "2",
                }
                for item in rows
            ]
            if stream == "account_instruments"
            else deepcopy(rows),
        )
        for stream, rows in fixture.harness.script
    ]


@pytest.mark.asyncio
async def test_native_fee_get_uses_original_current_lease_and_custody(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as fixture:
        _script_fee_instrument(fixture)
        source = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        seen = []
        clients = []

        def handler(request):
            seen.append(request)
            assert request.method == "GET"
            assert request.url.host == "openapi.okx.com"
            assert request.url.path == fees.FEE_ENDPOINT
            assert tuple(request.url.params.multi_items()) == (
                ("instFamily", "BTC-USDT"),
                ("instType", "SWAP"),
            )
            assert request.headers["x-simulated-trading"] == "1"
            assert request.headers["ok-access-sign"]
            return _response(request)

        def new_client():
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                trust_env=False,
                follow_redirects=False,
            )
            clients.append(client)
            return client

        monkeypatch.setattr(native_fee.collector, "_new_client", new_client)
        monkeypatch.setattr(native_fee, "native_stamp", fixture.samples)

        def closed_before_seal():
            assert len(clients) == 1 and clients[0].is_closed

        saved = _custody(monkeypatch, before_seal=closed_before_seal)
        result = await native_fee.record_native_demo_swap_fee_v1(
            current_source=source, instrument_id=INSTRUMENT
        )
        assert len(seen) == len(saved) == 1
        assert result.source_bundle_raw == saved[0]
        assert sha(saved[0]) == result.source_bundle_sha256
        document = json.loads(saved[0])
        assert document["fee_observation"]["raw_body_sha256"] == sha(
            bytes.fromhex(document["fee_observation"]["raw_body_hex"])
        )
        assert document["fee_diagnostic_receipt_hex"]
        assert document["fee_observation"]["status_code"] == 200
        assert document["fee_observation"]["content_type"] == "application/json"
        assert document["fee_observation"]["content_encoding"] == "identity"
        assert document["fee_observation"]["redirected"] is False
        assert result.signed_maker_rate == Decimal("0.0001")
        assert result.signed_taker_rate == Decimal("-0.0006")
        assert result.transport_classification == "synthetic_mock"
        assert result.admission == "DENY"
        assert not any(
            (
                result.account_complete,
                result.source_authenticity_verified,
                result.fee_cost_authority,
                result.execution_authority,
            )
        )
        for token in (
            fixture.session._credentials.api_key,
            fixture.session._credentials.api_secret,
            fixture.session._credentials.passphrase,
            fixture.session._credentials.session_binding_id,
        ):
            assert token.encode("ascii") not in saved[0]
        with pytest.raises(
            native_fee.NativeSwapFeeError, match="owner_already_attempted"
        ):
            await native_fee.record_native_demo_swap_fee_v1(
                current_source=source, instrument_id="ETH-USDT-SWAP"
            )
        assert len(seen) == len(saved) == 1


@pytest.mark.asyncio
async def test_wrong_fee_group_denies_without_sealing(monkeypatch, tmp_path):
    with setup(monkeypatch, tmp_path) as fixture:
        _script_fee_instrument(fixture)
        source = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        seen = []

        def handler(request):
            seen.append(request)
            return _response(request, matching=False)

        monkeypatch.setattr(
            native_fee.collector,
            "_new_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                trust_env=False,
                follow_redirects=False,
            ),
        )
        monkeypatch.setattr(native_fee, "native_stamp", fixture.samples)
        saved = _custody(monkeypatch)
        with pytest.raises(native_fee.NativeSwapFeeError):
            await native_fee.record_native_demo_swap_fee_v1(
                current_source=source, instrument_id=INSTRUMENT
            )
        assert len(seen) == 1
        assert saved == []


@pytest.mark.asyncio
async def test_portable_or_missing_owner_cannot_trigger_get(monkeypatch):
    seen = []
    monkeypatch.setattr(
        native_fee.collector,
        "_new_client",
        lambda: seen.append(True),
    )
    with pytest.raises(native_fee.NativeSwapFeeError, match="current_owner_required"):
        await native_fee.record_native_demo_swap_fee_v1(
            current_source=object(), instrument_id=INSTRUMENT
        )
    assert seen == []


@pytest.mark.asyncio
async def test_closed_original_lease_denies_before_fee_get(monkeypatch, tmp_path):
    with setup(monkeypatch, tmp_path) as fixture:
        _script_fee_instrument(fixture)
        source = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        seen = []
        monkeypatch.setattr(
            native_fee.collector,
            "_new_client",
            lambda: seen.append(True),
        )
        monkeypatch.setattr(native_fee, "native_stamp", fixture.samples)
        fixture.session._read_session._lease.active = False
        with pytest.raises(native_fee.NativeSwapFeeError):
            await native_fee.record_native_demo_swap_fee_v1(
                current_source=source, instrument_id=INSTRUMENT
            )
        assert seen == []


@pytest.mark.asyncio
async def test_late_source_expiry_keeps_sealed_evidence(monkeypatch, tmp_path):
    with setup(monkeypatch, tmp_path) as fixture:
        _script_fee_instrument(fixture)
        source = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        seen = []

        def handler(request):
            seen.append(request)
            return _response(request)

        monkeypatch.setattr(
            native_fee.collector,
            "_new_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                trust_env=False,
                follow_redirects=False,
            ),
        )
        monkeypatch.setattr(native_fee, "native_stamp", fixture.samples)
        saved = _custody(monkeypatch)
        original = custody.RuntimeBundleCustodyRepository.seal_bundle

        async def seal_and_expire(*args, **kwargs):
            receipt = await original(*args, **kwargs)
            fixture.samples.calls += 31_000
            return receipt

        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository, "seal_bundle", seal_and_expire
        )
        with pytest.raises(native_fee.NativeSwapFeeError):
            await native_fee.record_native_demo_swap_fee_v1(
                current_source=source, instrument_id=INSTRUMENT
            )
        assert len(seen) == len(saved) == 1


@pytest.mark.asyncio
async def test_fee_transport_cap_drift_denies_before_get(monkeypatch, tmp_path):
    with setup(monkeypatch, tmp_path) as fixture:
        _script_fee_instrument(fixture)
        source = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        seen = []
        monkeypatch.setattr(
            native_fee.collector,
            "_new_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: seen.append(request)),
                trust_env=False,
                follow_redirects=False,
            ),
        )
        monkeypatch.setattr(native_fee, "native_stamp", fixture.samples)
        monkeypatch.setattr(
            native_fee.signed.history,
            "MAX_PAGE_BYTES",
            fees.MAX_FEE_BYTES + 1,
        )
        with pytest.raises(
            native_fee.NativeSwapFeeError, match="transport_byte_policy_changed"
        ):
            await native_fee.record_native_demo_swap_fee_v1(
                current_source=source, instrument_id=INSTRUMENT
            )
        assert seen == []
