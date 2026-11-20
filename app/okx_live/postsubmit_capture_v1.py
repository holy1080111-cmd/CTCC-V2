"""Original Live lifecycle GET capture, separate from pure constructor DATA."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import live_account_current_chain_v1 as current
from app.exchange.okx.live_account_config_custody_v1 import (
    _read_custodied_live_account_config,
)
from app.exchange.okx.live_account_config_observation_v1 import (
    _stamp,
    verify_live_account_config_readback_v1,
)
from app.exchange.okx.live_account_raw_page_v1 import (
    verify_live_account_raw_page_readback_v1,
)
from app.exchange.okx.live_trade_read_page_v1 import (
    observe_live_trade_read_page_v1,
    select_live_trade_read_v1,
    verify_live_trade_read_page_v1,
)
from app.okx_live.lifecycle_custody_v1 import read_live_lifecycle_blob_v1

_CAPTURE_KEY_V1 = object()


@dataclass(frozen=True, slots=True, repr=False)
class OriginalLiveLifecycleCaptureV1:
    order: object
    fills: tuple
    protection: object
    config_before: object
    config_after: object
    current_pages: tuple
    current_custody_sha256: str
    raw_custody_sha256: str
    validated_at: object
    tick_size: Decimal
    contract_value: Decimal
    contract_multiplier: Decimal
    contract_value_currency: str
    execution_authority: bool = False


def _need(condition):
    if condition is not True:
        raise ValueError("native_live_postsubmit_capture_unavailable") from None


def _rows(page):
    document = decode(page.raw_body, maximum=8 * 1024 * 1024)
    _need(
        type(document) is dict
        and document.get("code") == "0"
        and type(document.get("data")) is list
    )
    return document["data"]


def _current_original_pages(root, attempt, receipt, lease):
    # Reopen the exact ordered chain. No source flags or saved receipt can
    # substitute for the original raw files, terminal cursors and lease replay.
    current.read_live_current_account_v1(
        root, attempt_id=attempt, expected_custody_sha256=receipt["custody_sha256"]
    )
    _, before = _read_custodied_live_account_config(
        root / attempt,
        attempt_id=current._CONFIG_BEFORE,
        expected_custody_sha256=receipt["config_before_custody_sha256"],
    )
    _, after = _read_custodied_live_account_config(
        root / attempt,
        attempt_id=current._CONFIG_AFTER,
        expected_custody_sha256=receipt["config_after_custody_sha256"],
    )
    verify_live_account_config_readback_v1(before, lease=lease)
    verify_live_account_config_readback_v1(after, lease=lease)
    with current._root_context(root) as base, base.child(attempt) as directory:
        plan = current._read(directory, "observation.json")
        instruments = current._instruments(decode(plan, maximum=8192)["instruments"])
        _need(
            plan
            == current._plan(
                attempt,
                instruments,
                lease_binding_id=lease.binding_id,
                origin=lease._pin.origin,
            )
        )
        coverage = current._CurrentCoverage(before, instruments)
        previous, pages = sha(plan), []
        for index in range(receipt["page_count"]):
            selector = coverage.selector()
            _need(selector is not None)
            with directory.child(current._page_name(index)) as page_directory:
                page, previous = current._read_page(
                    page_directory, index, previous, selector
                )
            verify_live_account_raw_page_readback_v1(page, lease=lease)
            coverage.accept(page)
            pages.append(page)
        coverage.finish(
            after, current.ClockStamp.model_validate(receipt["capture_completed"])
        )
        _need(coverage.selector() is None and previous == receipt["last_page_sha256"])
    return before, after, tuple(pages)


def _metadata(pages, instrument_id):
    matching = [
        row
        for page in pages
        if page.selector.stream == "account_instruments"
        for row in _rows(page)
        if row.get("instId") == instrument_id
    ]
    _need(len(matching) == 1)
    row = matching[0]
    values = []
    for field in ("tickSz", "ctVal", "ctMult"):
        _need(type(row.get(field)) is str and 0 < len(row[field]) <= 80)
        value = Decimal(row[field])
        _need(value.is_finite() and value > 0)
        values.append(value)
    base = instrument_id.split("-", 1)[0]
    _need(
        row.get("ctType") == "linear"
        and row.get("settleCcy") == "USDT"
        and row.get("ctValCcy") == base
    )
    return *values, row["ctValCcy"]


async def capture_owned_live_postsubmit_pages_v1(owner, *, key):
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(key is _CAPTURE_KEY_V1)
    state = lifecycle._record(owner)
    _need(state["phase"] == "capturing" and lifecycle._ACTIVE_CAPTURE.get() is owner)
    client, lease, intent = state["client"], state["lease"], state["intent"]
    lease.verify_current()
    order = await observe_live_trade_read_page_v1(
        client,
        lease,
        select_live_trade_read_v1(
            "order",
            instrument_id=intent.instrument_id,
            client_order_id=intent.client_order_id,
        ),
    )
    verify_live_trade_read_page_v1(order, lease=lease)
    rows = _rows(order)
    _need(len(rows) == 1 and rows[0].get("clOrdId") == intent.client_order_id)
    order_id = rows[0].get("ordId")
    fills, after, seen = [], None, set()
    for _ in range(64):
        page = await observe_live_trade_read_page_v1(
            client,
            lease,
            select_live_trade_read_v1(
                "fills",
                instrument_id=intent.instrument_id,
                order_id=order_id,
                after_bill_id=after,
            ),
        )
        verify_live_trade_read_page_v1(page, lease=lease)
        fills.append(page)
        incoming = _rows(page)
        if not incoming:
            break
        ids = [row["billId"] for row in incoming]
        _need(not seen.intersection(ids) and len(ids) == len(set(ids)))
        _need(after is None or all(int(item) < int(after) for item in ids))
        seen.update(ids)
        after = ids[-1]
    else:
        _need(False)
    attempt = state["capture_id"]
    receipt = await current.collect_live_current_account_v1(
        client, lease, private_root=state["source_root"], attempt_id=attempt
    )
    _need(receipt["synthetic_mock"] is False)
    before, after_config, pages = _current_original_pages(
        state["source_root"], attempt, receipt, lease
    )
    protection = None
    # A definite canceled zero-fill has no attached active protection. Every
    # other result requires an exact algo query, including already-fired OCOs.
    if not (rows[0].get("state") == "canceled" and rows[0].get("accFillSz") == "0"):
        protection = await observe_live_trade_read_page_v1(
            client,
            lease,
            select_live_trade_read_v1(
                "algo",
                instrument_id=intent.instrument_id,
                algo_client_id=intent.protection_client_id,
            ),
        )
        verify_live_trade_read_page_v1(protection, lease=lease)
    lease.verify_current()
    raw_records = state["raw_captures"]
    _need(raw_records)
    for path, digest, body_digest, _, _ in raw_records:
        document = decode(
            read_live_lifecycle_blob_v1(state["evidence_root"], digest, domain="raw"),
            maximum=12 * 1024 * 1024,
        )
        _need(
            path.name == digest + ".json" and document["raw_body_sha256"] == body_digest
        )
    bank = sha(canonical([item[1] for item in raw_records]))
    return OriginalLiveLifecycleCaptureV1(
        order,
        tuple(fills),
        protection,
        before,
        after_config,
        pages,
        receipt["custody_sha256"],
        bank,
        _stamp(),
        *_metadata(pages, intent.instrument_id),
    )
