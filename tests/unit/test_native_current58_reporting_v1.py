"""Current58 report worker DATA mechanics; no real SQL, Notion or order."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main
from app.core import native_manual_demo_lifespan_0056_v1 as manual
from app.trade_evidence import native_current58_reporting_v1 as current
from app.trade_evidence import native_notion_delivery_source_v55 as notion
from app.trade_evidence.submission_runtime import SubmissionOutboxRuntime
from app.trade_qualification import native_continuation_guard_v1 as guard
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1
from tests.unit.test_native_manual_demo_http_0056_v1 import app
from tests.unit.test_native_manual_demo_lifespan_0056_v1 import settings


def test_current58_guard_and_legacy_notion_guard_compile_without_source_promotion():
    current._NATIVE_GUARD()
    notion._NATIVE_GUARD()


@pytest.mark.parametrize("fault", ["writer", "report_catalog", "forensics"])
def test_actual_guard_rejects_replaced_catalog_or_numeric_reader(monkeypatch, fault):
    if fault == "writer":
        module, name = (
            current.reporting.writer56,
            "observe_restricted_readwrite_writer_0056_v1",
        )
    elif fault == "report_catalog":
        module, name = (
            current.reporting.writer56.inherited.protective58.report_catalog,
            "observe_catalog",
        )
    else:
        module, name = (
            current.protective_data.mapper,
            "reconstruct_native_protective_forensics_v58",
        )
    monkeypatch.setattr(module, name, lambda *args, **kwargs: True)
    with pytest.raises(guard.NativeSourceClosureError):
        current._NATIVE_GUARD()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_foreign_data_cleanup_attempts_all_owned_engines_and_inhibits_partial_lease(
    cancelled,
):
    # Foreign DATA primitive only: no live cap, Settings, database or callbacks
    # accepted by the product source is manufactured here.
    cap = object()
    engines = [SimpleNamespace(dispose=AsyncMock()) for _ in range(3)]
    engines[0].dispose.side_effect = (
        asyncio.CancelledError if cancelled else ValueError("synthetic dispose failure")
    )
    value = {"phase": "READY", "engines": tuple(engines)}
    registry, unknown = {cap: value}, {}
    primitive = foreign_primitive_v1(
        current.close_current58_reporting_v1,
        {"_record": lambda _: value, "_LEASES": registry, "_UNKNOWN": unknown},
    )
    with pytest.raises(asyncio.CancelledError if cancelled else ValueError):
        await primitive(cap)
    assert value["phase"] == "CLOSING" and registry[cap] is value
    assert unknown[id(cap)] is value
    for engine in engines:
        engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_current58_cap_cannot_be_constructed_or_replaced_by_flag_or_dto():
    with pytest.raises(ValueError):
        current.OwnedCurrent58ReportingV1()
    forged = object.__new__(current.OwnedCurrent58ReportingV1)
    with pytest.raises(ValueError):
        copy.copy(forged)
    with pytest.raises(ValueError):
        current.current58_reporting_context_v1(forged, key=current._CONTEXT_KEY_V1)
    with pytest.raises(notion.NativeNotionSourceUnavailable):
        notion._replay_current58_reporting_lease_v1(
            object(), key=notion._CURRENT58_KEY_V1
        )


@pytest.mark.parametrize(
    "change",
    [
        {"submission_outbox_enabled": None},
        {"notion_outbox_enabled": 1},
        {"notion_outbox_enabled": True, "submission_outbox_enabled": False},
    ],
)
def test_manual_reporting_switches_remain_exact_bool_and_require_local_spool_worker(
    change,
):
    with pytest.raises(manual.NativeManualDemoStartup0056Error):
        manual.require_manual_demo_settings_0056_v1(settings(**change))


def test_explicit_reporting_switches_do_not_change_order_write_flags():
    value = settings(submission_outbox_enabled=True, notion_outbox_enabled=True)
    manual.require_manual_demo_settings_0056_v1(value)
    assert value.okx_demo_allow_order_writes is value.okx_demo_auto_execution is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["saved", "audit", "failure", "injected"])
async def test_current58_worker_uses_native_projection_and_retains_pending_on_failure(
    monkeypatch, tmp_path, mode
):
    # Explicit synthetic dispatch seam: mocked functions return DATA only and
    # do not create an OwnedCurrent58ReportingV1 or any Notion capability.
    config = SimpleNamespace(
        submission_outbox_enabled=True,
        submission_outbox_root=str(tmp_path),
        ctcc_native_demo_manual_lifespan_0056_v1=True,
        submission_outbox_batch_size=2,
    )
    monkeypatch.setattr("app.config.settings.get_settings", lambda: config)
    cap = object()
    acquired = AsyncMock(return_value=cap)
    closed = AsyncMock()
    calls = []

    async def ids(actual, *, namespace, after, limit, projected):
        assert actual is cap and namespace == "synthetic" and limit == 2
        calls.append((projected, after))
        return ("a" * 64,) if projected == (mode == "audit") else ()

    async def project(actual, *, spool_id, root, namespace):
        assert actual is cap and spool_id == "a" * 64 and root == tmp_path
        if mode == "failure":
            raise ValueError("synthetic filesystem sharing failure")

    monkeypatch.setattr(current, "acquire_current58_reporting_v1", acquired)
    monkeypatch.setattr(current, "close_current58_reporting_v1", closed)
    monkeypatch.setattr(current, "current58_reporting_ids_v1", ids)
    monkeypatch.setattr(current, "project_current58_report_v1", project)
    monkeypatch.setattr(
        current.reporting,
        "_configured_projection_queue",
        lambda _: (tmp_path, "synthetic"),
    )
    worker = SubmissionOutboxRuntime(
        config, session_factory=object() if mode == "injected" else None
    )
    await worker.tick()
    if mode == "injected":
        acquired.assert_not_awaited()
        assert worker.last_code == "submission_outbox_pass_pending"
    elif mode == "audit":
        assert calls == [(True, "")]
        assert worker.last_code == "submission_outbox_projection_audit_in_progress"
        assert worker._audit_after == "a" * 64
    elif mode == "failure":
        closed.assert_awaited_once_with(cap)
        assert worker._current58_lease is None
        assert worker._after == "a" * 64
        assert worker.last_code == "submission_outbox_projection_pending"
    else:
        assert calls == [(True, ""), (False, "")]
        assert worker.last_code == "submission_outbox_pass_complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "startup", "old_head", "worker"])
async def test_actual_manual_lifespan_starts_workers_only_after_verified58_and_cleans_partial_start(
    monkeypatch, fault
):
    instance = app(monkeypatch, ready=False)
    main.settings.submission_outbox_enabled = main.settings.notion_outbox_enabled = True
    events = []

    async def startup(*args):
        events.append("startup")
        if fault == "startup":
            raise ValueError("synthetic startup refusal")
        return "0057" if fault == "old_head" else "0058"

    class Worker:
        def __init__(self, name):
            self.name = name

        async def start(self):
            assert instance.state.native_manual_demo_recovery_confirmed is True
            events.append(self.name + "_start")
            if fault == "worker" and self.name == "notion":
                raise ValueError("synthetic partial worker start")

        async def stop(self):
            assert instance.state.native_manual_demo_recovery_confirmed is False
            events.append(self.name + "_stop")

    async def stop():
        events.append("automation_stop")

    async def dispose():
        events.append("dispose")

    monkeypatch.setattr(manual, "start_manual_demo_0056_v1", startup)
    monkeypatch.setattr(main, "submission_outbox_runtime", Worker("submission"))
    monkeypatch.setattr(main, "notion_outbox_runtime", Worker("notion"))
    monkeypatch.setattr(main.safe_demo_automation, "stop", stop)
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=dispose))

    async def run():
        async with main.lifespan(instance):
            events.append("yield")

    if fault is None:
        await run()
        assert events == [
            "startup",
            "submission_start",
            "notion_start",
            "yield",
            "notion_stop",
            "submission_stop",
            "automation_stop",
            "dispose",
        ]
    else:
        with pytest.raises((ValueError, RuntimeError)):
            await run()
        assert "yield" not in events
        if fault == "startup":
            assert events == ["startup", "dispose"]
        elif fault == "old_head":
            assert events == ["startup", "automation_stop", "dispose"]
        else:
            assert events == [
                "startup",
                "submission_start",
                "notion_start",
                "notion_stop",
                "submission_stop",
                "automation_stop",
                "dispose",
            ]
    assert instance.state.native_manual_demo_recovery_confirmed is False
