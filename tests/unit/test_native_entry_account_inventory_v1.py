"""Real private raw-byte replay with synthetic GETs, never source authority."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.domain.source_primitives import canonical, sha
from app.exchange.okx.live_account_history_page_v1 import (
    select_live_account_history_page_v1,
)
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from app.okx_live import entry_proof_inputs_v1 as subject
from app.okx_live import trusted_source_bridge_v1 as source
from tests.unit.exchange.test_live_account_config_custody_v1 import _private_root_path
from tests.unit.exchange.test_live_account_config_observation_v1 import _http, _settings
from tests.unit.exchange.test_live_account_current_chain_v1 import (
    CurrentAccount,
    _response,
)


@asynccontextmanager
async def _captured_bank(tmp_path, monkeypatch):
    root = _private_root_path(tmp_path)
    handler = CurrentAccount()

    def request(value):
        if value.url.path == "/api/v5/trade/fills-history":
            return _response([])
        return handler(value)

    captures = {}
    async with _http(request) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        with client.pinned_get_lease() as lease:
            for name in ("a", "b"):
                attempt = name * 32
                receipt = await source.current.collect_live_current_account_v1(
                    client, lease, private_root=root, attempt_id=attempt
                )
                captures[name] = (
                    attempt,
                    receipt,
                    source._read_original_page_bank(root, attempt, receipt),
                )
            cutoff = source.current._stamp().utc_ns // 1_000_000 - 10_000
            selector = select_live_account_history_page_v1(
                "fills_history",
                begin_ms=str(cutoff - 60_000),
                end_ms=str(cutoff),
                inst_type="SWAP",
            )
            receipt = await source.history.collect_live_account_history_v1(
                client, lease, selector, private_root=root, attempt_id="c" * 32
            )
            captures["c"] = (
                "c" * 32,
                receipt,
                source._read_original_page_bank(
                    root, "c" * 32, receipt, selector=selector
                ),
            )
            owner = object.__new__(source.OwnedLiveSourceV1)
            a, b, c = (captures[k] for k in ("a", "b", "c"))
            tail = canonical(
                {
                    "baseline_attempt": a[0],
                    "baseline_custody_sha256": a[1]["custody_sha256"],
                }
            )
            state = {
                "task": asyncio.current_task(),
                "root": root,
                "current_attempt": b[0],
                "current_digest": b[1]["custody_sha256"],
                "current_pages": b[2],
                "baseline_current_pages": a[2],
                "history": ((c[0], selector, c[1]["custody_sha256"], c[1]),),
                "history_pages": c[2],
                "financial_tail_json": tail,
                "receipt_json": canonical({"financial_tail_sha256": sha(tail)}),
            }
            # Only source admission is inert here. The production reader's
            # actual native ACL/nofollow/open/inventory/byte checks all run.
            monkeypatch.setattr(source, "_retained_source_identity_v1", lambda _: None)
            source._SOURCES[owner] = state
            try:
                yield owner, state, captures, client, lease
            finally:
                source._SOURCES.pop(owner, None)


@pytest.mark.asyncio
async def test_every_current_baseline_history_config_page_and_inventory_is_reopened(
    tmp_path, monkeypatch
):
    async with _captured_bank(tmp_path, monkeypatch) as (owner, state, *_):
        original = subject.account_raw_inventory_v1(owner)
        assert subject.account_raw_inventory_v1(owner) == original
        root = state["root"]
        for relative in (
            "b" * 32 + "/p-000000/body.raw",
            "a" * 32 + "/p-000000/headers.raw",
            "c" * 32 + "/p-000000/observation.json",
            "a" * 32 + "/" + "0" * 32 + "/body.raw",
        ):
            path = root / relative
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with pytest.raises((ValueError, OSError)):
                subject.account_raw_inventory_v1(owner)
            path.write_bytes(raw)
            assert subject.account_raw_inventory_v1(owner) == original
        extra = root / ("b" * 32) / "unreviewed.json"
        extra.write_bytes(b"{}")
        try:
            with pytest.raises((ValueError, OSError)):
                subject.account_raw_inventory_v1(owner)
        finally:
            extra.unlink()
        assert subject.account_raw_inventory_v1(owner) == original


@pytest.mark.asyncio
async def test_bound_baseline_id_cannot_point_to_another_valid_current_attempt(
    tmp_path, monkeypatch
):
    async with _captured_bank(tmp_path, monkeypatch) as (owner, state, captures, *_):
        original = subject.account_raw_inventory_v1(owner)
        original_tail, receipt = state["financial_tail_json"], state["receipt_json"]
        changed = canonical(
            {
                "baseline_attempt": captures["b"][0],
                "baseline_custody_sha256": captures["b"][1]["custody_sha256"],
            }
        )
        state.update(
            financial_tail_json=changed,
            receipt_json=canonical({"financial_tail_sha256": sha(changed)}),
        )
        with pytest.raises(ValueError):
            subject.account_raw_inventory_v1(owner)
        state.update(financial_tail_json=original_tail, receipt_json=receipt)
        assert subject.account_raw_inventory_v1(owner) == original
        state["previous_source_owner"] = owner
        with pytest.raises(ValueError):
            subject.account_raw_inventory_v1(owner)


@pytest.mark.asyncio
async def test_incremental_bank_rereads_its_own_new_baseline_and_original_prefix(
    tmp_path, monkeypatch
):
    async with _captured_bank(tmp_path, monkeypatch) as (
        previous,
        old,
        captures,
        client,
        lease,
    ):
        fresh = {}
        for name in ("d", "e"):
            receipt = await source.current.collect_live_current_account_v1(
                client, lease, private_root=old["root"], attempt_id=name * 32
            )
            fresh[name] = (
                receipt,
                source._read_original_page_bank(old["root"], name * 32, receipt),
            )
        tail = canonical(
            {
                "baseline_attempt": "d" * 32,
                "baseline_custody_sha256": fresh["d"][0]["custody_sha256"],
            }
        )
        owner = object.__new__(source.OwnedLiveSourceV1)
        source._SOURCES[owner] = old | {
            "previous_source_owner": previous,
            "current_attempt": "e" * 32,
            "current_digest": fresh["e"][0]["custody_sha256"],
            "current_pages": fresh["e"][1],
            "baseline_current_pages": fresh["d"][1],
            "financial_tail_json": tail,
            "receipt_json": canonical({"financial_tail_sha256": sha(tail)}),
        }
        try:
            expected = subject.account_raw_inventory_v1(owner)
            for name in ("a", "d"):
                path = old["root"] / (name * 32) / "p-000000" / "body.raw"
                raw = path.read_bytes()
                path.write_bytes(raw + b" ")
                with pytest.raises(ValueError):
                    subject.account_raw_inventory_v1(owner)
                path.write_bytes(raw)
                assert subject.account_raw_inventory_v1(owner) == expected
            assert (
                source._SOURCES[previous]["baseline_current_pages"] is captures["a"][2]
            )
        finally:
            source._SOURCES.pop(owner, None)
