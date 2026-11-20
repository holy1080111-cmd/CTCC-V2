"""Synthetic Live DATA: proven zero-effect close then original OCO, never fallback."""

from dataclasses import replace

import pytest

from app.domain.source_primitives import decode
from app.exchange.okx.live_trade_read_page_v1 import select_live_trade_read_v1
from app.okx_live import controlled_close_recovery_v1 as subject
from tests import native_live_postsubmit_fixtures_v1 as raw
from tests.unit.test_native_live_controlled_close_recovery_v1 import reraw, scenario


def zero_oco_scenario(
    *,
    direction="long",
    trigger="tp",
    shift=0,
    original_intent=None,
    retained_records=None,
    retained_journal=None,
    history_override=None,
):
    intent, close, args = scenario(
        direction=direction,
        cancel=False,
        shift=shift,
        original_intent=original_intent,
        retained_records=retained_records,
        retained_journal=retained_journal,
        history_override=history_override,
    )
    shift = (close.archive_cutoff.utc_ns - raw.BASE) // 1_000_000 - 160
    direction = intent.direction
    entry_id, algo_id = close.order.selector.order_id, close.protection.selector.algo_id
    close_id = close.close_order.selector.order_id
    child_id = str(int(close_id) + 1)
    zero = decode(close.close_order.raw_body)["data"][0]
    zero.update(state="canceled", accFillSz="0", avgPx="")
    entry = decode(close.order.raw_body)["data"][0]
    entry_fill = decode(close.fills[0].raw_body)["data"][0]
    price = intent.take_profit if trigger == "tp" else intent.stop_loss
    event_ms = int(zero["uTime"]) + 100
    algo = decode(close.protection.raw_body)["data"][0]
    algo.update(
        state="effective",
        actualSide=trigger,
        actualSz=str(intent.contracts),
        actualPx=str(price),
        ordId=child_id,
        ordIdList=[child_id],
        triggerTime=str(event_ms),
        uTime=str(event_ms + 15),
        failCode="",
    )
    child = {
        **zero,
        "ordId": child_id,
        "clOrdId": "",
        "ordType": "market",
        "state": "filled",
        "accFillSz": str(intent.contracts),
        "avgPx": str(price),
        "algoId": algo_id,
        "algoClOrdId": intent.protection_client_id,
        "cTime": str(event_ms + 1),
        "uTime": str(event_ms + 12),
    }
    exit_fill = decode(close.close_fills[0].raw_body)["data"][0]
    exit_fill.update(
        ordId=child_id,
        clOrdId="",
        fillPx=str(price),
        fillTime=str(event_ms + 5),
        ts=str(event_ms + 6),
    )

    def trade(kind, rows, at, **values):
        return raw.trade(
            select_live_trade_read_v1(
                kind, instrument_id=intent.instrument_id, **values
            ),
            rows,
            at + shift,
        )

    current = [
        raw.current("positions", [], 910 + shift),
        raw.current("orders_pending", [], 920 + shift),
    ]
    current.extend(
        raw.current("algo_" + kind, [], 930 + i * 10 + shift)
        for i, kind in enumerate(raw.ALGO_TYPES)
    )
    capture = subject.ControlledZeroCloseOcoCaptureV1(
        trade("order", [entry], 800, order_id=entry_id),
        (
            trade("fills_history", [entry_fill], 810, order_id=entry_id),
            trade("fills_history", [], 820, order_id=entry_id, after_bill_id="789"),
        ),
        trade("algo", [algo], 830, algo_id=algo_id),
        raw.config(900 + shift),
        raw.config(1030 + shift),
        tuple(current),
        close.current_custody_sha256,
        close.raw_custody_sha256,
        raw.stamp(1050 + shift),
        close.tick_size,
        close.contract_value,
        close.contract_multiplier,
        close.contract_value_currency,
        entry_order_after=trade("order", [entry], 870, order_id=entry_id),
        exit_order=trade("order", [child], 840, order_id=child_id),
        exit_order_after=trade("order", [child], 880, order_id=child_id),
        exit_fills=(
            trade("fills_history", [exit_fill], 850, order_id=child_id),
            trade("fills_history", [], 860, order_id=child_id, after_bill_id="790"),
        ),
        protection_after=trade("algo", [algo], 890, algo_id=algo_id),
        zero_order=trade("order", [zero], 700, order_id=close_id),
        zero_fills=(trade("fills_history", [], 710, order_id=close_id),),
        zero_order_after=trade("order", [zero], 720, order_id=close_id),
        zero_validated_at=raw.stamp(730 + shift),
        archive_pages=(reraw(close.archive_pages[0], [zero]), close.archive_pages[1]),
        archive_config_before=close.archive_config_before,
        archive_config_after=close.archive_config_after,
        archive_custody_sha256=close.archive_custody_sha256,
        archive_attempt_id=close.archive_attempt_id,
        archive_cutoff=close.archive_cutoff,
    )
    return intent, capture, args


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("trigger", ["tp", "sl"])
def test_proven_terminal_zero_effect_then_new_original_oco_can_resolve_data(
    direction, trigger
):
    intent, capture, args = zero_oco_scenario(
        direction=direction, trigger=trigger, shift=180_000
    )
    result = subject.assess_zero_effect_original_oco_v1(intent, capture, **args)
    assert result.outcome == "RECONCILED_FLAT" and result.exit_kind == "original_oco"
    assert result.exit_order_id == "125" and result.exit_lineage_verified is True
    assert result.execution_authority is result.resolution_authority is False
    assert result.raw_bank_sha256 == result.exit_source_sha256


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_order",
        "not_terminal",
        "partial_zero",
        "nonempty_fills",
        "missing_fills",
        "changed_after",
        "wrong_client",
        "archive_reuse",
        "unknown_archive",
        "wrong_original_algo",
        "partial_oco",
        "changed_original_stop",
        "oco_before_zero_verified",
        "stale_oco",
        "unexpected_cancel_claim",
    ],
)
def test_any_unknown_nonzero_changed_or_ambiguous_close_never_selects_original_oco(
    mutation,
):
    intent, capture, args = zero_oco_scenario()
    row = decode(capture.zero_order.raw_body)["data"][0]
    if mutation in {"not_terminal", "partial_zero", "wrong_client"}:
        row.update(
            {
                "not_terminal": {"state": "live"},
                "partial_zero": {"accFillSz": "0.1"},
                "wrong_client": {"clOrdId": "CTCCXother"},
            }[mutation]
        )
        capture = replace(
            capture,
            zero_order=reraw(capture.zero_order, [row]),
            zero_order_after=reraw(capture.zero_order_after, [row]),
            archive_pages=(
                reraw(capture.archive_pages[0], [row]),
                capture.archive_pages[1],
            ),
        )
    elif mutation == "missing_order":
        capture = replace(capture, zero_order=reraw(capture.zero_order, []))
    elif mutation == "missing_fills":
        capture = replace(capture, zero_fills=())
    elif mutation == "nonempty_fills":
        capture = replace(
            capture,
            zero_fills=(
                reraw(
                    capture.zero_fills[0],
                    [decode(capture.exit_fills[0].raw_body)["data"][0]],
                ),
            ),
        )
    elif mutation == "changed_after":
        row["state"] = "filled"
        capture = replace(
            capture, zero_order_after=reraw(capture.zero_order_after, [row])
        )
    elif mutation in {"archive_reuse", "unknown_archive"}:
        pages = (
            (
                reraw(capture.archive_pages[0], [{**row, "ordId": "126"}, row]),
                capture.archive_pages[1],
            )
            if mutation == "archive_reuse"
            else (reraw(capture.archive_pages[0], []),)
        )
        capture = replace(capture, archive_pages=pages)
    elif mutation in {"wrong_original_algo", "changed_original_stop"}:
        algo = decode(capture.protection.raw_body)["data"][0]
        algo.update(
            {"algoId": "999"}
            if mutation == "wrong_original_algo"
            else {"slTriggerPx": "98"}
        )
        capture = replace(
            capture,
            protection=reraw(capture.protection, [algo]),
            protection_after=reraw(capture.protection_after, [algo]),
        )
    elif mutation == "partial_oco":
        child = decode(capture.exit_order.raw_body)["data"][0]
        child["accFillSz"] = "0.5"
        capture = replace(
            capture,
            exit_order=reraw(capture.exit_order, [child]),
            exit_order_after=reraw(capture.exit_order_after, [child]),
        )
    elif mutation == "oco_before_zero_verified":
        capture = replace(capture, zero_validated_at=raw.stamp(805))
    elif mutation == "stale_oco":
        capture = replace(capture, validated_at=raw.stamp(7000))
    else:
        _, _, filled = scenario()
        args["records"] = replace(args["records"], cancel=filled["records"].cancel)
    result = subject.assess_zero_effect_original_oco_v1(intent, capture, **args)
    assert result.outcome == "UNCERTAIN" and result.exit_lineage_verified is False


def test_combined_terminal_projection_binds_zero_effect_and_original_oco_without_new_permission(
    monkeypatch,
):
    from app.okx_live import lifecycle_key_v1 as keys
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    intent, capture, args = zero_oco_scenario()
    assessed = subject.assess_zero_effect_original_oco_v1(intent, capture, **args)
    monkeypatch.setattr(
        keys,
        "lifecycle_key_metadata_v1",
        lambda key: {
            "environment": "live",
            "account_uid": intent.account_uid,
            "main_uid": intent.main_uid,
            "origin": raw.ORIGIN,
        },
    )
    state = {
        "intent": intent,
        "journal": args["journal"],
        "key_owner": object(),
        "report_id": "retained_report",
        "capture_barrier": args["barrier"],
        "submission_started": None,
        "submission_attempted": True,
        "submission_sha256": "f" * 64,
        "raw_captures": [],
        "recovery_history": args["history"],
        "controlled_close_recovery": args["records"],
    }
    result = decode(
        lifecycle._proof_document(
            state, capture=capture, facts=assessed, economics=None
        )
    )
    assert (
        result["outcome"] == "RECONCILED_FLAT" and result["exit_kind"] == "original_oco"
    )
    zero = result["nonexecuted_controlled_close"]
    assert zero["close_order_id"] == "124" and result["exit_order_id"] == "125"
    assert zero["close_claim_sha256"] == args["records"].close.document_sha256
    assert result["submission_started"] is result["fixed_fill_economics"] is None
    assert zero["execution_authority"] is result["execution_authority"] is False
