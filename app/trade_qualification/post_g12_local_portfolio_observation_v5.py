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
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import post_g12_account_event_observation_v4 as prior
from app.trade_qualification import public_source_runtime as source_runtime
from app.trade_qualification.event_observation import (
    MAX_OBSERVATION_BYTES,
    LedgerEventObservation,
)
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

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
                if record["local_flat_at_reads"] is not flat or record["code"] != (
                    "original_event_already_recorded"
                    if record["v4_code"] == "original_event_already_recorded"
                    else "local_state_stable_not_authority"
                    if flat
                    else "local_state_blocked"
                ):
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
            or value.known_flat is not (not record["blocking_reasons"])
        ):
            raise ValueError
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
        original_market,
        run=run,
        original_inputs=original_inputs,
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
    if v4.scope_sha256 != prior.digest(
        canonical([scope.environment, scope.account_id, scope.settlement_currency])
    ) or v4.account_uid_sha256 != sha(scope.account_id.encode("ascii")):
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
        record.update(
            code=(
                "original_event_already_recorded"
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
