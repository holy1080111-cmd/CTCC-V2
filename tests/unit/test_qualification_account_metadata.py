"""Source-row mapping under synthetic replay/owned transports; no account IO."""

import hashlib
import json
from decimal import Decimal

import pytest

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_metadata as module
from app.trade_qualification import account_runtime as runtime
from tests.unit import test_qualification_account_capture as old
from tests.unit import test_qualification_account_materializer as fixtures
from tests.unit import test_qualification_account_v4 as v4
from tests.unit.test_qualification_account_runtime import regional, setup


def source(*, pages=None, selected=None):
    values = fixtures.source_pages()
    values["account_instruments"] = [[old.row("account_instruments", tickSz="0.1")]]
    values.update({} if pages is None else pages)
    packet = old.verify(*old.records(pages=values, selected=selected))
    return packet


def derive(packet=None, supplied=None, **changes):
    packet = source() if packet is None else packet
    supplied = fixtures.inputs(instruments=()) if supplied is None else supplied
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    return module.derive_captured_instrument_metadata(
        packet,
        **{
            "expected_packet_sha256": frozen.sha256,
            "expected_plan_sha256": packet.plan_sha256,
            "inputs": supplied,
            "expected_inputs_sha256": mapping.materialization_inputs_sha256(supplied),
            **changes,
        },
    )


@pytest.mark.parametrize("selected", [old.plan(), regional()])
def test_packet_rows_produce_full_exact_metadata_without_caller_rows(selected):
    packet = source(selected=selected)
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    supplied = fixtures.inputs(instruments=())
    result = derive(packet, supplied)
    assert supplied.instruments == ()
    assert result.required_instrument_ids == (old.INSTRUMENT,)
    assert result.missing_instrument_ids == result.unsupported_instrument_ids == ()
    assert result.blocking_reasons == ()
    observation = next(
        item
        for item in packet.observations
        if item.request.stream == "account_instruments"
    )
    evidence = result.inputs.instruments[0]
    assert evidence.raw_response == observation.rows[0].canonical_json.encode()
    assert evidence.expected_sha256 == hashlib.sha256(evidence.raw_response).hexdigest()
    assert evidence.observed_at == evidence.received_at == observation.body_completed_at
    row = json.loads(evidence.raw_response)
    assert {
        name: row[name]
        for name in (
            "instType",
            "ctType",
            "ctVal",
            "ctValCcy",
            "baseCcy",
            "settleCcy",
            "lotSz",
            "minSz",
            "tickSz",
        )
    } == {
        "instType": "SWAP",
        "ctType": "linear",
        "ctVal": "0.01",
        "ctValCcy": "BTC",
        "baseCcy": "BTC",
        "settleCcy": "USDT",
        "lotSz": "0.1",
        "minSz": "0.1",
        "tickSz": "0.1",
    }
    receipt = json.loads(result.receipt_json)
    assert (
        receipt["row_bindings"][0]["page_receipt_sha256"] == observation.receipt_sha256
    )
    assert (
        receipt["row_bindings"][0]["timestamp_semantics"] == "measured_body_completion"
    )
    assert receipt["derived_inputs_sha256"] == mapping.materialization_inputs_sha256(
        result.inputs
    )
    assert receipt["source_authenticity_verified"] is False
    assert receipt["account_complete"] is receipt["execution_authority"] is False
    assert packet.plan.expected_uid not in result.receipt_json.decode()
    assert derive(packet, supplied).receipt_json == result.receipt_json
    assert (
        capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=packet.plan_sha256
        )
        == frozen
    )
    gaps = set()
    spec = mapping._instruments(result.inputs, packet.completed_at, gaps)[
        old.INSTRUMENT
    ]
    assert spec.contract_value == Decimal("0.01")
    assert spec.contract_value_currency == "BTC" and spec.settlement_currency == "USDT"
    assert spec.lot_size == Decimal("0.1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("instType", "SPOT"),
        ("ctType", "inverse"),
        ("ctVal", "1"),
        ("ctValCcy", "USDT"),
        ("ctMult", "10"),
        ("lotSz", "1"),
        ("minSz", "1"),
        ("maxLmtSz", "10000"),
        ("lever", "125"),
        ("tickSz", "0.001"),
        ("state", "suspend"),
        ("baseCcy", "ETH"),
        ("settleCcy", "BTC"),
        ("quoteCcy", "USDT"),
        ("fake", True),
    ],
)
def test_supplied_metadata_cannot_override_source_field_or_insert_missing_field(
    field, value
):
    supplied = fixtures.inputs(
        instruments=(fixtures.instrument(fixtures.raw_instrument(**{field: value})),)
    )
    with pytest.raises(
        module.CapturedMetadataError, match="supplied_metadata_source_conflict"
    ):
        derive(supplied=supplied)


def test_matching_partial_assertion_is_replaced_by_full_captured_row():
    supplied = fixtures.inputs(
        instruments=(fixtures.instrument({"instId": old.INSTRUMENT, "ctVal": "0.01"}),)
    )
    result = derive(supplied=supplied)
    assert "tickSz" in json.loads(result.inputs.instruments[0].raw_response)
    assert (
        result.inputs.instruments[0].received_at != supplied.instruments[0].received_at
    )


def test_duplicate_supplied_identity_rejected():
    supplied = fixtures.inputs(
        instruments=(fixtures.instrument(), fixtures.instrument())
    )
    with pytest.raises(
        module.CapturedMetadataError, match="duplicate_supplied_metadata_identity"
    ):
        derive(supplied=supplied)


def test_missing_requested_metadata_stays_missing_without_fallback():
    packet = source(pages={"account_instruments": [[]]})
    result = derive(packet)
    assert result.inputs.instruments == ()
    assert result.missing_instrument_ids == (old.INSTRUMENT,)
    assert "captured_instrument_metadata_missing" in result.blocking_reasons
    with pytest.raises(
        module.CapturedMetadataError, match="supplied_metadata_source_missing"
    ):
        derive(packet, fixtures.inputs())


@pytest.mark.parametrize(
    "stream", ["positions", "fills_history", "bills_archive", "account_position_risk"]
)
def test_required_coverage_includes_unplanned_exposure_history_bills_and_aggregate_inventory(
    stream,
):
    symbol = "ETH-USDT-SWAP"
    if stream == "account_position_risk":
        values = [
            [
                old.row(
                    stream,
                    posData=[{"posId": "88", "instId": symbol, "instType": "SWAP"}],
                )
            ]
        ]
    else:
        values = [[old.row(stream, instId=symbol)]]
        if stream in old.CURSORS:
            values.append([])
    result = derive(source(pages={stream: values}))
    assert symbol in result.required_instrument_ids
    assert symbol in result.missing_instrument_ids
    assert "captured_instrument_metadata_missing" in result.blocking_reasons
    if stream == "bills_archive":
        assert "captured_instrument_type_unknown" in result.blocking_reasons
        assert symbol in json.loads(result.receipt_json)["unknown_type_instrument_ids"]


@pytest.mark.parametrize("tick", [None, "", "0"])
def test_missing_tick_never_becomes_a_valid_default(tick):
    packet = source(
        pages={"account_instruments": [[old.row("account_instruments", tickSz=tick)]]}
    )
    result = derive(packet)
    assert "captured_instrument_tick_missing" in result.blocking_reasons
    assert json.loads(result.inputs.instruments[0].raw_response)["tickSz"] == tick


def test_inverse_contract_is_preserved_without_linear_conversion():
    row = old.row(
        "account_instruments",
        ctType="inverse",
        ctValCcy="USD",
        ctVal="100",
        settleCcy="BTC",
        tickSz="0.1",
    )
    packet = source(pages={"account_instruments": [[row]]})
    result = derive(packet)
    assert json.loads(result.inputs.instruments[0].raw_response) == row
    gaps = set()
    assert mapping._instruments(result.inputs, packet.completed_at, gaps) == {}
    assert "instrument_mapping_incomplete" in gaps


def test_v4_non_swap_histories_require_real_metadata_and_remain_unsupported():
    packet = v4.verify()
    result = derive(packet)
    assert len(result.required_instrument_ids) == 6
    assert (
        len(result.unsupported_instrument_ids)
        == len(result.missing_instrument_ids)
        == 5
    )
    assert "captured_instrument_product_unsupported" in result.blocking_reasons
    assert tuple(
        json.loads(item.raw_response)["instType"] for item in result.inputs.instruments
    ) == ("SWAP",)


def test_spot_and_margin_same_instrument_id_are_both_preserved_as_unsupported():
    symbol = "BTC-USDT"
    packet = v4.verify(
        pages={
            "fills_history_margin": [
                [{**v4.product_row("fills_history", "MARGIN"), "instId": symbol}],
                [],
            ]
        }
    )
    result = derive(packet)
    assert symbol in result.unsupported_instrument_ids
    assert symbol in result.missing_instrument_ids


def test_swap_product_identity_cannot_conflict_with_source_spot_row():
    packet = v4.verify(
        pages={
            "fills_history_spot": [
                [{**v4.product_row("fills_history", "SPOT"), "instId": old.INSTRUMENT}],
                [],
            ]
        }
    )
    with pytest.raises(
        module.CapturedMetadataError, match="captured_metadata_type_conflict"
    ):
        derive(packet)


@pytest.mark.parametrize(
    "field",
    ["expected_packet_sha256", "expected_plan_sha256", "expected_inputs_sha256"],
)
def test_all_external_pins_are_required(field):
    with pytest.raises(module.CapturedMetadataError, match="captured_metadata_invalid"):
        derive(**{field: "0" * 64})


def test_account_scope_must_match_exact_uid_not_main_uid():
    with pytest.raises(
        module.CapturedMetadataError, match="captured_metadata_scope_mismatch"
    ):
        derive(supplied=fixtures.inputs(account_id="9999"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_complete", True),
        ("source_authenticity_verified", True),
        ("execution_authority", True),
    ],
)
def test_forged_packet_authority_is_rejected(field, value):
    packet = source().model_copy(update={field: value})
    with pytest.raises(module.CapturedMetadataError):
        module.derive_captured_instrument_metadata(
            packet,
            expected_plan_sha256=packet.plan_sha256,
            expected_packet_sha256="0" * 64,
            inputs=fixtures.inputs(),
            expected_inputs_sha256="0" * 64,
        )


def test_instrument_budget_rejects_instead_of_truncating_inventory():
    packet = source(
        pages={
            "account_position_risk": [
                [
                    old.row(
                        "account_position_risk",
                        posData=[
                            {
                                "posId": str(1000 + i),
                                "instId": f"ASSET{i}-USDT-SWAP",
                                "instType": "SWAP",
                            }
                            for i in range(129)
                        ],
                    )
                ]
            ]
        }
    )
    with pytest.raises(
        module.CapturedMetadataError, match="captured_metadata_budget_exceeded"
    ):
        derive(packet)


def test_hostile_packet_subclass_cannot_execute_serializer():
    callbacks = []

    class Foreign(capture.DemoAccountPacket):
        def model_dump(self, *args, **kwargs):
            callbacks.append(True)
            raise AssertionError("must never be invoked")

    packet = source()
    foreign = Foreign.model_construct(**packet.__dict__)
    with pytest.raises(module.CapturedMetadataError):
        module.derive_captured_instrument_metadata(
            foreign,
            expected_plan_sha256=packet.plan_sha256,
            expected_packet_sha256="0" * 64,
            inputs=fixtures.inputs(),
            expected_inputs_sha256="0" * 64,
        )
    assert not callbacks


@pytest.mark.asyncio
async def test_owned_runtime_materializes_captured_metadata_without_caller_rows(
    monkeypatch,
):
    session, harness, _, arguments = setup(monkeypatch)
    arguments["inputs"] = fixtures.snapshot_inputs(ledger=None, instruments=())
    arguments["expected_inputs_sha256"] = mapping.materialization_inputs_sha256(
        arguments["inputs"]
    )
    result = await session.collect_and_materialize(**arguments)
    assert len(result.materialization_inputs.instruments) == 1
    assert result.materialization.snapshot is None
    assert result.materialization.incomplete_reasons
    assert result.materialization.instruments
    assert result.transport_provenance == "synthetic_transport"
    assert result.admission == "DENY" and result.execution_authority is False
    receipt = json.loads(result.receipt_json)
    assert receipt["schema_version"] == "ctcc.controlled_demo_account_runtime.v2"
    assert (
        receipt["instrument_metadata_sha256"]
        == result.instrument_metadata.receipt_sha256
    )
    assert "captured_instrument_tick_missing" in result.blocking_reasons
    harness.assert_closed()


@pytest.mark.asyncio
async def test_owned_runtime_rejects_conflicting_caller_metadata_without_order_io(
    monkeypatch,
):
    session, harness, _, arguments = setup(monkeypatch)
    arguments["inputs"] = fixtures.snapshot_inputs(
        ledger=None,
        instruments=(fixtures.instrument(fixtures.raw_instrument(ctVal="100")),),
    )
    arguments["expected_inputs_sha256"] = mapping.materialization_inputs_sha256(
        arguments["inputs"]
    )
    with pytest.raises(runtime.AccountRuntimeError, match="captured_metadata_invalid"):
        await session.collect_and_materialize(**arguments)
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
