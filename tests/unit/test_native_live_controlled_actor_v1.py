"""Synthetic DATA, raw replay and source-boundary checks; no venue or Arm."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.exchange.okx import native_live_close_write_v1 as physical
from app.exchange.okx import native_live_transport_v1 as transport
from app.exchange.okx.live_trade_read_page_v1 import select_live_trade_read_v1
from app.okx_live import controlled_close_capture_v1 as capture
from app.okx_live import owned_close_v1 as actor
from app.okx_live import owned_lifecycle_v1 as lifecycle
from app.okx_live import reconciliation_facts_v1 as facts
from tests import native_live_postsubmit_fixtures_v1 as raw


def _readback():
    value = raw.intent()
    rid, full, digest = exact_live_intent_document_v1(value)
    document = {
        "schema": "ctcc.native_live.intent_readback_data.v1",
        "reservation_id": rid,
        "intent_sha256": digest,
        "state": "reserved",
        "journal_sha256": "8" * 64,
        "qualification_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    return (
        SimpleNamespace(
            intent_data=value, reservation_id=rid, intent_readback=canonical(document)
        ),
        full,
        digest,
    )


def test_committed_receipt_is_verified_separately_from_exact_full_intent():
    value, full, digest = _readback()
    assert value.intent_readback != full
    observed = lifecycle._qualified_intent_receipt_v1(
        value, rid=value.reservation_id, intent_sha256=digest
    )
    assert observed == decode(value.intent_readback, maximum=4096)
    assert (
        observed["execution_authority"] is observed["qualification_verified"] is False
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "full_intent",
        "whitespace",
        "schema",
        "intent",
        "reservation",
        "state",
        "journal",
        "extra",
        "authority",
    ],
)
def test_changed_or_rewrapped_receipt_cannot_supply_intent_lineage(mutation):
    value, full, digest = _readback()
    doc = decode(value.intent_readback, maximum=4096)
    if mutation == "full_intent":
        value.intent_readback = full
    elif mutation == "whitespace":
        value.intent_readback += b" "
    else:
        key, changed = {
            "schema": ("schema", "ctcc.saved_other_receipt.v1"),
            "intent": ("intent_sha256", "0" * 64),
            "reservation": ("reservation_id", "0" * 64),
            "state": ("state", "reconciled"),
            "journal": ("journal_sha256", ""),
            "extra": ("passed", True),
            "authority": ("execution_authority", True),
        }[mutation]
        doc[key] = changed
        value.intent_readback = canonical(doc)
    with pytest.raises(lifecycle.NativeLiveLifecycleError):
        lifecycle._qualified_intent_receipt_v1(
            value, rid=value.reservation_id, intent_sha256=digest
        )


def _controlled_data(*, pending=False):
    value, args = raw.scenario()
    protected = facts.assess_live_postsubmit_facts_v1(value, **args)
    assert protected.outcome == "FULL_FILL_PROTECTED"
    original_algo = decode(args["protection_page"].raw_body, maximum=524288)["data"][0]
    original_algo.update(ordIdList=[], subAlgoIdList=[])
    original_protection = raw.rewrite(args["protection_page"], [original_algo])
    original_capture = SimpleNamespace(protection=original_protection)

    def shift_trade(page, at):
        return raw.trade(
            page.selector, decode(page.raw_body, maximum=524288)["data"], at
        )

    algo = {**original_algo, "state": "live" if pending else "canceled"}
    pages = []
    for page in args["current_pages"]:
        rows = decode(page.raw_body, maximum=524288)["data"]
        if page.selector.stream == "positions":
            rows = []
        if page.selector.stream == "algo_oco":
            rows = [algo] if pending and page.selector.after is None else []
            if not pending and page.selector.after is not None:
                continue
        pages.append(
            raw.current(
                page.selector.stream,
                rows,
                300 + (page.stamps[0].utc_ns - raw.BASE) // 1_000_000,
                after=page.selector.after,
            )
        )
    actual_capture = SimpleNamespace(
        order=shift_trade(args["order_page"], 340),
        fills=tuple(
            shift_trade(page, 350 + index * 10)
            for index, page in enumerate(args["fill_pages"])
        ),
        config_before=raw.config(330),
        config_after=raw.config(490),
        current_pages=tuple(pages),
        protection=raw.trade(args["protection_page"].selector, [algo], 480),
        tick_size=Decimal("0.01"),
    )
    row = {
        "instId": value.instrument_id,
        "ordId": "124",
        "clOrdId": "CTCCXsynthetic",
        "side": "sell",
        "posSide": "net",
        "tdMode": "isolated",
        "ordType": "fok",
        "sz": "1",
        "accFillSz": "1",
        "lever": "1",
        "reduceOnly": "true",
        "px": "99",
        "avgPx": "100.1",
        "state": "filled",
        "cTime": str(raw.BASE // 1_000_000 + 310),
        "uTime": str(raw.BASE // 1_000_000 + 315),
    }
    selector = select_live_trade_read_v1(
        "order", instrument_id=value.instrument_id, client_order_id=row["clOrdId"]
    )
    before, after = raw.trade(selector, [row], 325), raw.trade(selector, [row], 495)
    fill = {
        "instId": value.instrument_id,
        "ordId": "124",
        "clOrdId": row["clOrdId"],
        "side": "sell",
        "posSide": "net",
        "billId": "790",
        "tradeId": "11",
        "fillSz": "1",
        "fillPx": "100.1",
        "fee": "-0.05",
        "feeCcy": "USDT",
        "fillTime": str(raw.BASE // 1_000_000 + 312),
        "ts": str(raw.BASE // 1_000_000 + 313),
    }
    fills = (
        raw.trade(
            select_live_trade_read_v1(
                "fills", instrument_id=value.instrument_id, order_id="124"
            ),
            [fill],
            326,
        ),
        raw.trade(
            select_live_trade_read_v1(
                "fills",
                instrument_id=value.instrument_id,
                order_id="124",
                after_bill_id="790",
            ),
            [],
            328,
        ),
    )
    state = {
        "intent": value,
        "protected_facts": protected,
        "protected_capture": original_capture,
        "close_client_order_id": row["clOrdId"],
        "close_deadline": utc_from_ns(raw.stamp(5000).utc_ns),
    }
    return (
        state,
        actual_capture,
        before,
        after,
        fills,
        {
            "request_attempted": True,
            "started_at": raw.stamp(300).model_dump(),
            "raw_body": canonical(
                {
                    "code": "0",
                    "data": [
                        {"sCode": "0", "ordId": "124", "clOrdId": "CTCCXsynthetic"}
                    ],
                }
            ),
        },
    )


@pytest.mark.parametrize("pending", [False, True])
def test_actual_raw_fill_and_complete_global_flat_replay_distinguishes_original_pending_algo(
    pending,
):
    state, current, before, after, fills, response = _controlled_data(pending=pending)
    result = capture._assess(
        state,
        current,
        before,
        after,
        fills,
        response,
        barrier=raw.stamp(320),
        observed=raw.stamp(500),
        allow_original_algo=pending,
    )
    assert result == ("124", Decimal("100.1"), Decimal("-0.05"), pending)
    # This pure tuple authenticates no source, signer, SQL resolution or POST.
    assert before.synthetic_mock is after.synthetic_mock is True


@pytest.mark.parametrize(
    "mutation",
    [
        "no_terminal",
        "partial",
        "wrong_order",
        "wrong_client",
        "old_time",
        "outside_deadline",
        "nonreduce",
        "wrong_lever",
        "adverse",
        "pending_order",
        "unknown_position",
        "algo_child",
        "changed_sl",
        "future",
        "stale",
        "changed_after",
    ],
)
def test_raw_unknown_partial_foreign_or_stale_close_never_becomes_flat(mutation):
    state, current, before, after, fills, response = _controlled_data()
    row = decode(before.raw_body, maximum=524288)["data"][0]
    if mutation == "no_terminal":
        fills = fills[:1]
    elif mutation in {
        "partial",
        "wrong_order",
        "wrong_client",
        "old_time",
        "outside_deadline",
        "nonreduce",
        "wrong_lever",
        "adverse",
        "future",
        "changed_after",
    }:
        key, value = {
            "partial": ("accFillSz", "0.5"),
            "wrong_order": ("ordId", "123"),
            "wrong_client": ("clOrdId", "CTCCXforeign"),
            "old_time": ("cTime", str(raw.BASE // 1_000_000 + 299)),
            "outside_deadline": ("cTime", str(raw.BASE // 1_000_000 + 6000)),
            "nonreduce": ("reduceOnly", "false"),
            "wrong_lever": ("lever", "3"),
            "adverse": ("avgPx", "98"),
            "future": ("uTime", str(raw.BASE // 1_000_000 + 9999)),
            "changed_after": ("avgPx", "100.2"),
        }[mutation]
        row[key] = value
        after = raw.rewrite(after, [row])
        if mutation != "changed_after":
            before = raw.rewrite(before, [row])
    elif mutation == "pending_order":
        current.current_pages = tuple(
            raw.rewrite(page, [{"ordId": "999"}])
            if page.selector.stream == "orders_pending"
            else page
            for page in current.current_pages
        )
    elif mutation == "unknown_position":
        current.current_pages = tuple(
            raw.rewrite(page, [{"instId": "BTC-USDT-SWAP", "pos": "1"}])
            if page.selector.stream == "positions"
            else page
            for page in current.current_pages
        )
    elif mutation in {"algo_child", "changed_sl"}:
        algo = decode(current.protection.raw_body, maximum=524288)["data"][0]
        algo["ordIdList" if mutation == "algo_child" else "slTriggerPx"] = (
            ["125"] if mutation == "algo_child" else "98"
        )
        current.protection = raw.rewrite(current.protection, [algo])
    observed = raw.stamp(6000 if mutation == "stale" else 500)
    with pytest.raises((ValueError, KeyError)):
        capture._assess(
            state,
            current,
            before,
            after,
            fills,
            response,
            barrier=raw.stamp(320),
            observed=observed,
            allow_original_algo=False,
        )


@pytest.mark.asyncio
async def test_closed_owner_and_transport_families_refuse_constructor_and_saved_data():
    actor._NATIVE_GUARD()
    for candidate in (
        {"passed": True},
        object(),
        object.__new__(actor.OwnedLiveControlledCloseV1),
    ):
        with pytest.raises(actor.LiveControlledCloseError):
            actor._record(candidate)
        with pytest.raises((ValueError, TypeError)):
            await physical.submit_owned_live_controlled_action_v1(candidate)
    with pytest.raises(ValueError):
        physical._PhysicalLiveCloseGateV1()
    invalid = object.__new__(physical._PhysicalLiveCloseGateV1)
    with pytest.raises(ValueError):
        transport._gate(invalid)
    assert not actor._OWNED and not physical._GATES


@pytest.mark.asyncio
async def test_in_place_source_or_catalog_mutation_cannot_open_closed_actor(
    monkeypatch,
):
    original = actor._intent_document.__code__

    def changed(state):
        return b'{"passed":true}'

    monkeypatch.setattr(actor._intent_document, "__code__", changed.__code__)
    with pytest.raises(ValueError):
        actor._NATIVE_GUARD()
    monkeypatch.setattr(actor._intent_document, "__code__", original)
    actor._NATIVE_GUARD()


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"revision": 9},
        {"emergency_stop": True},
        {"revision": 10, "emergency_stop": False},
        {"armed": True},
        {"process_sha256": "0" * 64},
    ],
)
def test_close_control_accepts_only_exact_one_step_successor_and_never_stop_clear_adoption(
    changes,
):
    value = raw.intent()
    now = utc_from_ns(raw.stamp(500).utc_ns)
    state = {"intent": value, "disarm_successor": {"after_revision": 8}}
    control = {
        "environment": "live",
        "account_uid": value.account_uid,
        "main_uid": value.main_uid,
        "credential_session_sha256": value.credential_session_sha256,
        "process_sha256": value.process_sha256,
        "policy_sha256": value.policy_sha256,
        "revision": 8,
        "armed": False,
        "emergency_stop": False,
        "arm_id": None,
        "arm_expires_at": None,
        "lease_until": now + timedelta(seconds=10),
        **changes,
    }
    if changes:
        with pytest.raises(actor.LiveControlledCloseError):
            actor._validate_control(state, control, now)
    else:
        actor._validate_control(state, control, now)


@pytest.mark.parametrize(
    "mutation", ["missing", "foreign", "entry_id", "non_numeric", "wrong_client"]
)
def test_ack_identity_must_bind_the_exact_fresh_order_and_fill_chain(mutation):
    state, current, before, after, fills, response = _controlled_data()
    row = {"sCode": "0", "ordId": "124", "clOrdId": "CTCCXsynthetic"}
    if mutation == "missing":
        row.pop("ordId")
    elif mutation == "foreign":
        row["ordId"] = "999"
    elif mutation == "entry_id":
        row["ordId"] = "123"
    elif mutation == "non_numeric":
        row["ordId"] = "ambiguous"
    else:
        row["clOrdId"] = "CTCCXforeign"
    response["raw_body"] = canonical({"code": "0", "data": [row]})
    with pytest.raises(ValueError):
        capture._assess(
            state,
            current,
            before,
            after,
            fills,
            response,
            barrier=raw.stamp(320),
            observed=raw.stamp(500),
            allow_original_algo=False,
        )


@pytest.mark.parametrize("identity", ["tradeId", "billId"])
def test_same_instrument_cross_order_identity_conflict_is_not_deduplicated(identity):
    state, current, before, after, fills, response = _controlled_data()
    row = decode(fills[0].raw_body, maximum=524288)["data"][0]
    original = decode(current.fills[0].raw_body, maximum=524288)["data"][0]
    row[identity] = original[identity]
    first = raw.rewrite(fills[0], [row])
    # Keep the terminal cursor coherent so the cross-order conflict itself is
    # tested, rather than an unrelated cursor mismatch.
    terminal = raw.trade(
        select_live_trade_read_v1(
            "fills",
            instrument_id=state["intent"].instrument_id,
            order_id="124",
            after_bill_id=row["billId"],
        ),
        [],
        328,
    )
    with pytest.raises(ValueError):
        capture._assess(
            state,
            current,
            before,
            after,
            (first, terminal),
            response,
            barrier=raw.stamp(320),
            observed=raw.stamp(500),
            allow_original_algo=False,
        )


@pytest.mark.parametrize(
    "module,name",
    [
        (lifecycle, "_record"),
        (actor._qualification, "_replay_spent_owned_live_close_permission_v1"),
    ],
)
def test_changed_owner_callback_cannot_bypass_its_own_inner_guard(
    monkeypatch, module, name
):
    function = getattr(module, name)
    original = function.__code__

    def forged(*args, **kwargs):
        return {"passed": True}

    monkeypatch.setattr(function, "__code__", forged.__code__)
    with pytest.raises(ValueError):
        actor._NATIVE_GUARD()
    monkeypatch.setattr(function, "__code__", original)
    actor._NATIVE_GUARD()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["dns_return", "tls_return", "ssl_retry"])
async def test_cancellation_at_last_await_boundary_refuses_before_any_native_write(
    boundary,
):
    writes = []

    async def worker():
        async def final_await():
            await asyncio.sleep(0)
            # Simulate delivery as the DNS/TLS/WantRead await completes.
            asyncio.current_task().cancel(boundary)

        await final_await()
        try:
            with pytest.raises(actor.LiveControlledCloseError, match="cancelled"):
                actor._source_checks({})
            assert not writes
        finally:
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        return "denied"

    assert await asyncio.create_task(worker()) == "denied"
    assert writes == []


def test_signed_cancel_claim_carries_original_close_client_identity_into_custody_parser():
    from app.database.repositories import native_live_controlled_close_v1 as repository
    from app.domain.source_primitives import ClockStamp
    from tests.unit.native_live_close_fixture_v1 import (
        METADATA,
        claim_document,
        intent_document,
    )

    parent = intent_document()
    expected = claim_document(parent, action="cancel_original_protection")
    state = {
        "intent_json": canonical(parent),
        "action": "cancel_original_protection",
        "claim_observation": ClockStamp.model_validate(
            expected["observation"], strict=True
        ),
        "request_json": expected["request_json"].encode("ascii"),
        "request_wire_sha256": expected["request_wire_sha256"],
        "request_deadline": repository._time(expected["request_deadline"]),
        "request_deadline_monotonic_ns": expected["request_deadline_monotonic_ns"],
        "action_raw_custody_sha256": expected["raw_custody_sha256"],
        "cancel_verified_facts": {
            name: expected[name]
            for name in (
                "full_account_verified",
                "fills_complete",
                "flat_verified",
                "unknown_exposure",
                "position_count",
                "pending_order_count",
                "pending_algo_count",
                "close_claim_sha256",
                "close_order_id",
                "close_client_order_id",
                "entry_order_id",
                "close_order_state",
                "close_filled_contracts",
                "protection_algo_id",
                "protection_client_id",
                "account_config_before_sha256",
                "account_config_after_sha256",
            )
        },
    }
    actual = actor._claim_document(state)
    parsed, _, _ = repository._parse_data(actual, metadata=METADATA, claim=True)
    assert parsed["close_client_order_id"] == parent["close_client_order_id"]
    assert parsed["execution_authority"] is False


def test_close_history_keeps_measured_original_post_utc_without_replaying_old_monotonic_clock():
    from tests.unit.test_native_live_recovery_history_v1 import history_scenario

    value, data = history_scenario()
    original = {"submission_started": raw.stamp(0)}
    journal = SimpleNamespace(intent_recorded_at=utc_from_ns(raw.stamp(-1).utc_ns))
    history = actor._close_submission_history_v1(original, journal)
    assert history.submission_started_utc == utc_from_ns(raw.stamp(0).utc_ns)
    assert history.intent_recorded_at == journal.intent_recorded_at
    data["history"] = history
    assert data["submission_started"] is None
    observed = facts.assess_live_postsubmit_facts_v1(value, **data)
    assert observed.outcome == "FULL_FILL_PROTECTED"
    assert (
        observed.execution_authority
        is observed.economic_qualification_verified
        is False
    )
    data["submission_started"] = raw.stamp(0)
    assert facts.assess_live_postsubmit_facts_v1(value, **data).outcome == "UNCERTAIN"


@pytest.mark.parametrize("started,recorded", [(None, 0), ({}, 0), (raw.stamp(0), 1)])
def test_close_history_cannot_invent_a_post_start_or_precede_committed_intent(
    started, recorded
):
    with pytest.raises(actor.LiveControlledCloseError):
        actor._close_submission_history_v1(
            {"submission_started": started},
            SimpleNamespace(intent_recorded_at=utc_from_ns(raw.stamp(recorded).utc_ns)),
        )
