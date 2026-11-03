"""Real HTTPX retained DATA mechanics; no socket, native owner or SQL grant."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.exchange.okx import native_demo_write_v2 as write
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_controlled_account_source_v6 as source
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def request_data():
    url = "https://www.okx.com/api/v5/account/config"
    request = httpx.Request(
        "GET",
        url,
        headers={
            "OK-ACCESS-SIGN": "unit-hmac",
            "OK-ACCESS-KEY": "unit-key",
            "OK-ACCESS-PASSPHRASE": "unit-pass",
        },
    )
    journal = read.ReadJournalV1("/api/v5/account/config", (), url, "a" * 64)
    journal.signature = "unit-hmac"
    return journal, request


def test_prepared_signed_get_is_retained_before_headers_or_response():
    journal, request = request_data()
    read.retain_prepared_request_v1(journal, request)
    assert journal.prepared_request is request and journal.response is None
    data = read.retained_httpx_journal_data_v1(journal)
    assert b"unit-hmac" in data and b"unit-key" in data and b"unit-pass" in data
    assert all(type(value) in (bytes, str) for value in data)
    assert len({id(value) for value in data}) == len(data)
    with pytest.raises(ValueError):
        read.retain_prepared_request_v1(journal, request)


def test_response_stored_copy_is_counted_as_well_as_request_and_chunks():
    journal, request = request_data()
    read.retain_prepared_request_v1(journal, request)
    body = b'{"code":"0","data":[]}'
    stored_copy = bytes(bytearray(body))
    assert stored_copy == body and stored_copy is not body
    journal.chunks.append(body)
    journal.response = httpx.Response(
        200,
        content=stored_copy,
        request=request,
        headers={"Content-Type": "application/json"},
    )
    data = read.retained_httpx_journal_data_v1(journal)
    assert any(value is vars(journal.response)["_content"] for value in data)
    assert b"unit-key" in data
    # No received echo scan is called on these deliberately signed headers.
    assert journal.observation is None and journal.eof_stamp is None


def test_foreign_sdk_object_and_unread_stream_are_not_generic_traversal():
    for value in (
        object(),
        SimpleNamespace(headers={"secret": "x"}),
        httpx.Request("POST", "https://www.okx.com/", stream=iter((b"x",))),
    ):
        with pytest.raises(ValueError):
            read.retained_httpx_request_data_v1(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase", ("before_gate", "held_after_gate", "missing_after_gate")
)
async def test_actual_root_buffer_body_counts_existing_wire_and_rejects_missing_custody(
    phase,
):
    """Explicit foreign-namespace extraction tests the DATA enumeration only.

    The actual Source/Root registry authentication is NOT replaced in product.
    The clone fails the actual guard and creates no write/read/cleanup permit.
    """
    binding = object()
    state = {
        "task": asyncio.current_task(),
        "request_json": "unit-request",
        "intent": SimpleNamespace(canonical_json="unit-intent"),
    }
    record = None
    if phase != "before_gate":
        journal = write._native_transport._ResponseDataJournalV2()
        journal.retain_headers(200, ((b"content-type", b"application/json"),))
        journal.retain_tls(b"HTTP/1.1 200 OK\r\n\r\n")
        journal.retain_body(b'{"code":"0"}')
        _, request = request_data()
        gate = object.__new__(write._PhysicalWriteGateV2)
        record = {
            "state": state,
            "gate": gate,
            "received": journal,
            "original_transport_facts": SimpleNamespace(transport_binding=binding),
            "pid": write.os.getpid(),
            "thread": write.threading.get_ident(),
            "task": state["task"],
            "loop": asyncio.get_running_loop(),
            "wire": b"actual-signed-request-wire",
            "actual_post_signature": "unit-hmac",
            "request": request,
            "observed_result": None,
        }
        state["native_transport_received_data_v2"] = journal
        if phase == "held_after_gate":
            # Still strongly held by the genuine native state after Root maps
            # retire; no missing pointer is interpreted as actual FD closure.
            state["native_root_resource_data_v1"] = record
    overrides = {
        "_NATIVE_GUARD": lambda: None,
        "_GATES": {},
        "_native_source_memory_state_v1": lambda token, native: (state, False),
        "_native_retained_request_documents_v1": lambda held: (b"actual-intent-json",),
    }
    body = foreign_primitive_v1(write._native_source_retained_buffers_v1, overrides)
    if phase == "missing_after_gate":
        with pytest.raises(write.NativeDemoWriteError):
            body(binding, key=source._SCAN_KEY_V6)
    else:
        values = body(binding, key=source._SCAN_KEY_V6)
        if phase == "before_gate":
            assert values == ("unit-request", "unit-intent")
        else:
            assert (
                record["wire"] in values
                and "unit-hmac" in values
                and b"unit-key" in values
            )
            assert any(
                value is state["native_transport_received_data_v2"].body_chunks[0]
                for value in values
            )
    with pytest.raises(ValueError):
        guard._function_pin(body, {})


def test_unregistered_binding_never_becomes_empty_source_memory():
    with pytest.raises(ValueError):
        write._native_source_retained_buffers_v1(object(), key=source._SCAN_KEY_V6)
