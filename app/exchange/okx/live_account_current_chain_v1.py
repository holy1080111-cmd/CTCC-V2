"""Capture the current Live account streams into a private, ordered page chain."""

from __future__ import annotations

import ipaddress
import re
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from app.demo_automation.native_private_root_v2 import mkdir_private_evidence_child_v2
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    validate_stamps,
)
from app.exchange.okx.live_account_config_custody_v1 import (
    _check_directory,
    _check_name,
    _decode_headers,
    _encode_headers,
    _publish,
    _read,
    _read_custodied_live_account_config,
    _read_files,
    capture_live_account_config_to_private_custody_v1,
)
from app.exchange.okx.live_account_config_observation_v1 import (
    MAX_BODY,
    _checked_response_headers,
    _stamp,
    _validate_signed_time,
    verify_live_account_config_readback_v1,
)
from app.exchange.okx.live_account_raw_page_v1 import (
    LiveAccountRawPageV1,
    _checked_rows,
    live_account_raw_page_receipt_document_v1,
    observe_live_account_raw_page_v1,
    select_live_account_raw_page_v1,
    verify_live_account_raw_page_readback_v1,
)
from app.exchange.okx.live_read_lease_v1 import (
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.trade_qualification.account_native_proof_storage import _root_context

SCHEMA = "ctcc.live_account_current_raw_chain.v1"
_CONFIG_BEFORE = "0" * 32
_CONFIG_AFTER = "1" * 32
_PAGE_CAP = 128
_STREAM_PAGE_CAP = 16
_PAGE_FILES = frozenset({"body.raw", "headers.raw", "observation.json"})
_ID = re.compile(r"[1-9][0-9]{0,39}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SWAP = re.compile(r"[A-Z0-9]{1,20}-USDT-SWAP")
_DECIMAL = re.compile(r"-?[0-9]{1,40}(?:\.[0-9]{1,24})?")
_ALGO_STREAMS = tuple(
    "algo_" + kind
    for kind in (
        "conditional",
        "oco",
        "chase",
        "trigger",
        "move_order_stop",
        "iceberg",
        "twap",
        "smart_iceberg",
    )
)
_PENDING_STREAMS = ("orders_pending", *_ALGO_STREAMS)
_LEVERAGE_STREAMS = ("leverage_cross", "leverage_isolated")
_STREAMS = (
    "account_position_risk",
    "balance",
    "positions",
    *_PENDING_STREAMS,
)


class LiveCurrentAccountChainError(ValueError):
    """Fixed capture refusal without provider fields or private paths."""


def _deny(code: str) -> None:
    raise LiveCurrentAccountChainError(code) from None


def _rows(raw: bytes) -> list[dict]:
    payload = decode(raw, maximum=MAX_BODY)
    if (
        type(payload) is not dict
        or payload.get("code") != "0"
        or type(payload.get("data")) is not list
        or any(type(row) is not dict for row in payload["data"])
    ):
        _deny("live_current_account_rows_invalid")
    return payload["data"]


def _one(rows: list[dict]) -> dict:
    if len(rows) != 1:
        _deny("live_current_account_row_count_invalid")
    return rows[0]


def _decimal(value) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        _deny("live_current_account_numeric_field_invalid")
    return Decimal(value)


@dataclass(frozen=True, slots=True, repr=False)
class _ConfigIdentity:
    uid: str
    main_uid: str
    account_mode: str
    position_mode: str
    permissions: frozenset[str]
    ip_binding: frozenset[str]
    usd_contract_settlement: str | None


def _check_config(raw: bytes) -> _ConfigIdentity:
    row = _one(_rows(raw))
    permissions = row["perm"].split(",")
    bound = row["ip"].split(",")
    usd_settlement = row.get("settleCcy")
    if (
        row["acctLv"] not in ("2", "3")
        or row["posMode"] not in ("net_mode", "long_short_mode")
        or len(permissions) != 2
        or set(permissions) != {"read_only", "trade"}
        or not 1 <= len(bound) <= 20
        or len({ipaddress.ip_address(value) for value in bound}) != len(bound)
        or (usd_settlement is not None and type(usd_settlement) is not str)
    ):
        _deny("live_current_account_config_invalid")
    return _ConfigIdentity(
        row["uid"],
        row["mainUid"],
        row["acctLv"],
        row["posMode"],
        frozenset(permissions),
        frozenset(bound),
        usd_settlement,
    )


def _check_instrument(row, instrument) -> None:
    if (
        row["instId"] != instrument
        or row["instType"] != "SWAP"
        or row["settleCcy"] != "USDT"
        or row["ctType"] != "linear"
        or row["ctValCcy"] != instrument.split("-")[0]
        or row["state"] != "live"
        or any(
            _decimal(row[field]) <= 0
            for field in ("ctVal", "ctMult", "tickSz", "lotSz", "minSz", "lever")
        )
    ):
        _deny("live_current_account_instrument_invalid")


def _check_leverage_rows(rows, instruments, mode, position_mode) -> None:
    sides = ("net",) if position_mode == "net_mode" else ("long", "short")
    expected = {(instrument, side) for instrument in instruments for side in sides}
    seen = set()
    for row in rows:
        key = row["instId"], row["posSide"]
        if (
            row["mgnMode"] != mode
            or key not in expected
            or key in seen
            or _decimal(row["lever"]) <= 0
        ):
            _deny("live_current_account_leverage_invalid")
        seen.add(key)
    if seen != expected:
        _deny("live_current_account_leverage_missing")


def _check_unpaged(selector, rows, instruments, position_mode) -> bool:
    stream = selector.stream
    if stream == "account_position_risk":
        row = _one(rows)
        if any(
            type(row[field]) is not list
            or any(type(item) is not dict for item in row[field])
            for field in ("balData", "posData")
        ):
            _deny("live_current_account_position_risk_invalid")
        return not row["posData"]
    if stream == "balance":
        row = _one(rows)
        if (
            _decimal(row["totalEq"]) < 0
            or type(row["details"]) is not list
            or any(type(item) is not dict for item in row["details"])
        ):
            _deny("live_current_account_balance_invalid")
    elif stream == "positions":
        position_ids = [row["posId"] for row in rows]
        if (
            any(type(row["instId"]) is not str or not row["instId"] for row in rows)
            or any(
                type(value) is not str or _ID.fullmatch(value) is None
                for value in position_ids
            )
            or len(position_ids) != len(set(position_ids))
        ):
            _deny("live_current_account_position_invalid")
        return all(_decimal(row["pos"]) == 0 for row in rows)
    elif stream == "account_instruments":
        _check_instrument(_one(rows), selector.instrument_id)
    elif stream in _LEVERAGE_STREAMS:
        _check_leverage_rows(
            rows, instruments, stream.removeprefix("leverage_"), position_mode
        )
    return True


def _next_cursor(page, selector, rows) -> str | None:
    ids = tuple(row[selector.cursor_type] for row in rows)
    if (
        len(ids) > 100
        or ids != page.row_ids
        or page.empty_page is not (not ids)
        or any(type(value) is not str or _ID.fullmatch(value) is None for value in ids)
    ):
        _deny("live_current_account_cursor_invalid")
    if not ids:
        return None
    values = tuple(map(int, ids))
    if (selector.after is not None and values[0] >= int(selector.after)) or any(
        left <= right for left, right in pairwise(values)
    ):
        _deny("live_current_account_cursor_not_advancing")
    return ids[-1]


@contextmanager
def _attempt_at(root, attempt_id, identities):
    with _root_context(root) as base, base.child(attempt_id) as attempt:
        if (_check_directory(base), _check_directory(attempt)) != identities:
            _deny("live_current_account_custody_changed")
        yield attempt


def _page_metadata(index, previous, page) -> bytes:
    return canonical(
        {
            "schema_version": SCHEMA,
            "page_index": index,
            "previous_page_sha256": previous,
            "source_receipt_sha256": page.receipt_sha256,
            "source": live_account_raw_page_receipt_document_v1(page),
        }
    )


def _save_page(root, attempt_id, identities, name, index, previous, page, lease) -> str:
    metadata = _page_metadata(index, previous, page)
    files = {
        "body.raw": page.raw_body,
        "headers.raw": _encode_headers(page.raw_headers),
        "observation.json": metadata,
    }
    with _attempt_at(root, attempt_id, identities) as attempt:
        mkdir_private_evidence_child_v2(attempt, name)
        with attempt.child(name) as directory:
            page_identity = _check_directory(directory)
            if directory.names():
                _deny("live_current_account_page_exists")
            for filename, raw in files.items():
                _publish(directory, filename, raw)
    with (
        _attempt_at(root, attempt_id, identities) as attempt,
        attempt.child(name) as directory,
    ):
        if _check_directory(directory) != page_identity:
            _deny("live_current_account_custody_changed")
        stored = _read_files(directory, _PAGE_FILES)
    if stored != files:
        _deny("live_current_account_page_readback_changed")
    replayed = replace(
        page,
        raw_body=stored["body.raw"],
        raw_headers=_decode_headers(stored["headers.raw"]),
    )
    verify_live_account_raw_page_readback_v1(replayed, lease=lease)
    return sha(metadata)


def _settings_pin(client, lease, instruments) -> None:
    lease.verify_current()
    if tuple(sorted(client.settings.okx_live_allowed_symbol_list)) != instruments:
        _deny("live_current_account_instrument_policy_changed")


def _advance_clock(first, previous, current) -> None:
    validate_stamps(tuple(stamp.model_dump() for stamp in (first, previous, *current)))


async def _config_capture(client, lease, root, attempt_id):
    receipt = await capture_live_account_config_to_private_custody_v1(
        client, lease, private_root=root, attempt_id=attempt_id
    )
    _, observed = _read_custodied_live_account_config(
        root, attempt_id=attempt_id, expected_custody_sha256=receipt["custody_sha256"]
    )
    verify_live_account_config_readback_v1(observed, lease=lease)
    return receipt["custody_sha256"], observed


def _instruments(values) -> tuple[str, ...]:
    if (
        not 1 <= len(values) <= 20
        or any(
            type(value) is not str or _SWAP.fullmatch(value) is None for value in values
        )
        or tuple(sorted(set(values))) != tuple(values)
    ):
        _deny("live_current_account_instrument_policy_invalid")
    return tuple(values)


def _requests(instruments):
    return (
        *((stream, None) for stream in _STREAMS),
        *(("account_instruments", instrument) for instrument in instruments),
        *((stream, None) for stream in _LEVERAGE_STREAMS),
    )


def _plan(attempt_id, instruments, *, lease_binding_id, origin) -> bytes:
    return canonical(
        {
            "schema_version": SCHEMA,
            "attempt_id": attempt_id,
            "environment": "live",
            "lease_binding_id": lease_binding_id,
            "origin": origin,
            "instrument_settlement_currency": "USDT",
            "streams": [list(request) for request in _requests(instruments)],
            "instruments": list(instruments),
            "max_pages": _PAGE_CAP,
            "max_pages_per_stream": _STREAM_PAGE_CAP,
        }
    )


class _CurrentCoverage:
    def __init__(self, before, instruments):
        self.before = before
        self.config = _check_config(before.raw_body)
        self.instruments = instruments
        self.streams = _requests(instruments)
        self.previous_stamp = before.stamps[-1]
        self.stream_index = 0
        self.pages = 0
        self.stream_pages = 0
        self.after = None
        self.flat = True

    def selector(self):
        if self.stream_index == len(self.streams):
            return None
        if self.pages == _PAGE_CAP or self.stream_pages == _STREAM_PAGE_CAP:
            _deny("live_current_account_terminal_page_missing")
        stream, instrument = self.streams[self.stream_index]
        return select_live_account_raw_page_v1(
            stream,
            after=self.after,
            instrument_id=instrument,
            leverage_instrument_ids=self.instruments
            if stream in _LEVERAGE_STREAMS
            else (),
        )

    def check_time(self, sample) -> None:
        _advance_clock(self.before.stamps[0], self.previous_stamp, (sample,))

    def accept(self, page) -> None:
        selector = self.selector()
        if (
            selector is None
            or page.selector != selector
            or page.origin != self.before.origin
            or page.lease_binding_id != self.before.lease_binding_id
            or page.synthetic_mock is not self.before.synthetic_mock
        ):
            _deny("live_current_account_selector_changed")
        _advance_clock(self.before.stamps[0], self.previous_stamp, page.stamps)
        self.previous_stamp = page.stamps[-1]
        self.pages += 1
        self.stream_pages += 1
        rows = _rows(page.raw_body)
        if selector.cursor_type is None:
            self.flat = (
                _check_unpaged(
                    selector, rows, self.instruments, self.config.position_mode
                )
                and self.flat
            )
        else:
            self.after = _next_cursor(page, selector, rows)
            self.flat = self.flat and not rows
            if self.after is not None:
                return
        self.stream_pages = 0
        self.stream_index += 1

    def finish(self, after, capture_completed) -> None:
        if self.selector() is not None:
            _deny("live_current_account_stream_missing")
        _advance_clock(
            self.before.stamps[0],
            self.previous_stamp,
            (*after.stamps, capture_completed),
        )
        if (
            after.origin != self.before.origin
            or after.lease_binding_id != self.before.lease_binding_id
            or after.synthetic_mock is not self.before.synthetic_mock
            or _check_config(after.raw_body) != self.config
        ):
            _deny("live_current_account_config_changed")


def _receipt(
    attempt_id, identities, plan, before, after, previous, coverage, capture_completed
) -> bytes:
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
            "capture_completed": capture_completed.model_dump(),
            "no_open_positions_or_pending_orders_observed": coverage.flat,
            "synthetic_mock": coverage.before.synthetic_mock,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    )


def _page_name(index: int) -> str:
    return f"p-{index:06d}"


def _inventory(count: int) -> set[str]:
    return {
        "observation.json",
        _CONFIG_BEFORE,
        _CONFIG_AFTER,
        *map(_page_name, range(count)),
    }


def _read_page(directory, index, previous, selector):
    files = _read_files(directory, _PAGE_FILES)
    metadata = files["observation.json"]
    source = decode(metadata, maximum=8192)["source"]
    body = files["body.raw"]
    headers = _decode_headers(files["headers.raw"])
    declared = _checked_response_headers(headers, private_tokens=())
    canonical_body, row_ids, row_count, empty = _checked_rows(
        body, selector, private_tokens=()
    )
    page = LiveAccountRawPageV1(
        selector=selector,
        raw_body=body,
        raw_headers=headers,
        row_ids=row_ids,
        row_count=row_count,
        empty_page=empty,
        signed_at=source["signed_at"],
        stamps=tuple(ClockStamp.model_validate(stamp) for stamp in source["stamps"]),
        lease_binding_id=source["lease_binding_id"],
        origin=source["origin"],
        raw_sha256=sha(body),
        canonical_sha256=sha(canonical_body),
        headers_sha256=sha(files["headers.raw"]),
        request_signature_sha256=source["request_signature_sha256"],
        tls_certificate_sha256=source["tls_certificate_sha256"],
        receipt_sha256=sha(canonical(source)),
        synthetic_mock=source["synthetic_mock"],
    )
    if (
        metadata != _page_metadata(index, previous, page)
        or (declared is not None and declared != len(body))
        or len(page.stamps) != 6
        or _DIGEST.fullmatch(page.request_signature_sha256) is None
        or (page.tls_certificate_sha256 is None) is not page.synthetic_mock
        or (
            page.tls_certificate_sha256 is not None
            and _DIGEST.fullmatch(page.tls_certificate_sha256) is None
        )
    ):
        _deny("live_current_account_page_readback_changed")
    _validate_signed_time(page.signed_at, page.stamps[0], page.stamps[1])
    return page, sha(metadata)


def read_live_current_account_v1(
    private_root: Path, *, attempt_id: str, expected_custody_sha256: str
) -> dict:
    """Replay current coverage from private bytes against a separately retained digest."""
    _check_name(attempt_id)
    if (
        type(expected_custody_sha256) is not str
        or _DIGEST.fullmatch(expected_custody_sha256) is None
    ):
        _deny("live_current_account_expected_digest_required")
    try:
        with _root_context(private_root) as base, base.child(attempt_id) as attempt:
            identities = _check_directory(base), _check_directory(attempt)
            raw_receipt = _read(attempt, "custody.json")
            if sha(raw_receipt) != expected_custody_sha256:
                _deny("live_current_account_receipt_readback_changed")
            receipt = decode(raw_receipt, maximum=2048)
            capture_completed = ClockStamp.model_validate(receipt["capture_completed"])
            plan = _read(attempt, "observation.json")
            count = receipt["page_count"]
            if type(count) is not int or not 1 <= count <= _PAGE_CAP:
                _deny("live_current_account_page_count_invalid")
            expected = _inventory(count) | {"custody.json"}
            if set(attempt.names()) != expected:
                _deny("live_current_account_inventory_invalid")
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
        instruments = _instruments(decode(plan, maximum=8192)["instruments"])
        if plan != _plan(
            attempt_id,
            instruments,
            lease_binding_id=before.lease_binding_id,
            origin=before.origin,
        ):
            _deny("live_current_account_plan_changed")
        coverage = _CurrentCoverage(before, instruments)
        previous = sha(plan)
        with _attempt_at(private_root, attempt_id, identities) as attempt:
            for index in range(count):
                selector = coverage.selector()
                if selector is None:
                    _deny("live_current_account_extra_page")
                with attempt.child(_page_name(index)) as directory:
                    _check_directory(directory)
                    page, previous = _read_page(directory, index, previous, selector)
                coverage.accept(page)
            coverage.finish(after, capture_completed)
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
                    capture_completed,
                )
            ):
                _deny("live_current_account_receipt_readback_changed")
        del receipt["directory_identities"]
        return {**receipt, "custody_sha256": expected_custody_sha256}
    except LiveCurrentAccountChainError:
        raise
    except Exception:  # noqa: BLE001 - storage and parser details remain private
        _deny("live_current_account_readback_incomplete")


async def collect_live_current_account_v1(
    client: OkxLivePinnedGetClientV1,
    lease: LivePinnedGetLeaseV1,
    *,
    private_root: Path,
    attempt_id: str,
) -> dict:
    """Collect explicit current coverage and keep every accepted raw page."""
    _check_name(attempt_id)
    try:
        instruments = _instruments(sorted(client.settings.okx_live_allowed_symbol_list))
        _settings_pin(client, lease, instruments)
        plan = _plan(
            attempt_id,
            instruments,
            lease_binding_id=lease.binding_id,
            origin=lease._pin.origin,
        )
        with _root_context(private_root) as base:
            base_id = _check_directory(base)
            mkdir_private_evidence_child_v2(base, attempt_id)
            with base.child(attempt_id) as attempt:
                identities = base_id, _check_directory(attempt)
                if attempt.names():
                    _deny("live_current_account_attempt_not_empty")
                _publish(attempt, "observation.json", plan)
        attempt_path = private_root / attempt_id
        before_digest, before = await _config_capture(
            client, lease, attempt_path, _CONFIG_BEFORE
        )
        coverage = _CurrentCoverage(before, instruments)
        previous_digest = sha(plan)
        while (selector := coverage.selector()) is not None:
            _settings_pin(client, lease, instruments)
            coverage.check_time(_stamp())
            page = await observe_live_account_raw_page_v1(client, lease, selector)
            _settings_pin(client, lease, instruments)
            verify_live_account_raw_page_readback_v1(page, lease=lease)
            index = coverage.pages
            previous_digest = _save_page(
                private_root,
                attempt_id,
                identities,
                _page_name(index),
                index,
                previous_digest,
                page,
                lease,
            )
            coverage.accept(page)
        with _attempt_at(private_root, attempt_id, identities):
            _settings_pin(client, lease, instruments)
            coverage.check_time(_stamp())
        after_digest, after_config = await _config_capture(
            client, lease, attempt_path, _CONFIG_AFTER
        )
        _settings_pin(client, lease, instruments)
        capture_completed = _stamp()
        coverage.finish(after_config, capture_completed)
        raw_receipt = _receipt(
            attempt_id,
            identities,
            plan,
            before_digest,
            after_digest,
            previous_digest,
            coverage,
            capture_completed,
        )
        with _attempt_at(private_root, attempt_id, identities) as attempt:
            if (
                set(attempt.names()) != _inventory(coverage.pages)
                or _read(attempt, "observation.json") != plan
            ):
                _deny("live_current_account_inventory_invalid")
            _publish(attempt, "custody.json", raw_receipt)
        replayed = read_live_current_account_v1(
            private_root,
            attempt_id=attempt_id,
            expected_custody_sha256=sha(raw_receipt),
        )
        _settings_pin(client, lease, instruments)
        _advance_clock(before.stamps[0], capture_completed, (_stamp(),))
        return replayed
    except LiveCurrentAccountChainError:
        raise
    except Exception:  # noqa: BLE001 - remote and local private fields stay in custody
        _deny("live_current_account_capture_incomplete")
