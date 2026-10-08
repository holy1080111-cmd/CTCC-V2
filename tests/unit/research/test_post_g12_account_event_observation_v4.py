"""Synthetic V4 event observation bridge; no exchange or order authority."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import post_g12_account_event_observation_v4 as bridge
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.reservations import (
    LedgerScope,
    ReservationReceipt,
    RiskCoverage,
    ScenarioOperands,
    exact_coverage,
)
from tests.unit.research import test_post_g12_account_history_join_v3 as source_fixture
from tests.unit.research.test_post_g12_account_history_join_v3 import (
    HISTORY_ID,
    _minimal_receipt,
    _session,
)
from tests.unit.test_qualification_one_shot import UID


@pytest.fixture(scope="module")
def source_inputs_fixture():
    return source_fixture._source_inputs_fixture.__wrapped__()


def _receipt(expected, *, changed=None):
    deadline = expected["deadline"]
    times = {
        "publication_completed_at": deadline - timedelta(seconds=3),
        "public_first_request_started_at": deadline
        - timedelta(seconds=2, milliseconds=900),
        "account_history_readback_started_at": deadline
        - timedelta(seconds=2, milliseconds=800),
        "account_history_readback_completed_at": deadline
        - timedelta(seconds=2, milliseconds=700),
        "account_history_replay_verified_at": deadline
        - timedelta(seconds=2, milliseconds=600),
        "account_session_first_http_started_at": deadline
        - timedelta(seconds=2, milliseconds=500),
        "public_only_recheck_observed_at": deadline
        - timedelta(seconds=1, milliseconds=500),
        "observed_at": deadline - timedelta(seconds=1),
    }
    value = _minimal_receipt()
    value.update({name: pin for name, pin in expected.items() if name != "deadline"})
    value.update({name: stamp.isoformat() for name, stamp in times.items()})
    value.update(
        code="joined_unqualified",
        public_request_count=1,
        public_only_recheck_code="projected_math_consistent_account_path_required",
        public_only_recheck_receipt_persisted=True,
        committed_history_readback_before_first_http=True,
        **{
            name: str(index % 10) * 64
            for index, name in enumerate(joined._PIN_FIELDS[5:], start=1)
        },
    )
    if changed:
        value.update(changed)
    return joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(canonical(value))


def _observation(scope, key, expected, *, changed=None):
    at = expected["deadline"] - timedelta(milliseconds=600)
    values = {
        "contract_version": VERSION,
        "scope": scope,
        "original_event_key": key,
        "account_revision": 0,
        "ledger_revision": 0,
        "matched": None,
        "request_started_at": at,
        "observed_at": at + timedelta(milliseconds=100),
        "received_at": at + timedelta(milliseconds=200),
        "monotonic_started_ns": 10,
        "monotonic_received_ns": 11,
    }
    values.update(changed or {})
    return LedgerEventObservation(**values)


def _matched(scope, key, expected):
    operands = ScenarioOperands(
        entry=Decimal(100),
        stop_loss=Decimal(99),
        cost_per_base=Decimal("0.1"),
        contracts=Decimal(1),
        contract_value=Decimal(1),
        leverage=3,
    )
    coverage = RiskCoverage(
        candidate=operands,
        execution=operands,
        **exact_coverage(operands, operands),
    )
    return ReservationReceipt(
        scope=scope,
        reservation_id=bridge.reservation_id(scope, key),
        original_event_key=key,
        report_id="renamed-report-cannot-hide-original-event",
        instrument_id="BTC-USDT-SWAP",
        direction="long",
        correlation_group="btc",
        request_sha256="f" * 64,
        coverage=coverage,
        state="reserved",
        state_revision=1,
        account_revision=1,
        ledger_revision=2,
        created_at=expected["deadline"] - timedelta(seconds=10),
        updated_at=expected["deadline"] - timedelta(seconds=10),
        deadline=expected["deadline"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault,code,read_count",
    (
        (None, "trusted_execution_inputs_missing", 1),
        ("event_recorded_renamed", "original_event_already_recorded", 1),
        ("wrong_uid", "ledger_observation_invalid", 1),
        ("wrong_currency", "ledger_observation_invalid", 1),
        ("wrong_event", "ledger_observation_invalid", 1),
        ("late_db", "ledger_observation_invalid", 1),
        ("db_unavailable", "ledger_observation_unavailable", 1),
        ("changed_report", "v3_binding_invalid", 0),
        ("changed_reference_readback", "v3_binding_invalid", 0),
        ("changed_g12_readback", "v3_binding_invalid", 0),
        ("changed_public_readback", "v3_binding_invalid", 0),
        ("session_unconsumed", "v3_binding_invalid", 0),
        ("stale_v3", "v3_stale", 0),
        ("public_recheck_rejected", "v3_incomplete", 0),
    ),
)
async def test_v4_one_invocation_v3_readback_then_uid_event_only(
    source_inputs_fixture, tmp_path, monkeypatch, fault, code, read_count
):
    source, values, run = source_inputs_fixture
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    session = _session()
    expected = bridge._original_binding(
        source.market, run, values, session, scope, HISTORY_ID
    )
    (tmp_path / "join").mkdir()
    (tmp_path / "event").mkdir()
    ledger = QualificationLedgerRepository(None, clock=lambda: expected["deadline"])
    calls = []
    observations = []
    task = asyncio.current_task()

    async def issue(*args, **kwargs):
        assert asyncio.current_task() is task
        assert args[4] == tmp_path / "join"
        assert kwargs["account_session"] is session
        assert kwargs["history_capture_id"] == HISTORY_ID
        calls.append("new_v3")
        changed = {
            "changed_report": {"report_id": "renamed-report"},
            "stale_v3": {"observed_at": expected["deadline"].isoformat()},
            "public_recheck_rejected": {
                "public_only_recheck_code": "current_g1_rejected"
            },
        }.get(fault)
        result = _receipt(expected, changed=changed)
        if fault != "session_unconsumed":
            session._used = True
        identity = joined.source_runtime._native_recheck_root_identity(
            tmp_path / "join"
        )
        joined._publish_receipt(
            tmp_path / "join", result, expected_root_identity=identity
        )
        if fault in {
            "changed_reference_readback",
            "changed_g12_readback",
            "changed_public_readback",
        }:
            field = {
                "changed_reference_readback": "history_reference_sha256",
                "changed_g12_readback": "g12_report_sha256",
                "changed_public_readback": "public_packet_sha256",
            }[fault]
            return _receipt(expected, changed={field: "a" * 64})
        return result

    async def read_event(read_scope, key):
        assert asyncio.current_task() is task
        assert calls == ["new_v3"]
        calls.append("uid_event_read")
        assert read_scope == scope and key == expected["original_event_key"]
        if fault == "db_unavailable":
            raise RuntimeError("private SQL details remain redacted")
        changed = {}
        if fault == "wrong_uid":
            changed["scope"] = LedgerScope(account_id="999", settlement_currency="USDT")
        if fault == "wrong_currency":
            changed["scope"] = LedgerScope(account_id=UID, settlement_currency="BTC")
        if fault == "wrong_event":
            changed["original_event_key"] = "f" * 64
        if fault == "late_db":
            changed["received_at"] = expected["deadline"]
        if fault == "event_recorded_renamed":
            changed.update(
                account_revision=1,
                ledger_revision=2,
                matched=_matched(scope, key, expected),
            )
        observation = _observation(scope, key, expected, changed=changed)
        observations.append(observation)
        return observation

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("V4 attempted a ledger mutation")

    monkeypatch.setattr(joined, "publish_capture_public_account_history_v3", issue)
    monkeypatch.setattr(ledger, "read_event_observation", read_event)
    monkeypatch.setattr(ledger, "reserve", forbidden)
    monkeypatch.setattr(ledger, "reserve_control_bound", forbidden)
    monkeypatch.setattr(ledger, "consume_with_submission_intent", forbidden)
    result = await bridge.publish_capture_inspect_history_event_v4(
        tmp_path / "g12",
        tmp_path / "public",
        tmp_path / "account",
        tmp_path / "recheck",
        tmp_path / "join",
        tmp_path / "event",
        source.market,
        run=run,
        original_inputs=values,
        market_policy=object(),
        account_session=session,
        session_factory=object(),
        history_capture_id=HISTORY_ID,
        ledger=ledger,
        scope=scope,
    )
    assert result.code == code
    assert calls == ["new_v3", *("uid_event_read",) * read_count]
    assert result.admission == "DENY"
    assert result.execution_authority is False
    assert result.atomic_risk_reserved is False
    assert result.durable_intent_created is False
    assert result.order_submitted is False
    event_root = tmp_path / "event"
    replayed = bridge.read_post_g12_account_event_observation_v4(
        event_root,
        expected_sha256=result.receipt_sha256,
        expected_root_identity=joined.source_runtime._native_recheck_root_identity(
            event_root
        ),
    )
    assert replayed == result
    assert (event_root / "receipt.json").read_bytes() == result.receipt_json
    record = decode(result.receipt_json, bridge._MAX_RECEIPT)
    assert record["candidate_deadline_at"] == expected["deadline"].isoformat()
    assert record["account_uid_sha256"] == sha(scope.account_id.encode("ascii"))
    assert record["candidate_sha256"] == expected["candidate_sha256"]
    assert record["account_plan_sha256"] == expected["account_plan_sha256"]
    assert record["original_event_key"] == expected["original_event_key"]
    assert record["v3_receipt_sha256"] == result.v3_receipt_sha256
    assert record["ledger_observation_sha256"] == result.ledger_observation_sha256
    assert record["admission"] == "DENY"
    assert "private SQL details" not in repr(result)
    assert UID not in repr(result)
    assert UID.encode("ascii") not in result.receipt_json
    if result.ledger_observation_sha256 is None:
        assert {item.name for item in event_root.iterdir()} == {"receipt.json"}
    else:
        sidecar = event_root / "observation.json"
        retained = sidecar.read_bytes()
        assert sha(retained) == result.ledger_observation_sha256
        assert UID.encode("ascii") in retained
        assert {item.name for item in event_root.iterdir()} == {
            "observation.json",
            "receipt.json",
        }
        if fault is None:
            forged_observation = observations[-1].model_copy(
                update={
                    "scope": LedgerScope(account_id="999", settlement_currency="USDT")
                }
            )
            forged_raw = forged_observation.canonical_json.encode("ascii")
            forged_receipt = replace(result, ledger_observation_sha256=sha(forged_raw))
            sidecar.write_bytes(forged_raw)
            (event_root / "receipt.json").write_bytes(forged_receipt.receipt_json)
            with pytest.raises(
                bridge.PostG12AccountEventObservationError,
                match="v4_readback_failed",
            ):
                bridge.read_post_g12_account_event_observation_v4(
                    event_root,
                    expected_sha256=forged_receipt.receipt_sha256,
                    expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                        event_root
                    ),
                )
            sidecar.write_bytes(retained)
            (event_root / "receipt.json").write_bytes(result.receipt_json)
            late_root = tmp_path / "late-event"
            late_root.mkdir()
            late_identity = joined.source_runtime._native_recheck_root_identity(
                late_root
            )

            def fail_reopen(*_args, **_kwargs):
                raise bridge.PostG12AccountEventObservationError(
                    "synthetic_late_readback"
                )

            with monkeypatch.context() as patch:
                patch.setattr(
                    bridge, "read_post_g12_account_event_observation_v4", fail_reopen
                )
                with pytest.raises(
                    bridge.PostG12AccountEventObservationError,
                    match="v4_publish_failed",
                ):
                    bridge._publish_receipt(
                        late_root,
                        result,
                        expected_root_identity=late_identity,
                        observation=observations[-1],
                    )
            assert (late_root / "observation.json").read_bytes() == retained
            assert (late_root / "receipt.json").read_bytes() == result.receipt_json
            with pytest.raises(
                bridge.PostG12AccountEventObservationError, match="v4_publish_failed"
            ):
                bridge._publish_receipt(
                    late_root,
                    result,
                    expected_root_identity=late_identity,
                    observation=observations[-1],
                )
            partial_root = tmp_path / "partial-event"
            partial_root.mkdir()
            partial_identity = joined.source_runtime._native_recheck_root_identity(
                partial_root
            )
            native_root = bridge.source_runtime._native_recheck_root

            @contextmanager
            def fail_final_receipt(root):
                with native_root(root) as directory:

                    class FailingFinalReceipt:
                        path = directory.path
                        fd = getattr(directory, "fd", None)

                        def __getattr__(self, name):
                            return getattr(directory, name)

                        def publish(self, name, payload):
                            if name == "receipt.json":
                                raise OSError("synthetic final receipt failure")
                            return directory.publish(name, payload)

                    yield FailingFinalReceipt()

            with monkeypatch.context() as patch:
                patch.setattr(
                    bridge.source_runtime,
                    "_native_recheck_root",
                    fail_final_receipt,
                )
                with pytest.raises(
                    bridge.PostG12AccountEventObservationError,
                    match="v4_publish_failed",
                ):
                    bridge._publish_receipt(
                        partial_root,
                        result,
                        expected_root_identity=partial_identity,
                        observation=observations[-1],
                    )
            assert (partial_root / "observation.json").read_bytes() == retained
            assert not (partial_root / "receipt.json").exists()
            with pytest.raises(
                bridge.PostG12AccountEventObservationError,
                match="v4_publish_failed",
            ):
                bridge._publish_receipt(
                    partial_root,
                    result,
                    expected_root_identity=partial_identity,
                    observation=observations[-1],
                )
        sidecar.write_bytes(b"{}")
        with pytest.raises(
            bridge.PostG12AccountEventObservationError, match="v4_readback_failed"
        ):
            bridge.read_post_g12_account_event_observation_v4(
                event_root,
                expected_sha256=result.receipt_sha256,
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    event_root
                ),
            )
        sidecar.write_bytes(retained)
        sidecar.unlink()
        with pytest.raises(
            bridge.PostG12AccountEventObservationError, match="v4_readback_failed"
        ):
            bridge.read_post_g12_account_event_observation_v4(
                event_root,
                expected_sha256=result.receipt_sha256,
                expected_root_identity=joined.source_runtime._native_recheck_root_identity(
                    event_root
                ),
            )
        assert observations


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("uid", "currency"))
async def test_v4_scope_mismatch_rejected_before_publication(
    source_inputs_fixture, tmp_path, change
):
    source, values, run = source_inputs_fixture
    scope = LedgerScope(
        account_id="999" if change == "uid" else UID,
        settlement_currency="BTC" if change == "currency" else "USDT",
    )
    ledger = QualificationLedgerRepository(None, clock=lambda: run.evaluated_at)
    with pytest.raises(
        bridge.PostG12AccountEventObservationError, match="v4_account_scope_mismatch"
    ):
        await bridge.publish_capture_inspect_history_event_v4(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            tmp_path / "recheck",
            tmp_path / "join",
            tmp_path / "event",
            source.market,
            run=run,
            original_inputs=values,
            market_policy=object(),
            account_session=_session(),
            session_factory=object(),
            history_capture_id=HISTORY_ID,
            ledger=ledger,
            scope=scope,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_v4_existing_join_receipt_cannot_be_reused(
    source_inputs_fixture, tmp_path, monkeypatch
):
    source, values, run = source_inputs_fixture
    join_root = tmp_path / "join"
    join_root.mkdir()
    event_root = tmp_path / "event"
    event_root.mkdir()
    (join_root / "receipt.json").write_bytes(b"old receipt cannot be replayed")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("old receipt reached a new source invocation")

    monkeypatch.setattr(joined, "publish_capture_public_account_history_v3", forbidden)
    ledger = QualificationLedgerRepository(None, clock=lambda: run.evaluated_at)
    with pytest.raises(
        joined.prior.PostG12AccountJoinError, match="v4_join_root_unavailable"
    ):
        await bridge.publish_capture_inspect_history_event_v4(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            tmp_path / "recheck",
            join_root,
            event_root,
            source.market,
            run=run,
            original_inputs=values,
            market_policy=object(),
            account_session=_session(),
            session_factory=object(),
            history_capture_id=HISTORY_ID,
            ledger=ledger,
            scope=LedgerScope(account_id=UID, settlement_currency="USDT"),
        )
    assert (
        join_root / "receipt.json"
    ).read_bytes() == b"old receipt cannot be replayed"
    assert list(event_root.iterdir()) == []


@pytest.mark.asyncio
async def test_v4_existing_event_receipt_rejected_before_new_v3(
    source_inputs_fixture, tmp_path, monkeypatch
):
    source, values, run = source_inputs_fixture
    join_root = tmp_path / "join"
    join_root.mkdir()
    event_root = tmp_path / "event"
    event_root.mkdir()
    (event_root / "receipt.json").write_bytes(b"retained prior event journal")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("old event root reached a new V3 source invocation")

    monkeypatch.setattr(joined, "publish_capture_public_account_history_v3", forbidden)
    ledger = QualificationLedgerRepository(None, clock=lambda: run.evaluated_at)
    with pytest.raises(
        bridge.PostG12AccountEventObservationError, match="v4_event_root_unavailable"
    ):
        await bridge.publish_capture_inspect_history_event_v4(
            tmp_path / "g12",
            tmp_path / "public",
            tmp_path / "account",
            tmp_path / "recheck",
            join_root,
            event_root,
            source.market,
            run=run,
            original_inputs=values,
            market_policy=object(),
            account_session=_session(),
            session_factory=object(),
            history_capture_id=HISTORY_ID,
            ledger=ledger,
            scope=LedgerScope(account_id=UID, settlement_currency="USDT"),
        )
    assert (event_root / "receipt.json").read_bytes() == b"retained prior event journal"
    assert list(join_root.iterdir()) == []


def test_v4_result_cannot_claim_authority():
    for field in (
        "atomic_risk_reserved",
        "durable_intent_created",
        "execution_authority",
        "order_submitted",
    ):
        with pytest.raises(
            bridge.PostG12AccountEventObservationError,
            match="v4_cannot_grant_execution",
        ):
            bridge.PostG12AccountEventObservationV4(
                code="v3_incomplete",
                report_id="synthetic",
                scope_sha256="1" * 64,
                account_uid_sha256="4" * 64,
                candidate_sha256="5" * 64,
                account_plan_sha256="6" * 64,
                original_event_key="2" * 64,
                reservation_id="3" * 64,
                candidate_deadline_at="2026-10-09T00:00:00+00:00",
                **{field: True},
            )


@pytest.mark.parametrize(
    "code,override",
    (
        ("trusted_execution_inputs_missing", {}),
        ("original_event_already_recorded", {"ledger_event_state": "absent_at_read"}),
        ("ledger_observation_invalid", {"ledger_event_state": "absent_at_read"}),
        ("ledger_observation_unavailable", {"ledger_observation_sha256": "e" * 64}),
        ("v3_incomplete", {"g12_evidence_sha256": "e" * 64}),
        ("v3_binding_invalid", {"v3_receipt_sha256": "e" * 64}),
        ("fake_pass", {}),
    ),
)
def test_v4_constructor_rejects_contradictory_audit_claims(code, override):
    with pytest.raises(bridge.PostG12AccountEventObservationError):
        bridge.PostG12AccountEventObservationV4(
            code=code,
            report_id="synthetic",
            scope_sha256="1" * 64,
            account_uid_sha256="4" * 64,
            candidate_sha256="5" * 64,
            account_plan_sha256="6" * 64,
            original_event_key="2" * 64,
            reservation_id="3" * 64,
            candidate_deadline_at="2026-10-09T00:00:00+00:00",
            **override,
        )


def _minimal_v4():
    return bridge.PostG12AccountEventObservationV4(
        code="v3_binding_invalid",
        report_id="synthetic",
        scope_sha256="1" * 64,
        account_uid_sha256="4" * 64,
        candidate_sha256="5" * 64,
        account_plan_sha256="6" * 64,
        original_event_key="2" * 64,
        reservation_id="3" * 64,
        candidate_deadline_at="2026-10-09T00:00:00+00:00",
    )


def test_v4_denial_receipt_pins_original_candidate_and_account_plan():
    original = _minimal_v4()
    changed_candidate = replace(original, candidate_sha256="a" * 64)
    changed_plan = replace(original, account_plan_sha256="b" * 64)
    assert (
        len(
            {
                original.receipt_sha256,
                changed_candidate.receipt_sha256,
                changed_plan.receipt_sha256,
            }
        )
        == 3
    )
    for result in (original, changed_candidate, changed_plan):
        record = decode(result.receipt_json)
        assert record["report_id"] == "synthetic"
        assert record["original_event_key"] == "2" * 64
        assert result.admission == "DENY"


def test_v4_no_clobber_readback_rejects_tamper(tmp_path):
    root = tmp_path / "event"
    root.mkdir()
    result = _minimal_v4()
    identity = joined.source_runtime._native_recheck_root_identity(root)
    bridge._publish_receipt(root, result, expected_root_identity=identity)
    assert (
        bridge.read_post_g12_account_event_observation_v4(
            root, expected_sha256=result.receipt_sha256, expected_root_identity=identity
        )
        == result
    )
    with pytest.raises(
        bridge.PostG12AccountEventObservationError, match="v4_publish_failed"
    ):
        bridge._publish_receipt(root, result, expected_root_identity=identity)
    assert (root / "receipt.json").read_bytes() == result.receipt_json

    altered = {**decode(result.receipt_json), "admission": "PASS"}
    forged = canonical(altered)
    (root / "receipt.json").write_bytes(forged)
    with pytest.raises(
        bridge.PostG12AccountEventObservationError, match="v4_readback_failed"
    ):
        bridge.read_post_g12_account_event_observation_v4(
            root, expected_sha256=sha(forged), expected_root_identity=identity
        )


def test_v4_late_readback_failure_retains_published_receipt(tmp_path, monkeypatch):
    root = tmp_path / "event"
    root.mkdir()
    result = _minimal_v4()
    identity = joined.source_runtime._native_recheck_root_identity(root)

    def fail_readback(*_args, **_kwargs):
        raise bridge.PostG12AccountEventObservationError("synthetic_late_failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            bridge, "read_post_g12_account_event_observation_v4", fail_readback
        )
        with pytest.raises(
            bridge.PostG12AccountEventObservationError, match="v4_publish_failed"
        ):
            bridge._publish_receipt(root, result, expected_root_identity=identity)
    assert (root / "receipt.json").read_bytes() == result.receipt_json
    with pytest.raises(
        bridge.PostG12AccountEventObservationError, match="v4_publish_failed"
    ):
        bridge._publish_receipt(root, result, expected_root_identity=identity)
    assert (root / "receipt.json").read_bytes() == result.receipt_json
