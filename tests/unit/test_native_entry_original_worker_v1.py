"""Original worker orchestration with declared inert admission/SQL/POST seams.

This is engineering coverage, never live, configured-source or venue acceptance.
The real byte reader and real compiled source guard have separate unmocked tests.
"""

import json
from contextlib import asynccontextmanager
from cProfile import Profile
from pstats import Stats

import pytest

from app.okx_live import owned_qualification_v1 as q
from tests.native_entry_graph_fixture_data_v2 import synthetic_graph_bytes_v2
from tests.unit.native_entry_worker_fixture_v1 import WorkerFixture
from tests.unit.test_live_qualification_math_v1 import (
    market as market,  # noqa: PLC0414 -- declared long/short four-frame raw tape.
)


@pytest.mark.asyncio
async def test_original_worker_prepares_proof_only_after_fixed_r7_and_seals_after_readback(
    monkeypatch, tmp_path, market, record_property
):
    worker = WorkerFixture(monkeypatch, tmp_path, market)
    try:
        owner, handle = await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        assert handle is worker.state["physical_handle"]
        proof = q.entry_proof._PROOFS[q.entry_proof._SEALED[owner]]
        assert proof["phase"] == "sealed"
        assert q._QUALIFIED[owner]["spent"] is True
        assert worker.session not in q.entry_proof._PENDING
        assert worker.operations.index("post_g12") < worker.operations.index(
            "durable-commit"
        )
        assert (
            worker.operations.index("durable-commit")
            < worker.operations.index("independent-readback")
            < worker.operations.index("physical-seam")
        )
        assert worker.state["initial_math"].pre_evidence_complete
        assert worker.state["current_math"].pre_evidence_complete
        assert (
            worker.state["current_math"].detection
            == worker.state["initial_math"].detection
        )
        for name in ("candidate_entry", "stop_loss", "take_profit"):
            assert getattr(worker.state["current_math"].result, name) == getattr(
                worker.state["initial_math"].result, name
            )
        value = q._QUALIFIED[owner]["facts"].intent_data
        assert (
            q.source._utc_ns(value.deadline)
            <= worker.view["current_completed"].utc_ns
            + worker.view["plan"].max_current_age_ns
        )
        record_property("synthetic_32_entry_guard_cpu_seconds", worker.guard_times[0])
        final = worker.native_phases[-1][1]
        current = {
            path: (final["monotonic_ns"] - sample["monotonic_ns"]) / 1e9
            for stage, path, sample in worker.native_arrivals
            if stage == "post_g12"
            and path
            in (
                "/api/v5/market/ticker",
                "/api/v5/public/mark-price",
                "/api/v5/public/funding-rate",
                "ws",
            )
        }
        record_property(
            "native_optimistic_entry_budget_json",
            json.dumps(
                {
                    "schema": "ctcc.synthetic_original_worker_actual_native_elapsed.v1",
                    "source_admission_sql_physical_seams_inert": True,
                    "original_semantic_clock": "declared_synthetic_fixture_unchanged",
                    "raw_market_math_g12_and_32_guard_computations_run": True,
                    "account_inventory_and_source_guard_cost_included": False,
                    "actual_native_samples": worker.native_phases,
                    "observed_synthetic_transport_to_last_guard_seconds": current,
                    "optimistic_five_second_budget_failed": any(
                        age > 5 for age in current.values()
                    ),
                    "execution_authority": False,
                    "configured_entry_acceptance": False,
                    "actual_venue_acceptance": False,
                },
                sort_keys=True,
            ),
        )
        before = list(worker.operations)
        with pytest.raises(ValueError):
            await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        assert worker.operations == before
        forged = object.__new__(q.OwnedLiveQualificationV1)
        with pytest.raises(ValueError):
            q._qualified(forged, spent=True)
    finally:
        worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ("model", "raw", "artifact", "stop", "expiry"))
async def test_final_guard_burns_original_seal_on_changed_inputs_without_repair(
    monkeypatch, tmp_path, market, mutation
):
    worker = WorkerFixture(monkeypatch, tmp_path, market)
    worker.guard_count = 1
    try:
        owner, _ = await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        original = worker.state["current_math"].receipt_json
        if mutation == "model":
            result = worker.state["current_math"].result
            object.__setattr__(result, "stop_loss", result.stop_loss + 1)
        elif mutation == "raw":
            current = q.public._OWNERS[worker.state["current_public_owner"]]
            raw = worker.banks[current["raw_root"]].content
            name = next(n for n in raw if n.endswith(".raw"))
            raw[name] += b" "
        elif mutation == "artifact":
            path = worker.evidence_root / worker.state["report_id"] / "summary.png"
            path.write_bytes(path.read_bytes() + b" ")
        elif mutation == "stop":
            worker.state["control"]["emergency_stop"] = True
        else:
            worker.clock.count += 6000
        with pytest.raises(ValueError):
            q._qualified(owner, spent=True)
        assert worker.state["current_math"].receipt_json == original
        proof = q.entry_proof._PROOFS[q.entry_proof._SEALED[owner]]
        # Expiry can be caught by the outer original session before entering
        # its private proof. The spent response can never be resubmitted.
        assert mutation == "expiry" or proof["phase"] == "burned"
        assert q._QUALIFIED[owner]["spent"] is True
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_unknown_commit_burns_pending_proof_and_never_reaches_physical_seam(
    monkeypatch, tmp_path, market
):
    worker = WorkerFixture(monkeypatch, tmp_path, market)
    worker.guard_count = 1
    original = q.ledger.NativeLiveLedgerDataRepositoryV1

    class UnknownCommit(original):
        async def reserve_and_commit_intent_data_v1(self, value, *, owned_source):
            assert worker.session in q.entry_proof._PENDING
            raise OSError("synthetic_commit_unknown")

    monkeypatch.setattr(q.ledger, "NativeLiveLedgerDataRepositoryV1", UnknownCommit)
    try:
        with pytest.raises(OSError, match="synthetic_commit_unknown"):
            await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        assert worker.session not in q.entry_proof._PENDING
        assert "physical-seam" not in worker.operations
        assert worker.state["active"] is False
        assert "qualified_owner" not in worker.state
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_actual_entry_guard_per_function_profile(
    monkeypatch, tmp_path, market, record_property
):
    """Diagnostic CPU breakdown, never a configured or venue acceptance."""
    worker = WorkerFixture(monkeypatch, tmp_path, market)
    worker.guard_count = 1
    try:
        owner, _ = await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        profile = Profile()
        profile.enable()
        q._qualified(owner, spent=True)
        profile.disable()
        rows = [
            {
                "file": file,
                "line": line,
                "function": name,
                "primitive_calls": cc,
                "calls": nc,
                "self_seconds": tt,
                "cumulative_seconds": ct,
            }
            for (file, line, name), (cc, nc, tt, ct, _callers) in Stats(
                profile
            ).stats.items()
        ]
        rows.sort(key=lambda row: row["self_seconds"], reverse=True)
        record_property("actual_entry_guard_cpu_profile_json", json.dumps(rows[:50]))
        raw = synthetic_graph_bytes_v2(
            (
                worker.state["invocation"],
                q.entry_proof._document(
                    worker.state, q._QUALIFIED[owner]["facts"].intent_data
                ),
            )
        )
        artifact = tmp_path / "declared_synthetic_entry_graph_v2.pickle"
        artifact.write_bytes(raw)
        record_property("synthetic_graph_artifact_path", str(artifact))
        record_property("synthetic_graph_artifact_sha256", q.sha(raw))
    finally:
        worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ("old_math", "old_raw", "old_source", "old_receipt", "old_geometry")
)
async def test_pre_g12_original_proof_rejects_changed_original_without_current_repair(
    monkeypatch, tmp_path, market, mutation
):
    worker = WorkerFixture(monkeypatch, tmp_path, market)
    worker.guard_count = 1
    actual = q._post_g12_public_stage_v2

    @asynccontextmanager
    async def changed(session):
        async with actual(session) as stage:
            if mutation == "old_math":
                object.__setattr__(worker.state["initial_math"].result, "raw_score", 1)
            elif mutation == "old_raw":
                record = q.public._OWNERS[worker.state["initial_public_owner"]]
                raw = worker.banks[record["raw_root"]].content
                name = next(n for n in raw if n.endswith(".raw"))
                raw[name] += b" "
            elif mutation == "old_source":
                worker.view["source_revision_sha256"] = "a" * 64
            elif mutation == "old_receipt":
                proof = q.entry_original._SESSIONS[session]
                replacement = object.__new__(q.entry_original._OriginalMathProofV2)
                q.entry_original._SESSIONS[session] = replacement
                # A copied DTO/hash has no original source registry record.
                assert replacement not in q.entry_original._PROOFS
                q.entry_original._PROOFS.pop(proof)
            else:
                object.__setattr__(worker.state["initial_math"].result, "stop_loss", 1)
            yield stage

    monkeypatch.setattr(q, "_post_g12_public_stage_v2", changed)
    try:
        with pytest.raises(ValueError):
            await q._ENTRY_RUN_BODY_SOURCE_V1(worker.session)
        assert "durable-commit" not in worker.operations
        assert "physical-seam" not in worker.operations
        assert worker.session not in q.entry_original._SESSIONS
    finally:
        worker.close()


@pytest.mark.asyncio
async def test_original_math_proof_cannot_be_minted_by_a_caller_frame(
    monkeypatch, tmp_path, market
):
    worker = WorkerFixture(monkeypatch, tmp_path, market)
    try:
        with pytest.raises(ValueError):
            q.entry_original.prepare_original_math_v2(
                worker.session, key=q._ENTRY_ORIGINAL_MATH_KEY_V2
            )
        assert worker.session not in q.entry_original._SESSIONS
    finally:
        worker.close()
