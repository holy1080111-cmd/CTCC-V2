"""Consent DATA and original-owner negatives; no Live permission is granted."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.okx_live import owned_lifecycle_v1 as lifecycle
from app.okx_live import owned_qualification_v1 as subject
from tests.native_live_postsubmit_fixtures_v1 import intent, stamp


def _data():
    received = stamp(0)
    facts = SimpleNamespace(
        account_uid="730058",
        main_uid="730058",
        credential_session_sha256="a" * 64,
        reservation_id="b" * 64,
        intent_readback=b"declared-synthetic-intent-data",
        intent_data=intent(),
    )
    state = {
        "challenge_id": "c" * 64,
        "process_sha256": "d" * 64,
        "approval_sha256": "e" * 64,
        "identity": {"uid": facts.account_uid, "main_uid": facts.main_uid},
        "close_consent_received": received,
        "close_expires_at": utc_from_ns(received.utc_ns) + timedelta(seconds=120),
        "close_expiry_monotonic_ns": received.monotonic_ns + 120_000_000_000,
        "deadline": utc_from_ns(received.utc_ns) + timedelta(seconds=30),
    }
    state["close_consent_sha256"] = sha(
        canonical(
            [
                "ctcc-native-live-human-controlled-close-v1",
                state["approval_sha256"],
                received.model_dump(),
                120,
                state["challenge_id"],
                facts.account_uid,
                facts.main_uid,
                state["process_sha256"],
            ]
        )
    )
    return {"facts": facts}, state


def test_close_consent_data_has_fixed120s_and_never_inherits_entry_arm_deadline():
    record, state = _data()
    observed = subject._close_consent_facts_v1(record, state)
    assert observed.expires_at - utc_from_ns(
        state["close_consent_received"].utc_ns
    ) == timedelta(seconds=120)
    assert (
        observed.maximum_close_submissions == 1
        and observed.execution_authority is False
    )
    assert (
        observed.original_intent_sha256
        == exact_live_intent_document_v1(record["facts"].intent_data)[2]
    )
    assert observed.original_intent_sha256 != sha(record["facts"].intent_readback)
    state["deadline"] -= timedelta(days=1)
    assert subject._close_consent_facts_v1(record, state) == observed


@pytest.mark.parametrize(
    "mutation",
    ("utc_extend", "monotonic_extend", "digest", "roundtrip", "identity", "process"),
)
def test_changed_consent_scope_or_deadline_never_replays(mutation):
    record, state = _data()
    if mutation == "utc_extend":
        state["close_expires_at"] += timedelta(seconds=1)
    elif mutation == "monotonic_extend":
        state["close_expiry_monotonic_ns"] += 1
    elif mutation == "digest":
        state["close_consent_sha256"] = "0" * 64
    elif mutation == "roundtrip":
        state["challenge_id"] = "0" * 64
    elif mutation == "identity":
        state["identity"]["uid"] = "730059"
    else:
        state["process_sha256"] = "0" * 64
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject._close_consent_facts_v1(record, state)


@pytest.mark.asyncio
async def test_saved_consent_data_and_actual_module_key_cannot_create_close_owner():
    record, state = _data()
    data = subject._close_consent_facts_v1(record, state)
    for owner in (
        data,
        {"controlled_exit_authorized": True},
        object.__new__(subject.OwnedLiveQualificationV1),
    ):
        for call in (
            subject._consume_owned_live_close_permission_v1,
            subject._replay_spent_owned_live_close_permission_v1,
        ):
            with pytest.raises(subject.OwnedLiveQualificationError):
                call(owner, object(), key=lifecycle._CLOSE_PERMISSION_KEY_V1)
    assert not subject._QUALIFIED


@pytest.mark.asyncio
async def test_constructor_or_saved_params_cannot_mint_close_public_request():
    request = object.__new__(subject._ClosePublicRequest)
    with pytest.raises(subject.OwnedLiveQualificationError):
        subject._consume_live_public_request_v1(
            request, key=subject.public._COORDINATOR_REQUEST_KEY_V1
        )
    with pytest.raises(subject.OwnedLiveQualificationError):
        await subject._capture_owned_live_close_public_v1(
            object(), object(), key=lifecycle._CLOSE_PERMISSION_KEY_V1
        )
    assert not subject._CLOSE_PUBLIC_REQUESTS
