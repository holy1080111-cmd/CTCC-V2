"""Synthetic journal joins test integrity only; no native Demo authority exists."""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from app.public_market_source.public_market_receipts import canonical, decode, sha
from app.trade_qualification import demo_public_origin
from app.trade_qualification import post_g12_public_runtime as coordinator
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification.post_g12_public_join_v1 import (
    PostG12PublicJoinError,
    PostG12PublicJoinReceiptV1,
    replay_post_g12_public_join_v1,
)
from app.trade_qualification.post_g12_recheck_v2 import (
    PostG12PublicRecheckReceiptV2,
    PostG12RecheckV2Error,
    evaluate_post_g12_public_recheck_v2,
    verify_post_g12_public_recheck_v2,
)
from app.trade_qualification.recheck_models import freeze_recheck_origin
from tests.unit.research.test_demo_public_origin_preflight import session
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    fresh_funding_return,
    policy,
    setup,
)
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_one_shot import inputs as inputs  # noqa: PLC0414


@pytest.mark.asyncio
async def test_routed_post_g12_public_join_is_integrity_only_and_denies_changes(
    inputs, tmp_path, monkeypatch
):
    source, values, run = inputs
    clock, directory, harness, publications = setup(
        monkeypatch, source, original=inputs
    )
    fresh_funding_return(harness, clock)
    controlled = session()
    route = demo_public_origin.reviewed_demo_public_route("global")
    rest_proof = {
        "classification": "owned_native_tls",
        "hostname": route.rest_hostname,
        "version": "TLSv1.3",
        "peer_sha256": "1" * 64,
    }
    monkeypatch.setattr(runtime, "_http_tls", lambda *_args: dict(rest_proof))

    def ws_connected(owner, socket):
        state = runtime._state(owner)
        assert socket is harness.socket and state["socket"] is None
        state["socket"] = socket
        runtime._event(
            owner,
            "ws_connected",
            {
                "connected": state["last"],
                "tls": {**rest_proof, "hostname": route.ws_hostname},
            },
        )

    monkeypatch.setattr(runtime, "_ws_connected", ws_connected)
    result = await coordinator.publish_capture_public_v2(
        tmp_path / "g12",
        tmp_path / "fresh",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
        demo_session=controlled,
    )
    assert result.packet is not None and result.context is not None, result.code
    assert result.final_g1 is not None and result.final_g1.passed
    assert len(publications) == 1
    harness.assert_closed()
    empty_registries()
    origin = freeze_recheck_origin(result.evidence)
    payload = {
        "expected_plan_sha256": sha(directory.content["plan.json"]),
        "expected_journal_sha256": sha(directory.content["summary.json"]),
        "expected_packet_sha256": result.packet.bundle_sha256,
        "origin": origin,
        "account_plan_sha256": controlled._pin,
        "current_market_json": canonical(
            result.context.market.model_dump(mode="json", round_trip=True)
        ).decode("ascii"),
        "reference_json": canonical(
            result.context.reference.model_dump(mode="json", round_trip=True)
        ).decode("ascii"),
        "observed_at": result.final_g1.evaluated_at,
    }
    receipt = replay_post_g12_public_join_v1(
        MemoryDirectory(directory.content), **payload
    )
    record = decode(receipt.receipt_json)
    assert receipt.admission == "DENY" and receipt.execution_authority is False
    assert record["code"] == "public_v2_integrity_join_only"
    assert (
        record["quote_v2_packet_sha256"]
        == decode(result.packet.packet_json)["quote_sha256"]
    )
    assert not any(
        record[name]
        for name in (
            "legacy_quote_bound",
            "source_authenticity_verified",
            "execution_recheck_performed",
            "account_complete",
            "atomic_risk_reserved",
            "execution_authority",
            "order_submitted",
        )
    )
    assert PostG12PublicJoinReceiptV1(receipt.receipt_json) == receipt

    recheck = evaluate_post_g12_public_recheck_v2(
        MemoryDirectory(directory.content), **payload
    )
    replayed = verify_post_g12_public_recheck_v2(
        recheck, MemoryDirectory(directory.content), **payload
    )
    rechecked = decode(replayed.receipt_json)
    assert replayed == recheck and recheck.admission == "DENY"
    assert not recheck.execution_authority
    assert rechecked["code"] == "projected_math_consistent_account_path_required"
    assert rechecked["g1"]["passed"] is True
    assert rechecked["current_g2_g4"]["passed"] is True
    assert rechecked["original_event_zone"]["zone_code"] == "passed"
    assert rechecked["original_entry"] == str(values["intent"].candidate_entry)
    assert rechecked["original_stop_loss"] == str(run.result.stop_loss)
    assert rechecked["original_take_profit"] == str(run.result.take_profit)
    assert rechecked["executable_reference"] == str(
        result.context.quote.ticker.ask
        if source.direction == "long"
        else result.context.quote.ticker.bid
    )
    assert rechecked["projected_economics"]["candidate"]["code"] == "passed"
    assert rechecked["projected_economics"]["execution"]["code"] == "passed"
    assert rechecked["projected_economics"]["comparison"]
    assert not any(
        rechecked[name]
        for name in (
            "complete_path_verified",
            "original_event_survival_verified",
            "actual_account_costs_verified",
            "account_complete",
            "execution_recheck_performed",
            "atomic_risk_reserved",
            "execution_authority",
            "order_submitted",
        )
    )
    forged_recheck = canonical({**rechecked, "code": "current_g1_rejected"})
    with pytest.raises(PostG12RecheckV2Error, match="receipt_changed"):
        verify_post_g12_public_recheck_v2(
            PostG12PublicRecheckReceiptV2(forged_recheck),
            MemoryDirectory(directory.content),
            **payload,
        )
    with pytest.raises(PostG12RecheckV2Error, match="receipt_invalid"):
        PostG12PublicRecheckReceiptV2(
            canonical({**rechecked, "atomic_risk_reserved": True})
        )
    with pytest.raises(TypeError):
        evaluate_post_g12_public_recheck_v2(
            MemoryDirectory(directory.content), **payload, quote_json="legacy"
        )

    # Every row below is computed from a real full synthetic journal replay, not
    # a mocked packet/result. Its exact-match receipt is still permanently DENY.
    for field in (
        "account_plan_sha256",
        "expected_packet_sha256",
        "expected_journal_sha256",
        "expected_plan_sha256",
    ):
        changed = dict(payload)
        changed[field] = "0" * 64
        with pytest.raises(PostG12PublicJoinError):
            replay_post_g12_public_join_v1(
                MemoryDirectory(directory.content), **changed
            )
    changed = dict(payload)
    changed["current_market_json"] = canonical(
        {
            **decode(payload["current_market_json"].encode("ascii")),
            "quality": {"caller": "substituted"},
        }
    ).decode("ascii")
    with pytest.raises(PostG12PublicJoinError, match="projection_mismatch"):
        replay_post_g12_public_join_v1(MemoryDirectory(directory.content), **changed)
    changed = dict(payload)
    changed["reference_json"] = canonical(
        {
            **decode(payload["reference_json"].encode("ascii")),
            "report_id": "different-report",
        }
    ).decode("ascii")
    with pytest.raises(PostG12PublicJoinError, match="projection_mismatch"):
        replay_post_g12_public_join_v1(MemoryDirectory(directory.content), **changed)
    changed = dict(payload)
    changed["observed_at"] = origin.publication_completed_at - timedelta(microseconds=1)
    with pytest.raises(PostG12PublicJoinError, match="observation_time_invalid"):
        replay_post_g12_public_join_v1(MemoryDirectory(directory.content), **changed)
    changed = dict(payload)
    changed["origin"] = origin.model_copy(update={"original_event_key": "0" * 64})
    with pytest.raises(PostG12PublicJoinError):
        replay_post_g12_public_join_v1(MemoryDirectory(directory.content), **changed)
    tampered = copy.deepcopy(directory.content)
    event = next(name for name in tampered if name.endswith(".raw"))
    tampered[event] += b"changed"
    with pytest.raises(PostG12PublicJoinError, match="replay_denied"):
        replay_post_g12_public_join_v1(MemoryDirectory(tampered), **payload)
    forged = dict(record, execution_authority=True)
    with pytest.raises(PostG12PublicJoinError, match="receipt_invalid"):
        PostG12PublicJoinReceiptV1(canonical(forged))
