"""Restricted, source-consumed close intent/claim custody; never an order permit.

Every commit is followed by a separate session readback with retained HMAC
verification. Unknown commit/readback burns the original owner and never retries.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app.database.repositories import native_live_lifecycle_v1 as lifecycle
from app.domain import native_live_controlled_close_schema_v1 as spec
from app.domain.source_primitives import ClockStamp, canonical, decode, sha, utc_from_ns
from app.okx_live import lifecycle_key_v1 as keys

_INTENT_SOURCE_KEY_V1 = object()
_CLAIM_SOURCE_KEY_V1 = object()
_HEX = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)


class LiveControlledCloseRepositoryError(ValueError):
    pass


class LiveControlledCloseCommitUnknown(RuntimeError):
    pass


class LiveControlledCloseReadbackRequired(RuntimeError):
    pass


def _need(value):
    if value is not True:
        raise LiveControlledCloseRepositoryError(
            "native_live_close_custody_refused"
        ) from None


def _time(value):
    _need(type(value) is str and len(value) <= 64)
    result = datetime.fromisoformat(value)
    _need(result.tzinfo is UTC and result.isoformat() == value)
    return result


def _digest(value):
    _need(type(value) is str and _HEX.fullmatch(value) is not None)
    return value


def _numeric_id(value):
    _need(type(value) is str and re.fullmatch(r"[1-9][0-9]{0,39}", value) is not None)
    return value


def _positive_decimal(value):
    _need(type(value) is str and 0 < len(value) <= 80 and value.isascii())
    try:
        result = Decimal(value)
        _need(result.is_finite() and result > 0)
    except InvalidOperation:
        raise LiveControlledCloseRepositoryError(
            "native_live_close_custody_refused"
        ) from None
    return result


def _parse_data(raw, *, metadata, claim=False):
    """Bounded DATA validation only; the caller must independently verify HMAC."""
    _need(
        type(raw) is bytes
        and raw.isascii()
        and 0 < len(raw) <= (32768 if claim else 65536)
    )
    document = decode(raw, maximum=32768 if claim else 65536)
    _need(type(document) is dict and canonical(document) == raw)
    _need(
        document.get("schema") == (spec.CLAIM_SCHEMA if claim else spec.SCHEMA)
        and document.get("environment") == "live"
        and document.get("execution_authority") is False
    )
    for name in (
        "account_uid",
        "main_uid",
        "origin",
        "database_oid",
        "owner_oid",
        "reconciler_oid",
        "key_id",
    ):
        expected = metadata[name]
        _need(type(document.get(name)) is type(expected) and document[name] == expected)
    for name in (
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
    ):
        _digest(document.get(name))
    _need(
        type(document.get("expected_control_revision")) is int
        and document["expected_control_revision"] > 0
    )
    observation = ClockStamp.model_validate(document["observation"], strict=True)
    observed = _time(document["observed_at"])
    _need(observed == utc_from_ns(observation.utc_ns))
    request = document.get("request_json")
    _need(type(request) is str and request.isascii() and 0 < len(request) <= 8192)
    request_raw = request.encode("ascii")
    parsed = decode(request_raw, maximum=8192)
    _need(
        canonical(parsed) == request_raw
        and sha(request_raw) == _digest(document.get("request_sha256"))
    )
    if claim:
        action = document.get("action")
        _need(type(action) is str and action in spec.ACTIONS)
        _digest(document.get("close_intent_sha256"))
        _digest(document.get("request_wire_sha256"))
        deadline = _time(document["request_deadline"])
        mono = document["request_deadline_monotonic_ns"]
    else:
        _need(
            document.get("position_side") == "net"
            and type(document.get("maximum_close_submissions")) is int
            and document["maximum_close_submissions"] == 1
        )
        consent = ClockStamp.model_validate(document["consent_received"], strict=True)
        _need(type(document.get("consent_expiry_monotonic_ns")) is int)
        _need(
            document["consent_expiry_monotonic_ns"]
            == consent.monotonic_ns + 120_000_000_000
        )
        _need(
            _time(document["consent_expires_at"])
            == utc_from_ns(consent.utc_ns) + timedelta(seconds=120)
        )
        _need(
            consent.utc_ns <= observation.utc_ns
            and consent.monotonic_ns <= observation.monotonic_ns
        )
        deadline = _time(document["close_deadline"])
        mono = document["close_deadline_monotonic_ns"]
        _need(type(mono) is int)
        _need(
            deadline <= _time(document["consent_expires_at"])
            and mono <= document["consent_expiry_monotonic_ns"]
        )
        _digest(document.get("public_quote_sha256"))
        _digest(document.get("protected_proof_sha256"))
        _numeric_id(document.get("protection_algo_id"))
        _numeric_id(document.get("entry_order_id"))
        _need(type(document.get("protection_client_id")) is str)
    _need(
        type(mono) is int
        and observation.monotonic_ns < mono <= observation.monotonic_ns + 5_000_000_000
    )
    _need(observed < deadline <= observed + timedelta(seconds=5))
    action = document["action"] if claim else "close"
    if not claim or action == "cancel_original_protection":
        _need(
            deadline
            == utc_from_ns(observation.utc_ns + mono - observation.monotonic_ns)
        )
    if action == "close":
        _need(
            type(parsed) is dict
            and set(parsed)
            == {
                "instId",
                "tdMode",
                "side",
                "posSide",
                "ordType",
                "sz",
                "px",
                "clOrdId",
                "reduceOnly",
            }
        )
        _need(
            all(
                type(value) is str
                for name, value in parsed.items()
                if name != "reduceOnly"
            )
        )
        _need(
            parsed["reduceOnly"] is True
            and parsed["tdMode"] == "isolated"
            and parsed["posSide"] == "net"
            and parsed["ordType"] == "fok"
            and parsed["side"] in {"buy", "sell"}
        )
        _positive_decimal(parsed["sz"])
        _positive_decimal(parsed["px"])
        _need(re.fullmatch(r"CTCCX[A-Za-z0-9]{1,27}", parsed["clOrdId"]) is not None)
        if not claim:
            _need(document.get("close_client_order_id") == parsed["clOrdId"])
        else:
            _digest(document.get("public_quote_sha256"))
    else:
        _need(
            type(parsed) is list
            and len(parsed) == 1
            and type(parsed[0]) is dict
            and set(parsed[0]) == {"instId", "algoId"}
        )
        _need(all(type(value) is str for value in parsed[0].values()))
        for name in ("full_account_verified", "fills_complete", "flat_verified"):
            _need(document.get(name) is True)
        _need(document.get("unknown_exposure") is False)
        for name, expected in (
            ("position_count", 0),
            ("pending_order_count", 0),
            ("pending_algo_count", 1),
        ):
            _need(type(document.get(name)) is int and document[name] == expected)
        _digest(document.get("close_claim_sha256"))
        _digest(document.get("account_config_before_sha256"))
        _digest(document.get("account_config_after_sha256"))
        _numeric_id(document.get("close_order_id"))
        _numeric_id(document.get("entry_order_id"))
        _numeric_id(document.get("protection_algo_id"))
        _positive_decimal(document.get("close_filled_contracts"))
        _need(
            document.get("close_order_state") == "filled"
            and document["close_order_id"] != document["entry_order_id"]
            and parsed[0]["algoId"] == document["protection_algo_id"]
        )
    return document, request_raw, deadline


@dataclass(frozen=True, slots=True, repr=False)
class LiveControlledCloseIntentDataV1:
    close_intent_id: str
    reservation_id: str
    document_json: bytes
    document_sha256: str
    signature: str
    request_json: bytes
    request_sha256: str
    deadline: datetime
    recorded_at: datetime
    execution_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class LiveControlledCloseClaimDataV1:
    intent: LiveControlledCloseIntentDataV1
    action: str
    document_json: bytes
    document_sha256: str
    signature: str
    request_json: bytes
    request_sha256: str
    request_wire_sha256: str
    deadline: datetime
    recorded_at: datetime
    execution_authority: bool = field(default=False, init=False)


class NativeLiveControlledCloseRepositoryV1:
    def __init__(self, factory, pins):
        lifecycle._factory(factory, pins)
        self.factory, self.pins = factory, pins

    def _key(self, owner):
        lifecycle._factory(self.factory, self.pins)
        record = keys._record(owner)
        _need(
            record["state"]["reconciler_factory"] is self.factory
            and record["state"]["reconciler_pins"] is self.pins
        )
        return keys.lifecycle_key_metadata_v1(owner)

    def _document(self, raw, signature, key_owner, *, claim=False):
        metadata = self._key(key_owner)
        _digest(signature)
        verifier = (
            keys._verify_retained_controlled_close_claim_signature_v1
            if claim
            else keys._verify_retained_controlled_close_intent_signature_v1
        )
        _need(verifier(key_owner, raw, signature) is True)
        return _parse_data(raw, metadata=metadata, claim=claim)

    async def _settings(self, session, *, readonly):
        for sql in (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            if readonly
            else lifecycle._SETTINGS[0],
            *lifecycle._SETTINGS[1:],
        ):
            await session.execute(text(sql))
        await lifecycle._profile(session, self.pins)

    def _intent_row(self, row, key_owner, metadata):
        _need(type(row[1]) is str and row[1].isascii())
        raw = row[1].encode("ascii")
        doc, request, deadline = self._document(raw, row[3], key_owner)
        _need(
            sha(raw) == row[2]
            and doc["close_intent_id"] == row[0]
            and type(row[4]) is datetime
            and row[4].tzinfo is UTC
            and row[4] < deadline
        )
        _need(
            tuple(row[5:])
            == (
                metadata["account_uid"],
                metadata["key_id"],
                doc["reservation_id"],
                doc["request_json"],
                doc["request_sha256"],
                deadline,
                doc["expected_control_revision"],
                _time(doc["consent_expires_at"]),
            )
        )
        return LiveControlledCloseIntentDataV1(
            row[0],
            doc["reservation_id"],
            raw,
            row[2],
            row[3],
            request,
            doc["request_sha256"],
            deadline,
            row[4],
        )

    async def read_close_intent_v1(self, key_owner, close_id):
        metadata = self._key(key_owner)
        _digest(close_id)
        async with self.factory() as session, session.begin():
            await self._settings(session, readonly=True)
            row = (
                await session.execute(
                    text("""SELECT close_intent_id,close_intent_json,close_intent_sha256,signature,created_at,
 account_uid,key_id,reservation_id,request_json,request_sha256,close_deadline,expected_control_revision,consent_expires_at
 FROM public.native_live_controlled_close_intents_v1 WHERE close_intent_id=:id AND environment='live' AND account_uid=:uid"""),
                    {"id": close_id, "uid": metadata["account_uid"]},
                )
            ).one()
            value = self._intent_row(row, key_owner, metadata)
            now = await session.scalar(text("SELECT clock_timestamp()"))
            _need(value.recorded_at <= now)
        _need(self._key(key_owner) == metadata)
        return value

    async def read_close_claim_v1(self, key_owner, close_id, action):
        metadata = self._key(key_owner)
        _digest(close_id)
        _need(type(action) is str and action in spec.ACTIONS)
        async with self.factory() as session, session.begin():
            await self._settings(session, readonly=True)
            parent = (
                await session.execute(
                    text("""SELECT close_intent_id,close_intent_json,close_intent_sha256,signature,created_at,
 account_uid,key_id,reservation_id,request_json,request_sha256,close_deadline,expected_control_revision,consent_expires_at
 FROM public.native_live_controlled_close_intents_v1 WHERE close_intent_id=:id AND environment='live' AND account_uid=:uid"""),
                    {"id": close_id, "uid": metadata["account_uid"]},
                )
            ).one()
            intent = self._intent_row(parent, key_owner, metadata)
            row = (
                await session.execute(
                    text("""SELECT claim_json,claim_sha256,signature,request_json,request_sha256,request_wire_sha256,request_deadline,claimed_at
 FROM public.native_live_controlled_close_claims_v1 WHERE close_intent_id=:id AND action=:action"""),
                    {"id": close_id, "action": action},
                )
            ).one()
            _need(type(row[0]) is str and row[0].isascii())
            raw = row[0].encode("ascii")
            doc, request, deadline = self._document(raw, row[2], key_owner, claim=True)
            original = decode(intent.document_json, maximum=65536)
            _need(
                doc["close_intent_id"] == close_id
                and doc["action"] == action
                and doc["close_intent_sha256"] == intent.document_sha256
                and sha(raw) == row[1]
            )
            _need(
                tuple(row[3:7])
                == (
                    doc["request_json"],
                    doc["request_sha256"],
                    doc["request_wire_sha256"],
                    deadline,
                )
            )
            for name in (
                "reservation_id",
                "original_intent_sha256",
                "consent_sha256",
                "operator_roundtrip_id",
                "credential_session_sha256",
                "original_credential_session_sha256",
                "process_sha256",
                "original_process_sha256",
                "candidate_sha256",
                "report_sha256",
                "policy_sha256",
                "expected_control_revision",
            ):
                _need(
                    doc[name] == original[name]
                    and type(doc[name]) is type(original[name])
                )
            _need(
                deadline <= _time(original["consent_expires_at"])
                and doc["request_deadline_monotonic_ns"]
                <= original["consent_expiry_monotonic_ns"]
                and doc["observation"]["utc_ns"] >= original["observation"]["utc_ns"]
                and doc["observation"]["monotonic_ns"]
                >= original["observation"]["monotonic_ns"]
            )
            if action == "close":
                _need(
                    request == intent.request_json
                    and deadline == intent.deadline
                    and doc["request_deadline_monotonic_ns"]
                    == original["close_deadline_monotonic_ns"]
                )
            else:
                original_request = decode(intent.request_json, maximum=8192)
                cancel_request = decode(request, maximum=8192)
                _need(
                    all(
                        doc[name] == original[name]
                        for name in (
                            "entry_order_id",
                            "protection_algo_id",
                            "protection_client_id",
                            "close_client_order_id",
                        )
                    )
                    and cancel_request[0]["instId"] == original_request["instId"]
                    and _positive_decimal(doc["close_filled_contracts"])
                    == _positive_decimal(original_request["sz"])
                )
                prior = (
                    await session.execute(
                        text("""SELECT claim_json,claim_sha256,signature FROM public.native_live_controlled_close_claims_v1
 WHERE close_intent_id=:id AND action='close'"""),
                        {"id": close_id},
                    )
                ).one()
                _need(type(prior[0]) is str and prior[0].isascii())
                prior_raw = prior[0].encode("ascii")
                prior_doc, prior_request, prior_deadline = self._document(
                    prior_raw, prior[2], key_owner, claim=True
                )
                _need(
                    sha(prior_raw) == prior[1] == doc["close_claim_sha256"]
                    and prior_doc["action"] == "close"
                    and prior_doc["close_intent_id"] == close_id
                    and prior_doc["close_intent_sha256"] == intent.document_sha256
                    and prior_request == intent.request_json
                    and prior_deadline == intent.deadline
                )
            _need(
                type(row[7]) is datetime
                and row[7].tzinfo is UTC
                and intent.recorded_at
                <= row[7]
                <= await session.scalar(text("SELECT clock_timestamp()"))
                and row[7] < deadline
            )
            value = LiveControlledCloseClaimDataV1(
                intent,
                action,
                raw,
                row[1],
                row[2],
                request,
                doc["request_sha256"],
                doc["request_wire_sha256"],
                deadline,
                row[7],
            )
        _need(self._key(key_owner) == metadata)
        return value

    async def _commit_once(self, raw, signature, key_owner, *, claim):
        doc, _, _ = self._document(raw, signature, key_owner, claim=claim)
        session = self.factory()
        committing = False
        try:
            async with session.begin():
                await self._settings(session, readonly=False)
                function = spec.EXECUTABLE_FUNCTIONS[1 if claim else 0]
                result = await session.scalar(
                    text(f"SELECT public.{function}(:proof,:signature)"),
                    {"proof": raw.decode("ascii"), "signature": signature},
                )
                _need(result == sha(raw))
                committing = True
        except BaseException as error:
            if committing:
                raise LiveControlledCloseCommitUnknown(
                    "native_live_close_commit_unknown_no_retry"
                ) from None
            if isinstance(error, asyncio.CancelledError):
                raise
            raise LiveControlledCloseRepositoryError(
                "native_live_close_commit_refused"
            ) from None
        finally:
            await session.close()
        try:
            value = (
                await self.read_close_claim_v1(
                    key_owner, doc["close_intent_id"], doc["action"]
                )
                if claim
                else await self.read_close_intent_v1(key_owner, doc["close_intent_id"])
            )
            _need(value.document_json == raw and value.signature == signature)
            return value
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- committed intent/claim is retained and never replayed.
            raise LiveControlledCloseReadbackRequired(
                "native_live_close_committed_readback_required_no_retry"
            ) from None

    async def prepare_owned_intent_v1(self, close_owner):
        try:
            from app.okx_live import owned_close_v1 as actor
        except ImportError:
            raise LiveControlledCloseRepositoryError(
                "native_live_close_source_unavailable"
            ) from None

        raw, signature, key_owner = actor._consume_owned_live_close_intent_v1(
            close_owner, key=_INTENT_SOURCE_KEY_V1
        )
        return await self._commit_once(raw, signature, key_owner, claim=False)

    async def claim_owned_submission_v1(self, close_owner, *, action):
        try:
            from app.okx_live import owned_close_v1 as actor
        except ImportError:
            raise LiveControlledCloseRepositoryError(
                "native_live_close_source_unavailable"
            ) from None

        _need(type(action) is str and action in spec.ACTIONS)
        raw, signature, key_owner = actor._consume_owned_live_close_claim_v1(
            close_owner, action=action, key=_CLAIM_SOURCE_KEY_V1
        )
        _need(decode(raw, maximum=32768).get("action") == action)
        return await self._commit_once(raw, signature, key_owner, claim=True)
