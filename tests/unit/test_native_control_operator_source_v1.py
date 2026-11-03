"""Negative source boundaries only; no native/SQL/flat/account positive credit."""

from pathlib import Path

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
