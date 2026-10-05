"""B5 real synthetic B1 bytes, not native account or PostgreSQL acceptance."""

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification import account_portfolio_runtime as runtime
from app.trade_qualification.reservations import LedgerScope
from tests.unit.test_account_current_source_verifier import SCOPE, flat_pages, recorded
from tests.unit.test_qualification_account_capture import NOW, ms


async def measurement(monkeypatch, *, equity="1000", old_update=None):
    pages = flat_pages()
    pages["balance"][0][0]["details"][0]["eq"] = equity
    pages["account_position_risk"][0][0]["balData"][0]["eq"] = equity
    if old_update is not None:
        pages["balance"][0][0]["uTime"] = old_update
        pages["balance"][0][0]["details"][0]["uTime"] = old_update
    chain = await recorded(monkeypatch, pages=pages)
    before = tuple(point.event for point in chain)
    value = components.observe_balance(
        chain, reference=observed.source_reference(chain), scope=SCOPE
    )
    assert tuple(point.event for point in chain) == before
    return chain, value


@pytest.mark.asyncio
async def test_measurement_keeps_raw_currency_timestamp_and_source_locator(monkeypatch):
    old = ms(NOW - timedelta(days=10))
    chain, result = await measurement(monkeypatch, old_update=old)
    value = json.loads(result.source_json)
    row = json.loads(value["raw_row_json"])
    assert value["equity"] == {"numerator": "1000", "denominator": "1"}
    assert value["available_margin"] == {"numerator": "800", "denominator": "1"}
    assert value["account_uTime"] == value["settlement_uTime"] == old
    assert row["details"][0]["uTime"] == old
    assert value["raw_row_sha256"] == journal.digest(value["raw_row_json"].encode())
    assert value["source_reference"] == observed.reference_document(
        observed.source_reference(chain)
    )
    assert (
        value["request_started_at"]
        <= value["headers_received_at"]
        <= value["body_completed_at"]
    )
    assert not value["recorded_native_transport"]
    assert not value["current_source_authority"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("eq", ""), ("availEq", ""), ("liab", "1")])
async def test_missing_or_unsupported_balance_never_becomes_zero(
    monkeypatch, field, value
):
    pages = flat_pages()
    pages["balance"][0][0]["details"][0][field] = value
    chain = await recorded(monkeypatch, pages=pages)
    with pytest.raises(ValueError):
        components.observe_balance(
            chain, reference=observed.source_reference(chain), scope=SCOPE
        )


@pytest.mark.asyncio
async def test_balance_replay_rejects_changed_source_pin_scope_or_missing_terminal(
    monkeypatch,
):
    chain, _ = await measurement(monkeypatch)
    reference = observed.source_reference(chain)
    for events, ref, scope in (
        (chain, replace(reference, head_sha256="0" * 64), SCOPE),
        (chain, reference, LedgerScope(account_id="999", settlement_currency="USDT")),
        (chain[:-1], reference, SCOPE),
    ):
        with pytest.raises(ValueError):
            components.observe_balance(events, reference=ref, scope=scope)


@pytest.mark.asyncio
async def test_sampled_max_retains_genesis_prior_peak_and_repeated_exchange_utime(
    monkeypatch,
):
    samples = []
    old = ms(NOW - timedelta(days=10))
    for equity in ("1000", "1200", "900"):
        _, value = await measurement(monkeypatch, equity=equity, old_update=old)
        samples.append(value)
    fold = components.MeasuredHWMFold(SCOPE, 3, "c" * 64)
    for number, value in enumerate(samples, 1):
        fold.add(
            number,
            None if number == 1 else "abc"[number - 2] * 64,
            "abc"[number - 1] * 64,
            value,
        )
    genesis = datetime.fromisoformat(
        json.loads(samples[0].source_json)["body_completed_at"]
    )
    result = json.loads(fold.finish(required_window_started_at=genesis))
    assert result["peak_equity"] == {"numerator": "1200", "denominator": "1"}
    assert result["peak_source_sha256"] == journal.digest(samples[1].source_json)
    assert result["sample_count"] == 3 and result["risk_window_matches"] is True
    assert result["window_started_at"] == genesis.isoformat()
    assert result["measured_population_complete"] is True
    assert result["all_sources_recorded_native"] is False
    assert result["unsampled_intermediate_peak"] == "unknown"
    assert not result["execution_authority"]
    earlier = json.loads(
        fold.finish(required_window_started_at=genesis - timedelta(days=7))
    )
    assert earlier["risk_window_matches"] is False
    assert earlier["peak_equity"] == result["peak_equity"]


@pytest.mark.asyncio
async def test_hwm_no_genesis_reset_omitted_prefix_or_reordered_member(monkeypatch):
    _, value = await measurement(monkeypatch)
    fold = components.MeasuredHWMFold(SCOPE, 2, "b" * 64)
    with pytest.raises(components.PortfolioComponentError, match="prefix"):
        fold.add(2, "a" * 64, "b" * 64, value)
    fold.add(1, None, "a" * 64, value)
    with pytest.raises(components.PortfolioComponentError, match="population"):
        fold.finish()
    for sequence, previous in ((1, "a" * 64), (2, "0" * 64), (True, "a" * 64)):
        with pytest.raises(components.PortfolioComponentError, match="prefix"):
            fold.add(sequence, previous, "b" * 64, value)
    other = components.MeasuredHWMFold(
        LedgerScope(account_id="999", settlement_currency="USDT"), 1, "a" * 64
    )
    with pytest.raises(components.PortfolioComponentError, match="scope"):
        other.add(1, None, "a" * 64, value)


def test_diagnostic_or_constructed_identity_cannot_acquire_runtime_owner():
    with pytest.raises(components.PortfolioComponentError, match="not_constructible"):
        runtime.OwnedAccountComponents()
    for supplied in ({"passed": True}, b"{}", None):
        with pytest.raises(components.PortfolioComponentError, match="native_owner"):
            runtime._consume_owned_components(
                supplied, expected_receipt_sha256="a" * 64
            )
    forged = object.__new__(runtime.OwnedAccountComponents)
    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        runtime._consume_owned_components(forged, expected_receipt_sha256="a" * 64)


def test_untrusted_pin_callback_is_not_invoked_and_owner_is_consumed():
    class ForeignPin:
        def __eq__(self, value):
            raise AssertionError("foreign equality callback")

        def __ne__(self, value):
            raise AssertionError("foreign inequality callback")

    owner = object.__new__(runtime.OwnedAccountComponents)
    handoff = runtime.ComponentHandoff(None, None, b"synthetic pin mechanism")
    runtime._OWNERS[owner] = runtime._RegisteredComponents(handoff, None)
    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        runtime._consume_owned_components(owner, expected_receipt_sha256=ForeignPin())
    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        runtime._consume_owned_components(
            owner, expected_receipt_sha256=journal.digest(handoff.receipt_json)
        )


@pytest.mark.parametrize("delta", [-1, 0, 29, 30])
def test_legacy_utc_callback_cannot_supply_missing_native_boundary(delta):
    # A legacy injected callback is never accepted as the new native capability.
    owner = object.__new__(runtime.OwnedAccountComponents)
    handoff = runtime.ComponentHandoff(None, None, b"synthetic registry mechanism")
    callbacks = []

    def foreign_clock():
        callbacks.append(True)
        return NOW + timedelta(seconds=delta)

    runtime._OWNERS[owner] = runtime._RegisteredComponents(handoff, foreign_clock)
    with pytest.raises(components.PortfolioComponentError, match="native_clock"):
        runtime._consume_owned_components(
            owner, expected_receipt_sha256=journal.digest(handoff.receipt_json)
        )
    assert not callbacks
    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        runtime._consume_owned_components(
            owner, expected_receipt_sha256=journal.digest(handoff.receipt_json)
        )
