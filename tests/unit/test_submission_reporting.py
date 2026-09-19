"""Capture consistency and isolated reporter lifecycle, without exchange IO."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.trade_evidence.submission_reporting import (
    CapturedSubmission,
    SubmissionReportingError,
    checked_capture,
    classify,
    decode_capture,
    freeze_capture,
    sha,
)
from app.trade_evidence.submission_runtime import SubmissionOutboxRuntime

NOW = datetime(2026, 9, 19, tzinfo=UTC)
CLIENT = "CTQ" + "1" * 29


def capture(body=None, **changes):
    raw = (
        {"code": "0", "data": [{"sCode": "0", "ordId": "1234", "clOrdId": CLIENT}]}
        if body is None
        else body
    )
    values = {
        "exchange_request_sha256": "a" * 64,
        "request_started_at": NOW,
        "headers_received_at": NOW,
        "body_completed_at": NOW,
        "completed_at": NOW,
        "http_status": 200,
        "raw_body": json.dumps(raw).encode(),
        "transport_complete": True,
    }
    values.update(changes)
    return CapturedSubmission(**values)


def test_raw_capture_roundtrip_preserves_exact_noncanonical_bytes():
    value = capture(raw_body=b'  {"data": [], "code":"0"} \n')
    raw = freeze_capture(value)
    restored = decode_capture(raw, sha(raw.encode()))
    assert restored == value
    assert restored.raw_body == value.raw_body
    assert "raw_body_sha256" in json.loads(raw)
    with pytest.raises(SubmissionReportingError):
        decode_capture(raw + " ", sha((raw + " ").encode()))


def test_foreign_timezone_and_bytes_subclass_callbacks_are_never_invoked():
    from datetime import tzinfo

    calls = []

    class ForeignZone(tzinfo):
        def utcoffset(self, value):
            calls.append("timezone")
            return timedelta(0)

    class ForeignBody(bytes):
        def hex(self, *args, **kwargs):
            calls.append("bytes")
            return super().hex(*args, **kwargs)

    for changes in (
        {"request_started_at": NOW.replace(tzinfo=ForeignZone())},
        {"raw_body": ForeignBody(b"{}")},
    ):
        with pytest.raises(SubmissionReportingError):
            freeze_capture(capture().model_copy(update=changes))
    assert not calls


@pytest.mark.parametrize(
    "change",
    (
        {"http_status": True},
        {"transport_complete": 1},
        {"raw_body": "not-bytes"},
        {"raw_body": b"x" * 65537},
        {"headers_received_at": NOW - timedelta(seconds=1)},
        {"body_completed_at": NOW + timedelta(seconds=1)},
        {"request_started_at": NOW.replace(tzinfo=None)},
        {"headers_received_at": None},
    ),
)
def test_capture_rejects_dirty_models_and_bad_causality(change):
    with pytest.raises(SubmissionReportingError):
        checked_capture(capture().model_copy(update=change))


@pytest.mark.parametrize(
    "body",
    (
        {"code": "0", "data": []},
        {
            "code": "0",
            "data": [{"sCode": "0", "ordId": "1234", "clOrdId": "different"}],
        },
        {"code": "1", "data": [{"sCode": "0", "ordId": "1234", "clOrdId": CLIENT}]},
        {"code": "0", "data": [{"sCode": "0", "ordId": "", "clOrdId": CLIENT}]},
        {"code": "0", "data": [{"sCode": 0, "ordId": "1234", "clOrdId": CLIENT}]},
        {"code": "0", "data": ["invalid"]},
    ),
)
def test_ambiguous_ack_never_becomes_confirmed(body):
    assert classify(capture(body), CLIENT, NOW + timedelta(seconds=1))[0] == "uncertain"


def test_ack_rejection_and_unknown_have_distinct_status():
    assert classify(capture(), CLIENT, NOW + timedelta(seconds=1))[:3] == (
        "acknowledged",
        "1234",
        "0",
    )
    rejected = capture(
        {"code": "1", "data": [{"sCode": "51000", "ordId": "", "clOrdId": CLIENT}]}
    )
    assert classify(rejected, CLIENT, NOW + timedelta(seconds=1))[:3] == (
        "rejected",
        None,
        "51000",
    )
    for value in (
        capture(http_status=500),
        capture(transport_complete=False),
        capture(raw_body=b'{"code":"0","code":"0","data":[]}'),
    ):
        assert classify(value, CLIENT, NOW + timedelta(seconds=1))[0] == "uncertain"
    assert classify(capture(), CLIENT, NOW)[0] == "uncertain"


@pytest.mark.asyncio
async def test_missing_external_configuration_does_not_abort_startup():
    runtime = SubmissionOutboxRuntime(
        Settings(_env_file=None, environment="test", submission_outbox_enabled=True)
    )
    await runtime.tick()
    assert runtime.status == "pending" and runtime.last_code.endswith(
        "configuration_missing"
    )
    await runtime.stop()


@pytest.mark.asyncio
async def test_one_pending_spool_failure_does_not_block_other_reports(
    tmp_path, monkeypatch, caplog
):
    import app.trade_evidence.submission_runtime as module

    called = []

    class Repository:
        def __init__(self, *args, **kwargs):
            pass

        async def pending_ids(self, *args, **kwargs):
            return ("a" * 64, "b" * 64)

        async def project_one(self, spool_id, **kwargs):
            called.append(spool_id)
            if len(called) == 1:
                raise RuntimeError("synthetic-private-error-must-not-be-logged")

    monkeypatch.setattr(module, "SubmissionReportingRepository", Repository)
    values = Settings(
        _env_file=None,
        environment="test",
        submission_outbox_enabled=True,
        submission_outbox_root=str(tmp_path),
    )
    assert not values.notion_outbox_token_file
    runtime = SubmissionOutboxRuntime(values, session_factory=object())
    await runtime.tick()
    assert len(called) == 2 and runtime.status == "pending"
    assert "synthetic-private-error" not in caplog.text


@pytest.mark.asyncio
async def test_worker_pool_created_and_disposed_on_own_thread(tmp_path, monkeypatch):
    import app.trade_evidence.submission_runtime as module

    ids = []
    processed = threading.Event()

    class Engine:
        async def dispose(self):
            ids.append(("dispose", threading.get_ident()))

    def engine(*args, **kwargs):
        ids.append(("create", threading.get_ident()))
        return Engine()

    class Repository:
        def __init__(self, *args, **kwargs):
            pass

        async def pending_ids(self, *args, **kwargs):
            processed.set()
            return ()

    monkeypatch.setattr(module, "create_async_engine", engine)
    monkeypatch.setattr(module, "async_sessionmaker", lambda *a, **k: object())
    monkeypatch.setattr(module, "SubmissionReportingRepository", Repository)
    settings = Settings(
        _env_file=None,
        environment="test",
        submission_outbox_enabled=True,
        submission_outbox_root=str(tmp_path),
        submission_outbox_poll_seconds=1,
    )
    runtime = SubmissionOutboxRuntime(settings)
    await runtime.start()
    assert await asyncio.to_thread(processed.wait, 5)
    first = runtime._thread
    await runtime.start()
    assert runtime._thread is first
    await runtime.stop()
    assert runtime.status == "stopped" and runtime._sessions is None
    assert ids[0][0] == "create" and ids[-1][0] == "dispose"
    assert ids[0][1] == ids[-1][1] != threading.get_ident()


def test_reporter_has_no_order_transport_arm_or_callback_imports():
    import ast

    root = Path(__file__).parents[2]
    for name in (
        "app/trade_evidence/submission_runtime.py",
        "app/database/repositories/submission_reporting.py",
        "app/trade_evidence/submission_reporting.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        modules = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        assert all(
            not any(
                part in module
                for part in (
                    "okx_demo.service",
                    "okx_live",
                    "execution_authority",
                    "exchange.client",
                    "demo_automation",
                )
            )
            for module in modules
        )
