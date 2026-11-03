"""Primitive DATA custody only; no native source, TLS success or private IO."""

import weakref
from types import SimpleNamespace

import httpx
import pytest

from app.exchange.okx import native_reconciliation_observation_v1 as read


@pytest.mark.asyncio
async def test_actual_httpx_owner_survives_caller_reference_drop_before_dispatch():
    sdk = SimpleNamespace()
    transport = httpx.AsyncClient(trust_env=False)
    reference = weakref.ref(transport)
    journal = read.begin(
        sdk,
        SimpleNamespace(binding_id="a" * 64),
        "/api/v5/account/config",
        {},
        "https://www.okx.com/api/v5/account/config",
        transport_client=transport,
    )
    del transport
    assert reference() is journal.transport_client
    assert sdk._native_reconciliation_read_journals_v1[0] is journal
    await journal.transport_client.aclose()


@pytest.mark.asyncio
async def test_actual_response_is_retained_before_later_tls_or_close_failure():
    sdk = SimpleNamespace()
    async with httpx.AsyncClient(trust_env=False) as transport:
        request = httpx.Request(
            "GET",
            "https://www.okx.com/api/v5/account/config",
            headers={"OK-ACCESS-SIGN": "unit-signature"},
        )
        response = httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=b'{"code":"0","data":[]}',
        )
        reference = weakref.ref(response)
        journal = read.begin(
            sdk,
            SimpleNamespace(binding_id="a" * 64),
            "/api/v5/account/config",
            {},
            str(request.url),
            transport_client=transport,
        )
        journal.signature = "unit-signature"
        read.retain_prepared_request_v1(journal, request)
        # This synthetic already-consumed response MUST fail native TLS checks.
        with pytest.raises(ValueError, match="native_reconciliation_read_data_invalid"):
            read.retain_transport(journal, transport, request, response)
        journal.fault("close_failed")
        del response
        assert reference() is journal.response and journal.prepared_request is request
        assert journal.transport_client is transport and journal.faults == [
            "close_failed"
        ]
