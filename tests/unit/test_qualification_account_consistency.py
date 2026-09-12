"""Synthetic source crosschecks only; no authenticated accounts, clock or IO."""

from __future__ import annotations

import ast
import inspect
from datetime import timedelta
from decimal import Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError, model_serializer

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_consistency as module
from tests.unit.test_qualification_account_capture import INSTRUMENT, NOW, ms
from tests.unit.test_qualification_account_materializer import (
    materialize,
    packet,
    snapshot_inputs,
    usdt_detail,
)


def reconcile(source=None, **kwargs):
    current, pin = packet() if source is None else source
    return module.reconcile_recorded_account_sources(
        current,
        **{
            "expected_plan_sha256": current.plan_sha256,
            "expected_packet_sha256": pin,
            **kwargs,
        },
    )


def changed_row(target, field, value):
    def mutate(pages):
        if target == "anchor_balance":
            target_row = pages["account_position_risk"][0][0]["balData"][0]
        elif target == "anchor_position":
            target_row = pages["account_position_risk"][0][0]["posData"][0]
        elif target == "currency_balance":
            target_row = pages["balance"][0][0]["details"][0]
        else:
            target_row = pages[target][0][0]
        if value is _MISSING:
            target_row.pop(field, None)
        else:
            target_row[field] = value

    return packet(changes=mutate)


_MISSING = object()


def test_matching_recorded_sources_do_not_grant_authentication_or_atomicity():
    current, pin = packet()
    result = reconcile((current, pin))
    assert result.findings == result.blocking_reasons == ()
    assert result.packet_sha256 == pin
    assert result.anchor_observed_at == NOW - timedelta(seconds=1)
    assert result.account_complete is False
    assert result.source_authenticity_verified is False
    assert result.execution_authority is False
    assert "source_authenticity_unverified" in result.unverified_boundaries
    assert "cross_source_atomicity_unverified" in result.unverified_boundaries
    assert (
        "account_scope_and_history_completeness_unverified"
        in result.unverified_boundaries
    )
    receipts = {
        item.request.stream: item.receipt_sha256 for item in current.observations
    }
    assert result.anchor_receipt_sha256 == receipts["account_position_risk"]
    assert result.balance_receipt_sha256 == receipts["balance"]
    assert result.position_receipt_sha256s == (receipts["positions"],)
    assert result == reconcile((current, pin))


@pytest.mark.parametrize("field", ["eq"])
@pytest.mark.parametrize("target", ["anchor_balance", "currency_balance"])
def test_shared_currency_values_must_match_exactly(field, target):
    result = reconcile(changed_row(target, field, "999.99999999999999999999"))
    assert result.blocking_reasons == ("account_anchor_balance_field_conflict",)
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.field == field and finding.row_id == "USDT"
    assert finding.status == "conflict" and finding.right_stream == "balance"
    assert finding.left_receipt_sha256 == result.anchor_receipt_sha256
    assert finding.right_receipt_sha256 == result.balance_receipt_sha256


@pytest.mark.parametrize("field", ["eq"])
@pytest.mark.parametrize("target", ["anchor_balance", "currency_balance"])
@pytest.mark.parametrize("value", [_MISSING, None, ""])
def test_missing_numeric_fields_never_match_as_none_or_zero(field, target, value):
    result = reconcile(changed_row(target, field, value))
    assert result.blocking_reasons == ("account_anchor_balance_field_missing",)
    assert result.findings[0].status == "missing"


@pytest.mark.parametrize("field", ["eq"])
def test_equivalent_decimal_encodings_use_exact_values_not_context_rounding(field):
    source = changed_row("anchor_balance", field, "1000.00000000000000000000")
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        assert reconcile(source).findings == ()


@pytest.mark.parametrize("field", ["totalEq", "availEq", "adjEq"])
def test_usd_top_level_values_are_not_compared_as_settlement_amounts(field):
    stream = "account_position_risk" if field == "adjEq" else "balance"
    result = reconcile(changed_row(stream, field, "99999"))
    assert result.findings == ()


@pytest.mark.parametrize("which", ["anchor", "balance"])
def test_extra_currency_is_not_silently_filtered_out(which):
    def change(pages):
        if which == "anchor":
            pages["account_position_risk"][0][0]["balData"].append(
                {"ccy": "BTC", "eq": "0", "cashBal": "0"}
            )
        else:
            pages["balance"][0][0]["details"].append(
                usdt_detail(ccy="BTC", eq="0", cashBal="0")
            )

    result = reconcile(packet(changes=change))
    assert result.blocking_reasons == ("account_anchor_currency_inventory_conflict",)
    assert result.findings[0].row_id == "BTC"


def test_two_empty_balance_inventories_do_not_prove_settlement_equity():
    def change(pages):
        pages["account_position_risk"][0][0]["balData"] = []
        pages["balance"][0][0]["details"] = []

    result = reconcile(packet(changes=change))
    assert result.blocking_reasons == ("account_anchor_settlement_currency_missing",)


def test_inventory_identity_with_whitespace_is_rejected_by_raw_capture():
    with pytest.raises(
        capture.AccountCaptureError, match="source_identity_field_invalid"
    ):
        changed_row("anchor_balance", "ccy", " USDT ")


def test_documented_anchor_balance_shape_does_not_require_cash_balance():
    def change(pages):
        pages["account_position_risk"][0][0]["balData"] = [
            {"ccy": "USDT", "eq": "1000", "disEq": "1000"}
        ]
        pages["balance"][0][0]["details"][0]["cashBal"] = "900"

    source = packet(changes=change)
    assert reconcile(source).findings == ()
    assert materialize(source=source, supplied=snapshot_inputs()).snapshot is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("instId", "ETH-USDT-SWAP"),
        ("instType", "FUTURES"),
        ("posSide", "long"),
        ("mgnMode", "isolated"),
        ("pos", "-2"),
        ("ccy", "USDC"),
    ],
)
def test_equal_position_id_does_not_hide_conflicting_identity_or_quantity(field, value):
    result = reconcile(changed_row("anchor_position", field, value))
    assert result.blocking_reasons == ("account_anchor_position_field_conflict",)
    assert result.findings[0].field == field
    assert result.findings[0].row_id == "900"
    assert result.findings[0].right_stream == "positions"


@pytest.mark.parametrize("field", ["instType", "posSide", "mgnMode", "pos"])
@pytest.mark.parametrize("value", [_MISSING, None, ""])
def test_missing_position_fields_are_not_assumed_from_another_source(field, value):
    result = reconcile(changed_row("anchor_position", field, value))
    assert result.blocking_reasons == ("account_anchor_position_field_missing",)


@pytest.mark.parametrize("value", [_MISSING, None, ""])
def test_optional_anchor_currency_is_not_inferred_or_required_for_every_product(value):
    result = reconcile(changed_row("anchor_position", "ccy", value))
    assert result.findings == ()
    assert result.account_complete is False


@pytest.mark.parametrize("which", ["anchor", "positions"])
def test_inventory_omission_is_not_hidden_even_for_recorded_zero_positions(which):
    def change(pages):
        if which == "anchor":
            pages["account_position_risk"][0][0]["posData"] = []
            pages["positions"][0][0]["pos"] = "0"
        else:
            pages["account_position_risk"][0][0]["posData"][0]["pos"] = "0"
            pages["positions"] = [[]]

    result = reconcile(packet(changes=change))
    assert result.blocking_reasons == ("account_anchor_position_inventory_conflict",)


def test_matching_empty_position_inventory_still_leaves_whole_account_unverified():
    def change(pages):
        pages["account_position_risk"][0][0]["posData"] = []
        pages["positions"] = [[]]

    result = reconcile(packet(changes=change))
    assert result.findings == ()
    assert result.account_complete is False


@pytest.mark.parametrize(
    "target,field",
    [("balance", "uTime"), ("currency_balance", "uTime"), ("positions", "uTime")],
)
def test_source_update_after_anchor_cannot_be_claimed_same_cutoff(target, field):
    result = reconcile(
        changed_row(target, field, ms(NOW - timedelta(milliseconds=500)))
    )
    assert result.blocking_reasons == ("account_anchor_cutoff_conflict",)
    assert result.findings[0].field == field


@pytest.mark.parametrize(
    "target,field",
    [
        ("balance", "uTime"),
        ("currency_balance", "uTime"),
        ("positions", "uTime"),
        ("positions", "cTime"),
    ],
)
@pytest.mark.parametrize("value", [_MISSING, None, ""])
def test_missing_source_clock_is_not_replaced_with_receipt_time(target, field, value):
    result = reconcile(changed_row(target, field, value))
    assert result.blocking_reasons == ("account_anchor_source_clock_missing",)


@pytest.mark.parametrize("value", [_MISSING, None, ""])
def test_anchor_without_clock_is_not_reconstructed_from_other_sources(value):
    result = reconcile(changed_row("account_position_risk", "ts", value))
    assert result.anchor_observed_at is None
    assert result.blocking_reasons == ("account_anchor_clock_missing",)


def test_anchor_before_publication_is_blocked_without_tolerance_or_clock_rewrite():
    result = reconcile(
        changed_row("account_position_risk", "ts", ms(NOW - timedelta(seconds=3)))
    )
    assert "account_anchor_predates_publication" in result.blocking_reasons
    assert result.anchor_observed_at == NOW - timedelta(seconds=3)


def test_old_unchanged_position_update_is_not_forced_to_equal_anchor_clock():
    result = reconcile(
        changed_row("positions", "uTime", ms(NOW - timedelta(minutes=1)))
    )
    assert result.findings == ()


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("anchor_balance", "eq", "999"),
        ("anchor_position", "posSide", "long"),
        ("anchor_position", "mgnMode", _MISSING),
        ("currency_balance", "uTime", ms(NOW - timedelta(milliseconds=500))),
        ("account_position_risk", "ts", _MISSING),
    ],
)
def test_materializer_integrates_crosschecks_and_refuses_even_incomplete_snapshot(
    target, field, value
):
    source = changed_row(target, field, value)
    checks = reconcile(source)
    result = materialize(source=source, supplied=snapshot_inputs())
    assert result.snapshot is None
    assert set(checks.blocking_reasons) <= set(result.incomplete_reasons)
    assert result.positions  # Recorded mappings remain inspectable, not complete.
    assert result.account_complete is result.execution_authority is False


def test_good_recorded_snapshot_still_has_all_four_incomplete_stamps():
    result = materialize(supplied=snapshot_inputs())
    assert result.snapshot is not None
    for name in (
        "balance_stamp",
        "positions_stamp",
        "history_stamp",
        "reservations_stamp",
    ):
        assert getattr(result.snapshot, name).complete is False
    assert result.account_complete is result.source_authenticity_verified is False


@pytest.mark.parametrize("pin", ["expected_plan_sha256", "expected_packet_sha256"])
@pytest.mark.parametrize("value", ["0" * 64, "not-a-digest", None, 0, True])
def test_external_pins_must_match_replayed_raw_source(pin, value):
    with pytest.raises(
        module.AccountConsistencyError, match="^recorded_account_consistency_invalid$"
    ) as error:
        reconcile(**{pin: value})
    assert error.value.__context__ is None


def test_tampered_raw_bytes_cannot_be_hidden_by_reusing_derived_packet_fields():
    current, pin = packet()
    original = current.observations[1]
    bad = original.model_copy(update={"response_body": b'{"private":"SENSITIVE"}'})
    forged = current.model_copy(
        update={
            "observations": (current.observations[0], bad, *current.observations[2:])
        }
    )
    with pytest.raises(module.AccountConsistencyError) as error:
        reconcile((forged, pin))
    assert "SENSITIVE" not in str(error.value)
    assert error.value.__context__ is None


def test_packet_subclass_serializer_is_never_invoked():
    callbacks = []

    class Forged(capture.DemoAccountPacket):
        @model_serializer
        def trap(self):
            callbacks.append("serializer")
            return {}

    current, pin = packet()
    forged = Forged.model_construct(**current.__dict__)
    with pytest.raises(module.AccountConsistencyError):
        reconcile((forged, pin))
    assert callbacks == []


@pytest.mark.parametrize(
    "field", ["account_complete", "source_authenticity_verified", "execution_authority"]
)
@pytest.mark.parametrize("value", [True, 1, 0, "true", "false"])
def test_report_constructor_never_accepts_caller_authority(field, value):
    report = reconcile()
    with pytest.raises((module.AccountConsistencyError, ValidationError)):
        module.AccountConsistencyReport(**(report.__dict__ | {field: value}))


def test_report_is_frozen_and_has_no_write_or_complete_switch():
    report = reconcile()
    with pytest.raises(ValidationError):
        report.account_id = "999"
    assert (
        "report"
        not in inspect.signature(module.reconcile_recorded_account_sources).parameters
    )
    assert set(
        inspect.signature(module.reconcile_recorded_account_sources).parameters
    ) == {"packet", "expected_plan_sha256", "expected_packet_sha256"}


@pytest.mark.parametrize("value", [(), ("source_authenticity_unverified",)])
def test_report_cannot_drop_the_fixed_unverified_boundaries(value):
    report = reconcile()
    with pytest.raises(ValidationError):
        module.AccountConsistencyReport(
            **(report.__dict__ | {"unverified_boundaries": value})
        )


def test_findings_budget_rejects_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(module, "MAX_FINDINGS", 0)
    with pytest.raises(module.AccountConsistencyError):
        reconcile(changed_row("anchor_position", "instId", INSTRUMENT + "-OTHER"))


def test_import_surface_has_no_network_credentials_runtime_database_or_execution():
    tree = ast.parse(inspect.getsource(module))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any(
        forbidden in name
        for name in imports
        for forbidden in (
            "httpx",
            "socket",
            "database",
            "settings",
            "execution",
            "collector",
            "credentials",
        )
    )
