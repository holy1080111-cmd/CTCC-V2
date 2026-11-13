"""Negative source boundaries only; no native/SQL/flat/account positive credit."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification import native_demo_control_owner_v2 as owner
from app.trade_qualification.demo_control_clear_v2 import CLEAR_CONFIRMATION_V2


def test_control_owner_constructor_does_not_mint_runtime_authority():
    with pytest.raises(owner.NativeDemoControlError):
        owner.OwnedDemoControlSessionV2()


def test_control_operator_source_constructor_is_denied():
    with pytest.raises(native.NativeAccountRiskAssemblyError):
        native._OwnedControlOperatorSourceV1()


@pytest.mark.parametrize("value", [object(), {}, None])
def test_caller_values_cannot_supply_a_stopped_owner(value, tmp_path):
    # Even the correct phrase plus real absolute roots cannot turn DATA into
    # a source. No active owner, DB session, GET, clear or initialization exists.
    with pytest.raises(owner.NativeDemoControlError):
        native.perform_owned_control_operator_v1(
            value,
            purpose="clear_stop",
            confirmation=CLEAR_CONFIRMATION_V2,
            private_proof_root=tmp_path / "private",
            native_proof_root=tmp_path / "native",
        )


def test_bypassing_new_still_cannot_forge_registered_operator_cap(tmp_path):
    forged = object.__new__(owner.OwnedDemoControlSessionV2)
    with pytest.raises(owner.NativeDemoControlError):
        forged.clear_stop(
            confirmation=CLEAR_CONFIRMATION_V2,
            private_proof_root=tmp_path / "private",
            native_proof_root=tmp_path / "native",
        )


@pytest.mark.parametrize("bad", ["", "clear", "CTCC DEMO CLEAR STOP"])
def test_exact_operator_phrase_is_required_before_owner_move(bad, tmp_path):
    with pytest.raises(native.NativeAccountRiskAssemblyError):
        native.perform_owned_control_operator_v1(
            object(),
            purpose="clear_stop",
            confirmation=bad,
            private_proof_root=tmp_path / "private",
            native_proof_root=tmp_path / "native",
        )


@pytest.mark.parametrize("kind", ["same", "nested", "relative"])
def test_operator_roots_must_be_absolute_and_disjoint(kind, tmp_path):
    if kind == "same":
        left, right = tmp_path / "a", tmp_path / "a"
    elif kind == "nested":
        left, right = tmp_path / "a", tmp_path / "a" / "b"
    else:
        left, right = Path("relative"), tmp_path / "b"
    with pytest.raises(native.NativeAccountRiskAssemblyError):
        native.ControlOperatorRootsV1(left, right)


def test_forged_native_cap_is_not_a_repository_source():
    from app.database.repositories import (
        demo_operator_acknowledgements_current55_v1 as repository,
    )

    forged = object.__new__(native._OwnedControlOperatorSourceV1)
    with pytest.raises(native.NativeAccountRiskAssemblyError):
        repository.record_owned_operator_acknowledgement_v1(forged)


@pytest.mark.parametrize("branch", ["legacy", "original0056", "foreign0056"])
async def test_operator_transaction_keeps_catalog_context_through_write(
    monkeypatch, branch
):
    from app.database.repositories import (
        demo_operator_acknowledgements_current55_v1 as repository,
    )

    engine = object()
    scope = object()
    roles = object()
    session = SimpleNamespace(
        execute=AsyncMock(),
        get_bind=Mock(return_value=engine),
    )
    factory = SimpleNamespace(kw={"bind": SimpleNamespace(sync_engine=engine)})
    selected = SimpleNamespace(writer_factory=factory)
    replay = SimpleNamespace(
        _owned_database_0056=None if branch == "legacy" else (selected, roles),
        previous_control=SimpleNamespace(scope=scope),
        session_factory=object() if branch == "foreign0056" else factory,
    )
    source = Mock(return_value=replay)
    legacy = AsyncMock()
    held = SimpleNamespace(check=Mock(), recheck_catalog=AsyncMock())
    order = []

    @asynccontextmanager
    async def activate(actual, provided_session, provided_scope, *, roles):
        assert actual is selected and roles is replay._owned_database_0056[1]
        assert provided_session is session and provided_scope is scope
        order.append("enter")
        yield held
        order.append("exit")

    async def older(provided_session):
        assert provided_session is session
        order.append("legacy")
        await legacy()

    monkeypatch.setattr(
        repository.checkpoint,
        "held_qualification_checkpoint_0056_v1",
        activate,
        raising=False,
    )
    monkeypatch.setattr(repository.catalog55, "verify_actual_control_catalog_v2", older)

    if branch == "foreign0056":
        with pytest.raises(ValueError, match="original_writer"):
            async with repository._transaction(session, source):
                pytest.fail("foreign factory received live transaction")
        assert not order
    else:
        async with repository._transaction(session, source) as witness:
            assert witness is (held if branch == "original0056" else None)
            order.append("body")
        assert order == (
            ["enter", "body", "exit"]
            if branch == "original0056"
            else ["legacy", "body", "legacy"]
        )
    assert source.call_count >= 1
    if branch == "original0056":
        assert any(
            call.args == (repository._OPERATOR_FENCE,)
            for call in session.execute.await_args_list
        )
        held.recheck_catalog.assert_awaited_once()
        held.check.assert_called_once()
    else:
        held.check.assert_not_called()
