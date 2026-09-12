"""Actual filesystem G12 then mocked-public IO then real offline calculations.

Raw market/account inputs and all clocks are synthetic. Filesystem publication
is native and verified; network responses still use MockTransport. No test grants
source/account authentication, complete price-path or runtime/order authority.
"""

import asyncio
import hashlib
import os
from datetime import timedelta

import httpx
import pytest

from app.trade_evidence.gates import publish_qualification_evidence
from app.trade_evidence.storage import FILE_NAMES
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.recheck import evaluate_recorded_recheck
from app.trade_qualification.recheck_models import freeze_recheck_origin
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.qualification_recheck_fixtures import capture_recheck_source
from tests.unit.test_qualification_evidence_gate import Clock


@pytest.mark.parametrize("direction", ("long", "short"))
@pytest.mark.parametrize("scenario", ("same_interval", "next_boundary"))
def test_native_publication_precedes_every_new_request(
    tmp_path, monkeypatch, direction, scenario
):
    source = engine_source(direction)
    inputs = engine_inputs(source)
    inputs["intent"] = inputs["intent"].model_copy(
        update={
            "expires_at": inputs["intent"].created_at + timedelta(minutes=10),
        }
    )
    pre = evaluate_pre_evidence(source.market, **inputs)
    assert pre.pre_evidence_complete
    root = tmp_path / "owned-native-recheck-root"
    root.mkdir()
    evidence = publish_qualification_evidence(
        root,
        source.market,
        run=pre,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    if os.name == "nt" and not evidence.result.gates[-1].passed:
        # Report the actual restricted Windows result without weakening native
        # pinning or starting a replacement execution path. Root/platform QA
        # must inspect this marker before claiming successful native IO.
        assert evidence.result.gates[-1].code == "evidence_publication_failed"
        assert evidence.receipt is None
        assert (
            "permissionerror" in evidence.error_detail.lower()
            or "winerror 5" in evidence.error_detail.lower()
        )
        assert not list(root.iterdir())
        print("NATIVE_RECHECK=windows_permission_denied; new_requests=0")
        return
    assert evidence.result.gates[-1].passed, evidence.error_detail
    assert evidence.receipt.status == "written"
    directory = root / source.report_id
    assert {path.name for path in directory.iterdir()} == set(FILE_NAMES)
    before = {
        record.name: (directory / record.name).read_bytes()
        for record in evidence.receipt.files
    }
    for record in evidence.receipt.files:
        assert len(before[record.name]) == record.size_bytes
        assert hashlib.sha256(before[record.name]).hexdigest() == record.sha256
    requests = []
    original_transport = httpx.MockTransport.handle_async_request

    async def after_publication(transport, request):
        # A spy on the real mocked-public request boundary, not an injected PASS.
        assert evidence.receipt.status == "written"
        assert {path.name for path in directory.iterdir()} == set(FILE_NAMES)
        for name, payload in before.items():
            assert (directory / name).read_bytes() == payload
        requests.append((request.method, request.url.host, request.url.path))
        return await original_transport(transport, request)

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", after_publication)
    latest = asyncio.run(capture_recheck_source(source, scenario=scenario))
    assert latest.original_run == pre
    assert latest.barrier_completed_at == evidence.receipt.completed_at
    assert len(requests) == 3
    assert all(
        method == "GET" and host == "www.okx.com" for method, host, _ in requests
    )
    assert all(
        p.request_started_at > evidence.receipt.completed_at
        for p in latest.latest_source.quote.provenance
    )
    result = evaluate_recorded_recheck(
        source.market,
        latest.latest_source.market,
        origin=freeze_recheck_origin(evidence),
        original_inputs=inputs,
        quote=latest.latest_source.quote,
        reference=latest.latest_source.reference,
        current_risk_inputs=latest.current_risk_inputs,
        consumed_event_keys=frozenset(),
        observed_at=latest.latest_source.evaluated_at,
    )
    if scenario == "same_interval":
        assert result.computational_checks_passed
        assert len(result.checks) == 8
    else:
        assert result.code == "fixed_target_intervening_barrier"
        assert result.current_risk is None
        assert not result.computational_checks_passed
    assert len(result.origin.candidate.gates) == 12
    assert result.origin.candidate.qualified is False
    assert result.runtime_admissible is False
    assert result.publication_observed_here is False  # coordinator is record-only
    assert result.complete_path_verified is False
    assert result.account_evidence_authenticated is False
    assert result.atomic_risk_reserved is False
    assert {name: (directory / name).read_bytes() for name in before} == before
    print(
        f"NATIVE_RECHECK=written_readback_then_three_public_test_requests; direction={direction}; scenario={scenario}; decision={result.code}; runtime_authority=0"
    )
