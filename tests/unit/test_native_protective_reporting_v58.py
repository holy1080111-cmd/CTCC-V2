"""Original-OCO reporting DATA primitives; synthetic SQL handoffs, no authority."""

import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.database.repositories import native_protective_reporting_owner_v58 as owner
from app.database.repositories import native_protective_reporting_v58 as data
from app.demo_automation.native_requests_v2 import NativeDemoRecoveryResultV1
from app.domain import native_closed_report_schema_v55 as old_report
from app.domain import native_protective_report_schema_v58 as report
from app.domain import native_submission_binding_schema_v58 as binding
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def test_report_sql_addition_strips_to_exact55_and_preserves_false_authority():
    assert report.statements() == (report.ELIGIBLE_SQL,)
    assert (
        report.ELIGIBLE_SQL.replace(report._BRANCH, "", 1)
        .replace(report._EXTRA_DECLARATIONS, "", 1)
        .replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
        == old_report.ELIGIBLE_SQL
    )
    assert report.ELIGIBLE_BODY_SHA256 != old_report.body_sha(
        old_report.ELIGIBLE_SQL, "$ctcc$"
    )
    assert "IS DISTINCT FROM '0058'" in report._HEADER
    assert "forensic.sequence>=closed.sequence" in report._HEADER
    assert "original_selection_retired" in report._CLEANUP
    assert "native_callback_tail_and_sdk_pool_closed" in report._CLEANUP
    assert "ARRAY['custody','frame_reader']" in report._CLEANUP
    assert "unknown" in report._common_math()
    assert "source_pages') NOT BETWEEN 1 AND 2" in report._common_math()
    assert "ctcc.native_controlled_close_intent.v1" in report._HEADER
    assert report.restore_statements() == (
        old_report.ELIGIBLE_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        ),
    )


def test_new_ack_versions_have_positive58_guard_without_changing_old55_tail():
    current, original = binding.CURRENT_SQL, binding._public_ddl(binding.ANCESTOR_SQL)
    without_report = current.replace(binding._REPORT_GUARD, "", 1)
    start, end = original.index(binding._START), original.index(binding._END)
    assert current[:start] == original[:start]
    assert without_report[without_report.index(binding._END) :] == original[end:]
    assert binding._REPORT_GUARD.lstrip().startswith("IF NEW.capture_json")
    assert current.index(binding._REPORT_GUARD) > current.index(
        "RAISE EXCEPTION 'submission_outcome_binding_denied'"
    )
    for version in (report.CLEANUP_VERSION, report.READY_VERSION):
        assert version in binding._REPORT_GUARD
    for pin in (
        "NEW.status='acknowledged'",
        "closed.reason_code='post_submit_native_original_oco_flat_v58'",
        "witness.outcome_id=NEW.capture_json::jsonb->>'closure_outcome_id'",
        "forensic.outcome_id=NEW.capture_json::jsonb->>'forensics_outcome_id'",
        "NOT EXISTS(SELECT 1",
        report.REPORT_POLICY_SHA256,
    ):
        assert pin in binding._REPORT_GUARD
    assert binding.statements() == (current,)


def test_owner_only_diagnostic_sql_cannot_replace_the_native_predicate():
    from tests.integration.test_native_original_oco_reporting_0058_pg import (
        _diagnostic_report_sql,
    )

    before = report.ELIGIBLE_SQL
    copied = _diagnostic_report_sql()
    assert copied.startswith(
        "CREATE FUNCTION public.ctcc_original_oco_report_probe_v58("
    )
    assert "CREATE OR REPLACE FUNCTION" not in copied
    assert "native_original_oco_report_rule_001" in copied
    assert "EXCEPTION WHEN OTHERS THEN RAISE;" in copied
    assert "RETURN false;" not in copied
    assert report.ELIGIBLE_SQL == before


def test_owned_report_writer_guards_compile_and_forged_caps_are_denied():
    owner._NATIVE_GUARD()
    with pytest.raises(ValueError):
        owner.OwnedProtectiveReportWriterV58()
    forged = object.__new__(owner.OwnedProtectiveReportWriterV58)
    for operation in (copy.copy, copy.deepcopy):
        with pytest.raises(ValueError):
            operation(forged)
    with pytest.raises(ValueError):
        owner._record(forged, key=post._PROTECTIVE_REPORT_KEY_V58)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [None, {}, {"verified_normal_protected_recovery": None}]
)
async def test_preparing_writer_without_original_source_fails_closed(state):
    with pytest.raises(ValueError):
        await owner.prepare_owned_protective_report_writer_v58(
            state, result=object(), key=post._PROTECTIVE_REPORT_KEY_V58
        )


@pytest.mark.parametrize(
    "status,spool", [("pending", "a" * 64), ("durable_pending_projection", None)]
)
def test_recovery_ui_cannot_claim_durable_spool_without_exact_readback(status, spool):
    with pytest.raises(ValidationError):
        NativeDemoRecoveryResultV1(
            invocation_id="synthetic",
            reservation_id="a" * 64,
            classification="original_oco_full_exit_flat",
            outcome_id="b" * 64,
            receipt_sha256="c" * 64,
            risk_hold_retained=False,
            fresh_resource_cleanup_confirmed=True,
            report_status=status,
            report_spool_id=spool,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "write_replay", "commit", "readback", "same_xid"]
)
async def test_foreign_data_primitive_atomically_spools_then_independently_replays(
    failure,
):
    # This foreign primitive is deliberately not a native capability. Real SQL
    # guards/roles/commits are exercised separately by the public58 PG cases.
    cap, events = object(), []
    report_bytes = b'{"declared_synthetic_report":true}'
    target = SimpleNamespace(
        reservation_id="a" * 64,
        request_json="{}",
        intent=SimpleNamespace(sha256="b" * 64),
        request=SimpleNamespace(
            origin=SimpleNamespace(
                candidate=SimpleNamespace(report_id="synthetic-report")
            )
        ),
    )
    value = {
        "phase": "RETIRED",
        "target": target,
        "report": SimpleNamespace(receipt_json=report_bytes),
        "namespace": "synthetic-report",
        "cleanup": {"declared_synthetic_cleanup": True},
        "closure_json": '{"assessment_sha256":"' + "c" * 64 + '"}',
        "result": SimpleNamespace(outcome_id="d" * 64),
        "forensic_id": "e" * 64,
        "current_manifest_id": "f" * 64,
    }
    registry, store = {cap: value}, {}

    class Session:
        def __init__(self, index):
            self.index = index

        async def scalar(self, query):
            if "pg_current_xact_id" in str(query):
                return "tx1" if failure == "same_xid" else f"tx{self.index}"
            return store.get("spool")

        async def get(self, model, identity):
            assert model is owner.Spool
            events.append("new_session_readback")
            if failure == "readback":
                raise ValueError("synthetic readback failure")
            assert store["spool"].spool_id == identity
            return store["spool"]

        def add(self, row):
            assert type(row) is owner.Spool
            events.append("spool")
            store["spool"] = row

        async def flush(self):
            events.append("flush")

    @asynccontextmanager
    async def writer(actual):
        assert actual is value
        index = 1 if actual["phase"] == "RETIRED" else 2
        events.append(f"begin{index}")
        yield Session(index), object()
        if failure == "commit" and index == 1:
            raise ValueError("synthetic uncertain commit")
        events.append(f"commit{index}")

    async def append(actual, session, scope, capture):
        assert actual is value
        events.append(capture["version"])
        assert capture["forensics_outcome_id"] == value["forensic_id"]
        return SimpleNamespace(
            outcome_id=("1" if capture["version"] == report.CLEANUP_VERSION else "2")
            * 64,
            recorded_at=datetime.now(UTC),
        )

    async def replay(session, spool):
        events.append(f"replay{session.index}")
        assert spool.report_bytes == report_bytes
        if failure == "write_replay" and session.index == 1:
            raise ValueError("synthetic write replay failure")

    primitive = foreign_primitive_v1(
        owner.persist_retired_protective_report_v58,
        {
            "_record": lambda actual, *, key: value if actual is cap else None,
            "_writer": writer,
            "_append": append,
            "_WRITERS": registry,
            "replay": SimpleNamespace(replay_protective_terminal_spool_data_v58=replay),
        },
    )
    if failure is None:
        result = await primitive(cap, key=object())
        assert result.status == "durable_pending_projection"
        assert result.execution_authority is result.order_retry_authority is False
        assert events.index("commit1") < events.index("begin2")
        assert events.index("new_session_readback") < events.index("replay2")
        assert cap not in registry
    else:
        with pytest.raises(ValueError):
            await primitive(cap, key=object())
        assert cap in registry
        assert "commit2" not in events
        assert value["phase"] == (
            "RETIRED" if failure in {"write_replay", "commit"} else "COMMITTED"
        )


@pytest.mark.asyncio
async def test_forged_spool_data_is_not_a_source_or_report_authority():
    with pytest.raises(ValueError):
        await data.replay_protective_terminal_spool_data_v58(object(), object())
