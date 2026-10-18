"""V2 bounded calculation uses synthetic original B1 captures, never account authority."""

import json

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_measured_hwm_continuation as v2
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as v1
from app.trade_qualification import account_portfolio_runtime as runtime
from tests.unit.test_account_current_source_verifier import SCOPE
from tests.unit.test_account_portfolio_components import measurement


def source_reference(measurement):
    return observed.CaptureReference(
        **json.loads(measurement.source_json)["source_reference"]
    )


def advance(previous, sequence, measurement, genesis, peak, **changes):
    source_previous = (
        None if previous is None else json.loads(previous.receipt_json)["head_sha256"]
    )
    arguments = {
        "sequence": sequence,
        "previous_event_sha256": source_previous,
        "event_sha256": f"{sequence:064x}",
        "measurement": measurement,
        "expected_reference": source_reference(measurement),
        "predecessor": previous,
        "expected_predecessor_sha256": None
        if previous is None
        else previous.receipt_sha256,
        "genesis_witness": None if previous is None else genesis,
        "selected_peak_witness": None if previous is None else peak,
    }
    arguments.update(changes)
    return v2.advance_measured_hwm(SCOPE, **arguments)


@pytest.mark.asyncio
async def test_v2_bounded_continuation_preserves_v1_measured_peak_without_reusing_v1_membership(
    monkeypatch,
):
    samples = [
        (await measurement(monkeypatch, equity=equity))[1]
        for equity in ("1000", "1200", "900")
    ]
    old = v1.MeasuredHWMFold(SCOPE, 40, f"{40:064x}")
    result = None
    selected = samples[0]
    measurement_calls = []
    original_measurement = v2._measurement

    def counted_measurement(*args):
        measurement_calls.append(True)
        return original_measurement(*args)

    for sequence in range(1, 41):
        item = samples[min(sequence - 1, 2)]
        prior_head = None if sequence == 1 else f"{sequence - 1:064x}"
        old.add(sequence, prior_head, f"{sequence:064x}", item)
        if sequence == 40:
            monkeypatch.setattr(v2, "_measurement", counted_measurement)
        result = advance(result, sequence, item, samples[0], selected)
        if sequence == 2:
            selected = samples[1]
    assert len(measurement_calls) == 3
    assert result is not None
    projected = json.loads(result.receipt_json)
    replayed = json.loads(old.finish())
    assert projected["sample_count"] == replayed["sample_count"] == 40
    assert (
        projected["peak_equity"]
        == replayed["peak_equity"]
        == {
            "numerator": "1200",
            "denominator": "1",
        }
    )
    assert projected["window_started_at"] == replayed["window_started_at"]
    assert projected["last_measured_at"] == replayed["last_measured_at"]
    assert projected["peak_measurement_sha256"] == replayed["peak_source_sha256"]
    assert projected["membership_sha256"] != replayed["membership_sha256"]
    assert projected["policy_sha256"] == v2.POLICY_SHA256
    assert projected["unsampled_intermediate_peak"] == "unknown"
    assert projected["durable_membership_verified"] is False
    assert projected["native_clock_verified"] is False
    assert projected["account_complete"] is False
    assert projected["execution_authority"] is result.execution_authority is False
    assert projected["admission"] == "DENY"
    with pytest.raises(v1.PortfolioComponentError, match="native_owner"):
        runtime._consume_owned_components(
            result, expected_receipt_sha256=result.receipt_sha256
        )


@pytest.mark.asyncio
async def test_v2_rejects_gap_reordered_head_and_unpinned_predecessor(monkeypatch):
    _, sample = await measurement(monkeypatch)
    first = advance(None, 1, sample, sample, sample)
    valid = advance(first, 2, sample, sample, sample)
    assert json.loads(valid.receipt_json)["through_sequence"] == 2
    with pytest.raises(v1.PortfolioComponentError, match="predecessor_mismatch"):
        advance(first, 3, sample, sample, sample)
    for changes in (
        {"previous_event_sha256": "0" * 64},
        {"expected_predecessor_sha256": "0" * 64},
        {"expected_reference": observed.CaptureReference("0" * 32, *(["0" * 64] * 4))},
        {"genesis_witness": None},
        {"selected_peak_witness": None},
    ):
        with pytest.raises(v1.PortfolioComponentError):
            advance(first, 2, sample, sample, sample, **changes)
    with pytest.raises(v1.PortfolioComponentError, match="genesis"):
        advance(first, 1, sample, sample, sample)


@pytest.mark.asyncio
async def test_v2_rejects_changed_genesis_peak_and_projection_bytes(monkeypatch):
    samples = [
        (await measurement(monkeypatch, equity=equity))[1]
        for equity in ("1000", "1200", "900")
    ]
    first = advance(None, 1, samples[0], samples[0], samples[0])
    second = advance(first, 2, samples[1], samples[0], samples[0])
    for genesis, peak in (
        (samples[1], samples[1]),
        (samples[0], samples[0]),
    ):
        with pytest.raises(v1.PortfolioComponentError, match="witness"):
            advance(second, 3, samples[2], genesis, peak)
    corrupted = json.loads(second.receipt_json)
    corrupted["membership_sha256"] = "0" * 64
    forged = v2.HWMContinuation(journal.canonical(corrupted))
    with pytest.raises(v1.PortfolioComponentError, match="predecessor_mismatch"):
        advance(
            forged,
            3,
            samples[2],
            samples[0],
            samples[1],
            expected_predecessor_sha256=second.receipt_sha256,
        )
    corrupted["execution_authority"] = True
    forged = v2.HWMContinuation(journal.canonical(corrupted))
    with pytest.raises(v1.PortfolioComponentError, match="predecessor_invalid"):
        advance(forged, 3, samples[2], samples[0], samples[1])


@pytest.mark.asyncio
async def test_v2_never_treats_constructed_balance_as_source_authority(monkeypatch):
    _, sample = await measurement(monkeypatch)
    changed = json.loads(sample.source_json)
    changed["current_source_authority"] = True
    forged = v1.BalanceMeasurement(journal.canonical(changed))
    with pytest.raises(v1.PortfolioComponentError, match="measurement_invalid"):
        advance(None, 1, forged, forged, forged)
    changed = json.loads(sample.source_json)
    changed["body_completed_at"] = changed["request_started_at"]
    changed["headers_received_at"] = "2020-01-01T00:00:00+00:00"
    forged = v1.BalanceMeasurement(journal.canonical(changed))
    with pytest.raises(v1.PortfolioComponentError, match="measurement_invalid"):
        advance(None, 1, forged, forged, forged)


@pytest.mark.asyncio
async def test_v2_deep_json_fails_with_static_error_for_measurement_and_predecessor(
    monkeypatch,
):
    _, sample = await measurement(monkeypatch)
    deep_json = b"[" * 4000 + b"0" + b"]" * 4000
    assert len(deep_json) < 1048576
    malformed = v1.BalanceMeasurement(deep_json)
    with pytest.raises(v1.PortfolioComponentError, match="measurement_invalid"):
        v2.advance_measured_hwm(
            SCOPE,
            sequence=1,
            previous_event_sha256=None,
            event_sha256=f"{1:064x}",
            measurement=malformed,
            expected_reference=source_reference(sample),
        )

    first = advance(None, 1, sample, sample, sample)
    malformed_prior = v2.HWMContinuation(deep_json)
    with pytest.raises(v1.PortfolioComponentError, match="predecessor_invalid"):
        v2.advance_measured_hwm(
            SCOPE,
            sequence=2,
            previous_event_sha256=json.loads(first.receipt_json)["head_sha256"],
            event_sha256=f"{2:064x}",
            measurement=sample,
            expected_reference=source_reference(sample),
            predecessor=malformed_prior,
            expected_predecessor_sha256=journal.digest(deep_json),
            genesis_witness=sample,
            selected_peak_witness=sample,
        )
    assert json.loads(first.receipt_json)["admission"] == "DENY"
