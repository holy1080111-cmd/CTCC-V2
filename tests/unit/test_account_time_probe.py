"""Exact public-time receipt replay; fixtures grant no native or account authority."""

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification.account_time_probe import (
    AccountTimeProbePlanV1,
    AccountTimeProbeReceiptV1,
    verify_account_time,
)

BASE_NS = 1_780_000_000_000_000_000


def evidence(*, server_ns=BASE_NS + 1_000_000, response_headers=None):
    plan = AccountTimeProbePlanV1(created_ns=BASE_NS - 1_000_000)
    raw = canonical(
        {
            "code": "0",
            "msg": "",
            "data": [{"ts": str(server_ns // 1_000_000)}],
        }
    )
    receipt = AccountTimeProbeReceiptV1(
        plan_sha256=plan.canonical_sha256(),
        body_sha256=sha(raw),
        body_size=len(raw),
        canonical_body_sha256=sha(raw),
        request_start={"utc_ns": BASE_NS, "monotonic_ns": 10},
        headers_received={"utc_ns": BASE_NS + 1_000_000, "monotonic_ns": 20},
        body_complete={"utc_ns": BASE_NS + 2_000_000, "monotonic_ns": 30},
        validation_complete={"utc_ns": BASE_NS + 3_000_000, "monotonic_ns": 40},
        tls_peer_sha256="a" * 64,
        tls_version="TLSv1.3",
        response_headers=(("content-type", "application/json"),)
        if response_headers is None
        else response_headers,
        transport_origin="synthetic_test",
    )
    return plan, receipt, raw


def test_public_time_receipt_replays_only_when_plan_and_causal_window_match():
    plan, receipt, raw = evidence()
    assert verify_account_time(plan, receipt, raw) == BASE_NS + 1_000_000
    with pytest.raises(ValueError, match="time_probe_identity_mismatch"):
        verify_account_time(
            AccountTimeProbePlanV1(created_ns=BASE_NS - 2_000_000), receipt, raw
        )


def test_server_time_outside_measured_request_fails_closed():
    plan, receipt, raw = evidence(server_ns=BASE_NS + 10_000_000)
    with pytest.raises(ValueError, match="exchange_time_outside_request"):
        verify_account_time(plan, receipt, raw)


def test_ambiguous_or_duplicate_response_header_evidence_is_rejected():
    with pytest.raises(ValueError, match="time_probe_receipt_invalid"):
        evidence(
            response_headers=(
                ("content-type", "application/json"),
                ("content-type", "application/json"),
            )
        )
