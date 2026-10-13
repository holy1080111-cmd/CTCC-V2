"""Read the local all-currency exposure ledger after this invocation's V4.

This is an append-only diagnostic, not a current portfolio, reservation, intent,
or order authority. V4 still starts with caller-origin pre-evidence, and the
checkpoint cannot authenticate exchange history or exclude future writers.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PosixPath, WindowsPath
from typing import Literal

from app.database.repositories.qualification_ledger import (
    PortfolioLocalCheckpoint,
    QualificationLedgerRepository,
)
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import public_source_runtime as source_runtime
from app.trade_qualification.event_observation import (
    MAX_OBSERVATION_BYTES,
    LedgerEventObservation,
)
from app.trade_qualification.reservations import (
    LedgerScope,
    LedgerScopeState,
    ReservationReceipt,
    checked_bootstrap,
)

_SCHEMA = "ctcc.post_g12_local_portfolio_observation.v5"
_MAX_RECEIPT = 3072
_MAX_CHECKPOINT = 262144
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_REASON = re.compile(r"[a-z0-9_]{1,80}\Z")
_CODES = frozenset(
    {
        "v4_incomplete",
        "local_state_blocked",
        "local_state_stable_not_authority",
        "public_journal_readback_invalid_at_local_readback",
        "public_validation_clock_invalid_at_local_readback",
        "public_g1_rejected_at_local_readback",
        "original_event_already_recorded",
    }
)
_FALSE_FIELDS = (
    "original_source_verified",
    "account_complete",
    "execution_recheck_performed",
    "atomic_risk_reserved",
    "durable_intent_created",
    "execution_authority",
    "order_submitted",
)
_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "v4_code",
        "v4_receipt_sha256",
        "scope_sha256",
        "account_uid_sha256",
        "candidate_sha256",
        "original_event_key",
        "candidate_deadline_at",
        "v4_event_read_received_at",
        "first_checkpoint_sha256",
        "second_checkpoint_sha256",
        "first_observed_at",
        "first_received_at",
        "second_observed_at",
        "second_received_at",
        "local_blocking_reasons",
        "local_flat_at_reads",
        "admission",
        *_FALSE_FIELDS,
    }
)
_ADVANCED_V4 = frozenset(
    {"trusted_execution_inputs_missing", "original_event_already_recorded"}
)


class PostG12LocalPortfolioObservationError(ValueError):
    """A fixed denial without native path, account, exchange, or SQL text."""


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError
    if value.utcoffset().total_seconds() != 0:
        raise ValueError
    return value


def _utc_text(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError
    instant = _utc(datetime.fromisoformat(value))
    if instant.isoformat() != value:
        raise ValueError
    return instant


@dataclass(frozen=True, slots=True, repr=False)
class PostG12LocalPortfolioObservationV5:
    receipt_json: bytes

    def __post_init__(self):
        try:
            if type(self.receipt_json) is not bytes:
                raise ValueError
            record = decode(self.receipt_json, _MAX_RECEIPT)
            if (
                type(record) is not dict
                or set(record) != _FIELDS
                or canonical(record) != self.receipt_json
                or record["schema_version"] != _SCHEMA
                or record["code"] not in _CODES
                or record["v4_code"] not in prior._CODES
                or record["admission"] != "DENY"
                or any(record[name] is not False for name in _FALSE_FIELDS)
                or any(
                    type(record[name]) is not str
                    or _HEX.fullmatch(record[name]) is None
                    for name in (
                        "v4_receipt_sha256",
                        "scope_sha256",
                        "account_uid_sha256",
                        "candidate_sha256",
                        "original_event_key",
                    )
                )
            ):
                raise ValueError
            deadline = _utc_text(record["candidate_deadline_at"])
            local = record["local_blocking_reasons"]
            if (
                type(local) is not list
                or len(local) > 32
                or any(
                    type(reason) is not str or _REASON.fullmatch(reason) is None
                    for reason in local
                )
                or local != sorted(set(local))
                or type(record["local_flat_at_reads"]) is not bool
            ):
                raise ValueError
            checkpoint_fields = (
                "v4_event_read_received_at",
                "first_checkpoint_sha256",
                "second_checkpoint_sha256",
                "first_observed_at",
                "first_received_at",
                "second_observed_at",
                "second_received_at",
            )
            if record["v4_code"] not in _ADVANCED_V4:
                if (
                    record["code"] != "v4_incomplete"
                    or any(record[name] is not None for name in checkpoint_fields)
                    or local
                    or record["local_flat_at_reads"] is not False
                ):
                    raise ValueError
            else:
                if any(record[name] is None for name in checkpoint_fields):
                    raise ValueError
                for name in ("first_checkpoint_sha256", "second_checkpoint_sha256"):
                    if (
                        type(record[name]) is not str
                        or _HEX.fullmatch(record[name]) is None
                    ):
                        raise ValueError
                if (
                    record["first_checkpoint_sha256"]
                    != record["second_checkpoint_sha256"]
                ):
                    raise ValueError
                sequence = [
                    _utc_text(record[name])
                    for name in (
                        "v4_event_read_received_at",
                        "first_observed_at",
                        "first_received_at",
                        "second_observed_at",
                        "second_received_at",
                    )
                ]
                if not (
                    sequence[0]
                    < sequence[1]
                    <= sequence[2]
                    < sequence[3]
                    <= sequence[4]
                    < deadline
                ):
                    raise ValueError
                flat = not local
                expected_code = (
                    "original_event_already_recorded"
                    if record["v4_code"] == "original_event_already_recorded"
                    else "local_state_stable_not_authority"
                    if flat
                    else "local_state_blocked"
                )
                if record["local_flat_at_reads"] is not flat or record["code"] not in {
                    expected_code,
                    "public_journal_readback_invalid_at_local_readback",
                    "public_validation_clock_invalid_at_local_readback",
                    "public_g1_rejected_at_local_readback",
                }:
                    raise ValueError
        except Exception:  # noqa: BLE001 -- untrusted bytes never enter exceptions
            raise PostG12LocalPortfolioObservationError("v5_receipt_invalid") from None

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def read_post_g12_local_portfolio_observation_v5(
    root, *, expected_sha256, expected_root_identity
) -> PostG12LocalPortfolioObservationV5:
    """Read this V5 receipt from its separate native no-clobber root."""
    try:
        if (
            type(root) not in (PosixPath, WindowsPath)
            or not root.is_absolute()
            or type(expected_sha256) is not str
            or _HEX.fullmatch(expected_sha256) is None
        ):
            raise ValueError
        with source_runtime._native_recheck_root(root) as directory:
            if prior.joined.prior._native_directory_identity(
                directory
            ) != expected_root_identity or set(directory.names()) != {"receipt.json"}:
                raise ValueError
            raw = directory.read("receipt.json", _MAX_RECEIPT)
            if sha(raw) != expected_sha256:
                raise ValueError
            result = PostG12LocalPortfolioObservationV5(raw)
        if source_runtime._native_recheck_root_identity(root) != expected_root_identity:
            raise ValueError
        return result
    except Exception:  # noqa: BLE001 -- no local paths or payloads in failure
        raise PostG12LocalPortfolioObservationError("v5_readback_failed") from None


def _publish_receipt(root, receipt, *, expected_root_identity):
    if type(receipt) is not PostG12LocalPortfolioObservationV5:
        raise PostG12LocalPortfolioObservationError("v5_exact_receipt_required")
    try:
        with source_runtime._native_recheck_root(root) as directory:
            if (
                prior.joined.prior._native_directory_identity(directory)
                != expected_root_identity
                or directory.names()
            ):
                raise ValueError
            directory.publish("receipt.json", receipt.receipt_json)
            if directory.read("receipt.json", _MAX_RECEIPT) != receipt.receipt_json:
                raise ValueError
        replayed = read_post_g12_local_portfolio_observation_v5(
            root,
            expected_sha256=receipt.receipt_sha256,
            expected_root_identity=expected_root_identity,
        )
        if replayed.receipt_json != receipt.receipt_json:
            raise ValueError
    except Exception:  # noqa: BLE001 -- never erase accepted V4 or V5 bytes
        raise PostG12LocalPortfolioObservationError("v5_publish_failed") from None


def _v4_event_read_received(root, receipt, *, expected_root_identity):
    try:
        with source_runtime._native_recheck_root(root) as directory:
            if prior.joined.prior._native_directory_identity(
                directory
            ) != expected_root_identity or set(directory.names()) != {
                "receipt.json",
                "observation.json",
            }:
                raise ValueError
            raw = directory.read("observation.json", MAX_OBSERVATION_BYTES)
            observed = LedgerEventObservation.model_validate_json(raw, strict=True)
            if (
                observed.canonical_json.encode("ascii") != raw
                or prior._observation_bytes_for_receipt(observed, receipt) != raw
            ):
                raise ValueError
        if source_runtime._native_recheck_root_identity(root) != expected_root_identity:
            raise ValueError
        return _utc(observed.received_at)
    except Exception:  # noqa: BLE001 -- never include private event data
        raise PostG12LocalPortfolioObservationError(
            "v5_v4_event_readback_failed"
        ) from None


def _legacy_datetime(value):
    if value is None:
        return None
    if type(value) is not str or len(value) > 40:
        raise ValueError
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None or instant.isoformat() != value:
        raise ValueError
    return instant


def _checked_local_structure(record, scope):
    """Reject missing or contradictory nested storage; never infer flatness."""
    reasons = set(record["blocking_reasons"])
    states = record["currency_states"]
    if not 1 <= len(states) <= 128:
        raise ValueError
    currencies = []
    has_active = False
    for raw in states:
        if type(raw) is not dict or set(raw) != set(LedgerScopeState.model_fields):
            raise ValueError
        state = LedgerScopeState.model_validate_json(canonical(raw), strict=True)
        if (
            type(raw["scope"]) is not dict
            or set(raw["scope"]) != set(LedgerScope.model_fields)
            or state.scope.environment != scope.environment
            or state.scope.account_id != scope.account_id
        ):
            raise ValueError
        currencies.append(state.scope.settlement_currency)
        if any(
            type(item) is not dict
            or set(item) != set(ReservationReceipt.model_fields)
            or receipt.scope != state.scope
            or receipt.state == "reconciled_flat"
            for item, receipt in zip(raw["active"], state.active, strict=True)
        ):
            raise ValueError
        has_active |= bool(state.active)
    if (
        currencies != sorted(set(currencies))
        or scope.settlement_currency not in currencies
        or (has_active and "all_currency_local_holds_unresolved" not in reasons)
    ):
        raise ValueError
    intents = record["unresolved_intents"]
    if len(intents) > 2048 or (
        intents and "all_currency_local_intents_unresolved" not in reasons
    ):
        raise ValueError
    for item in intents:
        if (
            type(item) is not list
            or len(item) != 5
            or type(item[0]) is not str
            or _HEX.fullmatch(item[0]) is None
            or type(item[1]) is not str
            or item[1] not in currencies
            or item[2] not in ("reserved", "consumed", "uncertain")
            or type(item[3]) is not int
            or item[3] < 0
            or type(item[4]) is not str
            or _REASON.fullmatch(item[4]) is None
        ):
            raise ValueError
    legacy = record["legacy"]
    if set(legacy) != {
        "automation",
        "sync_checkpoint",
        "positions",
        "orders",
        "algos",
    }:
        raise ValueError
    for key in ("positions", "orders", "algos"):
        identities = legacy[key]
        if (
            type(identities) is not list
            or len(identities) > 2048
            or any(type(item) is not str or not item for item in identities)
            or identities != sorted(set(identities))
            or (identities and "legacy_unattributed_active_" + key not in reasons)
        ):
            raise ValueError
    automation = legacy["automation"]
    if automation is None:
        if "legacy_automation_initialization_unknown" not in reasons:
            raise ValueError
    else:
        if (
            type(automation) is not dict
            or set(automation)
            != {
                "armed",
                "emergency_stop",
                "locked",
                "lock_reasons",
                "active_instrument_id",
                "active_client_order_id",
                "active_start_equity",
                "active_started_at",
                "active_trades",
                "last_started_at",
                "last_completed_at",
                "last_error",
                "updated_at",
            }
            or any(
                type(automation[key]) is not bool
                for key in ("armed", "emergency_stop", "locked")
            )
            or type(automation["lock_reasons"]) is not list
            or type(automation["active_trades"]) not in (dict, list)
            or any(
                automation[key] is not None and type(automation[key]) is not str
                for key in (
                    "active_instrument_id",
                    "active_client_order_id",
                    "active_start_equity",
                    "last_error",
                )
            )
        ):
            raise ValueError
        started = _legacy_datetime(automation["last_started_at"])
        completed = _legacy_datetime(automation["last_completed_at"])
        _legacy_datetime(automation["active_started_at"])
        _legacy_datetime(automation["updated_at"])
        tracked = bool(automation["active_trades"]) or any(
            automation[key] is not None
            for key in (
                "active_instrument_id",
                "active_client_order_id",
                "active_start_equity",
                "active_started_at",
            )
        )
        inflight = (
            automation["armed"]
            or automation["locked"]
            or bool(automation["lock_reasons"])
            or automation["last_error"] not in (None, "")
            or (started is not None and (completed is None or started > completed))
        )
        if (tracked and "legacy_tracked_exposure_unattributed" not in reasons) or (
            inflight and "legacy_inflight_or_uncertain" not in reasons
        ):
            raise ValueError
    sync = legacy["sync_checkpoint"]
    if sync is None:
        if "legacy_reconciliation_initialization_unknown" not in reasons:
            raise ValueError
    else:
        if (
            type(sync) is not dict
            or set(sync)
            != {
                "status",
                "order_count",
                "position_count",
                "algo_order_count",
                "last_error",
                "reconciled_at",
                "updated_at",
                "exact_uid_bound",
                "credential_session_binding_id",
            }
            or type(sync["status"]) is not str
            or any(
                type(sync[key]) is not int
                for key in ("order_count", "position_count", "algo_order_count")
            )
            or type(sync["exact_uid_bound"]) is not bool
            or sync["last_error"] is not None
            and type(sync["last_error"]) is not str
            or sync["credential_session_binding_id"] is not None
            and type(sync["credential_session_binding_id"]) is not str
        ):
            raise ValueError
        _legacy_datetime(sync["reconciled_at"])
        _legacy_datetime(sync["updated_at"])
        binding = sync["credential_session_binding_id"]
        if type(binding) is not str or _HEX.fullmatch(binding) is None:
            raise ValueError
        if (
            not sync["exact_uid_bound"]
            and "legacy_reconciliation_uid_unbound" not in reasons
        ):
            raise ValueError
        incomplete = (
            sync["status"] != "reconciled"
            or sync["reconciled_at"] is None
            or sync["last_error"] not in (None, "")
            or any(
                sync[key] != 0
                for key in ("order_count", "position_count", "algo_order_count")
            )
        )
        if incomplete and "legacy_reconciliation_incomplete_or_exposed" not in reasons:
            raise ValueError


def _checked_checkpoint(value, scope):
    try:
        if type(value) is not PortfolioLocalCheckpoint:
            raise ValueError
        raw = value.document_json
        record = decode(raw, _MAX_CHECKPOINT)
        if (
            type(record) is not dict
            or canonical(record) != raw
            or set(record)
            != {
                "schema_version",
                "environment",
                "account_id",
                "requested_settlement_currency",
                "currency_states",
                "unresolved_intents",
                "legacy",
                "blocking_reasons",
                "future_writer_exclusion",
                "execution_authority",
            }
            or record["schema_version"] != "ctcc.portfolio_local_checkpoint.v2"
            or (
                record["environment"],
                record["account_id"],
                record["requested_settlement_currency"],
            )
            != (scope.environment, scope.account_id, scope.settlement_currency)
            or record["future_writer_exclusion"] is not False
            or record["execution_authority"] is not False
            or type(record["currency_states"]) is not list
            or type(record["unresolved_intents"]) is not list
            or type(record["legacy"]) is not dict
            or type(record["blocking_reasons"]) is not list
            or len(record["blocking_reasons"]) > 32
            or any(
                type(reason) is not str or _REASON.fullmatch(reason) is None
                for reason in record["blocking_reasons"]
            )
            or record["blocking_reasons"] != sorted(set(record["blocking_reasons"]))
        ):
            raise ValueError
        _checked_local_structure(record, scope)
        observed, received = _utc(value.observed_at), _utc(value.received_at)
        if observed > received:
            raise ValueError
        return value.state_sha256, observed, received, record["blocking_reasons"]
    except Exception:  # noqa: BLE001 -- raw local state never appears in errors
        raise PostG12LocalPortfolioObservationError(
            "v5_local_checkpoint_invalid"
        ) from None


async def publish_capture_inspect_local_portfolio_v5(
    evidence_root,
    public_root,
    account_root,
    recheck_root,
    join_root,
    event_root,
    local_root,
    original_market,
    *,
    run,
    original_inputs,
    market_policy,
    account_session,
    session_factory,
    history_capture_id,
    ledger: QualificationLedgerRepository,
    scope: LedgerScope,
    g12_custody_repository=None,
) -> PostG12LocalPortfolioObservationV5:
    """Own V4 and two later local DB reads; never reserve or dispatch."""
    if type(ledger) is not QualificationLedgerRepository:
        raise PostG12LocalPortfolioObservationError("v5_exact_ledger_required")
    if session_factory is not ledger.session_factory:
        raise PostG12LocalPortfolioObservationError("v5_account_database_mismatch")
    scope = checked_bootstrap(scope, LedgerScope)
    # Freeze the original candidate and policy before V4's first async DB step.
    market, pre, inputs = prior.joined._original(original_market, run, original_inputs)
    original_binding = prior._checked_original_binding(
        pre, inputs, account_session, scope, history_capture_id
    )
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    try:
        for root in (
            evidence_root,
            public_root,
            account_root,
            recheck_root,
            join_root,
            event_root,
        ):
            prior.joined.original_source._roots(root, local_root)
        local_identity = prior.joined.prior._empty_native_receipt_root_identity(
            local_root, "v5_local_root_unavailable"
        )
        event_identity = source_runtime._native_recheck_root_identity(event_root)
        public_identity = source_runtime._native_recheck_root_identity(public_root)
    except Exception:  # noqa: BLE001 -- no filesystem path in refusal
        raise PostG12LocalPortfolioObservationError(
            "v5_local_root_unavailable"
        ) from None
    v4 = await prior.publish_capture_inspect_history_event_v4(
        evidence_root,
        public_root,
        account_root,
        recheck_root,
        join_root,
        event_root,
        market,
        run=pre,
        original_inputs=inputs,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        history_capture_id=history_capture_id,
        ledger=ledger,
        scope=scope,
        g12_custody_repository=g12_custody_repository,
    )
    if task is not asyncio.current_task() or task.cancelling():
        raise asyncio.CancelledError
    if type(v4) is not prior.PostG12AccountEventObservationV4:
        raise PostG12LocalPortfolioObservationError("v5_v4_result_invalid")
    reread = prior.read_post_g12_account_event_observation_v4(
        event_root,
        expected_sha256=v4.receipt_sha256,
        expected_root_identity=event_identity,
    )
    if reread.receipt_json != v4.receipt_json:
        raise PostG12LocalPortfolioObservationError("v5_v4_result_invalid")
    if (
        v4.scope_sha256 != original_binding["account_scope_sha256"]
        or v4.account_uid_sha256 != sha(scope.account_id.encode("ascii"))
        or v4.candidate_sha256 != original_binding["candidate_sha256"]
        or v4.original_event_key != original_binding["original_event_key"]
        or v4.report_id != original_binding["report_id"]
        or v4.account_plan_sha256 != original_binding["account_plan_sha256"]
        or v4.candidate_deadline_at != original_binding["deadline"].isoformat()
    ):
        raise PostG12LocalPortfolioObservationError("v5_v4_scope_mismatch")
    record = {
        "schema_version": _SCHEMA,
        "code": "v4_incomplete",
        "v4_code": v4.code,
        "v4_receipt_sha256": v4.receipt_sha256,
        "scope_sha256": v4.scope_sha256,
        "account_uid_sha256": v4.account_uid_sha256,
        "candidate_sha256": v4.candidate_sha256,
        "original_event_key": v4.original_event_key,
        "candidate_deadline_at": v4.candidate_deadline_at,
        "v4_event_read_received_at": None,
        "first_checkpoint_sha256": None,
        "second_checkpoint_sha256": None,
        "first_observed_at": None,
        "first_received_at": None,
        "second_observed_at": None,
        "second_received_at": None,
        "local_blocking_reasons": [],
        "local_flat_at_reads": False,
        "admission": "DENY",
        **{name: False for name in _FALSE_FIELDS},
    }
    if v4.code in _ADVANCED_V4:
        observed_at = _v4_event_read_received(
            event_root, v4, expected_root_identity=event_identity
        )
        try:
            first = await ledger.read_portfolio_checkpoint(scope)
            if task is not asyncio.current_task() or task.cancelling():
                raise asyncio.CancelledError
            second = await ledger.read_portfolio_checkpoint(scope)
            if task is not asyncio.current_task() or task.cancelling():
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- never serialize SQL or local state
            raise PostG12LocalPortfolioObservationError(
                "v5_local_checkpoint_unavailable"
            ) from None
        first_sha, first_at, first_end, first_reasons = _checked_checkpoint(
            first, scope
        )
        second_sha, second_at, second_end, second_reasons = _checked_checkpoint(
            second, scope
        )
        if first_sha != second_sha or first_reasons != second_reasons:
            raise PostG12LocalPortfolioObservationError("v5_local_state_changed")
        deadline = _utc_text(v4.candidate_deadline_at)
        if not (
            observed_at < first_at <= first_end < second_at <= second_end < deadline
        ):
            raise PostG12LocalPortfolioObservationError(
                "v5_local_checkpoint_clock_invalid"
            )
        try:
            packet = prior._read_bound_public_journal(
                public_root,
                {
                    "public_packet_sha256": v4.public_packet_sha256,
                    "public_journal_sha256": v4.public_journal_sha256,
                },
                root_identity=public_identity,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- changed source cannot advance
            public_failure_code = "public_journal_readback_invalid_at_local_readback"
        else:
            try:
                validation_at = utc_from_ns(native_stamp()["utc_ns"])
                if not second_end <= validation_at < deadline:
                    raise ValueError("v5_public_validation_clock_invalid")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- unusable validation time stays DENY
                public_failure_code = (
                    "public_validation_clock_invalid_at_local_readback"
                )
            else:
                try:
                    prior.joined.prior._require_final_original_g1(
                        packet, pre, validation_at
                    )
                    public_failure_code = None
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 -- original G1 rejection stays DENY
                    public_failure_code = "public_g1_rejected_at_local_readback"
        record.update(
            code=(
                public_failure_code
                if public_failure_code is not None
                else "original_event_already_recorded"
                if v4.code == "original_event_already_recorded"
                else "local_state_stable_not_authority"
                if not first_reasons
                else "local_state_blocked"
            ),
            v4_event_read_received_at=observed_at.isoformat(),
            first_checkpoint_sha256=first_sha,
            second_checkpoint_sha256=second_sha,
            first_observed_at=first_at.isoformat(),
            first_received_at=first_end.isoformat(),
            second_observed_at=second_at.isoformat(),
            second_received_at=second_end.isoformat(),
            local_blocking_reasons=first_reasons,
            local_flat_at_reads=not first_reasons,
        )
    result = PostG12LocalPortfolioObservationV5(canonical(record))
    _publish_receipt(local_root, result, expected_root_identity=local_identity)
    return result
