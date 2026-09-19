"""Documented all-product queries over synthetic bytes; no account or order IO."""

import hashlib
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_materializer as mapping
from tests.unit import test_qualification_account_capture as old
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_runtime import regional, setup

PRODUCTS = ("SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION", "EVENTS")
FAMILIES = ("fills_history", "orders_history_recent", "orders_history_archive")
QUERIES = tuple(
    (f"{family}_{kind.lower()}", family, kind)
    for family in FAMILIES
    for kind in PRODUCTS
)
EXPECTED_STREAMS = tuple(
    name
    for stream in old.STREAMS
    for name in (
        tuple(f"{stream}_{kind.lower()}" for kind in PRODUCTS)
        if stream in FAMILIES
        else (stream,)
    )
)


def plan(**changes):
    values = capture._plain(regional())
    values.update(
        contract_version="ctcc.demo_account_plan.v4",
        capture_scope="all_standard_products_v4_and_current_algos",
        **changes,
    )
    return capture.AllProductDemoAccountCapturePlan(**values)


def product_row(family, kind, **changes):
    symbols = {
        "SPOT": "BTC-USDT",
        "MARGIN": "ETH-USDT",
        "SWAP": old.INSTRUMENT,
        "FUTURES": "BTC-USDT-261225",
        "OPTION": "BTC-USD-261225-100000-C",
        "EVENTS": "SYNTHETIC-YES-EVENT",
    }
    return old.row(
        family,
        identifier=str(9100 + PRODUCTS.index(kind)),
        instType=kind,
        instId=symbols[kind],
        posSide="net" if kind in {"SWAP", "FUTURES"} else "",
        **changes,
    )


def script(*, pages=None, source=None):
    pages = {} if pages is None else pages
    source = {} if source is None else source
    variants = {name: (family, kind) for name, family, kind in QUERIES}
    result = []
    for stream in EXPECTED_STREAMS:
        if stream in pages:
            selected = pages[stream]
        elif stream in variants:
            family, kind = variants[stream]
            if source:
                selected = source.get(family, [[]]) if kind == "SWAP" else [[]]
            else:
                selected = [[product_row(family, kind)], []]
        elif stream in source:
            selected = source[stream]
        elif stream in old.CURSORS:
            selected = [[old.row(stream)], []]
        else:
            selected = [[old.row(stream)]]
        result.extend((stream, rows) for rows in selected)
    return result


def records(*, selected=None, pages=None, source=None):
    selected = plan() if selected is None else selected
    items = []
    identity = None
    previous_stream = None
    for stream, rows in script(pages=pages, source=source):
        if stream != previous_stream:
            index, after, previous = 0, None, None
        at = old.NOW + timedelta(milliseconds=10 + 3 * len(items))
        item = old.observe(
            old.wire(rows),
            selected=selected,
            stream=stream,
            page_index=index,
            after=after,
            previous_page_sha256=previous,
            identity_receipt_sha256=identity,
            request_started_at=at,
            headers_received_at=at + timedelta(milliseconds=1),
            body_completed_at=at + timedelta(milliseconds=2),
        )
        items.append(item)
        if stream == "config_before":
            identity = item.receipt_sha256
        index += 1
        previous = item.receipt_sha256
        family = (
            stream.rsplit("_", 1)[0] if stream in {q[0] for q in QUERIES} else stream
        )
        if rows and family in old.CURSORS:
            after = rows[-1][old.CURSORS[family]]
        previous_stream = stream
    return selected, tuple(items)


def verify(**kwargs):
    return old.verify(*records(**kwargs))


def test_v4_exact_inventory_replay_retains_all_types_without_authority():
    packet = verify()
    assert (
        tuple(dict.fromkeys(p.request.stream for p in packet.observations))
        == EXPECTED_STREAMS
    )
    assert len(EXPECTED_STREAMS) == 38
    assert packet.schema_version == "ctcc.demo_account_capture.v4"
    assert type(packet.plan) is capture.AllProductDemoAccountCapturePlan
    assert "non_swap_history_not_requested" not in packet.incomplete_reasons
    assert {
        "advanced_product_scope_unverified",
        "all_product_metadata_coverage_unverified",
        "history_ingestion_watermark_unverified",
        "history_retention_unverified",
        "history_seed_missing",
        "peak_window_evidence_missing",
        "source_authenticity_unverified",
    } <= set(packet.incomplete_reasons)
    assert not packet.account_complete and not packet.execution_authority
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    assert (
        capture.verify_demo_account_packet(
            frozen.payload,
            expected_sha256=frozen.sha256,
            expected_plan_sha256=packet.plan_sha256,
        )
        == packet
    )
    assert mapping._records(packet)["fills_history"]
    assert {
        row[1]["instType"] for row in mapping._records(packet)["fills_history"]
    } == set(PRODUCTS)


@pytest.mark.parametrize("stream,family,kind", QUERIES)
def test_required_product_query_uses_documented_endpoint_and_cursor(
    stream, family, kind
):
    request = capture.account_request(plan(), stream, after="8888")
    params = dict(request.parameters)
    endpoints = {
        "fills_history": "/api/v5/trade/fills-history",
        "orders_history_recent": "/api/v5/trade/orders-history",
        "orders_history_archive": "/api/v5/trade/orders-history-archive",
    }
    assert request.endpoint == endpoints[family]
    assert params == {
        "instType": kind,
        "after": "8888",
        "limit": "100",
        "begin": old.ms(plan().history_start),
        "end": old.ms(plan().history_end),
    }
    assert request.origin == "https://openapi.okx.com"


@pytest.mark.parametrize(
    "stream",
    (
        "fills_recent",
        "positions",
        "orders_pending",
        "bills_recent",
        "bills_archive",
        *(f"algo_{kind}" for kind in capture.ALGO_ORDER_TYPES),
    ),
)
def test_current_inventory_and_recent_fills_are_not_filtered_to_swap(stream):
    params = dict(capture.account_request(plan(), stream).parameters)
    assert not ({"instType", "instId", "instFamily", "state"} & params.keys())


@pytest.mark.parametrize("stream,family,kind", QUERIES)
def test_any_missing_product_chain_is_incomplete(stream, family, kind):
    with pytest.raises(
        capture.AccountCaptureError, match="inventory_stream_missing_or_limit"
    ):
        verify(pages={stream: []})


@pytest.mark.parametrize("stream,family,kind", QUERIES)
def test_short_nonempty_page_does_not_prove_terminal(stream, family, kind):
    with pytest.raises(
        capture.AccountCaptureError, match="empty_terminal_page_required"
    ):
        verify(pages={stream: [[product_row(family, kind)]]})


@pytest.mark.parametrize("stream,family,kind", QUERIES)
def test_cross_product_response_is_not_relabelled(stream, family, kind):
    other = "SWAP" if kind != "SWAP" else "SPOT"
    with pytest.raises(
        capture.AccountCaptureError, match="history_instrument_scope_mismatch"
    ):
        verify(pages={stream: [[product_row(family, other)], []]})


def test_fills_history_cursor_uses_bill_id_not_trade_id_or_order_id():
    packet = verify()
    for item in packet.observations:
        if item.request.stream.startswith("fills_history_") and item.page_index == 1:
            assert item.after not in {"700", "800"}
            assert dict(item.request.parameters)["after"] == item.after


def test_cross_product_duplicate_identity_is_rejected():
    wrong = product_row("fills_history", "MARGIN", billId="9100")
    with pytest.raises(
        capture.AccountCaptureError, match="conflicting_history_product_identity"
    ):
        verify(pages={"fills_history_margin": [[wrong], []]})


@pytest.mark.parametrize("unit", ("quote_ccy", "base_ccy", "", None, "invented"))
def test_spot_market_quantity_units_are_preserved_not_compared_blindly(unit):
    value = product_row(
        "orders_history_recent",
        "SPOT",
        ordType="market",
        sz="1",
        accFillSz="20",
        tgtCcy=unit,
    )
    if unit is None:
        del value["tgtCcy"]
    if unit in {"base_ccy", "invented"}:
        with pytest.raises(
            capture.AccountCaptureError,
            match="filled_quantity_exceeds_order|source_quantity_unit_invalid",
        ):
            verify(pages={"orders_history_recent_spot": [[value], []]})
    else:
        packet = verify(pages={"orders_history_recent_spot": [[value], []]})
        record = next(
            o.rows[0]
            for o in packet.observations
            if o.request.stream == "orders_history_recent_spot" and o.rows
        )
        assert json.loads(record.canonical_json)["accFillSz"] == "20"
        assert ("tgtCcy" in record.missing_fields) == (unit in {"", None})


def test_new_plan_requires_explicit_version_and_sufficient_inventory_budget():
    values = capture._plain(plan())
    del values["contract_version"]
    with pytest.raises(ValidationError):
        capture.AllProductDemoAccountCapturePlan(**values)
    with pytest.raises(ValueError, match="plan_inventory_budget_invalid"):
        plan(max_total_pages=37)
    with pytest.raises(capture.AccountCaptureError, match="stream_invalid"):
        capture.account_request(regional(), "fills_history_spot")
    with pytest.raises(capture.AccountCaptureError, match="stream_invalid"):
        capture.account_request(plan(), "fills_history")


@pytest.mark.parametrize(
    "selected,expected",
    [
        (None, "3e1f8908c1ef2418abad4d94ca3c49eb01272eb32244b3b49fbdeb8cda74488d"),
        (
            "regional",
            "ecfd11ede9a8a86996f610434b6956d0c594374e6c1ac24e468879ab02ecd7bc",
        ),
    ],
)
def test_legacy_packet_bytes_match_ecda5214_checkpoint(selected, expected):
    # Independently checked against git-show ecda5214 account_capture.py.
    selected = regional() if selected else None
    packet = old.verify(*old.records(selected=selected))
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    assert hashlib.sha256(frozen.payload).hexdigest() == expected


@pytest.mark.asyncio
async def test_owned_v4_collector_binds_actual_requested_queries_and_empty_terminals(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    harness.script = script()
    selected = plan()
    packet = await collector.collect_demo_account_records(
        credentials=credentials(),
        clock=harness.clock,
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
        barrier_completed_at=old.BARRIER,
    )
    assert len(harness.requests) == len(packet.observations)
    for request, observed in zip(harness.requests, packet.observations, strict=True):
        assert request.method == "GET"
        assert request.headers["x-simulated-trading"] == "1"
        assert request.url.host == "openapi.okx.com"
        assert tuple(request.url.params.multi_items()) == observed.request.parameters
        assert observed.request_started_at > old.BARRIER
    assert all(o.terminal for o in packet.observations if o.page_index == 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PRODUCTS)
async def test_runtime_preserves_non_swap_history_and_denies_unmapped_economics(
    monkeypatch, kind
):
    selected = plan()
    session, harness, observed, arguments = setup(monkeypatch, selected=selected)
    pages = {}
    if kind != "SWAP":
        pages[f"fills_history_{kind.lower()}"] = [
            [product_row("fills_history", kind)],
            [],
        ]
    harness.script = script(source=source_pages(), pages=pages)
    result = await session.collect_and_materialize(**arguments)
    assert result.packet.schema_version == "ctcc.demo_account_capture.v4"
    assert result.admission == "DENY" and not result.execution_authority
    assert len(observed) == 2
    assert result.transport_provenance == "synthetic_transport"
    if kind != "SWAP":
        assert "history_product_mapping_unsupported" in result.blocking_reasons
        assert result.materialization.snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ("positions", "orders_pending", "algo_trigger"))
async def test_non_swap_current_exposure_is_retained_with_unknown_risk(
    monkeypatch, stream
):
    selected = plan()
    session, harness, _, arguments = setup(monkeypatch, selected=selected)
    value = old.row(
        stream,
        identifier="9876",
        instType="MARGIN",
        instId="ETH-USDT",
        posSide="net" if stream == "positions" else "",
    )
    pages = {stream: [[value]] if stream == "positions" else [[value], []]}
    harness.script = script(source=source_pages(), pages=pages)
    result = await session.collect_and_materialize(**arguments)
    projected = next(
        p for p in result.materialization.projections if p.row_id == "9876"
    )
    assert projected.stream == stream and projected.reasons
    assert projected.contracts is None
    assert projected.risk_amount is None and projected.margin is None
    assert result.materialization.snapshot is None and result.admission == "DENY"


@pytest.mark.parametrize(
    "stream,kind,side",
    (
        ("positions", "SPOT", "net"),
        ("positions", "MARGIN", ""),
        ("algo_chase", "SPOT", ""),
    ),
)
def test_product_specific_inventory_semantics_reject_malformed_source(
    stream, kind, side
):
    value = old.row(stream, instType=kind, posSide=side)
    with pytest.raises(
        capture.AccountCaptureError,
        match="source_instrument_type_invalid|position_side_invalid",
    ):
        verify(pages={stream: [[value]] if stream == "positions" else [[value], []]})


@pytest.mark.asyncio
async def test_unavailable_required_product_aborts_without_empty_substitution_or_retry(
    monkeypatch,
):
    def reject(stream, _, rows):
        if stream == "fills_history_events":
            return old.wire([], code="51000", msg="synthetic unsupported product")
        return None

    harness = Harness(monkeypatch, change=reject)
    harness.script = script()
    selected = plan()
    with pytest.raises(collector.AccountCollectionError):
        await collector.collect_demo_account_records(
            credentials=credentials(),
            clock=harness.clock,
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
            barrier_completed_at=old.BARRIER,
        )
    assert dict(harness.requests[-1].url.params)["instType"] == "EVENTS"
    assert (
        len(harness.requests)
        == next(
            i
            for i, (stream, _) in enumerate(harness.script)
            if stream == "fills_history_events"
        )
        + 1
    )


@pytest.mark.parametrize("mutate", ("plan_scope", "packet_version", "cached_plan"))
def test_replayed_or_resigned_old_scope_cannot_gain_v4_coverage(mutate):
    packet = verify()
    if mutate == "plan_scope":
        packet = packet.model_copy(
            update={
                "plan": packet.plan.model_copy(
                    update={"capture_scope": "all_current_algos_v2_and_swap_history"}
                )
            }
        )
    elif mutate == "packet_version":
        packet = packet.model_copy(
            update={"schema_version": "ctcc.demo_account_capture.v3"}
        )
    else:
        packet = packet.model_copy(update={"plan": regional()})
    with pytest.raises(capture.AccountCaptureError):
        capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=packet.plan_sha256
        )
