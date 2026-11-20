"""Fresh exact Live controlled-exit raw replay; never infer flatness from ACK.

Only the original same-task close actor can collect. Every account partition,
order, fill chain and original protection is newly observed after the action.
The pure assessment remains DATA and cannot sign or resolve a reservation.
"""

import base64
import secrets
from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from app.domain.source_primitives import canonical, decode, sha, validate_stamps
from app.exchange.okx.live_account_config_observation_v1 import _stamp
from app.exchange.okx.live_account_raw_page_v1 import ALGO_TYPES
from app.exchange.okx.live_trade_read_page_v1 import (
    observe_live_trade_read_page_v1,
    select_live_trade_read_v1,
    verify_live_trade_read_page_v1,
)
from app.okx_live import reconciliation_facts_v1 as facts


def _need(value):
    if value is not True:
        raise ValueError("native_live_controlled_exit_facts_incomplete") from None


@dataclass(frozen=True, slots=True, repr=False)
class ControlledLiveResultV1:
    source_owner: object
    entry_capture: object
    close_order_before: object
    close_order_after: object
    close_fills: tuple
    close_response_json: bytes
    barrier: object
    observed: object
    raw_custody_sha256: str
    raw_bank_sha256: str
    close_order_id: str
    close_average: Decimal
    close_fees: Decimal
    close_filled: bool
    flat_verified: bool
    original_algo_pending: bool
    cancel_facts: dict | None
    execution_authority: bool = field(default=False, init=False)


def _assess(
    state,
    capture,
    before,
    after,
    fills,
    response,
    *,
    barrier,
    observed,
    allow_original_algo,
):
    intent = state["intent"]
    _need(type(allow_original_algo) is bool)
    context = {
        "barrier": barrier,
        "validated_at": observed,
        "origin": capture.order.origin,
        "binding": capture.order.lease_binding_id,
    }
    entry_rows = facts._payload(capture.order, **context)
    _need(len(entry_rows) == 1)
    entry = entry_rows[0]
    old = state["protected_facts"]
    _need(
        entry.get("ordId") == old.order_id
        and entry.get("clOrdId") == intent.client_order_id
    )
    _need(
        entry.get("instId") == intent.instrument_id and entry.get("state") == "filled"
    )
    _need(
        entry.get("ordType") == "fok"
        and entry.get("tdMode") == "isolated"
        and entry.get("posSide") == "net"
    )
    _need(entry.get("side") == ("buy" if intent.direction == "long" else "sell"))
    _need(
        facts._decimal(entry, "sz", positive=True)
        == facts._decimal(entry, "accFillSz", positive=True)
        == intent.contracts
    )
    _need(facts._decimal(entry, "avgPx", positive=True) == old.average_price)
    _need(facts._decimal(entry, "lever", positive=True) == intent.leverage)
    entry_fills = facts._chain(
        capture.fills,
        kind="fills",
        params=(intent.instrument_id, old.order_id),
        getter=lambda value: (
            value.kind,
            value.after_bill_id,
            value.instrument_id,
            value.order_id,
        ),
        **context,
    )
    _need(
        sum(
            (facts._decimal(row, "fillSz", positive=True) for row in entry_fills),
            Decimal(0),
        )
        == intent.contracts
    )
    for row in entry_fills:
        _need(
            row.get("ordId") == old.order_id
            and row.get("clOrdId") == intent.client_order_id
        )
        _need(
            row.get("instId") == intent.instrument_id
            and row.get("posSide") == "net"
            and row.get("side") == entry["side"]
        )
    entry_weighted = (
        sum(
            (
                facts._decimal(row, "fillSz", positive=True)
                * facts._decimal(row, "fillPx", positive=True)
                for row in entry_fills
            ),
            Decimal(0),
        )
        / intent.contracts
    )
    _need(abs(entry_weighted - old.average_price) <= capture.tick_size / 2)
    entry_trade_ids = [row.get("tradeId") for row in entry_fills]
    _need(
        all(
            type(value) is str
            and value.isascii()
            and value.isdecimal()
            and int(value) > 0
            for value in entry_trade_ids
        )
    )
    _need(len(set(entry_trade_ids)) == len(entry_trade_ids))
    first, last = facts._payload(before, **context), facts._payload(after, **context)
    _need(len(first) == len(last) == 1 and first[0] == last[0])
    close = first[0]
    _need(
        close.get("instId") == intent.instrument_id
        and close.get("clOrdId") == state["close_client_order_id"]
    )
    close_id = close.get("ordId")
    _need(
        type(close_id) is str
        and close_id.isascii()
        and close_id.isdecimal()
        and int(close_id) > 0
        and close_id != old.order_id
    )
    ack_raw = response.get("raw_body")
    if ack_raw is None:
        _need(type(response.get("raw_body_base64")) is str)
        ack_raw = base64.b64decode(response["raw_body_base64"], validate=True)
    ack = decode(ack_raw, maximum=65536)
    _need(
        ack.get("code") == "0"
        and type(ack.get("data")) is list
        and len(ack["data"]) == 1
    )
    _need(
        ack["data"][0].get("sCode") == "0"
        and ack["data"][0].get("ordId") == close_id
        and ack["data"][0].get("clOrdId") == state["close_client_order_id"]
    )
    _need(
        close.get("state") == "filled"
        and close.get("ordType") == "fok"
        and close.get("reduceOnly") == "true"
    )
    _need(close.get("tdMode") == "isolated" and close.get("posSide") == "net")
    _need(close.get("side") == ("sell" if intent.direction == "long" else "buy"))
    _need(
        facts._decimal(close, "sz", positive=True)
        == facts._decimal(close, "accFillSz", positive=True)
        == intent.contracts
    )
    _need(facts._decimal(close, "lever", positive=True) == intent.leverage)
    _need(facts._decimal(close, "px", positive=True) == intent.stop_loss)
    _need(response["request_attempted"] is True and response["started_at"] is not None)
    start_ms = response["started_at"]["utc_ns"] // 1_000_000
    from app.okx_live.owned_close_v1 import _utc_ns

    _need(
        start_ms <= int(close["cTime"]) <= _utc_ns(state["close_deadline"]) // 1_000_000
    )
    fill_rows = facts._chain(
        fills,
        kind="fills",
        params=(intent.instrument_id, close_id),
        getter=lambda value: (
            value.kind,
            value.after_bill_id,
            value.instrument_id,
            value.order_id,
        ),
        **context,
    )
    seen, quantity, fee, weighted = set(), Decimal(0), Decimal(0), Decimal(0)
    entry_bill_ids = {row["billId"] for row in entry_fills}
    for row in fill_rows:
        _need(
            row.get("ordId") == close_id
            and row.get("clOrdId") == state["close_client_order_id"]
        )
        _need(
            row.get("instId") == intent.instrument_id
            and row.get("side") == close["side"]
            and row.get("posSide") == "net"
        )
        trade = row.get("tradeId")
        _need(
            type(trade) is str
            and trade.isascii()
            and trade.isdecimal()
            and int(trade) > 0
            and trade not in seen
            and trade not in entry_trade_ids
        )
        _need(row["billId"] not in entry_bill_ids)
        seen.add(trade)
        _need(row.get("feeCcy") == "USDT")
        _need(
            start_ms
            <= int(row["fillTime"])
            <= int(row["ts"])
            <= observed.utc_ns // 1_000_000
        )
        size, price = (
            facts._decimal(row, "fillSz", positive=True),
            facts._decimal(row, "fillPx", positive=True),
        )
        quantity += size
        weighted += size * price
        fee += facts._decimal(row, "fee")
    _need(quantity == intent.contracts)
    average = facts._decimal(close, "avgPx", positive=True)
    _need(abs(average - weighted / quantity) <= capture.tick_size / 2)
    _need(
        average >= intent.stop_loss
        if intent.direction == "long"
        else average <= intent.stop_loss
    )
    configs = [
        facts._payload(page, **context)
        for page in (capture.config_before, capture.config_after)
    ]
    _need(
        all(
            len(rows) == 1
            and rows[0].get("uid") == intent.account_uid
            and rows[0].get("mainUid") == intent.main_uid
            and rows[0].get("posMode") == "net_mode"
            for rows in configs
        )
    )
    _need(
        configs[0][0].get("acctLv") == configs[1][0].get("acctLv")
        and configs[0][0].get("acctLv") in ("2", "3")
    )
    grouped = {}
    for page in capture.current_pages:
        grouped.setdefault(page.selector.stream, []).append(page)
    _need(
        {"positions", "orders_pending", *("algo_" + kind for kind in ALGO_TYPES)}
        <= set(grouped)
    )
    positions = []
    for page in grouped["positions"]:
        positions.extend(facts._payload(page, **context))
    _need(all(facts._decimal(row, "pos") == 0 for row in positions))
    normal = facts._chain(
        tuple(grouped["orders_pending"]),
        kind="orders_pending",
        params=(),
        getter=lambda selector: (selector.stream, selector.after),
        **context,
    )
    _need(not normal)
    active = []
    for kind in ALGO_TYPES:
        stream = "algo_" + kind
        active.extend(
            facts._chain(
                tuple(grouped[stream]),
                kind=stream,
                params=(),
                getter=lambda selector: (selector.stream, selector.after),
                **context,
            )
        )
    protection = facts._payload(capture.protection, **context)
    _need(len(protection) == 1)
    algo = protection[0]
    _need(
        algo.get("algoId") == old.protection_algo_id
        and algo.get("algoClOrdId") == intent.protection_client_id
    )
    original_algo = decode(
        state["protected_capture"].protection.raw_body, maximum=524288
    )["data"][0]
    for key in (
        "algoId",
        "algoClOrdId",
        "instId",
        "posSide",
        "tdMode",
        "side",
        "ordType",
        "sz",
        "cTime",
        "tpTriggerPx",
        "slTriggerPx",
        "tpTriggerPxType",
        "slTriggerPxType",
        "tpOrdPx",
        "slOrdPx",
        "reduceOnly",
    ):
        _need(type(algo.get(key)) is str and algo[key] == original_algo.get(key))
    _need(facts._decimal(algo, "actualSz", nonnegative=True) == 0)
    _need(algo.get("ordIdList") == [] and algo.get("subAlgoIdList") == [])
    pending = bool(active)
    if pending:
        _need(allow_original_algo and len(active) == 1 and algo.get("state") == "live")
        for key in (
            "algoId",
            "algoClOrdId",
            "instId",
            "posSide",
            "tdMode",
            "side",
            "ordType",
            "sz",
            "state",
            "tpTriggerPx",
            "slTriggerPx",
            "tpTriggerPxType",
            "slTriggerPxType",
            "reduceOnly",
        ):
            _need(active[0].get(key) == algo.get(key))
    else:
        _need(algo.get("state") == "canceled")
    return close_id, average, fee, pending


async def capture_owned_live_controlled_result_v1(owner, *, allow_original_algo):
    from app.exchange.okx import native_live_close_write_v1 as physical
    from app.okx_live import owned_close_v1 as actor
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1

    state, original = actor._record(owner), actor._original(actor._record(owner))
    response_handle = state["responses"]["close"]
    response = physical._replay_owned_live_controlled_response_v1(
        response_handle, key=physical._ACTOR_SOURCE_KEY_V1
    )
    response_raw = _response_json(response)
    publish_live_lifecycle_blob_v1(
        state["evidence_root"], response_raw, domain="submissions"
    )
    return await _capture_result_pages(
        owner,
        state,
        original,
        response,
        response_raw,
        allow_original_algo=allow_original_algo,
    )


def _response_json(response):
    retained_response = {
        key: value
        for key, value in response.items()
        if key not in ("raw_body", "raw_headers")
    }
    retained_response["raw_body_base64"] = (
        base64.b64encode(response["raw_body"]).decode("ascii")
        if response["raw_body"] is not None
        else None
    )
    retained_response["raw_headers_base64"] = (
        [
            [base64.b64encode(item).decode("ascii") for item in pair]
            for pair in response["raw_headers"]
        ]
        if response["raw_headers"] is not None
        else None
    )
    return canonical(retained_response)


def retain_owned_controlled_responses_v1(state):
    """Keep safe actual received DATA on late rejection without new order effect."""
    from app.exchange.okx import native_live_close_write_v1 as physical
    from app.okx_live.lifecycle_custody_v1 import publish_live_lifecycle_blob_v1

    for action, handle in state.get("responses", {}).items():
        _need(physical._record(handle)["state"] is state)
        response = physical._replay_owned_live_controlled_response_v1(
            handle, key=physical._ACTOR_SOURCE_KEY_V1
        )
        _need(response["action"] == action)
        publish_live_lifecycle_blob_v1(
            state["evidence_root"], _response_json(response), domain="submissions"
        )


async def _capture_result_pages(
    owner, state, original, response, response_raw, *, allow_original_algo
):
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import postsubmit_capture_v1 as entry_capture
    from app.okx_live import trusted_source_bridge_v1 as source

    barrier = _stamp()
    capture_owner = lifecycle._new_owner(
        **{
            key: original[key]
            for key in (
                "runtime_owner",
                "key_owner",
                "repository",
                "source_owner",
                "intent",
                "handoff",
                "evidence_root",
                "report_id",
                "submission_started",
                "submission_sha256",
                "submission_attempted",
            )
        },
        journal=state["protected_journal"],
        capture_id=secrets.token_hex(16),
        capture_barrier=barrier,
    )
    current = lifecycle._record(capture_owner)
    current["phase"] = "capturing"
    token = lifecycle._ACTIVE_CAPTURE.set(capture_owner)
    try:
        client, lease, root, uid, main, credential = source._live_postsubmit_lease_v1(
            original["source_owner"], key=lifecycle._SOURCE_LEASE_KEY_V1
        )
        _need(
            (uid, main, credential)
            == (
                state["intent"].account_uid,
                state["intent"].main_uid,
                state["intent"].credential_session_sha256,
            )
        )
        current.update(client=client, lease=lease, source_root=root)
        selector = select_live_trade_read_v1(
            "order",
            instrument_id=state["intent"].instrument_id,
            client_order_id=state["close_client_order_id"],
        )
        before = await observe_live_trade_read_page_v1(client, lease, selector)
        verify_live_trade_read_page_v1(before, lease=lease)
        rows = decode(before.raw_body, maximum=524288)["data"]
        _need(len(rows) == 1)
        order_id = rows[0]["ordId"]
        pages, after_bill, seen = [], None, set()
        for _ in range(64):
            page = await observe_live_trade_read_page_v1(
                client,
                lease,
                select_live_trade_read_v1(
                    "fills",
                    instrument_id=state["intent"].instrument_id,
                    order_id=order_id,
                    after_bill_id=after_bill,
                ),
            )
            verify_live_trade_read_page_v1(page, lease=lease)
            pages.append(page)
            incoming = decode(page.raw_body, maximum=524288)["data"]
            if not incoming:
                break
            ids = [row["billId"] for row in incoming]
            _need(len(ids) == len(set(ids)) and not seen.intersection(ids))
            _need(
                after_bill is None or all(int(item) < int(after_bill) for item in ids)
            )
            seen.update(ids)
            after_bill = ids[-1]
        else:
            _need(False)
        captured = await entry_capture.capture_owned_live_postsubmit_pages_v1(
            capture_owner, key=entry_capture._CAPTURE_KEY_V1
        )
        after = await observe_live_trade_read_page_v1(client, lease, selector)
        verify_live_trade_read_page_v1(after, lease=lease)
        lease.verify_current()
        observed = _stamp()
        with localcontext(
            facts.Context(
                prec=200,
                rounding=facts.ROUND_HALF_EVEN,
                traps=[facts.InvalidOperation, facts.DivisionByZero, facts.Overflow],
            )
        ):
            close_id, average, fee, pending = _assess(
                state,
                captured,
                before,
                after,
                tuple(pages),
                response,
                barrier=barrier,
                observed=observed,
                allow_original_algo=allow_original_algo,
            )
    finally:
        lifecycle._ACTIVE_CAPTURE.reset(token)
    raw_custody = sha(canonical([item[1] for item in current["raw_captures"]]))
    bank = sha(
        canonical(
            [
                captured.raw_custody_sha256,
                before.receipt_sha256,
                after.receipt_sha256,
                *(page.receipt_sha256 for page in pages),
                sha(response_raw),
            ]
        )
    )
    cancel = (
        {
            "full_account_verified": True,
            "fills_complete": True,
            "flat_verified": True,
            "unknown_exposure": False,
            "position_count": 0,
            "pending_order_count": 0,
            "pending_algo_count": 1,
            "close_claim_sha256": state["claims"]["close"].document_sha256,
            "close_order_id": close_id,
            "close_client_order_id": state["close_client_order_id"],
            "entry_order_id": state["protected_facts"].order_id,
            "close_order_state": "filled",
            "close_filled_contracts": format(state["intent"].contracts, "f"),
            "protection_algo_id": state["protected_facts"].protection_algo_id,
            "protection_client_id": state["intent"].protection_client_id,
            "account_config_before_sha256": captured.config_before.receipt_sha256,
            "account_config_after_sha256": captured.config_after.receipt_sha256,
        }
        if pending
        else None
    )
    return ControlledLiveResultV1(
        capture_owner,
        captured,
        before,
        after,
        tuple(pages),
        response_raw,
        barrier,
        observed,
        raw_custody,
        bank,
        close_id,
        average,
        fee,
        True,
        not pending,
        pending,
        cancel,
    )


def verify_owned_controlled_result_v1(state):
    return _verify_result(state, state["terminal_capture"], pending=False)


def verify_owned_cancel_result_v1(state):
    result = state["cancel_capture"]
    _verify_result(state, result, pending=True)
    expected = {
        "full_account_verified": True,
        "fills_complete": True,
        "flat_verified": True,
        "unknown_exposure": False,
        "position_count": 0,
        "pending_order_count": 0,
        "pending_algo_count": 1,
        "close_claim_sha256": state["claims"]["close"].document_sha256,
        "close_order_id": result.close_order_id,
        "close_client_order_id": state["close_client_order_id"],
        "entry_order_id": state["protected_facts"].order_id,
        "close_order_state": "filled",
        "close_filled_contracts": format(state["intent"].contracts, "f"),
        "protection_algo_id": state["protected_facts"].protection_algo_id,
        "protection_client_id": state["intent"].protection_client_id,
        "account_config_before_sha256": result.entry_capture.config_before.receipt_sha256,
        "account_config_after_sha256": result.entry_capture.config_after.receipt_sha256,
    }
    _need(state["cancel_verified_facts"] == result.cancel_facts == expected)
    return True


def _verify_result(state, result, *, pending):
    from app.exchange.okx import native_live_close_write_v1 as physical
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(
        type(result) is ControlledLiveResultV1
        and result.close_filled is True
        and result.flat_verified is (not pending)
        and result.original_algo_pending is pending
    )
    current = lifecycle._record(result.source_owner)
    _need(current["lease"] is state["lease"] and current["intent"] is state["intent"])
    handle = state["responses"]["close"]
    _need(physical._record(handle)["state"] is state)
    response_data = physical._replay_owned_live_controlled_response_v1(
        handle, key=physical._ACTOR_SOURCE_KEY_V1
    )
    _need(_response_json(response_data) == result.close_response_json)
    _need(
        response_data["request_wire_sha256"]
        == state["claims"]["close"].request_wire_sha256
    )
    now = _stamp()
    validate_stamps((result.observed.model_dump(), now.model_dump()))
    _need(now.monotonic_ns - result.observed.monotonic_ns <= 5_000_000_000)
    verify_owned_capture_pages_v1(
        result.source_owner,
        result.entry_capture,
        extra_pages=(
            result.close_order_before,
            *result.close_fills,
            result.close_order_after,
        ),
    )
    response = decode(result.close_response_json, maximum=131072)
    with localcontext(
        facts.Context(
            prec=200,
            rounding=facts.ROUND_HALF_EVEN,
            traps=[facts.InvalidOperation, facts.DivisionByZero, facts.Overflow],
        )
    ):
        actual = _assess(
            state,
            result.entry_capture,
            result.close_order_before,
            result.close_order_after,
            result.close_fills,
            response,
            barrier=result.barrier,
            observed=result.observed,
            allow_original_algo=pending,
        )
    _need(
        actual
        == (result.close_order_id, result.close_average, result.close_fees, pending)
    )
    return True


def verify_owned_capture_pages_v1(owner, capture, *, extra_pages=()):
    """Reopen original private raw bytes and signed wire bindings, not flags."""
    from app.exchange.okx.live_account_config_observation_v1 import (
        verify_live_account_config_readback_v1,
    )
    from app.exchange.okx.live_account_raw_page_v1 import (
        verify_live_account_raw_page_readback_v1,
    )
    from app.exchange.okx.live_trade_read_page_v1 import verify_live_trade_read_page_v1
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1

    current = lifecycle._record(owner)
    lease = current["lease"]
    lease.verify_current()
    retained = []
    for _, digest, _, _, _ in current["raw_captures"]:
        raw = read_live_lifecycle_blob_v1(
            current["evidence_root"], digest, domain="raw"
        )
        _need(sha(raw) == digest)
        retained.append(decode(raw, maximum=12 * 1024 * 1024))
    pages = (
        capture.config_before,
        capture.order,
        *capture.fills,
        *capture.current_pages,
        *(() if capture.protection is None else (capture.protection,)),
        capture.config_after,
        *extra_pages,
    )
    for page in pages:
        _need(page.synthetic_mock is False)
        _need(
            any(
                item["raw_body_sha256"] == page.raw_sha256
                and item["raw_body_base64"]
                == base64.b64encode(page.raw_body).decode("ascii")
                and item["request_signature_sha256"] == page.request_signature_sha256
                and item["stamps"] == [stamp.model_dump() for stamp in page.stamps[:5]]
                for item in retained
            )
        )
    for page in (capture.config_before, capture.config_after):
        verify_live_account_config_readback_v1(page, lease=lease)
    for page in capture.current_pages:
        verify_live_account_raw_page_readback_v1(page, lease=lease)
    for page in (
        capture.order,
        *capture.fills,
        *(() if capture.protection is None else (capture.protection,)),
        *extra_pages,
    ):
        verify_live_trade_read_page_v1(page, lease=lease)


def controlled_close_terminal_document_v1(state):
    from app.domain.native_live_lifecycle_schema_v1 import SCHEMA
    from app.domain.source_primitives import utc_from_ns
    from app.okx_live import lifecycle_key_v1 as keys

    verify_owned_controlled_result_v1(state)
    result, journal, intent = (
        state["terminal_capture"],
        state["terminal_journal"],
        state["intent"],
    )
    original = decode(state["protected_journal"].proofs[-1], maximum=65536)
    document = {
        **original,
        **{
            key: value
            for key, value in keys.lifecycle_key_metadata_v1(state["key_owner"]).items()
            if key != "key_material_sha256"
        },
    }
    document.update(
        schema=SCHEMA,
        outcome="RECONCILED_FLAT",
        sequence=len(journal.proofs) + 1,
        previous_sha256=sha(journal.proofs[-1]) if journal.proofs else None,
        observed_at=utc_from_ns(result.observed.utc_ns).isoformat(),
        reconciliation_barrier=result.barrier.model_dump(),
        raw_bank_sha256=result.raw_bank_sha256,
        raw_custody_sha256=result.raw_custody_sha256,
        current_custody_sha256=result.entry_capture.current_custody_sha256,
        full_account_verified=True,
        fills_complete=True,
        flat_verified=True,
        unknown_exposure=False,
        position_count=0,
        pending_order_count=0,
        pending_algo_count=0,
        account_config_before_sha256=result.entry_capture.config_before.receipt_sha256,
        account_config_after_sha256=result.entry_capture.config_after.receipt_sha256,
        protection_verified=False,
        protection_algo_id=state["protected_facts"].protection_algo_id,
        entry_order_state="filled",
        entry_filled_contracts=format(intent.contracts, "f"),
        exit_order_id=result.close_order_id,
        exit_client_order_id=state["close_client_order_id"],
        exit_filled_contracts=format(intent.contracts, "f"),
        exit_lineage_verified=True,
        exit_kind="controlled_micro_close",
        exit_source_sha256=result.raw_bank_sha256,
        controlled_close_intent_sha256=sha(state["intent_json"]),
        controlled_close_claim_sha256=state["claims"]["close"].document_sha256,
        controlled_close_consent_sha256=state["consent"].consent_sha256,
        controlled_cancel_claim_sha256=state["claims"][
            "cancel_original_protection"
        ].document_sha256
        if "cancel_original_protection" in state["claims"]
        else None,
        failure_code=None,
        source_fact_codes=[],
        execution_authority=False,
    )
    return canonical(document)
