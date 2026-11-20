"""Actual main-0058 close custody, restricted SQL/HMAC and concurrency tests.

Authored for the isolated TLS PostgreSQL lane. No test-local SPEC installation,
venue data, credentials, human consent, Live Arm or order acceptance is claimed.
Published synthetic key material exercises SQL only, never a source owner.
"""

import asyncio
import hashlib
import hmac
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database.repositories import native_live_lifecycle_v1 as lifecycle_repository
from app.domain import native_live_controlled_close_schema_v1 as spec
from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, sha
from tests.integration import test_native_live_lifecycle_v1_pg as fixtures
from tests.unit.native_live_close_fixture_v1 import (
    claim_document,
    intent_document,
    stamp,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_live58_lab = fixtures._live58_lab
lifecycle_lab = fixtures.lifecycle_lab


def _signed(lab, document, *, claim=False, wrong_domain=False):
    raw = canonical(document)
    domain = spec.CLAIM_DOMAIN if claim else spec.INTENT_DOMAIN
    if wrong_domain:
        domain = fixtures.spec.DOMAIN
    return raw, hmac.digest(lab[6], domain + raw, hashlib.sha256).hex()


async def _call(lab, document, *, claim=False, wrong_domain=False):
    raw, signature = _signed(lab, document, claim=claim, wrong_domain=wrong_domain)
    function = spec.EXECUTABLE_FUNCTIONS[1 if claim else 0]
    async with lab[3]() as session, session.begin():
        result = await session.scalar(
            text(f"SELECT public.{function}(:raw,:signature)"),
            {"raw": raw.decode("ascii"), "signature": signature},
        )
    assert result == sha(raw)
    return raw, signature


async def _ready(lab):
    value = lab[2]
    rid, _, digest = exact_live_intent_document_v1(value)
    proof, raw, signature = fixtures._proof(
        lab,
        outcome="PROTECTED_HOLD",
        protection_verified=True,
        protection_algo_id="987654",
        protection_client_id=value.protection_client_id,
        entry_order_id="123456",
        entry_order_state="filled",
        entry_filled_contracts=str(value.contracts),
    )
    assert await fixtures._append(lab, raw, signature) == sha(raw)
    async with lab[0].begin() as connection:
        revision = await connection.scalar(
            text("""UPDATE public.native_live_controls_v1 SET armed=false,emergency_stop=false,
 arm_id=NULL,arm_expires_at=NULL,revision=revision+1,updated_at=clock_timestamp()
 WHERE environment='live' AND account_uid=:uid RETURNING revision"""),
            {"uid": value.account_uid},
        )
    document = intent_document(
        observation=stamp(datetime.now(UTC), monotonic_ns=10_000_000_000),
        metadata={
            name: entry
            for name, entry in lab[5].items()
            if name != "key_material_sha256"
        },
        reservation_id=rid,
        original_intent_sha256=digest,
        original_credential_session_sha256=value.credential_session_sha256,
        credential_session_sha256=value.credential_session_sha256,
        original_process_sha256=value.process_sha256,
        process_sha256=value.process_sha256,
        candidate_sha256=value.candidate_sha256,
        report_sha256=value.report_sha256,
        policy_sha256=value.policy_sha256,
        expected_control_revision=revision,
        protected_proof_sha256=sha(raw),
        protection_client_id=value.protection_client_id,
    )
    request = {
        "instId": value.instrument_id,
        "tdMode": "isolated",
        "side": "sell" if value.direction == "long" else "buy",
        "posSide": "net",
        "ordType": "fok",
        "sz": str(value.contracts),
        "px": str(value.stop_loss),
        "clOrdId": document["close_client_order_id"],
        "reduceOnly": True,
    }
    request_raw = canonical(request)
    document.update(
        request_json=request_raw.decode("ascii"), request_sha256=sha(request_raw)
    )
    return document, proof, raw


async def _state(lab):
    async with lab[0].connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT state,resolution_sha256 FROM public.native_live_reservations_v1"
                )
            )
        ).one()


async def test_actual58_close_closed_profile_and_immutable_two_action_schema(
    lifecycle_lab,
):
    lab = lifecycle_lab
    async with lab[3]() as session, session.begin():
        await lifecycle_repository._profile(session, lab[4])
    document, _, _ = await _ready(lab)
    raw, signature = await _call(lab, document)
    async with lab[3]() as session, session.begin():
        actual = (
            await session.execute(
                text("""SELECT close_intent_json,close_intent_sha256,signature,
 expected_control_revision,request_json,request_sha256,close_deadline FROM public.native_live_controlled_close_intents_v1""")
            )
        ).one()
    assert actual == (
        raw.decode(),
        sha(raw),
        signature,
        document["expected_control_revision"],
        document["request_json"],
        document["request_sha256"],
        datetime.fromisoformat(document["close_deadline"]),
    )
    for sql in (
        "UPDATE public.native_live_controlled_close_intents_v1 SET signature=signature",
        "DELETE FROM public.native_live_controlled_close_intents_v1",
        "INSERT INTO public.native_live_controlled_close_claims_v1 DEFAULT VALUES",
        "SELECT key_material FROM public.native_live_lifecycle_keys_v1",
    ):
        async with lab[3]() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(sql))
    async with lab[0].connect() as connection:
        # The owner also cannot rewrite an accepted immutable intent.
        with pytest.raises(DBAPIError):
            async with connection.begin():
                await connection.execute(
                    text(
                        "UPDATE public.native_live_controlled_close_intents_v1 SET signature=signature"
                    )
                )


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_domain",
        "wrong_uid",
        "wrong_revision",
        "null_deadline",
        "string_consent_clock",
        "missing_consent_clock",
        "fraction_revision",
        "expired",
        "wrong_algo",
        "wrong_size",
        "changed_stop",
        "market",
        "hedged",
        "not_reduce_only",
        "wrong_original_process",
    ),
)
async def test_actual58_close_rejects_unbound_or_changed_original_geometry(
    lifecycle_lab, mutation
):
    lab = lifecycle_lab
    document, _, _ = await _ready(lab)
    request = json.loads(document["request_json"])
    if mutation == "wrong_uid":
        document["account_uid"] = "999999"
    elif mutation == "wrong_revision":
        document["expected_control_revision"] += 1
    elif mutation == "null_deadline":
        document["close_deadline"] = None
    elif mutation == "string_consent_clock":
        document["consent_expiry_monotonic_ns"] = str(
            document["consent_expiry_monotonic_ns"]
        )
    elif mutation == "missing_consent_clock":
        document.pop("consent_expiry_monotonic_ns")
    elif mutation == "fraction_revision":
        # Canonical source JSON forbids floats; an exact decimal token is used
        # below to exercise PostgreSQL's bigint-rounding trap independently.
        token = f'"expected_control_revision":{document["expected_control_revision"]}'.encode()
        raw = canonical(document).replace(token, token + b".1")
        assert token + b".1" in raw
        signature = hmac.digest(lab[6], spec.INTENT_DOMAIN + raw, hashlib.sha256).hex()
        async with lab[3]() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(
                        text(
                            "SELECT public.native_live_controlled_close_prepare_v1(:raw,:signature)"
                        ),
                        {"raw": raw.decode(), "signature": signature},
                    )
        return
    elif mutation == "expired":
        document["close_deadline"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    elif mutation == "wrong_algo":
        document["protection_algo_id"] = "999999"
    elif mutation == "wrong_original_process":
        document["original_process_sha256"] = "0" * 64
    elif mutation == "wrong_size":
        request["sz"] = "2"
    elif mutation == "changed_stop":
        request["px"] = "98"
    elif mutation == "market":
        request["ordType"] = "market"
    elif mutation == "hedged":
        request["posSide"] = "long"
    elif mutation == "not_reduce_only":
        request["reduceOnly"] = False
    request_raw = canonical(request)
    document.update(request_json=request_raw.decode(), request_sha256=sha(request_raw))
    with pytest.raises(DBAPIError):
        await _call(lab, document, wrong_domain=mutation == "wrong_domain")
    assert await _state(lab) == ("reserved", None)
    async with lab[0].connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.native_live_controlled_close_intents_v1"
                )
            )
            == 0
        )


async def test_actual58_close_two_workers_one_parent_have_at_most_one_intent(
    lifecycle_lab,
):
    lab = lifecycle_lab
    first, _, _ = await _ready(lab)
    second = deepcopy(first)
    second["close_intent_id"] = "0" * 64
    second["close_client_order_id"] = "CTCCXsecond"
    request = json.loads(second["request_json"])
    request["clOrdId"] = second["close_client_order_id"]
    raw = canonical(request)
    second.update(request_json=raw.decode(), request_sha256=sha(raw))
    results = await asyncio.gather(
        _call(lab, first), _call(lab, second), return_exceptions=True
    )
    assert sum(type(result) is tuple for result in results) == 1
    assert sum(isinstance(result, DBAPIError) for result in results) == 1
    async with lab[0].connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.native_live_controlled_close_intents_v1"
                )
            )
            == 1
        )
    assert await _state(lab) == ("reserved", None)


async def test_actual58_close_claim_burns_once_without_releasing_risk_hold(
    lifecycle_lab,
):
    lab = lifecycle_lab
    parent, _, _ = await _ready(lab)
    await _call(lab, parent)
    claim = claim_document(parent)
    results = await asyncio.gather(
        _call(lab, claim, claim=True),
        _call(lab, claim, claim=True),
        return_exceptions=True,
    )
    assert sum(type(result) is tuple for result in results) == 1
    assert sum(isinstance(result, DBAPIError) for result in results) == 1
    assert await _state(lab) == ("reserved", None)


@pytest.mark.parametrize(
    "mutation",
    (
        "before_close_claim",
        "not_filled",
        "partial_fill",
        "position",
        "ordinary_pending",
        "no_algo",
        "other_algo",
        "missing_fills",
        "changed_revision",
        "new_stop",
    ),
)
async def test_actual58_close_original_protection_cancel_is_narrow_and_revocable(
    lifecycle_lab, mutation
):
    lab = lifecycle_lab
    parent, _, _ = await _ready(lab)
    await _call(lab, parent)
    claim = claim_document(parent)
    if mutation != "before_close_claim":
        await _call(lab, claim, claim=True)
    cancel = claim_document(parent, action="cancel_original_protection")
    changes = {
        "not_filled": {"close_order_state": "canceled"},
        "partial_fill": {"close_filled_contracts": "0.5"},
        "position": {"position_count": 1},
        "ordinary_pending": {"pending_order_count": 1},
        "no_algo": {"pending_algo_count": 0},
        "missing_fills": {"fills_complete": None},
    }
    cancel.update(changes.get(mutation, {}))
    if mutation == "other_algo":
        request = canonical([{"instId": "ETH-USDT-SWAP", "algoId": "999999"}])
        cancel.update(request_json=request.decode(), request_sha256=sha(request))
    if mutation in ("changed_revision", "new_stop"):
        async with lab[0].begin() as connection:
            await connection.execute(
                text(
                    "UPDATE public.native_live_controls_v1 SET revision=revision+1,emergency_stop=:stop"
                ),
                {"stop": mutation == "new_stop"},
            )
        # Adopting a newly observed revision cannot repair an old consent.
        cancel["expected_control_revision"] += 1
    with pytest.raises(DBAPIError):
        await _call(lab, cancel, claim=True)
    assert await _state(lab) == ("reserved", None)


async def test_actual58_close_cancel_has_separate_one_shot_request_and_terminal_proof(
    lifecycle_lab,
):
    lab = lifecycle_lab
    parent, _, protected_raw = await _ready(lab)
    intent_raw, _ = await _call(lab, parent)
    close_raw, _ = await _call(lab, claim_document(parent), claim=True)
    observation = stamp(datetime.now(UTC), monotonic_ns=11_000_000_000)
    cancel = claim_document(
        parent, action="cancel_original_protection", observation=observation
    )
    assert cancel["request_deadline"] != parent["close_deadline"]
    cancel_raw, _ = await _call(lab, cancel, claim=True)
    with pytest.raises(DBAPIError):
        await _call(lab, cancel, claim=True)
    assert await _state(lab) == ("reserved", None)
    terminal = {
        "outcome": "RECONCILED_FLAT",
        "sequence": 2,
        "previous_sha256": sha(protected_raw),
        "full_account_verified": True,
        "fills_complete": True,
        "flat_verified": True,
        "unknown_exposure": False,
        "raw_bank_sha256": "1" * 64,
        "submission_sha256": "2" * 64,
        "account_config_before_sha256": "3" * 64,
        "account_config_after_sha256": "4" * 64,
        "entry_order_id": "123456",
        "position_count": 0,
        "pending_order_count": 0,
        "pending_algo_count": 0,
        "submission_attempted": True,
        "entry_filled_contracts": str(lab[2].contracts),
        "exit_filled_contracts": str(lab[2].contracts),
        "exit_lineage_verified": True,
        "protection_client_id": lab[2].protection_client_id,
        "exit_kind": "controlled_micro_close",
        "exit_order_id": "123457",
        "exit_client_order_id": parent["close_client_order_id"],
        "exit_source_sha256": "5" * 64,
        "controlled_close_intent_sha256": sha(intent_raw),
        "controlled_close_claim_sha256": sha(close_raw),
        "controlled_cancel_claim_sha256": sha(cancel_raw),
        "controlled_close_consent_sha256": parent["consent_sha256"],
    }
    _, bad, signature = fixtures._proof(lab, **{**terminal, "pending_algo_count": 1})
    with pytest.raises(DBAPIError):
        await fixtures._append(lab, bad, signature)
    assert await _state(lab) == ("reserved", None)
    _, raw, signature = fixtures._proof(lab, **terminal)
    assert await fixtures._append(lab, raw, signature) == sha(raw)
    assert await _state(lab) == ("reconciled", sha(raw))


@pytest.mark.parametrize("change", ("revision", "stop"))
async def test_actual58_close_prepared_consent_cannot_adopt_later_control(
    lifecycle_lab, change
):
    lab = lifecycle_lab
    parent, _, _ = await _ready(lab)
    await _call(lab, parent)
    claim = claim_document(parent)
    async with lab[0].begin() as connection:
        await connection.execute(
            text(
                "UPDATE public.native_live_controls_v1 SET revision=revision+1,emergency_stop=:stop"
            ),
            {"stop": change == "stop"},
        )
    claim["expected_control_revision"] += 1
    with pytest.raises(DBAPIError):
        await _call(lab, claim, claim=True)
    assert await _state(lab) == ("reserved", None)


@pytest.mark.parametrize("drift", ("column", "write_acl"))
async def test_actual58_close_catalog_and_effective_grants_drift_fail_closed(
    lifecycle_lab, drift
):
    lab = lifecycle_lab
    async with lab[0].begin() as connection:
        if drift == "column":
            await connection.exec_driver_sql(
                "ALTER TABLE public.native_live_controlled_close_claims_v1 ADD COLUMN unreviewed text"
            )
        else:
            await connection.exec_driver_sql(
                f'GRANT UPDATE ON public.native_live_controlled_close_claims_v1 TO "{lab[4].reconciler_name}"'
            )
    async with lab[3]() as session, session.begin():
        with pytest.raises(lifecycle_repository.LiveLifecycleRepositoryError):
            await lifecycle_repository._profile(session, lab[4])


async def test_actual58_close_unresolved_legacy_intent_is_a_global_negative_blocker(
    lifecycle_lab,
):
    lab = lifecycle_lab
    parent, _, _ = await _ready(lab)
    async with lab[0].begin() as connection:
        await connection.execute(
            text("""INSERT INTO public.okx_live_execution_intents
 (idempotency_key,request_hash,action,status,instrument_id,detail_codes)
 VALUES('legacySyntheticCloseBlocked',:hash,'cancel_order','ambiguous','ETH-USDT-SWAP','[]'::jsonb)"""),
            {"hash": "a" * 64},
        )
    with pytest.raises(DBAPIError):
        await _call(lab, parent)
    assert await _state(lab) == ("reserved", None)
