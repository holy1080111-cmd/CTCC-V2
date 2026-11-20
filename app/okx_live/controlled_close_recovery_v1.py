"""Fresh GET recovery of persisted controlled closes; never reuse permission.

An expired authenticated claim is historical DATA. It cannot create another
close/cancel, and no missing ACK or latest client-ID match is turned into one.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from itertools import pairwise

from app.database.repositories import native_live_controlled_close_v1 as custody
from app.domain.native_live_intent_v1 import (
    checked_live_intent_data_v1,
    exact_live_intent_document_v1,
)
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    validate_stamps,
)
from app.exchange.okx import live_account_history_chain_v1 as history_chain
from app.exchange.okx.live_account_history_page_v1 import (
    LiveAccountHistoryPageV1,
    _check_pure_page,
    select_live_account_history_page_v1,
    verify_live_account_history_page_readback_v1,
)
from app.exchange.okx.live_trade_read_page_v1 import (
    observe_live_trade_read_page_v1,
    select_live_trade_read_v1,
    verify_live_trade_read_page_v1,
)
from app.okx_live import original_oco_recovery_v1 as original
from app.okx_live import reconciliation_facts_v1 as facts
from app.okx_live.postsubmit_capture_v1 import OriginalLiveLifecycleCaptureV1

_CAPTURE_KEY = object()


def _need(value):
    if value is not True:
        raise facts.LiveReconciliationFactsError(
            "live_controlled_restart_incomplete_or_changed"
        ) from None


@dataclass(frozen=True, slots=True, repr=False)
class RetainedControlledCloseV1:
    intent: custody.LiveControlledCloseIntentDataV1
    close: custody.LiveControlledCloseClaimDataV1 | None
    cancel: custody.LiveControlledCloseClaimDataV1 | None


def records_sha256_v1(records):
    if records is None:
        return sha(canonical(None))
    _need(type(records) is RetainedControlledCloseV1)
    rows = []
    for value in (records.intent, records.close, records.cancel):
        if value is None:
            rows.append(None)
        else:
            _need(
                type(value)
                in (
                    custody.LiveControlledCloseIntentDataV1,
                    custody.LiveControlledCloseClaimDataV1,
                )
            )
            _need(
                type(value.recorded_at) is datetime and value.recorded_at.tzinfo is UTC
            )
            rows.append(
                [
                    value.close_intent_id
                    if type(value) is custody.LiveControlledCloseIntentDataV1
                    else value.intent.close_intent_id,
                    value.reservation_id
                    if type(value) is custody.LiveControlledCloseIntentDataV1
                    else value.intent.reservation_id,
                    None
                    if type(value) is custody.LiveControlledCloseIntentDataV1
                    else value.action,
                    None
                    if type(value) is custody.LiveControlledCloseIntentDataV1
                    else value.request_wire_sha256,
                    sha(value.document_json),
                    value.document_sha256,
                    value.signature,
                    sha(value.request_json),
                    value.request_sha256,
                    value.deadline.isoformat(),
                    value.recorded_at.isoformat(),
                ]
            )
    return sha(canonical(rows))


async def read_retained_records_v1(cfg, key_owner, journal):
    repository = custody.NativeLiveControlledCloseRepositoryV1(
        cfg["reconciler_factory"], cfg["reconciler_pins"]
    )

    async def read():
        parent = await repository.read_by_reservation_v1(
            key_owner, journal.reservation_id
        )
        if parent is None:
            return None
        close = await repository.read_claim_if_present_v1(
            key_owner, parent.close_intent_id, "close"
        )
        cancel = await repository.read_claim_if_present_v1(
            key_owner, parent.close_intent_id, "cancel_original_protection"
        )
        _need(cancel is None or close is not None)
        return RetainedControlledCloseV1(parent, close, cancel)

    first, second = await read(), await read()
    _need(records_sha256_v1(first) == records_sha256_v1(second))
    if first is not None:
        verify_retained_records_v1(first, repository, key_owner, journal)
    return first


def verify_retained_records_v1(records, repository, key_owner, journal):
    if records is None:
        return
    _need(type(records) is RetainedControlledCloseV1)
    parent = records.intent
    _need(
        type(parent) is custody.LiveControlledCloseIntentDataV1
        and parent.execution_authority is False
    )
    document, request, deadline = repository._document(
        parent.document_json, parent.signature, key_owner
    )
    _need(
        parent.reservation_id == journal.reservation_id == document["reservation_id"]
        and document["original_intent_sha256"] == journal.intent_sha256
        and parent.document_sha256 == sha(parent.document_json)
        and parent.close_intent_id == document["close_intent_id"]
        and parent.request_json == request
        and parent.request_sha256 == sha(request)
        and parent.deadline == deadline
        and parent.recorded_at < deadline
    )
    for value, action in (
        (records.close, "close"),
        (records.cancel, "cancel_original_protection"),
    ):
        if value is None:
            continue
        _need(
            type(value) is custody.LiveControlledCloseClaimDataV1
            and value.execution_authority is False
            and value.intent.document_json == parent.document_json
            and value.intent.signature == parent.signature
        )
        claim, body, until = repository._document(
            value.document_json, value.signature, key_owner, claim=True
        )
        _need(
            value.action == action == claim["action"]
            and value.document_sha256 == sha(value.document_json)
            and claim["close_intent_sha256"] == parent.document_sha256
            and claim["close_intent_id"] == parent.close_intent_id
            and value.request_json == body
            and value.request_sha256 == sha(body)
            and value.request_wire_sha256 == claim["request_wire_sha256"]
            and value.deadline == until
            and parent.recorded_at <= value.recorded_at < until
        )
        for name in (
            "reservation_id",
            "original_intent_sha256",
            "candidate_sha256",
            "report_sha256",
            "policy_sha256",
            "consent_sha256",
            "operator_roundtrip_id",
            "original_credential_session_sha256",
            "credential_session_sha256",
            "original_process_sha256",
            "process_sha256",
            "expected_control_revision",
        ):
            _need(
                type(claim[name]) is type(document[name])
                and claim[name] == document[name]
            )
        if action == "close":
            _need(
                value.request_json == parent.request_json
                and value.deadline == parent.deadline
            )
        else:
            _need(
                records.close is not None
                and claim["close_claim_sha256"] == records.close.document_sha256
            )


def _ms(value):
    _need(type(value) is datetime and value.tzinfo is UTC)
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ControlledCloseRecoveryCaptureV1(OriginalLiveLifecycleCaptureV1):
    entry_order_after: object
    close_order: object
    close_order_after: object
    close_fills: tuple
    protection_after: object
    archive_pages: tuple
    archive_config_before: object
    archive_config_after: object
    archive_custody_sha256: str
    archive_attempt_id: str
    archive_cutoff: ClockStamp


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ControlledZeroCloseOcoCaptureV1(original.OriginalLiveOcoTerminalCaptureV1):
    zero_order: object
    zero_fills: tuple
    zero_order_after: object
    zero_validated_at: ClockStamp
    archive_pages: tuple
    archive_config_before: object
    archive_config_after: object
    archive_custody_sha256: str
    archive_attempt_id: str
    archive_cutoff: ClockStamp


def is_controlled_capture_data_v1(capture):
    return type(capture) in (
        ControlledCloseRecoveryCaptureV1,
        ControlledZeroCloseOcoCaptureV1,
    )


def _close_lineage_v1(intent, records, journal):
    rid, _, digest = exact_live_intent_document_v1(intent)
    _need(type(records) is RetainedControlledCloseV1 and records.close is not None)
    parent, claim = records.intent, records.close
    document = decode(parent.document_json, maximum=65536)
    claim_document = decode(claim.document_json, maximum=32768)
    _need(
        parent.reservation_id == rid == document["reservation_id"]
        and document["original_intent_sha256"] == digest == journal.intent_sha256
        and claim_document["close_intent_sha256"] == parent.document_sha256
        and claim_document["action"] == "close"
        and claim.intent.document_sha256 == parent.document_sha256
    )
    entry, algo = original.expected_original_bindings_v1(journal)
    _need(
        entry == document["entry_order_id"] and algo == document["protection_algo_id"]
    )
    referenced = [
        decode(raw, maximum=65536)
        for raw in journal.proofs
        if sha(raw) == document["protected_proof_sha256"]
    ]
    _need(
        len(referenced) == 1
        and referenced[0]["outcome"] == "PROTECTED_HOLD"
        and referenced[0]["entry_order_id"] == entry
        and referenced[0]["protection_algo_id"] == algo
    )
    request = decode(parent.request_json, maximum=8192)
    _need(
        request
        == {
            "instId": intent.instrument_id,
            "tdMode": "isolated",
            "posSide": "net",
            "side": "sell" if intent.direction == "long" else "buy",
            "ordType": "fok",
            "sz": format(intent.contracts, "f"),
            "px": format(intent.stop_loss, "f"),
            "clOrdId": document["close_client_order_id"],
            "reduceOnly": True,
        }
    )
    _need(
        document["protection_client_id"] == intent.protection_client_id
        and document["candidate_sha256"] == intent.candidate_sha256
        and document["report_sha256"] == intent.report_sha256
        and document["policy_sha256"] == intent.policy_sha256
    )
    return document, entry, algo


def _verify_zero_effect_v1(
    intent,
    records,
    journal,
    *,
    order,
    fills,
    after,
    validated,
    archive_pages,
    archive_before,
    archive_after,
    cutoff,
    barrier,
):
    """A terminal canceled FOK plus complete empty fills proves no close effect."""
    checked_live_intent_data_v1(intent)
    document, entry, _ = _close_lineage_v1(intent, records, journal)
    _need(records.cancel is None)
    context = {
        "barrier": barrier,
        "validated_at": validated,
        "origin": order.origin,
        "binding": order.lease_binding_id,
    }
    archived = archive_original_close_v1(
        intent,
        records,
        archive_pages,
        barrier=barrier,
        cutoff=cutoff,
        origin=context["origin"],
        binding=context["binding"],
    )
    identity = archived["ordId"]
    _need(
        identity != entry
        and order.selector
        == after.selector
        == select_live_trade_read_v1(
            "order", instrument_id=intent.instrument_id, order_id=identity
        )
    )
    row = original._one(order, context)
    _need(row == original._one(after, context))
    _need(type(fills) is tuple and len(fills) == 1)
    page = fills[0]
    _need(
        page.selector
        == select_live_trade_read_v1(
            "fills_history", instrument_id=intent.instrument_id, order_id=identity
        )
    )
    _need(facts._payload(page, **context) == [])
    for name in (
        "ordId",
        "clOrdId",
        "instId",
        "instType",
        "side",
        "posSide",
        "tdMode",
        "ordType",
        "state",
        "sz",
        "accFillSz",
        "avgPx",
        "lever",
        "px",
        "reduceOnly",
        "cTime",
    ):
        _need(type(row.get(name)) is str and row[name] == archived.get(name))
    _need(
        all(
            row.get(name) == value
            for name, value in {
                "ordId": identity,
                "clOrdId": document["close_client_order_id"],
                "instId": intent.instrument_id,
                "instType": "SWAP",
                "side": "sell" if intent.direction == "long" else "buy",
                "posSide": "net",
                "tdMode": "isolated",
                "ordType": "fok",
                "state": "canceled",
                "reduceOnly": "true",
                "avgPx": "",
                "algoId": "",
                "algoClOrdId": "",
            }.items()
        )
    )
    _need(
        facts._decimal(row, "sz", positive=True) == intent.contracts
        and facts._decimal(row, "accFillSz", nonnegative=True) == 0
        and facts._decimal(row, "px", positive=True) == intent.stop_loss
        and facts._decimal(row, "lever", positive=True) == intent.leverage
        and _ms(records.close.recorded_at)
        <= original._time(row, "cTime")
        <= _ms(records.close.deadline)
        and original._time(row, "cTime") <= original._time(row, "uTime")
    )
    sequence = (archive_before, *archive_pages, archive_after, order, page, after)
    for left, right in pairwise(sequence):
        validate_stamps((left.stamps[-1].model_dump(), right.stamps[0].model_dump()))
    validate_stamps(
        (barrier.model_dump(), cutoff.model_dump(), sequence[0].stamps[0].model_dump())
    )
    configs = [
        original._one(value, {**context, "validated_at": value.stamps[-1]})
        for value in (archive_before, archive_after)
    ]
    _need(
        configs[0] == configs[1]
        and configs[0].get("uid") == intent.account_uid
        and configs[0].get("mainUid") == intent.main_uid
        and configs[0].get("posMode") == "net_mode"
    )
    return sha(
        canonical(
            [
                records.intent.document_sha256,
                records.close.document_sha256,
                identity,
                [item.receipt_sha256 for item in sequence],
                validated.model_dump(),
            ]
        )
    )


def _original_oco_view_v1(capture):
    _need(type(capture) is ControlledZeroCloseOcoCaptureV1)
    # The exact original Live DTOs, origins, raw bytes and measured stamps are
    # retained unchanged; this DATA wrapper creates no source/submit authority.
    return original.OriginalLiveOcoTerminalCaptureV1(
        capture.order,
        capture.fills,
        capture.protection,
        capture.config_before,
        capture.config_after,
        capture.current_pages,
        capture.current_custody_sha256,
        capture.raw_custody_sha256,
        capture.validated_at,
        capture.tick_size,
        capture.contract_value,
        capture.contract_multiplier,
        capture.contract_value_currency,
        entry_order_after=capture.entry_order_after,
        exit_order=capture.exit_order,
        exit_order_after=capture.exit_order_after,
        exit_fills=capture.exit_fills,
        protection_after=capture.protection_after,
    )


def assess_zero_effect_original_oco_v1(
    intent, capture, *, history, barrier, records, journal
):
    rid = digest = None
    try:
        intent = checked_live_intent_data_v1(intent)
        rid, _, digest = exact_live_intent_document_v1(intent)
        _need(type(capture) is ControlledZeroCloseOcoCaptureV1)
        nonexecution = _verify_zero_effect_v1(
            intent,
            records,
            journal,
            order=capture.zero_order,
            fills=capture.zero_fills,
            after=capture.zero_order_after,
            validated=capture.zero_validated_at,
            archive_pages=capture.archive_pages,
            archive_before=capture.archive_config_before,
            archive_after=capture.archive_config_after,
            cutoff=capture.archive_cutoff,
            barrier=barrier,
        )
        entry, algo = original.expected_original_bindings_v1(journal)
        assessed = original.assess_original_live_oco_terminal_v1(
            intent,
            _original_oco_view_v1(capture),
            history=history,
            barrier=capture.zero_validated_at,
            expected_algo_id=algo,
            expected_entry_order_id=entry,
        )
        _need(
            assessed.outcome == "RECONCILED_FLAT"
            and assessed.exit_lineage_verified is True
        )
        _need(
            capture.exit_order.selector.order_id != capture.zero_order.selector.order_id
        )
        bank = sha(
            canonical(
                [
                    nonexecution,
                    assessed.raw_bank_sha256,
                    capture.archive_custody_sha256,
                    capture.archive_attempt_id,
                ]
            )
        )
        return replace(assessed, raw_bank_sha256=bank, exit_source_sha256=bank)
    except Exception:  # noqa: BLE001 -- a failed zero proof never selects another path.
        return facts.LiveReconciliationFactsV1(
            "UNCERTAIN", ("live_zero_close_original_oco_incomplete",), rid, digest
        )


def archive_original_close_v1(
    intent, records, pages, *, barrier, cutoff, origin, binding
):
    """Complete archive uniqueness: cTime is identity, never fill time or PnL."""
    _need(type(records) is RetainedControlledCloseV1 and records.close is not None)
    parent, claim = records.intent, records.close
    request = decode(parent.request_json, maximum=8192)
    begin, end = str(_ms(claim.recorded_at)), str(cutoff.utc_ns // 1_000_000 - 1)
    _need(type(pages) is tuple and 1 <= len(pages) <= history_chain._PAGE_CAP)
    rows, previous, seen = [], None, set()
    previous_page = None
    for index, page in enumerate(pages):
        _need(
            type(page) is LiveAccountHistoryPageV1
            and page.origin == origin
            and page.lease_binding_id == binding
        )
        _check_pure_page(page)
        _need(
            page.selector
            == select_live_account_history_page_v1(
                "orders_archive",
                begin_ms=begin,
                end_ms=end,
                inst_type="SWAP",
                after=previous,
            )
        )
        validate_stamps(
            (
                barrier.model_dump(),
                cutoff.model_dump(),
                *(stamp.model_dump() for stamp in page.stamps),
            )
        )
        _need(
            page.stamps[0].utc_ns > cutoff.utc_ns
            and page.stamps[0].monotonic_ns > cutoff.monotonic_ns
        )
        if previous_page is not None:
            _need(
                page.stamps[0].monotonic_ns - previous_page.stamps[-1].monotonic_ns
                >= 500_000_000
            )
        history_chain._require_window(
            page.selector, start=pages[0].stamps[0], through=page.stamps[-1]
        )
        history_chain._check_row_times(page)
        previous_page = page
        incoming = decode(page.raw_body, maximum=524288)
        _need(incoming.get("code") == "0" and type(incoming.get("data")) is list)
        incoming = incoming["data"]
        if not incoming:
            _need(index == len(pages) - 1)
            break
        _need(index < len(pages) - 1)
        ids = [row.get("ordId") for row in incoming]
        _need(
            all(
                type(value) is str
                and value.isascii()
                and value.isdecimal()
                and 0 < int(value) < 10**40
                for value in ids
            )
        )
        _need(
            all(int(left) > int(right) for left, right in pairwise(ids))
            and not seen.intersection(ids)
            and len(set(ids)) == len(ids)
            and (previous is None or all(int(value) < int(previous) for value in ids))
        )
        seen.update(ids)
        previous = ids[-1]
        rows.extend(incoming)
    else:
        _need(False)
    # Count every matching client ID through the cutoff, including later
    # reuses. Never pick the latest/best candidate or deduplicate a conflict.
    matched = [row for row in rows if row.get("clOrdId") == request["clOrdId"]]
    _need(len(matched) == 1)
    row = matched[0]
    _need(row.get("instId") == intent.instrument_id and row.get("instType") == "SWAP")
    _need(_ms(claim.recorded_at) <= original._time(row, "cTime") <= _ms(claim.deadline))
    # orders-archive filters creation time. A legitimate fill/update may be
    # later than that creation cutoff; the source body receipt bounds it.
    _need(original._time(row, "cTime") <= original._time(row, "uTime"))
    return row


def _fresh_pages(capture):
    _need(type(capture) is ControlledCloseRecoveryCaptureV1)
    return (
        capture.order,
        *capture.fills,
        capture.protection,
        capture.close_order,
        *capture.close_fills,
        capture.entry_order_after,
        capture.close_order_after,
        capture.protection_after,
        capture.config_before,
        *capture.current_pages,
        capture.config_after,
    )


def assess_controlled_close_recovery_v1(
    intent, capture, *, history, barrier, records, journal
):
    """DATA replay only: history proves identity; fills prove actual execution."""
    rid = digest = None
    try:
        with localcontext(
            Context(
                prec=200,
                Emin=-999,
                Emax=999,
                rounding=ROUND_HALF_EVEN,
                traps=[InvalidOperation, DivisionByZero, Overflow],
            )
        ):
            intent = checked_live_intent_data_v1(intent)
            rid, _, digest = exact_live_intent_document_v1(intent)
            _need(
                type(capture) is ControlledCloseRecoveryCaptureV1
                and capture.execution_authority is False
                and type(records) is RetainedControlledCloseV1
                and records.close is not None
                and type(history) is facts.LiveReconciliationHistoryV1
            )
            custody._digest(capture.archive_custody_sha256)
            _need(
                type(capture.archive_attempt_id) is str
                and len(capture.archive_attempt_id) == 32
                and set(capture.archive_attempt_id) <= set("0123456789abcdef")
            )
            parent, claim = records.intent, records.close
            document, original_entry, original_algo = _close_lineage_v1(
                intent, records, journal
            )
            opposite = "sell" if intent.direction == "long" else "buy"
            _need(
                type(capture.tick_size) is Decimal
                and capture.tick_size.is_finite()
                and capture.tick_size > 0
            )
            _need(
                all(
                    type(value) is Decimal and value.is_finite() and value > 0
                    for value in (capture.contract_value, capture.contract_multiplier)
                )
                and capture.contract_value_currency
                == intent.instrument_id.split("-", 1)[0]
            )
            _need(
                type(history.intent_recorded_at) is datetime
                and history.intent_recorded_at.tzinfo is UTC
                and history.intent_recorded_at <= intent.deadline
            )
            lower = (
                history.submission_started_utc
                if history.submission_started_utc is not None
                else history.intent_recorded_at
            )
            _need(
                type(lower) is datetime
                and lower.tzinfo is UTC
                and history.intent_recorded_at <= lower <= intent.deadline
            )
            context = {
                "barrier": barrier,
                "validated_at": capture.validated_at,
                "origin": capture.order.origin,
                "binding": capture.order.lease_binding_id,
            }
            pages = _fresh_pages(capture)
            for left, right in pairwise(pages):
                validate_stamps(
                    (left.stamps[-1].model_dump(), right.stamps[0].model_dump())
                )
            _need(
                capture.order.selector
                == capture.entry_order_after.selector
                == select_live_trade_read_v1(
                    "order", instrument_id=intent.instrument_id, order_id=original_entry
                )
            )
            entry = original._one(capture.order, context)
            _need(entry == original._one(capture.entry_order_after, context))
            _need(
                all(
                    entry.get(name) == value
                    for name, value in {
                        "ordId": original_entry,
                        "clOrdId": intent.client_order_id,
                        "instId": intent.instrument_id,
                        "instType": "SWAP",
                        "tdMode": "isolated",
                        "posSide": "net",
                        "side": "buy" if intent.direction == "long" else "sell",
                        "ordType": "fok",
                        "state": "filled",
                    }.items()
                )
            )
            _need(
                _ms(lower) <= original._time(entry, "cTime") <= _ms(intent.deadline)
                and facts._decimal(entry, "sz", positive=True)
                == facts._decimal(entry, "accFillSz", positive=True)
                == intent.contracts
                and facts._decimal(entry, "lever", positive=True) == intent.leverage
            )
            entry_rows, entry_trades, entry_bills, entry_average, entry_fee = (
                original._fills(
                    capture.fills, intent, entry, context, lower_ms=_ms(lower)
                )
            )
            _need(
                abs(facts._decimal(entry, "avgPx", positive=True) - entry_average)
                <= capture.tick_size / 2
                and original._time(entry, "cTime")
                <= min(original._time(row, "fillTime") for row in entry_rows)
                <= max(original._time(row, "fillTime") for row in entry_rows)
                <= original._time(entry, "uTime")
            )
            archived = archive_original_close_v1(
                intent,
                records,
                capture.archive_pages,
                barrier=barrier,
                cutoff=capture.archive_cutoff,
                origin=context["origin"],
                binding=context["binding"],
            )
            archive_sequence = (
                capture.archive_config_before,
                *capture.archive_pages,
                capture.archive_config_after,
                capture.order,
            )
            validate_stamps(
                (
                    barrier.model_dump(),
                    capture.archive_cutoff.model_dump(),
                    archive_sequence[0].stamps[0].model_dump(),
                )
            )
            for left, right in pairwise(archive_sequence):
                validate_stamps(
                    (left.stamps[-1].model_dump(), right.stamps[0].model_dump())
                )
            archive_configs = []
            for page in (capture.archive_config_before, capture.archive_config_after):
                # Historical identity evidence is checked at its original
                # measured receipt. Fresh terminal state uses the later full
                # current capture and the unchanged five-second guard.
                own = {**context, "validated_at": page.stamps[-1]}
                archive_configs.append(original._one(page, own))
            _need(
                archive_configs[0] == archive_configs[1]
                and archive_configs[0].get("uid") == intent.account_uid
                and archive_configs[0].get("mainUid") == intent.main_uid
                and archive_configs[0].get("posMode") == "net_mode"
            )
            order_id = archived["ordId"]
            _need(
                order_id != original_entry
                and capture.close_order.selector
                == capture.close_order_after.selector
                == select_live_trade_read_v1(
                    "order", instrument_id=intent.instrument_id, order_id=order_id
                )
            )
            close = original._one(capture.close_order, context)
            _need(close == original._one(capture.close_order_after, context))
            for name in (
                "ordId",
                "clOrdId",
                "instId",
                "instType",
                "side",
                "posSide",
                "tdMode",
                "ordType",
                "state",
                "sz",
                "accFillSz",
                "avgPx",
                "lever",
                "px",
                "reduceOnly",
                "cTime",
            ):
                _need(
                    type(close.get(name)) is str and close[name] == archived.get(name)
                )
            _need(
                all(
                    close.get(name) == value
                    for name, value in {
                        "clOrdId": document["close_client_order_id"],
                        "instId": intent.instrument_id,
                        "instType": "SWAP",
                        "side": opposite,
                        "posSide": "net",
                        "tdMode": "isolated",
                        "ordType": "fok",
                        "state": "filled",
                        "reduceOnly": "true",
                    }.items()
                )
            )
            _need(close.get("algoId") == close.get("algoClOrdId") == "")
            _need(
                _ms(claim.recorded_at)
                <= original._time(close, "cTime")
                <= _ms(claim.deadline)
                and facts._decimal(close, "px", positive=True) == intent.stop_loss
                and facts._decimal(close, "sz", positive=True)
                == facts._decimal(close, "accFillSz", positive=True)
                == intent.contracts
                and facts._decimal(close, "lever", positive=True) == intent.leverage
            )
            # Expiry bounds request creation, never a later execution timestamp.
            exits, trades, bills, average, _fee = original._fills(
                capture.close_fills,
                intent,
                close,
                context,
                lower_ms=_ms(claim.recorded_at),
            )
            _need(
                not trades.intersection(entry_trades)
                and not bills.intersection(entry_bills)
                and original._time(close, "cTime")
                <= min(original._time(row, "fillTime") for row in exits)
                <= max(original._time(row, "fillTime") for row in exits)
                <= original._time(close, "uTime")
                and abs(facts._decimal(close, "avgPx", positive=True) - average)
                <= capture.tick_size / 2
            )
            _need(
                average >= intent.stop_loss
                if intent.direction == "long"
                else average <= intent.stop_loss
            )
            _need(
                capture.protection.selector
                == capture.protection_after.selector
                == select_live_trade_read_v1(
                    "algo", instrument_id=intent.instrument_id, algo_id=original_algo
                )
            )
            protection = original._one(capture.protection, context)
            _need(protection == original._one(capture.protection_after, context))
            _need(
                all(
                    protection.get(name) == value
                    for name, value in {
                        "algoId": original_algo,
                        "algoClOrdId": intent.protection_client_id,
                        "instId": intent.instrument_id,
                        "instType": "SWAP",
                        "posSide": "net",
                        "tdMode": "isolated",
                        "side": opposite,
                        "ordType": "oco",
                        "state": "canceled",
                        "actualSide": "",
                        "ordId": "",
                        "ordIdList": [],
                        "subAlgoIdList": [],
                        "actualPx": "",
                        "triggerTime": "",
                        "tpTriggerPxType": "mark",
                        "slTriggerPxType": "mark",
                        "tpOrdPx": "-1",
                        "slOrdPx": "-1",
                        "reduceOnly": "true",
                        "closeFraction": "",
                    }.items()
                )
            )
            _need(
                facts._decimal(protection, "sz", positive=True) == intent.contracts
                and facts._decimal(protection, "actualSz", nonnegative=True) == 0
                and facts._decimal(protection, "tpTriggerPx", positive=True)
                == intent.take_profit
                and facts._decimal(protection, "slTriggerPx", positive=True)
                == intent.stop_loss
                and facts._decimal(protection, "lever", positive=True)
                == intent.leverage
                and original._time(protection, "cTime")
                <= original._time(protection, "uTime")
            )
            if records.cancel is not None:
                cancel = decode(records.cancel.document_json, maximum=32768)
                _need(
                    cancel["close_order_id"] == order_id
                    and cancel["close_claim_sha256"] == claim.document_sha256
                    and cancel["entry_order_id"] == original_entry
                    and cancel["protection_algo_id"] == original_algo
                    and original._time(protection, "uTime")
                    >= _ms(records.cancel.recorded_at)
                )
            original._flat(capture, intent, context)
            bank = sha(
                canonical(
                    [
                        capture.archive_custody_sha256,
                        capture.archive_attempt_id,
                        [
                            page.receipt_sha256
                            for page in (
                                capture.archive_config_before,
                                *capture.archive_pages,
                                capture.archive_config_after,
                                *pages,
                            )
                        ],
                    ]
                )
            )
            return facts.LiveReconciliationFactsV1(
                "RECONCILED_FLAT",
                (),
                rid,
                digest,
                original_entry,
                intent.contracts,
                entry_average,
                entry_fee,
                original_algo,
                bank,
                exit_order_id=order_id,
                exit_filled_contracts=intent.contracts,
                exit_source_sha256=bank,
                exit_kind="controlled_micro_close",
                exit_lineage_verified=True,
                exit_client_order_id=document["close_client_order_id"],
                controlled_close_intent_sha256=parent.document_sha256,
                controlled_close_claim_sha256=claim.document_sha256,
                controlled_close_consent_sha256=document["consent_sha256"],
                controlled_cancel_claim_sha256=records.cancel.document_sha256
                if records.cancel is not None
                else None,
            )
    except facts.LiveReconciliationFactsError:
        return facts.LiveReconciliationFactsV1(
            "UNCERTAIN", ("live_controlled_restart_incomplete_or_changed",), rid, digest
        )
    except Exception:  # noqa: BLE001 -- no missing order, ACK or raw field becomes flat.
        return facts.LiveReconciliationFactsV1(
            "UNCERTAIN", ("live_controlled_restart_raw_invalid",), rid, digest
        )


def _archive_readback(root, attempt, receipt, selector, lease):
    """Reopen the genuine Live history DTOs, never bridge/convert a Demo page."""
    replayed = history_chain.read_live_account_history_v1(
        root, attempt_id=attempt, expected_custody_sha256=receipt["custody_sha256"]
    )
    _need(replayed == receipt)
    _, before = history_chain._read_custodied_live_account_config(
        root / attempt,
        attempt_id=history_chain._CONFIG_BEFORE,
        expected_custody_sha256=receipt["config_before_custody_sha256"],
    )
    _, after = history_chain._read_custodied_live_account_config(
        root / attempt,
        attempt_id=history_chain._CONFIG_AFTER,
        expected_custody_sha256=receipt["config_after_custody_sha256"],
    )
    with history_chain._root_context(root) as base, base.child(attempt) as directory:
        plan = history_chain._read(directory, "observation.json")
        _need(
            plan
            == history_chain._plan(
                attempt,
                selector,
                lease_binding_id=lease.binding_id,
                origin=lease._pin.origin,
            )
        )
        coverage = history_chain._HistoryCoverage(before, selector)
        previous, pages = sha(plan), []
        for index in range(receipt["page_count"]):
            selected = coverage.selector()
            _need(selected is not None)
            with directory.child(history_chain._page_name(index)) as child:
                page, previous = history_chain._read_page(
                    child, index, previous, selected
                )
            verify_live_account_history_page_readback_v1(page, lease=lease)
            coverage.accept(page)
            pages.append(page)
        coverage.finish(after, ClockStamp.model_validate(receipt["capture_completed"]))
        _need(coverage.selector() is None and previous == receipt["last_page_sha256"])
    return before, after, tuple(pages)


async def capture_controlled_close_recovery_v1(owner, *, key):
    import secrets

    from app.exchange.okx import live_account_current_chain_v1 as current
    from app.exchange.okx.live_account_config_observation_v1 import _stamp
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_recovery_v1 as recovery
    from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1
    from app.okx_live.postsubmit_capture_v1 import (
        _current_original_pages,
        _metadata,
        _rows,
    )

    _need(key is _CAPTURE_KEY)
    state, _ = recovery._scope(owner)
    _need(state["phase"] == "capturing" and lifecycle._ACTIVE_CAPTURE.get() is owner)
    records = state["controlled_close_recovery"]
    _need(type(records) is RetainedControlledCloseV1 and records.close is not None)
    intent, client, lease = state["intent"], state["client"], state["lease"]
    document = decode(records.intent.document_json, maximum=65536)
    cutoff = _stamp()
    selector = select_live_account_history_page_v1(
        "orders_archive",
        begin_ms=str(_ms(records.close.recorded_at)),
        end_ms=str(cutoff.utc_ns // 1_000_000 - 1),
        inst_type="SWAP",
    )
    attempt = secrets.token_hex(16)
    receipt = await history_chain.collect_live_account_history_v1(
        client, lease, selector, private_root=state["source_root"], attempt_id=attempt
    )
    _need(receipt["synthetic_mock"] is False)
    # Private owner state pins the original source inventory independently of
    # the returned DATA object, before any signer or terminal projection.
    _need("controlled_archive_custody" not in state)
    state["controlled_archive_custody"] = (attempt, canonical(receipt), selector)
    archive_before, archive_after, archive = _archive_readback(
        state["source_root"], attempt, receipt, selector, lease
    )
    close = archive_original_close_v1(
        intent,
        records,
        archive,
        barrier=state["capture_barrier"],
        cutoff=cutoff,
        origin=lease._pin.origin,
        binding=lease.binding_id,
    )
    close_id = close["ordId"]

    async def read(kind, **values):
        page = await observe_live_trade_read_page_v1(
            client,
            lease,
            select_live_trade_read_v1(
                kind, instrument_id=intent.instrument_id, **values
            ),
        )
        verify_live_trade_read_page_v1(page, lease=lease)
        return page

    async def fills(order_id):
        result, after, seen = [], None, set()
        for _ in range(64):
            page = await read("fills_history", order_id=order_id, after_bill_id=after)
            result.append(page)
            incoming = _rows(page)
            if not incoming:
                return tuple(result)
            ids = [row["billId"] for row in incoming]
            _need(
                len(ids) == len(set(ids))
                and not seen.intersection(ids)
                and (after is None or all(int(value) < int(after) for value in ids))
            )
            seen.update(ids)
            after = ids[-1]
        _need(False)

    if (
        close.get("state") == "canceled"
        and facts._decimal(close, "accFillSz", nonnegative=True) == 0
    ):
        zero = await read("order", order_id=close_id)
        zero_fills = await fills(close_id)
        zero_after = await read("order", order_id=close_id)
        zero_validated = _stamp()
        # No generic failure fallback: prove this exact close had zero effect
        # before any original OCO collection is selected.
        _verify_zero_effect_v1(
            intent,
            records,
            state["journal"],
            order=zero,
            fills=zero_fills,
            after=zero_after,
            validated=zero_validated,
            archive_pages=archive,
            archive_before=archive_before,
            archive_after=archive_after,
            cutoff=cutoff,
            barrier=state["capture_barrier"],
        )
        oco = await original.capture_owned_original_oco_terminal_v1(
            owner, key=original._CAPTURE_KEY
        )
        _need(type(oco) is original.OriginalLiveOcoTerminalCaptureV1)
        return ControlledZeroCloseOcoCaptureV1(
            oco.order,
            oco.fills,
            oco.protection,
            oco.config_before,
            oco.config_after,
            oco.current_pages,
            oco.current_custody_sha256,
            oco.raw_custody_sha256,
            oco.validated_at,
            oco.tick_size,
            oco.contract_value,
            oco.contract_multiplier,
            oco.contract_value_currency,
            entry_order_after=oco.entry_order_after,
            exit_order=oco.exit_order,
            exit_order_after=oco.exit_order_after,
            exit_fills=oco.exit_fills,
            protection_after=oco.protection_after,
            zero_order=zero,
            zero_fills=zero_fills,
            zero_order_after=zero_after,
            zero_validated_at=zero_validated,
            archive_pages=archive,
            archive_config_before=archive_before,
            archive_config_after=archive_after,
            archive_custody_sha256=receipt["custody_sha256"],
            archive_attempt_id=attempt,
            archive_cutoff=cutoff,
        )

    entry = await read("order", order_id=document["entry_order_id"])
    entry_fills = await fills(document["entry_order_id"])
    protection = await read("algo", algo_id=document["protection_algo_id"])
    before = await read("order", order_id=close_id)
    exits = await fills(close_id)
    entry_after = await read("order", order_id=document["entry_order_id"])
    after = await read("order", order_id=close_id)
    protection_after = await read("algo", algo_id=document["protection_algo_id"])
    current_attempt = secrets.token_hex(16)
    current_receipt = await current.collect_live_current_account_v1(
        client, lease, private_root=state["source_root"], attempt_id=current_attempt
    )
    _need(current_receipt["synthetic_mock"] is False)
    cfg_before, cfg_after, pages = _current_original_pages(
        state["source_root"], current_attempt, current_receipt, lease
    )
    for path, digest, body_digest, _, _ in state["raw_captures"]:
        raw = decode(
            read_live_lifecycle_blob_v1(state["evidence_root"], digest, domain="raw"),
            maximum=12 * 1024 * 1024,
        )
        _need(path.name == digest + ".json" and raw["raw_body_sha256"] == body_digest)
    lease.verify_current()
    return ControlledCloseRecoveryCaptureV1(
        entry,
        entry_fills,
        protection,
        cfg_before,
        cfg_after,
        pages,
        current_receipt["custody_sha256"],
        sha(canonical([item[1] for item in state["raw_captures"]])),
        _stamp(),
        *_metadata(pages, intent.instrument_id),
        entry_order_after=entry_after,
        close_order=before,
        close_order_after=after,
        close_fills=exits,
        protection_after=protection_after,
        archive_pages=archive,
        archive_config_before=archive_before,
        archive_config_after=archive_after,
        archive_custody_sha256=receipt["custody_sha256"],
        archive_attempt_id=attempt,
        archive_cutoff=cutoff,
    )


def extra_trade_pages_v1(capture):
    if type(capture) is ControlledZeroCloseOcoCaptureV1:
        return (
            capture.entry_order_after,
            capture.exit_order,
            *capture.exit_fills,
            capture.exit_order_after,
            capture.protection_after,
            capture.zero_order,
            *capture.zero_fills,
            capture.zero_order_after,
        )
    return (
        ()
        if type(capture) is not ControlledCloseRecoveryCaptureV1
        else (
            capture.entry_order_after,
            capture.close_order,
            *capture.close_fills,
            capture.close_order_after,
            capture.protection_after,
        )
    )


def verify_owned_archive_custody_v1(owner, capture, *, key):
    from app.okx_live import owned_lifecycle_v1 as lifecycle
    from app.okx_live import owned_recovery_v1 as recovery

    _need(key is lifecycle._SOURCE_LEASE_KEY_V1)
    state, _ = recovery._scope(owner)
    _need(state["phase"] == "source_verified")
    _need(is_controlled_capture_data_v1(capture) and state["capture"] is capture)
    attempt, receipt_raw, selected = state["controlled_archive_custody"]
    receipt = decode(receipt_raw, maximum=8192)
    _need(
        attempt == capture.archive_attempt_id
        and receipt["custody_sha256"] == capture.archive_custody_sha256
    )
    before, after, pages = _archive_readback(
        state["source_root"], attempt, receipt, selected, state["lease"]
    )
    original_pages = (
        capture.archive_config_before,
        *capture.archive_pages,
        capture.archive_config_after,
    )
    replayed = (before, *pages, after)
    _need(len(original_pages) == len(replayed))
    for first, second in zip(original_pages, replayed, strict=True):
        _need(
            first.receipt_sha256 == second.receipt_sha256
            and first.raw_body == second.raw_body
            and first.raw_headers == second.raw_headers
            and first.stamps == second.stamps
            and first.origin == second.origin
            and first.lease_binding_id == second.lease_binding_id
        )


def extra_history_pages_v1(capture):
    return () if not is_controlled_capture_data_v1(capture) else capture.archive_pages


def extra_config_pages_v1(capture):
    return (
        ()
        if not is_controlled_capture_data_v1(capture)
        else (capture.archive_config_before, capture.archive_config_after)
    )


def assess_owned_capture_data_v1(state, capture):
    if type(capture) is ControlledZeroCloseOcoCaptureV1:
        return assess_zero_effect_original_oco_v1(
            state["intent"],
            capture,
            history=state["recovery_history"],
            barrier=state["capture_barrier"],
            records=state["controlled_close_recovery"],
            journal=state["journal"],
        )
    return assess_controlled_close_recovery_v1(
        state["intent"],
        capture,
        history=state["recovery_history"],
        barrier=state["capture_barrier"],
        records=state["controlled_close_recovery"],
        journal=state["journal"],
    )


def nonexecution_proof_data_v1(state, capture):
    _need(type(capture) is ControlledZeroCloseOcoCaptureV1)
    records = state["controlled_close_recovery"]
    return {
        "semantics": "authenticated_terminal_zero_effect_before_original_oco",
        "close_intent_sha256": records.intent.document_sha256,
        "close_claim_sha256": records.close.document_sha256,
        "close_order_id": capture.zero_order.selector.order_id,
        "zero_effect_source_sha256": _verify_zero_effect_v1(
            state["intent"],
            records,
            state["journal"],
            order=capture.zero_order,
            fills=capture.zero_fills,
            after=capture.zero_order_after,
            validated=capture.zero_validated_at,
            archive_pages=capture.archive_pages,
            archive_before=capture.archive_config_before,
            archive_after=capture.archive_config_after,
            cutoff=capture.archive_cutoff,
            barrier=state["capture_barrier"],
        ),
        "execution_authority": False,
    }
