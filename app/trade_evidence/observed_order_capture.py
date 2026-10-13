"""Retain an OKX order response against a replayed intent without granting ack.

The transport sidecar has application observation bounds, not a proved socket
dispatch, credential UID, or header receipt. Its raw bytes can enter the private
submission ledger, but cannot make that ledger's acknowledgement classifier pass.
"""

import hashlib
import json
import re
from datetime import UTC, datetime

from app.exchange.okx.transport_observation import (
    MAX_ORDER_RESPONSE_BYTES,
    ObservedOrderResult,
    OrderTransportObservation,
)
from app.trade_evidence.submission_reporting import (
    CapturedSubmission,
    SubmissionReportingError,
    _replay_reporting_intent,
)
from app.trade_qualification import submission_intent


def capture_unverified_order_response(
    request, intent_raw, *, intent_sha256, observed
) -> CapturedSubmission:
    """Build an uncertain private capture for the existing durable DB writer.

    This does not write or send anything. The caller must call the account-scoped
    ``record_observation`` with the same consumed intent and inspect its separate
    committed readback. Any failure retains the unresolved intent for reconciliation.
    """
    try:
        if type(observed) is not ObservedOrderResult:
            raise ValueError("observation_type")
        item = observed.observation
        if type(item) is not OrderTransportObservation:
            raise ValueError("observation_type")
        if (
            type(item.request_body_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", item.request_body_sha256) is None
            or type(item.transport_complete) is not bool
            or type(item.send_invoked_at) is not datetime
            or item.send_invoked_at.tzinfo is not UTC
            or type(item.response_returned_at) is not datetime
            or item.response_returned_at.tzinfo is not UTC
            or item.response_returned_at < item.send_invoked_at
            or type(item.http_status) is not int
            or not 100 <= item.http_status <= 599
        ):
            raise ValueError("observation_shape")
        if item.transport_complete:
            if (
                type(item.body_observed_at) is not datetime
                or item.body_observed_at.tzinfo is not UTC
                or item.body_observed_at < item.response_returned_at
                or type(item.raw_body) is not bytes
                or len(item.raw_body) > MAX_ORDER_RESPONSE_BYTES
            ):
                raise ValueError("complete_observation_shape")
        elif item.body_observed_at is not None or item.raw_body is not None:
            raise ValueError("incomplete_observation_shape")
        body_digest = (
            hashlib.sha256(item.raw_body).hexdigest()
            if item.raw_body is not None
            else None
        )
        if type(observed.response_body_sha256) not in (str, type(None)) or (
            observed.response_body_sha256 != body_digest
        ):
            raise ValueError("response_digest")

        _, inner, _, _ = _replay_reporting_intent(request, intent_raw, intent_sha256)
        intent = json.loads(inner.canonical_json)
        dispatch = intent["exchange_request"]
        if (
            intent["version"] != submission_intent.VERSION_V2
            or dispatch["method"] != "POST"
            or dispatch["path"] != "/api/v5/trade/order"
            or dispatch["environment"] != "demo"
            or dispatch["headers"]["x-simulated-trading"] != "1"
            or dispatch["account_uid"] != request.scope.account_id
            or hashlib.sha256(submission_intent._json(dispatch).encode()).hexdigest()
            != intent["exchange_request_sha256"]
        ):
            raise ValueError("dispatch_lineage")
        # This is the exact encoding used by the private REST client's order
        # route. The whole dispatch hash also binds account, path and headers.
        body = json.dumps(
            dispatch["body"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if hashlib.sha256(body).hexdigest() != item.request_body_sha256:
            raise ValueError("prepared_body_mismatch")
        return CapturedSubmission(
            exchange_request_sha256=intent["exchange_request_sha256"],
            request_started_at=item.send_invoked_at,
            completed_at=item.body_observed_at or item.response_returned_at,
            # Application bounds cannot prove socket header receipt or the
            # first byte of dispatch. Retain raw bytes, but force uncertainty.
            headers_received_at=None,
            body_completed_at=None,
            http_status=item.http_status,
            raw_body=item.raw_body,
            transport_complete=False,
        )
    except Exception:  # noqa: BLE001 -- No private bytes, UID or error text escapes.
        raise SubmissionReportingError(
            "submission_transport_capture_unverified"
        ) from None
