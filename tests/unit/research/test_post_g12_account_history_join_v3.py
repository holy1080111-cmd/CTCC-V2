"""Synthetic V3 post-G12 account-history join; never Demo acceptance."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_history_join as account_join
from app.trade_qualification import account_current_source_verifier as current_source
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import demo_public_origin, public_source_runtime
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification.account_capture_journal import digest
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_original_candidate_policy as original_fixture
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    policy,
    setup,
)
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import UID

HISTORY_ID = "a" * 32
CURRENT_ID = "b" * 32


def _session():
    plan = current_plan_v7()
    return ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )


@pytest.fixture(scope="module", name="source_inputs_fixture")
def _source_inputs_fixture():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prefix, "CAPTURED_AT", original_fixture.NOW)
        source = replace(
            v2_engine_source("long"),
            evaluated_at=original_fixture.NOW + timedelta(milliseconds=10),
        )
    values = engine_inputs(source)
    risk = values["risk_inputs"]
    account = risk.account.model_copy(
        update={
            "account_id": UID,
            **{
                name: getattr(risk.account, name).model_copy(update={"account_id": UID})
                for name in (
                    "balance_stamp",
                    "positions_stamp",
                    "history_stamp",
                    "reservations_stamp",
                )
            },
        }
    )
    authority = risk.authority.model_copy(
        update={"stamp": risk.authority.stamp.model_copy(update={"account_id": UID})}
    )
    values["risk_inputs"] = risk.model_copy(
        update={"account": account, "authority": authority}
    )
    run = evaluate_pre_evidence(source.market, **values)
    assert run.pre_evidence_complete
    return source, values, run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault,expected",
    [
        (None, "joined_unqualified"),
        ("pre_g12_http", "account_unavailable"),
        ("history_locator_changed", "account_unavailable"),
        ("history_reference_changed", "account_unavailable"),
        ("session_changed", "account_unavailable"),
        ("uncommitted_history", "account_unavailable"),
        ("history_replay_after_http", "account_unavailable"),
        ("missing_tail_blocker", "account_unavailable"),
        ("recheck_replay_failure", "account_unavailable"),
        ("stale_after_join", "account_unavailable"),
    ],
)
async def test_v3_g12_public_then_committed_history_and_fresh_current(
    source_inputs_fixture, tmp_path, monkeypatch, fault, expected
):
    source, values, run = source_inputs_fixture
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    clock, _, harness, _ = setup(monkeypatch, source)
    monkeypatch.setattr(joined, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    route = demo_public_origin.reviewed_demo_public_route("global")
    monkeypatch.setattr(
        public_runtime.origin_preflight,
        "_session_route",
        lambda session: (route, session._pin),
    )
    proof = {
        "classification": "owned_native_tls",
        "hostname": route.rest_hostname,
        "version": "TLSv1.3",
        "peer_sha256": "1" * 64,
    }
    monkeypatch.setattr(public_source_runtime, "_http_tls", lambda *_: dict(proof))

    def connected(owner, socket):
        state = public_source_runtime._state(owner)
        assert socket is harness.socket and state["socket"] is None
        state["socket"] = socket
        public_source_runtime._event(
            owner,
            "ws_connected",
            {
                "connected": state["last"],
                "tls": {**proof, "hostname": route.ws_hostname},
            },
        )

    monkeypatch.setattr(public_source_runtime, "_ws_connected", connected)
    controlled = _session()
    parent = asyncio.current_task()
    calls = []

    async def account_source(
        session, *, session_factory, proof_root, history_capture_id
    ):
        assert asyncio.current_task() is parent
        assert session is controlled
        assert proof_root == tmp_path / "account"
        assert history_capture_id == HISTORY_ID
        calls.append("account_after_public")
        session._used = True
        start = clock.stamp()
        end = clock.stamp()
        verified = clock.stamp()
        first = clock.stamp()
        if fault == "pre_g12_http":
            first = {
                "utc_ns": int(
                    (source.evaluated_at - timedelta(seconds=1)).timestamp()
                    * 1_000_000_000
                ),
                "monotonic_ns": 1,
            }
        if fault == "stale_after_join":
            for _ in range(6000):
                clock.stamp()
        if fault == "history_replay_after_http":
            verified = clock.stamp()
        binding = digest(controlled._plan.session_binding_id.encode("ascii"))
        history = {
            "capture_id": HISTORY_ID
            if fault != "history_locator_changed"
            else "c" * 32,
            "head_sha256": "2" * 64,
            # V5 and V7 have distinct plan contracts and distinct hashes.
            "plan_sha256": "3" * 64,
            "packet_sha256": "4" * 64,
            "session_binding_sha256": binding,
        }
        current = {
            "capture_id": CURRENT_ID,
            "head_sha256": "5" * 64,
            "plan_sha256": controlled._pin,
            "packet_sha256": "6" * 64,
            "session_binding_sha256": binding
            if fault != "session_changed"
            else "7" * 64,
        }
        return account_native.NativeCurrentHistoryJoinDiagnostic(
            canonical(
                {
                    "schema_version": "ctcc.native_current_history_join_diagnostic.v3",
                    "native_current_receipt_sha256": "e" * 64,
                    "locked_history_join_receipt_sha256": "f" * 64,
                    "join_policy_sha256": account_join.V7_ORDERED_POLICY_SHA256,
                    "current_source_policy_sha256": current_source.V7_POLICY_SHA256,
                    "history_source_reference": history,
                    "current_source_reference": current,
                    "pre_http_history_source_reference": (
                        {**history, "packet_sha256": "0" * 64}
                        if fault == "history_reference_changed"
                        else history
                    ),
                    "pre_http_history_db_chain_sha256": "8" * 64,
                    "pre_http_history_query_receipt_sha256": "9" * 64,
                    "pre_http_history_readback_started": start,
                    "pre_http_history_readback_completed": end,
                    "pre_http_history_replay_verified": verified,
                    "first_http_request_started": first,
                    "committed_history_readback_before_first_http": fault
                    != "uncommitted_history",
                    "native_current_source_observed": True,
                    "historical_native_source_observed": False,
                    "locked_readback_blocking_reasons": (
                        ["funding_accrual_provenance_missing"]
                        if fault == "missing_tail_blocker"
                        else ["history_tail_not_atomically_closed"]
                    ),
                    "history_tail_closed": False,
                    "flat_start_permission": False,
                    "snapshot": None,
                    "account_complete": False,
                    "account_revision_published": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
        )

    monkeypatch.setattr(
        account_native, "capture_native_current_history_join", account_source
    )
    if fault == "recheck_replay_failure":

        def reject_replay(*_args, **_kwargs):
            raise joined.public_recheck.PostG12RecheckV2Error("synthetic_replay_denial")

        monkeypatch.setattr(
            joined.public_recheck, "evaluate_post_g12_public_recheck_v2", reject_replay
        )
    result = await joined.publish_capture_public_account_history_v3(
        tmp_path / "g12",
        tmp_path / "public",
        tmp_path / "account",
        tmp_path / "recheck",
        tmp_path / "join",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
        history_capture_id=HISTORY_ID,
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == expected
    assert calls == ["account_after_public"]
    assert controlled._used is True
    assert (
        joined.read_post_g12_account_history_receipt_v3(
            tmp_path / "join",
            expected_sha256=result.receipt_sha256,
            expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                tmp_path / "join"
            ),
        ).receipt_json
        == result.receipt_json
    )
    assert (tmp_path / "join" / "receipt.json").read_bytes() == result.receipt_json
    assert utc_from_ns(clock.stamp()["utc_ns"]) > joined._utc(
        receipt["publication_completed_at"]
    )
    assert joined._utc(receipt["publication_completed_at"]) < joined._utc(
        receipt["public_first_request_started_at"]
    )
    if fault is None:
        assert receipt["committed_history_readback_before_first_http"] is True
        assert (
            joined._utc(receipt["publication_completed_at"])
            < joined._utc(receipt["account_history_readback_started_at"])
            < joined._utc(receipt["account_history_readback_completed_at"])
            < joined._utc(receipt["account_history_replay_verified_at"])
            < joined._utc(receipt["account_session_first_http_started_at"])
        )
        assert receipt["history_reference_sha256"] is not None
        assert receipt["current_reference_sha256"] is not None
        assert receipt["public_only_recheck_receipt_persisted"] is True
        assert (tmp_path / "recheck" / "receipt.json").exists()
    else:
        assert receipt["native_account_join_sha256"] is None
        assert receipt["history_reference_sha256"] is None
        assert receipt["current_reference_sha256"] is None
        assert receipt["public_only_recheck_receipt_persisted"] is False
        assert not (tmp_path / "recheck" / "receipt.json").exists()
    assert receipt["admission"] == "DENY"
    assert all(receipt[name] is False for name in joined._FALSE_FIELDS)
    assert result.execution_authority is False
    harness.assert_closed()
    empty_registries()


def _minimal_receipt():
    return {
        "schema_version": joined._SCHEMA,
        "code": "new_g12_required",
        "report_id": "synthetic",
        "instrument_id": "BTC-USDT-SWAP",
        "direction": "long",
        "original_entry": "100",
        "original_stop_loss": "99",
        "original_take_profit": "102",
        "publication_completed_at": None,
        "public_first_request_started_at": None,
        "account_history_readback_started_at": None,
        "account_history_readback_completed_at": None,
        "account_history_replay_verified_at": None,
        "account_session_first_http_started_at": None,
        "observed_at": None,
        "public_request_count": 0,
        "public_only_recheck_code": None,
        "public_only_recheck_observed_at": None,
        "public_only_recheck_receipt_persisted": False,
        "committed_history_readback_before_first_http": False,
        "admission": "DENY",
        **{field: "a" * 64 for field in joined._PIN_FIELDS[:5]},
        **{field: None for field in joined._PIN_FIELDS[5:]},
        **{field: False for field in joined._FALSE_FIELDS},
    }


def test_v3_receipt_cannot_promote_current_join_to_authority():
    base = _minimal_receipt()
    joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(canonical(base))
    for field in joined._FALSE_FIELDS:
        forged = {**base, field: True}
        with pytest.raises(joined.PostG12AccountHistoryJoinError):
            joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(canonical(forged))


def test_v3_receipt_observation_cannot_precede_claimed_g12_publication():
    value = _minimal_receipt()
    value.update(
        code="denied",
        publication_completed_at="2026-10-08T00:00:01+00:00",
        observed_at="2026-10-08T00:00:00+00:00",
        g12_evidence_sha256="b" * 64,
        g12_report_sha256="c" * 64,
    )
    with pytest.raises(
        joined.PostG12AccountHistoryJoinError, match="post_g12_history_receipt_invalid"
    ):
        joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(canonical(value))


def test_v3_late_join_readback_failure_preserves_durable_bytes(tmp_path, monkeypatch):
    root = tmp_path / "join"
    root.mkdir()
    receipt = joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(
        canonical(_minimal_receipt())
    )
    identity = joined.source_runtime._native_recheck_root_identity(root)

    def fail_readback(*_args, **_kwargs):
        raise joined.PostG12AccountHistoryJoinError("synthetic_late_readback_failure")

    monkeypatch.setattr(
        joined, "read_post_g12_account_history_receipt_v3", fail_readback
    )
    with pytest.raises(
        joined.PostG12AccountHistoryJoinError, match="post_g12_history_publish_failed"
    ):
        joined._publish_receipt(root, receipt, expected_root_identity=identity)
    assert (root / "receipt.json").read_bytes() == receipt.receipt_json
    with pytest.raises(
        joined.PostG12AccountHistoryJoinError, match="post_g12_history_publish_failed"
    ):
        joined._publish_receipt(root, receipt, expected_root_identity=identity)
    assert (root / "receipt.json").read_bytes() == receipt.receipt_json
