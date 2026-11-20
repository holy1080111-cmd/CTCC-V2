"""Synthetic explicit Live DATA only; no venue, signing owner or SQL authority."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import native_live_controlled_close_v1 as repository
from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.exchange.okx.live_account_config_observation_v1 import _header_digest
from app.exchange.okx.live_account_history_page_v1 import (
    LiveAccountHistoryPageV1,
    live_account_history_page_receipt_document_v1,
    select_live_account_history_page_v1,
)
from app.exchange.okx.live_trade_read_page_v1 import select_live_trade_read_v1
from app.okx_live import controlled_close_recovery_v1 as subject
from tests import native_live_postsubmit_fixtures_v1 as raw
from tests.unit.native_live_close_fixture_v1 import claim_document, intent_document
from tests.unit.test_native_live_original_oco_recovery_v1 import (
    original_bindings,
)
from tests.unit.test_native_live_original_oco_recovery_v1 import (
    scenario as oco_scenario,
)


def history(rows, at, *, begin, end, after=None):
    selected = select_live_account_history_page_v1(
        "orders_archive",
        begin_ms=str(begin),
        end_ms=str(end),
        inst_type="SWAP",
        after=after,
    )
    common = raw._common(rows, at)
    common["headers_sha256"] = _header_digest(common["raw_headers"])
    page = LiveAccountHistoryPageV1(
        selector=selected,
        row_ids=tuple(row["ordId"] for row in rows),
        row_count=len(rows),
        empty_page=not rows,
        **common,
    )
    return replace(
        page,
        receipt_sha256=sha(
            canonical(live_account_history_page_receipt_document_v1(page))
        ),
    )


def scenario(
    *,
    direction="long",
    shift=0,
    cancel=True,
    original_intent=None,
    retained_records=None,
    retained_journal=None,
    history_override=None,
):
    intent, original, original_args = oco_scenario(
        direction=direction, original_intent=original_intent
    )
    direction = intent.direction
    rid, _, digest = exact_live_intent_document_v1(intent)
    proof = original_bindings().proofs[0]
    journal = SimpleNamespace(
        reservation_id=rid, intent_sha256=digest, intent=intent, proofs=(proof,)
    )
    document = intent_document(
        observation=raw.stamp(100).model_dump(),
        reservation_id=rid,
        original_intent_sha256=digest,
        candidate_sha256=intent.candidate_sha256,
        report_sha256=intent.report_sha256,
        policy_sha256=intent.policy_sha256,
        original_credential_session_sha256=intent.credential_session_sha256,
        original_process_sha256=intent.process_sha256,
        entry_order_id="123",
        protection_algo_id="456",
        protected_proof_sha256=sha(proof),
    )
    request = decode(document["request_json"].encode())
    opposite = "sell" if direction == "long" else "buy"
    request.update(side=opposite, px=str(intent.stop_loss))
    request = canonical(request)
    document.update(request_json=request.decode(), request_sha256=sha(request))
    parent_raw = canonical(document)
    parent = repository.LiveControlledCloseIntentDataV1(
        document["close_intent_id"],
        rid,
        parent_raw,
        sha(parent_raw),
        "a" * 64,
        request,
        sha(request),
        datetime.fromisoformat(document["close_deadline"]),
        utc_from_ns(raw.stamp(101).utc_ns),
    )
    claim = claim_document(document)
    claim_raw = canonical(claim)
    close = repository.LiveControlledCloseClaimDataV1(
        parent,
        "close",
        claim_raw,
        sha(claim_raw),
        "b" * 64,
        request,
        sha(request),
        claim["request_wire_sha256"],
        parent.deadline,
        utc_from_ns(raw.stamp(102).utc_ns),
    )
    cleanup = None
    if cancel:
        cancel_doc = claim_document(
            document,
            action="cancel_original_protection",
            observation=raw.stamp(155).model_dump(),
            close_order_id="124",
        )
        cancel_raw = canonical(cancel_doc)
        cancel_request = cancel_doc["request_json"].encode()
        cleanup = repository.LiveControlledCloseClaimDataV1(
            parent,
            "cancel_original_protection",
            cancel_raw,
            sha(cancel_raw),
            "c" * 64,
            cancel_request,
            sha(cancel_request),
            cancel_doc["request_wire_sha256"],
            datetime.fromisoformat(cancel_doc["request_deadline"]),
            utc_from_ns(raw.stamp(156).utc_ns),
        )
    records = (
        subject.RetainedControlledCloseV1(parent, close, cleanup)
        if retained_records is None
        else retained_records
    )
    journal = journal if retained_journal is None else retained_journal
    document = decode(records.intent.document_json)
    entry_id, algo_id = document["entry_order_id"], document["protection_algo_id"]
    close_id = str(int(entry_id) + 1)
    close_ms = max(
        raw.BASE // 1_000_000 + 110, subject._ms(records.close.recorded_at) + 1
    )
    cancel_ms = (
        close_ms + 48
        if records.cancel is None
        else max(close_ms + 48, subject._ms(records.cancel.recorded_at))
    )
    shift = max(shift, cancel_ms - raw.BASE // 1_000_000 + 2 - 160)
    entry = decode(original.order.raw_body)["data"][0]
    entry["ordId"] = entry_id
    entry_fill = decode(original.fills[0].raw_body)["data"][0]
    entry_fill["ordId"] = entry_id
    exit_row = decode(original.exit_order.raw_body)["data"][0]
    exit_row.update(
        ordId=close_id,
        clOrdId=document["close_client_order_id"],
        ordType="fok",
        px=str(intent.stop_loss),
        avgPx=str(intent.stop_loss),
        algoId="",
        algoClOrdId="",
        cTime=str(close_ms),
        uTime=str(close_ms + 40),
    )
    exit_fill = decode(original.exit_fills[0].raw_body)["data"][0]
    exit_fill.update(
        ordId=close_id,
        clOrdId=document["close_client_order_id"],
        fillPx=str(intent.stop_loss),
        fillTime=str(close_ms + 20),
        ts=str(close_ms + 21),
    )
    algo = decode(original.protection.raw_body)["data"][0]
    algo.update(
        algoId=algo_id,
        state="canceled",
        actualSide="",
        actualSz="0",
        actualPx="",
        triggerTime="",
        ordId="",
        ordIdList=[],
        subAlgoIdList=[],
        uTime=str(cancel_ms),
    )

    def trade(kind, rows, at, **values):
        return raw.trade(
            select_live_trade_read_v1(
                kind, instrument_id=intent.instrument_id, **values
            ),
            rows,
            at + shift + 500,
        )

    cutoff = raw.stamp(160 + shift)
    begin, end = subject._ms(records.close.recorded_at), cutoff.utc_ns // 1_000_000 - 1
    archive = (
        history([exit_row], 170 + shift, begin=begin, end=end),
        history([], 680 + shift, begin=begin, end=end, after=close_id),
    )
    current = [
        raw.current("positions", [], 810 + shift),
        raw.current("orders_pending", [], 820 + shift),
    ]
    current.extend(
        raw.current("algo_" + kind, [], 830 + shift + i * 10)
        for i, kind in enumerate(raw.ALGO_TYPES)
    )
    capture = subject.ControlledCloseRecoveryCaptureV1(
        trade("order", [entry], 200, order_id=entry_id),
        (
            trade("fills_history", [entry_fill], 210, order_id=entry_id),
            trade("fills_history", [], 220, order_id=entry_id, after_bill_id="789"),
        ),
        trade("algo", [algo], 230, algo_id=algo_id),
        raw.config(800 + shift),
        raw.config(930 + shift),
        tuple(current),
        "c" * 64,
        "d" * 64,
        raw.stamp(950 + shift),
        original.tick_size,
        original.contract_value,
        original.contract_multiplier,
        original.contract_value_currency,
        entry_order_after=trade("order", [entry], 270, order_id=entry_id),
        close_order=trade("order", [exit_row], 240, order_id=close_id),
        close_order_after=trade("order", [exit_row], 280, order_id=close_id),
        close_fills=(
            trade("fills_history", [exit_fill], 250, order_id=close_id),
            trade("fills_history", [], 260, order_id=close_id, after_bill_id="790"),
        ),
        protection_after=trade("algo", [algo], 290, algo_id=algo_id),
        archive_pages=archive,
        archive_config_before=raw.config(161 + shift),
        archive_config_after=raw.config(690 + shift),
        archive_custody_sha256="e" * 64,
        archive_attempt_id="8" * 32,
        archive_cutoff=cutoff,
    )
    return (
        intent,
        capture,
        {
            "history": original_args["history"]
            if history_override is None
            else history_override,
            "barrier": raw.stamp(159 + shift),
            "records": records,
            "journal": journal,
        },
    )


def reraw(page, rows):
    if type(page) is LiveAccountHistoryPageV1:
        return history(
            rows,
            (page.stamps[0].utc_ns - raw.BASE) // 1_000_000,
            begin=page.selector.begin_ms,
            end=page.selector.end_ms,
            after=page.selector.after,
        )
    return raw.trade(
        page.selector, rows, (page.stamps[0].utc_ns - raw.BASE) // 1_000_000
    )


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("shift", [0, 180_000])
@pytest.mark.parametrize("cancel", [False, True])
def test_exact_retained_close_and_fresh_flat_are_data_even_after_old_consent_expired(
    direction, shift, cancel
):
    intent, capture, args = scenario(direction=direction, shift=shift, cancel=cancel)
    result = subject.assess_controlled_close_recovery_v1(intent, capture, **args)
    assert result.outcome == "RECONCILED_FLAT" and result.codes == ()
    assert (
        result.exit_kind == "controlled_micro_close" and result.exit_order_id == "124"
    )
    assert result.exit_client_order_id == "CTCCXsynthetic"
    assert result.controlled_close_claim_sha256 == args["records"].close.document_sha256
    assert result.controlled_cancel_claim_sha256 == (
        args["records"].cancel.document_sha256 if cancel else None
    )
    assert (
        result.execution_authority
        is result.resolution_authority
        is result.source_authenticity_verified
        is False
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing_terminal",
        "later_client_reuse",
        "duplicate_identity",
        "wrong_cursor",
        "wrong_window",
        "partial_close",
        "wrong_side",
        "wrong_price",
        "wrong_margin",
        "wrong_leverage",
        "foreign_fills",
        "changed_order_after",
        "changed_entry_after",
        "active_protection",
        "effective_oco",
        "wrong_original_algo",
        "pending_algo",
        "position",
        "missing_close_claim",
        "wrong_parent",
        "wrong_cancel",
        "future_fill",
        "stale",
    ],
)
def test_unknown_changed_or_ambiguous_controlled_close_keeps_risk_held(change):
    intent, capture, args = scenario()
    row = decode(capture.close_order.raw_body)["data"][0]
    if change in {
        "partial_close",
        "wrong_side",
        "wrong_price",
        "wrong_margin",
        "wrong_leverage",
    }:
        row.update(
            {
                "partial_close": {"accFillSz": "0.5"},
                "wrong_side": {"side": "buy"},
                "wrong_price": {"px": "98"},
                "wrong_margin": {"tdMode": "cross"},
                "wrong_leverage": {"lever": "5"},
            }[change]
        )
        capture = replace(
            capture,
            close_order=reraw(capture.close_order, [row]),
            close_order_after=reraw(capture.close_order_after, [row]),
        )
    elif change in {"later_client_reuse", "duplicate_identity"}:
        later = {**row, "ordId": "125" if change == "later_client_reuse" else "124"}
        capture = replace(
            capture,
            archive_pages=(
                reraw(capture.archive_pages[0], [later, row]),
                capture.archive_pages[1],
            ),
        )
    elif change == "missing_terminal":
        capture = replace(capture, archive_pages=capture.archive_pages[:1])
    elif change == "wrong_cursor":
        page = capture.archive_pages[-1]
        capture = replace(
            capture,
            archive_pages=(
                capture.archive_pages[0],
                history(
                    [],
                    180,
                    begin=page.selector.begin_ms,
                    end=page.selector.end_ms,
                    after="123",
                ),
            ),
        )
    elif change == "wrong_window":
        capture = replace(capture, archive_cutoff=raw.stamp(158))
    elif change in {"changed_order_after", "changed_entry_after"}:
        name = (
            "close_order_after"
            if change == "changed_order_after"
            else "entry_order_after"
        )
        page = getattr(capture, name)
        value = decode(page.raw_body)["data"][0]
        value["ordId"] = "999"
        capture = replace(capture, **{name: reraw(page, [value])})
    elif change in {"foreign_fills", "future_fill"}:
        page = capture.close_fills[0]
        value = decode(page.raw_body)["data"][0]
        value.update(
            {"ordId": "999"}
            if change == "foreign_fills"
            else {"fillTime": str(raw.BASE // 1_000_000 + 99999)}
        )
        capture = replace(
            capture, close_fills=(reraw(page, [value]), capture.close_fills[1])
        )
    elif change in {"active_protection", "effective_oco", "wrong_original_algo"}:
        value = decode(capture.protection.raw_body)["data"][0]
        value.update(
            {"state": "live"}
            if change == "active_protection"
            else {"state": "effective"}
            if change == "effective_oco"
            else {"algoId": "999"}
        )
        capture = replace(
            capture,
            protection=reraw(capture.protection, [value]),
            protection_after=reraw(capture.protection_after, [value]),
        )
    elif change in {"pending_algo", "position"}:
        pages = list(capture.current_pages)
        index = 2 if change == "pending_algo" else 0
        pages[index] = (
            raw.current("algo_" + raw.ALGO_TYPES[0], [{"algoId": "456"}], 330)
            if index
            else raw.current(
                "positions",
                [{"pos": "1", "instId": intent.instrument_id, "posSide": "net"}],
                310,
            )
        )
        capture = replace(capture, current_pages=tuple(pages))
    elif change == "missing_close_claim":
        args["records"] = replace(args["records"], close=None)
    elif change == "wrong_parent":
        args["records"] = replace(
            args["records"],
            intent=replace(args["records"].intent, reservation_id="0" * 64),
        )
    elif change == "wrong_cancel":
        value = decode(args["records"].cancel.document_json)
        value["close_order_id"] = "999"
        args["records"] = replace(
            args["records"],
            cancel=replace(args["records"].cancel, document_json=canonical(value)),
        )
    else:
        capture = replace(capture, validated_at=raw.stamp(6000))
    result = subject.assess_controlled_close_recovery_v1(intent, capture, **args)
    assert result.outcome == "UNCERTAIN" and not result.exit_lineage_verified


def test_fill_time_after_old_request_deadline_is_not_rewritten_or_confused_with_creation():
    intent, capture, args = scenario(shift=10_000)
    close = decode(capture.close_order.raw_body)["data"][0]
    close["uTime"] = str(raw.BASE // 1_000_000 + 6000)
    fill = decode(capture.close_fills[0].raw_body)["data"][0]
    fill.update(
        fillTime=str(raw.BASE // 1_000_000 + 5000), ts=str(raw.BASE // 1_000_000 + 5001)
    )
    capture = replace(
        capture,
        close_order=reraw(capture.close_order, [close]),
        close_order_after=reraw(capture.close_order_after, [close]),
        close_fills=(reraw(capture.close_fills[0], [fill]), capture.close_fills[1]),
        archive_pages=(
            reraw(capture.archive_pages[0], [close]),
            capture.archive_pages[1],
        ),
    )
    assert (
        utc_from_ns(int(fill["fillTime"]) * 1_000_000) > args["records"].close.deadline
    )
    assert (
        subject.assess_controlled_close_recovery_v1(intent, capture, **args).outcome
        == "RECONCILED_FLAT"
    )


def test_independent_record_fingerprint_detects_mutated_retained_data():
    _, _, args = scenario()
    record = args["records"]
    original = subject.records_sha256_v1(record)
    object.__setattr__(
        record.close,
        "recorded_at",
        record.close.recorded_at + timedelta(milliseconds=1),
    )
    assert subject.records_sha256_v1(record) != original


def test_minimal_sql_only_protected_seed_cannot_substitute_for_complete_source_binding():
    from app.okx_live import original_oco_recovery_v1 as original
    from app.okx_live import reconciliation_facts_v1 as facts

    intent, capture, args = scenario()
    complete = args["journal"]
    assert original.expected_original_bindings_v1(complete) == ("123", "456")
    assert (
        subject.assess_controlled_close_recovery_v1(intent, capture, **args).outcome
        == "RECONCILED_FLAT"
    )
    minimal_document = decode(complete.proofs[0])
    del minimal_document["full_account_verified"]
    del minimal_document["fills_complete"]
    minimal = SimpleNamespace(
        **{**vars(complete), "proofs": (canonical(minimal_document),)}
    )
    with pytest.raises(facts.LiveReconciliationFactsError):
        original.expected_original_bindings_v1(minimal)
    result = subject.assess_controlled_close_recovery_v1(
        intent, capture, **{**args, "journal": minimal}
    )
    assert result.outcome == "UNCERTAIN" and result.exit_lineage_verified is False


def test_complete_synthetic_sql_identity_seed_is_selected_before_constructing_new_raw():
    """The real PG IDs differ from the small pure fixture, never from a retag."""
    intent, _, original = scenario()
    previous = original["records"]
    protected = decode(original["journal"].proofs[0])
    protected.update(entry_order_id="123456", protection_algo_id="987654")
    protected_raw = canonical(protected)
    document = decode(previous.intent.document_json)
    document.update(
        entry_order_id="123456",
        protection_algo_id="987654",
        protected_proof_sha256=sha(protected_raw),
    )
    parent_raw = canonical(document)
    parent = replace(
        previous.intent, document_json=parent_raw, document_sha256=sha(parent_raw)
    )
    close_doc = decode(previous.close.document_json)
    close_doc["close_intent_sha256"] = parent.document_sha256
    close_raw = canonical(close_doc)
    close = replace(
        previous.close,
        intent=parent,
        document_json=close_raw,
        document_sha256=sha(close_raw),
    )
    cancel_doc = decode(previous.cancel.document_json)
    cancel_doc.update(
        close_intent_sha256=parent.document_sha256,
        close_claim_sha256=close.document_sha256,
        entry_order_id="123456",
        protection_algo_id="987654",
        close_order_id="123457",
    )
    cancel_request = canonical([{"instId": intent.instrument_id, "algoId": "987654"}])
    cancel_doc.update(
        request_json=cancel_request.decode(), request_sha256=sha(cancel_request)
    )
    cancel_raw = canonical(cancel_doc)
    cancel = replace(
        previous.cancel,
        intent=parent,
        document_json=cancel_raw,
        document_sha256=sha(cancel_raw),
        request_json=cancel_request,
        request_sha256=sha(cancel_request),
    )
    records = subject.RetainedControlledCloseV1(parent, close, cancel)
    journal = SimpleNamespace(
        **{**vars(original["journal"]), "proofs": (protected_raw,)}
    )
    value, capture, args = scenario(
        original_intent=intent,
        retained_records=records,
        retained_journal=journal,
        history_override=original["history"],
    )
    assert (
        capture.order.selector.order_id == "123456"
        and capture.close_order.selector.order_id == "123457"
    )
    assert (
        subject.assess_controlled_close_recovery_v1(value, capture, **args).outcome
        == "RECONCILED_FLAT"
    )


def signed_records(monkeypatch):
    """Published synthetic key only; actual retained domain verifier is reused."""
    import hashlib
    import hmac

    from app.domain import native_live_controlled_close_schema_v1 as spec
    from app.okx_live import lifecycle_key_v1 as keys
    from tests.unit.native_live_close_fixture_v1 import METADATA

    _, _, args = scenario()
    records = args["records"]
    material = bytes(range(32))
    monkeypatch.setattr(
        keys, "_record", lambda owner: {"document": {"key_hex": material.hex()}}
    )
    verifier = object.__new__(repository.NativeLiveControlledCloseRepositoryV1)
    verifier._key = lambda owner: METADATA

    def signature(value, domain):
        return hmac.digest(material, domain + value.document_json, hashlib.sha256).hex()

    parent = replace(
        records.intent, signature=signature(records.intent, spec.INTENT_DOMAIN)
    )
    close = replace(
        records.close,
        intent=parent,
        signature=signature(records.close, spec.CLAIM_DOMAIN),
    )
    cancel = replace(
        records.cancel,
        intent=parent,
        signature=signature(records.cancel, spec.CLAIM_DOMAIN),
    )
    return (
        subject.RetainedControlledCloseV1(parent, close, cancel),
        verifier,
        args["journal"],
    )


def test_expired_retained_claim_hmac_is_data_and_never_a_new_current_permission(
    monkeypatch,
):
    records, verifier, journal = signed_records(monkeypatch)
    subject.verify_retained_records_v1(records, verifier, object(), journal)
    assert records.close.deadline < datetime.now(records.close.deadline.tzinfo)
    assert all(
        value.execution_authority is False
        for value in (records.intent, records.close, records.cancel)
    )


@pytest.mark.parametrize(
    "field",
    [
        "signature",
        "parent_id",
        "request",
        "wire_sha",
        "deadline",
        "action",
        "authority",
        "document",
    ],
)
def test_retained_hmac_or_original_scope_change_cannot_be_replayed(monkeypatch, field):
    records, verifier, journal = signed_records(monkeypatch)
    if field == "parent_id":
        records = replace(
            records, intent=replace(records.intent, close_intent_id="0" * 64)
        )
    else:
        claim = records.close
        mutations = {
            "signature": {"signature": "0" * 64},
            "request": {"request_json": b"{}"},
            "wire_sha": {"request_wire_sha256": "0" * 64},
            "deadline": {"deadline": claim.deadline + timedelta(seconds=1)},
            "action": {"action": "cancel_original_protection"},
            "document": {"document_json": claim.document_json + b" "},
        }
        if field == "authority":
            object.__setattr__(claim, "execution_authority", True)
        else:
            claim = replace(claim, **mutations[field])
        records = replace(records, close=claim)
    with pytest.raises(ValueError):
        subject.verify_retained_records_v1(records, verifier, object(), journal)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_persisted_close_claim_selects_one_readonly_collector_and_never_falls_back(
    monkeypatch, failure
):
    from app.okx_live import original_oco_recovery_v1 as original
    from app.okx_live import owned_recovery_v1 as recovery

    _, capture, args = scenario()
    state, owner, calls = {"controlled_close_recovery": args["records"]}, object(), []
    monkeypatch.setattr(
        recovery,
        "_scope",
        lambda value: (state, SimpleNamespace(controlled_records=args["records"])),
    )

    async def controlled(value, *, key):
        assert value is owner and key is subject._CAPTURE_KEY
        calls.append("new_get_only_controlled")
        if failure:
            raise TimeoutError("declared synthetic missing ACK/history")
        return capture

    async def forbidden(*args, **kwargs):
        pytest.fail(
            "a persisted close claim must never be reclassified as original OCO"
        )

    marker = object()
    monkeypatch.setattr(subject, "capture_controlled_close_recovery_v1", controlled)
    monkeypatch.setattr(
        subject, "assess_owned_capture_data_v1", lambda value, data: marker
    )
    monkeypatch.setattr(original, "capture_recovery_pages_v1", forbidden)
    if failure:
        with pytest.raises(TimeoutError):
            await recovery._capture_for_recovery_v1(owner)
    else:
        assert await recovery._capture_for_recovery_v1(owner) == (capture, marker)
    assert calls == ["new_get_only_controlled"]


def test_controlled_terminal_proof_keeps_historical_claims_and_no_original_http_start(
    monkeypatch,
):
    from app.okx_live import lifecycle_key_v1 as keys
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    intent, capture, args = scenario()
    assessed = subject.assess_controlled_close_recovery_v1(intent, capture, **args)
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
        and document["exit_kind"] == "controlled_micro_close"
    )
    assert (
        document["controlled_close_claim_sha256"]
        == args["records"].close.document_sha256
    )
    assert (
        document["controlled_cancel_claim_sha256"]
        == args["records"].cancel.document_sha256
    )
    assert (
        document["exit_client_order_id"] == "CTCCXsynthetic"
        and document["submission_started"] is None
    )
    assert (
        document["fixed_fill_economics"] is None
        and document["protection_verified"] is False
    )
    assert document["execution_authority"] is False


@pytest.mark.parametrize("replacement", [None, object(), lambda *args: None])
def test_named_source_verifier_cannot_disappear_through_foreign_callable_substitution(
    monkeypatch, replacement
):
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    lifecycle._require_lifecycle_source_modules_v1()
    monkeypatch.setattr(subject, "verify_owned_archive_custody_v1", replacement)
    with pytest.raises(ValueError):
        lifecycle._require_lifecycle_source_modules_v1()


def test_same_named_compiled_function_with_foreign_globals_is_refused(monkeypatch):
    from types import FunctionType

    from app.okx_live import owned_lifecycle_v1 as lifecycle

    original = subject.verify_owned_archive_custody_v1
    forged = FunctionType(
        original.__code__, {"__name__": subject.__name__}, original.__name__
    )
    assert (
        forged.__module__ == original.__module__
        and forged.__code__ is original.__code__
    )
    monkeypatch.setattr(subject, original.__name__, forged)
    with pytest.raises(ValueError):
        lifecycle._require_lifecycle_source_modules_v1()


@pytest.mark.parametrize(
    "mutation", [None, "attempt", "custody", "body", "missing_page"]
)
def test_signer_independently_reopens_exact_archive_inventory(monkeypatch, mutation):
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_recovery_v1 as recovery

    _, capture, _ = scenario()
    attempt, root, owner, lease = (
        capture.archive_attempt_id,
        object(),
        object(),
        object(),
    )
    receipt = {"custody_sha256": capture.archive_custody_sha256}
    state = {
        "phase": "source_verified",
        "capture": capture,
        "source_root": root,
        "lease": lease,
        "controlled_archive_custody": (
            attempt,
            canonical(receipt),
            capture.archive_pages[0].selector,
        ),
    }
    calls = []
    monkeypatch.setattr(recovery, "_scope", lambda value: (state, None))

    def reopen(actual_root, actual_attempt, actual_receipt, selector, active):
        assert actual_root is root and active is lease and actual_attempt == attempt
        assert (
            actual_receipt == receipt and selector == capture.archive_pages[0].selector
        )
        calls.append("complete_private_inventory_reopened")
        pages = capture.archive_pages
        if mutation == "body":
            pages = (replace(pages[0], raw_body=b"{}"), *pages[1:])
        elif mutation == "missing_page":
            pages = pages[:-1]
        return capture.archive_config_before, capture.archive_config_after, pages

    monkeypatch.setattr(subject, "_archive_readback", reopen)
    if mutation in ("attempt", "custody"):
        capture = replace(
            capture,
            **(
                {"archive_attempt_id": "9" * 32}
                if mutation == "attempt"
                else {"archive_custody_sha256": "0" * 64}
            ),
        )
        state["capture"] = capture
    if mutation:
        with pytest.raises(ValueError):
            subject.verify_owned_archive_custody_v1(
                owner, capture, key=lifecycle._SOURCE_LEASE_KEY_V1
            )
    else:
        subject.verify_owned_archive_custody_v1(
            owner, capture, key=lifecycle._SOURCE_LEASE_KEY_V1
        )
    assert calls == (
        []
        if mutation in ("attempt", "custody")
        else ["complete_private_inventory_reopened"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["parent", "claim"])
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_optional_restricted_query_distinguishes_absence_from_conflict_and_full_replay(
    which, count
):
    _, _, args = scenario()
    records, calls = args["records"], []
    value = records.intent if which == "parent" else records.close
    close_id = records.intent.close_intent_id

    class Session:
        async def __aenter__(self):
            calls.append("independent_read_open")
            return self

        async def __aexit__(self, *args):
            calls.append("independent_read_closed")

        def begin(self):
            return self

        async def execute(self, statement, parameters):
            assert (
                "LIMIT 2" in str(statement)
                and parameters["uid"] == args["journal"].intent.account_uid
            )
            return SimpleNamespace(all=lambda: [(close_id,)] * count)

    observer = object.__new__(repository.NativeLiveControlledCloseRepositoryV1)
    observer.factory = Session
    observer._key = lambda owner: {"account_uid": args["journal"].intent.account_uid}

    async def settings(session, *, readonly):
        assert readonly is True
        calls.append("restricted_profile_and_readonly")

    async def original(*args):
        assert calls[-1] == "independent_read_closed"
        calls.append("full_original_hmac_readback")
        return value

    observer._settings = settings
    observer.read_close_intent_v1 = original
    observer.read_close_claim_v1 = original
    read = (
        observer.read_by_reservation_v1(object(), records.intent.reservation_id)
        if which == "parent"
        else observer.read_claim_if_present_v1(object(), close_id, "close")
    )
    if count == 2:
        with pytest.raises(ValueError):
            await read
    else:
        assert await read is (None if count == 0 else value)
    assert calls.count("full_original_hmac_readback") == (1 if count == 1 else 0)
