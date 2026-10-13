"""Public transport pool isolation; configuration only, never network IO."""

import asyncio
import os
import ssl
from threading import get_ident

import httpcore
import httpx
import pytest

from app.trade_qualification import candle_collector as candles
from app.trade_qualification import market_aux_collector as aux
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import quote_collector as quotes


def alter_pool(pool, case):
    if case == "nested_proxy":
        pool._proxy = httpcore.Proxy("http://synthetic-proxy.invalid:8080")
    elif case == "unix_socket":
        pool._uds = "synthetic-local-socket"
    elif case == "local_bind":
        pool._local_address = "127.0.0.1"
    elif case == "socket_options":
        pool._socket_options = [(1, 2, 3)]
    elif case == "backend":
        pool._network_backend = object()
    else:
        raise AssertionError("undeclared case")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["nested_proxy", "unix_socket", "local_bind", "socket_options", "backend"]
)
async def test_shared_public_collectors_reject_hidden_routes_before_send(
    monkeypatch, case
):
    sends = []

    async def no_send(*args, **kwargs):
        sends.append(True)
        raise AssertionError("public request reached send")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_send)
    client = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
    )
    alter_pool(client._transport._pool, case)
    try:
        for checker in (
            quotes._public_client,
            candles._isolated_client,
            aux._isolated_client,
        ):
            with pytest.raises(
                quotes.QuoteCollectionError, match="transport_not_isolated"
            ):
                checker(client)
        assert sends == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_default_direct_pool_and_mock_boundary_are_distinct():
    direct = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
    )
    mocked = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: None), trust_env=False
    )
    try:
        quotes._public_client(direct)
        quotes._public_client(mocked)
        candles._isolated_client(mocked)
        aux._isolated_client(mocked)
    finally:
        await direct.aclose()
        await mocked.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden_proxy", [False, True])
async def test_owned_public_runtime_direct_pool_vs_hidden_proxy_before_io(
    monkeypatch, hidden_proxy
):
    sends = []

    async def no_send(*args, **kwargs):
        sends.append(True)
        raise AssertionError("owned public request reached send")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_send)
    context = ssl.create_default_context()
    client = httpx.AsyncClient(
        verify=context, trust_env=False, follow_redirects=False, auth=None
    )
    if hidden_proxy:
        alter_pool(client._transport._pool, "nested_proxy")
    owner = runtime._Source(runtime._ISSUER)
    loop = asyncio.get_running_loop()
    runtime._SOURCES[owner] = {
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": loop,
        "parent": asyncio.current_task(),
        "deadline": loop.time() + 60,
        "clients": {client: ("quote", context)},
    }
    try:
        if hidden_proxy:
            with pytest.raises(
                quotes.QuoteCollectionError, match="transport_not_isolated"
            ):
                runtime._verified_client(owner, client)
        else:
            assert runtime._verified_client(owner, client) == ("quote", context)
        assert sends == []
    finally:
        runtime._SOURCES.pop(owner)
        await client.aclose()
