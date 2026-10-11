"""Synthetic raw V7 config policy replay; never an authenticated Demo claim."""

import hashlib
import json

import pytest

from app.trade_qualification import account_api_key_policy as policy
from app.trade_qualification import account_capture as capture
from tests.unit.test_account_current_source_v7 import (
    current_plan_v7,
    empty_v7_pages,
)
from tests.unit.test_qualification_account_capture import MAIN_UID, UID, config
from tests.unit.test_qualification_account_capture import verify as verify_packet
from tests.unit.test_qualification_account_v4 import records

IP = "8.8.8.8"
PERM = "read_only,trade"
MISSING = object()


def _config_with(updates):
    result = config(perm=PERM, ip=IP)
    for key, value in (updates or {}).items():
        if value is MISSING:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def source(*, before=None, after=None):
    selected = current_plan_v7()
    pages = empty_v7_pages()
    pages["config_before"] = [[_config_with(before)]]
    pages["config_after"] = [[_config_with(after)]]
    _, observations = records(
        selected=selected, pages=pages, streams=capture.V7_CURRENT_STREAMS
    )
    packet = verify_packet(selected, observations)
    frozen = capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    return packet, frozen, packet.plan_sha256


def observe(*, before=None, after=None):
    packet, frozen, plan_sha256 = source(before=before, after=after)
    result = policy.observe_v7_demo_api_key_policy(
        frozen.payload,
        expected_packet_sha256=frozen.sha256,
        expected_plan_sha256=plan_sha256,
        expected_policy_sha256=policy.POLICY_SHA256,
    )
    return packet, frozen, plan_sha256, result, json.loads(result.receipt_json)


def test_exact_replayed_config_policy_is_redacted_deny_only():
    packet, frozen, plan_pin, result, receipt = observe()
    assert packet.schema_version == "ctcc.demo_current_account_capture.v7"
    assert packet.account_complete is False
    assert receipt["schema_version"] == "ctcc.demo_api_key_policy_observation.v1"
    assert receipt["policy_sha256"] == policy.POLICY_SHA256
    assert receipt["frozen_packet_sha256"] == frozen.sha256
    assert receipt["packet_record_sha256"] == packet.packet_sha256
    assert frozen.sha256 != packet.packet_sha256
    assert receipt["plan_sha256"] == plan_pin
    assert receipt["packet_identity_and_session_context_replayed"] is True
    assert receipt["observation_scope"] == "supplied_packet_replay_only"
    assert receipt["permissions_observed"] is True
    assert receipt["ip_binding_list_observed"] is True
    assert receipt["config_policy_consistent"] is True
    assert receipt["blocking_reasons"] == []
    assert receipt["registration_region_verified"] is False
    assert receipt["provider_credential_session_verified"] is False
    assert receipt["current_egress_ip_verified"] is False
    assert (
        receipt["source_authenticity_verified"]
        is result.source_authenticity_verified
        is False
    )
    assert receipt["account_complete"] is result.account_complete is False
    assert receipt["execution_authority"] is result.execution_authority is False
    assert receipt["admission"] == "DENY"
    assert (
        policy.verify_v7_demo_api_key_policy_receipt(
            frozen.payload,
            expected_packet_sha256=frozen.sha256,
            expected_plan_sha256=plan_pin,
            expected_policy_sha256=policy.POLICY_SHA256,
            receipt_json=result.receipt_json,
            expected_receipt_sha256=result.receipt_sha256,
        )
        == result
    )
    assert {"uid", "mainUid", "perm", "ip"}.isdisjoint(receipt)
    assert UID not in receipt.values()
    assert MAIN_UID not in receipt.values()
    for private in (IP, PERM, packet.plan.session_binding_id):
        assert private.encode() not in result.receipt_json


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"perm": MISSING}, "permissions_missing_or_malformed"),
        ({"perm": ""}, "permissions_missing_or_malformed"),
        ({"perm": "read_only"}, "read_trade_permission_missing"),
        ({"perm": "trade"}, "read_trade_permission_missing"),
        ({"perm": "read_only,trade,withdraw"}, "withdraw_permission_enabled"),
        ({"perm": "read_only,trade,trade"}, "permissions_missing_or_malformed"),
        ({"perm": "read_only,trade,unknown"}, "permissions_missing_or_malformed"),
        ({"perm": "read_only, trade"}, "permissions_missing_or_malformed"),
        ({"ip": MISSING}, "ip_binding_missing_or_malformed"),
        ({"ip": ""}, "ip_binding_missing_or_malformed"),
        ({"ip": "not-an-ip"}, "ip_binding_missing_or_malformed"),
        ({"ip": "fe80::1%eth0"}, "ip_binding_missing_or_malformed"),
        ({"ip": "2001:4860:4860::8888"}, "ip_binding_missing_or_malformed"),
        ({"ip": "0.0.0.0"}, "ip_binding_missing_or_malformed"),
        ({"ip": "::"}, "ip_binding_missing_or_malformed"),
        ({"ip": "127.0.0.1"}, "ip_binding_missing_or_malformed"),
        ({"ip": "100.64.0.1"}, "ip_binding_missing_or_malformed"),
        ({"ip": "192.0.2.1"}, "ip_binding_missing_or_malformed"),
        ({"ip": "224.0.0.1"}, "ip_binding_missing_or_malformed"),
        ({"ip": "255.255.255.255"}, "ip_binding_missing_or_malformed"),
        ({"ip": "10.0.0.1"}, "ip_binding_missing_or_malformed"),
        ({"ip": f"{IP},{IP}"}, "ip_binding_missing_or_malformed"),
        ({"ip": f"{IP}, 1.1.1.1"}, "ip_binding_missing_or_malformed"),
    ],
)
def test_missing_or_unsafe_source_fields_fail_closed(change, reason):
    _, _, _, _, receipt = observe(before=change, after=change)
    assert reason in receipt["blocking_reasons"]
    assert receipt["config_policy_consistent"] is False
    assert receipt["account_complete"] is False
    assert receipt["execution_authority"] is False
    assert receipt["admission"] == "DENY"


@pytest.mark.parametrize(
    ("after", "reason"),
    [
        ({"perm": "read_only"}, "permissions_changed"),
        ({"ip": "1.1.1.1"}, "ip_binding_changed"),
        ({"label": "changed"}, "config_changed"),
    ],
)
def test_any_config_change_is_recorded_as_blocker(after, reason):
    _, _, _, _, receipt = observe(after=after)
    assert "config_changed" in receipt["blocking_reasons"]
    assert reason in receipt["blocking_reasons"]
    assert receipt["config_policy_consistent"] is False
    assert receipt["admission"] == "DENY"


@pytest.mark.parametrize("field", ["uid", "mainUid", "acctLv", "posMode"])
def test_changed_identity_or_account_mode_never_replays_as_policy(field):
    wrong = {
        "uid": "700002",
        "mainUid": "700003",
        "acctLv": "3",
        "posMode": "long_short_mode",
    }[field]
    with pytest.raises(capture.AccountCaptureError):
        source(after={field: wrong})


def test_changed_session_or_saved_receipt_cannot_gain_policy_result():
    _, frozen, plan_pin, result, receipt = observe()
    changed_packet = json.loads(frozen.payload)
    changed_packet["observations"][1]["session_binding_id"] = "other-session"
    changed_bytes = capture._canonical(changed_packet).encode()
    with pytest.raises(
        policy.DemoApiKeyPolicyError, match="api_key_policy_source_invalid"
    ):
        policy.observe_v7_demo_api_key_policy(
            changed_bytes,
            expected_packet_sha256=hashlib.sha256(changed_bytes).hexdigest(),
            expected_plan_sha256=plan_pin,
            expected_policy_sha256=policy.POLICY_SHA256,
        )

    receipt["permissions_observed"] = False
    changed_receipt = json.dumps(
        receipt, sort_keys=True, separators=(",", ":")
    ).encode()
    with pytest.raises(
        policy.DemoApiKeyPolicyError, match="api_key_policy_receipt_mismatch"
    ):
        policy.verify_v7_demo_api_key_policy_receipt(
            frozen.payload,
            expected_packet_sha256=frozen.sha256,
            expected_plan_sha256=plan_pin,
            expected_policy_sha256=policy.POLICY_SHA256,
            receipt_json=changed_receipt,
            expected_receipt_sha256=hashlib.sha256(changed_receipt).hexdigest(),
        )
    assert result.execution_authority is False


def test_wrong_external_pins_or_policy_version_fail_before_interpretation():
    packet, frozen, plan_pin, _, _ = observe()
    for changed in (
        {"expected_packet_sha256": "0" * 64},
        {"expected_packet_sha256": packet.packet_sha256},
        {"expected_plan_sha256": "0" * 64},
        {"expected_policy_sha256": "0" * 64},
    ):
        with pytest.raises(policy.DemoApiKeyPolicyError):
            policy.observe_v7_demo_api_key_policy(
                frozen.payload,
                **{
                    "expected_packet_sha256": frozen.sha256,
                    "expected_plan_sha256": plan_pin,
                    "expected_policy_sha256": policy.POLICY_SHA256,
                    **changed,
                },
            )
    with pytest.raises(TypeError, match="expected_policy_sha256"):
        policy.observe_v7_demo_api_key_policy(
            frozen.payload,
            expected_packet_sha256=frozen.sha256,
            expected_plan_sha256=plan_pin,
        )
