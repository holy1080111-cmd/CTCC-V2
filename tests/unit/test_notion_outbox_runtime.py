"""Native durable filesystem + synthetic HTTP/lifecycle checks, no real Notion IO."""

import asyncio
import hashlib
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config.settings import Settings
from app.trade_evidence import notion_binding as binding
from app.trade_evidence import outbox, storage
from app.trade_evidence.runtime import NotionOutboxRuntime, discover_jobs
from scripts.setup_notion_outbox import discover_destination
from tests.unit.test_trade_evidence_notion_adapter import (
    DB,
    SOURCE,
    TOKEN,
    client_for,
    destination,
    listing,
    page,
    schema,
)
from tests.unit.test_trade_evidence_outbox import REPORT, Clock, payload


def database():
    return {
        "object": "database",
        "id": DB,
        "in_trash": False,
        "data_sources": [{"id": SOURCE, "name": "Synthetic schema"}],
    }


def settings(**values):
    return Settings(_env_file=None, environment="test", **values)


def service(root, handler, clock, *, batch=16):
    worker = NotionOutboxRuntime(
        settings(notion_outbox_enabled=True, notion_outbox_batch_size=batch),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
        clock=clock,
    )
    worker._configuration = lambda: (root, destination(), TOKEN)
    return worker


def recorder(root, clock):
    calls, created = [], {}

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            data = database() if "/databases/" in request.url.path else schema()
        else:
            # The runtime must finish its durable dispatch marker before any POST.
            report = next(
                name
                for name in discover_jobs(root)
                if outbox.read_job(root, name, clock=clock).status == "dispatching"
            )
            view = outbox.read_job(root, report, clock=clock)
            if request.url.path == "/v1/pages":
                assert report not in created
                created[report] = page(view.envelope.payload, outbox._claim(view))
                data = created[report]
            else:
                data = listing(created[report]) if report in created else listing()
        return httpx.Response(200, json=data)

    return handler, calls, created


@pytest.mark.asyncio
async def test_default_off_never_reads_files_or_starts_http_or_thread():
    def forbidden():
        pytest.fail("disabled reporting must perform no IO")

    worker = NotionOutboxRuntime(settings(), client_factory=forbidden)
    worker._configuration = forbidden
    await worker.start()
    await worker.tick()
    assert worker._thread is None and worker.status == "disabled"


@pytest.mark.asyncio
async def test_enabled_missing_configuration_is_pending_with_bounded_retry(caplog):
    worker = NotionOutboxRuntime(settings(notion_outbox_enabled=True))
    assert await worker.tick() == 30
    assert worker.last_code == "notion_outbox_configuration_missing"
    for _ in range(10):
        delay = await worker.tick()
    assert delay == 300 and worker.status == "pending"
    assert TOKEN.get_secret_value() not in caplog.text


@pytest.mark.asyncio
async def test_thread_starts_without_blocking_api_and_stops_idempotently():
    entered, release = threading.Event(), threading.Event()
    worker = NotionOutboxRuntime(settings(notion_outbox_enabled=True))

    def blocked_configuration():
        entered.set()
        release.wait(2)
        raise binding.NotionBindingError("notion_outbox_configuration_missing")

    worker._configuration = blocked_configuration
    try:
        await asyncio.wait_for(worker.start(), 0.2)
        assert await asyncio.to_thread(entered.wait, 2)
        original = worker._thread
        await worker.start()
        assert worker._thread is original
        # This heartbeat uses the API loop while worker filesystem work is blocked.
        await asyncio.wait_for(asyncio.sleep(0), 0.2)
    finally:
        release.set()
        await worker.stop()
        await worker.stop()
    assert worker._thread is None and worker.status == "stopped"


@pytest.mark.asyncio
async def test_native_delivery_readback_and_restart_never_recreate_page(tmp_path):
    clock = Clock()
    outbox.enqueue(tmp_path, payload(), clock=clock)
    handler, calls, created = recorder(tmp_path, clock)
    worker = service(tmp_path, handler, clock)
    await worker.tick()
    assert outbox.read_job(tmp_path, REPORT, clock=clock).status == "delivered"
    assert worker.last_result.execution_authority is False
    assert worker.status == "running" and len(created) == 1
    journal = {
        path.name: path.read_bytes()
        for path in (tmp_path / f"{REPORT}.state").iterdir()
    }
    await service(tmp_path, handler, clock).tick()
    assert sum(request.url.path == "/v1/pages" for request in calls) == 1
    assert journal == {
        path.name: path.read_bytes()
        for path in (tmp_path / f"{REPORT}.state").iterdir()
    }
    assert TOKEN.get_secret_value().encode() not in b"".join(journal.values())


@pytest.mark.asyncio
async def test_native_crashed_dispatch_recovers_uncertain_without_post(tmp_path):
    clock = Clock()
    outbox.enqueue(tmp_path, payload(), clock=clock)
    claim = outbox.claim_job(tmp_path, REPORT, worker_id="before-crash", clock=clock)
    outbox.begin_dispatch(tmp_path, claim, clock=clock)
    clock.advance(61)
    handler, calls, _ = recorder(tmp_path, clock)
    worker = service(tmp_path, handler, clock)
    await worker.tick()
    assert outbox.read_job(tmp_path, REPORT, clock=clock).status == "uncertain"
    assert worker.status == "paused"
    assert calls == []


@pytest.mark.asyncio
async def test_native_precreate_failure_retry_is_due_then_delivered(tmp_path):
    clock = Clock()
    outbox.enqueue(tmp_path, payload(), clock=clock)
    successful, calls, created = recorder(tmp_path, clock)
    first_schema = True

    def handler(request):
        nonlocal first_schema
        if request.url.path.endswith("/query") and first_schema:
            first_schema = False
            return httpx.Response(503, json={"object": "error"})
        return successful(request)

    worker = service(tmp_path, handler, clock)
    await worker.tick()
    assert outbox.read_job(tmp_path, REPORT, clock=clock).status == "retry_wait"
    assert not created
    await worker.tick()
    assert worker.last_result.items[0].code == "outbox_retry_not_due"
    clock.advance(6)
    await worker.tick()
    assert outbox.read_job(tmp_path, REPORT, clock=clock).status == "delivered"
    assert sum(request.url.path == "/v1/pages" for request in calls) == 1


@pytest.mark.asyncio
async def test_cancel_dedicated_thread_during_create_retains_uncertainty(tmp_path):
    clock, entered = Clock(), threading.Event()
    outbox.enqueue(tmp_path, payload(), clock=clock)
    normal, calls, _ = recorder(tmp_path, clock)

    async def handler(request):
        if request.url.path == "/v1/pages":
            entered.set()
            await asyncio.Future()
        return normal(request)

    worker = service(tmp_path, handler, clock)
    await worker.start()
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert outbox.read_job(tmp_path, REPORT, clock=clock).status == "dispatching"
    finally:
        await worker.stop()
    assert outbox.read_job(tmp_path, REPORT, clock=clock).status == "uncertain"
    count = len(calls)
    await service(tmp_path, normal, clock).tick()
    assert calls[count:] == []


@pytest.mark.asyncio
async def test_fair_batch_does_not_starve_later_jobs_behind_terminal(tmp_path):
    clock = Clock()
    for report in ("a-first", "b-second", "c-third"):
        outbox.enqueue(tmp_path, payload(report_id=report), clock=clock)
    handler, _, created = recorder(tmp_path, clock)
    worker = service(tmp_path, handler, clock, batch=1)
    for _ in range(3):
        await worker.tick()
    assert set(created) == {"a-first", "b-second", "c-third"}


@pytest.mark.asyncio
async def test_orphan_journal_is_preserved_and_not_delivered(tmp_path):
    journal = tmp_path / f"{REPORT}.state"
    journal.mkdir()
    original = journal / "00000001.json"
    original.write_bytes(b"synthetic incomplete publication")
    handler, calls, _ = recorder(tmp_path, Clock())
    await service(tmp_path, handler, Clock()).tick()
    assert original.read_bytes() == b"synthetic incomplete publication"
    assert [request.method for request in calls] == ["GET", "GET"]


@pytest.mark.asyncio
async def test_schema_pin_failure_never_claims_queued_report(tmp_path):
    clock = Clock()
    outbox.enqueue(tmp_path, payload(), clock=clock)
    invalid = schema()
    invalid["properties"]["Report"]["id"] = "changed-id"
    client, calls = client_for([database(), invalid])
    worker = service(tmp_path, lambda _: None, clock)
    worker._client_factory = lambda: client
    await worker.tick()
    assert worker.status == "pending"
    view = outbox.read_job(tmp_path, REPORT, clock=clock)
    assert view.status == "queued" and view.head.revision == 1
    assert [request.method for request in calls] == ["GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retry,expected", [("120", 120), ("0", 30), ("", None), ("bad", None)]
)
async def test_rate_limit_is_respected_or_paused_without_claim(
    tmp_path, retry, expected
):
    client, calls = client_for([httpx.Response(429, headers={"Retry-After": retry})])
    worker = service(tmp_path, lambda _: None, Clock())
    worker._client_factory = lambda: client
    assert await worker.tick() == expected
    assert worker.status == ("paused" if expected is None else "pending")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_delivery_429_stops_all_later_jobs_and_survives_restart(tmp_path):
    clock = Clock()
    for report in ("a-first", "b-second"):
        outbox.enqueue(tmp_path, payload(report_id=report), clock=clock)
    normal, calls, _ = recorder(tmp_path, clock)
    limited = False

    def handler(request):
        nonlocal limited
        assert not limited, "no request is allowed after first rate limit"
        if request.url.path == "/v1/pages":
            limited = True
            return httpx.Response(429, headers={"Retry-After": "120"})
        return normal(request)

    worker = service(tmp_path, handler, clock)
    assert await worker.tick() is None
    assert worker.status == "paused" and limited
    assert outbox.read_job(tmp_path, "a-first", clock=clock).status == "uncertain"
    assert outbox.read_job(tmp_path, "b-second", clock=clock).status == "queued"
    count = len(calls)
    restarted = service(tmp_path, handler, clock)
    assert await restarted.tick() == 120
    clock.advance(121)
    assert await restarted.tick() is None  # Cooldown expiry does not resolve ambiguity.
    assert len(calls) == count


@pytest.mark.asyncio
async def test_prebinding_cooldown_persists_and_only_retries_after_deadline(tmp_path):
    clock = Clock()
    client, calls = client_for([httpx.Response(429, headers={"Retry-After": "120"})])
    first = service(tmp_path, lambda _: None, clock)
    first._client_factory = lambda: client
    assert await first.tick() == 120
    normal, later_calls, _ = recorder(tmp_path, clock)
    second = service(tmp_path, normal, clock)
    assert await second.tick() == 120
    assert not later_calls
    clock.advance(119)
    assert await second.tick() == 1 and not later_calls
    clock.advance(1)
    await second.tick()
    assert len(later_calls) == 2 and len(calls) == 1


@pytest.mark.asyncio
async def test_reporting_lease_prevents_parallel_runtime_http(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    clock = Clock()
    normal, calls, _ = recorder(tmp_path, clock)

    async def handler(request):
        entered.set()
        await release.wait()
        return normal(request)

    first, second = service(tmp_path, handler, clock), service(tmp_path, normal, clock)
    task = asyncio.create_task(first.tick())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await second.tick()
        assert second.status == "pending" and calls == []
    finally:
        release.set()
        await task
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_static_error_allowlist_never_logs_injected_exception_code(caplog):
    worker = NotionOutboxRuntime(settings(notion_outbox_enabled=True))

    def invalid():
        raise binding.NotionBindingError(TOKEN.get_secret_value())

    worker._configuration = invalid
    await worker.tick()
    assert TOKEN.get_secret_value() not in caplog.text
    assert worker.last_code == "notion_outbox_configuration_or_delivery_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(302, headers={"Location": "https://example.invalid"}),
        lambda: httpx.Response(
            200, content=b"{}", headers={"Content-Type": "text/plain"}
        ),
        lambda: httpx.Response(
            200,
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "5"},
        ),
        lambda: httpx.Response(
            200,
            content=b"{}",
            headers=[
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Content-Length", "2"),
            ],
        ),
        lambda: httpx.Response(
            200, content=b'{"a":1,"a":2}', headers={"Content-Type": "application/json"}
        ),
        lambda: httpx.Response(
            200, content=b" " * 131073, headers={"Content-Type": "application/json"}
        ),
    ],
)
async def test_binding_rejects_invalid_wire_response_without_redirect_or_retry(
    response,
):
    client, calls = client_for([response()])
    async with client:
        with pytest.raises(ValueError):
            await binding.read_notion_resource(
                client, TOKEN, kind="databases", identity=DB
            )
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.asyncio
async def test_readback_proves_database_relation_and_exact_four_properties():
    client, calls = client_for([database(), schema()])
    async with client:
        receipt = await binding.verify_binding(client, TOKEN, destination())
    assert all(len(value) == 64 for value in receipt.values())
    assert [request.method for request in calls] == ["GET", "GET"]


@pytest.mark.asyncio
async def test_setup_reads_actual_ids_then_independent_schema_readback():
    client, calls = client_for([database(), schema(), database(), schema()])
    choices = iter(["1", "1", "1", "2", "3"])
    async with client:
        target, _ = await discover_destination(
            client, TOKEN, DB, input_fn=lambda _: next(choices)
        )
    assert target == destination()
    assert [request.method for request in calls] == ["GET"] * 4


@pytest.mark.asyncio
async def test_known_nonsecret_binding_needs_no_user_choices_and_uses_actual_ids():
    remote = schema()
    remote["properties"]["Envelope"]["id"] = "new%3Aactual"
    client, calls = client_for([database(), remote, database(), remote])

    def no_input(_):
        pytest.fail("prepared known binding must require no nonsecret user input")

    async with client:
        target, _ = await discover_destination(
            client,
            TOKEN,
            DB,
            input_fn=no_input,
            source_id=SOURCE,
            property_names={pin.role: pin.name for pin in destination().properties},
        )
    assert target.properties[1].property_id == "new%3Aactual"
    assert [request.method for request in calls] == ["GET"] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "name", "type", "readback"])
async def test_known_binding_mismatch_fails_without_modifying_schema(change):
    target_schema, second_schema = schema(), schema()
    names = {pin.role: pin.name for pin in destination().properties}
    if change == "name":
        names["report_id"] = "Renamed"
    if change == "type":
        target_schema["properties"]["Report"]["type"] = "rich_text"
    if change == "readback":
        second_schema["properties"]["Report"]["id"] = "changed-during-setup"
    client, calls = client_for([database(), target_schema, database(), second_schema])
    async with client:
        with pytest.raises(ValueError):
            await discover_destination(
                client,
                TOKEN,
                DB,
                source_id="9" * 32 if change == "source" else SOURCE,
                property_names=names,
            )
    assert all(request.method == "GET" for request in calls)


def test_native_private_token_permissions_pin_and_no_overwrite(tmp_path):
    token_file = tmp_path / "synthetic.token"
    binding.save_private_token(token_file, TOKEN)
    assert binding.load_private_token(token_file) == TOKEN
    with pytest.raises(FileExistsError):
        binding.save_private_token(token_file, TOKEN)
    destination_file = tmp_path / "destination.json"
    raw = destination().model_dump_json().encode()
    destination_file.write_bytes(raw)
    assert (
        binding.load_destination(destination_file, hashlib.sha256(raw).hexdigest())
        == destination()
    )
    queue = tmp_path / "queue"
    queue.mkdir()
    worker = NotionOutboxRuntime(
        settings(
            notion_outbox_enabled=True,
            notion_outbox_root=str(queue),
            notion_outbox_token_file=str(token_file),
            notion_outbox_destination_file=str(destination_file),
            notion_outbox_destination_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert worker._configuration() == (queue, destination(), TOKEN)
    destination_file.write_bytes(raw + b" ")
    with pytest.raises(binding.NotionBindingError, match="pin_mismatch"):
        binding.load_destination(destination_file, hashlib.sha256(raw).hexdigest())


def test_token_cannot_be_loaded_from_outbox_directory(tmp_path):
    worker = NotionOutboxRuntime(
        settings(
            notion_outbox_enabled=True,
            notion_outbox_root=str(tmp_path),
            notion_outbox_token_file=str(tmp_path / "private.token"),
            notion_outbox_destination_file=str(tmp_path / "destination.json"),
            notion_outbox_destination_sha256="a" * 64,
        )
    )
    with pytest.raises(binding.NotionBindingError, match="inside_outbox"):
        worker._configuration()


def test_private_token_replacement_between_acl_check_and_read_is_rejected(
    tmp_path, monkeypatch
):
    path, replacement = tmp_path / "owned.token", tmp_path / "replacement.token"
    binding.save_private_token(path, TOKEN)
    replacement.write_bytes(b"synthetic_replacement_token_other")
    real = binding.private_permissions
    replaced = False

    def swap_after_check(target, *, fd):
        nonlocal replaced
        real(target, fd=fd)
        if not replaced:
            replaced = True
            os.replace(replacement, target)

    monkeypatch.setattr(binding, "private_permissions", swap_after_check)
    with pytest.raises(
        (OSError, binding.NotionBindingError, storage.EvidencePublicationError)
    ):
        binding.load_private_token(path)


def test_private_token_save_never_writes_to_replacement_permissive_file(
    tmp_path, monkeypatch
):
    path, replacement = tmp_path / "new.token", tmp_path / "replacement.token"
    other = b"synthetic_replacement_content"
    replacement.write_bytes(other)
    real = binding.private_permissions
    swapped = False

    def swap_after_check(target, *, fd):
        nonlocal swapped
        real(target, fd=fd)
        if not swapped:
            swapped = True
            os.replace(replacement, target)

    monkeypatch.setattr(binding, "private_permissions", swap_after_check)
    with pytest.raises(
        (OSError, binding.NotionBindingError, storage.EvidencePublicationError)
    ):
        binding.save_private_token(path, TOKEN)
    assert path.read_bytes() in {b"", other}


@pytest.mark.parametrize("kind", ["git", "reports", "artifacts", "backups", "source"])
def test_secret_path_excludes_source_git_and_evidence(tmp_path, kind):
    if kind == "source":
        path = Path.cwd() / "private-token"
    elif kind == "git":
        (tmp_path / ".git").mkdir()
        path = tmp_path / "private-token"
    else:
        path = tmp_path / kind / "private-token"
    with pytest.raises(binding.NotionBindingError):
        binding.external_secret_path(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown_error", [False, True])
async def test_app_lifecycle_owns_worker_even_when_core_shutdown_raises(
    monkeypatch, shutdown_error
):
    from app import main

    worker = NotionOutboxRuntime(settings(notion_outbox_enabled=True))
    worker.start = AsyncMock()
    worker.stop = AsyncMock()
    monkeypatch.setattr(main, "notion_outbox_runtime", worker)
    monkeypatch.setattr(main, "settings", settings())
    for owner, method in (
        (main.demo_observability, "shutdown"),
        (main.controlled_live_automation, "stop"),
        (main.okx_live_service, "shutdown"),
        (main.safe_demo_automation, "stop"),
        (main.auto_paper_orchestrator, "stop"),
        (main.realtime_client, "stop"),
    ):
        monkeypatch.setattr(owner, method, AsyncMock())
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=AsyncMock()))
    if shutdown_error:
        main.demo_observability.shutdown.side_effect = RuntimeError(
            "synthetic core shutdown failure"
        )
    with pytest.raises(RuntimeError, match="synthetic"):
        async with main.lifespan(main.app):
            worker.start.assert_awaited_once()
            raise RuntimeError("synthetic application shutdown")
    worker.stop.assert_awaited_once()
