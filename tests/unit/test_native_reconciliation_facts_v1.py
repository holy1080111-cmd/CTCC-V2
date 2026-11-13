"""Synthetic wire/math only: no genuine source, DB closure or order permit."""

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest

from app.domain.source_primitives import canonical, decode
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from app.trade_qualification import reservations as ledger
from tests.unit.test_native_ledger_contracts_v2 import native_binding
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- pytest registers this exact fixture reexport.
)


@pytest.fixture(scope="module")
def target(synthetic):
    source, request = synthetic
    # Predeclare the synthetic DB chronology: R7 proof, then reserve, then
    # consume. Captured proof/publication timestamps remain original facts.
    at = source.latest_source.evaluated_at + timedelta(microseconds=2)
    reserved = ledger.ReservationReceipt(
        scope=request.scope,
        reservation_id=ledger.reservation_id(
            request.scope, request.origin.original_event_key
        ),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.risk_inputs.instrument.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=ledger.digest(request),
        coverage=request.coverage,
        state="reserved",
        state_revision=1,
        account_revision=3,
        ledger_revision=6,
        created_at=at - timedelta(microseconds=1),
        updated_at=at - timedelta(microseconds=1),
        deadline=request.origin.deadline,
    )
    raw = reserved.model_dump(mode="python", round_trip=True)
    raw.update(state="consumed", state_revision=2, ledger_revision=7, updated_at=at)
    consumed = ledger.ReservationReceipt.model_validate(raw, strict=True)
    state = control.ControlState(
        control.ControlScope("demo", request.scope.account_id),
        2,
        "a" * 64,
        1,
        request.origin.deadline + timedelta(seconds=1),
        control.ControlPins(
            "b" * 64,
            request.origin.original_policy_sha256,
            request.credential_session_sha256,
        ),
        False,
        "c" * 64,
        request.origin.deadline,
        at - timedelta(seconds=10),
        at - timedelta(seconds=5),
    )
    reserved_raw = bound.build_reserved_evidence(
        request, reserved, state, "d" * 64, checked_at=reserved.created_at
    )
    inner = wire.build_native_intent(
        request, consumed, execution_binding=native_binding(request)
    )
    outer = bound.build_control_bound_intent(
        request, inner, reserved_raw, state, "d" * 64
    )
    return facts.NativeReconciliationTargetV1(
        request,
        wire.canonical_request(request),
        outer.canonical_json,
        outer.sha256,
        consumed.reservation_id,
        "99887766",
        "https://www.okx.com",
        request.credential_session_sha256,
        "e" * 64,
    )


def data(target, *, filled=False, exact_outgoing_query=False):
    request = target.request
    inner = json.loads(json.loads(target.intent_json)["intent_json"])
    expected = inner["exact_order"]
    size, price = expected["sz"], expected["px"]
    at = request.origin.deadline + timedelta(seconds=1)
    delta = (at - timedelta(seconds=2)) - datetime(1970, 1, 1, tzinfo=UTC)
    created_ms = (
        delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    )
    created, traded = str(created_ms), str(created_ms + 1)
    order = {
        **{
            key: expected[key]
            for key in (
                "instId",
                "tdMode",
                "side",
                "posSide",
                "ordType",
                "clOrdId",
                "sz",
                "px",
            )
        },
        "instType": "SWAP",
        "ordId": "10001",
        "reduceOnly": "false",
        "state": "filled" if filled else "canceled",
        "accFillSz": size if filled else "0",
        "avgPx": price if filled else "",
        "lever": str(request.risk_inputs.requested_leverage),
        "cTime": created,
        "uTime": traded,
    }
    config = {
        "uid": request.scope.account_id,
        "mainUid": target.main_uid,
        "acctLv": "2",
        "posMode": inner["execution_binding"]["position_mode"],
    }
    query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    sequence = [
        ("config_before", "/api/v5/account/config", {}, [config]),
        ("order_before", "/api/v5/trade/order", query, [order]),
    ]
    for role, path in (
        ("fills_recent", "/api/v5/trade/fills"),
        ("fills_history", "/api/v5/trade/fills-history"),
    ):
        fill_query = {
            "instType": "SWAP",
            "instId": expected["instId"],
            "ordId": "10001",
            "limit": "100",
        }
        if filled:
            fill = {
                "billId": "9001",
                "tradeId": "7001",
                "ordId": "10001",
                "instId": expected["instId"],
                "instType": "SWAP",
                "side": expected["side"],
                "posSide": expected["posSide"],
                "fillSz": size,
                "fillPx": price,
                "fillTime": traded,
                "ts": traded,
                "feeCcy": "USDT",
                "fee": "0",
            }
            sequence.append((role, path, fill_query, [fill]))
            fill_query = {**fill_query, "after": "9001"}
        sequence.append((role, path, fill_query, []))
    position = {
        "posId": "5001",
        "instType": "SWAP",
        "instId": expected["instId"],
        "mgnMode": "isolated",
        "posSide": expected["posSide"],
        "pos": str(-Decimal(size))
        if expected["side"] == "sell" and expected["posSide"] == "net"
        else size,
        "avgPx": price,
        "lever": str(request.risk_inputs.requested_leverage),
        "ccy": "USDT",
        "cTime": created,
        "uTime": traded,
    }
    position["margin"] = str(request.coverage.margin_amount)
    sequence.extend(
        (
            (
                "balance",
                "/api/v5/account/balance",
                {},
                [
                    {
                        "uTime": traded,
                        "details": [
                            {
                                "ccy": "USDT",
                                "eq": "10000",
                                "availEq": "9000",
                                "liab": "0",
                                "borrowFroz": "0",
                                "uTime": traded,
                            }
                        ],
                    }
                ],
            ),
            (
                "positions",
                "/api/v5/account/positions",
                {},
                [position] if filled else [],
            ),
            (
                "account_risk",
                "/api/v5/account/account-position-risk",
                {},
                [
                    {
                        "ts": traded,
                        "balData": [{"ccy": "USDT", "eq": "10000"}],
                        "posData": [position] if filled else [],
                    }
                ],
            ),
            ("pending_orders", "/api/v5/trade/orders-pending", {"limit": "100"}, []),
        )
    )
    for kind in read.ALGO_TYPES:
        params = {"ordType": kind, "limit": "100"}
        if filled and kind == "oco":
            protection = expected["attachAlgoOrds"][0]
            algo = {
                "algoId": "8001",
                "algoClOrdId": protection["attachAlgoClOrdId"],
                "instType": "SWAP",
                "instId": expected["instId"],
                "tdMode": "isolated",
                "posSide": expected["posSide"],
                "side": "sell" if expected["side"] == "buy" else "buy",
                "ordType": "oco",
                "state": "live",
                "sz": size,
                "reduceOnly": "true",
                "cTime": traded,
                **{k: v for k, v in protection.items() if k != "attachAlgoClOrdId"},
            }
            sequence.append(
                ("algo_" + kind, "/api/v5/trade/orders-algo-pending", params, [algo])
            )
            params = {**params, "after": "8001"}
        sequence.append(
            ("algo_" + kind, "/api/v5/trade/orders-algo-pending", params, [])
        )
    sequence.extend(
        (
            (
                "leverage",
                "/api/v5/account/leverage-info",
                {"instId": expected["instId"], "mgnMode": "isolated"},
                [
                    {
                        "instId": expected["instId"],
                        "mgnMode": "isolated",
                        "posSide": expected["posSide"],
                        "lever": str(request.risk_inputs.requested_leverage),
                    }
                ],
            ),
            ("order_after", "/api/v5/trade/order", query, [order]),
            ("config_after", "/api/v5/account/config", {}, [config]),
        )
    )
    result = []
    for index, (role, path, params, values) in enumerate(sequence):
        started = at + timedelta(seconds=index)
        ordered = tuple(sorted(params.items()))
        outgoing = ordered if exact_outgoing_query else tuple(params.items())
        result.append(
            (
                role,
                read.DemoReconciliationObservationV1(
                    path,
                    ordered,
                    target.origin
                    + path
                    + ("?" + urlencode(outgoing) if outgoing else ""),
                    "f" * 64,
                    "a" * 64,
                    started,
                    index * 1000000000,
                    started + timedelta(milliseconds=1),
                    index * 1000000000 + 1000000,
                    started + timedelta(milliseconds=2),
                    index * 1000000000 + 2000000,
                    200,
                    (),
                    canonical({"code": "0", "data": copy.deepcopy(values)}),
                    "b" * 64,
                    "www.okx.com",
                ),
            )
        )
    return tuple(result)


def milliseconds(at):
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def changed(observations, role, edit, *, first_only=False):
    result, done = [], False
    for name, page in observations:
        if name == role and (not first_only or not done):
            payload = decode(page.raw_body, read.MAX_BODY)
            edit(payload["data"])
            page = replace(page, raw_body=canonical(payload))
            done = True
        result.append((name, page))
    return tuple(result)


def test_terminal_zero_fok_computation_and_filled_protection_keep_distinct_states(
    target,
):
    zero = facts.assess_native_reconciliation_v1(target, data(target))
    assert zero.terminal_zero_fill_flat and not zero.filled_and_protected
    filled = facts.assess_native_reconciliation_v1(target, data(target, filled=True))
    assert filled.filled_and_protected and not filled.terminal_zero_fill_flat
    assert filled.classification == "filled_and_protected_hold_retained"
    assert not filled.execution_authority and not filled.order_retry_authority


@pytest.mark.parametrize(
    "mutation",
    (
        "partial",
        "order_identity",
        "order_after",
        "uid",
        "main_uid",
        "mode",
        "position",
        "liability",
        "missing_terminal",
        "cursor_filter",
        "late_page_before_terminal_order",
    ),
)
def test_zero_fok_mutations_do_not_compute_a_flat_closure(target, mutation):
    observations = data(target)
    if mutation == "partial":
        observations = changed(
            observations,
            "order_before",
            lambda rows: rows[0].update(accFillSz="0.0001"),
        )
    elif mutation == "order_identity":
        observations = changed(
            observations, "order_before", lambda rows: rows[0].update(clOrdId="foreign")
        )
    elif mutation == "order_after":
        observations = changed(
            observations,
            "order_after",
            lambda rows: rows[0].update(state="filled", accFillSz="1"),
        )
    elif mutation in {"uid", "main_uid", "mode"}:
        field = {"uid": "uid", "main_uid": "mainUid", "mode": "posMode"}[mutation]
        current_mode = json.loads(json.loads(target.intent_json)["intent_json"])[
            "execution_binding"
        ]["position_mode"]
        value = (
            ("net_mode" if current_mode == "long_short_mode" else "long_short_mode")
            if mutation == "mode"
            else "42"
        )
        observations = changed(
            observations, "config_after", lambda rows: rows[0].update({field: value})
        )
    elif mutation == "position":
        observations = changed(
            observations, "positions", lambda rows: rows.append({"pos": "1"})
        )
    elif mutation == "liability":
        observations = changed(
            observations,
            "balance",
            lambda rows: rows[0]["details"][0].update(liab="0.1"),
        )
    elif mutation == "missing_terminal":
        observations = tuple(item for item in observations if item[0] != "algo_chase")
    elif mutation == "cursor_filter":
        observations = tuple(
            (
                name,
                replace(
                    page, parameters=(("instId", "BTC-USDT-SWAP"), ("limit", "100"))
                ),
            )
            if name == "pending_orders"
            else (name, page)
            for name, page in observations
        )
    elif mutation == "late_page_before_terminal_order":
        observations = (
            observations[0],
            observations[2],
            observations[1],
            *observations[3:],
        )
    with pytest.raises((ValueError, KeyError)):
        facts.assess_native_reconciliation_v1(target, observations)


@pytest.mark.parametrize(
    "mutation",
    (
        "average",
        "fee",
        "protection_id",
        "protection_size",
        "stop",
        "take_profit",
        "position_mode",
        "leverage",
        "duplicate_protection",
        "fill_quantity",
        "fill_identity",
        "fill_limit",
        "fill_terminal",
    ),
)
def test_filled_mutations_cannot_silently_repair_original_geometry_or_release(
    target, mutation
):
    observations = data(target, filled=True)
    if mutation == "average":
        observations = changed(
            observations, "order_before", lambda rows: rows[0].update(avgPx="1")
        )
        observations = changed(
            observations, "order_after", lambda rows: rows[0].update(avgPx="1")
        )
    elif mutation == "fee":
        observations = changed(
            observations,
            "fills_recent",
            lambda rows: rows and rows[0].update(fee="-999999"),
            first_only=True,
        )
        observations = changed(
            observations,
            "fills_history",
            lambda rows: rows and rows[0].update(fee="-999999"),
            first_only=True,
        )
    elif mutation in {
        "protection_id",
        "protection_size",
        "stop",
        "take_profit",
        "duplicate_protection",
    }:

        def edit(rows):
            if not rows:
                return
            if mutation == "duplicate_protection":
                rows.append({**rows[0], "algoId": "8000"})
            else:
                rows[0][
                    {
                        "protection_id": "algoClOrdId",
                        "protection_size": "sz",
                        "stop": "slTriggerPx",
                        "take_profit": "tpTriggerPx",
                    }[mutation]
                ] = "1"

        observations = changed(observations, "algo_oco", edit, first_only=True)
    elif mutation == "position_mode":
        observations = changed(
            observations, "positions", lambda rows: rows[0].update(posSide="foreign")
        )
    elif mutation == "leverage":
        observations = changed(
            observations, "leverage", lambda rows: rows[0].update(lever="125")
        )
    elif mutation in {"fill_quantity", "fill_identity", "fill_limit"}:
        field = {
            "fill_quantity": "fillSz",
            "fill_identity": "ordId",
            "fill_limit": "fillPx",
        }[mutation]
        value = "999999999999" if mutation != "fill_identity" else "42"
        observations = changed(
            observations,
            "fills_recent",
            lambda rows: rows and rows[0].update({field: value}),
            first_only=True,
        )
        observations = changed(
            observations,
            "fills_history",
            lambda rows: rows and rows[0].update({field: value}),
            first_only=True,
        )
    elif mutation == "fill_terminal":
        observations = tuple(
            item
            for item in observations
            if not (item[0] == "fills_history" and not facts.rows(item[1]))
        )
    with pytest.raises((ValueError, KeyError)):
        facts.assess_native_reconciliation_v1(target, observations)


@pytest.mark.parametrize(
    "role,field",
    (
        ("order_before", "cTime"),
        ("order_before", "uTime"),
        ("order_after", "uTime"),
        ("fills_recent", "fillTime"),
        ("fills_history", "ts"),
        ("positions", "uTime"),
        ("account_risk", "ts"),
        ("balance", "uTime"),
        ("algo_oco", "cTime"),
    ),
)
def test_each_future_source_event_or_generation_denies_against_its_own_eof(
    target, role, field
):
    observations = data(target, filled=True)
    page = next(
        page for name, page in observations if name == role and facts.rows(page)
    )
    future = str(milliseconds(page.eof_at) + 1)
    observations = changed(
        observations,
        role,
        lambda rows: rows and rows[0].update({field: future}),
        first_only=True,
    )
    with pytest.raises(
        facts.NativeReconciliationFactsError,
        match="native_reconcile_future_source_clock",
    ):
        facts.assess_native_reconciliation_v1(target, observations)


@pytest.mark.parametrize(
    "role,field",
    (
        ("order_before", "cTime"),
        ("order_after", "uTime"),
        ("fills_recent", "fillTime"),
        ("fills_history", "ts"),
        ("positions", "uTime"),
        ("balance", "uTime"),
        ("account_risk", "ts"),
        ("algo_oco", "cTime"),
    ),
)
def test_missing_documented_source_clocks_never_become_zero_or_receipt_time(
    target, role, field
):
    observations = changed(
        data(target, filled=True),
        role,
        lambda rows: rows and rows[0].pop(field),
        first_only=True,
    )
    with pytest.raises(
        facts.NativeReconciliationFactsError,
        match="native_reconcile_source_clock_missing_or_invalid",
    ):
        facts.assess_native_reconciliation_v1(target, observations)


def test_future_zero_fok_clocks_cannot_compute_flat_even_when_both_orders_agree(target):
    observations = data(target)
    future = str(milliseconds(observations[-1][1].eof_at) + 86400000)
    for role in ("order_before", "order_after"):
        observations = changed(
            observations, role, lambda rows: rows[0].update(cTime=future, uTime=future)
        )
    with pytest.raises(
        facts.NativeReconciliationFactsError,
        match="native_reconcile_future_source_clock",
    ):
        facts.assess_native_reconciliation_v1(target, observations)


def test_fill_record_generation_cannot_predate_actual_trade(target):
    observations = data(target, filled=True)
    for role in ("fills_recent", "fills_history"):
        observations = changed(
            observations,
            role,
            lambda rows: rows and rows[0].update(ts=str(int(rows[0]["fillTime"]) - 1)),
            first_only=True,
        )
    with pytest.raises(
        facts.NativeReconciliationFactsError,
        match="native_reconcile_fill_generation_precedes_trade",
    ):
        facts.assess_native_reconciliation_v1(target, observations)


def test_unchanged_old_balance_update_is_not_a_fake_fresh_generation_requirement(
    target,
):
    observations = data(target)
    old = str(milliseconds(observations[0][1].started_at) - 86400000)
    observations = changed(
        observations,
        "balance",
        lambda rows: (
            rows[0].update(uTime=old),
            rows[0]["details"][0].update(uTime=old),
        ),
    )
    observations = changed(
        observations, "account_risk", lambda rows: rows[0].update(ts=old)
    )
    assert facts.assess_native_reconciliation_v1(
        target, observations
    ).terminal_zero_fill_flat


def test_risk_snapshot_does_not_invent_nested_u_time_fields(target):
    observations = data(target, filled=True)

    def remove_undocumented_nested_clocks(rows):
        for position in rows[0]["posData"]:
            position.pop("cTime", None)
            position.pop("uTime", None)

    observations = changed(
        observations, "account_risk", remove_undocumented_nested_clocks
    )
    assert facts.assess_native_reconciliation_v1(
        target, observations
    ).filled_and_protected
