"""One-invocation V3-to-DB0017 event observation; never an order issuer.

The V3 source join remains unqualified. This bridge only demonstrates that its
fixed original event was inspected in the durable UID-scoped ledger *after* the
new G12/public/account diagnostic completed. An absent row is not a permit.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import PosixPath, WindowsPath
from typing import Literal

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture
from app.trade_qualification import post_g12_account_history_join_v3 as joined
from app.trade_qualification import public_source_runtime as source_runtime
from app.trade_qualification.account_capture_journal import digest
from app.trade_qualification.event_observation import (
    MAX_OBSERVATION_BYTES,
    LedgerEventObservation,
)
from app.trade_qualification.reservations import (
    LedgerScope,
    checked_bootstrap,
    reservation_id,
)
from app.trade_qualification.timing import event_identity


class PostG12AccountEventObservationError(ValueError):
    """A stable denial code without source, SQL, account or path details."""


_CODES = frozenset(
    {
        "v3_incomplete",
        "v3_binding_invalid",
        "v3_stale",
        "ledger_observation_unavailable",
        "ledger_observation_invalid",
        "original_event_already_recorded",
        "trusted_execution_inputs_missing",
    }
)
_SOURCE_PINS = (
    "v3_receipt_sha256",
    "g12_evidence_sha256",
    "g12_report_sha256",
    "public_packet_sha256",
    "public_journal_sha256",
    "native_account_join_sha256",
    "history_reference_sha256",
    "current_reference_sha256",
    "public_only_recheck_sha256",
)
_STATES = frozenset({"reserved", "consumed", "uncertain", "reconciled_flat"})
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_SCHEMA = "ctcc.post_g12_account_event_observation.v4"
_MAX_RECEIPT = 4096


@dataclass(frozen=True, slots=True, repr=False)
class PostG12AccountEventObservationV4:
    code: Literal[
        "v3_incomplete",
        "v3_binding_invalid",
        "v3_stale",
        "ledger_observation_unavailable",
        "ledger_observation_invalid",
        "original_event_already_recorded",
        "trusted_execution_inputs_missing",
    ]
    report_id: str
    scope_sha256: str
    account_uid_sha256: str
    candidate_sha256: str
    account_plan_sha256: str
    original_event_key: str
    reservation_id: str
    candidate_deadline_at: str
    v3_observed_at: str | None = None
    v3_receipt_sha256: str | None = None
    g12_evidence_sha256: str | None = None
    g12_report_sha256: str | None = None
    public_packet_sha256: str | None = None
    public_journal_sha256: str | None = None
    native_account_join_sha256: str | None = None
    history_reference_sha256: str | None = None
    current_reference_sha256: str | None = None
    public_only_recheck_sha256: str | None = None
    ledger_observation_sha256: str | None = None
    ledger_event_state: str | None = None
    admission: Literal["DENY"] = "DENY"
    original_source_verified: Literal[False] = False
    account_complete: Literal[False] = False
    execution_recheck_performed: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    durable_intent_created: Literal[False] = False
    execution_authority: Literal[False] = False
    order_submitted: Literal[False] = False

    def __post_init__(self):
        if (
            type(self.code) is not str
            or self.code not in _CODES
            or type(self.report_id) is not str
            or not 1 <= len(self.report_id) <= 128
            or any(
                type(getattr(self, name)) is not str
                or _HEX.fullmatch(getattr(self, name)) is None
                for name in (
                    "scope_sha256",
                    "account_uid_sha256",
                    "candidate_sha256",
                    "account_plan_sha256",
                    "original_event_key",
                    "reservation_id",
                )
            )
            or type(self.candidate_deadline_at) is not str
            or len(self.candidate_deadline_at) > 40
            or any(
                pin is not None
                and (type(pin) is not str or _HEX.fullmatch(pin) is None)
                for pin in (getattr(self, name) for name in _SOURCE_PINS)
            )
            or self.ledger_observation_sha256 is not None
            and (
                type(self.ledger_observation_sha256) is not str
                or _HEX.fullmatch(self.ledger_observation_sha256) is None
            )
            or self.admission != "DENY"
            or any(
                getattr(self, name) is not False
                for name in (
                    "original_source_verified",
                    "account_complete",
                    "execution_recheck_performed",
                    "atomic_risk_reserved",
                    "durable_intent_created",
                    "execution_authority",
                    "order_submitted",
                )
            )
        ):
            raise PostG12AccountEventObservationError("v4_cannot_grant_execution")
        source = tuple(getattr(self, name) for name in _SOURCE_PINS)
        if self.code in {"v3_binding_invalid", "v3_stale"}:
            complete = all(item is None for item in source)
        elif self.code == "v3_incomplete":
            complete = source[0] is not None and all(
                item is None for item in source[1:]
            )
        else:
            complete = all(item is not None for item in source)
        if not complete:
            raise PostG12AccountEventObservationError("v4_pin_state_invalid")
        if self.code == "trusted_execution_inputs_missing":
            valid_observation = (
                self.ledger_observation_sha256 is not None
                and self.ledger_event_state == "absent_at_read"
            )
        elif self.code == "original_event_already_recorded":
            valid_observation = (
                self.ledger_observation_sha256 is not None
                and type(self.ledger_event_state) is str
                and self.ledger_event_state in _STATES
            )
        else:
            valid_observation = (
                self.ledger_observation_sha256 is None
                and self.ledger_event_state is None
            )
        if not valid_observation:
            raise PostG12AccountEventObservationError("v4_observation_state_invalid")
        try:
            deadline = joined._utc(self.candidate_deadline_at)
            if deadline.isoformat() != self.candidate_deadline_at:
                raise ValueError
            if self.v3_observed_at is not None:
                observed = joined._utc(self.v3_observed_at)
                if (
                    type(self.v3_observed_at) is not str
                    or observed.isoformat() != self.v3_observed_at
                    or not observed < deadline
                ):
                    raise ValueError
            if (self.ledger_observation_sha256 is None) != (
                self.v3_observed_at is None
            ):
                raise ValueError
            if len(canonical(_receipt_document(self))) > _MAX_RECEIPT:
                raise ValueError
        except Exception:  # noqa: BLE001 -- bounded static rejection
            raise PostG12AccountEventObservationError("v4_receipt_invalid") from None

    @property
    def receipt_json(self) -> bytes:
        if type(self) is not PostG12AccountEventObservationV4:
            raise PostG12AccountEventObservationError("v4_exact_receipt_required")
        document = _receipt_document(self)
        PostG12AccountEventObservationV4(
            **{name: document[name] for name in _RECEIPT_VALUE_FIELDS}
        )
        return canonical(document)

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)


_RECEIPT_VALUE_FIELDS = tuple(PostG12AccountEventObservationV4.__dataclass_fields__)
_RECEIPT_FIELDS = frozenset(("schema_version", *_RECEIPT_VALUE_FIELDS))


def _receipt_document(value: PostG12AccountEventObservationV4) -> dict:
    return {
        "schema_version": _SCHEMA,
        **{name: getattr(value, name) for name in _RECEIPT_VALUE_FIELDS},
    }


def _receipt_from_bytes(raw: bytes) -> PostG12AccountEventObservationV4:
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= _MAX_RECEIPT:
            raise ValueError
        document = decode(raw, _MAX_RECEIPT)
        if (
            type(document) is not dict
            or set(document) != _RECEIPT_FIELDS
            or document["schema_version"] != _SCHEMA
            or canonical(document) != raw
        ):
            raise ValueError
        result = PostG12AccountEventObservationV4(
            **{name: document[name] for name in _RECEIPT_VALUE_FIELDS}
        )
        if result.receipt_json != raw:
            raise ValueError
        return result
    except Exception:  # noqa: BLE001 -- untrusted native bytes cannot escape
        raise PostG12AccountEventObservationError("v4_receipt_invalid") from None


def _observation_bytes_for_receipt(
    observation: LedgerEventObservation, receipt: PostG12AccountEventObservationV4
) -> bytes:
    """Strictly bind a private DB read to its public-safe hash receipt."""
    try:
        if (
            type(observation) is not LedgerEventObservation
            or type(receipt) is not PostG12AccountEventObservationV4
            or receipt.ledger_observation_sha256 is None
            or receipt.v3_observed_at is None
        ):
            raise ValueError
        raw = observation.canonical_json.encode("ascii")
        if not 0 < len(raw) <= MAX_OBSERVATION_BYTES:
            raise ValueError
        replayed = LedgerEventObservation.model_validate_json(raw, strict=True)
        if replayed != observation or replayed.canonical_json.encode("ascii") != raw:
            raise ValueError
        scope = replayed.scope
        state = "absent_at_read" if replayed.matched is None else replayed.matched.state
        if (
            sha(raw) != receipt.ledger_observation_sha256
            or scope.environment != "demo"
            or sha(scope.account_id.encode("ascii")) != receipt.account_uid_sha256
            or digest(
                canonical(
                    [scope.environment, scope.account_id, scope.settlement_currency]
                )
            )
            != receipt.scope_sha256
            or replayed.original_event_key != receipt.original_event_key
            or reservation_id(scope, replayed.original_event_key)
            != receipt.reservation_id
            or state != receipt.ledger_event_state
            or not joined._utc(receipt.v3_observed_at)
            < replayed.request_started_at
            <= replayed.observed_at
            <= replayed.received_at
            < joined._utc(receipt.candidate_deadline_at)
        ):
            raise ValueError
        return raw
    except Exception:  # noqa: BLE001 -- never disclose account or SQL details
        raise PostG12AccountEventObservationError("v4_observation_invalid") from None


def read_post_g12_account_event_observation_v4(
    root, *, expected_sha256, expected_root_identity
) -> PostG12AccountEventObservationV4:
    """Read the separate no-clobber V4 receipt; never grant execution."""
    try:
        if (
            type(root) not in (PosixPath, WindowsPath)
            or not root.is_absolute()
            or type(expected_sha256) is not str
            or _HEX.fullmatch(expected_sha256) is None
        ):
            raise ValueError
        with source_runtime._native_recheck_root(root) as directory:
            if (
                joined.prior._native_directory_identity(directory)
                != expected_root_identity
            ):
                raise ValueError
            raw = directory.read("receipt.json", _MAX_RECEIPT)
            if sha(raw) != expected_sha256:
                raise ValueError
            result = _receipt_from_bytes(raw)
            has_observation = result.ledger_observation_sha256 is not None
            expected_names = (
                {"receipt.json", "observation.json"}
                if has_observation
                else {"receipt.json"}
            )
            if set(directory.names()) != expected_names:
                raise ValueError
            if has_observation:
                observation_raw = directory.read(
                    "observation.json", MAX_OBSERVATION_BYTES
                )
                observation = LedgerEventObservation.model_validate_json(
                    observation_raw, strict=True
                )
                if (
                    observation.canonical_json.encode("ascii") != observation_raw
                    or _observation_bytes_for_receipt(observation, result)
                    != observation_raw
                ):
                    raise ValueError
        if source_runtime._native_recheck_root_identity(root) != expected_root_identity:
            raise ValueError
        return result
    except Exception:  # noqa: BLE001 -- no native path or private details
        raise PostG12AccountEventObservationError("v4_readback_failed") from None


def _publish_receipt(root, receipt, *, expected_root_identity, observation=None):
    if type(receipt) is not PostG12AccountEventObservationV4:
        raise PostG12AccountEventObservationError("v4_exact_receipt_required")
    raw = receipt.receipt_json
    try:
        if receipt.ledger_observation_sha256 is None:
            if observation is not None:
                raise ValueError
            observation_raw = None
        else:
            observation_raw = _observation_bytes_for_receipt(observation, receipt)
        with source_runtime._native_recheck_root(root) as directory:
            if (
                joined.prior._native_directory_identity(directory)
                != expected_root_identity
                or directory.names()
            ):
                raise ValueError
            if observation_raw is not None:
                directory.publish("observation.json", observation_raw)
                if (
                    directory.read("observation.json", MAX_OBSERVATION_BYTES)
                    != observation_raw
                ):
                    raise ValueError
            directory.publish("receipt.json", raw)
            if directory.read("receipt.json", _MAX_RECEIPT) != raw:
                raise ValueError
        replayed = read_post_g12_account_event_observation_v4(
            root,
            expected_sha256=sha(raw),
            expected_root_identity=expected_root_identity,
        )
        if replayed.receipt_json != raw:
            raise ValueError
    except Exception:  # noqa: BLE001 -- retain already published journal bytes
        raise PostG12AccountEventObservationError("v4_publish_failed") from None


def _original_binding(
    original_market, run, original_inputs, account_session, scope, history_capture_id
):
    """Copy the same original pins V3 derives before any asynchronous IO."""
    scope = checked_bootstrap(scope, LedgerScope)
    if type(account_session) is not joined.ControlledDemoAccountSession:
        raise PostG12AccountEventObservationError("v4_exact_account_session_required")
    plan = account_capture._checked_plan(account_session._plan, account_session._pin)
    if (
        type(plan) is not account_capture.CurrentDemoAccountCapturePlanV7
        or scope.environment != "demo"
        or scope.account_id != plan.expected_uid
        or scope.settlement_currency != plan.settlement_currency
        or type(history_capture_id) is not str
        or joined._CAPTURE_ID.fullmatch(history_capture_id) is None
    ):
        raise PostG12AccountEventObservationError("v4_account_scope_mismatch")
    _, pre, inputs = joined._original(original_market, run, original_inputs)
    intent, risk_inputs, candidate = inputs["intent"], inputs["risk_inputs"], pre.result
    key = event_identity(pre.prefix.detection)
    if (
        not pre.pre_evidence_complete
        or key is None
        or candidate.candidate_entry != intent.candidate_entry
        or candidate.stop_loss is None
        or candidate.take_profit is None
        or risk_inputs.account is None
        or risk_inputs.account.account_id != plan.expected_uid
        or risk_inputs.account.settlement_currency != plan.settlement_currency
        or risk_inputs.authority is None
        or risk_inputs.authority.stamp.account_id != plan.expected_uid
        or risk_inputs.instrument is None
        or risk_inputs.instrument.instrument_id != intent.instrument_id
    ):
        raise PostG12AccountEventObservationError("v4_original_incomplete")
    candidate_sha256 = sha(
        canonical(
            {
                "pre_evidence_sha256": pre.evaluation_sha256,
                "event_key": key,
                "entry": str(candidate.candidate_entry),
                "stop_loss": str(candidate.stop_loss),
                "take_profit": str(candidate.take_profit),
            }
        )
    )
    return {
        "report_id": intent.report_id,
        "instrument_id": intent.instrument_id,
        "direction": intent.direction,
        "original_entry": str(candidate.candidate_entry),
        "original_stop_loss": str(candidate.stop_loss),
        "original_take_profit": str(candidate.take_profit),
        "candidate_sha256": candidate_sha256,
        "original_event_key": key,
        "account_plan_sha256": account_session._pin,
        "account_scope_sha256": digest(
            canonical([scope.environment, scope.account_id, scope.settlement_currency])
        ),
        "history_capture_id_sha256": sha(history_capture_id.encode("ascii")),
        "deadline": min(
            pre.prefix.intent.expires_at,
            pre.prefix.detection.trigger.expires_at,
            pre.result.entry_zone.expires_at,
            pre.prefix.timing.latest_valid_entry_time,
        ),
    }


def _bound_v3(value, expected):
    """Inspect the exact read-back receipt, never a caller's `passed` flag."""
    if type(value) is not dict or any(
        value.get(name) != pin for name, pin in expected.items() if name != "deadline"
    ):
        raise PostG12AccountEventObservationError("v4_receipt_original_changed")
    if value.get("code") != "joined_unqualified":
        return False
    if (
        value.get("public_only_recheck_code")
        != "projected_math_consistent_account_path_required"
    ):
        return False
    if any(
        value.get(name) is None
        for name in (
            "g12_evidence_sha256",
            "g12_report_sha256",
            "public_packet_sha256",
            "public_journal_sha256",
            "native_account_join_sha256",
            "history_reference_sha256",
            "current_reference_sha256",
            "public_only_recheck_sha256",
        )
    ) or (
        value.get("committed_history_readback_before_first_http") is not True
        or value.get("public_only_recheck_receipt_persisted") is not True
        or value.get("admission") != "DENY"
        or any(value.get(name) is not False for name in joined._FALSE_FIELDS)
    ):
        raise PostG12AccountEventObservationError("v4_receipt_source_changed")
    observed = joined._utc(value["observed_at"])
    barrier = joined._utc(value["publication_completed_at"])
    if not barrier < observed < expected["deadline"]:
        raise PostG12AccountEventObservationError("v4_receipt_expired")
    return True


async def publish_capture_inspect_history_event_v4(
    evidence_root,
    public_root,
    account_root,
    recheck_root,
    join_root,
    event_root,
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
) -> PostG12AccountEventObservationV4:
    """Create this call's V3, read it back, then inspect its exact UID/event.

    There is no reserve, reconcile, intent, Arm, transport or execution path.
    Every result remains DENY, including a read that observes no prior event.
    """
    if type(ledger) is not QualificationLedgerRepository:
        raise PostG12AccountEventObservationError("v4_exact_ledger_required")
    scope = checked_bootstrap(scope, LedgerScope)
    expected = _original_binding(
        original_market,
        run,
        original_inputs,
        account_session,
        scope,
        history_capture_id,
    )
    base = {
        "report_id": expected["report_id"],
        "scope_sha256": expected["account_scope_sha256"],
        "account_uid_sha256": sha(scope.account_id.encode("ascii")),
        "candidate_sha256": expected["candidate_sha256"],
        "account_plan_sha256": expected["account_plan_sha256"],
        "original_event_key": expected["original_event_key"],
        "reservation_id": reservation_id(scope, expected["original_event_key"]),
        "candidate_deadline_at": expected["deadline"].isoformat(),
    }
    try:
        for root in (evidence_root, public_root, account_root, recheck_root, join_root):
            joined.original_source._roots(root, event_root)
        event_root_identity = joined.prior._empty_native_receipt_root_identity(
            event_root, "v4_event_root_unavailable"
        )
    except Exception:  # noqa: BLE001 -- no path or source detail in denial
        raise PostG12AccountEventObservationError("v4_event_root_unavailable") from None
    root_identity = joined.prior._empty_native_receipt_root_identity(
        join_root, "v4_join_root_unavailable"
    )
    receipt = await joined.publish_capture_public_account_history_v3(
        evidence_root,
        public_root,
        account_root,
        recheck_root,
        join_root,
        original_market,
        run=run,
        original_inputs=original_inputs,
        market_policy=market_policy,
        account_session=account_session,
        session_factory=session_factory,
        history_capture_id=history_capture_id,
    )
    result = None
    retained_observation = None
    if (
        type(receipt) is not joined.PostG12OwnedPublicAccountHistoryDiagnosticV3
        or account_session._used is not True
    ):
        result = PostG12AccountEventObservationV4(code="v3_binding_invalid", **base)
    else:
        try:
            replayed = joined.read_post_g12_account_history_receipt_v3(
                join_root,
                expected_sha256=receipt.receipt_sha256,
                expected_root_identity=root_identity,
            )
            if replayed.receipt_json != receipt.receipt_json:
                raise PostG12AccountEventObservationError("v4_readback_changed")
            value = decode(replayed.receipt_json, joined._MAX_RECEIPT)
            if not _bound_v3(value, expected):
                result = PostG12AccountEventObservationV4(
                    code="v3_incomplete",
                    v3_receipt_sha256=receipt.receipt_sha256,
                    **base,
                )
        except asyncio.CancelledError:
            raise
        except PostG12AccountEventObservationError as exc:
            result = PostG12AccountEventObservationV4(
                code=(
                    "v3_stale"
                    if str(exc) == "v4_receipt_expired"
                    else "v3_binding_invalid"
                ),
                **base,
            )
        except Exception:  # noqa: BLE001 -- no raw source/path in receipt
            result = PostG12AccountEventObservationV4(code="v3_binding_invalid", **base)
    if result is None:
        pins = {
            "v3_receipt_sha256": receipt.receipt_sha256,
            **{
                name: value[name]
                for name in (
                    "g12_evidence_sha256",
                    "g12_report_sha256",
                    "public_packet_sha256",
                    "public_journal_sha256",
                    "native_account_join_sha256",
                    "history_reference_sha256",
                    "current_reference_sha256",
                    "public_only_recheck_sha256",
                )
            },
        }
        try:
            observation = await ledger.read_event_observation(
                scope, expected["original_event_key"]
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- never expose private SQL details
            result = PostG12AccountEventObservationV4(
                code="ledger_observation_unavailable", **base, **pins
            )
    if result is None:
        try:
            if type(observation) is not LedgerEventObservation:
                raise ValueError
            replayed_observation = LedgerEventObservation.model_validate_json(
                observation.canonical_json, strict=True
            )
            v3_observed = joined._utc(value["observed_at"])
            if (
                replayed_observation != observation
                or observation.scope != scope
                or observation.original_event_key != expected["original_event_key"]
                or not v3_observed
                < observation.request_started_at
                <= observation.observed_at
                <= observation.received_at
                < expected["deadline"]
            ):
                raise ValueError
            state = (
                "absent_at_read"
                if observation.matched is None
                else observation.matched.state
            )
            result = PostG12AccountEventObservationV4(
                code=(
                    "trusted_execution_inputs_missing"
                    if observation.matched is None
                    else "original_event_already_recorded"
                ),
                ledger_observation_sha256=observation.sha256,
                ledger_event_state=state,
                v3_observed_at=value["observed_at"],
                **base,
                **pins,
            )
            _observation_bytes_for_receipt(replayed_observation, result)
            retained_observation = replayed_observation
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- mismatch cannot grant absence
            result = PostG12AccountEventObservationV4(
                code="ledger_observation_invalid", **base, **pins
            )
    # A late publication/readback failure propagates. Never delete an accepted
    # V3 receipt, erase this root, or reclassify a failed V4 append as success.
    _publish_receipt(
        event_root,
        result,
        expected_root_identity=event_root_identity,
        observation=retained_observation,
    )
    return result
