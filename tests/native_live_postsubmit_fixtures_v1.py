"""Synthetic raw DATA for pure Live math only; no transport/owner/signing proof."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.native_live_intent_v1 import LiveIntentDataV1
from app.domain.source_primitives import ClockStamp, canonical, sha, utc_from_ns
from app.exchange.okx.live_account_config_observation_v1 import (
    LiveAccountConfigObservationV1,
    _receipt_document,
)
from app.exchange.okx.live_account_raw_page_v1 import (
    ALGO_TYPES,
    LiveAccountRawPageV1,
    live_account_raw_page_receipt_document_v1,
    select_live_account_raw_page_v1,
)
from app.exchange.okx.live_trade_read_page_v1 import (
    LiveTradeReadPageV1,
    live_trade_read_receipt_v1,
    select_live_trade_read_v1,
)

BASE = 1_795_132_800_000_000_000
INSTRUMENT = "ETH-USDT-SWAP"
ORIGIN = "https://openapi.okx.com"
BINDING = "1" * 64


def stamp(milliseconds):
    return ClockStamp(
        utc_ns=BASE + milliseconds * 1_000_000,
        monotonic_ns=100_000_000_000 + milliseconds * 1_000_000,
    )


def intent():
    deadline = datetime(2026, 11, 20, tzinfo=UTC) + timedelta(seconds=20)
    raw = canonical(
        {
            "environment": "live",
            "instrument_id": INSTRUMENT,
            "direction": "long",
            "entry": "100",
            "stop_loss": "99",
            "take_profit": "103",
            "event_key": "f" * 64,
            "policy_sha256": "d" * 64,
            "expires_at": deadline.isoformat(),
        }
    )
    return LiveIntentDataV1(
        account_uid="730058",
        main_uid="730058",
        credential_session_sha256="a" * 64,
        source_revision_sha256="b" * 64,
        process_sha256="c" * 64,
        policy_sha256="d" * 64,
        arm_id="e" * 64,
        expected_control_revision=7,
        event_key="f" * 64,
        candidate_sha256=sha(raw),
        original_candidate_json=raw.decode(),
        report_sha256="1" * 64,
        evidence_sha256="2" * 64,
        recheck_sha256="3" * 64,
        instrument_id=INSTRUMENT,
        direction="long",
        entry=Decimal(100),
        stop_loss=Decimal(99),
        take_profit=Decimal(103),
        executable_price_bound=Decimal("100.1"),
        contracts=Decimal(1),
        leverage=1,
        margin_mode="isolated",
        position_side="net",
        client_order_id="CTCCLsynthetic",
        protection_client_id="CTCCAsynthetic",
        risk_samples=(Decimal("0.1"), Decimal("0.2")),
        margin_amount=Decimal(1),
        notional_amount=Decimal(10),
        deadline=deadline,
    )


def _common(rows, at, *, code="0"):
    raw = canonical({"code": code, "msg": "", "data": rows})
    return {
        "raw_body": raw,
        "raw_headers": ((b"content-type", b"application/json"),),
        "stamps": tuple(stamp(at + i) for i in range(6)),
        "signed_at": utc_from_ns(stamp(at).utc_ns)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "lease_binding_id": BINDING,
        "origin": ORIGIN,
        "raw_sha256": sha(raw),
        "canonical_sha256": sha(raw),
        "headers_sha256": "2" * 64,
        "request_signature_sha256": "3" * 64,
        "tls_certificate_sha256": None,
        "receipt_sha256": "",
        "synthetic_mock": True,
    }


def trade(selector, rows, at, *, code="0"):
    page = LiveTradeReadPageV1(selector=selector, **_common(rows, at, code=code))
    return replace(
        page, receipt_sha256=sha(canonical(live_trade_read_receipt_v1(page)))
    )


def current(stream, rows, at, *, after=None):
    selector = select_live_account_raw_page_v1(stream, after=after)
    field = (
        "ordId"
        if stream == "orders_pending"
        else "algoId"
        if stream.startswith("algo_")
        else None
    )
    ids = tuple(row[field] for row in rows) if field else ()
    page = LiveAccountRawPageV1(
        selector=selector,
        row_ids=ids,
        row_count=len(rows),
        empty_page=bool(field and not rows),
        **_common(rows, at),
    )
    return replace(
        page,
        receipt_sha256=sha(canonical(live_account_raw_page_receipt_document_v1(page))),
    )


def config(at, **changes):
    row = {
        "uid": "730058",
        "mainUid": "730058",
        "acctLv": "2",
        "posMode": "net_mode",
        **changes,
    }
    page = LiveAccountConfigObservationV1(
        expected_uid_sha256="4" * 64,
        expected_main_uid_sha256="5" * 64,
        **_common([row], at),
    )
    return replace(page, receipt_sha256=sha(canonical(_receipt_document(page))))


def rewrite(page, rows, *, code="0"):
    raw = canonical({"code": code, "msg": "", "data": rows})
    updated = replace(
        page,
        raw_body=raw,
        raw_sha256=sha(raw),
        canonical_sha256=sha(raw),
        receipt_sha256="",
    )
    receipt = (
        live_trade_read_receipt_v1(updated)
        if type(updated) is LiveTradeReadPageV1
        else live_account_raw_page_receipt_document_v1(updated)
        if type(updated) is LiveAccountRawPageV1
        else _receipt_document(updated)
    )
    return replace(updated, receipt_sha256=sha(canonical(receipt)))


def scenario(*, zero=False):
    value = intent()
    order = {
        "instId": INSTRUMENT,
        "ordId": "123",
        "clOrdId": value.client_order_id,
        "side": "buy",
        "posSide": "net",
        "tdMode": "isolated",
        "ordType": "fok",
        "sz": "1",
        "lever": "1",
        "accFillSz": "0" if zero else "1",
        "avgPx": "" if zero else "100",
        "state": "canceled" if zero else "filled",
        "cTime": str(BASE // 1_000_000 + 2),
        "uTime": str(BASE // 1_000_000 + 12),
    }
    fill = {
        "instId": INSTRUMENT,
        "ordId": "123",
        "clOrdId": value.client_order_id,
        "posSide": "net",
        "side": "buy",
        "billId": "789",
        "tradeId": "10",
        "fillSz": "1",
        "fillPx": "100",
        "fee": "-0.05",
        "feeCcy": "USDT",
        "fillTime": str(BASE // 1_000_000 + 10),
        "ts": str(BASE // 1_000_000 + 11),
    }
    protection = {
        "instId": INSTRUMENT,
        "algoId": "456",
        "algoClOrdId": value.protection_client_id,
        "side": "sell",
        "posSide": "net",
        "tdMode": "isolated",
        "ordType": "oco",
        "state": "live",
        "sz": "1",
        "tpTriggerPx": "103",
        "slTriggerPx": "99",
        "tpTriggerPxType": "mark",
        "slTriggerPxType": "mark",
        "tpOrdPx": "-1",
        "slOrdPx": "-1",
        "closeFraction": "",
        "actualSz": "0",
        "reduceOnly": "true",
        "cTime": str(BASE // 1_000_000 + 15),
        "uTime": str(BASE // 1_000_000 + 25),
    }
    first = trade(
        select_live_trade_read_v1("fills", instrument_id=INSTRUMENT, order_id="123"),
        [] if zero else [fill],
        50,
    )
    fills = (
        (first,)
        if zero
        else (
            first,
            trade(
                select_live_trade_read_v1(
                    "fills",
                    instrument_id=INSTRUMENT,
                    order_id="123",
                    after_bill_id="789",
                ),
                [],
                60,
            ),
        )
    )
    position = {
        "instId": INSTRUMENT,
        "posSide": "net",
        "mgnMode": "isolated",
        "lever": "1",
        "pos": "1",
        "cTime": str(BASE // 1_000_000 + 10),
        "uTime": str(BASE // 1_000_000 + 25),
    }
    pages = [
        current("positions", [] if zero else [position], 70),
        current("orders_pending", [], 80),
    ]
    for offset, kind in enumerate(ALGO_TYPES):
        stream = "algo_" + kind
        at = 90 + offset * 10
        pages.append(
            current(
                stream,
                [dict(protection, last="100")] if kind == "oco" and not zero else [],
                at,
            )
        )
        if kind == "oco" and not zero:
            pages.append(current(stream, [], at + 6, after="456"))
    return value, {
        "order_page": trade(
            select_live_trade_read_v1(
                "order", instrument_id=INSTRUMENT, client_order_id=value.client_order_id
            ),
            [order],
            40,
        ),
        "fill_pages": fills,
        "protection_page": None
        if zero
        else trade(
            select_live_trade_read_v1(
                "algo",
                instrument_id=INSTRUMENT,
                algo_client_id=value.protection_client_id,
            ),
            [dict(protection, last="100.2")],
            180,
        ),
        "config_before": config(30),
        "config_after": config(190),
        "current_pages": tuple(pages),
        "submission_started": stamp(0),
        "submission_barrier": stamp(20),
        "validated_at": stamp(200),
        "tick_size": Decimal("0.01"),
    }
