"""Diagnostic preclaim pinning; synthetic source never grants trading authority."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import decode
from app.trade_evidence import gates
from app.trade_qualification import g12_invocation_custody as custody
from app.trade_qualification import post_g12_public_runtime as runtime
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.research.test_owned_public_runtime_v2 import policy as public_policy


class Clock:
    def __init__(self, at):
        self.values = [at + timedelta(milliseconds=n) for n in (1, 2, 3)]

    def __call__(self):
        return self.values.pop(0)


def original():
    source = engine_source("long")
    inputs = engine_inputs(source)
    run = evaluate_pre_evidence(source.market, **inputs)
    assert run.pre_evidence_complete
    return source, inputs, run


def test_preclaim_pins_fixed_original_without_uid_or_authority(tmp_path):
    source, inputs, run = original()
    claim = custody.prepare_g12_claim(
        tmp_path,
        source.market,
        run,
        inputs,
        account_uid=inputs["risk_inputs"].account.account_id,
        account_plan_sha256="a" * 64,
    )
    record = claim.values
    assert record["event_key"] and record["original_run_sha256"]
    assert record["entry"] == str(run.result.candidate_entry)
    assert record["stop_loss"] == str(run.result.stop_loss)
    assert record["take_profit"] == str(run.result.take_profit)
    assert record["admission"] == "DENY"
    assert record["source_authenticity_verified"] is False
    assert record["execution_authority"] is False
    assert inputs["risk_inputs"].account.account_id not in claim.claim_json.decode()
    assert "risk_inputs" not in claim.claim_json.decode()
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.require_prepublication_root(claim, tmp_path)
    committed = replace(claim, db_claimed_at=source.evaluated_at - timedelta(seconds=1))
    custody.require_prepublication_root(committed, tmp_path)
    (tmp_path / source.report_id).mkdir()
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.require_prepublication_root(committed, tmp_path)


def test_postpublication_requires_new_six_file_write_and_same_root(tmp_path):
    source, inputs, run = original()
    claim = custody.prepare_g12_claim(
        tmp_path,
        source.market,
        run,
        inputs,
        account_uid=inputs["risk_inputs"].account.account_id,
        account_plan_sha256="a" * 64,
    )
    committed = replace(claim, db_claimed_at=source.evaluated_at - timedelta(seconds=1))
    evidence = gates.publish_qualification_evidence(
        tmp_path,
        source.market,
        run=run,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
        **inputs,
    )
    publication = custody.prepare_g12_publication(committed, evidence, tmp_path)
    assert publication.admission == "DENY"
    assert publication.execution_authority is False
    assert decode(publication.publication_json)["file_sha256"]["summary.png"]
    assert publication.publication_sha256
    other = tmp_path.parent / "other-evidence-root"
    other.mkdir()
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.prepare_g12_publication(committed, evidence, other)
    old = evidence.model_copy(
        update={
            "receipt": evidence.receipt.model_copy(update={"status": "already_present"})
        }
    )
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.prepare_g12_publication(committed, old, tmp_path)
    report = tmp_path / source.report_id / "report.json"
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.prepare_g12_publication(committed, evidence, tmp_path)


def test_postcommit_file_change_denies_before_fresh_capture(tmp_path):
    source, inputs, run = original()
    claim = custody.prepare_g12_claim(
        tmp_path,
        source.market,
        run,
        inputs,
        account_uid=inputs["risk_inputs"].account.account_id,
        account_plan_sha256="a" * 64,
    )
    committed = replace(claim, db_claimed_at=source.evaluated_at - timedelta(seconds=1))
    evidence = gates.publish_qualification_evidence(
        tmp_path,
        source.market,
        run=run,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
        **inputs,
    )
    publication = custody.prepare_g12_publication(committed, evidence, tmp_path)
    returned = replace(
        publication,
        db_recorded_at=evidence.receipt.completed_at + timedelta(milliseconds=1),
    )

    class Repository:
        async def publication(self, _claim, _publication):
            report = tmp_path / source.report_id / "report.json"
            report.write_bytes(report.read_bytes() + b" ")
            return returned

    with pytest.raises(custody.G12InvocationCustodyError, match="postcommit"):
        asyncio.run(
            runtime._complete_lineage_v2(
                Repository(), committed, tmp_path, evidence, source.market, run, inputs
            )
        )


def test_postcommit_same_open_rejects_root_path_swap(tmp_path, monkeypatch):
    source, inputs, run = original()
    claim = custody.prepare_g12_claim(
        tmp_path,
        source.market,
        run,
        inputs,
        account_uid=inputs["risk_inputs"].account.account_id,
        account_plan_sha256="a" * 64,
    )
    committed = replace(claim, db_claimed_at=source.evaluated_at - timedelta(seconds=1))
    evidence = gates.publish_qualification_evidence(
        tmp_path,
        source.market,
        run=run,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
        **inputs,
    )
    publication = custody.prepare_g12_publication(committed, evidence, tmp_path)
    returned = replace(
        publication,
        db_recorded_at=evidence.receipt.completed_at + timedelta(milliseconds=1),
    )
    identity = custody.public_source_runtime._native_recheck_root_identity
    root_checks = 0

    def path_swapped_during_read(path):
        nonlocal root_checks
        value = identity(path)
        if path == tmp_path:
            root_checks += 1
            if root_checks == 2:
                return value[0], value[1] + 1
        return value

    monkeypatch.setattr(
        custody.public_source_runtime,
        "_native_recheck_root_identity",
        path_swapped_during_read,
    )
    with pytest.raises(custody.G12InvocationCustodyError, match="postcommit"):
        custody.require_committed_publication_files(
            committed, returned, evidence, tmp_path
        )
    assert root_checks == 2


def test_preclaim_rejects_changed_exact_account_and_existing_report(tmp_path):
    source, inputs, run = original()
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.prepare_g12_claim(
            tmp_path,
            source.market,
            run,
            inputs,
            account_uid="different-account",
            account_plan_sha256="a" * 64,
        )
    (tmp_path / source.report_id).mkdir()
    with pytest.raises(custody.G12InvocationCustodyError):
        custody.prepare_g12_claim(
            tmp_path,
            source.market,
            run,
            inputs,
            account_uid=inputs["risk_inputs"].account.account_id,
            account_plan_sha256="a" * 64,
        )


def test_failed_preclaim_cannot_publish_g12_or_start_capture(tmp_path, monkeypatch):
    source, inputs, run = original()
    evidence_root, public_root = tmp_path / "evidence", tmp_path / "public"
    evidence_root.mkdir()
    public_root.mkdir()
    called = []

    async def denied(*_args, **_kwargs):
        raise custody.G12InvocationCustodyError("synthetic_preclaim_denied")

    def unexpected_publish(*_args, **_kwargs):
        called.append("g12")
        raise AssertionError("G12 after failed preclaim")

    async def unexpected_capture(*_args, **_kwargs):
        called.append("capture")
        raise AssertionError("capture after failed preclaim")

    monkeypatch.setattr(runtime, "_preclaim_lineage_v2", denied)
    monkeypatch.setattr(runtime, "_publish_lineage_v2", unexpected_publish)
    monkeypatch.setattr(runtime, "_capture_after_publication_v2", unexpected_capture)
    result = asyncio.run(
        runtime.publish_capture_public_v2(
            evidence_root,
            public_root,
            source.market,
            run=run,
            original_inputs=inputs,
            market_policy=public_policy(),
            g12_custody_repository=object(),
        )
    )
    assert result.code == "public_v2_denied" and not called
    assert result.execution_authority is False


def test_failed_publication_readback_cannot_start_capture(tmp_path, monkeypatch):
    source, inputs, run = original()
    evidence_root, public_root = tmp_path / "evidence", tmp_path / "public"
    evidence_root.mkdir()
    public_root.mkdir()
    called = []
    token = runtime._Publication(runtime._ISSUER)

    async def preclaimed(*_args, **_kwargs):
        return object()

    def published(*_args, **_kwargs):
        called.append("g12")
        return token, object(), object(), None

    async def denied(*_args, **_kwargs):
        raise custody.G12InvocationCustodyError("synthetic_publication_readback_denied")

    async def unexpected_capture(*_args, **_kwargs):
        called.append("capture")
        raise AssertionError("capture after failed publication readback")

    monkeypatch.setattr(runtime, "_preclaim_lineage_v2", preclaimed)
    monkeypatch.setattr(runtime, "_publish_lineage_v2", published)
    monkeypatch.setattr(runtime, "_complete_lineage_v2", denied)
    monkeypatch.setattr(runtime, "_capture_after_publication_v2", unexpected_capture)
    result = asyncio.run(
        runtime.publish_capture_public_v2(
            evidence_root,
            public_root,
            source.market,
            run=run,
            original_inputs=inputs,
            market_policy=public_policy(),
            g12_custody_repository=object(),
        )
    )
    assert result.code == "public_v2_denied" and called == ["g12"]
    assert result.execution_authority is False
