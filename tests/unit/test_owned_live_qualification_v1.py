"""Original-owner negatives only; these tests never Arm or submit Live."""

import asyncio
import copy
import pickle
from types import FunctionType

import pytest

from app.okx_live import native_runtime_v1 as runtime
from app.okx_live import owned_qualification_v1 as subject
from app.trade_qualification.native_continuation_guard_v1 import (
    NativeSourceClosureError,
)


def test_all_four_physical_hooks_are_actual_plain_source_functions():
    subject._NATIVE_GUARD()
    for name in subject.physical._API:
        call = getattr(subject, name)
        assert type(call) is FunctionType and call.__module__ == subject.__name__
        assert call.__code__.co_filename == subject.__file__
    actual = subject.physical._coordinator_api()
    assert actual[0] is subject and actual[3] is subject.LiveQualifiedExecutionFactsV1


@pytest.mark.asyncio
async def test_public_source_request_saved_dto_or_constructor_cannot_create_invocation():
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject.OwnedLiveOperatorSessionV1()
    forged = object.__new__(subject.OwnedLiveOperatorSessionV1)
    for operation in (
        lambda: copy.copy(forged),
        lambda: copy.deepcopy(forged),
        lambda: pickle.dumps(forged),
        lambda: forged.challenge_data,
    ):
        with pytest.raises(subject.OwnedLiveQualificationError):
            operation()
    with pytest.raises(subject.OwnedLiveQualificationError):
        await subject.run_owned_live_operator_once_v1(forged)
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject._consume_live_public_request_v1(
            {"passed": True}, key=subject.public._COORDINATOR_REQUEST_KEY_V1
        )
    fake_request = object.__new__(subject._PublicRequest)
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject._consume_live_public_request_v1(
            fake_request, key=subject.public._COORDINATOR_REQUEST_KEY_V1
        )


@pytest.mark.asyncio
async def test_every_physical_hook_refuses_forged_owner_even_with_actual_module_key():
    owner = object.__new__(subject.OwnedLiveQualificationV1)
    key = subject.physical._QUALIFICATION_SOURCE_KEY_V1
    for call in (
        subject._replay_owned_live_qualification_v1,
        subject._consume_owned_live_qualification_v1,
        subject._verify_spent_live_final_dispatch_v1,
    ):
        with pytest.raises(subject.OwnedLiveQualificationError):
            call(owner, key=key)
        with pytest.raises(subject.OwnedLiveQualificationError):
            call(owner, key=object())
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject._inhibit_owned_live_qualification_v1(owner, code="test", key=key)
    assert not subject._QUALIFIED


@pytest.mark.asyncio
async def test_expired_saved_nonce_and_plain_boolean_cannot_signal_approval():
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject.signal_live_operator_approval_v1(
            challenge_id="saved",
            arm_phrase="ARM_OKX_LIVE_REAL_MONEY",
            clear_stop_phrase="CLEAR_OKX_LIVE_STOP",
        )
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject.signal_live_operator_approval_v1(
            challenge_id=True, arm_phrase=True, clear_stop_phrase=True
        )
    assert not subject._CHALLENGES


@pytest.mark.asyncio
async def test_other_task_cannot_move_original_session_even_if_it_holds_the_python_reference():
    owner = object.__new__(subject.OwnedLiveOperatorSessionV1)
    subject._OWNED[owner] = {
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": subject.os.getpid(),
        "thread": subject.get_ident(),
        "closed": False,
    }
    try:

        async def detached():
            with pytest.raises(subject.OwnedLiveQualificationError):
                subject._session(owner)

        await asyncio.create_task(detached())
    finally:
        subject._OWNED.pop(owner)


def test_changed_helper_code_is_rejected_by_original_compiled_source_guard():
    original = subject._caps.__code__

    def counterfeit(view):
        return True

    subject._caps.__code__ = counterfeit.__code__
    try:
        with pytest.raises(NativeSourceClosureError):
            subject._NATIVE_GUARD()
    finally:
        subject._caps.__code__ = original
    subject._NATIVE_GUARD()


@pytest.mark.asyncio
async def test_missing_private_runtime_stays_not_configured_and_does_not_connect(
    monkeypatch,
):
    monkeypatch.delenv(runtime.ENVIRONMENT_NAME, raising=False)
    with pytest.raises(runtime.LiveRuntimeNotConfigured, match="NOT_CONFIGURED"):
        async with runtime.configured_live_runtime_v1():
            pytest.fail("runtime unexpectedly available")
