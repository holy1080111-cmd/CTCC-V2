"""Explicit synthetic LIVE raw DTOs; no venue, custody, signer or SQL authority."""

from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, localcontext
from types import SimpleNamespace

import pytest

from app.domain.native_live_intent_v1 import LiveIntentDataV1
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.exchange.okx.live_trade_read_page_v1 import select_live_trade_read_v1
from app.okx_live import original_oco_recovery_v1 as subject
from app.okx_live import reconciliation_facts_v1 as facts
from tests import native_live_postsubmit_fixtures_v1 as fixture


def original_bindings(*, entry="123", algo="456"):
    return SimpleNamespace(
        proofs=(
            canonical(
                {
                    "outcome": "PROTECTED_HOLD",
                    "entry_order_id": entry,
                    "protection_algo_id": algo,
                    "protection_verified": True,
                    "full_account_verified": True,
                    "fills_complete": True,
                }
            ),
        )
    )


def scenario(*, direction="long", trigger="tp", original_intent=None):
    intent, data = fixture.scenario()
    if original_intent is not None:
        # The supplied exact intent is selected before NEW synthetic raw DTOs
        # are constructed, never copied over a retained source or receipt.
        intent = original_intent
        direction = intent.direction
        assert intent.account_uid == "730058" and intent.main_uid == "730058"
        assert (
            intent.client_order_id == "CTCCLsynthetic"
            and intent.protection_client_id == "CTCCAsynthetic"
        )
        assert (
            intent.contracts == 1
            and intent.leverage == 1
            and intent.instrument_id == fixture.INSTRUMENT
        )
    if direction == "short" and original_intent is None:
        values = intent.model_dump(mode="python")
        original = decode(intent.original_candidate_json.encode())
        original.update(direction="short", stop_loss="101", take_profit="97")
        raw = canonical(original)
        values.update(
            direction="short",
            stop_loss=Decimal(101),
            take_profit=Decimal(97),
            executable_price_bound=Decimal("99.9"),
            original_candidate_json=raw.decode(),
            candidate_sha256=sha(raw),
        )
        intent = LiveIntentDataV1.model_validate(values, strict=True)
    side = "buy" if direction == "long" else "sell"
    opposite = "sell" if side == "buy" else "buy"
    entry = decode(data["order_page"].raw_body)["data"][0]
    entry.update(instType="SWAP", side=side)
    entry_fill = decode(data["fill_pages"][0].raw_body)["data"][0]
    entry_fill.update(instType="SWAP", side=side)
    price = intent.take_profit if trigger == "tp" else intent.stop_loss
    algo = decode(data["protection_page"].raw_body)["data"][0]
    algo.update(
        instType="SWAP",
        state="effective",
        side=opposite,
        lever="1",
        actualSide=trigger,
        actualSz="1",
        actualPx=str(price),
        tpTriggerPx=str(intent.take_profit),
        slTriggerPx=str(intent.stop_loss),
        failCode="",
        ordIdList=["124"],
        ordId="124",
        subAlgoIdList=[],
        triggerTime=str(fixture.BASE // 1_000_000 + 100),
        uTime=str(fixture.BASE // 1_000_000 + 115),
    )
    child = {
        "instId": intent.instrument_id,
        "instType": "SWAP",
        "ordId": "124",
        "clOrdId": "",
        "side": opposite,
        "posSide": "net",
        "tdMode": "isolated",
        "ordType": "market",
        "sz": "1",
        "accFillSz": "1",
        "avgPx": str(price),
        "state": "filled",
        "lever": "1",
        "reduceOnly": "true",
        "algoId": "456",
        "algoClOrdId": intent.protection_client_id,
        "cTime": str(fixture.BASE // 1_000_000 + 101),
        "uTime": str(fixture.BASE // 1_000_000 + 112),
    }
    exit_fill = {
        **entry_fill,
        "ordId": "124",
        "clOrdId": "",
        "side": opposite,
        "billId": "790",
        "tradeId": "11",
        "fillPx": str(price),
        "fee": "-0.03",
        "fillTime": str(fixture.BASE // 1_000_000 + 105),
        "ts": str(fixture.BASE // 1_000_000 + 106),
    }

    def trade(kind, rows, at, **values):
        return fixture.trade(
            select_live_trade_read_v1(
                kind, instrument_id=intent.instrument_id, **values
            ),
            rows,
            at,
        )

    pages = [
        fixture.current("positions", [], 310),
        fixture.current("orders_pending", [], 320),
    ]
    for offset, kind in enumerate(fixture.ALGO_TYPES):
        pages.append(fixture.current("algo_" + kind, [], 330 + offset * 10))
    capture = subject.OriginalLiveOcoTerminalCaptureV1(
        trade("order", [entry], 200, order_id="123"),
        (
            trade("fills_history", [entry_fill], 210, order_id="123"),
            trade("fills_history", [], 220, order_id="123", after_bill_id="789"),
        ),
        trade("algo", [algo], 230, algo_id="456"),
        fixture.config(300),
        fixture.config(430),
        tuple(pages),
        "c" * 64,
        "d" * 64,
        fixture.stamp(450),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal(1),
        "ETH",
        entry_order_after=trade("order", [entry], 270, order_id="123"),
        exit_order=trade("order", [child], 240, order_id="124"),
        exit_order_after=trade("order", [child], 280, order_id="124"),
        exit_fills=(
            trade("fills_history", [exit_fill], 250, order_id="124"),
            trade("fills_history", [], 260, order_id="124", after_bill_id="790"),
        ),
        protection_after=trade("algo", [algo], 290, algo_id="456"),
    )
    args = {
        "history": facts.LiveReconciliationHistoryV1(utc_from_ns(fixture.BASE)),
        "barrier": fixture.stamp(190),
        "expected_algo_id": "456",
        "expected_entry_order_id": "123",
    }
    return intent, capture, args


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("trigger", ["tp", "sl"])
def test_exact_original_oco_child_and_full_flat_source_classifies_only_data(
    direction, trigger
):
    intent, capture, args = scenario(direction=direction, trigger=trigger)
    result = subject.assess_original_live_oco_terminal_v1(intent, capture, **args)
    assert result.outcome == "RECONCILED_FLAT" and result.codes == ()
    assert (
        result.order_id == "123"
        and result.protection_algo_id == "456"
        and result.exit_order_id == "124"
    )
    assert result.filled_contracts == result.exit_filled_contracts == intent.contracts
    assert result.exit_kind == "original_oco" and result.exit_lineage_verified is True
    assert result.exit_source_sha256 == result.raw_bank_sha256
    assert (
        result.execution_authority
        is result.resolution_authority
        is result.source_authenticity_verified
        is False
    )
    assert result.economic_qualification_verified is False


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_original_id",
        "changed_original_entry_id",
        "changed_stop",
        "last_trigger",
        "split_exit",
        "sub_algo",
        "wrong_actual_side",
        "partial_algo",
        "wrong_algo_px",
        "wrong_child_algo",
        "partial_child",
        "wrong_child_side",
        "changed_algo_after",
        "changed_child_after",
        "changed_entry_after",
        "missing_exit_terminal",
        "wrong_bill_cursor",
        "foreign_exit_fill",
        "fee_currency",
        "missing_fee",
        "duplicate_trade",
        "duplicate_bill",
        "before_trigger",
        "stale",
        "nonflat",
        "pending",
        "active_algo",
        "wrong_uid",
        "mixed_session",
        "raw_changed",
        "unknown_history",
        "recent_substitute",
        "clock_reordered",
        "original_window",
        "unknown_actual_size",
    ],
)
def test_any_missing_changed_ambiguous_source_keeps_hold(mutation):
    intent, capture, args = scenario()
    changes = {}
    if mutation in (
        "unknown_original_id",
        "changed_original_entry_id",
        "unknown_history",
    ):
        args[
            {
                "unknown_original_id": "expected_algo_id",
                "changed_original_entry_id": "expected_entry_order_id",
                "unknown_history": "history",
            }[mutation]
        ] = None if mutation != "changed_original_entry_id" else "999"
    elif mutation in (
        "changed_stop",
        "last_trigger",
        "split_exit",
        "sub_algo",
        "wrong_actual_side",
        "partial_algo",
        "wrong_algo_px",
        "unknown_actual_size",
    ):
        row = decode(capture.protection.raw_body)["data"][0]
        row.update(
            {
                "changed_stop": {"slTriggerPx": "98"},
                "last_trigger": {"slTriggerPxType": "last"},
                "split_exit": {"ordIdList": ["124", "125"]},
                "sub_algo": {"subAlgoIdList": ["457"]},
                "wrong_actual_side": {"actualSide": ""},
                "partial_algo": {"actualSz": "0.5"},
                "wrong_algo_px": {"actualPx": "104"},
                "unknown_actual_size": {"actualSz": None},
            }[mutation]
        )
        changes.update(
            protection=fixture.rewrite(capture.protection, [row]),
            protection_after=fixture.rewrite(capture.protection_after, [row]),
        )
    elif mutation in ("wrong_child_algo", "partial_child", "wrong_child_side"):
        row = decode(capture.exit_order.raw_body)["data"][0]
        row.update(
            {
                "wrong_child_algo": {"algoId": "457"},
                "partial_child": {"accFillSz": "0.5"},
                "wrong_child_side": {"side": "buy"},
            }[mutation]
        )
        changes.update(
            exit_order=fixture.rewrite(capture.exit_order, [row]),
            exit_order_after=fixture.rewrite(capture.exit_order_after, [row]),
        )
    elif mutation in (
        "changed_algo_after",
        "changed_child_after",
        "changed_entry_after",
    ):
        field = {
            "changed_algo_after": "protection_after",
            "changed_child_after": "exit_order_after",
            "changed_entry_after": "entry_order_after",
        }[mutation]
        row = decode(getattr(capture, field).raw_body)["data"][0]
        row["sz"] = "2"
        changes[field] = fixture.rewrite(getattr(capture, field), [row])
    elif mutation == "missing_exit_terminal":
        changes["exit_fills"] = capture.exit_fills[:1]
    elif mutation == "wrong_bill_cursor":
        last = capture.exit_fills[-1]
        changes["exit_fills"] = (
            capture.exit_fills[0],
            replace(last, selector=replace(last.selector, after_bill_id="789")),
        )
    elif mutation in (
        "foreign_exit_fill",
        "fee_currency",
        "missing_fee",
        "duplicate_trade",
        "duplicate_bill",
        "before_trigger",
    ):
        row = decode(capture.exit_fills[0].raw_body)["data"][0]
        row.update(
            {
                "foreign_exit_fill": {"ordId": "125"},
                "fee_currency": {"feeCcy": "BTC"},
                "missing_fee": {"fee": None},
                "duplicate_trade": {"tradeId": "10"},
                "duplicate_bill": {"billId": "789"},
                "before_trigger": {"fillTime": str(fixture.BASE // 1_000_000 + 99)},
            }[mutation]
        )
        changes["exit_fills"] = (
            fixture.rewrite(capture.exit_fills[0], [row]),
            capture.exit_fills[-1],
        )
    elif mutation == "stale":
        changes["validated_at"] = fixture.stamp(6500)
    elif mutation in ("nonflat", "pending", "active_algo"):
        pages = list(capture.current_pages)
        index = 0 if mutation == "nonflat" else 1 if mutation == "pending" else 2
        pages[index] = fixture.rewrite(
            pages[index],
            [
                {"pos": "1"}
                if mutation == "nonflat"
                else {"ordId": "999"}
                if mutation == "pending"
                else {"algoId": "999"}
            ],
        )
        changes["current_pages"] = tuple(pages)
    elif mutation == "wrong_uid":
        row = decode(capture.config_after.raw_body)["data"][0]
        row["uid"] = "730059"
        changes["config_after"] = fixture.rewrite(capture.config_after, [row])
    elif mutation == "mixed_session":
        changes["exit_order"] = replace(capture.exit_order, lease_binding_id="9" * 64)
    elif mutation == "raw_changed":
        changes["exit_order"] = replace(
            capture.exit_order, raw_body=capture.exit_order.raw_body + b" "
        )
    elif mutation == "recent_substitute":
        changes["exit_fills"] = tuple(
            replace(
                page,
                selector=select_live_trade_read_v1(
                    "fills",
                    instrument_id=intent.instrument_id,
                    order_id="124",
                    after_bill_id=page.selector.after_bill_id,
                ),
            )
            for page in capture.exit_fills
        )
    elif mutation == "clock_reordered":
        changes["exit_order_after"] = replace(
            capture.exit_order_after, stamps=capture.exit_order.stamps
        )
    else:
        args["history"] = facts.LiveReconciliationHistoryV1(
            utc_from_ns(fixture.BASE + 3_000_000)
        )
    result = subject.assess_original_live_oco_terminal_v1(
        intent, replace(capture, **changes), **args
    )
    assert result.outcome == "UNCERTAIN" and result.codes
    assert result.exit_lineage_verified is False and result.exit_source_sha256 is None
    assert result.execution_authority is result.resolution_authority is False


def test_original_binding_requires_retained_protected_proof_and_all_ids_agree():
    assert subject.expected_original_bindings_v1(original_bindings()) == ("123", "456")
    assert subject.expected_original_bindings_v1(SimpleNamespace(proofs=())) == (
        None,
        None,
    )
    row = decode(original_bindings().proofs[0])
    row["outcome"] = "UNCERTAIN"
    assert subject.expected_original_bindings_v1(
        SimpleNamespace(proofs=(canonical(row),))
    ) == (None, None)
    for field in ("entry_order_id", "protection_algo_id"):
        changed = decode(original_bindings().proofs[0])
        changed[field] = "999"
        with pytest.raises(facts.LiveReconciliationFactsError):
            subject.expected_original_bindings_v1(
                SimpleNamespace(
                    proofs=(*original_bindings().proofs, canonical(changed))
                )
            )


def test_exact_math_is_independent_of_caller_decimal_context():
    intent, capture, args = scenario()
    expected = subject.assess_original_live_oco_terminal_v1(intent, capture, **args)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        assert (
            subject.assess_original_live_oco_terminal_v1(intent, capture, **args)
            == expected
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["effective", "live", "missing"])
async def test_original_id_probe_chooses_one_source_without_latest_id_substitution(
    monkeypatch, state_name
):
    from app.okx_live import owned_recovery_v1 as recovery
    from app.okx_live import postsubmit_capture_v1 as ordinary

    intent, capture, _ = scenario()
    marker, owner, lease = object(), object(), object()
    state = {
        "journal": original_bindings(),
        "intent": intent,
        "client": object(),
        "lease": lease,
    }
    calls = []
    monkeypatch.setattr(recovery, "_scope", lambda value: (state, None))

    async def observe(client, active, selector):
        assert client is state["client"] and active is lease
        assert (
            selector.kind == "algo"
            and selector.algo_id == "456"
            and selector.algo_client_id is None
        )
        calls.append("original_id_probe")
        rows = (
            []
            if state_name == "missing"
            else [
                {**decode(capture.protection.raw_body)["data"][0], "state": state_name}
            ]
        )
        return fixture.rewrite(capture.protection, rows)

    monkeypatch.setattr(subject, "observe_live_trade_read_page_v1", observe)
    monkeypatch.setattr(
        subject, "verify_live_trade_read_page_v1", lambda page, *, lease: None
    )

    async def terminal(value, *, key):
        assert value is owner and key is subject._CAPTURE_KEY
        calls.append("terminal_source")
        return marker

    async def original(value, *, key):
        assert value is owner and key is ordinary._CAPTURE_KEY_V1
        calls.append("ordinary_source")
        return marker

    monkeypatch.setattr(subject, "capture_owned_original_oco_terminal_v1", terminal)
    monkeypatch.setattr(ordinary, "capture_owned_live_postsubmit_pages_v1", original)
    if state_name == "missing":
        with pytest.raises(facts.LiveReconciliationFactsError):
            await subject.capture_recovery_pages_v1(owner, key=subject._CAPTURE_KEY)
        assert calls == ["original_id_probe"]
    else:
        assert (
            await subject.capture_recovery_pages_v1(owner, key=subject._CAPTURE_KEY)
            is marker
        )
        assert calls == [
            "original_id_probe",
            "terminal_source" if state_name == "effective" else "ordinary_source",
        ]
    assert state["recovery_entry_order_id"] == "123"


def test_terminal_projection_preserves_original_oco_without_invented_own_close(
    monkeypatch,
):
    from app.okx_live import lifecycle_key_v1 as keys
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    intent, capture, args = scenario()
    assessed = subject.assess_original_live_oco_terminal_v1(intent, capture, **args)
    rid, _, _ = subject.exact_live_intent_document_v1(intent)
    monkeypatch.setattr(
        keys,
        "lifecycle_key_metadata_v1",
        lambda key: {
            "environment": "live",
            "account_uid": intent.account_uid,
            "main_uid": intent.main_uid,
            "origin": fixture.ORIGIN,
        },
    )
    state = {
        "intent": intent,
        "journal": SimpleNamespace(
            reservation_id=rid, proofs=original_bindings().proofs
        ),
        "key_owner": object(),
        "report_id": "original_report",
        "capture_barrier": args["barrier"],
        "submission_started": None,
        "submission_attempted": True,
        "submission_sha256": "f" * 64,
        "raw_captures": [],
        "recovery_history": args["history"],
        "credential_continuity": "fresh_recovery_lease_uid_reverified",
    }
    document = decode(
        lifecycle._proof_document(
            state, capture=capture, facts=assessed, economics=None
        )
    )
    assert (
        document["outcome"] == "RECONCILED_FLAT"
        and document["exit_kind"] == "original_oco"
    )
    assert document["entry_order_id"] == "123" and document["exit_order_id"] == "124"
    assert (
        document["protection_algo_id"] == "456"
        and document["exit_lineage_verified"] is True
    )
    assert (
        document["entry_filled_contracts"] == document["exit_filled_contracts"] == "1"
    )
    assert (
        document["submission_started"] is None
        and document["fixed_fill_economics"] is None
    )
    assert document["recovery_history"]["economic_qualification_reestablished"] is False
    assert document["flat_verified"] is True and document["unknown_exposure"] is False
    assert all(
        document[name] == 0
        for name in ("position_count", "pending_order_count", "pending_algo_count")
    )
    assert not any("close_intent" in name or "own_close" in name for name in document)
    assert document["execution_authority"] is False
