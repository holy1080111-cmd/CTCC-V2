"""Offline, DENY-only API-key policy observation from an original V7 packet.

A packet and its SHA pins establish replay consistency, not OKX authenticity.
No credential, signer, HTTP client, account authority, or execution path is here.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_api_key_policy.v1",
        "packet_contract": "ctcc.demo_current_account_capture.v7",
        "config_source": "replayed_original_config_before_and_after_raw_pages",
        "same_session": "original_V7_request_context_not_provider_session_revision",
        "required_permissions": ["read_only", "trade"],
        "forbidden_permissions": ["withdraw"],
        "ip_binding": "nonempty_unique_global_unicast_ipv4_fixed_exclusions_v1_in_unchanged_config",
        "full_config": "before_and_after_canonical_rows_must_match",
        "registration_region_verified": False,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
_PERMISSIONS = frozenset({"read_only", "trade", "withdraw"})
_DIGEST = re.compile(r"[a-f0-9]{64}")
# Fixed V1 exclusions avoid interpreter-dependent ipaddress.is_global results.
# This intentionally rejects IPv6 pending a separately versioned policy.
_EXCLUDED_V4 = tuple(
    ipaddress.IPv4Network(prefix)
    for prefix in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


class DemoApiKeyPolicyError(ValueError):
    """Fixed errors; never include private source fields in an exception."""


@dataclass(frozen=True, slots=True, repr=False)
class DemoApiKeyPolicyObservation:
    receipt_json: bytes

    @property
    def receipt_sha256(self) -> str:
        return journal.digest(self.receipt_json)

    @property
    def source_authenticity_verified(self) -> bool:
        return False

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False


def _deny(code: str) -> None:
    raise DemoApiKeyPolicyError(code)


def _permissions(value: object) -> frozenset[str] | None:
    if type(value) is not str or not 1 <= len(value) <= 64:
        return None
    parts = value.split(",")
    if any(not part or part.strip() != part for part in parts):
        return None
    if len(set(parts)) != len(parts) or not set(parts) <= _PERMISSIONS:
        return None
    return frozenset(parts)


def _bound_ips(value: object) -> tuple[str, ...] | None:
    if type(value) is not str or not 1 <= len(value) <= 1024:
        return None
    parts = value.split(",")
    if len(parts) > 20 or any(
        not part or part.strip() != part or "%" in part for part in parts
    ):
        return None
    try:
        parsed = tuple(ipaddress.ip_address(part) for part in parts)
    except ValueError:
        return None
    if any(
        type(address) is not ipaddress.IPv4Address
        or any(address in excluded for excluded in _EXCLUDED_V4)
        for address in parsed
    ):
        return None
    addresses = tuple(str(address) for address in parsed)
    if len(set(addresses)) != len(addresses):
        return None
    return addresses


def observe_v7_demo_api_key_policy(
    packet_bytes: bytes,
    *,
    expected_packet_sha256: str,
    expected_plan_sha256: str,
    expected_policy_sha256: str,
) -> DemoApiKeyPolicyObservation:
    """Recompute a redacted diagnostic from externally pinned original bytes.

    A positive policy observation is never a source-authenticity, region, IP
    egress, portfolio, or order eligibility claim. No caller-supplied permission
    or IP status can affect the result.
    """
    if (
        type(expected_policy_sha256) is not str
        or expected_policy_sha256 != POLICY_SHA256
    ):
        _deny("api_key_policy_version_mismatch")
    if (
        type(expected_packet_sha256) is not str
        or _DIGEST.fullmatch(expected_packet_sha256) is None
        or type(expected_plan_sha256) is not str
        or _DIGEST.fullmatch(expected_plan_sha256) is None
    ):
        _deny("api_key_policy_source_pin_invalid")
    try:
        packet = capture.verify_demo_account_packet(
            packet_bytes,
            expected_sha256=expected_packet_sha256,
            expected_plan_sha256=expected_plan_sha256,
        )
    except capture.AccountCaptureError:
        _deny("api_key_policy_source_invalid")
    if (
        type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV7
        or packet.schema_version != "ctcc.demo_current_account_capture.v7"
        or packet.plan_sha256 != expected_plan_sha256
        or packet.account_complete is not False
        or packet.source_authenticity_verified is not False
        or packet.execution_authority is not False
    ):
        _deny("api_key_policy_scope_invalid")

    pages = {
        name: tuple(page for page in packet.observations if page.request.stream == name)
        for name in ("config_before", "config_after")
    }
    if any(len(items) != 1 or len(items[0].rows) != 1 for items in pages.values()):
        _deny("api_key_policy_config_chain_invalid")
    before_page = pages["config_before"][0]
    after_page = pages["config_after"][0]
    if (
        before_page.session_binding_id != packet.plan.session_binding_id
        or after_page.session_binding_id != packet.plan.session_binding_id
        or after_page.identity_receipt_sha256 != before_page.receipt_sha256
    ):
        _deny("api_key_policy_session_chain_invalid")

    # These canonical rows are freshly recomputed from original response bytes
    # by verify_demo_account_packet above, rather than supplied policy DTOs.
    try:
        before = json.loads(before_page.rows[0].canonical_json)
        after = json.loads(after_page.rows[0].canonical_json)
    except (TypeError, ValueError):
        _deny("api_key_policy_config_chain_invalid")
    for row in (before, after):
        if (
            type(row) is not dict
            or row.get("uid") != packet.plan.expected_uid
            or row.get("mainUid") != packet.plan.expected_main_uid
            or row.get("acctLv") not in {"1", "2", "3", "4"}
            or row.get("posMode") not in {"net_mode", "long_short_mode"}
        ):
            _deny("api_key_policy_identity_or_mode_invalid")

    reasons: set[str] = set()
    if before_page.rows[0].canonical_json != after_page.rows[0].canonical_json:
        reasons.add("config_changed")
    before_permissions = _permissions(before.get("perm"))
    after_permissions = _permissions(after.get("perm"))
    if before_permissions is None or after_permissions is None:
        reasons.add("permissions_missing_or_malformed")
    elif before.get("perm") != after.get("perm"):
        reasons.add("permissions_changed")
    elif "withdraw" in before_permissions:
        reasons.add("withdraw_permission_enabled")
    elif before_permissions != frozenset({"read_only", "trade"}):
        reasons.add("read_trade_permission_missing")

    before_ips = _bound_ips(before.get("ip"))
    after_ips = _bound_ips(after.get("ip"))
    if before_ips is None or after_ips is None:
        reasons.add("ip_binding_missing_or_malformed")
    elif before.get("ip") != after.get("ip"):
        reasons.add("ip_binding_changed")

    receipt = journal.canonical(
        {
            "schema_version": "ctcc.demo_api_key_policy_observation.v1",
            "policy_sha256": POLICY_SHA256,
            "frozen_packet_sha256": expected_packet_sha256,
            "packet_record_sha256": packet.packet_sha256,
            "plan_sha256": expected_plan_sha256,
            "config_page_receipt_sha256s": {
                "before": before_page.receipt_sha256,
                "after": after_page.receipt_sha256,
            },
            "packet_identity_and_session_context_replayed": True,
            "observation_scope": "supplied_packet_replay_only",
            "permissions_observed": not any(
                reason in reasons
                for reason in (
                    "permissions_missing_or_malformed",
                    "permissions_changed",
                    "withdraw_permission_enabled",
                    "read_trade_permission_missing",
                )
            ),
            "ip_binding_list_observed": not any(
                reason in reasons
                for reason in (
                    "ip_binding_missing_or_malformed",
                    "ip_binding_changed",
                )
            ),
            "config_policy_consistent": not reasons,
            "blocking_reasons": sorted(reasons),
            "registration_region_verified": False,
            "provider_credential_session_verified": False,
            "current_egress_ip_verified": False,
            "source_authenticity_verified": False,
            "account_complete": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return DemoApiKeyPolicyObservation(receipt_json=receipt)


def verify_v7_demo_api_key_policy_receipt(
    packet_bytes: bytes,
    *,
    expected_packet_sha256: str,
    expected_plan_sha256: str,
    receipt_json: bytes,
    expected_receipt_sha256: str,
    expected_policy_sha256: str,
) -> DemoApiKeyPolicyObservation:
    """Reject changed saved readouts; always replay the underlying packet."""
    if (
        type(receipt_json) is not bytes
        or not 1 <= len(receipt_json) <= 8192
        or type(expected_receipt_sha256) is not str
        or _DIGEST.fullmatch(expected_receipt_sha256) is None
        or journal.digest(receipt_json) != expected_receipt_sha256
    ):
        _deny("api_key_policy_receipt_invalid")
    observed = observe_v7_demo_api_key_policy(
        packet_bytes,
        expected_packet_sha256=expected_packet_sha256,
        expected_plan_sha256=expected_plan_sha256,
        expected_policy_sha256=expected_policy_sha256,
    )
    if observed.receipt_json != receipt_json:
        _deny("api_key_policy_receipt_mismatch")
    return observed
