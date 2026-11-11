"""Read and preserve a single pinned Live account history partition."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.demo_automation.native_private_root_v2 import mkdir_private_evidence_child_v2
from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.exchange.okx.live_account_config_custody_v1 import (
    _check_directory,
    _check_name,
    _decode_headers,
    _encode_headers,
    _publish,
    _read,
    _read_custodied_live_account_config,
    _read_files,
)
from app.exchange.okx.live_account_config_observation_v1 import (
    MAX_HISTORY_BODY,
    _stamp,
)
from app.exchange.okx.live_account_current_chain_v1 import (
    _advance_clock,
    _attempt_at,
    _check_config,
    _config_capture,
    _inventory,
    _page_name,
)
from app.exchange.okx.live_account_history_page_v1 import (
    LiveAccountHistorySelectorV1,
    live_account_history_page_receipt_document_v1,
    observe_live_account_history_page_v1,
    reconstruct_live_account_history_page_v1,
    select_live_account_history_page_v1,
    verify_live_account_history_page_readback_v1,
)
from app.exchange.okx.live_read_lease_v1 import (
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.trade_qualification.account_native_proof_storage import _root_context

SCHEMA = "ctcc.live_account_history_raw_chain.v1"
_CONFIG_BEFORE = "0" * 32
_CONFIG_AFTER = "1" * 32
_PAGE_CAP = 16
_PAGE_FILES = frozenset({"body.raw", "headers.raw", "observation.json"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MS = re.compile(r"[1-9][0-9]{12}")
_NS_PER_MS = 1_000_000
_DAY_MS = 86_400_000
# The archive scope stays inside even the shortest three calendar months.
_MAX_AGE_DAYS = {
    "fills_recent": 3,
    "bills_recent": 7,
    "orders_recent": 7,
    "fills_history": 84,
    "bills_archive": 84,
    "orders_archive": 84,
}
# The small timer margin leaves at least 500 ms between native measurements.
_PAGE_INTERVAL_SECONDS = 0.51


class LiveAccountHistoryChainError(ValueError):
    """Fixed failure code. Private source fields stay in custody."""


def _deny(code: str) -> None:
    raise LiveAccountHistoryChainError(code) from None


def _partition(selector: LiveAccountHistorySelectorV1) -> dict:
    return {
        "kind": selector.kind,
        "begin_ms": selector.begin_ms,
        "end_ms": selector.end_ms,
        "inst_type": selector.inst_type,
        "ord_type": selector.ord_type,
        "state": selector.state,
    }


def _plan(attempt_id, selector, *, lease_binding_id, origin) -> bytes:
    return canonical(
        {
            "schema_version": SCHEMA,
            "attempt_id": attempt_id,
            "environment": "live",
            "origin": origin,
            "lease_binding_id": lease_binding_id,
            "partition": _partition(selector),
            "max_pages": _PAGE_CAP,
            "minimum_page_interval_ms": 500,
        }
    )


def _require_window(selector, *, start, through) -> None:
    if selector.kind == "algo_history":
        return
    if (int(selector.end_ms) + 1) * _NS_PER_MS > start.utc_ns or through.utc_ns > (
        int(selector.begin_ms) + _MAX_AGE_DAYS[selector.kind] * _DAY_MS
    ) * _NS_PER_MS:
        _deny("live_history_window_outside_retention_or_cutoff")


def _millis(row, name) -> int:
    value = row.get(name)
    if type(value) is not str or _MS.fullmatch(value) is None:
        _deny("live_history_row_timestamp_invalid")
    return int(value)


def _check_row_times(page) -> None:
    selector = page.selector
    received_ms = page.stamps[3].utc_ns // _NS_PER_MS  # Raw body completed.
    bounds = (
        None
        if selector.begin_ms is None
        else (int(selector.begin_ms), int(selector.end_ms))
    )
    rows = decode(page.raw_body, maximum=MAX_HISTORY_BODY)["data"]
    for row in rows:
        if selector.kind.startswith("fills_"):
            filtered = _millis(row, "ts")  # Record generation, not execution.
            event = _millis(row, "fillTime")
        elif selector.kind.startswith("bills_"):
            filtered = event = _millis(row, "ts")  # Balance update only.
        else:
            filtered = _millis(row, "cTime")
            event = _millis(row, "uTime")
        if (
            max(filtered, event) > received_ms
            or (selector.kind.startswith("fills_") and event > filtered)
            or (selector.cursor_type in {"ordId", "algoId"} and event < filtered)
            or (bounds is not None and not bounds[0] <= filtered <= bounds[1])
        ):
            _deny("live_history_row_timestamp_outside_source_scope")
        if (
            selector.kind == "algo_history"
            and row.get("triggerTime") not in (None, "", "0")
            and not filtered <= _millis(row, "triggerTime") <= received_ms
        ):
            _deny("live_history_row_timestamp_outside_source_scope")


class _HistoryCoverage:
    def __init__(self, before, initial):
        self.before = before
        self.config = _check_config(before.raw_body)
        self.initial = initial
        self.previous_stamp = before.stamps[-1]
        self.pages = 0
        self.rows = 0
        self.after = None
        self.terminal = False
        _require_window(initial, start=before.stamps[0], through=self.previous_stamp)

    def selector(self) -> LiveAccountHistorySelectorV1 | None:
        if self.terminal:
            return None
        if self.pages >= _PAGE_CAP:
            _deny("live_history_terminal_page_missing")
        return select_live_account_history_page_v1(
            **_partition(self.initial), after=self.after
        )

    def check_time(self, sample) -> None:
        _advance_clock(self.before.stamps[0], self.previous_stamp, (sample,))
        _require_window(self.initial, start=self.before.stamps[0], through=sample)

    def accept(self, page) -> None:
        if (
            page.selector != self.selector()
            or page.origin != self.before.origin
            or page.lease_binding_id != self.before.lease_binding_id
            or page.synthetic_mock is not self.before.synthetic_mock
        ):
            _deny("live_history_selector_or_account_changed")
        _advance_clock(self.before.stamps[0], self.previous_stamp, page.stamps)
        if (
            self.pages
            and page.stamps[0].monotonic_ns - self.previous_stamp.monotonic_ns
            < 500_000_000
        ):
            _deny("live_history_page_interval_invalid")
        _require_window(
            self.initial, start=self.before.stamps[0], through=page.stamps[-1]
        )
        _check_row_times(page)
        self.previous_stamp = page.stamps[-1]
        self.pages += 1
        self.rows += page.row_count
        self.terminal = page.empty_page
        if page.row_ids:
            self.after = page.row_ids[-1]

    def finish(self, after, completed) -> None:
        if not self.terminal:
            _deny("live_history_terminal_page_missing")
        _advance_clock(
            self.before.stamps[0], self.previous_stamp, (*after.stamps, completed)
        )
        _require_window(self.initial, start=self.before.stamps[0], through=completed)
        if (
            after.origin != self.before.origin
            or after.lease_binding_id != self.before.lease_binding_id
            or after.synthetic_mock is not self.before.synthetic_mock
            or _check_config(after.raw_body) != self.config
        ):
            _deny("live_history_account_config_changed")


def _page_metadata(index, previous, page) -> bytes:
    return canonical(
        {
            "schema_version": SCHEMA,
            "page_index": index,
            "previous_page_sha256": previous,
            "source_receipt_sha256": page.receipt_sha256,
            "request_query": [list(item) for item in page.selector.query],
            "first_row_id": page.row_ids[0] if page.row_ids else None,
            "last_row_id": page.row_ids[-1] if page.row_ids else None,
            "source": live_account_history_page_receipt_document_v1(page),
        }
    )


def _read_page(directory, index, previous, selector):
    files = _read_files(directory, _PAGE_FILES, body_limit=MAX_HISTORY_BODY)
    metadata = files["observation.json"]
    source = decode(metadata, maximum=8192)["source"]
    page = reconstruct_live_account_history_page_v1(
        files["body.raw"], _decode_headers(files["headers.raw"]), source, selector
    )
    if metadata != _page_metadata(index, previous, page):
        _deny("live_history_page_readback_changed")
    return page, sha(metadata)


def _save_page(root, attempt_id, identities, index, previous, page, lease) -> str:
    name = _page_name(index)
    files = {
        "body.raw": page.raw_body,
        "headers.raw": _encode_headers(page.raw_headers),
        "observation.json": _page_metadata(index, previous, page),
    }
    with _attempt_at(root, attempt_id, identities) as attempt:
        mkdir_private_evidence_child_v2(attempt, name)
        with attempt.child(name) as directory:
            page_identity = _check_directory(directory)
            for filename, raw in files.items():
                _publish(directory, filename, raw, body_limit=MAX_HISTORY_BODY)
    with (
        _attempt_at(root, attempt_id, identities) as attempt,
        attempt.child(name) as directory,
    ):
        if _check_directory(directory) != page_identity:
            _deny("live_history_page_custody_changed")
        replay, digest = _read_page(directory, index, previous, page.selector)
    if (
        digest != sha(files["observation.json"])
        or replay.raw_body != page.raw_body
        or replay.raw_headers != page.raw_headers
    ):
        _deny("live_history_page_readback_changed")
    verify_live_account_history_page_readback_v1(replay, lease=lease)
    return digest


def _receipt(
    attempt_id, identities, plan, before, after, previous, coverage, completed
):
    return canonical(
        {
            "schema_version": SCHEMA,
            "attempt_id": attempt_id,
            "directory_identities": [list(identity) for identity in identities],
            "plan_sha256": sha(plan),
            "config_before_custody_sha256": before,
            "config_after_custody_sha256": after,
            "last_page_sha256": previous,
            "page_count": coverage.pages,
            "row_count": coverage.rows,
            "capture_completed": completed.model_dump(),
            "explicit_terminal_page_observed": True,
            "synthetic_mock": coverage.before.synthetic_mock,
            "history_complete": False,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    )


def read_live_account_history_v1(
    private_root: Path, *, attempt_id: str, expected_custody_sha256: str
) -> dict:
    """Independently replay a terminated partition against a pinned custody digest."""
    _check_name(attempt_id)
    if (
        type(expected_custody_sha256) is not str
        or _DIGEST.fullmatch(expected_custody_sha256) is None
    ):
        _deny("live_history_expected_digest_required")
    try:
        with _root_context(private_root) as base, base.child(attempt_id) as attempt:
            identities = _check_directory(base), _check_directory(attempt)
            raw_receipt = _read(attempt, "custody.json")
            if sha(raw_receipt) != expected_custody_sha256:
                _deny("live_history_receipt_readback_changed")
            receipt = decode(raw_receipt, maximum=2048)
            completed = ClockStamp.model_validate(receipt["capture_completed"])
            plan = _read(attempt, "observation.json")
            count = receipt["page_count"]
            if type(count) is not int or not 1 <= count <= _PAGE_CAP:
                _deny("live_history_page_count_invalid")
            expected = _inventory(count) | {"custody.json"}
            if set(attempt.names()) != expected:
                _deny("live_history_inventory_invalid")
        attempt_path = private_root / attempt_id
        before_digest = receipt["config_before_custody_sha256"]
        after_digest = receipt["config_after_custody_sha256"]
        _, before = _read_custodied_live_account_config(
            attempt_path,
            attempt_id=_CONFIG_BEFORE,
            expected_custody_sha256=before_digest,
        )
        _, after = _read_custodied_live_account_config(
            attempt_path, attempt_id=_CONFIG_AFTER, expected_custody_sha256=after_digest
        )
        initial = select_live_account_history_page_v1(
            **decode(plan, maximum=8192)["partition"]
        )
        if plan != _plan(
            attempt_id,
            initial,
            lease_binding_id=before.lease_binding_id,
            origin=before.origin,
        ):
            _deny("live_history_plan_readback_changed")
        coverage = _HistoryCoverage(before, initial)
        previous = sha(plan)
        with _attempt_at(private_root, attempt_id, identities) as attempt:
            for index in range(count):
                selector = coverage.selector()
                if selector is None:
                    _deny("live_history_extra_page")
                with attempt.child(_page_name(index)) as directory:
                    _check_directory(directory)
                    page, previous = _read_page(directory, index, previous, selector)
                coverage.accept(page)
            coverage.finish(after, completed)
            if (
                set(attempt.names()) != expected
                or _read(attempt, "custody.json") != raw_receipt
                or _read(attempt, "observation.json") != plan
                or raw_receipt
                != _receipt(
                    attempt_id,
                    identities,
                    plan,
                    before_digest,
                    after_digest,
                    previous,
                    coverage,
                    completed,
                )
            ):
                _deny("live_history_receipt_readback_changed")
        del receipt["directory_identities"]
        return {**receipt, "custody_sha256": expected_custody_sha256}
    except LiveAccountHistoryChainError:
        raise
    except Exception:  # noqa: BLE001 - storage or provider data may be private
        _deny("live_history_readback_incomplete")


async def collect_live_account_history_v1(
    client: OkxLivePinnedGetClientV1,
    lease: LivePinnedGetLeaseV1,
    selector: LiveAccountHistorySelectorV1,
    *,
    private_root: Path,
    attempt_id: str,
) -> dict:
    """Collect one fixed history partition from its first page to an empty page."""
    _check_name(attempt_id)
    try:
        if (
            type(selector) is not LiveAccountHistorySelectorV1
            or selector != select_live_account_history_page_v1(**_partition(selector))
        ):
            _deny("live_history_initial_selector_invalid")
        lease.verify_current()
        started = _stamp()
        _require_window(selector, start=started, through=started)
        plan = _plan(
            attempt_id,
            selector,
            lease_binding_id=lease.binding_id,
            origin=lease._pin.origin,
        )
        with _root_context(private_root) as base:
            base_id = _check_directory(base)
            mkdir_private_evidence_child_v2(base, attempt_id)
            with base.child(attempt_id) as attempt:
                identities = base_id, _check_directory(attempt)
                if attempt.names():
                    _deny("live_history_attempt_not_empty")
                _publish(attempt, "observation.json", plan)
        attempt_path = private_root / attempt_id
        before_digest, before = await _config_capture(
            client, lease, attempt_path, _CONFIG_BEFORE
        )
        coverage = _HistoryCoverage(before, selector)
        previous = sha(plan)
        while (next_selector := coverage.selector()) is not None:
            if coverage.pages:
                await asyncio.sleep(_PAGE_INTERVAL_SECONDS)
            lease.verify_current()
            coverage.check_time(_stamp())
            page = await observe_live_account_history_page_v1(
                client, lease, next_selector
            )
            lease.verify_current()
            previous = _save_page(
                private_root,
                attempt_id,
                identities,
                coverage.pages,
                previous,
                page,
                lease,
            )
            coverage.accept(page)
        with _attempt_at(private_root, attempt_id, identities):
            lease.verify_current()
            coverage.check_time(_stamp())
        after_digest, after = await _config_capture(
            client, lease, attempt_path, _CONFIG_AFTER
        )
        lease.verify_current()
        completed = _stamp()
        coverage.finish(after, completed)
        raw_receipt = _receipt(
            attempt_id,
            identities,
            plan,
            before_digest,
            after_digest,
            previous,
            coverage,
            completed,
        )
        with _attempt_at(private_root, attempt_id, identities) as attempt:
            if (
                set(attempt.names()) != _inventory(coverage.pages)
                or _read(attempt, "observation.json") != plan
            ):
                _deny("live_history_inventory_invalid")
            _publish(attempt, "custody.json", raw_receipt)
        replayed = read_live_account_history_v1(
            private_root,
            attempt_id=attempt_id,
            expected_custody_sha256=sha(raw_receipt),
        )
        lease.verify_current()
        _advance_clock(before.stamps[0], completed, (_stamp(),))
        return replayed
    except LiveAccountHistoryChainError:
        raise
    except Exception:  # noqa: BLE001 - storage or provider data may be private
        _deny("live_history_capture_incomplete")
