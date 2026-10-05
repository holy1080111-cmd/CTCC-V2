"""Explicit sweep V6 computation contracts; no runtime or order authority."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_core import TzInfo

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification.event_models import Digest, TriggerDetection
from app.trade_qualification.models import (
    EntryTrigger,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.timing import event_identity

SWEEP_PERMISSION_POLICY = "ctcc-sweep-history-protection-v1"
SWEEP_PERMISSION_SHA256 = (
    "abb0b422c2e5e0fa4c61596ebf9fb43aca57c2f13176f7ecc29770c4888f90ab"
)
SWEEP_SELECTION_POLICY = "ctcc-sweep-original-extreme-selection-v1"
SWEEP_SELECTION_BYTES = journal.canonical(
    {
        "policy_id": SWEEP_SELECTION_POLICY,
        "strategy": "liquidity_sweep_reversal",
        "history_permission_policy_sha256": SWEEP_PERMISSION_SHA256,
        "selectable_stop_source": "sweep_extreme_invalidation",
        "stop_anchor": "exact_original_event_extreme",
        "non_event_stops": "retained_with_rejection_code",
        "equal_pivot_pools": "unchanged_full_source_universe",
        "buffer": "unchanged_max_minimum_bps_or_ATR_plus_spread_slippage_tick",
        "tick_alignment": "unchanged_outward_stop_inward_source_zone",
        "target": "unchanged_nearest_opposing_source_barrier",
        "net_rr": "unchanged_costs_and_preregistered_minimum",
        "recheck": "fixed_original_entry_SL_TP_event_zone_policy",
        "old_version_upgrade": False,
        "execution_authority": False,
    }
)
SWEEP_SELECTION_SHA256 = journal.digest(SWEEP_SELECTION_BYTES)
_MAX_BYTES = 4 * 1024 * 1024
_AUTHORITY = (
    "execution_authority",
    "source_authenticity_verified",
    "complete_path_verified",
    "qualification_performed",
    "predictive_point_in_time_verified",
    "original_source_verified",
    "atomic_risk_reserved",
    "runtime_admissible",
)
_RECEIPT_FIELDS = frozenset(
    (
        "schema_version",
        "policy_id",
        "policy_sha256",
        "report_id",
        "strategy",
        "direction",
        "observed_at",
        "analysis_version",
        "instrument_id",
        "source_sha256",
        "source_received_at",
        "route",
        "detection",
        "event_key",
        "range_context",
        "range_context_sha256",
        "chronology",
        "conditions",
        "mathematical_confirmation",
        "retained_analysis_blockers",
        "code",
        "admitted",
        "permission_scope",
        *_AUTHORITY,
    )
)


def _utc(value):
    if type(value) is not datetime or type(value.tzinfo) not in (
        timezone,
        ZoneInfo,
        TzInfo,
    ):
        raise ValueError("exact datetime with a supported timezone is required")
    return require_aware(value)


def _guard(value, *, depth=0, budget=None):
    """Leaf event/scalar subset of regime admission's non-source guard.

    Preserve its exact bounds and pre-serializer checks for the only nested
    records used here. Importing the workflow evaluator from this contract
    would create a structural-protection/StrategyContext initialization cycle.
    """
    if budget is None:
        budget = [2000]
    budget[0] -= 1
    if depth > 12 or budget[0] < 0:
        raise ValueError("regime evidence tree exceeds bounds")
    kind = type(value)
    if any(kind is cls for cls in (TriggerDetection, EntryTrigger)):
        fields = object.__getattribute__(value, "__dict__")
        extras = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        expected = set(kind.model_fields)
        if (
            type(fields) is not dict
            or any(type(key) is not str for key in fields)
            or set(fields) != expected
            or extras is not None
            or private is not None
            or type(fields_set) is not set
            or any(type(key) is not str for key in fields_set)
            or not fields_set <= expected
        ):
            raise ValueError("hidden or missing regime evidence fields")
        if (
            kind is TriggerDetection
            and value.trigger is not None
            and type(value.trigger) is not EntryTrigger
        ):
            raise ValueError("exact trigger required")
        for item in fields.values():
            _guard(item, depth=depth + 1, budget=budget)
    elif kind is tuple:
        if len(value) > 32:
            raise ValueError("regime evidence sequence exceeds bounds")
        for item in value:
            _guard(item, depth=depth + 1, budget=budget)
    elif kind is datetime:
        _utc(value)
    elif kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 80
            or abs(value.as_tuple().exponent) > 40
        ):
            raise ValueError("regime evidence decimal exceeds bounds")
    elif kind is str:
        if len(value) > 512 or value != value.strip():
            raise ValueError("regime evidence text is noncanonical")
    elif value is None or kind is bool or (kind is int and abs(value) <= 10**40):
        pass
    else:
        raise ValueError("undeclared regime evidence type")


class SweepHistoryAdmissionRecord(QualificationModel):
    """A consistency record; only raw-source replay can verify its calculation."""

    model_config = ConfigDict(str_strip_whitespace=False)
    contract_version: Literal["ctcc-sweep-history-admission-v1"]
    policy_id: Literal["ctcc-sweep-history-protection-v1"]
    policy_sha256: Digest
    report_id: ReportId
    instrument_id: Text
    strategy: Literal["liquidity_sweep_reversal"]
    direction: Literal["long", "short"]
    observed_at: datetime
    analysis_version: str = Field(min_length=1, max_length=64)
    source_sha256: Digest | None
    detection: TriggerDetection | None
    event_key: Digest | None
    admitted: bool
    code: Text
    receipt_json: str = Field(min_length=1, max_length=_MAX_BYTES)
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    _clock = field_validator("observed_at")(_utc)

    @field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("sweep_admission_not_authority")
        return value

    @model_validator(mode="after")
    def receipt_bound(self):
        if self.policy_sha256 != SWEEP_PERMISSION_SHA256:
            raise ValueError("sweep_permission_policy_mismatch")
        raw = self.receipt_json.encode("utf-8")
        if len(raw) > _MAX_BYTES:
            raise ValueError("sweep_receipt_size_exceeded")
        value = json.loads(raw)
        if (
            type(value) is not dict
            or set(value) != _RECEIPT_FIELDS
            or journal.canonical(value) != raw
        ):
            raise ValueError("sweep_receipt_noncanonical")
        if (
            value.get("schema_version") != "ctcc.sweep_history_permission.v1"
            or value.get("permission_scope") != "history_evidence_only"
            or any(value.get(name) is not False for name in _AUTHORITY)
            or self.admitted != (self.code == "passed")
            or value.get("admitted") is not self.admitted
            or value.get("observed_at") != self.observed_at.isoformat()
        ):
            raise ValueError("sweep_receipt_contract_mismatch")
        for name in (
            "policy_id",
            "policy_sha256",
            "report_id",
            "strategy",
            "direction",
            "analysis_version",
            "source_sha256",
            "event_key",
            "code",
        ):
            if value.get(name) != getattr(self, name):
                raise ValueError("sweep_receipt_identity_mismatch")
        if (value.get("instrument_id") or "unknown") != self.instrument_id:
            raise ValueError("sweep_receipt_instrument_mismatch")
        detection = (
            TriggerDetection.model_validate_json(
                json.dumps(value["detection"]), strict=True
            )
            if value.get("detection") is not None
            else None
        )
        if self.detection is not None:
            _guard(self.detection)
        if detection != self.detection or self.event_key != (
            event_identity(detection) if detection is not None else None
        ):
            raise ValueError("sweep_receipt_detection_mismatch")
        if self.admitted and (
            detection is None
            or detection.fail_codes
            or detection.source_sha256 != self.source_sha256
            or self.source_sha256 is None
        ):
            raise ValueError("sweep_admission_source_missing")
        context = value["range_context"]
        if value["range_context_sha256"] != (
            journal.digest(journal.canonical(context)) if context is not None else None
        ):
            raise ValueError("sweep_receipt_context_digest_mismatch")
        return self

    @property
    def evaluation_sha256(self):
        raw = object.__getattribute__(self, "receipt_json")
        if type(raw) is not str or not 0 < len(raw) <= _MAX_BYTES:
            raise ValueError("sweep_receipt_bytes_invalid")
        return journal.digest(raw.encode("utf-8"))


def derive_sweep_admission(
    market, *, report_id, direction, observed_at, analysis_version
):
    from app.trade_qualification.sweep_history_permission import (
        evaluate_sweep_history_permission,
    )

    result = evaluate_sweep_history_permission(
        market,
        report_id=report_id,
        direction=direction,
        observed_at=observed_at,
        analysis_version=analysis_version,
        expected_policy_sha256=SWEEP_PERMISSION_SHA256,
    )
    value = json.loads(result.receipt_json)
    return SweepHistoryAdmissionRecord.model_validate_json(
        json.dumps(
            {
                "contract_version": "ctcc-sweep-history-admission-v1",
                **{
                    name: value[name]
                    for name in (
                        "policy_id",
                        "policy_sha256",
                        "report_id",
                        "strategy",
                        "direction",
                        "observed_at",
                        "analysis_version",
                        "source_sha256",
                        "detection",
                        "event_key",
                        "admitted",
                        "code",
                    )
                },
                "instrument_id": value["instrument_id"] or "unknown",
                "receipt_json": result.receipt_json.decode("utf-8"),
            }
        ),
        strict=True,
    )


def verify_sweep_admission(record, market, **original_inputs):
    if type(record) is not SweepHistoryAdmissionRecord:
        raise ValueError("exact_sweep_admission_required")
    raw = object.__getattribute__(record, "__dict__")
    if (
        type(raw) is not dict
        or any(type(key) is not str for key in raw)
        or set(raw) != set(SweepHistoryAdmissionRecord.model_fields)
        or object.__getattribute__(record, "__pydantic_extra__") is not None
        or object.__getattribute__(record, "__pydantic_private__") is not None
        or type(object.__getattribute__(record, "__pydantic_fields_set__")) is not set
        or any(type(key) is not str for key in record.__pydantic_fields_set__)
        or not record.__pydantic_fields_set__ <= set(raw)
    ):
        raise ValueError("sweep_admission_hidden_fields")
    for name, item in raw.items():
        if name == "receipt_json":
            if type(item) is not str or not 0 < len(item) <= _MAX_BYTES:
                raise ValueError("sweep_receipt_bytes_invalid")
        else:
            _guard(item)
    replayed = derive_sweep_admission(market, **original_inputs)
    if raw != object.__getattribute__(replayed, "__dict__"):
        raise ValueError("sweep_admission_source_replay_mismatch")
    return replayed
