"""Caller-origin G12 and both native source readbacks stay non-authorizing."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import ceil
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
    utc_from_ns,
)
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
from tests.unit.qualification_prefix_fixtures import DATA_POLICY
from tests.unit.research.test_owned_original_g1_diagnostic_v3 import (
    _native_account_receipt,
)
from tests.unit.research.test_owned_original_source_coordinator_v2 import session
from tests.unit.research.test_owned_public_runtime_v2 import (
    captured as captured,  # noqa: PLC0414 -- synthetic owned public packet
)
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    fresh_funding_return,
    policy,
    setup,
)
from tests.unit.research.test_owned_public_runtime_v2 import (
    source as source,  # noqa: PLC0414 -- fixture required by captured
)
from tests.unit.test_account_current_history_join import current_plan, recorded_current
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_qualification_account_capture import row
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_market_bridge import v2_engine_source
from tests.unit.test_qualification_one_shot import UID


def test_final_original_g1_can_expire_before_public_transport_profile(captured):
    diagnostic, _ = captured
    packet = diagnostic.packet
    early = diagnostic.context.evaluated_at
    final = early + timedelta(seconds=2)
    stricter = DATA_POLICY.model_copy(update={"maximum_quote_age_seconds": 1})
    pre = SimpleNamespace(policy=SimpleNamespace(prefix=SimpleNamespace(data=stricter)))

    early_g1 = joined.data_v2.evaluate_public_market_data_v2(
        packet,
        expected_bundle_sha256=packet.bundle_sha256,
        policy=stricter,
        evaluated_at=early,
    )
    assert early_g1.passed
    # The fixed transport profile can still be valid after the original
    # candidate's stricter G1 policy has expired.
    joined.public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=final
    )
    with pytest.raises(
        joined.PostG12AccountJoinError,
        match="^post_g12_final_original_g1_rejected$",
    ):
        joined._require_final_original_g1(packet, pre, final)


def test_final_original_economics_age_can_expire_after_g1_and_transport(
    captured, shifted_inputs
):
    packet = captured[0].packet
    quote = joined.current_economics_v2.public_v2._parts(packet)[1][1]
    stamps = (
        quote.ticker.source_generated_at,
        quote.mark.exchange_data_return_at,
        quote.funding.exchange_data_return_at,
        quote.capture_completed_at,
    )
    early = max(stamps) + timedelta(seconds=1)
    late = early + timedelta(seconds=1)
    policy = shifted_inputs[2].policy.economics.model_copy(
        update={
            "maximum_quote_age_seconds": ceil(
                max((early - stamp).total_seconds() for stamp in stamps)
            )
        }
    )
    pre = SimpleNamespace(policy=SimpleNamespace(economics=policy))
    joined.public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=late
    )
    joined._require_final_original_economics_quote(packet, pre, early)
    with pytest.raises(
        joined.PostG12AccountJoinError,
        match="^post_g12_final_original_economics_quote_rejected$",
    ):
        joined._require_final_original_economics_quote(packet, pre, late)


def test_final_freshness_uses_monotonic_elapsed_when_utc_slightly_lags():
    start_ns = 1_700_000_000_000_000_000
    start = {"utc_ns": start_ns, "monotonic_ns": 1_000_000_000}
    final = {
        "utc_ns": start_ns + 996_000_000,
        "monotonic_ns": start["monotonic_ns"] + 1_000_000_000,
    }
    # A four-millisecond wall-clock lag is inside the clock-pair tolerance,
    # but must not grant four extra milliseconds of source or deadline age.
    assert joined._monotonic_final_freshness_at(start, final) == utc_from_ns(
        start_ns + 1_000_000_000
    )
    final["utc_ns"] = start_ns + 1_001_000_000
    assert joined._monotonic_final_freshness_at(start, final) == utc_from_ns(
        final["utc_ns"]
    )


def test_monotonic_floor_closes_four_millisecond_economics_age_gap(
    captured, shifted_inputs
):
    packet = captured[0].packet
    quote = joined.current_economics_v2.public_v2._parts(packet)[1][1]
    stamps = (
        quote.ticker.source_generated_at,
        quote.mark.exchange_data_return_at,
        quote.funding.exchange_data_return_at,
        quote.capture_completed_at,
    )
    age_seconds = ceil((max(stamps) - min(stamps)).total_seconds()) + 2
    policy = shifted_inputs[2].policy.economics.model_copy(
        update={"maximum_quote_age_seconds": age_seconds}
    )
    pre = SimpleNamespace(policy=SimpleNamespace(economics=policy))
    wall_final = min(stamps) + timedelta(seconds=age_seconds, milliseconds=-2)
    wall_start = wall_final - timedelta(seconds=1)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    offset = wall_start - epoch
    start_ns = (
        (offset.days * 86400 + offset.seconds) * 1_000_000 + offset.microseconds
    ) * 1000
    start = {"utc_ns": start_ns, "monotonic_ns": 1_000_000_000}
    final = {
        "utc_ns": start_ns + 1_000_000_000,
        "monotonic_ns": start["monotonic_ns"] + 1_004_000_000,
    }
    joined._require_final_original_economics_quote(packet, pre, wall_final)
    with pytest.raises(
        joined.PostG12AccountJoinError,
        match="^post_g12_final_original_economics_quote_rejected$",
    ):
        joined._require_final_original_economics_quote(
            packet, pre, joined._monotonic_final_freshness_at(start, final)
        )


@pytest.mark.parametrize(
    "utc_elapsed_ns,monotonic_elapsed_ns",
    [(994_000_000, 1_000_000_000), (60_000_000_001, 60_000_000_001)],
)
def test_final_freshness_rejects_clock_rollback_or_overlong_pin(
    utc_elapsed_ns, monotonic_elapsed_ns
):
    start_ns = 1_700_000_000_000_000_000
    start = {"utc_ns": start_ns, "monotonic_ns": 1_000_000_000}
    final = {
        "utc_ns": start_ns + utc_elapsed_ns,
        "monotonic_ns": start["monotonic_ns"] + monotonic_elapsed_ns,
    }
    with pytest.raises(PublicReceiptError):
        joined._monotonic_final_freshness_at(start, final)


@pytest.mark.parametrize("final_passed", [True, False])
def test_final_account_join_replays_original_g1_policy(
    shifted_inputs, monkeypatch, final_passed
):
    _, _, run = shifted_inputs
    packet = SimpleNamespace(bundle_sha256="a" * 64)
    observed_at = original_fixture.NOW + timedelta(seconds=1)
    evaluated = object()
    calls = []

    def evaluate(received, **kwargs):
        calls.append(("evaluate", received, kwargs))
        return evaluated

    def verify(received, source, **kwargs):
        calls.append(("verify", received, source, kwargs))
        return SimpleNamespace(passed=final_passed)

    monkeypatch.setattr(joined.data_v2, "evaluate_public_market_data_v2", evaluate)
    monkeypatch.setattr(joined.data_v2, "verify_public_market_data_v2", verify)
    if final_passed:
        joined._require_final_original_g1(packet, run, observed_at)
    else:
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="^post_g12_final_original_g1_rejected$",
        ):
            joined._require_final_original_g1(packet, run, observed_at)
    expected = {
        "expected_bundle_sha256": packet.bundle_sha256,
        "policy": run.policy.prefix.data,
        "evaluated_at": observed_at,
    }
    assert calls == [
        ("evaluate", packet, expected),
        ("verify", evaluated, packet, expected),
    ]


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
        ("final_g1_rejected", "denied"),
        ("final_economics_quote_rejected", "denied"),
        ("stale_after_persist", "denied"),
        ("late_recheck_publish_failure", None),
        ("recheck_root_swapped", None),
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
        original_fixture.NOW + timedelta(milliseconds=102),
    )
    chain, account_harness = await recorded_current(
        monkeypatch, offset_seconds=601, pages=pages
    )
    # The synthetic journal uses in-memory bytes; give its native root an
    # identity for the source-bound final clock readback.
    (tmp_path / "public").mkdir()
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
    # Keep the synthetic funding source fresh enough for the original frozen
    # economics policy; the age-expiry branch is exercised separately below.
    fresh_funding_return(harness, clock)
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
        if fault == "recheck_root_swapped" and len(rechecks) == 2:
            (tmp_path / "recheck").rename(tmp_path / "recheck-original")
            (tmp_path / "recheck").mkdir()
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
    if fault == "final_g1_rejected":
        original_final_g1 = joined._require_final_original_g1

        def stale_only_at_final(*args, **kwargs):
            original_final_g1(*args, **kwargs)
            raise joined.PostG12AccountJoinError("post_g12_final_original_g1_rejected")

        monkeypatch.setattr(joined, "_require_final_original_g1", stale_only_at_final)
    if fault == "final_economics_quote_rejected":
        original_final_quote = joined._require_final_original_economics_quote

        def stale_quote_only_at_final(*args, **kwargs):
            original_final_quote(*args, **kwargs)
            raise joined.PostG12AccountJoinError(
                "post_g12_final_original_economics_quote_rejected"
            )

        monkeypatch.setattr(
            joined, "_require_final_original_economics_quote", stale_quote_only_at_final
        )
    if fault == "late_recheck_publish_failure":
        original_identity = joined.source_runtime._native_recheck_root_identity
        recheck_identity_calls = 0

        def fail_late_recheck_identity(path):
            nonlocal recheck_identity_calls
            if path == tmp_path / "recheck":
                recheck_identity_calls += 1
                if recheck_identity_calls == 4:
                    raise OSError("synthetic late recheck readback failure")
            return original_identity(path)

        monkeypatch.setattr(
            joined.source_runtime,
            "_native_recheck_root_identity",
            fail_late_recheck_identity,
        )
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

    async def invoke():
        return await joined.publish_capture_public_account_v2(
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

    if fault in {"late_recheck_publish_failure", "recheck_root_swapped"}:
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_publish_failed",
        ):
            await invoke()
        assert len(rechecks) == 2
        if fault == "late_recheck_publish_failure":
            assert recheck_identity_calls == 4
            assert (tmp_path / "recheck" / "receipt.json").read_bytes() == (
                rechecks[0].receipt_json
            )
        else:
            assert not list((tmp_path / "recheck").iterdir())
            assert not list((tmp_path / "recheck-original").iterdir())
        assert not list((tmp_path / "join").iterdir())
        assert controlled._used
        harness.assert_closed()
        empty_registries()
        return

    result = await invoke()
    receipt = decode(result.receipt_json)
    assert receipt["code"] == expected_code
    assert receipt["schema_version"] == joined._CLOCK_SCHEMA
    if fault is None:
        proof = receipt["final_clock_proof"]
        assert proof["public_journal_sha256"] == receipt["public_journal_sha256"]
        assert proof["consume_clock"] is not None
        cutoff = joined.final_clock_proof.replay_proof(
            proof,
            expected_public_journal_sha256=receipt["public_journal_sha256"],
        )
        assert (
            joined._utc_text(receipt["observed_at"])
            <= cutoff
            < joined._utc_text(receipt["candidate_deadline_at"])
        )
        with pytest.raises(joined.PostG12AccountJoinError):
            joined.PostG12OwnedPublicAccountDiagnosticV2(
                canonical({**receipt, "candidate_deadline_at": cutoff.isoformat()})
            )
        forged = {
            **receipt,
            "final_clock_proof": {
                **proof,
                "effective_cutoff_at": "1970-01-01T00:00:00+00:00",
            },
        }
        with pytest.raises(joined.PostG12AccountJoinError):
            joined.PostG12OwnedPublicAccountDiagnosticV2(canonical(forged))
    assert (
        joined.read_post_g12_join_receipt_v2(
            tmp_path / "join",
            expected_sha256=result.receipt_sha256,
            expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                tmp_path / "join"
            ),
        ).receipt_json
        == result.receipt_json
    )
    assert (tmp_path / "join" / "receipt.json").read_bytes() == result.receipt_json
    assert receipt["public_request_count"] > 0
    persisted = fault is None or fault in {
        "stale_after_persist",
        "final_g1_rejected",
        "final_economics_quote_rejected",
    }
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
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    tmp_path / "recheck"
                ),
            ).receipt_json
            == rechecks[0].receipt_json
        )
        retained = (tmp_path / "recheck" / "receipt.json").read_bytes()
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_publish_failed",
        ):
            joined._publish_public_only_recheck_receipt_v2(
                tmp_path / "recheck",
                rechecks[0],
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    tmp_path / "recheck"
                ),
            )
        assert (tmp_path / "recheck" / "receipt.json").read_bytes() == retained
        late_root = tmp_path / "late-recheck"
        late_root.mkdir()
        original_identity = joined.source_runtime._native_recheck_root_identity
        late_identity_pin = original_identity(late_root)
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
                joined._publish_public_only_recheck_receipt_v2(
                    late_root,
                    rechecks[0],
                    expected_root_identity=late_identity_pin,
                )
        assert (late_root / "receipt.json").read_bytes() == retained
        readback_swap_root = tmp_path / "readback-swap-recheck"
        readback_swap_root.mkdir()
        readback_pin = original_identity(readback_swap_root)
        moved_readback_root = tmp_path / "readback-swap-recheck-original"
        original_readback = joined.read_public_only_recheck_receipt_v2

        def swap_during_recheck_readback(root, **kwargs):
            root.rename(moved_readback_root)
            root.mkdir()
            return original_readback(root, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(
                joined,
                "read_public_only_recheck_receipt_v2",
                swap_during_recheck_readback,
            )
            with pytest.raises(
                joined.PostG12AccountJoinError,
                match="post_g12_public_recheck_publish_failed",
            ):
                joined._publish_public_only_recheck_receipt_v2(
                    readback_swap_root,
                    rechecks[0],
                    expected_root_identity=readback_pin,
                )
        assert (moved_readback_root / "receipt.json").read_bytes() == retained
        assert not list(readback_swap_root.iterdir())
        swapped_root = tmp_path / "swapped-recheck"
        swapped_root.mkdir()
        swapped_pin = original_identity(swapped_root)
        swapped_original = tmp_path / "swapped-recheck-original"
        swapped_root.rename(swapped_original)
        swapped_root.mkdir()
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_publish_failed",
        ):
            joined._publish_public_only_recheck_receipt_v2(
                swapped_root,
                rechecks[0],
                expected_root_identity=swapped_pin,
            )
        assert not list(swapped_root.iterdir())
        assert not list(swapped_original.iterdir())
        (swapped_root / "receipt.json").write_bytes(rechecks[0].receipt_json)
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_readback_failed",
        ):
            joined.read_public_only_recheck_receipt_v2(
                swapped_root,
                expected_sha256=rechecks[0].receipt_sha256,
                expected_root_identity=swapped_pin,
            )
        assert (swapped_root / "receipt.json").read_bytes() == (
            rechecks[0].receipt_json
        )
        (tmp_path / "recheck" / "receipt.json").write_bytes(b"synthetic-corruption")
        with pytest.raises(
            joined.PostG12AccountJoinError,
            match="post_g12_public_recheck_readback_failed",
        ):
            joined.read_public_only_recheck_receipt_v2(
                tmp_path / "recheck",
                expected_sha256=receipt["public_only_recheck_sha256"],
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    tmp_path / "recheck"
                ),
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
            joined._publish_post_g12_join_receipt_v2(
                tmp_path / "join",
                result,
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    tmp_path / "join"
                ),
            )
        assert (tmp_path / "join" / "receipt.json").read_bytes() == retained
        late_root = tmp_path / "late-join"
        late_root.mkdir()
        original_identity = joined.source_runtime._native_recheck_root_identity
        late_identity_pin = original_identity(late_root)
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
                joined._publish_post_g12_join_receipt_v2(
                    late_root,
                    result,
                    expected_root_identity=late_identity_pin,
                )
        assert (late_root / "receipt.json").read_bytes() == retained
        readback_swap_root = tmp_path / "readback-swap-join"
        readback_swap_root.mkdir()
        readback_pin = original_identity(readback_swap_root)
        moved_readback_root = tmp_path / "readback-swap-join-original"
        original_readback = joined.read_post_g12_join_receipt_v2

        def swap_during_join_readback(root, **kwargs):
            root.rename(moved_readback_root)
            root.mkdir()
            return original_readback(root, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(
                joined, "read_post_g12_join_receipt_v2", swap_during_join_readback
            )
            with pytest.raises(
                joined.PostG12AccountJoinError, match="post_g12_join_publish_failed"
            ):
                joined._publish_post_g12_join_receipt_v2(
                    readback_swap_root,
                    result,
                    expected_root_identity=readback_pin,
                )
        assert (moved_readback_root / "receipt.json").read_bytes() == retained
        assert not list(readback_swap_root.iterdir())
        swapped_root = tmp_path / "swapped-join"
        swapped_root.mkdir()
        swapped_pin = original_identity(swapped_root)
        swapped_original = tmp_path / "swapped-join-original"
        swapped_root.rename(swapped_original)
        swapped_root.mkdir()
        with pytest.raises(
            joined.PostG12AccountJoinError, match="post_g12_join_publish_failed"
        ):
            joined._publish_post_g12_join_receipt_v2(
                swapped_root, result, expected_root_identity=swapped_pin
            )
        assert not list(swapped_root.iterdir())
        assert not list(swapped_original.iterdir())
        (swapped_root / "receipt.json").write_bytes(result.receipt_json)
        with pytest.raises(
            joined.PostG12AccountJoinError, match="post_g12_join_readback_failed"
        ):
            joined.read_post_g12_join_receipt_v2(
                swapped_root,
                expected_sha256=result.receipt_sha256,
                expected_root_identity=swapped_pin,
            )
        assert (swapped_root / "receipt.json").read_bytes() == result.receipt_json
        (tmp_path / "join" / "receipt.json").write_bytes(b"synthetic-corruption")
        with pytest.raises(
            joined.PostG12AccountJoinError, match="post_g12_join_readback_failed"
        ):
            joined.read_post_g12_join_receipt_v2(
                tmp_path / "join",
                expected_sha256=result.receipt_sha256,
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    tmp_path / "join"
                ),
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
    origin_checks = []
    original_origin_guard = (
        public_source_runtime._require_trusted_v2_demo_origin_profile
    )

    def checked_origin(plan):
        origin_checks.append(plan)
        return original_origin_guard(plan)

    monkeypatch.setattr(
        public_source_runtime, "_require_trusted_v2_demo_origin_profile", checked_origin
    )

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
        g12_custody_repository=object.__new__(
            public_runtime.g12_invocation_custody.G12InvocationCustodyRepository
        ),
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
    assert len(origin_checks) == 1
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
    join_root = tmp_path / "join"

    def fail_late(path):
        nonlocal calls
        if path == join_root:
            calls += 1
            if calls == 4:
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
    assert calls == 4
    retained = (tmp_path / "join" / "receipt.json").read_bytes()
    assert decode(retained)["code"] == "denied"
    assert decode(retained)["admission"] == "DENY"
    assert not (tmp_path / "g12").exists()


@pytest.mark.asyncio
async def test_join_root_swap_after_preflight_cannot_publish_diagnostic(
    shifted_inputs, tmp_path, monkeypatch
):
    source, values, run = shifted_inputs
    (tmp_path / "recheck").mkdir()
    join_root = tmp_path / "join"
    join_root.mkdir()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    stages = []

    async def preclaimed(*_args, **_kwargs):
        # The real DB custody preclaim is tested separately. Reach the later
        # receipt-root identity check without granting any order authority.
        stages.append("preclaim")
        return object()

    def swap_root(*_args, **_kwargs):
        stages.append("root_swap")
        join_root.rename(tmp_path / "join-original")
        join_root.mkdir()
        return None, None, None, None

    monkeypatch.setattr(public_runtime, "_preclaim_lineage_v2", preclaimed)
    monkeypatch.setattr(public_runtime, "_publish_lineage_v2", swap_root)
    controlled = session()
    with pytest.raises(
        joined.PostG12AccountJoinError, match="post_g12_join_publish_failed"
    ):
        await joined.publish_capture_public_account_v2(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            tmp_path / "recheck",
            join_root,
            source.market,
            run=run,
            original_inputs=values,
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )
    assert stages == ["preclaim", "root_swap"]
    assert not list(join_root.iterdir())
    assert not list((tmp_path / "join-original").iterdir())
    assert not (tmp_path / "g12").exists()
    assert controlled._used


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
