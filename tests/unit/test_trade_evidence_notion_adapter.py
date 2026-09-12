"""Synthetic Notion HTTP contracts only; no real API, token or destination."""

import asyncio
import json
import os
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr, create_model

from app.trade_evidence import notion_adapter as module
from app.trade_evidence import outbox
from app.trade_evidence.outbox_worker import OutboxWorkerPolicy, run_outbox_pass
from tests.unit.test_trade_evidence_outbox import (
    NOW,
    REPORT,
    Clock,
    MemoryBackend,
    payload,
)

DB = "1" * 32
SOURCE = "2" * 32
PAGE = "3" * 32
TOKEN = SecretStr("synthetic_notion_token_not_a_real_credential")


@pytest.fixture
def memory(monkeypatch):
    backend = MemoryBackend()
    monkeypatch.setattr(outbox, "_root_context", backend.context)
    return backend


def destination():
    return module.NotionDestination(
        database_id=DB,
        data_source_id=SOURCE,
        properties=tuple(
            module.NotionPropertyPin(
                role=role, property_id=identity, name=name, kind=kind
            )
            for role, identity, name, kind in (
                ("report_id", "title", "Report", "title"),
                ("envelope_sha256", "env", "Envelope", "rich_text"),
                ("payload_sha256", "payload", "Payload", "rich_text"),
                ("metadata_json", "meta", "Metadata", "rich_text"),
            )
        ),
    )


def claim():
    return outbox.ClaimToken(
        report_id=REPORT,
        envelope_sha256="a" * 64,
        revision=3,
        event_sha256="b" * 64,
        fence_token="c" * 32,
        worker_id="synthetic-worker",
        lease_expires_at=NOW + timedelta(seconds=60),
        status="dispatching",
    )


def schema():
    target = destination()
    return {
        "object": "data_source",
        "id": SOURCE,
        "in_trash": False,
        "parent": {"type": "database_id", "database_id": DB},
        "properties": {
            pin.name: {
                "id": pin.property_id,
                "name": pin.name,
                "type": pin.kind,
                pin.kind: {},
            }
            for pin in target.properties
        },
    }


def page(value=None, token=None):
    texts = module._texts(value or payload(), token or claim())
    return {
        "object": "page",
        "id": PAGE,
        "in_trash": False,
        "archived": False,
        "parent": {
            "type": "data_source_id",
            "data_source_id": SOURCE,
            "database_id": DB,
        },
        "properties": {
            pin.name: {
                "id": pin.property_id,
                "type": pin.kind,
                pin.kind: [
                    {
                        "type": "text",
                        "text": {"content": texts[pin.role], "link": None},
                        "plain_text": texts[pin.role],
                        "href": None,
                    }
                ],
            }
            for pin in destination().properties
        },
    }


def listing(*pages):
    return {
        "object": "list",
        "type": "page_or_data_source",
        "page_or_data_source": {},
        "has_more": False,
        "next_cursor": None,
        "results": list(pages),
    }


def client_for(replies):
    calls = []

    def handler(request):
        calls.append(request)
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply(request)
        return (
            reply if type(reply) is httpx.Response else httpx.Response(200, json=reply)
        )

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ), calls


def adapter(client, **changes):
    return module.NotionDeliveryAdapter(
        **{
            "client": client,
            "token": TOKEN,
            "destination": destination(),
            "clock": Clock(),
            **changes,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [True, False])
async def test_exact_report_reuse_or_single_create_with_verified_query_readback(
    existing,
):
    replies = (
        [schema(), listing(page())]
        if existing
        else [schema(), listing(), page(), listing(page())]
    )
    client, calls = client_for(replies)
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "delivered" and result.remote_page_id == PAGE
    assert len(result.receipt_sha256) == 64
    assert [call.method for call in calls] == (
        ["GET", "POST"] if existing else ["GET", "POST", "POST", "POST"]
    )
    creates = [call for call in calls if call.url.path == "/v1/pages"]
    assert len(creates) == (0 if existing else 1)
    for call in calls:
        assert str(call.url).startswith(module.ORIGIN + "/v1/")
        assert call.headers["Notion-Version"] == "2026-03-11"
        assert call.headers["Authorization"] == "Bearer " + TOKEN.get_secret_value()
        assert "cookie" not in call.headers
        assert "Idempotency-Key" not in call.headers
    if creates:
        body = json.loads(creates[0].content)
        assert set(body) == {"parent", "properties"}
        assert body["parent"] == {"type": "data_source_id", "data_source_id": SOURCE}
        assert set(body["properties"]) == {
            pin.property_id for pin in destination().properties
        }
        metadata = body["properties"]["meta"]["rich_text"][0]["text"]["content"]
        assert json.loads(metadata)["execution_authority"] is False
        assert TOKEN.get_secret_value() not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["before_query", "after_create"])
@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "has_more",
        "cursor",
        "wrong_parent",
        "wrong_pin",
        "trashed",
        "wrong_page",
    ],
)
async def test_collisions_and_incomplete_readback_never_repeat_create(where, mutation):
    row = page()
    found = listing(row)
    if mutation == "duplicate":
        found = listing(row, row)
    elif mutation == "has_more":
        found["has_more"] = True
    elif mutation == "cursor":
        found["next_cursor"] = "synthetic-next"
    elif mutation == "wrong_parent":
        row["parent"]["database_id"] = "d" * 32
    elif mutation == "wrong_pin":
        row["properties"]["Envelope"]["rich_text"][0]["text"]["content"] = "d" * 64
    elif mutation == "trashed":
        row["in_trash"] = True
    else:
        if where == "before_query":
            row["id"] = "not-a-page"
        else:
            row["id"] = "4" * 32
    replies = (
        [schema(), found]
        if where == "before_query"
        else [schema(), listing(), page(), found]
    )
    client, calls = client_for(replies)
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "uncertain" and result.receipt_sha256 is None
    assert sum(call.url.path == "/v1/pages" for call in calls) == (
        where == "after_create"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["source", "parent", "missing", "kind", "id", "name", "trash", "bool"],
)
async def test_destination_schema_is_checked_before_query_or_create(mutation):
    value = schema()
    if mutation == "source":
        value["id"] = "f" * 32
    elif mutation == "parent":
        value["parent"]["database_id"] = "f" * 32
    elif mutation == "missing":
        del value["properties"]["Metadata"]
    elif mutation == "kind":
        value["properties"]["Metadata"]["type"] = "url"
    elif mutation in {"id", "name"}:
        value["properties"]["Metadata"][mutation] = "wrong"
    elif mutation == "trash":
        value["in_trash"] = True
    else:
        value["in_trash"] = 0
    client, calls = client_for([value])
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "uncertain" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 100])
async def test_existing_additional_database_columns_are_not_changed_or_required(count):
    source, row = schema(), page()
    for number in range(count):
        name, identity = f"Other {number}", f"other{number}"
        source["properties"][name] = {
            "id": identity,
            "name": name,
            "type": "rich_text",
            "rich_text": {},
        }
        row["properties"][name] = {"id": identity, "type": "rich_text", "rich_text": []}
    client, calls = client_for([source, listing(row)])
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "delivered" and len(calls) == 2
    assert all(call.url.path != "/v1/pages" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["schema", "page"])
async def test_additional_property_cannot_alias_a_pinned_property_id(role):
    source, row = schema(), page()
    target = source if role == "schema" else row
    target["properties"]["Alias"] = dict(target["properties"]["Envelope"])
    client, calls = client_for([source, listing(row)])
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "uncertain"
    assert all(call.url.path != "/v1/pages" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [1.25, -0.000005, 9007199254740991, 1e100, 1e-300])
async def test_unrelated_existing_number_column_accepts_bounded_finite_numbers(number):
    source, row = schema(), page()
    source["properties"]["Existing Number"] = {
        "id": "other",
        "name": "Existing Number",
        "type": "number",
        "number": {"format": "number"},
    }
    row["properties"]["Existing Number"] = {
        "id": "other",
        "type": "number",
        "number": number,
    }
    client, calls = client_for([source, listing(row)])
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "delivered" and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["schema", "create"])
@pytest.mark.parametrize(
    "failure",
    [
        "transport",
        "400",
        "401",
        "403",
        "404",
        "409",
        "429",
        "500",
        "503",
        "529",
        "503_retry_after",
    ],
)
async def test_remote_failures_are_never_blind_create_retries(stage, failure):
    if failure == "transport":
        bad = httpx.ReadTimeout("synthetic_private_message_never_exposed")
    else:
        status = int(failure.split("_", 1)[0])
        bad = httpx.Response(
            status,
            json={
                "object": "error",
                "message": "synthetic_private_message_never_exposed",
            },
            headers={"Retry-After": "120"} if "retry_after" in failure else {},
        )
    replies = [bad] if stage == "schema" else [schema(), listing(), bad]
    client, calls = client_for(replies)
    async with client:
        result = await adapter(client)(payload(), claim())
    expected = (
        "not_created_retryable"
        if stage == "schema" and failure in {"transport", "500", "503"}
        else "uncertain"
    )
    assert result.status == expected
    assert "synthetic_private_message" not in result.model_dump_json()
    assert TOKEN.get_secret_value() not in result.model_dump_json()
    assert sum(call.url.path == "/v1/pages" for call in calls) == (stage == "create")


@pytest.mark.asyncio
async def test_redirect_does_not_follow_or_forward_credentials():
    client, calls = client_for(
        [
            httpx.Response(
                307, headers={"Location": "https://evil.invalid/collect"}, json={}
            )
        ]
    )
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "uncertain" and len(calls) == 1
    assert calls[0].url.host == "api.notion.com"


@pytest.mark.asyncio
async def test_incoming_cookie_is_never_replayed_even_across_calls():
    first = httpx.Response(
        200,
        json=schema(),
        headers={"Set-Cookie": "session=synthetic; Path=/; Domain=api.notion.com"},
    )
    client, calls = client_for([first, listing(page()), schema(), listing(page())])
    async with client:
        service = adapter(client)
        assert (await service(payload(), claim())).status == "delivered"
        assert client.cookies
        assert (await service(payload(), claim())).status == "delivered"
    assert all("cookie" not in call.headers for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "env",
        "auth",
        "cookie",
        "headers",
        "params",
        "hooks",
        "redirects",
        "proxy",
        "retry",
        "tls",
        "uds",
        "mount",
    ],
)
async def test_client_isolation_rejects_dangerous_configuration_without_http(kind):
    values = {"trust_env": False}
    if kind == "env":
        values["trust_env"] = True
    elif kind == "auth":
        values["auth"] = ("synthetic", "synthetic")
    elif kind == "cookie":
        values["cookies"] = {"session": "synthetic"}
    elif kind == "headers":
        values["headers"] = {"Authorization": "synthetic"}
    elif kind == "params":
        values["params"] = {"secret": "synthetic"}
    elif kind == "hooks":
        values["event_hooks"] = {"request": [lambda request: None]}
    elif kind == "redirects":
        values["follow_redirects"] = True
    elif kind == "proxy":
        values["proxy"] = "http://synthetic.invalid"
    elif kind == "retry":
        values["transport"] = httpx.AsyncHTTPTransport(retries=1)
    elif kind == "tls":
        values["verify"] = False
    elif kind == "uds":
        values["transport"] = httpx.AsyncHTTPTransport(uds="synthetic.sock")
    else:
        values["mounts"] = {
            "https://": httpx.MockTransport(lambda request: httpx.Response(200))
        }
    async with httpx.AsyncClient(**values) as client:
        with pytest.raises(
            module.NotionAdapterError, match="notion_client_not_isolated"
        ):
            adapter(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "hidden",
        "subclass",
        "pins_list",
        "wrong_pin",
        "policy_bool",
        "policy_hidden",
        "secret",
        "secret_crlf",
        "secret_subclass",
    ],
)
async def test_original_contract_preflight_precedes_any_http(kind):
    target = destination()
    changes = {}
    if kind == "hidden":
        target = target.model_copy(update={"secret": "synthetic"})
    elif kind == "subclass":
        dirty = create_model(
            "DirtyTarget", __base__=module.NotionDestination, secret=(str, "synthetic")
        )
        target = dirty(**target.model_dump())
    elif kind == "pins_list":
        target = target.model_copy(update={"properties": list(target.properties)})
    elif kind == "wrong_pin":
        target = target.model_copy(
            update={"properties": (target, *target.properties[1:])}
        )
    elif kind == "policy_bool":
        changes["policy"] = False
    elif kind == "policy_hidden":
        changes["policy"] = module.NotionAdapterPolicy().model_copy(
            update={"hidden": True}
        )
    elif kind == "secret":
        changes["token"] = TOKEN.get_secret_value()
    elif kind == "secret_crlf":
        changes["token"] = SecretStr("synthetic_header\r\nInjection")
    else:

        class DirtySecret(SecretStr):
            pass

        changes["token"] = DirtySecret("synthetic_credential_value")
    client, calls = client_for([])
    async with client:
        with pytest.raises(module.NotionAdapterError):
            adapter(client, destination=target, **changes)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "claimed"),
        ("report_id", "other-report"),
        ("revision", True),
        ("hidden", True),
    ],
)
async def test_dispatch_claim_must_be_exact_and_current_shape(field, value):
    client, calls = client_for([])
    async with client:
        with pytest.raises(ValueError):
            await adapter(client)(payload(), claim().model_copy(update={field: value}))
    assert calls == []


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, entered=None, close_error=False):
        self.chunks, self.entered, self.close_error = chunks, entered, close_error
        self.closed = 0

    async def __aiter__(self):
        if self.entered is not None:
            self.entered.set()
            await asyncio.Event().wait()
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed += 1
        if self.close_error:
            raise RuntimeError("synthetic_close_secret")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "overlong",
        "empty_chunks",
        "duplicate",
        "deep",
        "nonfinite",
        "length",
        "encoding",
        "duplicate_header",
    ],
)
async def test_response_is_bounded_and_closed_before_any_create(kind):
    raw = json.dumps(schema()).encode()
    headers = [("Content-Type", "application/json")]
    if kind == "overlong":
        chunks = [b" " * 65537]
    elif kind == "empty_chunks":
        chunks = [b""] * 1025
    elif kind == "duplicate":
        chunks = [b'{"object":"data_source",' + raw[1:]]
    elif kind == "deep":
        chunks = [b'{"x":' + b"[" * 80 + b"0" + b"]" * 80 + b"}"]
    elif kind == "nonfinite":
        chunks = [b'{"x":NaN}']
    else:
        chunks = [raw]
        if kind == "length":
            headers.append(("Content-Length", str(len(raw) + 1)))
        elif kind == "encoding":
            headers.append(("Content-Encoding", "gzip"))
        else:
            headers.append(("Content-Type", "application/json"))
    stream = Stream(chunks)
    client, calls = client_for([httpx.Response(200, headers=headers, stream=stream)])
    async with client:
        result = await adapter(client)(payload(), claim())
    assert result.status == "uncertain" and len(calls) == 1
    assert stream.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_external_cancellation_survives_response_cleanup(cleanup_error):
    entered = asyncio.Event()
    stream = Stream([], entered=entered, close_error=cleanup_error)
    client, calls = client_for(
        [
            httpx.Response(
                200, headers={"Content-Type": "application/json"}, stream=stream
            )
        ]
    )
    async with client:
        task = asyncio.create_task(adapter(client)(payload(), claim()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert task.cancelled() and stream.closed == 1 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", [1, 3])
@pytest.mark.parametrize("cleanup", ["return", "error"])
async def test_cancel_swallowed_by_send_transport_never_starts_another_request(
    cancel_at, cleanup
):
    entered, calls = asyncio.Event(), []
    replies = [schema(), listing(), page(), listing(page())]

    async def handler(request):
        calls.append(request)
        if len(calls) == cancel_at:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if cleanup == "error":
                    raise RuntimeError("synthetic_cleanup_secret") from None
        return httpx.Response(200, json=replies[len(calls) - 1])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        task = asyncio.create_task(adapter(client)(payload(), claim()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert task.cancelled() and len(calls) == cancel_at


@pytest.mark.asyncio
async def test_real_outbox_dispatch_wires_adapter_after_durable_marker(memory):
    clock = Clock()
    value = payload()
    queued = outbox.enqueue(memory.root, value, clock=clock)
    calls = []
    created_page = None

    def handler(request):
        nonlocal created_page
        calls.append(request)
        assert memory.active_contexts == 0
        view = outbox.read_job(memory.root, REPORT, clock=clock)
        assert view.status == "dispatching"
        assert view.envelope.envelope_sha256 == queued.envelope.envelope_sha256
        if request.method == "GET":
            data = schema()
        elif request.url.path == "/v1/pages":
            created_page = page(value, outbox._claim(view))
            data = created_page
        else:
            data = listing() if created_page is None else listing(created_page)
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        service = adapter(client, clock=clock)
        result = await outbox.dispatch_once(
            memory.root, REPORT, worker_id="worker", adapter=service, clock=clock
        )
        assert result.status == "delivered" and result.execution_authority is False
        with pytest.raises(outbox.OutboxError):
            await outbox.dispatch_once(
                memory.root, REPORT, worker_id="worker", adapter=service, clock=clock
            )
    assert len(calls) == 4
    assert TOKEN.get_secret_value() not in b"".join(memory.files.values()).decode()


@pytest.mark.asyncio
async def test_bounded_worker_invokes_real_notion_adapter_with_memory_storage(memory):
    clock = Clock()
    value = payload()
    outbox.enqueue(memory.root, value, clock=clock)
    calls = []

    def handler(request):
        calls.append(request)
        assert memory.active_contexts == 0
        view = outbox.read_job(memory.root, REPORT, clock=clock)
        data = (
            schema()
            if request.method == "GET"
            else listing(page(value, outbox._claim(view)))
        )
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        result = await run_outbox_pass(
            memory.root,
            report_ids=(REPORT,),
            worker_id="worker",
            adapter=adapter(client, clock=clock),
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
    assert result.status == "completed"
    assert (
        result.items[0].outbox_status == "delivered" and result.items[0].attempts == 1
    )
    assert result.execution_authority is False and len(calls) == 2


@pytest.mark.skipif(
    os.name == "nt",
    reason="Real POSIX storage proof only; Windows ancestor-pin denial is not bypassed",
)
@pytest.mark.asyncio
async def test_native_posix_worker_adapter_and_durable_outbox_chain(tmp_path):
    root = tmp_path / "trusted-notion-outbox"
    root.mkdir()
    clock, value = Clock(), payload()
    outbox.enqueue(root, value, clock=clock)
    created_page, calls = None, []

    def handler(request):
        nonlocal created_page
        calls.append(request)
        view = outbox.read_job(root, REPORT, clock=clock)
        assert view.status == "dispatching"
        assert (root / f"{REPORT}.state" / "00000003.json").is_file()
        if request.method == "GET":
            data = schema()
        elif request.url.path == "/v1/pages":
            created_page = page(value, outbox._claim(view))
            data = created_page
        else:
            data = listing() if created_page is None else listing(created_page)
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        service = adapter(client, clock=clock)
        result = await run_outbox_pass(
            root,
            report_ids=(REPORT,),
            worker_id="worker",
            adapter=service,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
        second = await run_outbox_pass(
            root,
            report_ids=(REPORT,),
            worker_id="worker",
            adapter=service,
            policy=OutboxWorkerPolicy(),
            clock=clock,
        )
    assert result.items[0].outbox_status == "delivered"
    assert second.items[0].status == "skipped" and len(calls) == 4
    assert outbox.read_job(root, REPORT, clock=clock).status == "delivered"
    assert (
        TOKEN.get_secret_value()
        not in b"".join(path.read_bytes() for path in root.rglob("*.json")).decode()
    )


@pytest.mark.parametrize(
    "number", [b"1e401", b"1e-401", b"9" * 97, b"Infinity", b"NaN"]
)
def test_unrelated_number_still_has_representation_and_finiteness_bounds(number):
    with pytest.raises(module.NotionAdapterError):
        module._bounded_json(b'{"unrelated_number":' + number + b"}")


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [-1, 60])
async def test_observation_clock_reversal_or_expiry_never_starts_create(seconds):
    clock = Clock()

    def response(request):
        clock.now += timedelta(seconds=seconds)
        return httpx.Response(200, json=schema())

    client, calls = client_for([response])
    async with client:
        service = adapter(client, clock=clock)
        if seconds < 0:
            with pytest.raises(
                module.NotionAdapterError, match="notion_clock_reversed"
            ):
                await service(payload(), claim())
        else:
            assert (await service(payload(), claim())).status == "uncertain"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_first_request_clock_cannot_precede_capture_start_or_submission():
    times = iter([NOW, NOW - timedelta(seconds=5), NOW])
    client, calls = client_for([schema(), listing(page())])
    async with client:
        result = await adapter(client, clock=lambda: next(times))(payload(), claim())
    assert result.status == "uncertain" and calls == []
