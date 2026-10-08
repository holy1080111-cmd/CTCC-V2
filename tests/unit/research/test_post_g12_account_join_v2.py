"""Caller-origin G12 and both native source readbacks stay non-authorizing."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import demo_public_origin, public_source_runtime
from app.trade_qualification import post_g12_account_join_v2 as joined
from app.trade_qualification import post_g12_public_runtime as public_runtime
from app.trade_qualification.account_observation_index import CaptureReference
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit import qualification_prefix_fixtures as prefix
from tests.unit import test_original_candidate_policy as original_fixture
from tests.unit import test_qualification_account_runtime as account_runtime_fixture
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.research.test_owned_original_g1_diagnostic_v3 import (
    _native_account_receipt,
)
from tests.unit.research.test_owned_original_source_coordinator_v2 import session
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    policy,
    setup,
)
from tests.unit.test_account_current_history_join import current_plan, recorded_current
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_qualification_account_capture import row
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import UID


@pytest.fixture(scope="module")
def shifted_inputs():
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
    "fault,expected_code",
    [
        (None, "joined_unqualified"),
        ("early_account_page", "account_unavailable"),
        ("missing_account_lease", "account_unavailable"),
        ("stale_public_at_join", "account_unavailable"),
        ("expired_account_lease", "account_unavailable"),
        ("recheck_replay_failed", "account_unavailable"),
        ("stale_after_persist", "denied"),
    ],
)
async def test_new_g12_then_fresh_public_and_account_raw_page_starts(
    shifted_inputs, tmp_path, monkeypatch, fault, expected_code
):
    source, values, run = shifted_inputs
    pages = flat_pages()
    pages["account_instruments"] = [[row("account_instruments", tickSz="0.01")]]
    monkeypatch.setattr(
        account_runtime_fixture,
        "BARRIER",
        original_fixture.NOW + timedelta(milliseconds=100),
    )
    chain, account_harness = await recorded_current(
        monkeypatch, offset_seconds=601, pages=pages
    )
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    payload = next(
        item.event.packet_payload for item in chain if item.event.packet_payload
    )
    account_packet = capture.verify_demo_account_packet(
        payload,
        expected_sha256=sha(payload),
        expected_plan_sha256=capture.plan_sha256(current_plan()),
    )
    account_harness.assert_closed()
    clock, directory, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(joined, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    rechecks = []
    original_recheck = joined.public_recheck.evaluate_post_g12_public_recheck_v2

    def replay_public(*args, **kwargs):
        if fault == "recheck_replay_failed":
            raise joined.public_recheck.PostG12RecheckV2Error(
                "public_recheck_v2_denied"
            )
        receipt = original_recheck(*args, **kwargs)
        rechecks.append(receipt)
        return receipt

    monkeypatch.setattr(
        joined.public_recheck, "evaluate_post_g12_public_recheck_v2", replay_public
    )
    real_context = joined.public_market_context_v2
    context_calls = 0

    def final_context(*args, **kwargs):
        nonlocal context_calls
        context_calls += 1
        if fault == "stale_after_persist" and context_calls == 2:
            raise ValueError("synthetic stale public source")
        return real_context(*args, **kwargs)

    monkeypatch.setattr(joined, "public_market_context_v2", final_context)
    route = demo_public_origin.reviewed_demo_public_route("global")
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
    controlled = session()
    frozen = capture.freeze_demo_account_packet(
        account_packet, expected_plan_sha256=controlled._pin
    )
    reference = CaptureReference(
        capture_id="b" * 32,
        head_sha256="c" * 64,
        plan_sha256=controlled._pin,
        packet_sha256=frozen.sha256,
        session_binding_sha256="e" * 64,
    )
    parent = asyncio.current_task()
    state = {"account": None, "consumed": False}

    async def account_source(received, *, session_factory, proof_root):
        assert asyncio.current_task() is parent
        assert received is controlled and proof_root == tmp_path / "account"
        proof_root.mkdir()
        (proof_root / "synthetic-account-proof").write_bytes(b"synthetic-only-proof")
        received._used = True
        while clock.last <= account_packet.completed_at:
            clock.stamp()
        observed = utc_from_ns(clock.stamp()["utc_ns"])
        diagnostic = _native_account_receipt(received, observed)
        document = decode(diagnostic.receipt_json)
        document["source_reference"]["packet_sha256"] = frozen.sha256
        diagnostic = account_native.InitialNativeAccountDiagnostic(canonical(document))
        state["account"] = diagnostic
        return diagnostic

    def consume(diagnostic, received):
        assert asyncio.current_task() is parent
        assert diagnostic is state["account"] and received is controlled
        assert state["consumed"] is False
        state["consumed"] = True
        if fault == "missing_account_lease":
            raise account_native.NativeAccountRawPacketError(
                "native_account_raw_packet_unavailable"
            )
        record = decode(diagnostic.receipt_json)
        packet = account_packet
        if fault == "early_account_page":
            first = packet.observations[0].model_copy(
                update={"request_started_at": original_fixture.NOW}
            )
            packet = packet.model_copy(
                update={"observations": (first, *packet.observations[1:])}
            )
        if fault == "stale_public_at_join":
            for _ in range(6000):
                clock.stamp()
        if fault == "expired_account_lease":
            for _ in range(11000):
                clock.stamp()
        return account_native._ObservedNativeDemoAccountRawPacket(
            packet=packet,
            reference=reference,
            receipt_sha256=diagnostic.receipt_sha256,
            proof_sha256=record["proof_sha256"],
            readback_sha256=record["proof_readback_sha256"],
            observed_at=joined._utc_text(record["observed_at"]),
            expires_at=joined._utc_text(record["expires_at"]),
        )

    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    monkeypatch.setattr(account_native, "_consume_native_demo_raw_packet", consume)
    result = await joined.publish_capture_public_account_v2(
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
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == expected_code
    assert (
        joined.read_post_g12_join_receipt_v2(
            tmp_path / "join", expected_sha256=result.receipt_sha256
        ).receipt_json
        == result.receipt_json
    )
    assert (tmp_path / "join" / "receipt.json").read_bytes() == result.receipt_json
    assert receipt["public_request_count"] > 0
    persisted = fault is None or fault == "stale_after_persist"
    assert receipt["account_page_count"] == (
        len(account_packet.observations) if persisted else 0
    )
    assert joined._utc_text(receipt["publication_completed_at"]) < joined._utc_text(
        receipt["public_first_request_started_at"]
    )
    if persisted:
        assert joined._utc_text(receipt["publication_completed_at"]) < joined._utc_text(
            receipt["account_first_request_started_at"]
        )
    else:
        assert receipt["account_first_request_started_at"] is None
    assert receipt["public_packet_sha256"] is not None
    assert receipt["account_packet_sha256"] == (frozen.sha256 if persisted else None)
    if persisted:
        # The independent verifier recomputes the same receipt from the
        # retained public bytes before the diagnostic pins its digest.
        assert len(rechecks) == 2
        assert rechecks[0].receipt_json == rechecks[1].receipt_json
        replayed = decode(rechecks[0].receipt_json)
        assert receipt["public_only_recheck_sha256"] == rechecks[0].receipt_sha256
        assert receipt["public_only_recheck_code"] == replayed["code"]
        assert replayed["original_event_key"] == receipt["original_event_key"]
        assert replayed["original_entry"] == receipt["original_entry"]
        assert replayed["original_stop_loss"] == receipt["original_stop_loss"]
        assert replayed["original_take_profit"] == receipt["original_take_profit"]
        assert (
            joined._utc_text(receipt["publication_completed_at"])
            < joined._utc_text(receipt["public_only_recheck_observed_at"])
            <= joined._utc_text(receipt["observed_at"])
        )
        assert replayed["admission"] == "DENY"
        assert replayed["execution_authority"] is False
        assert receipt["public_only_recheck_receipt_persisted"] is True
        assert (
            joined.read_public_only_recheck_receipt_v2(
                tmp_path / "recheck",
                expected_sha256=receipt["public_only_recheck_sha256"],
            ).receipt_json
            == rechecks[0].receipt_json
        )
        retained = (tmp_path / "recheck" / "receipt.json").read_bytes()
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_publish_failed",
        ):
            joined._publish_public_only_recheck_receipt_v2(
                tmp_path / "recheck", rechecks[0]
            )
        assert (tmp_path / "recheck" / "receipt.json").read_bytes() == retained
        late_root = tmp_path / "late-recheck"
        late_root.mkdir()
        original_identity = joined.source_runtime._native_recheck_root_identity
        identity_calls = 0

        def late_identity(path):
            nonlocal identity_calls
            identity_calls += 1
            if identity_calls == 2:
                raise OSError("synthetic late readback failure")
            return original_identity(path)

        with monkeypatch.context() as patch:
            patch.setattr(
                joined.source_runtime, "_native_recheck_root_identity", late_identity
            )
            with pytest.raises(
                joined.PostG12AccountJoinError,
                match="post_g12_public_recheck_publish_failed",
            ):
                joined._publish_public_only_recheck_receipt_v2(late_root, rechecks[0])
        assert (late_root / "receipt.json").read_bytes() == retained
        (tmp_path / "recheck" / "receipt.json").write_bytes(b"synthetic-corruption")
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_readback_failed",
        ):
            joined.read_public_only_recheck_receipt_v2(
                tmp_path / "recheck",
                expected_sha256=receipt["public_only_recheck_sha256"],
            )
    else:
        assert not rechecks
        assert receipt["public_only_recheck_sha256"] is None
        assert receipt["public_only_recheck_code"] is None
        assert receipt["public_only_recheck_observed_at"] is None
        assert receipt["public_only_recheck_receipt_persisted"] is False
        assert not list((tmp_path / "recheck").iterdir())
    assert receipt["original_entry"] == str(run.result.candidate_entry)
    assert receipt["original_stop_loss"] == str(run.result.stop_loss)
    assert receipt["original_take_profit"] == str(run.result.take_profit)
    assert all(receipt[name] is False for name in joined._FALSE_FIELDS)
    assert result.execution_authority is False and controlled._used
    assert state["consumed"] and len(publications) == 1
    assert all(request.method == "GET" for request in harness.requests)
    assert directory.content["summary.json"]
    assert (tmp_path / "account" / "synthetic-account-proof").read_bytes() == (
        b"synthetic-only-proof"
    )
    if fault is None:
        retained = (tmp_path / "join" / "receipt.json").read_bytes()
        with pytest.raises(
            joined.PostG12AccountJoinError, match="post_g12_join_publish_failed"
        ):
            joined._publish_post_g12_join_receipt_v2(tmp_path / "join", result)
        assert (tmp_path / "join" / "receipt.json").read_bytes() == retained
        late_root = tmp_path / "late-join"
        late_root.mkdir()
        original_identity = joined.source_runtime._native_recheck_root_identity
        identity_calls = 0

        def late_identity(path):
            nonlocal identity_calls
            identity_calls += 1
            if identity_calls == 2:
                raise OSError("synthetic late join readback failure")
            return original_identity(path)

        with monkeypatch.context() as patch:
            patch.setattr(
                joined.source_runtime, "_native_recheck_root_identity", late_identity
            )
            with pytest.raises(
                joined.PostG12AccountJoinError, match="post_g12_join_publish_failed"
            ):
                joined._publish_post_g12_join_receipt_v2(late_root, result)
        assert (late_root / "receipt.json").read_bytes() == retained
        (tmp_path / "join" / "receipt.json").write_bytes(b"synthetic-corruption")
        with pytest.raises(
            joined.PostG12AccountJoinError, match="post_g12_join_readback_failed"
        ):
            joined.read_post_g12_join_receipt_v2(
                tmp_path / "join", expected_sha256=result.receipt_sha256
            )
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_preexisting_recheck_receipt_denies_before_new_g12(
    shifted_inputs, tmp_path, monkeypatch
):
    source, values, run = shifted_inputs
    recheck_root = tmp_path / "recheck"
    recheck_root.mkdir()
    (recheck_root / "receipt.json").write_bytes(b"previous-attempt")

    def forbidden(*_args, **_kwargs):
        pytest.fail("nonempty recheck root must stop before G12 or source IO")

    monkeypatch.setattr(public_runtime, "_publish_lineage_v2", forbidden)
    with pytest.raises(
        joined.PostG12AccountJoinError,
        match="post_g12_public_recheck_root_unavailable",
    ):
        await joined.publish_capture_public_account_v2(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            recheck_root,
            tmp_path / "join",
            source.market,
            run=run,
            original_inputs=values,
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
        )
    assert (recheck_root / "receipt.json").read_bytes() == b"previous-attempt"
    assert not (tmp_path / "g12").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "join_root_kind,expected_code",
    [
        ("occupied", "post_g12_join_root_unavailable"),
        ("overlap", "original_source_roots_overlap"),
    ],
)
async def test_join_root_must_be_empty_and_distinct_before_new_g12(
    shifted_inputs, tmp_path, monkeypatch, join_root_kind, expected_code
):
    source, values, run = shifted_inputs
    recheck_root = tmp_path / "recheck"
    recheck_root.mkdir()
    join_root = recheck_root if join_root_kind == "overlap" else tmp_path / "join"
    if join_root_kind == "occupied":
        join_root.mkdir()
        (join_root / "receipt.json").write_bytes(b"previous-attempt")

    def forbidden(*_args, **_kwargs):
        pytest.fail("unsafe join root must stop before G12 or source IO")

    monkeypatch.setattr(public_runtime, "_publish_lineage_v2", forbidden)
    with pytest.raises(ValueError, match=expected_code):
        await joined.publish_capture_public_account_v2(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            recheck_root,
            join_root,
            source.market,
            run=run,
            original_inputs=values,
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
        )
    if join_root_kind == "occupied":
        assert (join_root / "receipt.json").read_bytes() == b"previous-attempt"
    assert not (tmp_path / "g12").exists()


@pytest.mark.asyncio
async def test_unverified_demo_origin_stops_before_g12_public_and_account_io(
    shifted_inputs, tmp_path, monkeypatch
):
    source, values, run = shifted_inputs
    controlled = session()
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    def forbidden(*_args, **_kwargs):
        pytest.fail("unverified Demo origin must stop before G12 or account IO")

    monkeypatch.setattr(
        public_runtime, "publish_qualification_evidence_liquidity_v2", forbidden
    )
    monkeypatch.setattr(account_native, "capture_initial_native_account", forbidden)
    result = await joined.publish_capture_public_account_v2(
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
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "denied"
    assert receipt["g12_evidence_sha256"] is None
    assert receipt["public_packet_sha256"] is None
    assert receipt["account_packet_sha256"] is None
    assert receipt["public_only_recheck_sha256"] is None
    assert receipt["public_only_recheck_code"] is None
    assert receipt["public_only_recheck_receipt_persisted"] is False
    assert receipt["admission"] == "DENY"
    assert receipt["execution_authority"] is False
    assert controlled._used
    assert not (tmp_path / "g12").exists()
    assert not list((tmp_path / "recheck").iterdir())
    forged = dict(receipt, execution_authority=True)
    with pytest.raises(
        joined.PostG12AccountJoinError, match="post_g12_account_receipt_invalid"
    ):
        joined.PostG12OwnedPublicAccountDiagnosticV2(canonical(forged))


@pytest.mark.asyncio
async def test_late_join_publish_failure_retains_bytes_without_retry(
    shifted_inputs, tmp_path, monkeypatch
):
    source, values, run = shifted_inputs
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    original_identity = joined.source_runtime._native_recheck_root_identity
    calls = 0

    def fail_late(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic late join identity failure")
        return original_identity(path)

    monkeypatch.setattr(
        joined.source_runtime, "_native_recheck_root_identity", fail_late
    )
    with pytest.raises(
        joined.PostG12AccountJoinError, match="post_g12_join_publish_failed"
    ):
        await joined.publish_capture_public_account_v2(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            tmp_path / "recheck",
            tmp_path / "join",
            source.market,
            run=run,
            original_inputs=values,
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
        )
    assert calls == 2
    retained = (tmp_path / "join" / "receipt.json").read_bytes()
    assert decode(retained)["code"] == "denied"
    assert decode(retained)["admission"] == "DENY"
    assert not (tmp_path / "g12").exists()


@pytest.mark.asyncio
async def test_original_risk_account_must_match_controlled_exact_uid_before_g12(
    shifted_inputs, tmp_path, monkeypatch
):
    source, values, run = shifted_inputs
    selected = current_plan(expected_uid="700009")
    controlled = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    def forbidden(*_args, **_kwargs):
        pytest.fail("account identity mismatch must stop before G12 and source IO")

    monkeypatch.setattr(public_runtime, "_publish_lineage_v2", forbidden)
    monkeypatch.setattr(account_native, "capture_initial_native_account", forbidden)
    with pytest.raises(
        joined.PostG12AccountJoinError, match="post_g12_original_geometry_invalid"
    ):
        await joined.publish_capture_public_account_v2(
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
        )
    assert not (tmp_path / "g12").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra", ["passed", "candidate", "receipt", "barrier", "quote", "market_packet"]
)
async def test_caller_cannot_supply_gate_or_stale_source(
    shifted_inputs, tmp_path, extra
):
    source, values, run = shifted_inputs
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    with pytest.raises(TypeError):
        await joined.publish_capture_public_account_v2(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            tmp_path / "recheck",
            tmp_path / "join",
            source.market,
            run=run,
            original_inputs=values,
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
            **{extra: object()},
        )
