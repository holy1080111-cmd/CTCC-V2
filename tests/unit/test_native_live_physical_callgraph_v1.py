"""Native-paced genuine vendor path to loopback only, never OKX acceptance."""

import asyncio
import json
import re
from collections import Counter

import pytest

from app.domain.native_clock import native_stamp
from tests.unit.native_physical_callgraph_fixture_v1 import (
    CallgraphObservation,
    PhysicalWorkerFixture,
    ephemeral_certificate,
    install_loopback_dns,
    install_native_pacing,
    loopback_server,
    physical,
    q,
)
from tests.unit.test_native_live_lifecycle_custody_v1 import _private_root_path
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture
def original_market():
    """Build the existing synchronous source fixture before the async test loop."""
    return v2_engine_source("long")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profiled", "fresh_instrument"),
    [(False, True), (True, True), (True, False)],
    ids=["native_elapsed", "observed_call_counts", "stale_instrument_refused"],
)
async def test_original_staged_worker_real_vendor_loopback_first_post_budget(
    monkeypatch, tmp_path, record_property, profiled, fresh_instrument, original_market
):
    market = original_market
    tls_root = _private_root_path(tmp_path)
    cert, context = ephemeral_certificate(tls_root)
    observation = CallgraphObservation()
    server, port, complete, local = await loopback_server(context)
    worker = None
    failure = None
    failure_code = None
    handle = None
    try:
        install_native_pacing(monkeypatch)
        worker = PhysicalWorkerFixture(monkeypatch, tmp_path, market)
        worker.preserve_stale_current_instrument = not fresh_instrument
        worker.install_physical(cert, monkeypatch)
        install_loopback_dns(monkeypatch, port)
        physical._require_vendor_state_v1()
        started = native_stamp()
        semantic_started_ns = worker.clock.count * 1_000_000
        if profiled:
            observation.start_faults()
            worker.observer = observation
        try:
            _owner, handle = await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        except Exception as error:  # noqa: BLE001 -- retained below as class only, no private values
            failure = type(error).__name__
            if type(error.args) is tuple and len(error.args) == 1:
                candidate_code = error.args[0]
                if type(candidate_code) is str and re.fullmatch(
                    r"native_[a-z0-9_]{1,120}", candidate_code
                ):
                    failure_code = candidate_code
            handle = getattr(error, "_owned_live_response_v1", None)
        finally:
            observation.close()
        finished = native_stamp()
        if local["accepted_local_tls_connections"]:
            await asyncio.wait_for(complete.wait(), timeout=2)
        record = physical._record(handle) if handle is not None else {}
        first_byte = local["first_post_byte"]
        quote_arrivals = [
            stamp
            for stage, role, stamp in worker.native_arrivals
            if stage == "post_g12" and role in ("/api/v5/market/ticker", "ws")
        ]
        account_arrivals = [
            stamp for name, stamp in worker.native_phases if name == "account_completed"
        ]
        report = {
            "schema": "ctcc.synthetic_real_vendor_entry_callgraph.v1",
            "source_sql_credential_tls_dns_admission_synthetic": True,
            "semantic_clock_paced_by_at_least_actual_native_elapsed": True,
            "original_worker_and_vendor_code_unchanged": True,
            "read_only_local_code_observer_enabled": profiled,
            "guard_count_observer_starts_after_synthetic_committed_readback": True,
            "new_synthetic_instrument_response_used": fresh_instrument,
            "synthetic_stale_spec_latency_fault_seconds": 0 if fresh_instrument else 6,
            "artificial_guard_replay_loop": False,
            "source_venue_order_writes": 0,
            "actual_database_connections": 0,
            "blocked_database_connect_attempts": len(worker.database_connect_attempts),
            "operator_live_arm": False,
            "local_synthetic_http_posts": local["local_synthetic_http_posts"],
            "accepted_local_tls_connections": local["accepted_local_tls_connections"],
            "local_server_failure_class": local.get("server_failure_class"),
            "native_started": started,
            "native_finished": finished,
            "synthetic_semantic_elapsed_ns": worker.clock.count * 1_000_000
            - semantic_started_ns,
            "declared_synthetic_operations": worker.operations,
            "first_local_post_byte": first_byte,
            "first_native_application_ciphertext_enqueue_return": observation.first_application_enqueue,
            "first_local_post_body_complete": local["body_complete"],
            "native_quote_arrivals": quote_arrivals,
            "native_account_completed": account_arrivals[-1]
            if account_arrivals
            else None,
            "guard_call_counts": dict(observation.counter),
            "guard_count_semantics": "CPython local PY_START; coroutine resumes excluded",
            "native_guard_events": [
                {"phase": name, "stamp": stamp} for name, stamp in observation.events
            ],
            "observed_fixed_refusals": observation.refusals[:16],
            "guard_calls_after_first_post_byte": dict(
                Counter(
                    name
                    for name, stamp in observation.events
                    if first_byte is not None
                    and stamp["monotonic_ns"] > first_byte["monotonic_ns"]
                )
            ),
            "guard_calls_after_native_application_enqueue": dict(
                Counter(
                    name
                    for name, stamp in observation.events
                    if observation.first_application_enqueue is not None
                    and stamp["monotonic_ns"]
                    > observation.first_application_enqueue["monotonic_ns"]
                )
            ),
            "risk_hold_resolution_performed": False,
            "failure_class": failure,
            "failure_fixed_code": failure_code,
            "physical_phase": record.get("phase"),
            "physical_attempted": record.get("attempted", False),
            "physical_response_validated": record.get("response_checked", False),
            "durable_inhibit_unconfirmed": record.get(
                "durable_inhibit_unconfirmed", False
            ),
            "local_inhibit_unconfirmed": record.get("local_inhibit_unconfirmed", False),
            "synthetic_sql_disarm_successor_read_back": type(
                record.get("settled_disarm_successor")
            )
            is dict,
            "execution_authority": False,
            "configured_runtime_acceptance": False,
        }
        record_property(
            "native_vendor_loopback_entry_callgraph", json.dumps(report, sort_keys=True)
        )
        if not fresh_instrument:
            assert failure_code == "native_live_post_g12_cancelled", report
            assert local["local_synthetic_http_posts"] == 0, report
            assert local["accepted_local_tls_connections"] == 0, report
            assert worker.database_connect_attempts == [], report
            assert "durable-commit" not in worker.operations, report
            return
        assert failure is None, report
        assert worker.database_connect_attempts == [], report
        assert local["local_synthetic_http_posts"] == 1, report
        assert record.get("response_checked") is True, report
        assert record.get("phase") == "spent", report
        assert record.get("durable_inhibit_unconfirmed") is not True, report
        assert record.get("local_inhibit_unconfirmed") is not True, report
        assert type(record.get("settled_disarm_successor")) is dict, report
        assert quote_arrivals and first_byte is not None
        assert (
            max(
                (first_byte["monotonic_ns"] - stamp["monotonic_ns"]) / 1e9
                for stamp in quote_arrivals
            )
            < 5
        ), report
        if profiled:
            assert observation.counter["physical_gate"] > 0
            assert observation.counter["ssl_write"] > 0
            assert observation.counter["raw_write"] > 0
            assert observation.counter["held_readback"] >= 2
        before = local["local_synthetic_http_posts"]
        with pytest.raises(ValueError):
            await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        assert local["local_synthetic_http_posts"] == before == 1
    finally:
        observation.close()
        server.close()
        await server.wait_closed()
        if worker is not None:
            if hasattr(worker, "engine"):
                await worker.engine.dispose()
            for gate in tuple(physical._GATES):
                if (
                    physical._GATES[gate].get("facts", None) is not None
                    and physical._GATES[gate]["facts"].session_factory is worker.factory
                ):
                    physical._GATES.pop(gate, None)
            worker.close()
