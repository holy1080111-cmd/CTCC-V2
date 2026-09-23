"""Synthetic DB0020 observations in the existing isolated process-kill probe.

There is no exchange client or signing. One failed capture safely finalizes a
complete JSON buffer; a second capture holds an unfinished buffer only in RAM.
Actual SIGKILL/restart belongs to the surrounding Docker harness, not this code.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture as capture
from app.trade_qualification.account_capture_journal import (
    canonical,
    checked_event,
    digest,
    start_owned_journal,
)
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

_SAFE_RAW = b'{"code":"0","data":[]}'
_PARTIAL_RAW = b'{"code":"0","data":['
_SCENARIOS = ("safe_failed_capture", "unfinished_ram_prefix")
_TOKENS = (
    "synthetic-crash-probe-key",
    "synthetic-crash-probe-secret",
    "synthetic-crash-probe-passphrase",
)


@dataclass(frozen=True, slots=True, repr=False)
class SeededAccountCaptures:
    records: list[dict]
    owners: tuple[object, ...]


def _scope(scope):
    scope = checked_bootstrap(scope, LedgerScope)
    if (
        scope.environment != "demo"
        or not scope.account_id.startswith("987654321")
        or scope.settlement_currency != "USDT"
    ):
        raise RuntimeError("account_probe_synthetic_scope_required")
    return scope


def _chain_identity(chain):
    """Pin persisted content, including DB receipt time, excluding new read time."""
    return digest(
        canonical(
            [
                {
                    "event_sha256": digest(item.event.event_json),
                    "raw_sha256": None
                    if item.event.raw_body is None
                    else digest(item.event.raw_body),
                    "packet_sha256": None
                    if item.event.packet_payload is None
                    else digest(item.event.packet_payload),
                    "db_recorded_at": capture._utc(item.db_recorded_at).isoformat(),
                }
                for item in chain
            ]
        )
    )


def _verify_scenario(chain, scenario):
    records = [checked_event(item.event) for item in chain]
    if not records or any(item.event.packet_payload is not None for item in chain):
        raise RuntimeError("account_probe_unexpected_packet")
    raw = [item.event.raw_body for item in chain if item.event.raw_body is not None]
    if scenario == "safe_failed_capture":
        if (
            raw != [_SAFE_RAW]
            or records[-1]["kind"] != "terminal"
            or records[-1]["outcome"] != "failed"
        ):
            raise RuntimeError("account_probe_finalized_raw_missing")
    else:
        last = records[-1]
        if (
            raw
            or last["kind"] != "body_progress"
            or last["outcome"] != "in_progress"
            or last["data"]["observed_bytes"] != len(_PARTIAL_RAW)
            or last["data"]["prefix_sha256"] != digest(_PARTIAL_RAW)
            or last["data"]["body_completed_at"] is not None
            or last["data"]["raw_retention"] != "buffered_not_durable"
        ):
            raise RuntimeError("account_probe_unfinished_prefix_changed")


async def seed_account_captures(sessions, *, scope, clock=None):
    """Retain actual in-process buffers until the outer harness kills its owner."""
    scope = _scope(scope)
    clock = (lambda: datetime.now(UTC)) if clock is None else clock
    ledger = QualificationLedgerRepository(sessions, clock=clock)
    before = await ledger.read_bootstrap_checkpoint(scope)
    repository = AccountCaptureJournalRepository(sessions, clock=clock)
    records, owners = [], []
    for scenario in _SCENARIOS:
        now = capture._utc(clock())
        cutoff = now.replace(microsecond=0) - timedelta(seconds=1)
        plan = capture.DemoAccountCapturePlan(
            plan_id="synthetic-account-crash-probe",
            created_at=now,
            expected_uid=scope.account_id,
            expected_main_uid=scope.account_id,
            session_binding_id="synthetic-account-crash-session",
            settlement_currency=scope.settlement_currency,
            leverage_instrument_ids=("BTC-USDT-SWAP",),
            history_start=cutoff - timedelta(days=7),
            history_end=cutoff,
            max_batch_seconds=1,
        )
        owner = await start_owned_journal(
            repository=repository,
            scope=scope,
            plan=plan,
            checkpoint=before.state_sha256,
            clock=clock,
            tokens=_TOKENS,
        )
        owners.append(owner)
        stream = capture.streams_for_plan(plan)[0]
        await owner.request(
            stream=stream,
            page_index=0,
            after=None,
            previous_sha=None,
            identity=None,
            spec=capture.account_request(plan, stream, None),
            started=clock(),
        )
        await owner.headers(clock(), 200)
        owner.transport(None)  # Explicit synthetic source; never authenticated.
        await owner.chunk(
            _SAFE_RAW if scenario == "safe_failed_capture" else _PARTIAL_RAW,
            clock(),
        )
        if scenario == "safe_failed_capture":
            await owner.body_complete(clock())
            await owner.close_acquisition(successful=False)
            await owner.finish(error=RuntimeError("synthetic_acquisition_rejected"))
        chain = await repository.read_chain(scope, owner.capture_id)
        _verify_scenario(chain, scenario)
        records.append(
            {
                "scenario": scenario,
                "capture_id": owner.capture_id,
                "events": len(chain),
                "head_sha256": digest(chain[-1].event.event_json),
                "chain_sha256": _chain_identity(chain),
                "local_checkpoint_sha256": before.state_sha256,
            }
        )
    after = await ledger.read_bootstrap_checkpoint(scope)
    if after.state != before.state or after.state_sha256 != before.state_sha256:
        raise RuntimeError("account_probe_seed_changed_ledger")
    return SeededAccountCaptures(records, tuple(owners))


def confirm_ready_account_captures(seeded):
    if type(seeded) is not SeededAccountCaptures or len(seeded.owners) != 2:
        raise RuntimeError("account_probe_owner_missing")
    for index, owner in enumerate(seeded.owners):
        if (
            owner.capture_id != seeded.records[index]["capture_id"]
            or owner.previous != seeded.records[index]["head_sha256"]
            or owner.finished != (index == 0)
            or owner.closed != (index == 0)
            or bytes(owner.current["body"]) != (b"" if index == 0 else _PARTIAL_RAW)
        ):
            raise RuntimeError("account_probe_owner_changed_before_publish")


def _decode_records(records):
    keys = {
        "scenario",
        "capture_id",
        "events",
        "head_sha256",
        "chain_sha256",
        "local_checkpoint_sha256",
    }
    if type(records) is not list or len(records) != 2:
        raise RuntimeError("account_probe_marker_invalid")
    for expected, record in zip(_SCENARIOS, records, strict=True):
        if (
            type(record) is not dict
            or set(record) != keys
            or record["scenario"] != expected
            or type(record["capture_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", record["capture_id"]) is None
            or type(record["events"]) is not int
            or not 1 <= record["events"] <= 32
            or any(
                type(record[key]) is not str
                or re.fullmatch(r"[a-f0-9]{64}", record[key]) is None
                for key in ("head_sha256", "chain_sha256", "local_checkpoint_sha256")
            )
        ):
            raise RuntimeError("account_probe_marker_invalid")
    if records[0]["capture_id"] == records[1]["capture_id"]:
        raise RuntimeError("account_probe_duplicate_capture")
    return records


async def _wait_for_recovery_boundary(first, clock):
    # Observe real passage of time. No future receipt or synthetic replacement of
    # a DB timestamp is used to make recovery's audit-only deadline pass.
    deadline = capture._utc(
        datetime.fromisoformat(first["data"]["recovery_not_before"])
    )
    async with asyncio.timeout(25):
        while capture._utc(clock()) <= deadline:
            await asyncio.sleep(0.1)


async def verify_account_captures(sessions, records, *, scope, clock=None):
    scope = _scope(scope)
    records = _decode_records(records)
    clock = (lambda: datetime.now(UTC)) if clock is None else clock
    ledger = QualificationLedgerRepository(sessions, clock=clock)
    before = await ledger.read_bootstrap_checkpoint(scope)
    repository = AccountCaptureJournalRepository(sessions, clock=clock)
    for record in records:
        chain = await repository.read_chain(scope, record["capture_id"])
        if (
            len(chain) != record["events"]
            or _chain_identity(chain) != record["chain_sha256"]
            or digest(chain[-1].event.event_json) != record["head_sha256"]
            or before.state_sha256 != record["local_checkpoint_sha256"]
        ):
            raise RuntimeError("account_probe_durable_prefix_changed")
        _verify_scenario(chain, record["scenario"])
        if record["scenario"] == "unfinished_ram_prefix":
            await _wait_for_recovery_boundary(checked_event(chain[0].event), clock)
            receipt = await repository.recover_interrupted(
                scope,
                capture_id=record["capture_id"],
                expected_head_sha256=record["head_sha256"],
            )
            recovery = checked_event(receipt.event)
            missing = recovery["data"].get("missing_raw")
            if (
                recovery["kind"] != "recovery"
                or recovery["outcome"] != "interrupted_owner_unknown"
                or type(missing) is not list
                or len(missing) != 1
                or missing[0]["last_durable_observed_bytes"] != len(_PARTIAL_RAW)
                or missing[0]["last_durable_prefix_sha256"] != digest(_PARTIAL_RAW)
                or missing[0]["additional_unobserved_bytes"] is not None
                or missing[0]["original_body_completed_at"] is not None
                or missing[0]["raw_retention"] != "unavailable_owner_unverified"
                or receipt.event.raw_body is not None
                or receipt.event.packet_payload is not None
            ):
                raise RuntimeError("account_probe_recovery_invented_data")
            after_chain = await repository.read_chain(scope, record["capture_id"])
            if len(after_chain) != len(chain) + 1 or _chain_identity(
                after_chain[:-1]
            ) != _chain_identity(chain):
                raise RuntimeError("account_probe_recovery_changed_prefix")
            if _chain_identity((after_chain[-1],)) != _chain_identity((receipt,)):
                raise RuntimeError("account_probe_recovery_readback_changed")
    after = await ledger.read_bootstrap_checkpoint(scope)
    if after.state != before.state or after.state_sha256 != before.state_sha256:
        raise RuntimeError("account_probe_recovery_changed_ledger")
