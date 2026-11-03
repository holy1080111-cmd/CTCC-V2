"""Negative authority tests; no source owner, TCP request or DB fixture."""

import copy
import pickle

import pytest

from app.exchange.okx import native_demo_settlement_v1 as subject


@pytest.mark.parametrize("key", [None, True, False, 0, {}, object()])
def test_closed_transport_cannot_be_constructed_with_supplied_key(key):
    with pytest.raises(
        subject.NativeTransportSettlementError,
        match="^native_demo_closed_transport_not_constructible$",
    ):
        subject.OwnedNativeClosedTransportV1(key=key)


@pytest.mark.parametrize("supplied", [None, True, False, {}, object()])
def test_data_or_passed_flag_cannot_take_a_closed_transport(supplied):
    with pytest.raises(
        subject.NativeTransportSettlementError,
        match="^native_demo_transport_settlement_unconfirmed$",
    ):
        subject._take_native_closed_transport_for_settlement_v1(supplied, key=object())


def test_bypassing_constructor_still_does_not_create_registry_authority():
    forged = object.__new__(subject.OwnedNativeClosedTransportV1)
    with pytest.raises(
        subject.NativeTransportSettlementError,
        match="^native_demo_transport_settlement_unconfirmed$",
    ):
        subject._take_native_closed_transport_for_settlement_v1(forged, key=object())


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_owner_cannot_be_copied_or_serialized(operation):
    forged = object.__new__(subject.OwnedNativeClosedTransportV1)
    with pytest.raises(TypeError, match="^native_demo_closed_transport_not_"):
        operation(forged)


def test_unexposed_socket_never_becomes_closed_data():
    with pytest.raises(
        subject.NativeTransportSettlementError,
        match="^native_demo_transport_settlement_unconfirmed$",
    ):
        subject.observe_native_closed_httpx_tcp_data_v1(None, None, None, None)


def test_closed_view_constructor_is_only_inert_data():
    supplied = subject.NativeClosedTransportViewV1(
        object(), object(), object(), "a" * 64, "b" * 64, (), object()
    )
    assert supplied.execution_authority is False
    assert supplied.order_retry_authority is False
    with pytest.raises(
        subject.NativeTransportSettlementError,
        match="^native_demo_transport_settlement_unconfirmed$",
    ):
        subject._take_native_closed_transport_for_settlement_v1(supplied, key=object())
