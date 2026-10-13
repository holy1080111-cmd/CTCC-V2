"""Pure comparison of a proposed Demo FOK intent with non-secret request bytes.

This is diagnostic only. The intent contains a logical request, not the actual
signed HTTP request or its wire bytes. No result from this module grants READY,
submission authority, retry authority, or permission to perform network IO.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx

from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import ReservationRequest

VERSION = "ctcc.prepared_dispatch_diagnostic.v1"
_ALLOWED_HOSTS = frozenset(
    ("openapi.okx.com", "www.okx.com", "us.okx.com", "eea.okx.com", "tr.okx.com")
)
_SAFE_HEADERS = frozenset(("content-type", "x-simulated-trading", "exptime"))
_UNPROVEN = (
    "intent_origin_unbound",
    "intent_wire_body_bytes_unbound",
    "signed_request_unverified",
    "credential_session_transport_unverified",
    "current_control_unverified",
    "first_byte_guard_unavailable",
)
_TRANSPORT_UNPROVEN = _UNPROVEN + (
    "write_side_account_uid_unverified",
    "write_side_credential_session_unverified",
    "current_account_revision_unverified",
    "one_use_dispatch_claim_unavailable",
)


@dataclass(frozen=True, slots=True)
class PreparedOkxRequestProjectionV1:
    """Immutable non-secret projection; caller construction gives it no trust.

    ``body_bytes`` are supplied bytes, not attested bytes from the OKX transport.
    Secret authentication headers are prohibited; only three public headers are
    accepted by the comparator. The projection cannot prove what HTTPX sends.
    """

    method: str
    url: str = field(repr=False)
    safe_headers: tuple[tuple[str, str], ...] = field(repr=False)
    body_bytes: bytes = field(repr=False)
    environment: str
    account_uid: str = field(repr=False)
    credential_session_sha256: str = field(repr=False)
    version: Literal["ctcc.prepared_okx_request_projection.v1"] = field(
        default="ctcc.prepared_okx_request_projection.v1", init=False
    )


@dataclass(frozen=True, slots=True)
class PreparedDispatchDiagnosticV1:
    """Never a permit, including when all comparable proposed fields match."""

    code: Literal[
        "record_invalid",
        "prepared_invalid",
        "prepared_mismatch",
        "wire_binding_unavailable",
    ]
    mismatches: tuple[str, ...]
    blockers: tuple[str, ...]
    intent_sha256: str | None
    logical_request_sha256: str | None
    prepared_body_sha256: str | None
    proposed_fields_match: bool
    version: Literal["ctcc.prepared_dispatch_diagnostic.v1"] = field(
        default=VERSION, init=False
    )
    admission: Literal["DENY"] = field(default="DENY", init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class TransportPreparedDispatchDiagnosticV1:
    """Non-secret observation of an HTTPX Request; permanently DENY.

    The source-owned caller must pass the *same* Request to HTTPX ``send``.
    This record intentionally retains only hashes and comparison labels, never
    authentication headers, raw body, account UID or credential material.
    """

    code: Literal[
        "record_invalid",
        "prepared_invalid",
        "prepared_mismatch",
        "transport_visible_match_unbound",
    ]
    mismatches: tuple[str, ...]
    blockers: tuple[str, ...]
    intent_sha256: str | None
    logical_request_sha256: str | None
    prepared_body_sha256: str | None
    prepared_request_sha256: str | None
    visible_fields_match: bool
    version: Literal["ctcc.transport_prepared_dispatch_diagnostic.v1"] = field(
        default="ctcc.transport_prepared_dispatch_diagnostic.v1", init=False
    )
    admission: Literal["DENY"] = field(default="DENY", init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    order_retry_authority: Literal[False] = field(default=False, init=False)
    order_submitted: Literal[False] = field(default=False, init=False)


def _diagnostic(
    code,
    *,
    mismatches=(),
    blockers=(),
    intent_sha256=None,
    logical_request_sha256=None,
    prepared_body_sha256=None,
):
    return PreparedDispatchDiagnosticV1(
        code=code,
        mismatches=tuple(mismatches),
        blockers=tuple(blockers),
        intent_sha256=intent_sha256,
        logical_request_sha256=logical_request_sha256,
        prepared_body_sha256=prepared_body_sha256,
        proposed_fields_match=code == "wire_binding_unavailable",
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite_json")


def _prepared_parts(prepared):
    if (
        type(prepared) is not PreparedOkxRequestProjectionV1
        or type(prepared.method) is not str
        or prepared.version != "ctcc.prepared_okx_request_projection.v1"
        or type(prepared.url) is not str
        or not 1 <= len(prepared.url) <= 2048
        or type(prepared.safe_headers) is not tuple
        or type(prepared.body_bytes) is not bytes
        or not 1 <= len(prepared.body_bytes) <= 16384
        or type(prepared.environment) is not str
        or type(prepared.account_uid) is not str
        or type(prepared.credential_session_sha256) is not str
        or len(prepared.credential_session_sha256) != 64
        or any(
            char not in "0123456789abcdef"
            for char in prepared.credential_session_sha256
        )
    ):
        raise ValueError("prepared_shape")
    target = urlsplit(prepared.url)
    if (
        target.scheme != "https"
        or target.hostname not in _ALLOWED_HOSTS
        or target.port not in (None, 443)
        or target.username is not None
        or target.password is not None
        or target.fragment
        or not target.path.startswith("/api/v5/")
    ):
        raise ValueError("prepared_target")
    headers = {}
    for pair in prepared.safe_headers:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            or not 1 <= len(pair[0]) <= 64
            or not 1 <= len(pair[1]) <= 128
            or any(char in "\r\n" for char in pair[0] + pair[1])
        ):
            raise ValueError("prepared_header")
        name = pair[0].lower()
        if name not in _SAFE_HEADERS or name in headers:
            # Also rejects OK-ACCESS-*, Authorization and cookie values before
            # they can enter any comparison result or loggable diagnostic.
            raise ValueError("prepared_header")
        headers[name] = pair[1]
    body = json.loads(
        prepared.body_bytes.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if type(body) is not dict:
        raise ValueError("prepared_body")
    return target, headers, body


def compare_control_bound_prepared_request_v1(
    request: ReservationRequest,
    committed: bound.ControlBoundSubmissionIntentRecord,
    prepared: PreparedOkxRequestProjectionV1,
) -> PreparedDispatchDiagnosticV1:
    """Replay historical lineage and compare available proposed fields; DENY.

    Neither input is a trusted runtime producer. Even a complete visible match
    cannot prove the missing host/body-wire/signature/current-control binding.
    No credential values, signer, HTTP client, repository or network are used.
    """
    try:
        if type(committed) is not bound.ControlBoundSubmissionIntentRecord:
            raise ValueError("exact_control_bound_record_required")
        replayed, consumed = bound.replay_control_bound_intent(
            committed.canonical_json,
            request,
            expected_sha256=committed.sha256,
        )
        outer = json.loads(replayed.canonical_json)
        inner = json.loads(outer["intent_json"])
        logical = inner["exchange_request"]
        state = control.decode(outer["consume_control_state_json"])
        if (
            inner["version"] != "ctcc-demo-submit-intent-v2"
            or logical["method"] != "POST"
            or logical["path"] != "/api/v5/trade/order"
            or logical["environment"] != "demo"
            or logical["account_uid"] != consumed.scope.account_id
            or logical["body"]["ordType"] != "fok"
        ):
            raise ValueError("not_demo_fok")
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return _diagnostic("record_invalid", blockers=("record_invalid",))

    try:
        target, headers, body = _prepared_parts(prepared)
        body_json = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        expected_body_json = json.dumps(
            logical["body"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        RecursionError,
        OverflowError,
    ):
        return _diagnostic(
            "prepared_invalid",
            blockers=("prepared_invalid",) + _UNPROVEN,
            intent_sha256=replayed.sha256,
            logical_request_sha256=inner["exchange_request_sha256"],
        )

    mismatches = []
    if prepared.method != logical["method"]:
        mismatches.append("method")
    if target.path != logical["path"]:
        mismatches.append("path")
    if target.query:
        mismatches.append("query")
    if headers.get("content-type") != "application/json":
        mismatches.append("content_type")
    for name, expected in (
        ("x-simulated-trading", logical["headers"]["x-simulated-trading"]),
        ("exptime", logical["headers"]["expTime"]),
    ):
        if headers.get(name) != expected:
            mismatches.append(name)
    if body_json != expected_body_json:
        mismatches.append("body")
    if body.get("clOrdId") != inner["client_order_id"]:
        mismatches.append("client_order_id")
    attached = body.get("attachAlgoOrds")
    protection_id = (
        attached[0].get("attachAlgoClOrdId")
        if type(attached) is list and len(attached) == 1 and type(attached[0]) is dict
        else None
    )
    if protection_id != inner["protection_client_order_id"]:
        mismatches.append("protection_client_order_id")
    if prepared.environment != logical["environment"]:
        mismatches.append("environment")
    if prepared.account_uid != logical["account_uid"]:
        mismatches.append("account_uid")
    if prepared.credential_session_sha256 != state.pins.credential_session_sha256:
        mismatches.append("credential_session_sha256")
    return _diagnostic(
        "prepared_mismatch" if mismatches else "wire_binding_unavailable",
        mismatches=mismatches,
        blockers=_UNPROVEN,
        intent_sha256=replayed.sha256,
        logical_request_sha256=inner["exchange_request_sha256"],
        prepared_body_sha256=hashlib.sha256(prepared.body_bytes).hexdigest(),
    )


def compare_control_bound_httpx_request_v1(
    request: ReservationRequest,
    committed: bound.ControlBoundSubmissionIntentRecord,
    prepared_request: httpx.Request,
    *,
    transport_environment: str,
    selected_request_url: str,
) -> TransportPreparedDispatchDiagnosticV1:
    """Compare independently read-back intent with actual prepared HTTPX bytes.

    The transport observes its own Request object immediately before ``send``.
    Historical UID/session pins are copied only to replay the older logical
    comparator; they do *not* attest the write credential or account. Both
    checks are explicit blockers even when visible wire fields agree.
    """
    try:
        if type(committed) is not bound.ControlBoundSubmissionIntentRecord:
            raise ValueError("record_type")
        replayed, _ = bound.replay_control_bound_intent(
            committed.canonical_json, request, expected_sha256=committed.sha256
        )
        outer = json.loads(replayed.canonical_json)
        inner = json.loads(outer["intent_json"])
        logical = inner["exchange_request"]
        state = control.decode(outer["consume_control_state_json"])
        historical_uid = logical["account_uid"]
        historical_session = state.pins.credential_session_sha256
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return TransportPreparedDispatchDiagnosticV1(
            "record_invalid",
            (),
            ("record_invalid",) + _TRANSPORT_UNPROVEN,
            None,
            None,
            None,
            None,
            False,
        )

    try:
        if (
            type(prepared_request) is not httpx.Request
            or type(transport_environment) is not str
            or type(selected_request_url) is not str
            or prepared_request.headers.get_list("authorization")
            or prepared_request.headers.get_list("cookie")
        ):
            raise ValueError("request_shape")
        body_bytes = prepared_request.content
        if type(body_bytes) is not bytes:
            raise ValueError("request_body")
        safe_headers = tuple(
            (name, value)
            for name, value in prepared_request.headers.multi_items()
            if name.lower() in _SAFE_HEADERS
        )
        projection = PreparedOkxRequestProjectionV1(
            method=prepared_request.method,
            url=str(prepared_request.url),
            safe_headers=safe_headers,
            body_bytes=body_bytes,
            environment=transport_environment,
            account_uid=historical_uid,
            credential_session_sha256=historical_session,
        )
        # This validates the HTTPX body's exact bytes and public header shape
        # before constructing any durable or loggable diagnostic output.
        _prepared_parts(projection)
        body_sha256 = hashlib.sha256(body_bytes).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "method": prepared_request.method,
                    "url": str(prepared_request.url),
                    "safe_headers": safe_headers,
                    "body_sha256": body_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
    except (
        ValueError,
        TypeError,
        UnicodeError,
        httpx.RequestNotRead,
        RecursionError,
        OverflowError,
    ):
        return TransportPreparedDispatchDiagnosticV1(
            "prepared_invalid",
            (),
            ("prepared_invalid",) + _TRANSPORT_UNPROVEN,
            replayed.sha256,
            inner["exchange_request_sha256"],
            None,
            None,
            False,
        )

    compared = compare_control_bound_prepared_request_v1(request, committed, projection)
    mismatches = list(compared.mismatches)
    # The older comparator intentionally accepts JSON-equivalent proposals.
    # The actual write seam has a fixed serializer; accepting whitespace or
    # key-order drift would leave the signed bytes different from the intent's
    # replayed body. This is a diagnostic byte comparison, not a precommitted
    # wire hash or proof that HTTPX has crossed its internal first await.
    expected_body_bytes = json.dumps(
        logical["body"], separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if body_bytes != expected_body_bytes:
        mismatches.append("body_bytes")
    if str(prepared_request.url) != selected_request_url:
        mismatches.append("selected_target")
    visible_match = compared.code == "wire_binding_unavailable" and not mismatches
    return TransportPreparedDispatchDiagnosticV1(
        "transport_visible_match_unbound" if visible_match else "prepared_mismatch",
        tuple(mismatches),
        _TRANSPORT_UNPROVEN,
        compared.intent_sha256,
        compared.logical_request_sha256,
        body_sha256,
        fingerprint,
        visible_match,
    )
