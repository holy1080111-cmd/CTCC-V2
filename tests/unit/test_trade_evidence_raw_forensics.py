"""Only synthetic retained sources. No private API, file, order or Notion IO."""

import ast
import hashlib
import json
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest

from app.trade_evidence import post_submit
from app.trade_evidence import raw_forensics as module
from app.trade_evidence.forensics import candidate_sha256
from app.trade_qualification import account_capture as capture
from app.trade_qualification.reservations import digest
from tests.unit import test_trade_evidence_post_submit as submit
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
from tests.unit.test_trade_forensics import at


def wire(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def fixtures(
    *,
    direction="long",
    fill_changes=None,
    funding_changes=None,
    extra_fills=(),
    empty=False,
):
    value, args = submit.inputs(direction)
    value = value.model_copy(update={"account_id": UID})
    receipt = submit.reservation(value)
    args.update(
        expected_candidate_sha256=candidate_sha256(value),
        reservation_before_submit=receipt,
        expected_reservation_sha256=digest(receipt),
    )
    report = post_submit.build_submission_report(value, **args)
    report_raw = post_submit.freeze_submission_report(report)
    fill_changes = fill_changes or {}
    opening = "buy" if direction == "long" else "sell"
    closing = "sell" if direction == "long" else "buy"
    fills = [
        row(
            "fills_history",
            "903",
            ordId="801",
            clOrdId="closeSynthetic",
            tradeId="703",
            side=closing,
            fillPx="108" if direction == "long" else "92",
            fillSz="10",
            fee="-0.03",
            fillTime=ms(at(40)),
            ts=ms(at(41)),
            fillPnl="999999",
        ),
        row(
            "fills_history",
            "902",
            ordId=submit.EXCHANGE_ORDER_ID,
            clOrdId=submit.CLIENT_ORDER_ID,
            tradeId="702",
            side=opening,
            fillPx="102" if direction == "long" else "98",
            fillSz="6",
            fee="0.02",
            fillTime=ms(at(20)),
            ts=ms(at(21)),
            fillPnl="0",
        ),
        row(
            "fills_history",
            "901",
            ordId=submit.EXCHANGE_ORDER_ID,
            clOrdId=submit.CLIENT_ORDER_ID,
            tradeId="701",
            side=opening,
            fillPx="100",
            fillSz="4",
            fee="-0.01",
            fillTime=ms(at(10)),
            ts=ms(at(11)),
            fillPnl="0",
        ),
    ]
    fills = [item | fill_changes.get(item["billId"], {}) for item in fills]
    fills.extend(extra_fills)
    fills.sort(key=lambda item: int(item["billId"]), reverse=True)
    bill = row("bills_archive", "904", instId=INSTRUMENT, balChg="-0.04", ts=ms(at(25)))
    bill.update(funding_changes or {})
    # A mirrored trade bill must not add its fee/PnL to the already mapped fill.
    mirror = row(
        "bills_archive",
        "901",
        type="2",
        subType="1",
        instId=INSTRUMENT,
        fee="-999",
        pnl="999",
        ts=ms(at(11)),
    )
    pages = {stream: [[]] for stream in CURSORS}
    pages["positions"] = [[]]
    if not empty:
        pages["fills_history"] = [fills, []]
        pages["bills_archive"] = [[bill, mirror], []]
    selected = plan()
    account = verify(*records(selected=selected, pages=pages))
    frozen = capture.freeze_demo_account_packet(
        account, expected_plan_sha256=capture.plan_sha256(selected)
    )
    path = {
        "schema_version": "ctcc.recorded_holding_path.v1",
        "instrument_id": INSTRUMENT,
        "price_basis": "last_trade",
        "received_at_ms": ms(NOW),
        "intervals": [
            [ms(at(10)), ms(at(20)), "100", "103", "97", "101"],
            [ms(at(20)), ms(at(40)), "101", "109", "90", "108"],
        ],
    }
    path_raw = wire(path)
    attribution = {
        "schema_version": "ctcc.raw_trade_attribution.v1",
        "report_id": report.report_id,
        "candidate_sha256": report.candidate_sha256,
        "submission_report_sha256": sha(report_raw),
        "account_packet_sha256": frozen.sha256,
        "orders": [
            {
                "order_id": submit.EXCHANGE_ORDER_ID,
                "client_order_id": submit.CLIENT_ORDER_ID,
                "role": "entry",
            },
            {"order_id": "801", "client_order_id": "closeSynthetic", "role": "exit"},
        ],
        "funding_bill_ids": [] if empty else ["904"],
        "holding_path_sha256": sha(path_raw),
        "purpose": "synthetic_test",
    }
    attr_raw = wire(attribution)
    return report_raw, {
        "expected_submission_sha256": sha(report_raw),
        "account_bytes": frozen.payload,
        "expected_account_sha256": frozen.sha256,
        "expected_account_plan_sha256": capture.plan_sha256(selected),
        "attribution_bytes": attr_raw,
        "expected_attribution_sha256": sha(attr_raw),
        "holding_path_bytes": path_raw,
    }


@pytest.fixture(scope="module")
def sources():
    return fixtures()


def run(sources):
    body, args = sources
    return module.reconstruct_trade_forensics(body, **args)


def change_attr(sources, action):
    body, args = sources
    args = dict(args)
    value = json.loads(args["attribution_bytes"])
    action(value)
    raw = wire(value)
    args.update(attribution_bytes=raw, expected_attribution_sha256=sha(raw))
    return body, args


def change_path(sources, action):
    body, args = sources
    args = dict(args)
    value = json.loads(args["holding_path_bytes"])
    action(value)
    raw = wire(value)
    args["holding_path_bytes"] = raw
    return change_attr(
        (body, args), lambda attr: attr.update(holding_path_sha256=sha(raw))
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_raw_mapping_replays_partial_fills_and_never_marks_ack_as_filled(direction):
    result = run(fixtures(direction=direction))
    packet = result.analysis.inputs
    assert [item.evidence_id for item in packet.fills] == [
        "fill_901",
        "fill_902",
        "fill_903",
    ]
    assert [item.contracts for item in packet.fills] == [
        Decimal(4),
        Decimal(6),
        Decimal(10),
    ]
    assert [item.occurred_at for item in packet.fills] == [at(10), at(20), at(40)]
    assert [item.amount for item in packet.cashflows] == [
        Decimal("-.01"),
        Decimal(".02"),
        Decimal("-.03"),
    ]
    assert all(item.kind == "fee" for item in packet.cashflows)
    assert packet.fills[-1].exit_reason == "unknown"
    assert result.analysis.position_status == "unknown"
    assert not result.analysis.metrics_complete
    assert all(item.status == "partial" for item in packet.coverage)
    assert result.analysis.pnl.value is None
    assert result.analysis.mfe.value is None and result.analysis.mae.value is None
    assert result.analysis.original_risk.value == Decimal(5)
    assert not result.execution_authority and not result.source_authenticity_verified
    assert not result.funding_accrual_verified
    receipt = json.loads(result.canonical_receipt)
    assert receipt["funding_bill_claims"][0]["signed_balance_change"] == "-0.04"
    assert receipt["funding_bill_claims"][0]["effective_accrual_at"] is None
    assert receipt["funding_bill_claims"][0]["source_timestamp"] == at(25).isoformat()
    assert (
        not receipt["real_trade_sample_verified"] and not receipt["protection_verified"]
    )


def test_canonical_receipt_replay_requires_original_pinned_raw_sources(sources):
    result = run(sources)
    replay = module.verify_raw_forensics_receipt(
        result.canonical_receipt, result.receipt_sha256, sources[0], **sources[1]
    )
    assert replay == result
    assert sha(result.canonical_receipt) == result.receipt_sha256
    assert wire(json.loads(result.canonical_receipt)) == result.canonical_receipt


def test_result_repr_hides_raw_private_data_and_flags_cannot_be_constructor_claims(
    sources,
):
    result = run(sources)
    rendered = repr(result)
    assert "canonical_receipt=" not in rendered and "analysis=" not in rendered
    assert (
        submit.CLIENT_ORDER_ID not in rendered and "forensics_synthetic" not in rendered
    )
    for name in (
        "execution_authority",
        "source_authenticity_verified",
        "funding_accrual_verified",
    ):
        with pytest.raises(TypeError):
            module.RawForensicsReplay(
                analysis=result.analysis,
                canonical_receipt=result.canonical_receipt,
                receipt_sha256=result.receipt_sha256,
                **{name: True},
            )


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "source_authenticity_verified",
        "real_trade_sample_verified",
        "protection_verified",
        "funding_accrual_verified",
    ],
)
def test_self_consistently_rehashed_result_claims_rejected(sources, field):
    result = run(sources)
    receipt = json.loads(result.canonical_receipt)
    receipt[field] = True
    raw = wire(receipt)
    with pytest.raises(
        module.RawForensicsError, match="^raw_forensics_receipt_invalid$"
    ):
        module.verify_raw_forensics_receipt(raw, sha(raw), sources[0], **sources[1])


def test_self_consistently_rehashed_metric_claim_rejected(sources):
    result = run(sources)
    receipt = json.loads(result.canonical_receipt)
    receipt["analysis"]["original_risk"]["value"] = "999"
    raw = wire(receipt)
    with pytest.raises(module.RawForensicsError):
        module.verify_raw_forensics_receipt(raw, sha(raw), sources[0], **sources[1])


@pytest.mark.parametrize(
    "name",
    [
        "expected_submission_sha256",
        "expected_account_sha256",
        "expected_account_plan_sha256",
        "expected_attribution_sha256",
    ],
)
@pytest.mark.parametrize("pin", ["0" * 64, "F" * 64, "", None, True])
def test_external_pins_fail_closed(sources, name, pin):
    args = sources[1] | {name: pin}
    with pytest.raises(
        module.RawForensicsError, match="^raw_forensics_reconstruction_invalid$"
    ):
        run((sources[0], args))


@pytest.mark.parametrize(
    "field",
    [
        "report_id",
        "candidate_sha256",
        "submission_report_sha256",
        "account_packet_sha256",
        "schema_version",
        "purpose",
    ],
)
def test_attribution_identity_cannot_be_replaced_by_rehash(sources, field):
    altered = change_attr(sources, lambda attr: attr.update({field: "wrong"}))
    with pytest.raises(module.RawForensicsError):
        run(altered)


@pytest.mark.parametrize(
    "field",
    [
        "PASS",
        "complete",
        "metrics_complete",
        "source_authenticity_verified",
        "execution_authority",
        "pnl",
    ],
)
def test_attribution_accepts_no_outcome_or_authority_fields(sources, field):
    with pytest.raises(module.RawForensicsError):
        run(change_attr(sources, lambda attr: attr.update({field: "true"})))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda attr: attr.update(orders=[]),
        lambda attr: attr["orders"].append(dict(attr["orders"][0])),
        lambda attr: attr["orders"][0].update(order_id="123"),
        lambda attr: attr["orders"][0].update(client_order_id="different"),
        lambda attr: attr["orders"][1].update(role="entry"),
        lambda attr: attr["orders"][1].update(role="PASS"),
        lambda attr: attr["orders"][1].update(exit_reason="take_profit"),
        lambda attr: attr.update(funding_bill_ids=["904", "904"]),
        lambda attr: attr.update(funding_bill_ids=["999"]),
        lambda attr: attr.update(funding_bill_ids=["901"]),
        lambda attr: attr.update(holding_path_sha256=None),
    ],
)
def test_order_and_funding_attribution_is_bounded_and_exact(sources, mutation):
    with pytest.raises(module.RawForensicsError):
        run(change_attr(sources, mutation))


@pytest.mark.parametrize(
    "changes",
    [
        {"clOrdId": "wrong"},
        {"clOrdId": ""},
        {"instId": "ETH-USDT-SWAP"},
        {"posSide": "short"},
        {"fillSz": "0"},
        {"fillSz": ""},
        {"fillPx": ""},
        {"fillPx": "0"},
        {"fee": ""},
        {"fillTime": ""},
        {"fillTime": ms(at(9))},
        {"feeCcy": "bad"},
        {"tradeId": "702"},
        {"side": "sell"},
    ],
)
def test_bad_selected_raw_fills_are_not_skipped_or_repaired(changes):
    with pytest.raises(
        module.RawForensicsError, match="^raw_forensics_reconstruction_invalid$"
    ):
        run(fixtures(fill_changes={"901": changes}))


def test_all_matching_supplied_fills_are_selected_not_a_caller_subset():
    extra = row(
        "fills_history",
        "905",
        ordId=submit.EXCHANGE_ORDER_ID,
        clOrdId=submit.CLIENT_ORDER_ID,
        tradeId="705",
        side="buy",
        fillPx="102",
        fillSz="1",
        fee="0",
        fillTime=ms(at(30)),
        ts=ms(at(31)),
        fillPnl="0",
    )
    result = run(fixtures(extra_fills=(extra,)))
    assert len(result.analysis.inputs.fills) == 4
    assert result.analysis.inputs.fills[-2].evidence_id == "fill_905"


def test_unbound_same_instrument_fills_are_visible_not_silently_claimed():
    extra = row(
        "fills_history",
        "905",
        ordId="9001",
        clOrdId="otherTrade",
        tradeId="705",
        side="buy",
        fillPx="102",
        fillSz="1",
        fee="0",
        fillTime=ms(at(30)),
        ts=ms(at(31)),
    )
    result = run(fixtures(extra_fills=(extra,)))
    assert len(result.analysis.inputs.fills) == 3
    assert json.loads(result.canonical_receipt)[
        "unattributed_same_instrument_fill_ids"
    ] == ["905"]


@pytest.mark.parametrize(
    "changes",
    [
        {"instId": "ETH-USDT-SWAP"},
        {"type": "2"},
        {"subType": "1"},
        {"balChg": ""},
        {"ccy": "bad"},
        {"ts": ""},
    ],
)
def test_funding_bill_identity_and_raw_operands_are_required(changes):
    with pytest.raises(module.RawForensicsError):
        run(fixtures(funding_changes=changes))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda path: path.update(instrument_id="ETH-USDT-SWAP"),
        lambda path: path.update(price_basis="mark"),
        lambda path: path.update(schema_version="unknown"),
        lambda path: path.update(received_at_ms=ms(at(-1))),
        lambda path: path.update(received_at_ms="999999999999999"),
        lambda path: path.update(complete="true"),
        lambda path: path["intervals"][0].__setitem__(2, "0"),
        lambda path: path["intervals"][0].__setitem__(3, "95"),
        lambda path: path["intervals"][0].__setitem__(0, ms(at(21))),
        lambda path: path["intervals"].reverse(),
        lambda path: path["intervals"][0].append("extra"),
    ],
)
def test_path_scope_geometry_order_and_receipt_are_preserved(sources, mutation):
    with pytest.raises(module.RawForensicsError):
        run(change_path(sources, mutation))


def test_missing_path_and_empty_fills_never_mean_zero_pnl_or_unfilled():
    sources = fixtures(empty=True)
    sources = change_attr(sources, lambda attr: attr.update(holding_path_sha256=None))
    sources[1]["holding_path_bytes"] = None
    result = run(sources)
    assert result.analysis.inputs.fills == () and result.analysis.inputs.path == ()
    assert result.analysis.position_status == "unknown"
    assert (
        result.analysis.actual_fill.value is None
        and result.analysis.funding.value is None
    )


def test_gap_or_fill_crossing_path_is_not_interpolated(sources):
    result = run(
        change_path(
            sources, lambda path: path["intervals"][1].__setitem__(0, ms(at(25)))
        )
    )
    assert result.analysis.inputs.path[1].started_at == at(25)
    assert result.analysis.mfe.value is None


@pytest.mark.parametrize(
    "raw",
    [
        b"{}\n",
        b'{"x":"a","x":"b"}',
        b'{"x":NaN}',
        b'{"x":true}',
        b'{"x":1}',
        b"\xff",
        b"[" * 2000,
    ],
)
def test_malformed_ambiguous_or_noncanonical_attribution_has_static_error(sources, raw):
    args = sources[1] | {
        "attribution_bytes": raw,
        "expected_attribution_sha256": sha(raw),
    }
    with pytest.raises(
        module.RawForensicsError, match="^raw_forensics_reconstruction_invalid$"
    ):
        run((sources[0], args))


def test_untrusted_python_objects_do_not_execute_callbacks(sources):
    calls = []

    class Hostile:
        def __str__(self):
            calls.append("str")
            raise AssertionError

        def __iter__(self):
            calls.append("iter")
            raise AssertionError

        def __len__(self):
            calls.append("len")
            raise AssertionError

        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError

    for name in (
        "attribution_bytes",
        "expected_attribution_sha256",
        "holding_path_bytes",
        "account_bytes",
        "expected_account_sha256",
        "expected_account_plan_sha256",
        "expected_submission_sha256",
    ):
        with pytest.raises(module.RawForensicsError):
            run((sources[0], sources[1] | {name: Hostile()}))
    assert calls == []


def test_raw_account_mutation_cannot_keep_old_pin(sources):
    args = sources[1] | {
        "account_bytes": sources[1]["account_bytes"].replace(b"100.25", b"101.25")
        + b" "
    }
    with pytest.raises(module.RawForensicsError):
        run((sources[0], args))


def test_exact_arithmetic_independent_of_low_decimal_context(sources):
    normal = run(sources)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert run(sources) == normal


def test_module_has_no_io_or_service_registration_imports():
    import inspect

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(
        name.startswith(
            ("httpx", "asyncio", "os", "pathlib", "socket", "app.core", "app.main")
        )
        for name in imported
    )


def test_reported_observed_purpose_is_not_a_verified_real_trade(sources):
    result = run(change_attr(sources, lambda attr: attr.update(purpose="observed")))
    assert result.analysis.inputs.purpose == "observed"
    assert json.loads(result.canonical_receipt)["real_trade_sample_verified"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"903": {"fillSz": "11"}},
        {"903": {"fillTime": ms(at(20))}},
        {"901": {"fillTime": ms(at(20)), "ts": ms(at(21)), "side": "sell"}},
    ],
)
def test_raw_inventory_conflicts_and_mixed_role_time_are_rejected(changes):
    with pytest.raises(module.RawForensicsError):
        run(fixtures(fill_changes=changes))


@pytest.mark.parametrize("field,stamp", [(0, ms(at(-1))), (1, ms(NOW))])
def test_path_never_extends_before_candidate_or_beyond_account_cutoff(
    sources, field, stamp
):
    altered = change_path(
        sources, lambda path: path["intervals"][0].__setitem__(field, stamp)
    )
    with pytest.raises(module.RawForensicsError):
        run(altered)


def test_funding_bill_before_submission_cannot_be_attributed():
    with pytest.raises(module.RawForensicsError):
        run(fixtures(funding_changes={"ts": ms(at(9))}))


@pytest.mark.parametrize("kind", ["attribution", "path"])
def test_raw_source_byte_budgets(sources, kind):
    if kind == "attribution":
        raw = b" " * (module.MAX_ATTRIBUTION_BYTES + 1)
        altered = (
            sources[0],
            sources[1]
            | {"attribution_bytes": raw, "expected_attribution_sha256": sha(raw)},
        )
    else:
        raw = b" " * (module.MAX_PATH_BYTES + 1)
        altered = change_attr(
            (sources[0], sources[1] | {"holding_path_bytes": raw}),
            lambda attr: attr.update(holding_path_sha256=sha(raw)),
        )
    with pytest.raises(module.RawForensicsError):
        run(altered)


@pytest.mark.parametrize("kind", ["receipt", "pin"])
def test_bad_receipt_types_do_not_enter_replay(sources, kind):
    result = run(sources)
    with pytest.raises(
        module.RawForensicsError, match="^raw_forensics_receipt_invalid$"
    ):
        module.verify_raw_forensics_receipt(
            None if kind == "receipt" else result.canonical_receipt,
            None if kind == "pin" else result.receipt_sha256,
            sources[0],
            **sources[1],
        )


def test_unattributed_funding_is_visible_and_is_not_counted(sources):
    result = run(change_attr(sources, lambda attr: attr.update(funding_bill_ids=[])))
    receipt = json.loads(result.canonical_receipt)
    assert receipt["funding_bill_claims"] == []
    assert receipt["unattributed_same_instrument_funding_bill_ids"] == ["904"]
    assert result.analysis.funding.value is None


def test_source_events_preserve_page_body_and_receipt_pins(sources):
    result = run(sources)
    receipt = json.loads(result.canonical_receipt)
    links = {item["evidence_id"]: item for item in receipt["source_links"]}
    original = capture.verify_demo_account_packet(
        sources[1]["account_bytes"],
        expected_sha256=sources[1]["expected_account_sha256"],
        expected_plan_sha256=sources[1]["expected_account_plan_sha256"],
    )
    page = next(
        item for item in original.observations if item.request.stream == "fills_history"
    )
    for event in result.analysis.inputs.fills:
        assert event.source_sha256 == page.body_sha256
        assert links[event.evidence_id]["observation_sha256"] == page.receipt_sha256
        assert event.recorded_at == page.body_completed_at


def test_rejected_or_uncertain_submission_does_not_consume_account_source(
    sources, monkeypatch
):
    for outcome in (
        None,
        submit.write_result(
            acknowledged=False,
            acknowledgement=submit.acknowledgement(order_id="", exchange_code="51000"),
        ),
    ):
        value, args = submit.inputs()
        report = post_submit.build_submission_report(
            value, **(args | {"write_result": outcome})
        )
        raw = post_submit.freeze_submission_report(report)

        def forbidden(*args, **kwargs):
            pytest.fail("account replay must not run for unconfirmed submission")

        monkeypatch.setattr(capture, "verify_demo_account_packet", forbidden)
        with pytest.raises(module.RawForensicsError):
            run((raw, sources[1] | {"expected_submission_sha256": sha(raw)}))
