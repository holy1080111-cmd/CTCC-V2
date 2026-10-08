"""Synthetic offline source mapping only; never authenticated account or order IO."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError, model_serializer

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_materializer as module
from app.trade_qualification import reservations
from app.trade_qualification.portfolio import ObservedSource
from tests.unit.test_qualification_account_capture import (
    CURSORS,
    INSTRUMENT,
    NOW,
    UID,
    ms,
    plan,
    records,
    row,
    verify,
)

D = Decimal
GROUP = "synthetic-linear-crypto"
CALLBACKS = []


@pytest.fixture(autouse=True)
def no_foreign_callbacks():
    CALLBACKS.clear()
    yield
    assert CALLBACKS == []


def raw_instrument(**changes):
    return {
        "instId": INSTRUMENT,
        "instType": "SWAP",
        "ctType": "linear",
        "baseCcy": "BTC",
        "settleCcy": "USDT",
        "ctValCcy": "BTC",
        "ctVal": "0.01",
        "ctMult": "1",
        "lotSz": "0.1",
        "minSz": "0.1",
        "maxLmtSz": "1000",
        "lever": "20",
        "state": "live",
        **changes,
    }


def instrument(raw=None, **changes):
    raw = raw_instrument() if raw is None else raw
    body = raw if type(raw) is bytes else json.dumps(raw).encode()
    return module.InstrumentEvidence(
        **{
            "raw_response": body,
            "expected_sha256": hashlib.sha256(body).hexdigest(),
            "observed_at": NOW - timedelta(seconds=1),
            "received_at": NOW,
            **changes,
        }
    )


def cost(**changes):
    return module.ExposureCost(
        **{
            "instrument_id": INSTRUMENT,
            "cost_per_base": D("0.05"),
            "source_sha256": "c" * 64,
            "observed_at": NOW - timedelta(seconds=1),
            "received_at": NOW,
            **changes,
        }
    )


def inputs(**changes):
    return module.AccountMaterializationInputs(
        **{
            "account_id": UID,
            "settlement_currency": "USDT",
            "instruments": (instrument(),),
            "correlation_version": "synthetic-correlation-v1",
            "correlations": (
                module.CorrelationEntry(instrument_id=INSTRUMENT, group=GROUP),
            ),
            "costs": (cost(),),
            **changes,
        }
    )


def usdt_detail(**changes):
    return {
        "ccy": "USDT",
        "eq": "1000",
        "availEq": "800",
        "liab": "0",
        "borrowFroz": "0",
        "uTime": ms(),
        **changes,
    }


def source_pages(*, pending=True):
    pages = {stream: [[]] for stream in CURSORS}
    pages["account_position_risk"] = [
        [
            row(
                "account_position_risk",
                adjEq="1000",
                balData=[{"ccy": "USDT", "eq": "1000"}],
                posData=[
                    {
                        "posId": "900",
                        "instId": INSTRUMENT,
                        "instType": "SWAP",
                        "posSide": "net",
                        "mgnMode": "cross",
                        "pos": "2",
                        "ccy": "USDT",
                    }
                ],
            )
        ]
    ]
    pages["balance"] = [[row("balance", details=[usdt_detail()])]]
    pages["positions"] = [[row("positions", margin="5", markPx="101", lever="3")]]
    pages["algo_conditional"] = [
        [
            row(
                "algo_conditional",
                reduceOnly=True,
                side="sell",
                posSide="net",
                sz="2",
                tdMode="cross",
                slTriggerPx="95",
                slOrdPx="-1",
                slTriggerPxType="mark",
            )
        ],
        [],
    ]
    if pending:
        pages["orders_pending"] = [
            [
                row(
                    "orders_pending",
                    "800",
                    state="partially_filled",
                    ordType="limit",
                    reduceOnly=False,
                    px="100",
                    lever="5",
                    sz="2",
                    accFillSz="1",
                    attachAlgoOrds=[
                        {
                            "slTriggerPx": "95",
                            "slOrdPx": "-1",
                            "slTriggerPxType": "mark",
                        }
                    ],
                )
            ],
            [],
        ]
    return pages


def packet(*, pending=True, changes=None, selected=None):
    pages = source_pages(pending=pending)
    if changes is not None:
        changed = changes(pages)
        if changed is not None:
            pages = changed
    selected = plan() if selected is None else selected
    result = verify(*records(selected=selected, pages=pages))
    frozen = capture.freeze_demo_account_packet(
        result, expected_plan_sha256=result.plan_sha256
    )
    return result, frozen.sha256


def materialize(*, source=None, supplied=None, **changes):
    current, pin = packet() if source is None else source
    return module.materialize_demo_portfolio_snapshot(
        current,
        **{
            "expected_plan_sha256": current.plan_sha256,
            "expected_packet_sha256": pin,
            "inputs": inputs() if supplied is None else supplied,
            **changes,
        },
    )


def projection(result, stream, row_id=None):
    matches = [
        item
        for item in result.projections
        if item.stream == stream and (row_id is None or item.row_id == row_id)
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.fixture(scope="module")
def source():
    return packet()


def test_exact_positions_pending_remainder_and_protection_not_double_counted(source):
    result = materialize(source=source)
    assert result.equity == D("1000") and result.available_margin == D("800")
    assert len(result.positions) == len(result.pending_reservations) == 1
    position = result.positions[0]
    assert position.instrument_id == INSTRUMENT and position.direction == "long"
    assert (
        position.settlement_currency == "USDT" and position.correlation_group == GROUP
    )
    assert position.notional == D("2.02")
    assert position.margin == D("5")
    assert position.risk_amount == D("0.121")
    pending = result.pending_reservations[0]
    assert pending.instrument_id == INSTRUMENT and pending.direction == "long"
    assert pending.notional == D("1")
    assert pending.margin == D("0.2")
    assert pending.risk_amount == D("0.0505")
    assert pending.correlation_group == GROUP
    position_projection = projection(result, "positions")
    assert position_projection.contracts == D("2")
    assert position_projection.base_quantity == D("0.02")
    order_projection = projection(result, "orders_pending")
    assert order_projection.contracts == D("1")
    assert order_projection.base_quantity == D("0.01")
    assert result.snapshot is None
    assert result.incomplete_reasons
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False


def test_instrument_mapping_preserves_units_limits_and_public_source_pin(source):
    supplied = inputs()
    result = materialize(source=source, supplied=supplied)
    assert len(result.instruments) == 1
    spec = module.get_materialized_instrument(result, INSTRUMENT)
    assert spec == result.instruments[0]
    assert spec.contract_kind == "linear_base"
    assert spec.base_currency == spec.contract_value_currency == "BTC"
    assert spec.settlement_currency == "USDT"
    assert spec.contract_value == D("0.01")
    assert spec.lot_size == spec.min_contracts == D("0.1")
    assert spec.max_contracts == D("1000") and spec.max_leverage == 20
    assert spec.correlation_group == GROUP
    assert spec.observed_at == supplied.instruments[0].observed_at
    assert spec.received_at == supplied.instruments[0].received_at


def test_empty_pending_does_not_turn_protective_algo_into_opening_exposure():
    result = materialize(source=packet(pending=False))
    assert len(result.positions) == 1
    assert result.pending_reservations == ()
    assert result.positions[0].risk_amount == D("0.121")
    assert result.snapshot is None


def test_valid_reducing_pending_remains_visible_without_opening_risk():
    def alter(pages):
        pages["orders_pending"][0][0].update(reduceOnly=True, side="sell")

    result = materialize(source=packet(changes=alter))
    recorded = projection(result, "orders_pending")
    assert recorded.kind == "reducing_order"
    assert recorded.contracts == D("1")
    assert recorded.reasons == ()
    assert result.pending_reservations == ()
    assert result.snapshot is None


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"sz": "1", "accFillSz": "1"}, "reducing_remaining_quantity_invalid"),
        ({"sz": ""}, "reducing_remaining_quantity_invalid"),
        ({"tdMode": "unknown"}, "reducing_order_scope_unsupported"),
        ({"posSide": "long"}, "reducing_order_scope_unsupported"),
    ],
)
def test_malformed_reducing_pending_is_not_silently_treated_as_empty(change, expected):
    def alter(pages):
        pages["orders_pending"][0][0].update(reduceOnly=True, side="sell", **change)

    result = materialize(source=packet(changes=alter))
    recorded = projection(result, "orders_pending")
    assert recorded.kind == "reducing_order"
    assert expected in recorded.reasons
    assert expected in result.incomplete_reasons
    assert result.pending_reservations == ()
    assert result.snapshot is None
    assert result.account_complete is False and result.execution_authority is False


@pytest.mark.parametrize(
    "stream", ["algo_iceberg", "algo_twap", "algo_chase", "algo_smart_iceberg"]
)
def test_advanced_algo_exposure_cannot_disappear_or_claim_position_protection(stream):
    def change(pages):
        pages["algo_conditional"] = [[]]
        pages[stream] = [
            [
                row(
                    stream,
                    reduceOnly=True,
                    side="sell",
                    sz="2",
                    slTriggerPx="95",
                    slOrdPx="-1",
                    slTriggerPxType="mark",
                )
            ],
            [],
        ]

    result = materialize(source=packet(pending=False, changes=change))
    outstanding = projection(result, stream)
    assert outstanding.kind == "unsupported_algo"
    assert outstanding.reasons == ("outstanding_algo_unclassified",)
    assert "outstanding_algo_unclassified" in result.incomplete_reasons
    assert not any(item.kind == "protection" for item in result.projections)
    assert result.snapshot is None
    assert result.account_complete is False
    assert result.execution_authority is False


def test_top_level_usd_balance_is_never_relabelled_as_usdt():
    def change(pages):
        pages["balance"][0][0].update(totalEq="9999", availEq="8888")

    result = materialize(source=packet(changes=change))
    assert result.equity == D("1000") and result.available_margin == D("800")


def test_missing_currency_detail_cannot_fall_back_to_top_level_usd_totals():
    def change(pages):
        pages["balance"][0][0]["details"] = []

    result = materialize(source=packet(changes=change))
    assert result.equity is None and result.available_margin is None
    assert result.snapshot is None and result.incomplete_reasons
    assert len(result.positions) == 1
    assert projection(result, "positions").risk_amount == D("0.121")
    assert "single_currency_balance_required" in result.incomplete_reasons


@pytest.mark.parametrize("field", ["eq", "availEq", "liab", "borrowFroz", "uTime"])
def test_missing_required_balance_evidence_is_not_fabricated(field):
    def change(pages):
        pages["balance"][0][0]["details"][0].pop(field)

    result = materialize(source=packet(changes=change))
    baseline = materialize()
    assert result.snapshot is None
    assert set(result.incomplete_reasons) - set(baseline.incomplete_reasons)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ctType", "inverse"),
        ("ctValCcy", "USDT"),
        ("baseCcy", "ETH"),
        ("settleCcy", "BTC"),
        ("ctMult", "2"),
        ("instType", "FUTURES"),
        ("state", "suspend"),
    ],
)
def test_unsupported_instrument_units_or_state_never_produce_exposures(
    source, field, value
):
    result = materialize(
        source=source,
        supplied=inputs(instruments=(instrument(raw_instrument(**{field: value})),)),
    )
    assert result.instruments == ()
    assert result.positions == () and result.pending_reservations == ()
    assert result.snapshot is None and result.incomplete_reasons


@pytest.mark.parametrize(
    "field",
    [
        "ctVal",
        "ctMult",
        "ctValCcy",
        "settleCcy",
        "lotSz",
        "minSz",
        "maxLmtSz",
        "lever",
    ],
)
def test_missing_instrument_fields_do_not_default_to_one_or_symbol_inference(
    source, field
):
    raw = raw_instrument()
    raw.pop(field)
    result = materialize(source=source, supplied=inputs(instruments=(instrument(raw),)))
    assert result.instruments == ()
    assert result.positions == () and result.pending_reservations == ()
    assert result.incomplete_reasons


@pytest.mark.parametrize("missing", [False, True])
def test_linear_swap_base_currency_comes_from_ctvalccy_when_baseccy_is_empty_or_absent(
    source, missing
):
    raw = raw_instrument(baseCcy="")
    if missing:
        raw.pop("baseCcy")
    result = materialize(source=source, supplied=inputs(instruments=(instrument(raw),)))
    assert len(result.instruments) == 1
    spec = result.instruments[0]
    assert spec.base_currency == spec.contract_value_currency == "BTC"
    assert spec.settlement_currency == "USDT"
    assert result.positions[0].notional == D("2.02")
    assert result.positions[0].risk_amount == D("0.121")


@pytest.mark.parametrize(
    "field,value",
    [
        ("side", "buy"),
        ("posSide", "long"),
        ("sz", "1"),
        ("reduceOnly", False),
        ("slTriggerPxType", "last"),
        ("slOrdPx", "94"),
        ("slTriggerPx", "102"),
    ],
)
def test_wrong_protection_cannot_materialize_known_position_risk(field, value):
    def change(pages):
        pages["algo_conditional"][0][0][field] = value

    result = materialize(source=packet(changes=change))
    assert result.positions == ()
    assert projection(result, "positions").risk_amount is None
    assert projection(result, "positions").reasons


@pytest.mark.parametrize(
    "field", ["reduceOnly", "slTriggerPx", "slOrdPx", "slTriggerPxType", "tdMode"]
)
def test_missing_protection_fields_are_unknown_not_assumed_safe(field):
    def change(pages):
        pages["algo_conditional"][0][0].pop(field)

    result = materialize(source=packet(changes=change))
    assert result.positions == ()
    assert projection(result, "positions").risk_amount is None
    assert result.snapshot is None


def test_duplicate_matching_stops_do_not_prove_unique_protection():
    def change(pages):
        first = pages["algo_conditional"][0][0]
        pages["algo_conditional"][0].append({**first, "algoId": "909"})

    result = materialize(source=packet(changes=change))
    assert result.positions == ()
    assert projection(result, "positions").reasons


def test_short_position_uses_absolute_contracts_and_opposite_stop():
    def change(pages):
        pages["positions"][0][0]["pos"] = "-2"
        pages["account_position_risk"][0][0]["posData"][0]["pos"] = "-2"
        pages["algo_conditional"][0][0].update(side="buy", slTriggerPx="107")

    result = materialize(source=packet(pending=False, changes=change))
    assert len(result.positions) == 1
    assert result.positions[0].direction == "short"
    assert result.positions[0].notional == D("2.02")
    assert result.positions[0].risk_amount == D("0.121")
    assert projection(result, "positions").contracts == D("2")
    assert projection(result, "positions").base_quantity == D("0.02")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ordType", "market"),
        ("lever", ""),
        ("px", "0"),
        ("tdMode", "cash"),
    ],
)
def test_unsupported_pending_order_is_not_zero_risk(field, value):
    def change(pages):
        pages["orders_pending"][0][0][field] = value

    result = materialize(source=packet(changes=change))
    assert result.pending_reservations == ()
    assert projection(result, "orders_pending").reasons
    assert result.snapshot is None


def test_explicit_reducing_order_is_not_added_as_opening_exposure():
    def change(pages):
        pages["orders_pending"][0][0]["reduceOnly"] = True

    result = materialize(source=packet(changes=change))
    assert result.pending_reservations == ()
    assert projection(result, "orders_pending").kind == "reducing_order"
    assert result.snapshot is None


@pytest.mark.parametrize("field", ["px", "lever", "reduceOnly", "attachAlgoOrds"])
def test_missing_pending_price_leverage_or_attached_stop_stays_unknown(field):
    def change(pages):
        pages["orders_pending"][0][0].pop(field)

    result = materialize(source=packet(changes=change))
    assert result.pending_reservations == ()
    item = projection(result, "orders_pending")
    assert item.reasons
    if field in {"px", "attachAlgoOrds"}:
        assert item.risk_amount is None
    elif field == "lever":
        assert item.margin is None
        assert item.risk_amount == D("0.0505")


@pytest.mark.parametrize("precision", [2, 6, 28, 100])
def test_exact_components_ignore_ambient_decimal_precision_and_traps(source, precision):
    supplied = inputs()
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = materialize(source=source, supplied=supplied)
    assert result.positions[0].notional == D("2.02")
    assert result.positions[0].risk_amount == D("0.121")
    assert result.pending_reservations[0].risk_amount == D("0.0505")


def test_recurring_margin_rounds_up_once_without_using_rounded_base_quantity():
    def change(pages):
        pages["orders_pending"][0][0]["lever"] = "3"

    current = packet(changes=change)
    supplied = inputs()
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = materialize(source=current, supplied=supplied)
    assert result.pending_reservations[0].margin == D("0.33333333333333333334")
    assert result.pending_reservations[0].risk_amount == D("0.0505")


@pytest.mark.parametrize("pin", ["0" * 64, "", None, True, b"a" * 64])
def test_external_artifact_pin_is_required_and_is_not_internal_packet_hash(source, pin):
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, expected_packet_sha256=pin)
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, expected_packet_sha256=source[0].packet_sha256)


@pytest.mark.parametrize("pin", ["0" * 64, "", None, True])
def test_external_plan_pin_is_required(source, pin):
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, expected_plan_sha256=pin)


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", "700002"),
        ("settlement_currency", "USDC"),
        ("environment", "live"),
    ],
)
def test_inputs_must_match_exact_demo_uid_and_settlement_scope(source, field, value):
    with pytest.raises((module.AccountMaterializationError, ValidationError)):
        materialize(source=source, supplied=inputs(**{field: value}))


def test_instrument_wire_pin_mismatch_does_not_materialize_from_unverified_row(source):
    with pytest.raises(module.AccountMaterializationError):
        materialize(
            source=source,
            supplied=inputs(instruments=(instrument(expected_sha256="0" * 64),)),
        )


def test_missing_cost_and_correlation_never_defaults_to_zero_or_unknown_group(source):
    missing_cost = materialize(source=source, supplied=inputs(costs=()))
    assert missing_cost.positions == () and missing_cost.pending_reservations == ()
    assert projection(missing_cost, "positions").risk_amount is None
    missing_group = materialize(source=source, supplied=inputs(correlations=()))
    assert missing_group.positions == () and missing_group.pending_reservations == ()
    assert missing_group.snapshot is None


class Foreign:
    def __getattribute__(self, name):
        CALLBACKS.append("foreign attribute")
        raise AssertionError("foreign attribute executed")

    def __bool__(self):
        CALLBACKS.append("foreign truthiness")
        raise AssertionError("foreign truthiness executed")

    def __str__(self):
        CALLBACKS.append("foreign string")
        raise AssertionError("foreign string executed")


@pytest.mark.parametrize(
    "field",
    [
        "account_id",
        "instruments",
        "correlation_version",
        "costs",
        "ledger",
        "history",
        "peak",
    ],
)
def test_copied_input_foreign_fields_are_rejected_before_callbacks(source, field):
    supplied = inputs().model_copy(update={field: Foreign()})
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


@pytest.mark.parametrize("field", ["__pydantic_extra__", "__pydantic_private__"])
def test_hidden_input_metadata_is_rejected_without_truthiness(source, field):
    supplied = inputs()
    object.__setattr__(supplied, field, Foreign())
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


def test_foreign_input_serializer_is_never_called(source):
    class InputsTrap(module.AccountMaterializationInputs):
        @model_serializer
        def serialize(self):
            CALLBACKS.append("input serializer")
            raise AssertionError("input serializer executed")

    supplied = InputsTrap.model_construct(**inputs().__dict__)
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


def test_materialization_copy_freeze_and_full_source_replay(source):
    supplied = inputs()
    result = materialize(source=source, supplied=supplied)
    assert module.copy_materialization_inputs(supplied) == supplied
    assert module.copy_account_materialization_result(result) == result
    pins = {
        "expected_plan_sha256": source[0].plan_sha256,
        "expected_packet_sha256": source[1],
    }
    assert (
        module.verify_account_materialization(
            result, packet=source[0], inputs=supplied, **pins
        )
        == result
    )
    frozen = module.freeze_account_materialization(
        result, packet=source[0], inputs=supplied, **pins
    )
    restored = module.verify_frozen_account_materialization(
        frozen.payload,
        expected_sha256=frozen.sha256,
        packet=source[0],
        inputs=supplied,
        **pins,
    )
    assert restored == result
    with pytest.raises(module.AccountMaterializationError):
        module.verify_frozen_account_materialization(
            frozen.payload,
            expected_sha256="0" * 64,
            packet=source[0],
            inputs=supplied,
            **pins,
        )


def test_replay_rejects_changed_cost_without_rewriting_original_materialization(source):
    supplied = inputs()
    result = materialize(source=source, supplied=supplied)
    changed = inputs(costs=(cost(cost_per_base=D("0")),))
    assert module.materialization_inputs_sha256(
        changed
    ) != module.materialization_inputs_sha256(supplied)
    with pytest.raises(module.AccountMaterializationError):
        module.verify_account_materialization(
            result,
            packet=source[0],
            inputs=changed,
            expected_plan_sha256=source[0].plan_sha256,
            expected_packet_sha256=source[1],
        )
    assert result.positions[0].risk_amount == D("0.121")


@pytest.mark.parametrize("stream", ["positions", "orders_pending"])
@pytest.mark.parametrize("kind", ["FUTURES", "OPTION", "SPOT"])
def test_current_inventory_instrument_type_must_match_swap_spec(stream, kind):
    def change(pages):
        pages[stream][0][0]["instType"] = kind

    result = materialize(source=packet(changes=change))
    assert (
        result.positions == ()
        if stream == "positions"
        else result.pending_reservations == ()
    )
    assert projection(result, stream).reasons
    assert result.snapshot is None


@pytest.mark.parametrize(
    "field", ["execution_authority", "source_authenticity_verified", "account_complete"]
)
@pytest.mark.parametrize("value", [True, 1, 0, "false"])
def test_result_copy_cannot_grant_authority_through_model_copy(source, field, value):
    result = materialize(source=source).model_copy(update={field: value})
    with pytest.raises(module.AccountMaterializationError):
        module.copy_account_materialization_result(result)


def test_input_extra_complete_boolean_cannot_upgrade_missing_evidence(source):
    supplied = inputs().model_copy(update={"complete": True})
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


@pytest.mark.parametrize("field", ["equity", "available_margin"])
def test_replay_rejects_rehashed_or_unhashed_result_scalar_tampering(source, field):
    supplied = inputs()
    result = materialize(source=source, supplied=supplied)
    altered = result.model_copy(update={field: D("999")})
    with pytest.raises(module.AccountMaterializationError):
        module.verify_account_materialization(
            altered,
            packet=source[0],
            inputs=supplied,
            expected_plan_sha256=source[0].plan_sha256,
            expected_packet_sha256=source[1],
        )


@pytest.mark.parametrize("field", ["observed_at", "received_at"])
def test_future_cost_source_is_not_usable_for_known_risk(source, field):
    changes = {field: NOW + timedelta(days=1)}
    if field == "observed_at":
        changes["received_at"] = NOW + timedelta(days=1)
    supplied = inputs(costs=(cost(**changes),))
    result = materialize(source=source, supplied=supplied)
    assert result.positions == () and result.pending_reservations == ()
    assert projection(result, "positions").risk_amount is None
    assert projection(result, "orders_pending").risk_amount is None


def test_cost_source_observation_after_receipt_is_rejected_at_input_boundary(source):
    supplied = inputs(
        costs=(cost(observed_at=NOW + timedelta(days=1), received_at=NOW),)
    )
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


@pytest.mark.parametrize("field", ["instruments", "correlations", "costs"])
def test_duplicate_metadata_identity_cannot_silently_last_write_win(source, field):
    supplied = inputs()
    supplied = supplied.model_copy(update={field: getattr(supplied, field) * 2})
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


def ledger_evidence(*, active=(), account_id=UID):
    state = reservations.LedgerScopeState(
        scope=reservations.LedgerScope(
            account_id=account_id, settlement_currency="USDT"
        ),
        account_revision=2,
        ledger_revision=3,
        claims_sha256="d" * 64,
        active=active,
    )
    return module.LedgerEvidence(
        state=state,
        source=ObservedSource(
            source_sha256=reservations.digest(state),
            observed_at=NOW - timedelta(seconds=1),
            received_at=NOW,
        ),
    )


def history_evidence(**changes):
    return module.HistoryEvidence(
        **{
            "account_id": UID,
            "settlement_currency": "USDT",
            "source": ObservedSource(
                source_sha256="e" * 64,
                observed_at=NOW - timedelta(seconds=1),
                received_at=NOW,
            ),
            "history_start": NOW - timedelta(days=7),
            "history_end": NOW - timedelta(seconds=1),
            "loss_streak_at_history_start": 2,
            "groups": (),
            **changes,
        }
    )


def peak_evidence(**changes):
    samples = []
    for at, equity in (
        (NOW - timedelta(days=2), "1100"),
        (NOW - timedelta(days=1), "1200"),
    ):
        raw = row(
            "balance",
            uid=UID,
            uTime=ms(at),
            details=[usdt_detail(eq=equity, uTime=ms(at))],
        )
        samples.append(
            instrument(raw, observed_at=at, received_at=at + timedelta(milliseconds=1))
        )
    return module.PeakEvidence(
        **{
            "account_id": UID,
            "settlement_currency": "USDT",
            "window_started_at": NOW - timedelta(days=30),
            "samples": tuple(samples),
            **changes,
        }
    )


def snapshot_inputs(**changes):
    return inputs(
        **{
            "ledger": ledger_evidence(),
            "history": history_evidence(),
            "peak": peak_evidence(),
            **changes,
        }
    )


def expired_hold(*, state="uncertain"):
    candidate = reservations.ScenarioOperands(
        entry=D("100"),
        stop_loss=D("95"),
        cost_per_base=D("0.05"),
        contracts=D("1"),
        contract_value=D("0.01"),
        leverage=5,
    )
    execution = candidate.model_copy(update={"entry": D("101")})
    coverage = reservations.RiskCoverage(
        candidate=candidate,
        execution=execution,
        **reservations.exact_coverage(candidate, execution),
    )
    return reservations.ReservationReceipt(
        scope=reservations.LedgerScope(account_id=UID, settlement_currency="USDT"),
        reservation_id="1" * 64,
        original_event_key="2" * 64,
        report_id="synthetic-recorded-reservation",
        instrument_id=INSTRUMENT,
        direction="long",
        correlation_group=GROUP,
        request_sha256="3" * 64,
        coverage=coverage,
        state=state,
        state_revision=2,
        account_revision=2,
        ledger_revision=3,
        created_at=NOW - timedelta(minutes=3),
        updated_at=NOW - timedelta(minutes=1),
        deadline=NOW - timedelta(minutes=2),
    )


def test_incomplete_packet_keeps_diagnostics_without_portfolio_snapshot(source):
    supplied = snapshot_inputs()
    result = materialize(source=source, supplied=supplied)
    assert source[0].incomplete_reasons
    assert result.packet_sha256 == source[1]
    assert result.inputs_sha256 == module.materialization_inputs_sha256(supplied)
    assert set(source[0].incomplete_reasons) <= set(result.incomplete_reasons)
    assert result.equity == D("1000") and result.available_margin == D("800")
    assert len(result.positions) == len(result.pending_reservations) == 1
    assert result.loss_history == ()
    assert projection(result, "positions").risk_amount == D("0.121")
    assert projection(result, "orders_pending").risk_amount == D("0.0505")
    assert result.snapshot is None
    assert result.account_complete is False
    assert result.source_authenticity_verified is False
    assert result.execution_authority is False
    assert "continuous_peak_window_unverified" in result.incomplete_reasons
    assert "history_ingestion_watermark_unverified" in result.incomplete_reasons


def test_local_ledger_without_persisted_account_claims_keeps_hold_but_denies_snapshot(
    source,
):
    receipt = expired_hold()
    ledger = ledger_evidence(active=(receipt,))
    state = ledger.state.model_copy(update={"claims_sha256": None})
    unanchored = ledger.model_copy(
        update={
            "state": state,
            "source": ledger.source.model_copy(
                update={"source_sha256": reservations.digest(state)}
            ),
        }
    )
    result = materialize(source=source, supplied=snapshot_inputs(ledger=unanchored))
    assert "local_ledger_claims_missing" in result.incomplete_reasons
    assert any(
        item.reservation_id == "local:" + receipt.reservation_id
        for item in result.pending_reservations
    )
    assert result.snapshot is None
    assert result.account_complete is False
    assert result.execution_authority is False


def test_local_ledger_revision_behind_account_revision_is_incomplete(source):
    ledger = ledger_evidence()
    state = ledger.state.model_copy(update={"ledger_revision": 1})
    stale = ledger.model_copy(
        update={
            "state": state,
            "source": ledger.source.model_copy(
                update={"source_sha256": reservations.digest(state)}
            ),
        }
    )
    result = materialize(source=source, supplied=snapshot_inputs(ledger=stale))
    assert "local_ledger_revision_missing" in result.incomplete_reasons
    assert result.snapshot is None
    assert result.account_complete is False
    assert result.execution_authority is False


@pytest.mark.parametrize("field", ["ledger", "history", "peak"])
def test_supplemental_evidence_cannot_cross_account_uid(source, field):
    supplied = snapshot_inputs()
    if field == "ledger":
        replacement = ledger_evidence(account_id="700002")
    else:
        replacement = getattr(supplied, field).model_copy(
            update={"account_id": "700002"}
        )
    supplied = supplied.model_copy(update={field: replacement})
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=supplied)


@pytest.mark.parametrize("state", ["reserved", "consumed", "uncertain"])
def test_expired_local_reservation_is_retained_at_full_dual_scenario_coverage(
    source, state
):
    receipt = expired_hold(state=state)
    supplied = snapshot_inputs(ledger=ledger_evidence(active=(receipt,)))
    result = materialize(source=source, supplied=supplied)
    assert receipt.deadline < source[0].completed_at
    assert len(result.pending_reservations) == 2
    held = next(
        item
        for item in result.pending_reservations
        if item.reservation_id == "local:" + receipt.reservation_id
    )
    assert held.notional == D("1.01") == receipt.coverage.notional_amount
    assert held.margin == D("0.202") == receipt.coverage.margin_amount
    assert held.risk_amount == D("0.0605") == receipt.coverage.risk_amount
    assert projection(result, "local_ledger").risk_amount == held.risk_amount
    assert result.snapshot is None
    assert result.account_complete is False


def test_recorded_diagnostics_freeze_replay_preserves_pins_and_denial(source):
    supplied = snapshot_inputs()
    result = materialize(source=source, supplied=supplied)
    pins = {
        "expected_plan_sha256": source[0].plan_sha256,
        "expected_packet_sha256": source[1],
    }
    frozen = module.freeze_account_materialization(
        result, packet=source[0], inputs=supplied, **pins
    )
    restored = module.verify_frozen_account_materialization(
        frozen.payload,
        expected_sha256=frozen.sha256,
        packet=source[0],
        inputs=supplied,
        **pins,
    )
    assert restored == result
    assert restored.packet_sha256 == source[1]
    assert restored.inputs_sha256 == module.materialization_inputs_sha256(supplied)
    assert restored.snapshot is None
    assert restored.positions == result.positions
    assert restored.projections == result.projections
    assert set(source[0].incomplete_reasons) <= set(restored.incomplete_reasons)


def test_older_balance_update_does_not_grant_complete_account():
    def change(pages):
        pages["balance"][0][0]["uTime"] = ms(NOW - timedelta(seconds=2))

    result = materialize(source=packet(changes=change), supplied=snapshot_inputs())
    assert result.equity == D("1000")
    assert result.snapshot is None
    assert result.account_complete is False


def test_peak_raw_identity_mismatch_is_not_hidden_by_matching_evidence_scope(source):
    evidence = peak_evidence()
    raw = json.loads(evidence.samples[0].raw_response)
    raw["uid"] = "700002"
    bad_sample = instrument(
        raw,
        observed_at=evidence.samples[0].observed_at,
        received_at=evidence.samples[0].received_at,
    )
    evidence = evidence.model_copy(
        update={"samples": (bad_sample, evidence.samples[1])}
    )
    with pytest.raises(module.AccountMaterializationError):
        materialize(source=source, supplied=snapshot_inputs(peak=evidence))


ENTRY_FILL_AT = NOW - timedelta(hours=2)
EXIT_FILL_AT = NOW - timedelta(hours=1)


def history_source(
    *,
    funding=False,
    entry_fee="-0.01",
    exit_fee="-0.01",
    exit_quantity="1",
    exit_price="120",
    exit_pnl="0.2",
    wrong_fee_side=None,
    recent_only=False,
    overlap=False,
    overlap_conflict=False,
):
    def change(pages):
        entry = row(
            "fills_history",
            "901",
            ordId="801",
            tradeId="701",
            side="buy",
            fillPx="100",
            fillSz="1",
            fillPnl="0",
            fee=entry_fee,
            feeCcy="BTC" if wrong_fee_side == "entry" else "USDT",
            fillTime=ms(ENTRY_FILL_AT),
            ts=ms(ENTRY_FILL_AT + timedelta(seconds=1)),
            cTime=ms(NOW - timedelta(days=2)),
        )
        exit_fill = row(
            "fills_history",
            "902",
            ordId="802",
            tradeId="702",
            side="sell",
            fillPx=exit_price,
            fillSz=exit_quantity,
            fillPnl=exit_pnl,
            fee=exit_fee,
            feeCcy="BTC" if wrong_fee_side == "exit" else "USDT",
            fillTime=ms(EXIT_FILL_AT),
            ts=ms(EXIT_FILL_AT + timedelta(seconds=1)),
            cTime=ms(NOW - timedelta(days=1)),
        )
        # Provider pages descend by billId; outcome chronology uses fillTime.
        pages["fills_history"] = [[exit_fill, entry], []]
        if recent_only or overlap or overlap_conflict:
            recent_exit = (
                {**exit_fill, "fee": "-0.05"} if overlap_conflict else exit_fill
            )
            pages["fills_recent"] = [[recent_exit, entry], []]
        if recent_only:
            pages["fills_history"] = [[]]
        if funding:
            pages["bills_archive"] = [
                [
                    row(
                        "bills_archive",
                        "903",
                        type="8",
                        subType="173",
                        instId=INSTRUMENT,
                        ccy="USDT",
                        balChg="-0.03",
                        fee="-7",
                        pnl="99",
                        ts=ms(NOW - timedelta(minutes=90)),
                    )
                ],
                [],
            ]

    return packet(changes=change)


def nonempty_history_inputs(*, funding=False, sequence=0, fill_ids=("901", "902")):
    group = module.OutcomeGroup(
        outcome_id="synthetic-recorded-closed-outcome",
        sequence=sequence,
        fill_ids=fill_ids,
        funding_bill_ids=("903",) if funding else (),
    )
    return snapshot_inputs(history=history_evidence(groups=(group,)))


def test_nonempty_fills_derive_net_outcome_at_actual_exit_fill_time():
    result = materialize(source=history_source(), supplied=nonempty_history_inputs())
    assert len(result.loss_history) == 1
    outcome = result.loss_history[0]
    assert outcome.instrument_id == INSTRUMENT
    assert outcome.realized_pnl == D("0.18")
    assert outcome.closed_at == EXIT_FILL_AT
    assert outcome.closed_at != NOW - timedelta(days=1)
    assert outcome.sequence == 0
    assert result.snapshot is None
    assert "history_ingestion_watermark_unverified" in result.incomplete_reasons
    assert result.account_complete is False


@pytest.mark.parametrize(
    ("source_change", "expected"),
    [
        ({"exit_pnl": "0.19"}, "closed_outcome_gross_reconciliation_mismatch"),
        ({"exit_price": ""}, "closed_outcome_mapping_incomplete"),
    ],
)
def test_closed_outcome_requires_source_price_and_independent_gross_reconciliation(
    source_change, expected
):
    result = materialize(
        source=history_source(**source_change), supplied=nonempty_history_inputs()
    )
    assert result.loss_history == ()
    assert result.snapshot is None
    assert expected in result.incomplete_reasons
    assert result.account_complete is False and result.execution_authority is False


@pytest.mark.parametrize("recent_only,overlap", [(True, False), (False, True)])
def test_recent_fills_are_accounted_and_identical_history_overlap_is_not_double_charged(
    recent_only, overlap
):
    result = materialize(
        source=history_source(recent_only=recent_only, overlap=overlap),
        supplied=nonempty_history_inputs(),
    )
    assert len(result.loss_history) == 1
    assert result.loss_history[0].realized_pnl == D("0.18")
    assert "history_fill_inventory_unmapped" not in result.incomplete_reasons
    assert result.account_complete is False


@pytest.mark.parametrize("truncated", ["start", "end"])
def test_history_seed_must_cover_exact_pinned_query_window(truncated):
    groups = nonempty_history_inputs().history.groups
    if truncated == "start":
        evidence = history_evidence(
            history_start=NOW - timedelta(days=6), groups=groups
        )
    else:
        shortened_end = NOW - timedelta(seconds=2)
        evidence = history_evidence(
            history_end=shortened_end,
            groups=groups,
            source=ObservedSource(
                source_sha256="e" * 64,
                observed_at=shortened_end,
                received_at=NOW,
            ),
        )
    result = materialize(
        source=history_source(), supplied=snapshot_inputs(history=evidence)
    )
    assert result.loss_history == ()
    assert result.snapshot is None
    assert "history_recorded_window_incomplete" in result.incomplete_reasons


def test_conflicting_recent_and_archive_fill_is_incomplete_without_selecting_a_winner():
    result = materialize(
        source=history_source(overlap_conflict=True), supplied=nonempty_history_inputs()
    )
    assert "history_overlapping_source_conflict" in result.incomplete_reasons
    assert result.loss_history == ()
    assert result.snapshot is None


@pytest.mark.parametrize(
    "stream", ["account_instruments", "leverage_cross", "leverage_isolated"]
)
def test_missing_account_metadata_cannot_materialize_a_snapshot(stream):
    def change(pages):
        pages[stream] = [[]]

    result = materialize(source=packet(changes=change), supplied=snapshot_inputs())
    assert result.snapshot is None
    expected = (
        "account_metadata_instrument_coverage_incomplete"
        if stream == "account_instruments"
        else "account_leverage_coverage_incomplete"
    )
    assert expected in result.incomplete_reasons


def test_external_instrument_spec_cannot_override_new_account_capture():
    def change(pages):
        pages["account_instruments"] = [[row("account_instruments", ctVal="1")]]

    result = materialize(source=packet(changes=change), supplied=snapshot_inputs())
    assert "account_instrument_source_conflict" in result.incomplete_reasons
    assert result.snapshot is None


def test_conflicting_recent_and_archive_order_cannot_be_hidden_by_fill_only_mapping():
    def change(pages):
        pages["orders_history_recent"] = [
            [row("orders_history_recent", "850", avgPx="101")],
            [],
        ]
        pages["orders_history_archive"] = [
            [row("orders_history_archive", "850", avgPx="100")],
            [],
        ]

    result = materialize(source=packet(changes=change), supplied=snapshot_inputs())
    assert "history_overlapping_source_conflict" in result.incomplete_reasons
    assert result.loss_history == ()
    assert result.snapshot is None


def test_funding_cash_bill_time_cannot_be_used_as_holding_accrual_evidence():
    source = history_source(funding=True)
    result = materialize(
        source=source,
        supplied=nonempty_history_inputs(funding=True),
    )
    assert result.loss_history == ()
    assert result.snapshot is None
    assert "funding_accrual_provenance_missing" in result.incomplete_reasons
    bills = [
        o
        for o in source[0].observations
        if o.request.stream == "bills_archive" and o.rows
    ]
    assert json.loads(bills[0].rows[0].canonical_json)["balChg"] == "-0.03"


@pytest.mark.parametrize(
    "entry_fee,exit_fee,expected", [("0.01", "0.02", "0.23"), ("-0.01", "0.02", "0.21")]
)
def test_fee_and_rebate_signs_are_preserved(entry_fee, exit_fee, expected):
    result = materialize(
        source=history_source(entry_fee=entry_fee, exit_fee=exit_fee),
        supplied=nonempty_history_inputs(),
    )
    assert result.loss_history[0].realized_pnl == D(expected)
    assert result.loss_history[0].closed_at == EXIT_FILL_AT


def test_partial_close_cannot_be_promoted_to_a_closed_outcome():
    result = materialize(
        source=history_source(exit_quantity="0.5"), supplied=nonempty_history_inputs()
    )
    assert result.loss_history == ()
    assert result.snapshot is None
    assert "closed_outcome_mapping_incomplete" in result.incomplete_reasons


@pytest.mark.parametrize("side", ["entry", "exit"])
def test_fee_in_another_currency_cannot_be_added_as_settlement_pnl(side):
    result = materialize(
        source=history_source(wrong_fee_side=side), supplied=nonempty_history_inputs()
    )
    assert result.loss_history == ()
    assert result.snapshot is None
    assert "closed_outcome_mapping_incomplete" in result.incomplete_reasons


def test_duplicate_fill_identity_cannot_be_charged_or_counted_twice():
    with pytest.raises(module.AccountMaterializationError):
        materialize(
            source=history_source(),
            supplied=nonempty_history_inputs(fill_ids=("901", "901")),
        )


def test_outcome_uses_source_fill_chronology_not_group_id_order():
    result = materialize(
        source=history_source(),
        supplied=nonempty_history_inputs(fill_ids=("902", "901")),
    )
    assert result.loss_history[0].realized_pnl == D("0.18")
    assert result.loss_history[0].closed_at == EXIT_FILL_AT


def test_maximum_legal_outcome_sequence_survives_frozen_json_roundtrip():
    current, pin = history_source()
    supplied = nonempty_history_inputs(sequence=10**15)
    result = materialize(source=(current, pin), supplied=supplied)
    assert result.loss_history[0].sequence == 10**15
    replay = {
        "packet": current,
        "inputs": supplied,
        "expected_plan_sha256": current.plan_sha256,
        "expected_packet_sha256": pin,
    }
    frozen = module.freeze_account_materialization(result, **replay)
    restored = module.verify_frozen_account_materialization(
        frozen.payload, expected_sha256=frozen.sha256, **replay
    )
    assert restored == result
    assert restored.snapshot is None
    assert restored.loss_history[0].sequence == 10**15


@pytest.mark.parametrize("case", ["future", "missing", "before_window"])
def test_peak_currency_update_time_cannot_be_hidden_by_valid_account_update(
    source, case
):
    evidence = peak_evidence()
    sample = evidence.samples[1]
    raw = json.loads(sample.raw_response)
    observed_at = sample.observed_at
    if case == "future":
        raw["details"][0]["uTime"] = ms(NOW + timedelta(days=1))
    elif case == "missing":
        raw["details"][0].pop("uTime")
    else:
        observed_at = evidence.window_started_at - timedelta(days=1)
        raw["details"][0]["uTime"] = ms(observed_at)
    changed = instrument(raw, observed_at=observed_at, received_at=sample.received_at)
    evidence = evidence.model_copy(update={"samples": (evidence.samples[0], changed)})
    result = materialize(source=source, supplied=snapshot_inputs(peak=evidence))
    assert result.snapshot is None
    assert "peak_sample_mapping_incomplete" in result.incomplete_reasons
    assert result.equity == D("1000")


def test_peak_uses_earlier_valid_currency_update_not_later_account_time(source):
    evidence = peak_evidence()
    sample = evidence.samples[1]
    raw = json.loads(sample.raw_response)
    currency_updated = sample.observed_at - timedelta(hours=1)
    raw["details"][0]["uTime"] = ms(currency_updated)
    changed = instrument(
        raw, observed_at=currency_updated, received_at=sample.received_at
    )
    evidence = evidence.model_copy(update={"samples": (evidence.samples[0], changed)})
    result = materialize(source=source, supplied=snapshot_inputs(peak=evidence))
    assert result.snapshot is None
    assert result.equity == D("1000")
    assert "peak_sample_mapping_incomplete" not in result.incomplete_reasons
    peak_gaps = set()
    peak = module._peak(
        snapshot_inputs(peak=evidence), result.equity, source[0], peak_gaps
    )
    assert peak[0] == D("1200")
    assert peak[1] == currency_updated != sample.observed_at
