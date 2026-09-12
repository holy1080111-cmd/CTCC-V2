"""Real G1--G11 replay plus G12 barriers; no exchange/account observations.

Native publication cases run only on POSIX. Synthetic publisher-contract cases
exercise receipt validation, never claim native IO success or Windows pin proof.
"""

import hashlib
import json
import os
from datetime import timedelta, timezone
from decimal import Context, Decimal, Inexact, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError, create_model

from app.trade_evidence import gates as module
from app.trade_evidence.gates import (
    EvidenceGateError,
    EvidenceGateRun,
    publish_qualification_evidence,
)
from app.trade_evidence.storage import (
    FILE_NAMES,
    EvidencePublicationError,
    PublicationReceipt,
    PublishedFile,
)
from app.trade_qualification.engine import evaluate_pre_evidence
from app.trade_qualification.models import QualificationGate
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source

D = Decimal
FLAGS = (
    "execution_authority",
    "source_authenticity_verified",
    "account_evidence_authenticated",
    "atomic_risk_reserved",
    "execution_recheck_performed",
)
NATIVE_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows full ancestor-pin publication is not verified by POSIX tests",
)


@pytest.fixture(scope="module", params=("long", "short"))
def source(request):
    return engine_source(request.param)


@pytest.fixture(scope="module")
def inputs(source):
    return engine_inputs(source)


@pytest.fixture(scope="module")
def pre_evidence(source, inputs):
    result = evaluate_pre_evidence(source.market, **inputs)
    assert result.pre_evidence_complete, result.result.fail_codes
    return result


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "trusted-evidence-root"
    directory.mkdir()
    return directory


class Clock:
    def __init__(self, at, moments=None):
        self.moments = (
            list(moments)
            if moments is not None
            else [at + timedelta(milliseconds=index) for index in (1, 2, 3)]
        )
        self.calls = []

    def __call__(self):
        if len(self.calls) >= len(self.moments):
            pytest.fail("unexpected extra publication clock call")
        value = self.moments[len(self.calls)]
        self.calls.append(value)
        if isinstance(value, Exception):
            raise value
        return value


def forbidden(*_args, **_kwargs):
    pytest.fail("an evidence side effect crossed an earlier failure barrier")


def forbid(monkeypatch, *names):
    for name in names:
        monkeypatch.setattr(module, name, forbidden)


def publish(source, inputs, run, root, *, clock=None, market=None, **updates):
    arguments = {"purpose": "synthetic_test", **inputs, **updates}
    return publish_qualification_evidence(
        root,
        source.market if market is None else market,
        run=run,
        clock=Clock(source.evaluated_at) if clock is None else clock,
        **arguments,
    )


def assert_no_authority(result):
    assert all(getattr(result, field) is False for field in FLAGS)
    assert not result.result.qualified
    assert not result.result.execution_recheck_passed
    if result.snapshot is not None:
        assert not result.snapshot.execution_authority
        assert not result.snapshot.source_authenticity_verified
        assert not result.snapshot.gate_assessments_verified
        assert result.snapshot.evidence_gate == "not_evaluated"
        assert result.snapshot.execution_recheck == "not_evaluated"
    if result.receipt is not None:
        assert not result.receipt.execution_authority


def assert_g12(result, pre_evidence, code):
    assert result.pre_evidence == pre_evidence
    assert result.pre_evidence_sha256 == pre_evidence.evaluation_sha256
    assert result.result.gates[:-1] == pre_evidence.result.gates
    assert tuple(g.gate for g in result.result.gates) == tuple(QualificationGate)[:12]
    assert result.result.gates[-1].code == code
    assert result.result.gates[-1].passed == (code == "passed")
    assert len(pre_evidence.result.gates) == 11
    assert not pre_evidence.result.evidence_complete
    assert_no_authority(result)


def deadline(pre_evidence):
    prefix = pre_evidence.prefix
    return min(
        prefix.intent.expires_at,
        prefix.result.trigger.expires_at,
        prefix.result.entry_zone.expires_at,
        prefix.timing.latest_valid_entry_time,
    )


@pytest.mark.parametrize(
    "failure", ["g1", "g2", "g3", "g4", "g6", "g7", "g8", "g10", "g11"]
)
def test_real_earlier_failure_keeps_original_result_without_any_evidence_io(
    source, inputs, pre_evidence, monkeypatch, failure
):
    altered = dict(inputs)
    market = source.market
    if failure == "g1":
        altered["quote"] = None
    elif failure == "g2":
        altered["intent"] = inputs["intent"].model_copy(
            update={"strategy": "liquidity_sweep_reversal"}
        )
    elif failure == "g3":
        altered["intent"] = inputs["intent"].model_copy(
            update={"direction": "short" if source.direction == "long" else "long"}
        )
    elif failure == "g4":
        rows = [
            row.model_copy(
                update={
                    "open": D(100),
                    "close": D(100),
                    "high": D("100.1"),
                    "low": D("99.9"),
                }
            )
            for row in market.candles["15m"]
        ]
        market = market.model_copy(update={"candles": {**market.candles, "15m": rows}})
    elif failure == "g6":
        altered["consumed_event_keys"] = frozenset(
            {pre_evidence.prefix.timing.event_key}
        )
    elif failure == "g7":
        zone = pre_evidence.result.entry_zone
        candidate = (
            zone.zone_high + D(1)
            if source.direction == "long"
            else zone.zone_low - D(1)
        )
        altered["intent"] = inputs["intent"].model_copy(
            update={"candidate_entry": candidate}
        )
    elif failure == "g8":
        protection = inputs["policy"].protection.model_copy(
            update={"min_stop_distance_atr": D(9999)}
        )
        altered["policy"] = inputs["policy"].model_copy(
            update={"protection": protection}
        )
    elif failure == "g10":
        altered["policy"] = inputs["policy"].model_copy(update={"economics": None})
    else:
        altered["risk_inputs"] = inputs["risk_inputs"].model_copy(
            update={"account": None}
        )
    failed = evaluate_pre_evidence(market, **altered)
    assert not failed.pre_evidence_complete
    assert len(failed.result.gates) == int(failure[1:])
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    result = publish(
        source,
        altered,
        failed,
        Path(source.market.instrument_id),
        clock=forbidden,
        market=market,
    )
    assert result.result == failed.result
    assert result.pre_evidence == failed
    assert result.snapshot is result.snapshot_sha256 is result.receipt is None
    assert_no_authority(result)


@pytest.mark.parametrize("purpose", [None, True, "live", "Synthetic_Test", ""])
def test_invalid_purpose_rejected_before_clock_or_render(
    source, inputs, pre_evidence, monkeypatch, purpose
):
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    with pytest.raises(EvidenceGateError):
        publish(
            source, inputs, pre_evidence, Path.cwd(), purpose=purpose, clock=forbidden
        )


@pytest.mark.parametrize("limit", [None, True, 0, 79, 201, 80.0, "80"])
def test_invalid_candle_limit_rejected_before_clock_or_render(
    source, inputs, pre_evidence, monkeypatch, limit
):
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    with pytest.raises(EvidenceGateError):
        publish(
            source,
            inputs,
            pre_evidence,
            Path.cwd(),
            candle_limit=limit,
            clock=forbidden,
        )


@pytest.mark.parametrize(
    "change", ["clock", "reference", "policy", "ledger", "intent", "risk", "source"]
)
def test_changed_original_inputs_fail_replay_before_evidence_side_effects(
    source, inputs, pre_evidence, monkeypatch, change
):
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    updates = {}
    market = source.market
    if change == "clock":
        updates["evaluated_at"] = source.evaluated_at + timedelta(microseconds=1)
    elif change == "reference":
        updates["reference"] = inputs["reference"].model_copy(
            update={"bid": inputs["reference"].bid - D("0.001")}
        )
    elif change == "policy":
        updates["policy"] = inputs["policy"].model_copy(
            update={"policy_id": "synthetic-other-policy"}
        )
    elif change == "ledger":
        updates["consumed_event_keys"] = frozenset({"a" * 64})
    elif change == "intent":
        updates["intent"] = inputs["intent"].model_copy(
            update={
                "expires_at": inputs["intent"].expires_at + timedelta(microseconds=1)
            }
        )
    elif change == "risk":
        updates["risk_inputs"] = inputs["risk_inputs"].model_copy(
            update={
                "requested_contracts": inputs["risk_inputs"].requested_contracts + D(1)
            }
        )
    else:
        rows = list(market.candles["5m"])
        rows[0] = rows[0].model_copy(
            update={"volume_quote": rows[0].volume_quote + D(1)}
        )
        market = market.model_copy(update={"candles": {**market.candles, "5m": rows}})
    with pytest.raises(EvidenceGateError):
        publish(
            source,
            inputs,
            pre_evidence,
            Path.cwd(),
            market=market,
            clock=forbidden,
            **updates,
        )


def extended(record):
    model = create_model(
        "SyntheticExtended" + type(record).__name__,
        __base__=type(record),
        hidden_declared=(bool, True),
    )
    # Deliberately bypass constructors ONLY for adversarial consumer revalidation.
    # Every actual passing baseline still comes from the real evaluator chain.
    return model.model_construct(**record.__dict__, hidden_declared=True)


@pytest.mark.parametrize(
    "target",
    ["run", "prefix", "policy", "risk_inputs", "result", "economics", "portfolio"],
)
@pytest.mark.parametrize("defect", ["hidden", "subclass"])
def test_dirty_or_extended_run_records_never_reach_evidence(
    source, inputs, pre_evidence, monkeypatch, target, defect
):
    record = pre_evidence if target == "run" else getattr(pre_evidence, target)
    record = (
        record.model_copy(update={"hidden": True})
        if defect == "hidden"
        else extended(record)
    )
    altered = (
        record if target == "run" else pre_evidence.model_copy(update={target: record})
    )
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    with pytest.raises(EvidenceGateError):
        publish(source, inputs, altered, Path.cwd(), clock=forbidden)


@pytest.mark.parametrize(
    "value", [None, "2026-09-12T01:10:00Z", 123, ValueError("synthetic clock failure")]
)
def test_invalid_preparation_clock_never_prepares_or_publishes(
    source, inputs, pre_evidence, monkeypatch, value
):
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    clock = Clock(source.evaluated_at, [value])
    result = publish(source, inputs, pre_evidence, Path.cwd(), clock=clock)
    assert_g12(result, pre_evidence, "evidence_clock_invalid")
    assert result.snapshot is result.receipt is None
    assert len(clock.calls) == 1


@pytest.mark.parametrize(
    "kind", ["naive", "before_evaluation", "at_deadline", "after_deadline"]
)
def test_preparation_time_must_be_causal_and_strictly_before_entry_deadline(
    source, inputs, pre_evidence, monkeypatch, kind
):
    forbid(monkeypatch, "prepare_evidence", "render_evidence", "publish_evidence")
    value = {
        "naive": source.evaluated_at.replace(tzinfo=None),
        "before_evaluation": source.evaluated_at - timedelta(microseconds=1),
        "at_deadline": deadline(pre_evidence),
        "after_deadline": deadline(pre_evidence) + timedelta(microseconds=1),
    }[kind]
    result = publish(
        source,
        inputs,
        pre_evidence,
        Path.cwd(),
        clock=Clock(source.evaluated_at, [value]),
    )
    code = (
        "evidence_entry_expired"
        if kind.endswith("deadline")
        else "evidence_clock_invalid"
    )
    assert_g12(result, pre_evidence, code)
    assert result.snapshot is result.receipt is None


def test_preparation_error_stops_render_and_publication(
    source, inputs, pre_evidence, monkeypatch
):
    def fail(*_args, **_kwargs):
        raise ValueError("synthetic preparation failure")

    monkeypatch.setattr(module, "prepare_evidence", fail)
    forbid(monkeypatch, "render_evidence", "publish_evidence")
    clock = Clock(source.evaluated_at)
    result = publish(source, inputs, pre_evidence, Path.cwd(), clock=clock)
    assert_g12(result, pre_evidence, "evidence_preparation_failed")
    assert result.snapshot is result.receipt is None
    assert len(clock.calls) == 1


def test_render_error_preserves_prepared_snapshot_without_publication(
    source, inputs, pre_evidence, monkeypatch
):
    def fail(*_args, **_kwargs):
        raise ValueError("synthetic rendering failure")

    monkeypatch.setattr(module, "render_evidence", fail)
    forbid(monkeypatch, "publish_evidence")
    clock = Clock(source.evaluated_at)
    result = publish(source, inputs, pre_evidence, Path.cwd(), clock=clock)
    assert_g12(result, pre_evidence, "evidence_render_failed")
    assert result.snapshot is not None and result.receipt is None
    assert result.snapshot.qualification == pre_evidence.result
    assert len(clock.calls) == 1


@pytest.fixture(scope="module")
def rendered(source, inputs, pre_evidence):
    """One real preparation/render; intentionally never invokes native storage."""
    captured = {}
    original = module.render_evidence

    def capture(snapshot):
        captured["snapshot"] = snapshot
        captured["files"] = original(snapshot)
        return captured["files"]

    def no_io(*_args, **_kwargs):
        raise EvidencePublicationError("synthetic capture-only storage refusal")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "render_evidence", capture)
        patch.setattr(module, "publish_evidence", no_io)
        result = publish(source, inputs, pre_evidence, Path.cwd())
    assert_g12(result, pre_evidence, "evidence_publication_failed")
    assert set(captured["files"]) == set(FILE_NAMES)
    return captured


def cached_render(monkeypatch, rendered):
    def reuse(snapshot):
        assert snapshot == rendered["snapshot"]
        return rendered["files"]

    monkeypatch.setattr(module, "render_evidence", reuse)


def synthetic_publisher(
    *, mutate=None, status="written", clock_calls=2, fallback_time=None
):
    """Receipt-contract test double, NOT evidence of any native filesystem IO."""

    def publish(root, *, report_id, files, clock):
        samples = [clock() for _ in range(clock_calls)]
        completed = samples[-1] if samples else fallback_time
        records = tuple(
            PublishedFile(
                name=name,
                sha256=hashlib.sha256(files[name]).hexdigest(),
                size_bytes=len(files[name]),
            )
            for name in FILE_NAMES
        )
        receipt = PublicationReceipt(
            status=status,
            report_id=report_id,
            report_directory=str(root / report_id),
            report_sha256=records[-1].sha256,
            files=records,
            completed_at=completed,
        )
        return mutate(receipt) if mutate else receipt

    return publish


@pytest.mark.parametrize("clock_calls", [0, 1, 3])
def test_synthetic_receipt_requires_exactly_two_publisher_clock_samples(
    source, inputs, pre_evidence, rendered, root, monkeypatch, clock_calls
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(
        module,
        "publish_evidence",
        synthetic_publisher(clock_calls=clock_calls, fallback_time=source.evaluated_at),
    )
    moments = [source.evaluated_at + timedelta(milliseconds=i) for i in (1, 2, 3, 4)]
    result = publish(
        source, inputs, pre_evidence, root, clock=Clock(source.evaluated_at, moments)
    )
    assert_g12(result, pre_evidence, "evidence_publication_invalid")
    assert result.receipt is None


def test_synthetic_publisher_contract_pins_twelve_gate_result_but_packet_keeps_eleven(
    source, inputs, pre_evidence, rendered, root, monkeypatch
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher())
    before = pre_evidence.model_dump_json(round_trip=True)
    clock = Clock(source.evaluated_at)
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "passed")
    assert result.result.evidence_complete
    assert result.snapshot.qualification == pre_evidence.result
    assert len(result.snapshot.qualification.gates) == 11
    assert result.receipt.completed_at == clock.calls[2]
    assert len(clock.calls) == 3
    assert result.snapshot.prepared_at == clock.calls[0]
    assert (
        result.receipt.report_sha256
        == hashlib.sha256(rendered["files"]["report.json"]).hexdigest()
    )
    assert pre_evidence.model_dump_json(round_trip=True) == before
    assert not list(root.iterdir()), "synthetic contract publisher must not write files"
    restored = EvidenceGateRun.model_validate_json(
        result.model_dump_json(round_trip=True)
    )
    assert restored == result
    assert restored.evaluation_sha256 == result.evaluation_sha256


@pytest.mark.parametrize("phase", ["start", "completion"])
def test_synthetic_publication_clock_reversal_is_a_clock_failure(
    source, inputs, pre_evidence, rendered, root, monkeypatch, phase
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher())
    moments = Clock(source.evaluated_at).moments
    moments[1 if phase == "start" else 2] = source.evaluated_at
    clock = Clock(source.evaluated_at, moments)
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "evidence_clock_invalid")
    assert result.receipt is None
    assert result.snapshot is not None


def test_expiry_at_publication_start_prevents_even_synthetic_write_start(
    source, inputs, pre_evidence, rendered, root, monkeypatch
):
    cached_render(monkeypatch, rendered)
    writes_started = []

    def publisher(*_args, clock, **_kwargs):
        clock()
        writes_started.append(True)
        pytest.fail("publication was allowed to start after the entry deadline")

    monkeypatch.setattr(module, "publish_evidence", publisher)
    clock = Clock(
        source.evaluated_at, [rendered["snapshot"].prepared_at, deadline(pre_evidence)]
    )
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "evidence_entry_expired")
    assert not writes_started
    assert result.snapshot is not None and result.receipt is None


@pytest.mark.parametrize("offset", [timedelta(), timedelta(microseconds=1)])
def test_synthetic_postpublication_expiry_keeps_receipt_but_cannot_pass(
    source, inputs, pre_evidence, rendered, root, monkeypatch, offset
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher())
    clock = Clock(source.evaluated_at)
    clock.moments[2] = deadline(pre_evidence) + offset
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "evidence_entry_expired")
    assert result.snapshot is not None
    assert result.receipt.status == "written"
    assert result.receipt.completed_at == clock.calls[2]
    assert not result.result.evidence_complete


def test_synthetic_identical_existing_packet_is_not_a_new_g12_permission(
    source, inputs, pre_evidence, rendered, root, monkeypatch
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(
        module, "publish_evidence", synthetic_publisher(status="already_present")
    )
    result = publish(source, inputs, pre_evidence, root)
    assert_g12(result, pre_evidence, "evidence_already_published")
    assert result.receipt.status == "already_present"
    assert result.receipt.files == result.rendered_files


@pytest.mark.parametrize(
    "defect",
    [
        "report",
        "path",
        "time",
        "file_hash",
        "file_size",
        "report_hash",
        "hidden",
        "subclass",
        "authority",
        "files_missing",
    ],
)
def test_synthetic_invalid_receipts_never_become_g12_evidence(
    source, inputs, pre_evidence, rendered, root, monkeypatch, defect
):
    cached_render(monkeypatch, rendered)

    def mutate(receipt):
        if defect == "subclass":
            return extended(receipt)
        if defect in {"file_hash", "file_size"}:
            updates = (
                {"sha256": "0" * 64} if defect == "file_hash" else {"size_bytes": 1}
            )
            first = receipt.files[0].model_copy(update=updates)
            return receipt.model_copy(update={"files": (first, *receipt.files[1:])})
        changes = {
            "report": {"report_id": "synthetic-another-report"},
            "path": {"report_directory": str(root / "another-directory")},
            "time": {"completed_at": receipt.completed_at + timedelta(microseconds=1)},
            "report_hash": {"report_sha256": "0" * 64},
            "hidden": {"hidden": True},
            "authority": {"execution_authority": True},
            "files_missing": {"files": receipt.files[:-1]},
        }
        return receipt.model_copy(update=changes[defect])

    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher(mutate=mutate))
    result = publish(source, inputs, pre_evidence, root)
    assert_g12(result, pre_evidence, "evidence_publication_invalid")
    assert result.snapshot is not None and result.receipt is None


def test_publication_fault_keeps_render_pins_and_bounded_error_not_a_receipt(
    source, inputs, pre_evidence, rendered, root, monkeypatch
):
    cached_render(monkeypatch, rendered)

    def fail(*_args, **_kwargs):
        raise EvidencePublicationError(
            "synthetic partial publication failure " + "x" * 6000
        )

    monkeypatch.setattr(module, "publish_evidence", fail)
    result = publish(source, inputs, pre_evidence, root)
    assert_g12(result, pre_evidence, "evidence_publication_failed")
    assert result.snapshot is not None and result.receipt is None
    assert len(result.rendered_files) == 6
    assert 0 < len(result.error_detail) <= 4096
    assert len(result.result.gates[-1].reason) <= 512
    assert all(
        not isinstance(value, str) or len(value) <= 512
        for value in result.result.gates[-1].measured_values.values()
    )


@pytest.mark.parametrize(
    "target", ["run", "snapshot", "receipt", "pre_evidence", "rendered_file"]
)
@pytest.mark.parametrize("defect", ["hidden", "subclass"])
def test_output_fingerprint_rejects_deep_hidden_or_declared_subclass_fields(
    source, inputs, pre_evidence, rendered, root, monkeypatch, target, defect
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher())
    result = publish(source, inputs, pre_evidence, root)
    assert_g12(result, pre_evidence, "passed")
    record = (
        result
        if target == "run"
        else result.rendered_files[0]
        if target == "rendered_file"
        else getattr(result, target)
    )
    record = (
        record.model_copy(update={"hidden": True})
        if defect == "hidden"
        else extended(record)
    )
    if target == "run":
        altered = record
    elif target == "rendered_file":
        altered = result.model_copy(
            update={"rendered_files": (record, *result.rendered_files[1:])}
        )
    else:
        altered = result.model_copy(update={target: record})
    with pytest.raises((ValueError, TypeError)):
        _ = altered.evaluation_sha256


@pytest.mark.parametrize("field", FLAGS)
@pytest.mark.parametrize("value", [True, 1, "false"])
def test_output_authority_flags_remain_exact_false(
    source, inputs, pre_evidence, rendered, root, monkeypatch, field, value
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher())
    result = publish(source, inputs, pre_evidence, root)
    with pytest.raises((ValueError, TypeError)):
        _ = result.model_copy(update={field: value}).evaluation_sha256
    with pytest.raises(ValidationError):
        result.execution_authority = True


def test_hostile_decimal_context_does_not_change_synthetic_publication_contract(
    source, inputs, pre_evidence, rendered, root, monkeypatch
):
    cached_render(monkeypatch, rendered)
    monkeypatch.setattr(module, "publish_evidence", synthetic_publisher())
    expected = publish(source, inputs, pre_evidence, root)
    context = Context(prec=6)
    context.traps[Inexact] = True
    moments = [
        value.astimezone(timezone(timedelta(hours=8)))
        for value in Clock(source.evaluated_at).moments
    ]
    with localcontext(context):
        actual = publish(
            source,
            inputs,
            pre_evidence,
            root,
            clock=Clock(source.evaluated_at, moments),
        )
        assert actual == expected
        assert actual.evaluation_sha256 == expected.evaluation_sha256


@NATIVE_POSIX_ONLY
def test_native_posix_real_chain_publishes_six_files_and_keeps_report_pre_g12(
    source, inputs, pre_evidence, root
):
    before = pre_evidence.model_dump_json(round_trip=True)
    clock = Clock(source.evaluated_at)
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "passed")
    assert result.receipt.status == "written"
    directory = root / source.report_id
    assert {path.name for path in directory.iterdir()} == set(FILE_NAMES)
    for record in result.receipt.files:
        body = (directory / record.name).read_bytes()
        assert len(body) == record.size_bytes
        assert hashlib.sha256(body).hexdigest() == record.sha256
    report = json.loads((directory / "report.json").read_bytes())
    assert len(report["snapshot"]["qualification"]["gates"]) == 11
    assert report["snapshot"]["evidence_gate"] == "not_evaluated"
    assert report["snapshot"]["execution_recheck"] == "not_evaluated"
    assert result.receipt.completed_at == clock.calls[2]
    assert result.snapshot.prepared_at == clock.calls[0]
    assert pre_evidence.model_dump_json(round_trip=True) == before


@NATIVE_POSIX_ONLY
def test_native_posix_identical_retry_cannot_reuse_g12_completion(
    source, inputs, pre_evidence, root
):
    first = publish(source, inputs, pre_evidence, root)
    assert_g12(first, pre_evidence, "passed")
    before = {
        path.name: path.read_bytes() for path in (root / source.report_id).iterdir()
    }
    again = publish(source, inputs, pre_evidence, root)
    assert_g12(again, pre_evidence, "evidence_already_published")
    assert again.receipt.status == "already_present"
    assert before == {
        path.name: path.read_bytes() for path in (root / source.report_id).iterdir()
    }


@NATIVE_POSIX_ONLY
def test_native_posix_expired_before_write_leaves_no_report_directory(
    source, inputs, pre_evidence, root
):
    clock = Clock(
        source.evaluated_at,
        [source.evaluated_at + timedelta(milliseconds=1), deadline(pre_evidence)],
    )
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "evidence_entry_expired")
    assert result.receipt is None
    assert not list(root.iterdir())


@NATIVE_POSIX_ONLY
def test_native_posix_expiry_after_readback_retains_real_receipt_and_files(
    source, inputs, pre_evidence, root
):
    clock = Clock(source.evaluated_at)
    clock.moments[2] = deadline(pre_evidence)
    result = publish(source, inputs, pre_evidence, root, clock=clock)
    assert_g12(result, pre_evidence, "evidence_entry_expired")
    assert result.receipt.status == "written"
    assert {path.name for path in (root / source.report_id).iterdir()} == set(
        FILE_NAMES
    )
    assert result.receipt.completed_at == deadline(pre_evidence)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows drive-root rejection only")
def test_native_windows_filesystem_root_is_rejected_without_writing(
    source, inputs, pre_evidence
):
    drive_root = Path(Path.cwd().anchor)
    result = publish(source, inputs, pre_evidence, drive_root)
    assert_g12(result, pre_evidence, "evidence_publication_failed")
    assert result.snapshot is not None and result.receipt is None
    assert "filesystem or drive root" in result.error_detail


@pytest.mark.skipif(
    os.name != "nt", reason="Windows unavailable-pin fail-closed contract only"
)
def test_windows_unavailable_native_pin_is_not_a_success_fallback(
    source, inputs, pre_evidence, rendered, root, monkeypatch
):
    from app.trade_evidence import storage

    cached_render(monkeypatch, rendered)
    calls = []

    def unavailable(*_args, **_kwargs):
        calls.append(True)
        raise EvidencePublicationError("synthetic unavailable native directory pin")

    monkeypatch.setattr(storage, "_windows_root", unavailable)
    result = publish(source, inputs, pre_evidence, root)
    assert_g12(result, pre_evidence, "evidence_publication_failed")
    assert calls == [True]
    assert result.receipt is None
    assert not list(root.iterdir())


@pytest.mark.skipif(
    os.name != "nt", reason="Actual Windows native publication boundary only"
)
def test_native_windows_owned_root_reports_real_pin_outcome_without_fallback(
    source, inputs, pre_evidence, root
):
    # No publisher, pin primitive, renderer, or clock internals are mocked.
    result = publish(source, inputs, pre_evidence, root)
    if result.result.gates[-1].passed:
        assert_g12(result, pre_evidence, "passed")
        directory = root / source.report_id
        assert {path.name for path in directory.iterdir()} == set(FILE_NAMES)
        for record in result.receipt.files:
            payload = (directory / record.name).read_bytes()
            assert len(payload) == record.size_bytes
            assert hashlib.sha256(payload).hexdigest() == record.sha256
        print("NATIVE_WINDOWS_OWNED_ROOT=written_and_readback_verified")
    else:
        assert_g12(result, pre_evidence, "evidence_publication_failed")
        detail = result.error_detail.lower()
        assert "permissionerror" in detail or "winerror 5" in detail
        assert result.receipt is None
        assert not list(root.iterdir())
        print(
            "NATIVE_WINDOWS_OWNED_ROOT=permission_denied_fail_closed; successful native publication not verified"
        )
