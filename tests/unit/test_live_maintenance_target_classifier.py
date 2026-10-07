"""Classification from retained Live read DTOs never grants a POST permit."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.okx_live import OkxLiveReconcileResult
from app.exchange.okx.live_private_parsers import (
    parse_live_account_config,
    parse_live_algo_order,
    parse_live_balance,
    parse_live_order,
    parse_live_position,
)
from app.okx_live.maintenance_target_classifier import (
    classify_live_cancel_target,
    classify_live_close_target,
)

INSTRUMENT = "BTC-USDT-SWAP"
UID = "exact-sub-42"
MAIN_UID = "exact-main-1"


def order_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ordId": "ordinary-1",
        "clOrdId": "client-1",
        "instId": INSTRUMENT,
        "side": "buy",
        "posSide": "net",
        "ordType": "limit",
        "state": "live",
        "sz": "2",
        "accFillSz": "0",
        "reduceOnly": "false",
        "attachAlgoOrds": [],
    }
    row.update(changes)
    return row


def position_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "posId": "position-1",
        "instId": INSTRUMENT,
        "posSide": "net",
        "pos": "2",
        "availPos": "2",
        "mgnMode": "cross",
        "upl": "0",
    }
    row.update(changes)
    return row


def snapshot(
    *,
    orders: list[dict[str, object]] | None = None,
    positions: list[dict[str, object]] | None = None,
    recent: list[dict[str, object]] | None = None,
    algo: list[dict[str, object]] | None = None,
    uid: str = UID,
    main_uid: str = MAIN_UID,
    position_mode: str = "net_mode",
) -> OkxLiveReconcileResult:
    return OkxLiveReconcileResult(
        account_config=parse_live_account_config(
            {
                "uid": uid,
                "mainUid": main_uid,
                "posMode": position_mode,
                "perm": "read_only,trade",
                "ip": "203.0.113.8",
            }
        ),
        balance=parse_live_balance(
            {
                "totalEq": "100",
                "isoEq": "0",
                "adjEq": "100",
                "availEq": "100",
                "uTime": "1724742632153",
                "details": [
                    {
                        "ccy": "USDT",
                        "eq": "100",
                        "cashBal": "100",
                        "availBal": "100",
                        "frozenBal": "0",
                        "upl": "0",
                    }
                ],
            }
        ),
        positions=[parse_live_position(row) for row in positions or []],
        pending_orders=[parse_live_order(row) for row in orders or []],
        recent_orders=[parse_live_order(row) for row in recent or []],
        pending_algo_orders=[parse_live_algo_order(row) for row in algo or []],
        persisted=True,
    )


def cancel(value: OkxLiveReconcileResult, **changes: object):
    parameters = {
        "pinned_uid": UID,
        "pinned_main_uid": MAIN_UID,
        "instrument_id": INSTRUMENT,
        "order_id": "ordinary-1",
    }
    parameters.update(changes)
    return classify_live_cancel_target(value, **parameters)


def close(value: OkxLiveReconcileResult, **changes: object):
    parameters = {
        "pinned_uid": UID,
        "pinned_main_uid": MAIN_UID,
        "instrument_id": INSTRUMENT,
        "position_id": "position-1",
        "direction": "long",
        "margin_mode": "cross",
    }
    parameters.update(changes)
    return classify_live_close_target(value, **parameters)


def test_exact_unfilled_ordinary_order_is_review_only_without_post_authority():
    assessment = cancel(snapshot(orders=[order_row()]))
    assert assessment.kind == "ordinary_unfilled"
    assert assessment.disposition == "REVIEW_ONLY"
    assert assessment.account_uid == UID
    assert assessment.main_uid == MAIN_UID
    assert assessment.target_id == "ordinary-1"
    assert assessment.size == Decimal(2)
    assert assessment.authorizes_post is False


def test_exact_client_order_id_can_identify_one_pending_order_for_review():
    assessment = cancel(
        snapshot(orders=[order_row()]), order_id=None, client_order_id="client-1"
    )
    assert assessment.kind == "ordinary_unfilled"
    assert assessment.target_id == "ordinary-1"
    assert assessment.authorizes_post is False


@pytest.mark.parametrize(
    "identity",
    (
        {"pinned_uid": MAIN_UID},
        {"pinned_main_uid": UID},
        {"pinned_uid": ""},
    ),
)
def test_cancel_requires_exact_uid_and_main_uid(identity):
    assessment = cancel(snapshot(orders=[order_row()]), **identity)
    assert (assessment.kind, assessment.disposition) == ("unknown", "DENY")
    assert assessment.reason == "account_identity_not_exact"


@pytest.mark.parametrize("missing_key", ("accFillSz", "sz", "reduceOnly"))
def test_parser_rejects_missing_order_numeric_or_boolean_source(missing_key: str):
    row = order_row()
    del row[missing_key]
    with pytest.raises(ValueError, match="okx_live_.*_field_missing_or_invalid"):
        parse_live_order(row)


def test_missing_attachment_evidence_is_rejected_before_classification():
    row = order_row()
    del row["attachAlgoOrds"]
    with pytest.raises(ValueError, match="attached_algo_evidence_missing_or_invalid"):
        snapshot(orders=[row])


def test_partial_fill_is_denied_even_when_pending_state_still_live():
    assessment = cancel(snapshot(orders=[order_row(accFillSz="0.1")]))
    assert (assessment.kind, assessment.disposition) == ("partially_filled", "DENY")
    assert assessment.authorizes_post is False


@pytest.mark.parametrize(
    "row",
    (
        order_row(reduceOnly="true"),
        order_row(attachAlgoOrds=[{"attachAlgoId": "attached-1"}]),
        order_row(side="sell", posSide="long"),
    ),
)
def test_protective_or_reducing_order_is_denied(row):
    mode = "long_short_mode" if row["posSide"] == "long" else "net_mode"
    assessment = cancel(snapshot(orders=[row], position_mode=mode))
    assert (assessment.kind, assessment.disposition) == ("protective", "DENY")


def test_active_position_or_algo_on_instrument_denies_routine_cancel():
    with_position = cancel(snapshot(orders=[order_row()], positions=[position_row()]))
    with_algo = cancel(
        snapshot(
            orders=[order_row()],
            algo=[
                {
                    "algoId": "algo-1",
                    "instId": INSTRUMENT,
                    "ordType": "oco",
                    "state": "live",
                    "sz": "2",
                    "actualSz": "0",
                }
            ],
        )
    )
    assert with_position.kind == with_algo.kind == "protective"
    assert with_position.disposition == with_algo.disposition == "DENY"


def test_duplicate_or_history_conflicted_order_is_unknown():
    duplicate = cancel(snapshot(orders=[order_row(), order_row()]))
    history = cancel(snapshot(orders=[order_row()], recent=[order_row(state="filled")]))
    assert duplicate.reason == "target_not_unique_pending"
    assert history.reason == "pending_history_identity_conflict"
    assert duplicate.disposition == history.disposition == "DENY"


def test_missing_pending_target_is_unknown_not_empty_safe_order():
    assessment = cancel(snapshot(), client_order_id="client-1", order_id=None)
    assert (assessment.kind, assessment.disposition) == ("unknown", "DENY")


def test_exact_position_records_side_margin_and_size_but_not_permission():
    assessment = close(snapshot(positions=[position_row()]))
    assert assessment.kind == "exact_position"
    assert assessment.disposition == "REVIEW_ONLY"
    assert assessment.position_side == "net"
    assert assessment.margin_mode == "cross"
    assert assessment.size == Decimal(2)
    assert assessment.target_id == "position-1"
    assert assessment.authorizes_post is False


def test_signed_net_short_position_requires_matching_direction_and_margin():
    value = snapshot(positions=[position_row(pos="-2", mgnMode="isolated")])
    exact = close(value, direction="short", margin_mode="isolated")
    wrong_direction = close(value, direction="long", margin_mode="isolated")
    wrong_margin = close(value, direction="short", margin_mode="cross")
    assert (exact.kind, exact.disposition) == ("exact_position", "REVIEW_ONLY")
    assert wrong_direction.disposition == wrong_margin.disposition == "DENY"


def test_hedged_position_requires_exact_side_and_positive_contract_size():
    value = snapshot(
        positions=[position_row(posSide="short", pos="2")],
        position_mode="long_short_mode",
    )
    exact = close(value, direction="short")
    wrong_side = close(value, direction="long")
    assert exact.kind == "exact_position"
    assert exact.position_side == "short"
    assert wrong_side.disposition == "DENY"


@pytest.mark.parametrize("missing_key", ("pos", "availPos"))
def test_parser_rejects_missing_position_numeric_source(missing_key: str):
    row = position_row()
    del row[missing_key]
    with pytest.raises(ValueError, match="okx_live_numeric_field_missing_or_invalid"):
        parse_live_position(row)


def test_missing_margin_mode_remains_unknown_after_strict_parse():
    row = position_row()
    del row["mgnMode"]
    assessment = close(snapshot(positions=[row]))
    assert assessment.kind == "unknown"
    assert assessment.disposition == "DENY"


def test_frozen_or_multiple_positions_are_unknown():
    frozen = close(snapshot(positions=[position_row(availPos="1")]))
    multiple = close(
        snapshot(
            positions=[
                position_row(),
                position_row(posId="position-2", posSide="net", pos="1", availPos="1"),
            ]
        )
    )
    assert frozen.reason == "position_available_size_incomplete"
    assert multiple.reason == "multiple_instrument_positions"
    assert frozen.disposition == multiple.disposition == "DENY"


def test_position_with_pending_order_or_algo_is_protective_and_denied():
    ordinary = close(snapshot(positions=[position_row()], orders=[order_row()]))
    algo = close(
        snapshot(
            positions=[position_row()],
            algo=[
                {
                    "algoId": "algo-1",
                    "instId": INSTRUMENT,
                    "ordType": "oco",
                    "state": "live",
                    "sz": "2",
                    "actualSz": "0",
                }
            ],
        )
    )
    assert ordinary.kind == algo.kind == "protective"
    assert ordinary.disposition == algo.disposition == "DENY"


def test_close_requires_exact_uid_and_exact_position_id():
    value = snapshot(positions=[position_row()])
    wrong_identity = close(value, pinned_uid="different")
    wrong_position = close(value, position_id="position-2")
    assert wrong_identity.reason == "account_identity_not_exact"
    assert wrong_position.reason == "position_not_unique_or_instrument_mismatch"
    assert wrong_identity.authorizes_post is wrong_position.authorizes_post is False
