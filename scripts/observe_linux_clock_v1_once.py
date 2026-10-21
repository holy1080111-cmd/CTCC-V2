"""Observe one native Linux kernel clock, without input profiles or retries.

Invoke as ``python -m scripts.observe_linux_clock_v1_once``. Stdout contains
one bounded JSON record; a rejected observation exits 1, forbidden args exit 2.
This is a kernel-observation probe, never UTC/source/execution acceptance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys

from app.domain import linux_native_clock_v1 as clock
from app.domain.source_primitives import PublicReceiptError, canonical, decode

MAX_OUTPUT_BYTES = 128 * 1024

_ERROR_CODES = frozenset(
    {
        "linux_clock_evidence_invalid",
        "linux_clock_evidence_noncanonical",
        "linux_clock_runtime_file_unsafe",
        "linux_clock_read_budget",
        "linux_clock_runtime_changed",
        "linux_clock_native_read_failed",
        "linux_clock_platform_unsupported",
        "linux_clock_abi_unsupported",
        "linux_clock_environment_override",
        "linux_clock_python_hook",
        "linux_clock_profile_invalid",
        "linux_clock_profile_roles_invalid",
        "linux_clock_mapping_invalid",
        "linux_clock_runtime_profile_mismatch",
        "linux_clock_mapping_missing",
        "linux_clock_timezone_unknown",
        "linux_clock_boot_identity_invalid",
        "linux_clock_namespace_offset",
        "linux_clock_namespace_unknown",
        "linux_clock_kernel_identity_invalid",
        "linux_clock_profile_ambiguous",
        "linux_clock_profile_unsupported",
        "linux_clock_native_api_unavailable",
        "linux_clock_native_hook",
        "linux_clock_native_api_failed",
        "linux_clock_sample_unbounded",
        "linux_clock_utc_conversion_failed",
        "linux_clock_probe_expired",
        "linux_clock_suspend_or_reversed",
        "linux_clock_status_invalid",
        "linux_clock_kernel_unsynchronized",
        "linux_clock_uncertainty_unbounded",
        "linux_clock_status_time_invalid",
        "linux_clock_authority_forbidden",
        "linux_clock_resolution_unsupported",
        "linux_clock_pll_mode_mismatch",
        "linux_clock_status_causal_order",
        "linux_clock_owned_observation_required",
        "linux_clock_owned_observation_not_transferable",
        # Fixed shared primitive denials do not expose exception details.
        "clock_reversed",
        "clock_jump",
        "clock_lease_expired",
        "clock_sequence_invalid",
    }
)


def _status_markers(payload):
    empty = {
        "original_raw_status_available": False,
        "original_raw_status_sha256": None,
        "original_raw_status_length": None,
        "original_status_record_sha256": None,
        "original_status_record_base64": None,
    }
    if type(payload) is not bytes or not 0 < len(payload) <= clock.MAX_PAYLOAD_BYTES:
        return empty
    try:
        record = decode(payload, clock.MAX_PAYLOAD_BYTES)
        if canonical(record) != payload:
            return empty
        if type(record) is not dict or set(record) != {
            "return_state",
            "errno",
            "raw_base64",
            "raw_sha256",
            "raw_length",
        }:
            return empty
        if (
            type(record["return_state"]) is not int
            or not -1 <= record["return_state"] <= 5
            or type(record["errno"]) is not int
            or not 0 <= record["errno"] <= 4095
        ):
            return empty
        if (
            type(record["raw_base64"]) is not str
            or type(record["raw_length"]) is not int
            or record["raw_length"] != 208
        ):
            return empty
        raw = base64.b64decode(record["raw_base64"], validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if (
            len(raw) != 208
            or digest != record["raw_sha256"]
            or base64.b64encode(raw).decode("ascii") != record["raw_base64"]
        ):
            return empty
    except (ValueError, TypeError):
        return empty
    return {
        "original_raw_status_available": True,
        "original_raw_status_sha256": digest,
        "original_raw_status_length": len(raw),
        "original_status_record_sha256": hashlib.sha256(payload).hexdigest(),
        "original_status_record_base64": base64.b64encode(payload).decode("ascii"),
    }


def _error_code(error):
    if (
        len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in _ERROR_CODES
    ):
        return error.args[0]
    return "linux_clock_probe_unclassified_clock_error"


def main() -> int:
    record = {
        "schema_version": "ctcc.linux_kernel_clock_once_probe.v1",
        "scope": "read_only_kernel_observation_only",
        "actual_accepted": False,
        "error_code": None,
        "observation_attempts": 0,
        "consume_attempts": 0,
        "owned_token_reuse_denied": False,
        "evidence_sha256": None,
        "original_evidence_base64": None,
        "original_evidence_length": None,
        "profile_id": None,
        "pll_updates_enabled": None,
        "upstream_server": None,
        "last_upstream_sync_at": None,
        "utc_certified": False,
        "upstream_freshness_verified": False,
        "source_authenticity_verified": False,
        "historical_availability_verified": False,
        "execution_authority": False,
        **_status_markers(None),
    }
    exit_code = 1
    if len(sys.argv) != 1:
        record["error_code"] = "linux_clock_probe_arguments_forbidden"
        exit_code = 2
    else:
        try:
            record["observation_attempts"] = 1
            token = clock.observe_linux_clock_v1()
            record["consume_attempts"] = 1
            payload = clock.consume_owned_linux_clock_v1(token)
            if (
                type(payload) is not bytes
                or not 0 < len(payload) <= clock.MAX_PAYLOAD_BYTES
            ):
                raise clock.LinuxClockError("linux_clock_evidence_invalid")
            evidence = clock.replay_linux_clock_observation_v1(payload)
            record["evidence_sha256"] = hashlib.sha256(payload).hexdigest()
            record["original_evidence_base64"] = base64.b64encode(payload).decode(
                "ascii"
            )
            record["original_evidence_length"] = len(payload)
            record["profile_id"] = evidence["profile_id"]
            record["pll_updates_enabled"] = evidence["pll_updates_enabled"]
            record.update(_status_markers(canonical(evidence["status"])))
            record["consume_attempts"] = 2
            try:
                clock.consume_owned_linux_clock_v1(token)
            except clock.LinuxClockError as error:
                if _error_code(error) != "linux_clock_owned_observation_required":
                    raise
                record["owned_token_reuse_denied"] = True
            else:
                record["error_code"] = "linux_clock_probe_token_reuse_accepted"
            if record["owned_token_reuse_denied"]:
                record["actual_accepted"] = True
                exit_code = 0
        except PublicReceiptError as error:
            record["error_code"] = _error_code(error)
            if type(error) is clock.LinuxKernelStatusError:
                record.update(_status_markers(error.observation_bytes))
        except Exception:  # noqa: BLE001 - Print fixed failure, never raw diagnostics.
            record["error_code"] = "linux_clock_probe_unexpected_failure"
    serialized = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    if len(serialized.encode("ascii")) > MAX_OUTPUT_BYTES:
        record.update(
            {
                "actual_accepted": False,
                "error_code": "linux_clock_probe_output_budget",
                "evidence_sha256": None,
                "original_evidence_base64": None,
                "original_evidence_length": None,
                "profile_id": None,
                **_status_markers(None),
            }
        )
        serialized = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        exit_code = 1
    sys.stdout.write(serialized)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
