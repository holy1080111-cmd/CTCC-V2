"""Pure incremental coverage negatives; no provider, credentials or Live Arm."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, sha
from app.okx_live import financial_rows_v1 as rows
from app.okx_live import trusted_source_bridge_v1 as subject
from tests.unit import test_native_live_financial_rows_v1 as fixture


def test_incremental_selectors_keep_full_archive_start_and_exact_recent_overlap():
    view = fixture._view()
    cutoff = subject._utc_ns(fixture.NOW) // 1_000_000
    selectors = subject._incremental_selectors_v1(view["plan"], cutoff_ms=cutoff)
    assert len(selectors) == 21
    assert sum(s.kind == "positions_history" for s in selectors) == 1
    archives = [s for s in selectors if s.kind in ("fills_history", "bills_archive")]
    assert len(archives) == 7 and all(
        int(s.begin_ms) == subject._utc_ns(view["plan"].history_start) // 1_000_000
        for s in archives
    )
    recent = [
        s
        for s in selectors
        if s.kind in ("fills_recent", "bills_recent", "orders_recent")
    ]
    assert len(recent) == 13
    assert all(
        int(s.begin_ms) == subject._utc_ns(view["plan"].history_end) // 1_000_000 - 1000
        for s in recent
    )
    assert all(int(s.end_ms) == cutoff for s in (*archives, *recent))


def test_incremental_gap_exceeding_recent_retention_is_refused():
    view = fixture._view()
    cutoff = subject._utc_ns(fixture.NOW + timedelta(days=2)) // 1_000_000
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._incremental_selectors_v1(view["plan"], cutoff_ms=cutoff)


def test_known_cash_rows_cannot_be_omitted_or_conflicted_in_rechecked_archives():
    view = fixture._view()
    pages = view["history_pages"]
    cutoff = int(fixture._ms(-3))
    subject._require_incremental_cash_continuity_v1(
        pages, pages, previous_cutoff_ms=cutoff
    )
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._require_incremental_cash_continuity_v1(
            pages, (), previous_cutoff_ms=cutoff
        )
    modified = list(pages)
    index = next(
        i
        for i, p in enumerate(pages)
        if p.selector.kind == "fills_history"
        and p.selector.inst_type == "SWAP"
        and subject.current._rows(p.raw_body)
    )
    data = subject.current._rows(pages[index].raw_body)
    data[0]["fee"] = "-999"
    raw = canonical({"code": "0", "data": data})
    modified[index] = replace(
        pages[index], raw_body=raw, raw_sha256=sha(raw), canonical_sha256=sha(raw)
    )
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._require_incremental_cash_continuity_v1(
            pages, modified, previous_cutoff_ms=cutoff
        )


def test_newly_visible_prior_cash_row_fails_closed_instead_of_rewriting_prior_loss():
    view = fixture._view()
    pages = view["history_pages"]
    cutoff = int(fixture._ms(-3))
    incoming = list(pages)
    index = next(
        i
        for i, p in enumerate(pages)
        if p.selector.kind == "bills_archive" and subject.current._rows(p.raw_body)
    )
    data = subject.current._rows(pages[index].raw_body)
    data.insert(0, dict(data[0]) | {"billId": "99999", "ts": fixture._ms(-30)})
    raw = canonical({"code": "0", "data": data})
    incoming[index] = replace(
        pages[index], raw_body=raw, raw_sha256=sha(raw), canonical_sha256=sha(raw)
    )
    with pytest.raises(subject.LiveSourceBridgeError):
        subject._require_incremental_cash_continuity_v1(
            pages, incoming, previous_cutoff_ms=cutoff
        )


def test_financial_reader_validates_declared_original_partition_bank_and_missing_terminal():
    view = fixture._view()
    selectors = tuple(
        p.selector for p in view["history_pages"] if p.selector.after is None
    )
    actual = view | {"history_selectors": selectors}
    assert rows._derive_original_financial_rows_v1(actual).fills
    with pytest.raises(rows.LiveFinancialRowsError):
        rows._derive_original_financial_rows_v1(
            actual | {"history_selectors": selectors[:-1]}
        )
    with pytest.raises(rows.LiveFinancialRowsError):
        rows._derive_original_financial_rows_v1(
            actual | {"history_selectors": (*selectors, selectors[0])}
        )
