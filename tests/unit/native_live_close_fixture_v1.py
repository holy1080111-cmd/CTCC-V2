"""Public synthetic close documents; no source owner, HMAC key or authority."""

from datetime import UTC, datetime, timedelta

from app.domain import native_live_controlled_close_schema_v1 as spec
from app.domain.source_primitives import canonical, sha, utc_from_ns

METADATA = {
    "environment": "live",
    "account_uid": "730058",
    "main_uid": "730058",
    "origin": "https://openapi.okx.com",
    "database_oid": 101,
    "owner_oid": 102,
    "reconciler_oid": 103,
    "key_id": "a" * 64,
}


def stamp(now=None, *, monotonic_ns=1_000_000_000):
    now = now or datetime(2026, 11, 21, tzinfo=UTC)
    delta = now - datetime(1970, 1, 1, tzinfo=UTC)
    return {
        "utc_ns": (
            (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        )
        * 1000,
        "monotonic_ns": monotonic_ns,
    }


def intent_document(*, observation=None, metadata=None, **changes):
    observation = observation or stamp()
    observed = utc_from_ns(observation["utc_ns"])
    request = canonical(
        {
            "instId": "ETH-USDT-SWAP",
            "tdMode": "isolated",
            "side": "sell",
            "posSide": "net",
            "ordType": "fok",
            "sz": "1",
            "px": "99",
            "clOrdId": "CTCCXsynthetic",
            "reduceOnly": True,
        }
    )
    result = {
        **(METADATA if metadata is None else metadata),
        "schema": spec.SCHEMA,
        "execution_authority": False,
        "reservation_id": "b" * 64,
        "close_intent_id": "c" * 64,
        "original_intent_sha256": "d" * 64,
        "consent_sha256": "e" * 64,
        "operator_roundtrip_id": "f" * 64,
        "original_credential_session_sha256": "1" * 64,
        "credential_session_sha256": "1" * 64,
        "original_process_sha256": "2" * 64,
        "process_sha256": "2" * 64,
        "candidate_sha256": "3" * 64,
        "report_sha256": "4" * 64,
        "policy_sha256": "5" * 64,
        "raw_custody_sha256": "6" * 64,
        "public_quote_sha256": "7" * 64,
        "protected_proof_sha256": "8" * 64,
        "position_side": "net",
        "maximum_close_submissions": 1,
        "expected_control_revision": 7,
        "consent_received": observation.copy(),
        "consent_expires_at": (observed + timedelta(seconds=120)).isoformat(),
        "consent_expiry_monotonic_ns": observation["monotonic_ns"] + 120_000_000_000,
        "observation": observation.copy(),
        "observed_at": observed.isoformat(),
        "close_deadline": (observed + timedelta(seconds=4)).isoformat(),
        "close_deadline_monotonic_ns": observation["monotonic_ns"] + 4_000_000_000,
        "protection_algo_id": "987654",
        "protection_client_id": "CTCCAsynthetic",
        "entry_order_id": "123456",
        "close_client_order_id": "CTCCXsynthetic",
        "request_json": request.decode("ascii"),
        "request_sha256": sha(request),
    }
    result.update(changes)
    return result


def claim_document(parent, *, action="close", observation=None, **changes):
    observation = observation or parent["observation"].copy()
    observed = utc_from_ns(observation["utc_ns"])
    result = {
        name: value
        for name, value in parent.items()
        if name
        in {
            *METADATA,
            "reservation_id",
            "close_intent_id",
            "original_intent_sha256",
            "consent_sha256",
            "operator_roundtrip_id",
            "original_credential_session_sha256",
            "credential_session_sha256",
            "original_process_sha256",
            "process_sha256",
            "candidate_sha256",
            "report_sha256",
            "policy_sha256",
            "raw_custody_sha256",
            "public_quote_sha256",
            "expected_control_revision",
        }
    }
    result.update(
        schema=spec.CLAIM_SCHEMA,
        execution_authority=False,
        action=action,
        close_intent_sha256=sha(canonical(parent)),
        observation=observation,
        observed_at=observed.isoformat(),
        request_json=parent["request_json"],
        request_sha256=parent["request_sha256"],
        request_wire_sha256="9" * 64,
        request_deadline=parent["close_deadline"],
        request_deadline_monotonic_ns=parent["close_deadline_monotonic_ns"],
    )
    if action == "cancel_original_protection":
        request = canonical(
            [{"instId": "ETH-USDT-SWAP", "algoId": parent["protection_algo_id"]}]
        )
        result.update(
            close_claim_sha256=sha(canonical(claim_document(parent))),
            request_json=request.decode("ascii"),
            request_sha256=sha(request),
            request_deadline=(observed + timedelta(seconds=4)).isoformat(),
            request_deadline_monotonic_ns=observation["monotonic_ns"] + 4_000_000_000,
            full_account_verified=True,
            fills_complete=True,
            flat_verified=True,
            unknown_exposure=False,
            position_count=0,
            pending_order_count=0,
            pending_algo_count=1,
            close_order_id="123457",
            close_order_state="filled",
            close_filled_contracts="1",
            close_client_order_id=parent["close_client_order_id"],
            entry_order_id=parent["entry_order_id"],
            protection_algo_id=parent["protection_algo_id"],
            protection_client_id=parent["protection_client_id"],
            account_config_before_sha256="a" * 64,
            account_config_after_sha256="b" * 64,
        )
    result.update(changes)
    return result
