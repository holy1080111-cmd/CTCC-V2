"""Owned, measured Live raw-source bridge. Public receipts remain DATA/DENY.

The original configured production GET client creates its own zero-retry TLS
transport. Every current/history partition retains raw bytes and page chains in
the existing private custody. This bridge never substitutes Demo source, claims
financial completeness or grants order authority.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from weakref import WeakKeyDictionary

from app.config.settings import Settings, get_settings
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    validate_stamps,
)
from app.exchange.okx import live_account_current_chain_v1 as current
from app.exchange.okx import live_account_history_chain_v1 as history
from app.exchange.okx.live_account_config_observation_v1 import _header_digest, _stamp
from app.exchange.okx.live_account_history_page_v1 import (
    HISTORY_ALGO_STATES,
    HISTORY_ALGO_TYPES,
    HISTORY_INST_TYPES,
    LiveAccountHistorySelectorV1,
    select_live_account_history_page_v1,
)
from app.exchange.okx.live_account_raw_page_v1 import LiveAccountRawPageSelectorV1
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from app.okx_live.micro_live_readonly_preflight_v1 import _both_configs

_SOURCE_KEY = object()
_SOURCES = WeakKeyDictionary()
_HEX = re.compile(r"[a-f0-9]{32}\Z")
_FAMILIES = ("fills_recent", "fills_history", "orders_recent", "orders_archive")
_ORIGINS = {"global": "https://openapi.okx.com", "eea": "https://eea.okx.com"}


def _utc_ns(value):
    _need(type(value) is datetime and value.tzinfo is UTC)
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1000


class LiveSourceBridgeError(ValueError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveSourceBridgeError("owned_live_source_unavailable") from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveSourcePlanV1:
    invocation_id: str
    region: str
    history_start: datetime
    history_end: datetime
    publication_barrier: ClockStamp
    max_current_age_ns: int = 5_000_000_000


def _selectors(plan):
    _need(
        type(plan) is LiveSourcePlanV1
        and type(plan.invocation_id) is str
        and _HEX.fullmatch(plan.invocation_id) is not None
        and type(plan.region) is str
        and plan.region in _ORIGINS
        and type(plan.publication_barrier) is ClockStamp
        and type(plan.max_current_age_ns) is int
        and 0 < plan.max_current_age_ns <= 5_000_000_000
    )
    _need(
        type(plan.history_start) is type(plan.history_end) is datetime
        and plan.history_start.tzinfo is plan.history_end.tzinfo is UTC
        and timedelta(days=7)
        <= plan.history_end - plan.history_start
        <= timedelta(days=28)
    )
    begin = str(_utc_ns(plan.history_start) // 1_000_000)
    # Recent endpoints have shorter venue retention than the account-wide
    # archive window. Keep a fixed explicit overlap, never request an older
    # unsupported recent window and never shorten the archive's full coverage.
    recent_begin = str(_utc_ns(plan.history_end - timedelta(days=1)) // 1_000_000)
    end = str(_utc_ns(plan.history_end) // 1_000_000)
    return (
        *(
            select_live_account_history_page_v1(
                kind,
                begin_ms=recent_begin if kind.endswith("_recent") else begin,
                end_ms=end,
                inst_type=product,
            )
            for kind in _FAMILIES
            for product in HISTORY_INST_TYPES
        ),
        *(
            select_live_account_history_page_v1(
                kind,
                begin_ms=recent_begin if kind.endswith("_recent") else begin,
                end_ms=end,
            )
            for kind in ("bills_recent", "bills_archive")
        ),
        *(
            select_live_account_history_page_v1(
                "algo_history", ord_type=kind, state=state
            )
            for kind in HISTORY_ALGO_TYPES
            for state in HISTORY_ALGO_STATES
        ),
        select_live_account_history_page_v1("positions_history"),
    )


def _configured(plan):
    settings = get_settings()
    _need(
        type(settings) is Settings
        and settings.environment == "production"
        and settings.trading_mode == "live"
        and settings.okx_live_enabled is True
        and settings.okx_live_rest_base_url == _ORIGINS[plan.region]
        and settings.okx_live_read_max_retries == 0
        and all(
            value is False
            for value in (
                settings.live_trading,
                settings.okx_live_allow_order_writes,
                settings.okx_live_auto_execution,
                settings.okx_live_auto_reconcile_on_start,
                settings.auto_trade,
                settings.paper_auto_execution,
                settings.okx_demo_allow_order_writes,
                settings.okx_demo_auto_execution,
            )
        )
    )
    return settings


class OwnedLiveSourceV1:
    __slots__ = ("__weakref__",)

    def __init__(self, key=None):
        _need(key is _SOURCE_KEY)

    @property
    def execution_authority(self):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class LiveSourcePageDataV1:
    selector: object
    raw_body: bytes
    raw_headers: tuple
    stamps: tuple
    raw_sha256: str
    canonical_sha256: str
    headers_sha256: str
    page_sha256: str
    custody_sha256: str
    origin: str
    lease_binding_id: str
    signed_at: str
    tls_certificate_sha256: str
    request_signature_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class _LiveCredentialMaterialV1:
    credentials: tuple[str, str, str]
    origin: str
    user_agent: str
    timeout_seconds: float


def _live_postsubmit_lease_v1(owner, *, key):
    """Original lease for a new lifecycle capture, never an old snapshot permit.

    Entry's account snapshot may have expired. Its private owner, original task,
    unchanged configured credentials and retained raw bank must still agree;
    the lifecycle must then measure NEW GETs and re-verify account identity.
    """
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    _need(key is lifecycle._SOURCE_LEASE_KEY_V1)
    lifecycle._require_active_source_context_v1(owner, key=key)
    _retained_source_identity_v1(owner)
    state = _SOURCES[owner]
    lease = state["lease"]
    lease.verify_current()
    _need(
        _configured(state["plan"]) is state["settings"]
        and state["uid"] == lease._pin.expected_uid
        and state["main_uid"] == lease._pin.expected_main_uid
    )
    return (
        lease._owner,
        lease,
        state["root"],
        state["uid"],
        state["main_uid"],
        state["credential_session_sha256"],
    )


def _owned_live_credential_material_v1(owner, *, key):
    """Private physical scope only; neither a saved receipt nor a key is enough."""
    from app.exchange.okx import native_live_write_v1 as physical

    _need(key is physical._CREDENTIAL_SOURCE_KEY_V1)
    physical._require_live_source_access_v1(owner, key=key)
    _replay_owned_live_source_v1(owner, key=_SOURCE_KEY)
    pin = _SOURCES[owner]["lease"]._pin
    return _LiveCredentialMaterialV1(
        pin.credentials, pin.origin, pin.user_agent, pin.timeout_seconds
    )


def _page_data(page, chain, custody):
    return LiveSourcePageDataV1(
        page.selector,
        page.raw_body,
        page.raw_headers,
        page.stamps,
        page.raw_sha256,
        page.canonical_sha256,
        page.headers_sha256,
        chain,
        custody,
        page.origin,
        page.lease_binding_id,
        page.signed_at,
        page.tls_certificate_sha256,
        page.request_signature_sha256,
    )


def _read_original_page_bank(root, attempt_id, receipt, *, selector=None):
    """Reuse the original parser and chain replay, retaining immutable source bytes."""
    module = current if selector is None else history
    _, before = module._read_custodied_live_account_config(
        root / attempt_id,
        attempt_id=module._CONFIG_BEFORE,
        expected_custody_sha256=receipt["config_before_custody_sha256"],
    )
    _, after = module._read_custodied_live_account_config(
        root / attempt_id,
        attempt_id=module._CONFIG_AFTER,
        expected_custody_sha256=receipt["config_after_custody_sha256"],
    )
    with current._root_context(root) as base, base.child(attempt_id) as directory:
        plan = module._read(directory, "observation.json")
        if selector is None:
            instruments = current._instruments(
                decode(plan, maximum=8192)["instruments"]
            )
            _need(
                plan
                == current._plan(
                    attempt_id,
                    instruments,
                    lease_binding_id=before.lease_binding_id,
                    origin=before.origin,
                )
            )
            coverage = current._CurrentCoverage(before, instruments)
        else:
            _need(
                plan
                == history._plan(
                    attempt_id,
                    selector,
                    lease_binding_id=before.lease_binding_id,
                    origin=before.origin,
                )
            )
            coverage = history._HistoryCoverage(before, selector)
        previous = sha(plan)
        pages = []
        for index in range(receipt["page_count"]):
            selected = coverage.selector()
            _need(selected is not None)
            with directory.child(module._page_name(index)) as page_directory:
                page, previous = module._read_page(
                    page_directory, index, previous, selected
                )
            coverage.accept(page)
            pages.append(_page_data(page, previous, receipt["custody_sha256"]))
        coverage.finish(after, ClockStamp.model_validate(receipt["capture_completed"]))
        _need(previous == receipt["last_page_sha256"] and coverage.selector() is None)
    return tuple(pages)


def _bank_rows_v1(pages, *, canonical_bodies=None, remember=False):
    _need(
        type(pages) is tuple
        and 1 <= len(pages) <= 1024
        and sum(len(page.raw_body) for page in pages) <= 64 * 1024 * 1024
    )
    rows = []
    for page in pages:
        _need(type(page) is LiveSourcePageDataV1)
        raw = page.raw_body
        _need(
            type(raw) is bytes
            and type(page.selector)
            in (LiveAccountRawPageSelectorV1, LiveAccountHistorySelectorV1)
            and sha(raw) == page.raw_sha256
            and _header_digest(page.raw_headers) == page.headers_sha256
        )
        digest = None if canonical_bodies is None else canonical_bodies.get(raw)
        if digest is None:
            digest = sha(canonical(decode(raw, maximum=8 * 1024 * 1024)))
            if remember:
                canonical_bodies[raw] = digest
        _need(digest == page.canonical_sha256)
        rows.append(
            [
                asdict(page.selector),
                page.raw_sha256,
                page.canonical_sha256,
                page.headers_sha256,
                page.page_sha256,
                page.custody_sha256,
                page.origin,
                page.lease_binding_id,
                page.signed_at,
                page.tls_certificate_sha256,
                page.request_signature_sha256,
                [stamp.model_dump() for stamp in page.stamps],
            ]
        )
    return rows


def _bank_sha(pages):
    # Every independent source replay still validates every original page.
    return sha(canonical(_bank_rows_v1(pages)))


def _prepare_bank_prefix_v1(pages):
    """Invocation-local immutable DATA prepared before the final current GET."""
    _need(type(pages) is tuple)
    if not pages:
        return 0, 0, ()
    rows = _bank_rows_v1(pages)
    # The singleton outer list preserves canonical(rows)'s row nesting depth.
    # Strip only its literal brackets, retaining the exact canonical row bytes.
    encoded = tuple(canonical([row])[1:-1] for row in rows)
    return len(pages), sum(len(page.raw_body) for page in pages), encoded


def _complete_prepared_bank_sha_v1(prefix, current_pages):
    _need(
        type(prefix) is tuple
        and len(prefix) == 3
        and type(prefix[0]) is int
        and type(prefix[1]) is int
        and 0 <= prefix[0] <= 1024
        and 0 <= prefix[1] <= 64 * 1024 * 1024
        and type(prefix[2]) is tuple
        and len(prefix[2]) == prefix[0]
        and all(type(row) is bytes for row in prefix[2])
    )
    suffix = _prepare_bank_prefix_v1(current_pages)
    _need(
        1 <= prefix[0] + suffix[0] <= 1024 and prefix[1] + suffix[1] <= 64 * 1024 * 1024
    )
    # No sorting/deduplication. Subsequent original full-bank source replay
    # must still match this digest before the new DATA owner can be returned.
    return sha(b"[" + b",".join((*prefix[2], *suffix[2])) + b"]")


def _retained_source_identity_v1(owner, *, depth=0):
    """Replay immutable prior provenance, NEVER make its old snapshot fresh.

    This private operation is only for a same-task, same-lease incremental
    capture. It deliberately returns no account view or execution capability.
    """
    _check_retained_source_bank_v1(
        owner, depth=depth, canonical_bodies={}, remember=True
    )


def _check_retained_source_bank_v1(owner, *, depth, canonical_bodies, remember):
    # Only the newest bank can add exact immutable bytes. Its existing
    # page/byte limits also bound the local map. Ancestors independently verify
    # every occurrence, and an unmatched body is decoded without being stored.
    _need(type(owner) is OwnedLiveSourceV1 and type(depth) is int and 0 <= depth <= 2)
    state = _SOURCES.get(owner)
    _need(type(state) is dict and state["task"] is asyncio.current_task())
    state["lease"].verify_current()
    _need(_configured(state["plan"]) is state["settings"])
    receipt = decode(state["receipt_json"], maximum=65536)
    _need(
        canonical(receipt) == state["receipt_json"]
        and sha(state["receipt_json"]) == state["source_revision_sha256"]
        and receipt["credential_session_sha256"] == state["credential_session_sha256"]
        and receipt["financial_tail_sha256"] == sha(state["financial_tail_json"])
        and receipt["raw_bank_sha256"] == state["raw_bank_sha256"]
    )
    retained = state.get("retained_history_pages", ())
    _need(type(retained) is tuple)
    rows = _bank_rows_v1(
        (
            *retained,
            *state["history_pages"],
            *state["baseline_current_pages"],
            *state["current_pages"],
        ),
        canonical_bodies=canonical_bodies,
        remember=remember,
    )
    _need(sha(canonical(rows)) == state["raw_bank_sha256"])
    _need(
        state["history_selectors"] == tuple(item[1] for item in state["history"])
        and receipt["history_custody_sha256"] == [item[2] for item in state["history"]]
        and receipt["history_partitions"] == len(state["history"])
    )
    previous = state.get("previous_source_owner")
    if previous is not None:
        _check_retained_source_bank_v1(
            previous, depth=depth + 1, canonical_bodies=canonical_bodies, remember=False
        )
        old = _SOURCES[previous]
        _need(
            old["lease"] is state["lease"]
            and old["credential_session_sha256"] == state["credential_session_sha256"]
            and receipt["capture_mode"] == "incremental_refresh_v1"
            and receipt["immutable_prefix_source_sha256"]
            == old["source_revision_sha256"]
            and receipt["immutable_prefix_bank_sha256"] == old["raw_bank_sha256"]
            and retained
            == (
                *old.get("retained_history_pages", ()),
                *old["history_pages"],
                *old["baseline_current_pages"],
                *old["current_pages"],
            )
        )


def _replay_owned_live_source_v1(owner, *, key):
    _need(key is _SOURCE_KEY and type(owner) is OwnedLiveSourceV1)
    state = _SOURCES.get(owner)
    _need(type(state) is dict and state["task"] is asyncio.current_task())
    state["lease"].verify_current()
    _need(_configured(state["plan"]) is state["settings"])
    now = _stamp()
    end = state["current_completed"]
    validate_stamps((end.model_dump(), now.model_dump()))
    _need(0 <= now.monotonic_ns - end.monotonic_ns <= state["plan"].max_current_age_ns)
    _retained_source_identity_v1(owner)
    tail = decode(state["financial_tail_json"], maximum=16384)
    _need(
        canonical(tail) == state["financial_tail_json"]
        and sha(state["financial_tail_json"])
        == decode(state["receipt_json"], maximum=65536)["financial_tail_sha256"]
    )
    _need(
        tail["schema"] == "ctcc.native_live.financial_tail_data.v1"
        and tail["environment"] == "live"
        and tail["partition_count"] == 7
    )
    cutoff_ms = int(tail["end_ms"])
    _need(
        _balance_tail_anchor_v1(state["baseline_current_pages"], cutoff_ms=cutoff_ms)
        == _balance_tail_anchor_v1(state["current_pages"], cutoff_ms=cutoff_ms)
        == tail["balance_revision_sha256"]
    )
    after = _stamp()
    state["lease"].verify_current()
    validate_stamps((end.model_dump(), after.model_dump()))
    _need(
        0 <= after.monotonic_ns - end.monotonic_ns <= state["plan"].max_current_age_ns
    )
    return MappingProxyType(
        {
            name: state[name]
            for name in (
                "uid",
                "main_uid",
                "credential_session_sha256",
                "source_revision_sha256",
                "receipt_json",
                "current_readback",
                "plan",
                "current_completed",
                "current_pages",
                "history_pages",
                "account_config",
                "raw_bank_sha256",
                "financial_tail_json",
                "baseline_current_pages",
                "history_selectors",
            )
        }
    )


def live_source_data_receipt_v1(owner):
    return _replay_owned_live_source_v1(owner, key=_SOURCE_KEY)["receipt_json"]


def _balance_tail_anchor_v1(pages, *, cutoff_ms):
    """Original balance update times bracket the queried financial tail.

    This is evidence of a stable venue balance revision, not a funding accrual
    timestamp or a claim that an empty first history page is complete.
    """
    selected = [page for page in pages if page.selector.stream == "balance"]
    _need(len(selected) == 1)
    rows = current._rows(selected[0].raw_body)
    _need(len(rows) == 1)
    row = rows[0]
    details = row.get("details")
    _need(type(details) is list and 0 < len(details) <= 2048)
    stamps = [
        row.get("uTime"),
        *(item.get("uTime") for item in details if type(item) is dict),
    ]
    _need(len(stamps) == len(details) + 1)
    _need(
        all(
            type(value) is str
            and re.fullmatch(r"[1-9][0-9]{12}", value)
            and int(value) <= cutoff_ms
            for value in stamps
        )
    )
    economic_fields = (
        "ccy",
        "uTime",
        "eq",
        "cashBal",
        "availEq",
        "liab",
        "borrowFroz",
        "ordFrozen",
        "upl",
        "isoEq",
    )
    return sha(
        canonical(
            [
                row["uTime"],
                [[item.get(name) for name in economic_fields] for item in details],
            ]
        )
    )


def _extend_original_history_bank_v1(existing, incoming, *, baseline=()):
    """Apply the existing bounded bank budget before retaining another part."""
    pages = (*existing, *baseline, *incoming)
    _need(
        len(pages) <= 1024 and all(type(page) is LiveSourcePageDataV1 for page in pages)
    )
    _need(sum(len(page.raw_body) for page in pages) <= 64 * 1024 * 1024)
    existing.extend(incoming)


async def _capture_with_original_lease_v1(plan, private_root, settings, lease):
    selectors = _selectors(plan)
    client = lease._owner
    started = _stamp()
    validate_stamps((plan.publication_barrier.model_dump(), started.model_dump()))
    _need(
        started.monotonic_ns > plan.publication_barrier.monotonic_ns
        and _utc_ns(plan.history_end) <= started.utc_ns
    )
    uid, main_uid = lease._pin.expected_uid, lease._pin.expected_main_uid
    histories = []
    history_pages = []
    identity = None
    spans = []
    for index, selector in enumerate(selectors):
        attempt = sha(canonical([plan.invocation_id, "history", index]))[:32]
        captured = await history.collect_live_account_history_v1(
            client, lease, selector, private_root=private_root, attempt_id=attempt
        )
        actual, first = _both_configs(
            private_root,
            attempt,
            captured,
            lease.binding_id,
            lease._pin.origin,
            uid,
            main_uid,
        )
        identity = actual if identity is None else identity
        _need(
            actual == identity
            and captured["synthetic_mock"] is False
            and captured["explicit_terminal_page_observed"] is True
        )
        spans.extend((first, ClockStamp.model_validate(captured["capture_completed"])))
        histories.append((attempt, selector, captured["custody_sha256"], captured))
        _extend_original_history_bank_v1(
            history_pages,
            _read_original_page_bank(
                private_root, attempt, captured, selector=selector
            ),
        )

    async def capture_current(label):
        attempt = sha(canonical([plan.invocation_id, label]))[:32]
        captured = await current.collect_live_current_account_v1(
            client, lease, private_root=private_root, attempt_id=attempt
        )
        actual, first = _both_configs(
            private_root,
            attempt,
            captured,
            lease.binding_id,
            lease._pin.origin,
            uid,
            main_uid,
        )
        _need(actual == identity and captured["synthetic_mock"] is False)
        completed = ClockStamp.model_validate(captured["capture_completed"])
        spans.extend((first, completed))
        pages = _read_original_page_bank(private_root, attempt, captured)
        return attempt, captured, actual, completed, pages

    (
        baseline_attempt,
        baseline,
        _,
        baseline_completed,
        baseline_pages,
    ) = await capture_current("current-baseline")
    cutoff = _stamp()
    validate_stamps((baseline_completed.model_dump(), cutoff.model_dump()))
    tail_end_ms = cutoff.utc_ns // 1_000_000 - 1
    tail_begin_ms = _utc_ns(plan.history_end) // 1_000_000
    _need(tail_begin_ms <= tail_end_ms and tail_end_ms - tail_begin_ms <= 86_400_000)
    anchor = _balance_tail_anchor_v1(baseline_pages, cutoff_ms=tail_end_ms)
    tail_custody = []
    for index, selector in enumerate(
        (
            *(
                select_live_account_history_page_v1(
                    "fills_recent",
                    begin_ms=str(tail_begin_ms),
                    end_ms=str(tail_end_ms),
                    inst_type=product,
                )
                for product in HISTORY_INST_TYPES
            ),
            select_live_account_history_page_v1(
                "bills_recent", begin_ms=str(tail_begin_ms), end_ms=str(tail_end_ms)
            ),
        )
    ):
        attempt = sha(canonical([plan.invocation_id, "financial-tail", index]))[:32]
        captured = await history.collect_live_account_history_v1(
            client, lease, selector, private_root=private_root, attempt_id=attempt
        )
        actual, first = _both_configs(
            private_root,
            attempt,
            captured,
            lease.binding_id,
            lease._pin.origin,
            uid,
            main_uid,
        )
        _need(
            actual == identity
            and captured["synthetic_mock"] is False
            and captured["explicit_terminal_page_observed"] is True
        )
        _need(first.monotonic_ns > cutoff.monotonic_ns)
        spans.extend((first, ClockStamp.model_validate(captured["capture_completed"])))
        histories.append((attempt, selector, captured["custody_sha256"], captured))
        tail_custody.append(captured["custody_sha256"])
        _extend_original_history_bank_v1(
            history_pages,
            _read_original_page_bank(
                private_root, attempt, captured, selector=selector
            ),
            baseline=baseline_pages,
        )
    prepared_prefix = _prepare_bank_prefix_v1((*history_pages, *baseline_pages))
    current_attempt, captured, actual, completed, current_pages = await capture_current(
        "current-final"
    )
    current_digest = captured["custody_sha256"]
    _need(_balance_tail_anchor_v1(current_pages, cutoff_ms=tail_end_ms) == anchor)
    financial_tail_json = canonical(
        {
            "schema": "ctcc.native_live.financial_tail_data.v1",
            "environment": "live",
            "begin_ms": str(tail_begin_ms),
            "end_ms": str(tail_end_ms),
            "cutoff_measured": cutoff.model_dump(),
            "balance_revision_sha256": anchor,
            "baseline_custody_sha256": baseline["custody_sha256"],
            "final_custody_sha256": current_digest,
            "tail_custody_sha256": tail_custody,
            "baseline_attempt": baseline_attempt,
            "baseline_current_completed": baseline_completed.model_dump(),
            "final_current_completed": completed.model_dump(),
            "partition_count": 7,
            "semantics": "same_native_balance_revision_before_and_after_complete_fill_bill_tail",
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    validate_stamps(
        (
            plan.publication_barrier.model_dump(),
            *(stamp.model_dump() for stamp in spans),
        )
    )
    _need(
        all(
            stamp.monotonic_ns > plan.publication_barrier.monotonic_ns
            for stamp in spans
        )
    )
    readback = current.read_live_current_account_with_balance_v1(
        private_root, attempt_id=current_attempt, expected_custody_sha256=current_digest
    )
    bank_sha = _complete_prepared_bank_sha_v1(prepared_prefix, current_pages)
    credential_session = sha(
        canonical(["live", lease.binding_id, uid, main_uid, lease._pin.origin])
    )
    receipt = canonical(
        {
            "schema": "ctcc.native_live.owned_source_data.v1",
            "environment": "live",
            "region": plan.region,
            "origin": lease._pin.origin,
            "invocation_id": plan.invocation_id,
            "account_identity_sha256": sha(canonical(["live", uid, main_uid])),
            "credential_session_sha256": credential_session,
            "publication_barrier": plan.publication_barrier.model_dump(),
            "current_custody_sha256": current_digest,
            "history_custody_sha256": [item[2] for item in histories],
            "history_partitions": len(histories),
            "raw_bank_sha256": bank_sha,
            "financial_tail_sha256": sha(financial_tail_json),
            "current_completed": completed.model_dump(),
            "account_complete": False,
            "financial_history_complete": False,
            "qualification_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    owner = OwnedLiveSourceV1(_SOURCE_KEY)
    _SOURCES[owner] = {
        "task": asyncio.current_task(),
        "settings": settings,
        "plan": plan,
        "root": private_root,
        "lease": lease,
        "uid": uid,
        "main_uid": main_uid,
        "credential_session_sha256": credential_session,
        "history": tuple(histories),
        "history_selectors": tuple(item[1] for item in histories),
        "current_attempt": current_attempt,
        "current_digest": current_digest,
        "current_readback": readback,
        "current_pages": current_pages,
        "baseline_current_pages": baseline_pages,
        "history_pages": tuple(history_pages),
        "account_config": actual,
        "raw_bank_sha256": bank_sha,
        "current_completed": completed,
        "receipt_json": receipt,
        "source_revision_sha256": sha(receipt),
        "financial_tail_json": financial_tail_json,
    }
    _replay_owned_live_source_v1(owner, key=_SOURCE_KEY)
    return owner


@asynccontextmanager
async def acquire_owned_live_source_v1(plan, *, private_root: Path):
    """Acquire only authentic configured Live GET source; no client injection."""
    _selectors(plan)
    settings = _configured(plan)
    client = OkxLivePinnedGetClientV1(settings=settings)
    lease = None
    try:
        with client.pinned_get_lease() as lease:
            owner = await _capture_with_original_lease_v1(
                plan, private_root, settings, lease
            )
            yield owner
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- fixed refusal; private provider fields stay in custody.
        raise LiveSourceBridgeError("owned_live_source_unavailable") from None
    finally:
        for owner, state in list(_SOURCES.items()):
            if state["lease"] is lease:
                _SOURCES.pop(owner, None)


async def refresh_owned_live_source_v1(owner, *, publication_barrier, invocation_id):
    """New requests after a new barrier, using the original credential lease.

    This refresh has no qualification or Arm effect. The original historic
    source remains retained until its enclosing context closes.
    """
    from dataclasses import replace

    _need(type(owner) is OwnedLiveSourceV1)
    state = _SOURCES.get(owner)
    _need(type(state) is dict and state["task"] is asyncio.current_task())
    state["lease"].verify_current()
    _need(_configured(state["plan"]) is state["settings"])
    plan = replace(
        state["plan"],
        publication_barrier=publication_barrier,
        invocation_id=invocation_id,
    )
    _selectors(plan)
    _need(
        plan.invocation_id != state["plan"].invocation_id
        and plan.publication_barrier.monotonic_ns
        > state["current_completed"].monotonic_ns
    )
    try:
        return await _capture_incremental_with_original_lease_v1(owner, plan)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no private source diagnostics escape.
        raise LiveSourceBridgeError("owned_live_source_unavailable") from None


def _incremental_selectors_v1(plan, *, cutoff_ms):
    """Re-read authoritative cash archives and the entire new-event overlap.

    Archive reads retain the original eight-day start. This detects late prior
    cash rows rather than assuming that a short tail proves their absence.
    Recent fills/bills close archive publication lag; closed position history
    is fetched to its actual terminal page. Original order/algo history stays
    explicitly historical; fresh pending/algo exposure is in the NEW current
    capture, never inferred from old history.
    """
    _selectors(plan)
    begin = str(_utc_ns(plan.history_start) // 1_000_000)
    overlap = str(_utc_ns(plan.history_end) // 1_000_000 - 1000)
    end = str(cutoff_ms)
    _need(int(overlap) <= cutoff_ms and cutoff_ms - int(overlap) <= 86_400_000)
    return (
        *(
            select_live_account_history_page_v1(
                "fills_history", begin_ms=begin, end_ms=end, inst_type=product
            )
            for product in HISTORY_INST_TYPES
        ),
        select_live_account_history_page_v1(
            "bills_archive", begin_ms=begin, end_ms=end
        ),
        *(
            select_live_account_history_page_v1(
                "orders_recent", begin_ms=overlap, end_ms=end, inst_type=product
            )
            for product in HISTORY_INST_TYPES
        ),
        select_live_account_history_page_v1("positions_history"),
        *(
            select_live_account_history_page_v1(
                "fills_recent", begin_ms=overlap, end_ms=end, inst_type=product
            )
            for product in HISTORY_INST_TYPES
        ),
        select_live_account_history_page_v1(
            "bills_recent", begin_ms=overlap, end_ms=end
        ),
    )


def _prior_cash_rows_v1(pages, *, archives_only=False):
    result = {"fills": {}, "bills": {}}
    for page in pages:
        kind = page.selector.kind
        family = (
            "fills"
            if kind.startswith("fills_")
            else "bills"
            if kind.startswith("bills_")
            else None
        )
        if family is None or (
            archives_only and kind not in ("fills_history", "bills_archive")
        ):
            continue
        for row in current._rows(page.raw_body):
            identity, raw = row["billId"], canonical(row)
            old = result[family].get(identity)
            _need(old is None or old == raw)
            result[family][identity] = raw
    return result


def _require_incremental_cash_continuity_v1(previous, incoming, *, previous_cutoff_ms):
    """Known rows cannot vanish/change; newly late prior cash is INCOMPLETE."""
    known = _prior_cash_rows_v1(previous)
    old_archives = _prior_cash_rows_v1(previous, archives_only=True)
    new_archives = _prior_cash_rows_v1(incoming, archives_only=True)
    fresh = _prior_cash_rows_v1(incoming)
    for family in ("fills", "bills"):
        for identity, raw in old_archives[family].items():
            _need(new_archives[family].get(identity) == raw)
        for identity, raw in fresh[family].items():
            row = decode(raw, maximum=65536)
            field = "fillTime" if family == "fills" else "ts"
            _need(type(row.get(field)) is str and row[field].isdecimal())
            if identity in known[family]:
                _need(known[family][identity] == raw)
            else:
                _need(int(row[field]) > previous_cutoff_ms)


async def _capture_incremental_with_original_lease_v1(previous_owner, plan):
    _retained_source_identity_v1(previous_owner)
    old = _SOURCES[previous_owner]
    _need(old.get("refresh_depth", 0) < 2)
    lease, root, settings, identity = (
        old["lease"],
        old["root"],
        old["settings"],
        old["account_config"],
    )
    client = lease._owner
    _need(
        plan.region == old["plan"].region
        and plan.history_start == old["plan"].history_start
        and plan.history_end == old["plan"].history_end
    )
    started = _stamp()
    validate_stamps((plan.publication_barrier.model_dump(), started.model_dump()))
    _need(started.monotonic_ns > plan.publication_barrier.monotonic_ns)
    spans = []

    async def capture_current(label):
        attempt = sha(canonical([plan.invocation_id, label]))[:32]
        captured = await current.collect_live_current_account_v1(
            client, lease, private_root=root, attempt_id=attempt
        )
        actual, first = _both_configs(
            root,
            attempt,
            captured,
            lease.binding_id,
            lease._pin.origin,
            old["uid"],
            old["main_uid"],
        )
        _need(actual == identity and captured["synthetic_mock"] is False)
        completed = ClockStamp.model_validate(captured["capture_completed"])
        spans.extend((first, completed))
        return (
            attempt,
            captured,
            completed,
            _read_original_page_bank(root, attempt, captured),
        )

    (
        baseline_attempt,
        baseline,
        baseline_completed,
        baseline_pages,
    ) = await capture_current("incremental-current-baseline")
    cutoff = _stamp()
    validate_stamps((baseline_completed.model_dump(), cutoff.model_dump()))
    end_ms = cutoff.utc_ns // 1_000_000 - 1
    previous_tail = decode(old["financial_tail_json"], maximum=16384)
    _need(int(previous_tail["end_ms"]) < end_ms)
    anchor = _balance_tail_anchor_v1(baseline_pages, cutoff_ms=end_ms)
    selectors = _incremental_selectors_v1(plan, cutoff_ms=end_ms)
    fresh_history, fresh_pages, tail_custody = [], [], []
    for index, selector in enumerate(selectors):
        attempt = sha(canonical([plan.invocation_id, "incremental-history", index]))[
            :32
        ]
        captured = await history.collect_live_account_history_v1(
            client, lease, selector, private_root=root, attempt_id=attempt
        )
        actual, first = _both_configs(
            root,
            attempt,
            captured,
            lease.binding_id,
            lease._pin.origin,
            old["uid"],
            old["main_uid"],
        )
        _need(
            actual == identity
            and captured["synthetic_mock"] is False
            and captured["explicit_terminal_page_observed"] is True
            and first.monotonic_ns > cutoff.monotonic_ns
        )
        spans.extend((first, ClockStamp.model_validate(captured["capture_completed"])))
        fresh_history.append((attempt, selector, captured["custody_sha256"], captured))
        _extend_original_history_bank_v1(
            fresh_pages,
            _read_original_page_bank(root, attempt, captured, selector=selector),
            baseline=baseline_pages,
        )
        if selector.kind in ("fills_recent", "bills_recent"):
            tail_custody.append(captured["custody_sha256"])
    _need(len(tail_custody) == 7)
    _require_incremental_cash_continuity_v1(
        old["history_pages"],
        fresh_pages,
        previous_cutoff_ms=int(previous_tail["end_ms"]),
    )
    # Keep original non-financial history and pre-cutover recent overlaps with
    # their original timestamps. New archive/tail/positions chains supplement
    # them; superseded raw bytes remain in the immutable prefix bank below.
    base = {canonical(asdict(item)) for item in _selectors(plan)}
    keep = tuple(
        item
        for item in old["history"]
        if canonical(asdict(item[1])) in base
        and item[1].kind not in ("fills_history", "bills_archive", "positions_history")
    )
    retained_active_pages = tuple(
        page
        for page in old["history_pages"]
        if any(
            history._partition(page.selector) == history._partition(item[1])
            for item in keep
        )
    )
    histories = (*keep, *fresh_history)
    active_pages = (*retained_active_pages, *fresh_pages)
    retained = (
        *old.get("retained_history_pages", ()),
        *old["history_pages"],
        *old["baseline_current_pages"],
        *old["current_pages"],
    )
    prepared_prefix = _prepare_bank_prefix_v1(
        (*retained, *active_pages, *baseline_pages)
    )
    current_attempt, captured, completed, current_pages = await capture_current(
        "incremental-current-final"
    )
    _need(_balance_tail_anchor_v1(current_pages, cutoff_ms=end_ms) == anchor)
    bank_sha = _complete_prepared_bank_sha_v1(prepared_prefix, current_pages)
    tail = canonical(
        {
            "schema": "ctcc.native_live.financial_tail_data.v1",
            "environment": "live",
            "begin_ms": str(_utc_ns(plan.history_end) // 1_000_000 - 1000),
            "end_ms": str(end_ms),
            "cutoff_measured": cutoff.model_dump(),
            "balance_revision_sha256": anchor,
            "baseline_custody_sha256": baseline["custody_sha256"],
            "final_custody_sha256": captured["custody_sha256"],
            "tail_custody_sha256": tail_custody,
            "baseline_attempt": baseline_attempt,
            "baseline_current_completed": baseline_completed.model_dump(),
            "final_current_completed": completed.model_dump(),
            "partition_count": 7,
            "semantics": "same_native_balance_revision_before_and_after_complete_fill_bill_tail",
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    validate_stamps(
        (
            plan.publication_barrier.model_dump(),
            *(stamp.model_dump() for stamp in spans),
        )
    )
    _need(
        all(
            stamp.monotonic_ns > plan.publication_barrier.monotonic_ns
            for stamp in spans
        )
    )
    readback = current.read_live_current_account_with_balance_v1(
        root,
        attempt_id=current_attempt,
        expected_custody_sha256=captured["custody_sha256"],
    )
    receipt = canonical(
        {
            "schema": "ctcc.native_live.owned_source_data.v1",
            "capture_mode": "incremental_refresh_v1",
            "environment": "live",
            "region": plan.region,
            "origin": lease._pin.origin,
            "invocation_id": plan.invocation_id,
            "account_identity_sha256": sha(
                canonical(["live", old["uid"], old["main_uid"]])
            ),
            "credential_session_sha256": old["credential_session_sha256"],
            "publication_barrier": plan.publication_barrier.model_dump(),
            "current_custody_sha256": captured["custody_sha256"],
            "history_custody_sha256": [item[2] for item in histories],
            "history_partitions": len(histories),
            "raw_bank_sha256": bank_sha,
            "financial_tail_sha256": sha(tail),
            "current_completed": completed.model_dump(),
            "immutable_prefix_source_sha256": old["source_revision_sha256"],
            "immutable_prefix_bank_sha256": old["raw_bank_sha256"],
            "all_mutable_requests_after_barrier": True,
            "old_history_relabelled_fresh": False,
            "late_prior_cash_detected": False,
            "account_complete": False,
            "financial_history_complete": False,
            "qualification_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    owner = OwnedLiveSourceV1(_SOURCE_KEY)
    _SOURCES[owner] = {
        "task": asyncio.current_task(),
        "settings": settings,
        "plan": plan,
        "root": root,
        "lease": lease,
        "uid": old["uid"],
        "main_uid": old["main_uid"],
        "credential_session_sha256": old["credential_session_sha256"],
        "history": histories,
        "history_selectors": tuple(item[1] for item in histories),
        "history_pages": active_pages,
        "current_attempt": current_attempt,
        "current_digest": captured["custody_sha256"],
        "current_readback": readback,
        "current_pages": current_pages,
        "baseline_current_pages": baseline_pages,
        "account_config": identity,
        "raw_bank_sha256": bank_sha,
        "current_completed": completed,
        "receipt_json": receipt,
        "source_revision_sha256": sha(receipt),
        "financial_tail_json": tail,
        "retained_history_pages": retained,
        "previous_source_owner": previous_owner,
        "refresh_depth": old.get("refresh_depth", 0) + 1,
    }
    _replay_owned_live_source_v1(owner, key=_SOURCE_KEY)
    return owner
