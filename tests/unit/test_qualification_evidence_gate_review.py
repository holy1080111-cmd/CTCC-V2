"""Independent G12 barriers; synthetic contract receipts are NOT disk evidence.

Every G1–G11 run and baseline snapshot/PNG comes from the real source evaluators
and renderer. Publisher doubles below perform no filesystem IO and validate only
the caller/publisher contract. Native durable publication needs separate tests.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.trade_evidence import gates as module
from app.trade_evidence.gates import EvidenceGateError, EvidenceGateRun
from app.trade_evidence.storage import (
    FILE_NAMES,
    EvidencePublicationError,
    PublicationReceipt,
    PublishedFile,
)
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.models import QualificationState
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.qualification_prefix_fixtures import prefix_source

D = Decimal


class Clock:
    def __init__(self, at, values=None):
        self.values = (
            values
            if values is not None
            else [at + timedelta(milliseconds=i) for i in (1, 2, 3)]
        )
        self.calls = 0

    def __call__(self):
        assert self.calls < len(self.values), "unexpected extra clock observation"
        value = self.values[self.calls]
        self.calls += 1
        return value


def forbidden(*args, **kwargs):
    pytest.fail("evidence work crossed the prior failure barrier")


def contract_receipt(root, report_id, files, completed_at):
    """Fictional receipt of the bytes contract; no directory is opened or written."""
    records = tuple(
        PublishedFile(
            name=name,
            sha256=hashlib.sha256(files[name]).hexdigest(),
            size_bytes=len(files[name]),
        )
        for name in FILE_NAMES
    )
    return PublicationReceipt(
        status="written",
        report_id=report_id,
        report_directory=str(root / report_id),
        report_sha256=records[-1].sha256,
        files=records,
        completed_at=completed_at,
    )


def contract_publisher(root, *, report_id, files, clock):
    clock()
    return contract_receipt(root, report_id, files, clock())


@dataclass(frozen=True)
class ContractCase:
    source: object
    inputs: dict
    pre: object
    result: EvidenceGateRun
    files: object
    root: Path


@pytest.fixture(scope="module")
def case():
    source = engine_source()
    inputs = engine_inputs(source)
    pre = evaluate_pre_evidence(source.market, **inputs)
    root = Path.cwd() / "synthetic-contract-only-never-created"
    capture = {}
    real_render = module.render_evidence

    def render(snapshot):
        capture["files"] = real_render(snapshot)
        return capture["files"]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "render_evidence", render)
        patch.setattr(module, "publish_evidence", contract_publisher)
        result = module.publish_qualification_evidence(
            root,
            source.market,
            run=pre,
            purpose="synthetic_test",
            clock=Clock(source.evaluated_at),
            **inputs,
        )
    assert result.result.evidence_complete
    return ContractCase(source, inputs, pre, result, capture["files"], root)


def reuse_real_packet(monkeypatch, case):
    def render(snapshot):
        assert snapshot == case.result.snapshot
        return case.files

    monkeypatch.setattr(module, "render_evidence", render)


def invoke(case, *, clock=None, **updates):
    return module.publish_qualification_evidence(
        case.root,
        case.source.market,
        run=case.pre,
        clock=clock or Clock(case.source.evaluated_at),
        **({"purpose": "synthetic_test", **case.inputs} | updates),
    )


def test_contract_baseline_pins_real_six_bytes_but_does_not_claim_native_publication(
    case,
):
    result = case.result
    assert result.result.state == QualificationState.EVIDENCE_COMPLETE
    assert len(result.result.gates) == 12
    assert result.result.gates[:11] == case.pre.result.gates
    assert not result.result.qualified and not result.execution_authority
    assert not result.execution_recheck_performed and not result.atomic_risk_reserved
    assert not result.account_evidence_authenticated
    assert tuple(record.name for record in result.rendered_files) == FILE_NAMES
    assert result.receipt.files == result.rendered_files
    for record in result.rendered_files:
        assert record.sha256 == hashlib.sha256(case.files[record.name]).hexdigest()
        assert record.size_bytes == len(case.files[record.name])
    report = json.loads(case.files["report.json"])
    assert report["snapshot"] == result.snapshot.model_dump(
        mode="json", round_trip=True
    )
    assert len(report["snapshot"]["qualification"]["gates"]) == 11
    assert report["snapshot"]["evidence_gate"] == "not_evaluated"
    assert result.snapshot.gate_assessments_verified is False
    restored = EvidenceGateRun.model_validate_json(
        result.model_dump_json(round_trip=True), strict=True
    )
    assert restored == result and restored.evaluation_sha256 == result.evaluation_sha256


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("gate", [3, 4, 5, 7, 8])
def test_real_remaining_first_failures_do_not_touch_root_clock_or_evidence(
    monkeypatch, direction, gate
):
    source = prefix_source(direction) if gate == 8 else engine_source(direction)
    args = engine_inputs(source)
    if gate == 3:
        args["intent"] = args["intent"].model_copy(
            update={"direction": "short" if direction == "long" else "long"}
        )
    elif gate in (4, 5):
        index, price = (-1, D("100.79")) if gate == 4 else (-2, D("100.89"))
        if direction == "short":
            price = D(200) - price
        rows = source.market.candles["5m"]
        rows[index] = rows[index].model_copy(
            update={
                "open": price,
                "close": price,
                "high": price + D("0.01"),
                "low": price - D("0.01"),
            }
        )
    elif gate == 7:
        args["intent"] = args["intent"].model_copy(
            update={"candidate_entry": D(102) if direction == "long" else D(98)}
        )
    failed = evaluate_pre_evidence(source.market, **args)
    assert len(failed.result.gates) == gate and not failed.pre_evidence_complete
    assert all(g.passed for g in failed.result.gates[:-1])
    if gate == 5:
        assert failed.result.effective_score == 100
    for name in ("prepare_evidence", "render_evidence", "publish_evidence"):
        monkeypatch.setattr(module, name, forbidden)

    class UnopenedRoot:
        __fspath__ = __str__ = __truediv__ = forbidden

    result = module.publish_qualification_evidence(
        UnopenedRoot(),
        source.market,
        run=failed,
        clock=forbidden,
        purpose="synthetic_test",
        **args,
    )
    assert result.result == failed.result
    assert result.snapshot is result.receipt is result.preparation_started_at is None
    assert result.rendered_files == ()
    assert not result.execution_authority


def test_freshly_rehashed_stored_audit_cannot_replace_real_eleven_gate_replay(
    case, monkeypatch
):
    audit = json.loads(case.pre.protection_audit_json)
    audit["untrusted_annotation"] = "self-signed checksums are not evaluator provenance"
    raw = json.dumps(audit, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    forged = case.pre.model_copy(
        update={
            "protection_audit_json": raw,
            "protection_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        }
    )
    assert forged.evaluation_sha256 != case.pre.evaluation_sha256
    for name in ("prepare_evidence", "render_evidence", "publish_evidence"):
        monkeypatch.setattr(module, name, forbidden)
    with pytest.raises(EvidenceGateError, match="pre_evidence_replay_failed"):
        module.publish_qualification_evidence(
            case.root,
            case.source.market,
            run=forged,
            clock=forbidden,
            purpose="synthetic_test",
            **case.inputs,
        )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_snapshot",
        "other_source",
        "changed_stop",
        "claimed_g12",
        "different_preparation",
        "not_object",
        "deep_json",
    ],
)
def test_report_snapshot_mismatch_is_detected_before_any_publisher_call(
    case, monkeypatch, defect
):
    packet = dict(case.files)
    report = json.loads(packet["report.json"])
    if defect == "missing_snapshot":
        del report["snapshot"]
    elif defect == "other_source":
        report["snapshot"]["source_sha256"] = "0" * 64
    elif defect == "changed_stop":
        report["snapshot"]["qualification"]["stop_loss"] = "100.5"
    elif defect == "claimed_g12":
        report["snapshot"]["evidence_gate"] = "passed"
    elif defect == "different_preparation":
        report["snapshot"]["prepared_at"] = (
            case.result.preparation_started_at + timedelta(microseconds=1)
        ).isoformat()
    elif defect == "not_object":
        report = [report]
    packet["report.json"] = (
        ("[" * 1500 + "0" + "]" * 1500).encode()
        if defect == "deep_json"
        else json.dumps(report).encode()
    )
    monkeypatch.setattr(module, "render_evidence", lambda snapshot: packet)
    monkeypatch.setattr(module, "publish_evidence", forbidden)
    result = invoke(case)
    assert result.result.gates[-1].code == "evidence_render_failed"
    assert result.snapshot == case.result.snapshot
    assert result.receipt is None and result.rendered_files == ()
    assert not result.result.evidence_complete


@pytest.mark.parametrize("filename", FILE_NAMES)
@pytest.mark.parametrize("defect", ["missing", "mutable", "empty"])
def test_every_required_packet_member_is_validated_before_publication(
    case, monkeypatch, filename, defect
):
    packet = dict(case.files)
    if defect == "missing":
        del packet[filename]
    else:
        packet[filename] = bytearray(packet[filename]) if defect == "mutable" else b""
    monkeypatch.setattr(module, "render_evidence", lambda snapshot: packet)
    monkeypatch.setattr(module, "publish_evidence", forbidden)
    result = invoke(case)
    assert result.result.gates[-1].code == "evidence_render_failed"
    assert result.receipt is None and result.rendered_files == ()


@pytest.mark.parametrize("filename", FILE_NAMES)
def test_receipt_cannot_substitute_any_of_the_six_rendered_bytes(
    case, monkeypatch, filename
):
    reuse_real_packet(monkeypatch, case)

    def publisher(root, *, report_id, files, clock):
        clock()
        completed = clock()
        altered = dict(files)
        altered[filename] += b"altered after rendering"
        return contract_receipt(root, report_id, altered, completed)

    monkeypatch.setattr(module, "publish_evidence", publisher)
    result = invoke(case)
    assert result.result.gates[-1].code == "evidence_publication_invalid"
    assert result.receipt is None and len(result.rendered_files) == 6
    assert result.snapshot == case.result.snapshot


@pytest.mark.parametrize("observations", [0, 1, 3])
def test_old_receipt_with_missing_or_extra_current_clock_observations_is_rejected(
    case, monkeypatch, observations
):
    reuse_real_packet(monkeypatch, case)

    def old_receipt(*args, clock, **kwargs):
        for _ in range(observations):
            clock()
        return case.result.receipt

    monkeypatch.setattr(module, "publish_evidence", old_receipt)
    at = case.source.evaluated_at
    result = invoke(
        case, clock=Clock(at, [at + timedelta(milliseconds=i) for i in (1, 2, 3, 4)])
    )
    assert result.result.gates[-1].code == "evidence_publication_invalid"
    assert result.receipt is None


def test_old_receipt_cannot_be_retitled_as_the_current_completion(case, monkeypatch):
    reuse_real_packet(monkeypatch, case)

    def old_receipt(*args, clock, **kwargs):
        clock()
        clock()
        return case.result.receipt

    monkeypatch.setattr(module, "publish_evidence", old_receipt)
    at = case.source.evaluated_at
    result = invoke(
        case, clock=Clock(at, [at + timedelta(milliseconds=i) for i in (1, 10, 11)])
    )
    assert result.result.gates[-1].code == "evidence_publication_invalid"
    assert result.receipt is None and not result.result.evidence_complete


def test_a_twelve_gate_result_or_receipt_is_not_an_input_to_the_one_shot_api(
    case, monkeypatch
):
    for name in ("prepare_evidence", "render_evidence", "publish_evidence"):
        monkeypatch.setattr(module, name, forbidden)
    for wrong_run in (case.result, case.result.result, case.result.receipt):
        with pytest.raises(EvidenceGateError, match="pre_evidence_replay_failed"):
            module.publish_qualification_evidence(
                case.root,
                case.source.market,
                run=wrong_run,
                clock=forbidden,
                purpose="synthetic_test",
                **case.inputs,
            )


@pytest.mark.parametrize("phase", ["before_open", "after_readback"])
def test_wrapped_clock_failure_keeps_its_identity_and_never_claims_receipt(
    case, monkeypatch, phase
):
    reuse_real_packet(monkeypatch, case)
    simulated_writes = []

    def publisher(*args, clock, **kwargs):
        try:
            clock()
            simulated_writes.append("contract-only-no-actual-write")
            clock()
        except Exception as exc:
            raise EvidencePublicationError("synthetic wrapped clock failure") from exc
        pytest.fail("invalid clock should not produce a receipt")

    monkeypatch.setattr(module, "publish_evidence", publisher)
    at = case.source.evaluated_at
    moments = [at + timedelta(milliseconds=i) for i in (1, 2, 3)]
    moments[1 if phase == "before_open" else 2] = at
    result = invoke(case, clock=Clock(at, moments))
    assert result.result.gates[-1].code == "evidence_clock_invalid"
    assert result.receipt is None
    assert bool(simulated_writes) == (phase == "after_readback")


def test_publication_expiry_boundary_is_exclusive_but_preserves_contract_receipt(
    case, monkeypatch
):
    reuse_real_packet(monkeypatch, case)
    monkeypatch.setattr(module, "publish_evidence", contract_publisher)
    deadline = min(
        case.pre.prefix.intent.expires_at,
        case.pre.prefix.detection.trigger.expires_at,
        case.pre.result.entry_zone.expires_at,
        case.pre.prefix.timing.latest_valid_entry_time,
    )
    at = case.source.evaluated_at
    for moment, expected in (
        (deadline - timedelta(microseconds=1), "passed"),
        (deadline, "evidence_entry_expired"),
    ):
        result = invoke(
            case,
            clock=Clock(
                at,
                [
                    at + timedelta(milliseconds=1),
                    at + timedelta(milliseconds=2),
                    moment,
                ],
            ),
        )
        assert result.result.gates[-1].code == expected
        assert result.receipt.completed_at == moment
        assert result.result.candidate_entry == case.pre.result.candidate_entry
        assert result.result.stop_loss == case.pre.result.stop_loss
        assert result.result.take_profit == case.pre.result.take_profit
        assert not result.execution_recheck_performed and not result.execution_authority
