"""Declared original sources only; no native/DB/TLS/account acceptance.

All positive raw balance choices happen before new capture. No old source is
edited/re-signed. Pure peak vectors do not substitute for the real stream or
owned persistence tests; the core case replays actual original B3/witnesses.
"""

import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_global_financial_v2 as core
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import (
    account_original_balance_measurement_v1 as measurement,
)
from app.trade_qualification.reservations import LedgerScope
from tests.unit import test_account_history_global_financial_v1 as batches_fixture
from tests.unit import test_account_history_original_financial_stream_v2 as wire_fixture
from tests.unit import test_account_lifecycle_history as acquisition
from tests.unit import test_account_native_proof as native_fixture
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_qualification_account_capture import NOW, ms

SCOPE = acquisition.SCOPE
CHECKED = NOW + timedelta(seconds=60)


@contextmanager
def declared_balance(
    monkeypatch, *, equity="1000", available="800", liability="0", borrowed="0"
):
    real_pages = acquisition.flat_pages

    def pages():
        value = copy.deepcopy(real_pages())
        value["balance"][0][0]["details"][0].update(
            eq=equity, availEq=available, liab=liability, borrowFroz=borrowed
        )
        value["account_position_risk"][0][0]["balData"][0]["eq"] = equity
        return value

    with monkeypatch.context() as fresh:
        fresh.setattr(acquisition, "flat_pages", pages)
        yield fresh
    assert acquisition.flat_pages is real_pages


async def fresh_v5(
    monkeypatch, *, at=NOW, equity="1000", available="800", liability="0", borrowed="0"
):
    with (
        wire_fixture._wire_construction(monkeypatch) as wire,
        declared_balance(
            wire,
            equity=equity,
            available=available,
            liability=liability,
            borrowed=borrowed,
        ) as fresh,
    ):
        chain = await acquisition.original_capture(
            fresh, at, base=NOW, filled=False, case="A01"
        )
    return chain, observed.source_reference(chain)


async def fresh_v6_or_v7(monkeypatch, version):
    if version == "V6":
        original = native_fixture.algo_history_v6_pages

        def pages():
            supplied = flat_pages()
            supplied["account_position_risk"][0][0]["ts"] = ms(
                NOW + timedelta(milliseconds=2)
            )
            current_rows = {
                name: supplied[name]
                for name in capture.V7_CURRENT_STREAMS
                if name in supplied
            }
            return original(extra=current_rows)

        monkeypatch.setattr(native_fixture, "algo_history_v6_pages", pages)
    _, _, chain, scope, _ = await native_fixture.companion_fixture(
        monkeypatch,
        current_v7=version == "V7",
        algo_v6=version == "V6",
        verify_baseline=False,
    )
    assert scope == SCOPE
    return chain, observed.source_reference(chain)


async def raw_measurement(monkeypatch, version):
    chain, reference = (
        await fresh_v5(monkeypatch)
        if version == "V5"
        else await fresh_v6_or_v7(monkeypatch, version)
    )
    raw = measurement.measure_original_balance_v1(
        chain, reference=reference, scope=SCOPE, checked_at=CHECKED
    )
    return chain, reference, raw


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["V5", "V6", "V7"])
async def test_new_versioned_measurement_reads_actual_original_packet_values(
    monkeypatch, version
):
    chain, reference, raw = await raw_measurement(monkeypatch, version)
    value = json.loads(raw)
    assert value["equity"] == {"numerator": "1000", "denominator": "1"}
    assert value["available_margin"] == {"numerator": "800", "denominator": "1"}
    assert value["source_reference"] == observed.reference_document(reference)
    assert (
        value["packet_contract"]
        == {
            "V5": "ctcc.demo_account_plan.v5",
            "V6": "ctcc.demo_account_plan.v6",
            "V7": "ctcc.demo_current_account_plan.v7",
        }[version]
    )
    assert value["native_clock_issued"] is value["current_owner_issued"] is False
    assert observed.source_reference(chain) == reference


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["head_sha256", "packet_sha256", "session_binding_sha256"]
)
async def test_external_reference_cannot_relabel_original_bytes(monkeypatch, field):
    chain, reference = await fresh_v5(monkeypatch)
    changed = replace(reference, **{field: "f" * 64})
    with pytest.raises(measurement.OriginalBalanceMeasurementError):
        measurement.measure_original_balance_v1(
            chain, reference=changed, scope=SCOPE, checked_at=CHECKED
        )
    assert observed.source_reference(chain) == reference


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["available_above_equity", "liability", "borrowed"])
async def test_original_finite_but_unsupported_balance_is_not_coerced(
    monkeypatch, damage
):
    values = (
        {"available": "1001"}
        if damage == "available_above_equity"
        else {"liability": "1"}
        if damage == "liability"
        else {"borrowed": "1"}
    )
    chain, reference = await fresh_v5(monkeypatch, **values)
    with pytest.raises(measurement.OriginalBalanceMeasurementError):
        measurement.measure_original_balance_v1(
            chain, reference=reference, scope=SCOPE, checked_at=CHECKED
        )


@pytest.mark.asyncio
async def test_coherently_mutated_private_packet_dto_still_fails_original_byte_pin(
    monkeypatch,
):
    chain, reference = await fresh_v5(monkeypatch)
    packet = copy.deepcopy(lifecycle._packet(chain, reference))
    for page in packet.observations:
        if page.request.stream in {"balance", "account_position_risk"}:
            row = page.rows[0]
            value = json.loads(row.canonical_json)
            if page.request.stream == "balance":
                value["details"][0]["eq"] = "1200"
            else:
                value["balData"][0]["eq"] = "1200"
            object.__setattr__(row, "canonical_json", journal.canonical(value).decode())
    with pytest.raises(
        measurement.OriginalBalanceMeasurementError,
        match="^original_balance_packet_bytes_changed$",
    ):
        measurement._measure_verified_packet_v1(packet, reference, SCOPE)
    assert observed.source_reference(chain) == reference


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["uid", "currency", "before_completion"])
async def test_exact_scope_and_original_availability_remain_required(
    monkeypatch, damage
):
    chain, reference = await fresh_v5(monkeypatch)
    scope = (
        LedgerScope(account_id="999999", settlement_currency="USDT")
        if damage == "uid"
        else (
            LedgerScope(account_id=SCOPE.account_id, settlement_currency="USDC")
            if damage == "currency"
            else SCOPE
        )
    )
    checked = NOW - timedelta(seconds=1) if damage == "before_completion" else CHECKED
    with pytest.raises(measurement.OriginalBalanceMeasurementError):
        measurement.measure_original_balance_v1(
            chain, reference=reference, scope=scope, checked_at=checked
        )


async def population(monkeypatch):
    sources, raw = [], []
    for index, equity in enumerate(("1000", "1200", "900")):
        chain, reference = await fresh_v5(
            monkeypatch,
            at=NOW + timedelta(seconds=index * 10),
            equity=equity,
            available="800",
        )
        sources.append((chain, reference))
        raw.append(
            measurement.measure_original_balance_v1(
                chain, reference=reference, scope=SCOPE, checked_at=CHECKED
            )
        )
    windows = tuple(
        observed.ExecutionWindow(
            lifecycle._packet(chain, reference).plan.history_start,
            lifecycle._packet(chain, reference).plan.history_end
            - timedelta(seconds=300),
        )
        for chain, reference in sources
    )
    batches = batches_fixture.original_batches(tuple(sources), windows)
    rows = [
        {
            "sequence": batch.sequence,
            "reference": observed.reference_document(reference),
            "event_sha256": batch.event_sha256,
        }
        for (_, reference), batch in zip(sources, batches, strict=True)
    ]
    return tuple(sources), batches, raw, rows


@pytest.mark.asyncio
async def test_real_full_prefix_core_keeps_earlier_peak_instead_of_last_sample(
    monkeypatch,
):
    sources, batches, _, _ = await population(monkeypatch)
    original_pins = tuple(observed.source_reference(chain) for chain, _ in sources)
    accumulator = core.OriginalHistoryFinancialAccumulatorV2(scope=SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        accumulator.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=batches_fixture.original_witnesses(sources, batch),
        )
    value = json.loads(
        accumulator.finish(
            expected_through=3,
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
        ).receipt_json
    )
    peak = value["sampled_balance_peak"]
    assert peak["sample_count"] == 3 and peak["peak_equity"] == {
        "numerator": "1200",
        "denominator": "1",
    }
    assert peak["last_available_margin"] == {"numerator": "800", "denominator": "1"}
    assert peak["peak_source_reference"] == observed.reference_document(sources[1][1])
    assert value["sampled_balance_peak_missing_reason"] is None
    assert (
        peak["historical_account_peak"]
        is peak["initial_account_peak"]
        is peak["external_cashflow_adjustment"]
        is None
    )
    assert (
        value["account_complete"]
        is value["execution_authority"]
        is value["original_stream_prefix_verified"]
        is False
    )
    assert (
        tuple(observed.source_reference(chain) for chain, _ in sources) == original_pins
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "omit_first",
        "reorder",
        "head",
        "reference",
        "bool_count",
        "promote_clock",
        "rational",
        "time",
    ],
)
async def test_arithmetic_prefix_rejects_omission_tamper_and_unknown_promotion(
    monkeypatch, damage
):
    _, batches, raw, sources = await population(monkeypatch)
    expected_through, expected_head = 3, batches[-1].event_sha256
    raw, sources = list(raw), copy.deepcopy(sources)
    if damage == "omit_first":
        raw, sources = raw[1:], sources[1:]
    elif damage == "reorder":
        raw[0], raw[1] = raw[1], raw[0]
    elif damage == "head":
        expected_head = "f" * 64
    elif damage == "reference":
        sources[1]["reference"]["session_binding_sha256"] = "f" * 64
    elif damage == "bool_count":
        expected_through = True
    else:
        value = json.loads(raw[1])
        if damage == "promote_clock":
            value["native_clock_issued"] = True
        elif damage == "rational":
            value["equity"] = {"numerator": "2400", "denominator": "2"}
        else:
            value["body_completed_at"] = (NOW - timedelta(days=1)).isoformat()
        raw[1] = journal.canonical(value)
    with pytest.raises(measurement.OriginalBalanceMeasurementError):
        measurement.sampled_peak_from_original_measurements_v1(
            raw,
            sources,
            scope=SCOPE,
            expected_through=expected_through,
            expected_head=expected_head,
        )


@pytest.mark.asyncio
async def test_explicit_cancel_cannot_return_partial_peak_or_reuse_core(monkeypatch):
    sources, batches, _, _ = await population(monkeypatch)
    accumulator = core.OriginalHistoryFinancialAccumulatorV2(scope=SCOPE)
    accumulator.consume_current(
        chain=sources[0][0], reference=sources[0][1], batch=batches[0]
    )
    accumulator.cancel()
    assert accumulator._state is None
    with pytest.raises(core.GlobalFinancialHistoryError):
        accumulator.finish(
            expected_through=3,
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
        )
