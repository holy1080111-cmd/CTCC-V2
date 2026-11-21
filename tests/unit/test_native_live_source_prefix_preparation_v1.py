"""Declared DATA-only capture ingress; no trusted transport, DB or Live Arm.

The collector/config/lease seams below provide synthetic raw source, never
order authority. The producer and its final full source replay remain real.
"""

import asyncio
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import (
    ClockStamp,
    PublicReceiptError,
    canonical,
    sha,
)
from app.okx_live import trusted_source_bridge_v1 as subject

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _page(index=0, *, selector=None, raw=None, stamps=None):
    selector = selector or subject.current.select_live_account_raw_page_v1("positions")
    raw = raw or canonical({"code": "0", "data": [], "msg": "declared synthetic"})
    stamp = ClockStamp(utc_ns=subject._utc_ns(NOW), monotonic_ns=1_000_000)
    headers = ((b"content-type", b"application/json"),)
    return subject.LiveSourcePageDataV1(
        selector,
        raw,
        headers,
        stamps or (stamp, stamp, stamp),
        sha(raw),
        sha(raw),
        subject._header_digest(headers),
        sha(canonical(["page", index])),
        "b" * 64,
        "https://openapi.okx.com",
        "c" * 64,
        "2026-11-20T00:00:00.000Z",
        "d" * 64,
        "e" * 64,
    )


def _legacy_digest(pages):
    """Frozen pre-change row-layout contract, independent of the new helper."""
    rows = [
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
        for page in pages
    ]
    return sha(canonical(rows))


@pytest.mark.parametrize("count", (1, 3, 16, 1024))
def test_prepared_split_keeps_legacy_row_order_and_digest_for_every_boundary(count):
    pages = tuple(_page(index) for index in range(count))
    expected = _legacy_digest(pages)
    assert subject._bank_sha(pages) == expected
    for boundary in {0, 1, count // 2, count}:
        prefix = subject._prepare_bank_prefix_v1(pages[:boundary])
        assert type(prefix) is tuple and type(prefix[2]) is tuple
        assert all(type(value) is bytes for value in prefix[2])
        assert (
            subject._complete_prepared_bank_sha_v1(prefix, pages[boundary:]) == expected
        )
    if count > 1:
        assert subject._bank_sha(tuple(reversed(pages))) != expected


def test_mixed_selectors_duplicate_rows_and_ascii_escaping_are_preserved():
    history = subject.select_live_account_history_page_v1(
        "fills_recent",
        begin_ms="1795132700000",
        end_ms="1795132799000",
        inst_type="SWAP",
    )
    first = replace(_page(0, selector=history), signed_at="declared\n\u2603")
    second = _page(1)
    pages = first, second, first
    assert (
        subject._complete_prepared_bank_sha_v1(
            subject._prepare_bank_prefix_v1(pages[:2]), pages[2:]
        )
        == _legacy_digest(pages)
        == subject._bank_sha(pages)
    )


@pytest.mark.parametrize("depth", (18, 24, 28))
def test_canonical_outer_list_depth_limits_remain_identical(depth):
    value = "declared synthetic scalar"
    for _ in range(depth):
        value = [value]
    pages = (replace(_page(), signed_at=value),)
    try:
        expected = subject._bank_sha(pages)
    except PublicReceiptError:
        with pytest.raises(PublicReceiptError):
            subject._prepare_bank_prefix_v1(pages)
    else:
        assert (
            subject._complete_prepared_bank_sha_v1(
                subject._prepare_bank_prefix_v1(()), pages
            )
            == expected
        )


@pytest.mark.parametrize(
    "invalid",
    (None, [], (True, 0, ()), (0, True, ()), (0, -1, ()), (1, 0, ("not bytes",))),
)
def test_unknown_prepared_shapes_never_become_source_evidence(invalid):
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._complete_prepared_bank_sha_v1(invalid, (_page(),))


def test_empty_complete_bank_and_old_capacity_limits_still_deny():
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._bank_sha(())
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._complete_prepared_bank_sha_v1(subject._prepare_bank_prefix_v1(()), ())
    prepared = subject._prepare_bank_prefix_v1((_page(),) * 1024)
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._complete_prepared_bank_sha_v1(prepared, (_page(),))
    huge = replace(_page(), raw_body=b"x" * (8 * 1024 * 1024))
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._prepare_bank_prefix_v1((huge,) * 9)


class _DeclaredCaptureIngress:
    def __init__(self, monkeypatch):
        self.tick = 0
        self.invocation = "a" * 32
        self.events, self.pages, self.history_pages = [], {}, []
        self.mutate_after_final = False
        self.settings, self.identity = object(), object()
        self.lease = SimpleNamespace(
            _owner=object(),
            binding_id="c" * 64,
            _pin=SimpleNamespace(
                expected_uid="synthetic",
                expected_main_uid="synthetic",
                origin="https://openapi.okx.com",
            ),
            verify_current=lambda: None,
        )
        monkeypatch.setattr(subject, "_stamp", self.clock)
        monkeypatch.setattr(subject, "_configured", lambda plan: self.settings)
        monkeypatch.setattr(subject, "_both_configs", self.config)
        monkeypatch.setattr(subject, "_read_original_page_bank", self.read_bank)
        monkeypatch.setattr(
            subject.history, "collect_live_account_history_v1", self.history
        )
        monkeypatch.setattr(
            subject.current, "collect_live_current_account_v1", self.current
        )
        monkeypatch.setattr(
            subject.current,
            "read_live_current_account_with_balance_v1",
            lambda *_args, **_kwargs: SimpleNamespace(
                receipt={"no_open_positions_or_pending_orders_observed": False}
            ),
        )
        original = subject._prepare_bank_prefix_v1

        def prepare(pages):
            self.events.append(("prepare", len(pages)))
            return original(pages)

        monkeypatch.setattr(subject, "_prepare_bank_prefix_v1", prepare)

    def clock(self):
        self.tick += 1
        return ClockStamp(
            utc_ns=subject._utc_ns(NOW) + self.tick * 1_000_000,
            monotonic_ns=1_000_000_000 + self.tick * 1_000_000,
        )

    def plan(self, invocation="a" * 32):
        self.invocation = invocation
        return subject.LiveSourcePlanV1(
            invocation,
            "global",
            NOW - timedelta(days=8),
            NOW - timedelta(seconds=60),
            self.clock(),
        )

    def config(self, _root, _attempt, receipt, *_args):
        return self.identity, receipt["declared_first"]

    def read_bank(self, _root, attempt, _receipt, **_kwargs):
        return self.pages[attempt]

    def receipt(self, attempt, first, last):
        return {
            "custody_sha256": sha(canonical(["synthetic", attempt, last.model_dump()])),
            "synthetic_mock": False,
            "explicit_terminal_page_observed": True,
            "capture_completed": last.model_dump(),
            "declared_first": first,
        }

    async def history(self, _client, _lease, selector, *, attempt_id, **_kwargs):
        first = self.clock()
        await asyncio.sleep(0)
        last = self.clock()
        page = _page(
            len(self.history_pages), selector=selector, stamps=(first, first, last)
        )
        self.history_pages.append(page)
        self.pages[attempt_id] = (page,)
        return self.receipt(attempt_id, first, last)

    async def current(self, _client, _lease, *, attempt_id, **_kwargs):
        labels = (
            "current-baseline",
            "current-final",
            "incremental-current-baseline",
            "incremental-current-final",
        )
        label = next(
            name
            for name in labels
            if sha(canonical([self.invocation, name]))[:32] == attempt_id
        )
        final = label.endswith("final")
        if final:
            self.events.append(("final_get_started", label))
        first = self.clock()
        await asyncio.sleep(0)
        last = self.clock()
        raw = canonical(
            {
                "code": "0",
                "data": [
                    {
                        "uTime": str(
                            subject._utc_ns(NOW - timedelta(seconds=2)) // 1_000_000
                        ),
                        "details": [
                            {
                                "ccy": "USDT",
                                "eq": "100",
                                "availEq": "100",
                                "uTime": str(
                                    subject._utc_ns(NOW - timedelta(seconds=2))
                                    // 1_000_000
                                ),
                            }
                        ],
                    }
                ],
            }
        )
        self.pages[attempt_id] = (
            _page(
                1000 + self.tick,
                selector=subject.current.select_live_account_raw_page_v1("balance"),
                raw=raw,
                stamps=(first, first, last),
            ),
        )
        if final:
            self.events.append(("final_get_returned", label))
            if self.mutate_after_final:
                page = self.history_pages[0]
                changed = canonical(
                    {"code": "0", "data": [], "msg": "changed after final GET"}
                )
                object.__setattr__(page, "raw_body", changed)
                object.__setattr__(page, "raw_sha256", sha(changed))
                object.__setattr__(page, "canonical_sha256", sha(changed))
        return self.receipt(attempt_id, first, last)


@pytest.mark.asyncio
@pytest.mark.parametrize("incremental", (False, True))
@pytest.mark.parametrize("mutate", (False, True))
async def test_producer_prepares_before_final_get_and_unchanged_replay_rejects_late_mutation(
    monkeypatch, tmp_path, incremental, mutate
):
    fixture = _DeclaredCaptureIngress(monkeypatch)
    plan = fixture.plan()
    if incremental:
        previous = await subject._capture_with_original_lease_v1(
            plan, tmp_path, fixture.settings, fixture.lease
        )
        fixture.events.clear()
        plan = fixture.plan("b" * 32)
    fixture.mutate_after_final = mutate
    if incremental:
        operation = subject._capture_incremental_with_original_lease_v1(previous, plan)
    else:
        operation = subject._capture_with_original_lease_v1(
            plan, tmp_path, fixture.settings, fixture.lease
        )
    if mutate:
        with pytest.raises(subject.LiveSourceBridgeError):
            await operation
    else:
        owner = await operation
        assert owner.execution_authority is False
        state = subject._SOURCES[owner]
        retained = state.get("retained_history_pages", ())
        assert state["raw_bank_sha256"] == subject._bank_sha(
            (
                *retained,
                *state["history_pages"],
                *state["baseline_current_pages"],
                *state["current_pages"],
            )
        )
        subject._replay_owned_live_source_v1(owner, key=subject._SOURCE_KEY)
    names = [event[0] for event in fixture.events]
    assert (
        names.index("prepare")
        < names.index("final_get_started")
        < names.index("final_get_returned")
    )
    assert names[-1] == "prepare"  # Only the new current suffix is prepared afterwards.
