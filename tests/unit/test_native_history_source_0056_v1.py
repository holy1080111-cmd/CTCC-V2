"""Configured history observation boundaries without external exchange requests."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.demo_automation import native_history_source_0056_v1 as source
from app.domain.source_primitives import decode
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import native_demo_control_owner_v2 as control_owner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bootstrap_only", "stopped", "control_changed"),
    [
        (False, False, False),
        (False, False, True),
        (True, True, False),
        (True, True, True),
        (True, False, False),
    ],
)
async def test_history_uses_one_pinned_selection_and_withholds_changed_endpoint(
    monkeypatch, tmp_path, bootstrap_only, stopped, control_changed
):
    factories = SimpleNamespace(writer_factory=object())
    role_evidence = object()
    lease = SimpleNamespace(verify_current=Mock())
    client = SimpleNamespace(pinned_read_session=Mock(return_value=nullcontext(lease)))
    scope = history.NativeHistoryScopeV1("123", "321", "a" * 64, ("BTC-USDT-SWAP",))
    selection = SimpleNamespace(
        factories=factories, source_roles_0056=role_evidence, scope_config=scope
    )
    before = SimpleNamespace(
        control=SimpleNamespace(state=SimpleNamespace(emergency_stop=stopped))
    )
    after = SimpleNamespace(
        control=SimpleNamespace(
            state=SimpleNamespace(revision=7), event_sha256="b" * 64
        ),
        server_incarnation_sha256="c" * 64,
        role_route_sha256="d" * 64,
        writer_catalog=SimpleNamespace(native_definitions_sha256="e" * 64),
    )
    selector = AsyncMock(return_value=selection)
    observe = AsyncMock(side_effect=(before, after, after))
    genesis = AsyncMock(return_value=SimpleNamespace(source_sha256="a" * 64))
    monkeypatch.setattr(control_owner, "_read_genesis", genesis)
    collect = AsyncMock(return_value="f" * 64)
    compare = Mock(side_effect=ValueError if control_changed else None)
    reopen = Mock(return_value="f" * 64)
    dispose = AsyncMock()
    monkeypatch.setattr(source, "OkxDemoPrivateRestClient", Mock(return_value=client))
    monkeypatch.setattr(
        source.selectors, "select_configured_demo_source_identity_0056_v1", selector
    )
    monkeypatch.setattr(source, "_observe_control", observe)
    monkeypatch.setattr(source, "_readback_control", compare)
    monkeypatch.setattr(
        history, "acquire_configured_history_current_source_0056_v1", collect
    )
    monkeypatch.setattr(source, "_history_bundle_hash", reopen)
    monkeypatch.setattr(source, "_dispose", dispose)

    if control_changed or (bootstrap_only and not stopped):
        with pytest.raises(ValueError):
            await source._capture(
                object(), "BTC-USDT-SWAP", tmp_path, bootstrap_only=bootstrap_only
            )
        reopen.assert_not_called()
    else:
        receipt = decode(
            await source._capture(
                object(), "BTC-USDT-SWAP", tmp_path, bootstrap_only=bootstrap_only
            )
        )
        assert receipt["history_bundle_sha256"] == "f" * 64
        assert receipt.get("measured_genesis_sha256") == (
            "a" * 64 if bootstrap_only else None
        )
        assert receipt["capture_contract"] == "ctcc.demo_account_plan.v5"
        assert receipt["current_v7_page_chains_verified"] is False
        assert receipt["admission"] == "DENY"
        assert receipt["history_tail_closed"] is receipt["account_complete"] is False
        assert receipt["post_g12_eligible"] is receipt["execution_authority"] is False
        assert receipt["order_writes"] == 0
        assert "uid" not in receipt and "mainUid" not in receipt
        reopen.assert_called_once_with(tmp_path / "history-raw")
        lease.verify_current.assert_called_once()
    selector.assert_awaited_once_with(
        private_client=client, read_session=lease, instrument_ids=("BTC-USDT-SWAP",)
    )
    dispose.assert_awaited_once_with(selection, factories)
    if bootstrap_only and not stopped:
        assert observe.await_count == 1
        collect.assert_not_awaited()
        genesis.assert_not_awaited()
        return
    collect.assert_awaited_once_with(
        selection=selection,
        read_session=lease,
        private_client=client,
        raw_root=tmp_path / "history-raw",
        companion_root=tmp_path / "history-companion",
        bootstrap_only=bootstrap_only,
    )
    assert compare.call_args_list[0].args == (before, after)
    if bootstrap_only and not control_changed:
        assert observe.await_count == compare.call_count + 1 == 3
        assert compare.call_args_list[1].args == (after, after)
        genesis.assert_awaited_once_with(
            factories.writer_factory,
            control_owner.LedgerScope(account_id="123", settlement_currency="USDT"),
        )
    else:
        assert observe.await_count == compare.call_count + 1 == 2
        genesis.assert_not_awaited()


@pytest.mark.parametrize("revision", [31, 63, 95])
def test_diagnostic_stops_before_append_requiring_original_checkpoint(revision):
    with pytest.raises(history.NativeHistorySourceError, match="checkpoint_requires"):
        history._require_source_history_revision_0056(revision)


def test_distinct_history_owner_is_refused_by_legacy_financial_consumer(monkeypatch):
    monkeypatch.setattr(history, "_financial_key_v1", lambda key: None)
    owner = object.__new__(history._OwnedNativeHistoryCurrentSource0056V1)
    with pytest.raises(history.NativeHistorySourceError, match="owner_unavailable"):
        history._take_financial_assembly_v2(owner, key=None)
    with pytest.raises(history.NativeHistorySourceError, match="owner_unavailable"):
        history.consume_owned_native_history_current_source_only_v1(owner)
