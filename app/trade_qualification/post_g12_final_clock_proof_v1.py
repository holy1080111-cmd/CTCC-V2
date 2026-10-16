"""Replayable, DENY-only proof of a post-G12 final freshness cutoff.

The native UTC observation is never replaced. The effective cutoff is the
later of that observation and elapsed monotonic time from the public capture.
This proof cannot authenticate its own native clock or grant order authority.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.domain.source_primitives import sha, utc_from_ns, validate_stamps
from app.trade_qualification import public_source_runtime as source_runtime

SCHEMA = "ctcc.post_g12_final_clock_proof.v1"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_FIELDS = frozenset(
    {
        "schema_version",
        "public_journal_sha256",
        "public_clock",
        "consume_clock",
        "final_clock",
        "effective_cutoff_at",
        "execution_authority",
    }
)


class FinalClockProofError(ValueError):
    """Fixed denial; no source or native clock value appears in the error."""


def effective_cutoff(
    public_clock: dict, final_clock: dict, consume_clock: dict | None = None
) -> datetime:
    try:
        checked = validate_stamps(
            (public_clock, final_clock)
            if consume_clock is None
            else (public_clock, consume_clock, final_clock)
        )
        first, last = checked[0], checked[-1]
        floors = [
            utc_from_ns(last.utc_ns),
            utc_from_ns(first.utc_ns + last.monotonic_ns - first.monotonic_ns),
        ]
        if consume_clock is not None:
            consumed = checked[1]
            floors.append(
                utc_from_ns(consumed.utc_ns + last.monotonic_ns - consumed.monotonic_ns)
            )
        return max(floors)
    except Exception:  # noqa: BLE001 -- untrusted clock samples stay DENY
        raise FinalClockProofError("post_g12_final_clock_invalid") from None


def make_proof(
    public_clock: dict,
    final_clock: dict,
    public_journal_sha256: str,
    *,
    consume_clock: dict | None = None,
) -> dict:
    if (
        type(public_journal_sha256) is not str
        or _HEX.fullmatch(public_journal_sha256) is None
    ):
        raise FinalClockProofError("post_g12_final_clock_invalid")
    cutoff = effective_cutoff(public_clock, final_clock, consume_clock)
    return {
        "schema_version": SCHEMA,
        "public_journal_sha256": public_journal_sha256,
        "public_clock": dict(public_clock),
        "consume_clock": None if consume_clock is None else dict(consume_clock),
        "final_clock": dict(final_clock),
        "effective_cutoff_at": cutoff.isoformat(),
        "execution_authority": False,
    }


def replay_proof(value: dict, *, expected_public_journal_sha256: str) -> datetime:
    try:
        if (
            type(value) is not dict
            or set(value) != _FIELDS
            or value["schema_version"] != SCHEMA
            or value["public_journal_sha256"] != expected_public_journal_sha256
            or type(expected_public_journal_sha256) is not str
            or _HEX.fullmatch(expected_public_journal_sha256) is None
            or value["execution_authority"] is not False
            or any(
                type(value[name]) is not dict
                or set(value[name]) != {"utc_ns", "monotonic_ns"}
                for name in ("public_clock", "final_clock")
            )
            or value["consume_clock"] is not None
            and (
                type(value["consume_clock"]) is not dict
                or set(value["consume_clock"]) != {"utc_ns", "monotonic_ns"}
            )
        ):
            raise ValueError
        cutoff = effective_cutoff(
            value["public_clock"], value["final_clock"], value["consume_clock"]
        )
        if value["effective_cutoff_at"] != cutoff.isoformat():
            raise ValueError
        return cutoff
    except Exception:  # noqa: BLE001 -- no untrusted proof content in errors
        raise FinalClockProofError("post_g12_final_clock_proof_invalid") from None


def read_persisted_public_clock(
    root,
    *,
    expected_journal_sha256: str,
    expected_packet_sha256: str,
    expected_root_identity=None,
) -> tuple[dict, tuple[int, int]]:
    """Reopen the pinned raw public journal; its completed pair is the anchor."""
    try:
        identity = source_runtime._native_recheck_root_identity(root)
        if expected_root_identity is not None and identity != expected_root_identity:
            raise ValueError
        with source_runtime._reopen_runtime_journal(root) as directory:
            plan_raw = directory.read("plan.json", source_runtime.MAX_RAW)
            summary_raw = directory.read("summary.json", source_runtime.MAX_RAW)
            if sha(summary_raw) != expected_journal_sha256:
                raise ValueError
            summary, _, packet = source_runtime.replay_public_runtime(
                directory, expected_plan_sha256=sha(plan_raw)
            )
            if (
                summary["disposition"] != "captured"
                or packet is None
                or packet.bundle_sha256 != expected_packet_sha256
                or directory.read("summary.json", source_runtime.MAX_RAW) != summary_raw
                or source_runtime._native_recheck_root_identity(root) != identity
            ):
                raise ValueError
            completed = summary["completed"]
        validate_stamps((completed, completed))
        return completed, identity
    except Exception:  # noqa: BLE001 -- no public source/path detail in denial
        raise FinalClockProofError("post_g12_public_clock_readback_failed") from None
