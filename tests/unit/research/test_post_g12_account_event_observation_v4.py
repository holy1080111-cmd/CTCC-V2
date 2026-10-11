"""Synthetic V4 event observation bridge; no exchange or order authority."""

import asyncio
import io
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from PIL import Image

from app.database.repositories.account_capture_journal import (
    LockedAccountSourceJoinReadback,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence import storage as evidence_storage
from app.trade_qualification import account_current_history_join as account_join
from app.trade_qualification import account_current_source_verifier as current_source
from app.trade_qualification import account_native_proof as native_proof
from app.trade_qualification import account_native_proof_storage as native_storage
from app.trade_qualification import post_g12_account_event_observation_v4 as bridge
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification import post_g12_recheck_v2 as public_recheck
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.reservations import (
    LedgerScope,
    ReservationReceipt,
    RiskCoverage,
    ScenarioOperands,
    exact_coverage,
)
from tests.unit.research import test_owned_public_runtime_v2 as public_fixture
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


@pytest.fixture(scope="module")
async def captured_public_journal(source_inputs_fixture, tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        _, directory, harness, publications = public_fixture.setup(
            patch, source_inputs_fixture[0]
        )
        result = await public_fixture.invoke(tmp_path_factory.mktemp("v4-public"))
        assert result.packet is not None and result.journal_sha256 is not None
        harness.assert_closed()
        assert not publications
        public_fixture.empty_registries()
        return result, dict(directory.content)


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


def _publish_synthetic_artifacts(
    root, expected, receipt, public_journal, account_session
):
    """Actual G12, public journal and sidecar bytes for the synthetic V3 double."""
    value = decode(receipt.receipt_json)
    public_result, public_files = public_journal
    public_directory = root / "public"
    for name, payload in public_files.items():
        (public_directory / name).write_bytes(payload)
    value["public_packet_sha256"] = public_result.packet.bundle_sha256
    value["public_journal_sha256"] = public_result.journal_sha256
    image_buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (20, 30, 40)).save(image_buffer, format="PNG")
    png = image_buffer.getvalue()
    images = {name: png for name in evidence_storage.PNG_NAMES}
    report = {
        "schema_version": "ctcc.trade_evidence.v1",
        "report_id": expected["report_id"],
        "snapshot": {
            "report_id": expected["report_id"],
            "instrument_id": expected["instrument_id"],
            "direction": expected["direction"],
            "qualification": {
                "report_id": expected["report_id"],
                "direction": expected["direction"],
                "candidate_entry": expected["original_entry"],
                "stop_loss": expected["original_stop_loss"],
                "take_profit": expected["original_take_profit"],
            },
        },
        "images": {
            name: {"sha256": sha(png), "size_bytes": len(png)}
            for name in evidence_storage.PNG_NAMES
        },
        "execution_authority": False,
        "gate_assessments_verified": False,
        "source_authenticity_verified": False,
    }
    report_raw = canonical(report)
    evidence_directory = root / "g12" / expected["report_id"]
    evidence_directory.mkdir()
    for name, payload in (*images.items(), ("report.json", report_raw)):
        (evidence_directory / name).write_bytes(payload)
    value["g12_report_sha256"] = sha(report_raw)
    recheck = {
        "schema_version": public_recheck._SCHEMA,
        "code": value["public_only_recheck_code"],
        "public_join_sha256": "a" * 64,
        "public_packet_sha256": value["public_packet_sha256"],
        "origin_sha256": "b" * 64,
        "candidate_sha256": "c" * 64,
        "original_event_key": expected["original_event_key"],
        "report_id": expected["report_id"],
        "instrument_id": expected["instrument_id"],
        "direction": expected["direction"],
        "original_entry": expected["original_entry"],
        "original_stop_loss": expected["original_stop_loss"],
        "original_take_profit": expected["original_take_profit"],
        "executable_reference": expected["original_entry"],
        "quote_v2_packet_sha256": "d" * 64,
        "observed_at": value["public_only_recheck_observed_at"],
        "g1": {"passed": True},
        "current_g2_g4": {"passed": True},
        "original_event_zone": {"zone_code": "passed"},
        "projected_economics": {
            "candidate": {"code": "passed"},
            "execution": {"code": "passed"},
        },
        "admission": "DENY",
        **{name: False for name in public_recheck._FALSE_FIELDS},
    }
    recheck_raw = canonical(recheck)
    public_recheck.PostG12PublicRecheckReceiptV2(recheck_raw)
    (root / "recheck" / "receipt.json").write_bytes(recheck_raw)
    value["public_only_recheck_sha256"] = sha(recheck_raw)
    account_directory = root / "account"
    account_directory.mkdir(exist_ok=True)
    binding = sha(account_session._plan.session_binding_id.encode("ascii"))
    history_reference = {
        "capture_id": HISTORY_ID,
        "head_sha256": "2" * 64,
        "plan_sha256": "3" * 64,
        "packet_sha256": "4" * 64,
        "session_binding_sha256": binding,
    }
    current_reference = {
        "capture_id": source_fixture.CURRENT_ID,
        "head_sha256": "5" * 64,
        "plan_sha256": expected["account_plan_sha256"],
        "packet_sha256": "6" * 64,
        "session_binding_sha256": binding,
    }
    first_http = joined._utc(value["account_session_first_http_started_at"])
    persist = first_http + timedelta(milliseconds=10)
    complete = persist + timedelta(milliseconds=10)
    parts = {name: b"{}" for name in native_proof.FILES}
    proof_doc = {
        "schema_version": native_proof.V7_FLAT_SCHEMA,
        "policy_sha256": native_proof.V7_FLAT_POLICY_SHA256,
        "scope_sha256": expected["account_scope_sha256"],
        "source_reference": current_reference,
        "clock_file_sha256": {name: sha(raw) for name, raw in parts.items()},
        "proof_persist_start": {
            "utc_ns": int(persist.timestamp() * 1_000_000_000),
            "monotonic_ns": 100_000_000,
        },
        "expires_at": (expected["deadline"] + timedelta(seconds=10)).isoformat(),
        "historical_hwm_clock_verified": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    proof_raw = canonical(proof_doc)
    identity = native_storage._root_identity(account_directory)
    readback_raw = canonical(
        {
            "schema_version": native_storage.V7_FLAT_READBACK_SCHEMA,
            "proof_sha256": sha(proof_raw),
            "clock_file_sha256": {name: sha(raw) for name, raw in parts.items()},
            "root_device": identity[0],
            "root_inode": identity[1],
            "readback_complete": {
                "utc_ns": int(complete.timestamp() * 1_000_000_000),
                "monotonic_ns": 110_000_000,
            },
            "execution_authority": False,
        }
    )
    for name, raw in (
        *parts.items(),
        ("proof.json", proof_raw),
        ("readback.json", readback_raw),
    ):
        (account_directory / name).write_bytes(raw)
    value["native_account_proof_sha256"] = sha(proof_raw)
    value["native_account_readback_sha256"] = sha(readback_raw)
    value["history_db_chain_sha256"] = "8" * 64
    value["history_reference_sha256"] = sha(canonical(history_reference))
    value["current_reference_sha256"] = sha(canonical(current_reference))
    return joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(canonical(value))


def _locked_account_receipt(root, expected, receipt):
    value = decode(receipt.receipt_json)
    current_reference = decode((root / "account" / "proof.json").read_bytes())[
        "source_reference"
    ]
    history_reference = {
        **current_reference,
        "capture_id": HISTORY_ID,
        "head_sha256": "2" * 64,
        "plan_sha256": "3" * 64,
        "packet_sha256": "4" * 64,
    }
    observed = joined._utc(value["observed_at"]) + timedelta(milliseconds=100)
    received = observed + timedelta(milliseconds=100)
    return {
        "schema_version": "ctcc.demo_account_locked_source_join.v4",
        "join_receipt_sha256": "1" * 64,
        "history_source_reference": history_reference,
        "current_source_reference": current_reference,
        "scope_sha256": expected["account_scope_sha256"],
        "session_binding_sha256": current_reference["session_binding_sha256"],
        "recorded_local_checkpoint_sha256": "2" * 64,
        "db_local_state_sha256": "2" * 64,
        "db_account_revision": 0,
        "db_ledger_revision": 0,
        "db_active_hold_count": 0,
        "readback_observed_at": observed.isoformat(),
        "readback_received_at": received.isoformat(),
        "recorded_pre_lock_blocking_reasons": [
            "current_local_revision_readback_required",
            "history_tail_not_atomically_closed",
        ],
        "locked_readback_blocking_reasons": ["history_tail_not_atomically_closed"],
        "local_revision_readback_verified": True,
        "exchange_atomic_revision_verified": False,
        "history_tail_closed": False,
        "snapshot": None,
        "account_complete": False,
        "account_revision_published": False,
        "execution_authority": False,
        "admission": "DENY",
        "join_policy_sha256": account_join.V7_ORDERED_POLICY_SHA256,
        "current_source_policy_sha256": current_source.V7_POLICY_SHA256,
        "flat_start_permission": False,
        "history_journal_terminal_db_recorded_at": (
            joined._utc(value["account_history_readback_started_at"])
            - timedelta(milliseconds=1)
        ).isoformat(),
        "history_original_db_chain_sha256": value["history_db_chain_sha256"],
        "current_capture_started_at": (
            joined._utc(value["account_session_first_http_started_at"])
            + timedelta(milliseconds=1)
        ).isoformat(),
        "history_terminal_db_timestamp_before_current_request": True,
        "history_commit_before_current_request": False,
    }


def test_native_companion_rejects_rehashed_clock_against_original_proof(
    source_inputs_fixture, captured_public_journal, tmp_path
):
    source, values, run = source_inputs_fixture
    session = _session()
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    expected = bridge._original_binding(
        source.market, run, values, session, scope, HISTORY_ID
    )
    for name in ("g12", "public", "recheck", "account"):
        (tmp_path / name).mkdir()
    receipt = _publish_synthetic_artifacts(
        tmp_path, expected, _receipt(expected), captured_public_journal, session
    )
    record = decode(receipt.receipt_json)
    account_root = tmp_path / "account"
    proof_doc = decode((account_root / "proof.json").read_bytes())
    part_name = native_proof.FILES[0]
    (account_root / part_name).write_bytes(b"changed")
    readback_path = account_root / "readback.json"
    readback = decode(readback_path.read_bytes())
    readback["clock_file_sha256"][part_name] = sha(b"changed")
    readback_path.write_bytes(canonical(readback))
    with pytest.raises(
        joined.PostG12AccountHistoryJoinError,
        match="post_g12_history_account_companion_invalid",
    ):
        joined._account_companion_pins(
            account_root,
            current_reference=proof_doc["source_reference"],
            scope_sha256=expected["account_scope_sha256"],
            first_http_at=joined._utc(record["account_session_first_http_started_at"]),
        )


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
        ("g12_png_changed", "v3_artifact_readback_invalid", 0),
        ("g12_report_missing", "v3_artifact_readback_invalid", 0),
        ("g12_rehashed_entry", "v3_artifact_readback_invalid", 0),
        ("recheck_changed", "v3_artifact_readback_invalid", 0),
        ("recheck_missing", "v3_artifact_readback_invalid", 0),
        ("recheck_rehashed_event", "v3_artifact_readback_invalid", 0),
        ("recheck_rehashed_time", "v3_artifact_readback_invalid", 0),
        ("public_raw_changed", "v3_artifact_readback_invalid", 0),
        ("public_summary_missing", "v3_artifact_readback_invalid", 0),
        ("public_root_replaced", "v3_artifact_readback_invalid", 0),
        ("account_proof_changed", "account_readback_invalid", 0),
        ("account_readback_changed", "account_readback_invalid", 0),
        ("account_clock_changed", "account_readback_invalid", 0),
        ("account_root_replaced", "account_readback_invalid", 0),
        ("history_chain_changed", "account_readback_invalid", 0),
        ("history_timestamp_changed", "account_readback_invalid", 0),
        ("current_chain_changed", "account_readback_invalid", 0),
        ("current_capture_changed", "account_readback_invalid", 0),
        ("account_authority_true", "account_readback_invalid", 0),
        ("account_db_unavailable", "account_readback_invalid", 0),
        ("account_db_late", "account_readback_invalid", 0),
    ),
)
async def test_v4_one_invocation_v3_readback_then_uid_event_only(
    source_inputs_fixture,
    captured_public_journal,
    tmp_path,
    monkeypatch,
    fault,
    code,
    read_count,
):
    source, values, run = source_inputs_fixture
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    session = _session()
    expected = bridge._original_binding(
        source.market, run, values, session, scope, HISTORY_ID
    )
    (tmp_path / "g12").mkdir()
    (tmp_path / "public").mkdir()
    (tmp_path / "account").mkdir()
    (tmp_path / "recheck").mkdir()
    (tmp_path / "join").mkdir()
    (tmp_path / "event").mkdir()
    ledger = QualificationLedgerRepository(None, clock=lambda: expected["deadline"])
    calls = []
    observations = []
    published_receipt = []
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
        result = _publish_synthetic_artifacts(
            tmp_path,
            expected,
            _receipt(expected, changed=changed),
            captured_public_journal,
            session,
        )
        if fault in {
            "g12_rehashed_entry",
            "recheck_rehashed_event",
            "recheck_rehashed_time",
        }:
            v3 = decode(result.receipt_json)
            if fault == "g12_rehashed_entry":
                report_path = tmp_path / "g12" / expected["report_id"] / "report.json"
                report = decode(report_path.read_bytes())
                report["snapshot"]["qualification"]["candidate_entry"] = "12345"
                raw = canonical(report)
                report_path.write_bytes(raw)
                v3["g12_report_sha256"] = sha(raw)
            else:
                recheck_path = tmp_path / "recheck" / "receipt.json"
                recheck = decode(recheck_path.read_bytes())
                if fault == "recheck_rehashed_event":
                    recheck["original_event_key"] = "f" * 64
                else:
                    recheck["observed_at"] = (
                        expected["deadline"] - timedelta(milliseconds=700)
                    ).isoformat()
                raw = canonical(recheck)
                recheck_path.write_bytes(raw)
                v3["public_only_recheck_sha256"] = sha(raw)
            result = joined.PostG12OwnedPublicAccountHistoryDiagnosticV3(canonical(v3))
        if fault != "session_unconsumed":
            session._used = True
        identity = joined.source_runtime._native_recheck_root_identity(
            tmp_path / "join"
        )
        joined._publish_receipt(
            tmp_path / "join", result, expected_root_identity=identity
        )
        if fault == "g12_png_changed":
            (tmp_path / "g12" / expected["report_id"] / "4h.png").write_bytes(b"bad")
        if fault == "g12_report_missing":
            (tmp_path / "g12" / expected["report_id"] / "report.json").unlink()
        if fault == "recheck_changed":
            (tmp_path / "recheck" / "receipt.json").write_bytes(b"{}")
        if fault == "recheck_missing":
            (tmp_path / "recheck" / "receipt.json").unlink()
        if fault == "public_raw_changed":
            raw_path = next((tmp_path / "public").glob("event-*.raw"))
            raw = raw_path.read_bytes()
            raw_path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        if fault == "public_summary_missing":
            (tmp_path / "public" / "summary.json").unlink()
        if fault == "public_root_replaced":
            public_root = tmp_path / "public"
            public_root.rename(tmp_path / "retired-public")
            public_root.mkdir()
            for name, payload in captured_public_journal[1].items():
                (public_root / name).write_bytes(payload)
        if fault == "account_proof_changed":
            (tmp_path / "account" / "proof.json").write_bytes(b"{}")
        if fault == "account_readback_changed":
            (tmp_path / "account" / "readback.json").write_bytes(b"{}")
        if fault == "account_clock_changed":
            first_part = native_proof.FILES[0]
            (tmp_path / "account" / first_part).write_bytes(b"changed")
        if fault == "account_root_replaced":
            account_root = tmp_path / "account"
            account_root.rename(tmp_path / "retired-account")
            account_root.mkdir()
            for file in (tmp_path / "retired-account").iterdir():
                (account_root / file.name).write_bytes(file.read_bytes())
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
        published_receipt.append(result)
        return result

    async def read_event(read_scope, key):
        assert asyncio.current_task() is task
        assert calls == ["new_v3", "uid_account_read"]
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

    class SyntheticAccountRepository:
        def __init__(self, factory, *, clock):
            assert factory is ledger.session_factory and clock is ledger.clock

        async def read_locked_current_history_join(
            self,
            read_scope,
            *,
            history_capture_id,
            current_capture_id,
            expected_policy_sha256,
        ):
            assert read_scope == scope
            assert history_capture_id == HISTORY_ID
            assert current_capture_id == source_fixture.CURRENT_ID
            assert expected_policy_sha256 == account_join.V7_ORDERED_POLICY_SHA256
            calls.append("uid_account_read")
            if fault == "account_db_unavailable":
                raise RuntimeError("private account SQL details remain redacted")
            record = _locked_account_receipt(tmp_path, expected, published_receipt[0])
            if fault == "history_chain_changed":
                record["history_source_reference"]["packet_sha256"] = "7" * 64
            if fault == "history_timestamp_changed":
                record["history_original_db_chain_sha256"] = "7" * 64
            if fault == "current_chain_changed":
                record["current_source_reference"]["packet_sha256"] = "7" * 64
            if fault == "current_capture_changed":
                record["current_source_reference"]["capture_id"] = "c" * 32
            if fault == "account_authority_true":
                record["execution_authority"] = True
            if fault == "account_db_late":
                record["readback_received_at"] = expected["deadline"].isoformat()
            return LockedAccountSourceJoinReadback(canonical(record))

    monkeypatch.setattr(joined, "publish_capture_public_account_history_v3", issue)
    monkeypatch.setattr(
        bridge, "AccountCaptureJournalRepository", SyntheticAccountRepository
    )
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
        session_factory=ledger.session_factory,
        history_capture_id=HISTORY_ID,
        ledger=ledger,
        scope=scope,
    )
    assert result.code == code
    expected_account_read = code not in {
        "v3_binding_invalid",
        "v3_stale",
        "v3_incomplete",
        "v3_artifact_readback_invalid",
    } and fault not in {
        "account_proof_changed",
        "account_readback_changed",
        "account_clock_changed",
        "account_root_replaced",
    }
    assert calls == [
        "new_v3",
        *("uid_account_read",) * expected_account_read,
        *("uid_event_read",) * read_count,
    ]
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
            session_factory=ledger.session_factory,
            history_capture_id=HISTORY_ID,
            ledger=ledger,
            scope=scope,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_v4_rejects_different_account_and_event_database_factories(
    source_inputs_fixture, tmp_path
):
    source, values, run = source_inputs_fixture
    ledger = QualificationLedgerRepository(None, clock=lambda: run.evaluated_at)
    with pytest.raises(
        bridge.PostG12AccountEventObservationError,
        match="v4_account_database_mismatch",
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
            scope=LedgerScope(account_id=UID, settlement_currency="USDT"),
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
            session_factory=ledger.session_factory,
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
            session_factory=ledger.session_factory,
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
