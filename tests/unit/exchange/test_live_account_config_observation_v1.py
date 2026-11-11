from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.config.settings import Settings
from app.domain.source_primitives import ClockStamp
from app.exchange.okx import live_account_config_observation_v1 as observation_module
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.live_account_config_observation_v1 import (
    LiveConfigObservationError,
    _check_client,
    observe_live_account_config_v1,
    verify_live_account_config_readback_v1,
)
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1


def _settings(**updates) -> Settings:
    values = {
        "environment": "test",
        "trading_mode": "analysis_only",
        "live_trading": False,
        "okx_live_enabled": True,
        "okx_live_allow_order_writes": False,
        "okx_live_auto_execution": False,
        "okx_live_api_key": "synthetic-key",
        "okx_live_api_secret": "synthetic-secret",
        "okx_live_api_passphrase": "synthetic-passphrase",
        "okx_live_expected_uid": "1234567",
        "okx_live_expected_main_uid": "1234568",
        "okx_live_read_max_retries": 5,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.okx.com",
        trust_env=False,
        follow_redirects=False,
    )


class _RawStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body

    async def aclose(self) -> None:
        pass


def _raw_response(
    body: bytes, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        stream=_RawStream(body),
        headers=headers
        or {"content-type": "application/json", "content-length": str(len(body))},
    )


def _success() -> httpx.Response:
    return _raw_response(
        b'{"code":"0","msg":"","data":[{"uid":"1234567",'
        b'"mainUid":"1234568","acctLv":"2"}]}'
    )


@pytest.mark.asyncio
async def test_live_config_raw_get_is_bounded_synthetic_data_only() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://openapi.okx.com/api/v5/account/config"
        assert request.headers["accept-encoding"] == "identity"
        assert "x-simulated-trading" not in request.headers
        return _success()

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            observed = await observe_live_account_config_v1(owner, lease)
            readback = verify_live_account_config_readback_v1(observed, lease=lease)
            assert readback["receipt_sha256"] == observed.receipt_sha256
            assert readback["synthetic_mock"] is True
            assert observed.tls_certificate_sha256 is None
            assert observed.account_complete is False
            assert observed.source_authenticity_verified is False
            assert observed.execution_authority is False
            assert len(observed.stamps) == 6
            assert len(observed.raw_body) > 0
            assert "synthetic-secret" not in repr(observed)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b'{"code":"0","data":[]}', "live_config_row_count_invalid"),
        (
            b'{"code":"0","data":[{"uid":"999","mainUid":"1234568"}]}',
            "live_config_account_identity_mismatch",
        ),
        (
            b'{"code":"0","code":"0","data":[{"uid":"1234567","mainUid":"1234568"}]}',
            "live_config_response_invalid",
        ),
        (b"x" * 65537, "live_config_body_bound"),
    ],
    ids=("empty", "uid_mismatch", "duplicate_json", "oversize"),
)
async def test_live_config_invalid_raw_body_fails_closed(
    body: bytes, code: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _raw_response(body)

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with (
            owner.pinned_get_lease() as lease,
            pytest.raises(LiveConfigObservationError) as caught,
        ):
            await observe_live_account_config_v1(owner, lease)
    assert str(caught.value) == code
    assert calls == 1


@pytest.mark.asyncio
async def test_live_config_rejects_redirect_and_compression_without_decode() -> None:
    for response, code in (
        (
            httpx.Response(302, headers={"location": "https://eea.okx.com/"}),
            "live_config_http_response_invalid",
        ),
        (
            httpx.Response(
                200,
                stream=_RawStream(b"compressed"),
                headers={
                    "content-type": "application/json",
                    "content-encoding": "gzip",
                },
            ),
            "live_config_http_response_invalid",
        ),
    ):
        async with _http(lambda _request, item=response: item) as http:
            owner = OkxLivePinnedGetClientV1(http, settings=_settings())
            with (
                owner.pinned_get_lease() as lease,
                pytest.raises(LiveConfigObservationError) as caught,
            ):
                await observe_live_account_config_v1(owner, lease)
        assert str(caught.value) == code


@pytest.mark.asyncio
async def test_live_config_readback_rejects_mutated_raw_without_authority() -> None:
    async with _http(lambda _request: _success()) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            observed = await observe_live_account_config_v1(owner, lease)
            forged = replace(observed, raw_body=b'{"code":"0","data":[]}')
            with pytest.raises(LiveConfigObservationError) as caught:
                verify_live_account_config_readback_v1(forged, lease=lease)
    assert str(caught.value) == "live_config_readback_invalid"


@pytest.mark.asyncio
async def test_live_config_observation_refuses_closed_lease_before_http() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success()

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with owner.pinned_get_lease() as lease:
            pass
        with pytest.raises(OkxPrivateApiError) as closed:
            await observe_live_account_config_v1(owner, lease)
    assert closed.value.code == "live_get_lease_invalid"
    assert calls == 0


@pytest.mark.asyncio
async def test_live_config_observation_never_retries_transport_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("synthetic transport failure", request=request)

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with (
            owner.pinned_get_lease() as lease,
            pytest.raises(LiveConfigObservationError) as caught,
        ):
            await observe_live_account_config_v1(owner, lease)
    assert str(caught.value) == "live_config_observation_failed"
    assert calls == 1


@pytest.mark.asyncio
async def test_live_config_observation_rejects_secret_echo_before_retention() -> None:
    body = (
        b'{"code":"0","data":[{"uid":"1234567",'
        b'"mainUid":"1234568","echo":"synthetic-secret"}]}'
    )
    async with _http(lambda _request: _raw_response(body)) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with (
            owner.pinned_get_lease() as lease,
            pytest.raises(LiveConfigObservationError) as caught,
        ):
            await observe_live_account_config_v1(owner, lease)
    assert str(caught.value) == "live_config_credential_echo_rejected"


@pytest.mark.asyncio
async def test_live_config_observation_rejects_private_response_header() -> None:
    raw = b'{"code":"0","data":[{"uid":"1234567","mainUid":"1234568"}]}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return _raw_response(
            raw,
            headers={
                "content-type": "application/json",
                "x-echo": "synthetic-secret",
            },
        )

    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        with (
            owner.pinned_get_lease() as lease,
            pytest.raises(LiveConfigObservationError) as caught,
        ):
            await observe_live_account_config_v1(owner, lease)
    assert str(caught.value) == "live_config_credential_echo_rejected"


@pytest.mark.asyncio
async def test_live_config_runtime_client_guard_requires_isolated_tls() -> None:
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
        follow_redirects=False,
    ) as isolated:
        _check_client(isolated, synthetic=False)
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=True,
        follow_redirects=False,
    ) as inherited:
        with pytest.raises(LiveConfigObservationError) as caught:
            _check_client(inherited, synthetic=False)
    assert str(caught.value) == "live_config_transport_invalid"


@pytest.mark.asyncio
async def test_live_config_late_final_check_cannot_dispatch_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    advanced = False
    original_stamp = observation_module._stamp

    def fake_stamp() -> ClockStamp:
        stamp = original_stamp()
        if advanced:
            return ClockStamp(
                utc_ns=stamp.utc_ns + 11_000_000_000,
                monotonic_ns=stamp.monotonic_ns + 11_000_000_000,
            )
        return stamp

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success()

    monkeypatch.setattr(observation_module, "_stamp", fake_stamp)
    async with _http(handler) as http:
        owner = OkxLivePinnedGetClientV1(http, settings=_settings())
        original_final = owner._final_dispatch_check

        def late_final(*, method: str, path: str) -> None:
            nonlocal advanced
            original_final(method=method, path=path)
            advanced = True

        monkeypatch.setattr(owner, "_final_dispatch_check", late_final)
        with (
            owner.pinned_get_lease() as lease,
            pytest.raises(LiveConfigObservationError) as caught,
        ):
            await observe_live_account_config_v1(owner, lease)
    assert str(caught.value) == "live_config_request_deadline_exceeded"
    assert calls == 0
