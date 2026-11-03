"""Ownership/negative retirement only, no manufactured Source/DB positives."""

import pytest

from app.exchange.okx import native_demo_write_v2 as write
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_controlled_close_v1 as close
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def test_final_read_cap_is_not_constructible():
    with pytest.raises(close.NativeControlledCloseError):
        close.OwnedNativeClosePostReadV1()


def test_copied_or_unregistered_response_cannot_mint_final_read_cap():
    for supplied in (object(), {"clOrdId": "CTC" + "a" * 29, "state": "filled"}):
        with pytest.raises(ValueError):
            close.take_owned_native_close_post_read_v1(supplied)


def test_object_new_cannot_register_a_third_read_owner():
    forged = object.__new__(close.OwnedNativeClosePostReadV1)
    with pytest.raises(ValueError):
        post._register_native_close_post_read_v1(forged, key=close._SOURCE_KEY_V1)


def test_unregistered_spent_intent_does_not_recover_a_close_response():
    forged = object.__new__(close.OwnedNativeCloseIntentV1)
    with pytest.raises(ValueError):
        close.take_owned_native_close_attempt_post_read_v1(forged)


def test_actual_received_eof_document_is_available_when_later_sdk_observation_failed():
    journal = write._native_transport._ResponseDataJournalV2()
    body = b'{"code":"0","data":[]}'
    journal.retain_headers(200, ((b"content-type", b"application/json"),))
    journal.retain_body(body)
    journal.retain_eof()
    record = {"received": journal}  # No later SDK ObservedOrderResult exists.
    function = foreign_primitive_v1(
        write._native_postsubmit_complete_documents_v1,
        {
            "_native_postsubmit_scan_record_v1": lambda token, key: record,
            "_native_retained_request_documents_v1": lambda actual: (b'{"intent":1}',),
        },
    )
    assert function(object(), key=object()) == (b'{"intent":1}', body)
    with pytest.raises(ValueError):
        guard._function_pin(function, {})


def test_terminal_close_data_is_not_the_existing_zero_fok_cleanup_owner():
    supplied = post.NativeReconciliationResultV1(
        "a" * 64,
        "own_entry_and_close_filled_flat_hold_retained",
        "b" * 64,
        "c" * 64,
        True,
    )
    with pytest.raises(ValueError):
        post.settle_owned_native_reconciliation_v1(supplied)


def test_unstarted_third_read_close_retains_original_and_both_post_objects():
    # Explicitly negative retirement primitive: arbitrary held objects never
    # become native owners, sources or permits by entering this data record.
    binding = object()
    held = (object(), object(), object(), object())
    state = {"binding": binding, "held": held, "closed": False}
    post._LIVE[binding] = state
    try:
        attempt = post._ClosePostReadAttempt(state)
        attempt.close()
        assert binding not in post._LIVE and post._UNKNOWN.get(binding) is state
        assert state["closed"] is True and state["held"] is held
    finally:
        post._LIVE.pop(binding, None)
        post._UNKNOWN.pop(binding, None)
