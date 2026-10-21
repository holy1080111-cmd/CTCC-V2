"""Complete current-source raw bundle replay; never a runtime source issuer.

The restricted database retains these exact bytes. This is current observation
custody, not proof of historical availability or protection against a database
administrator. Account config bytes are private account evidence.
"""

from __future__ import annotations

import re

import app.public_market_source.public_runtime_journal as journal
from app.domain.source_primitives import (
    PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
    PublicReceiptError,
    canonical,
    decode,
    demo_public_v2_route,
    sha,
    validate_stamps,
)

MAX_BUNDLE_BYTES = 32 * 1024 * 1024
SCHEMA = "ctcc.public_current_raw_bundle.v4"
PROOF_SCHEMA = "ctcc.demo_operational_route_source_proof.v4"


def _deny():
    raise PublicReceiptError("public_v4_raw_bundle_invalid")


def _pin(value):
    if type(value) is not str or re.fullmatch("[a-f0-9]{64}", value) is None:
        _deny()


def replay_operational_proof_v4(proof):
    """Reconstruct config identity and measured clocks, without granting trust."""
    expected = {
        "schema_version",
        "account_plan_sha256",
        "credential_session_sha256",
        "account_identity",
        "route_region",
        "rest_origin",
        "ws_origin",
        "registration_site",
        "config_raw_hex",
        "config_raw_sha256",
        "config_canonical_sha256",
        "tls_peer_sha256",
        "request_start",
        "headers_received",
        "body_completed",
        "validated",
        "consumed",
    }
    if type(proof) is not dict or set(proof) != expected:
        _deny()
    for name in (
        "account_plan_sha256",
        "credential_session_sha256",
        "config_raw_sha256",
        "config_canonical_sha256",
        "tls_peer_sha256",
    ):
        _pin(proof[name])
    rest, ws, _, _ = demo_public_v2_route(proof["route_region"])
    if (
        proof["schema_version"] != PROOF_SCHEMA
        or proof["registration_site"] != "UNKNOWN"
        or proof["rest_origin"] != rest
        or proof["ws_origin"] != ws
        or type(proof["config_raw_hex"]) is not str
        or not 0 < len(proof["config_raw_hex"]) <= 16384
    ):
        _deny()
    raw = bytes.fromhex(proof["config_raw_hex"])
    if raw.hex() != proof["config_raw_hex"] or sha(raw) != proof["config_raw_sha256"]:
        _deny()
    config = decode(raw, 8192)
    if (
        type(config) is not dict
        or set(config) != {"code", "msg", "data"}
        or config["code"] != "0"
        or config["msg"] != ""
        or type(config["data"]) is not list
        or len(config["data"]) != 1
        or type(config["data"][0]) is not dict
        or sha(canonical(config)) != proof["config_canonical_sha256"]
    ):
        _deny()
    row = config["data"][0]
    identity = {key: row[key] for key in ("uid", "mainUid", "acctLv", "posMode")}
    if (
        identity != proof["account_identity"]
        or any(
            type(identity[k]) is not str
            or re.fullmatch("[1-9][0-9]{0,39}", identity[k]) is None
            for k in ("uid", "mainUid")
        )
        or identity["acctLv"] not in {"1", "2", "3", "4"}
        or identity["posMode"] not in {"net_mode", "long_short_mode"}
    ):
        _deny()
    validate_stamps(
        tuple(
            proof[name]
            for name in (
                "request_start",
                "headers_received",
                "body_completed",
                "validated",
                "consumed",
            )
        )
    )
    if (
        not 0
        < proof["consumed"]["monotonic_ns"] - proof["validated"]["monotonic_ns"]
        < 5_000_000_000
    ):
        _deny()
    return raw


class RuntimeBundleDirectoryV4:
    """Bounded replay view of retained exact bytes, with no native authority."""

    def __init__(self, files):
        self._files = files

    def names(self):
        return tuple(self._files)

    def read(self, name, maximum):
        raw = self._files[name]
        if type(raw) is not bytes or len(raw) > maximum:
            _deny()
        return raw


def freeze_runtime_bundle_v4(directory, proof):
    replay_operational_proof_v4(proof)
    plan_raw = directory.read("plan.json", journal.MAX_RAW)
    summary, events = journal.replay_runtime_attempt(
        directory, expected_plan_sha256=sha(plan_raw)
    )
    if len(events) > 2046 or summary["disposition"] != "captured":
        _deny()
    names = ["plan.json", "summary.json"]
    for event in events:
        names.append(f"event-{event['index']:04d}.json")
        if event["raw_sha256"] is not None:
            names.append(f"event-{event['index']:04d}.raw")
    raw = canonical(
        {
            "schema_version": SCHEMA,
            "current_source_policy_sha256": PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
            "operational_route_proof": proof,
            "files": [
                {"name": name, "raw_hex": directory.read(name, journal.MAX_RAW).hex()}
                for name in names
            ],
        }
    )
    replay_runtime_bundle_v4(raw, expected_sha256=sha(raw))
    return raw


def replay_runtime_bundle_v4(raw, *, expected_sha256):
    """Replay retained bytes; self-consistent bytes cannot mint an owned source."""
    try:
        _pin(expected_sha256)
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= MAX_BUNDLE_BYTES
            or sha(raw) != expected_sha256
        ):
            _deny()
        document = decode(raw, MAX_BUNDLE_BYTES)
        if (
            canonical(document) != raw
            or set(document)
            != {
                "schema_version",
                "current_source_policy_sha256",
                "operational_route_proof",
                "files",
            }
            or document["schema_version"] != SCHEMA
            or document["current_source_policy_sha256"]
            != PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256
            or type(document["files"]) is not list
            or not 2 <= len(document["files"]) <= 4096
        ):
            _deny()
        proof = document["operational_route_proof"]
        replay_operational_proof_v4(proof)
        files, total = {}, 0
        for entry in document["files"]:
            if (
                type(entry) is not dict
                or set(entry) != {"name", "raw_hex"}
                or type(entry["name"]) is not str
                or re.fullmatch(
                    r"(?:plan|summary)\.json|event-[0-9]{4}\.(?:json|raw)",
                    entry["name"],
                )
                is None
                or entry["name"] in files
                or type(entry["raw_hex"]) is not str
                or len(entry["raw_hex"]) > journal.MAX_RAW * 2
            ):
                _deny()
            payload = bytes.fromhex(entry["raw_hex"])
            if payload.hex() != entry["raw_hex"]:
                _deny()
            total += len(payload)
            if total > journal.MAX_TOTAL:
                _deny()
            files[entry["name"]] = payload
        directory = RuntimeBundleDirectoryV4(files)
        plan = decode(directory.read("plan.json", journal.MAX_RAW), journal.MAX_RAW)
        summary, events = journal.replay_runtime_attempt(
            directory, expected_plan_sha256=sha(files["plan.json"])
        )
        if (
            plan["schema_version"] != "ctcc.public.initial_runtime_plan.v4"
            or summary["disposition"] != "captured"
            or plan["operational_route_proof_sha256"] != sha(canonical(proof))
            or plan["account_plan_sha256"] != proof["account_plan_sha256"]
            or plan["credential_session_sha256"] != proof["credential_session_sha256"]
            or plan["rest_origin"] != proof["rest_origin"]
            or plan["ws_origin"] != proof["ws_origin"]
            or plan["invocation_started"]["utc_ns"] <= proof["consumed"]["utc_ns"]
            or plan["invocation_started"]["monotonic_ns"]
            <= proof["consumed"]["monotonic_ns"]
        ):
            _deny()
        return directory, proof, summary, events
    except Exception:  # noqa: BLE001 -- private raw config and response stay private
        raise PublicReceiptError("public_v4_raw_bundle_invalid") from None
