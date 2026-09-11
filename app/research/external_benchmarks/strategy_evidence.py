"""Offline, declaration-only strategy metadata; never source verification."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.research.external_benchmarks.contracts import (
    LicenseStatus,
    ReferenceContract,
    require_utc,
)

MAX_METADATA_BYTES = 256 * 1024
PACK_FILE_ROLES = (
    "strategy_profile.json",
    "logic.yaml",
    "performance.json",
    "source_manifest.json",
    "ctcc_comparison.json",
)
Identifier = Annotated[
    str, Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9._:-]*$")
]
ShortText = Annotated[str, Field(min_length=1, max_length=240)]
Description = Annotated[str, Field(min_length=1, max_length=2000)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MetadataContract(ReferenceContract):
    model_config = ConfigDict(strict=True)
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False
    promotion_eligible: Literal[False] = False
    reference_only: Literal[True] = True

    @field_validator(
        "runtime_consumers",
        "execution_authority",
        "promotion_eligible",
        "reference_only",
        "predictive_eligible",
        "source_authenticity_verified",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def exact_authority_literals(cls, value, info):
        expected = {"runtime_consumers": 0, "reference_only": True}.get(
            info.field_name, False
        )
        if type(value) is not type(expected) or value != expected:
            raise ValueError(
                f"{info.field_name} must retain its exact zero-authority literal"
            )
        return value


class StrategyFamily(StrEnum):
    TREND_MOMENTUM = "trend_momentum"
    STRUCTURE_SMC = "structure_smc"
    MEAN_REVERSION = "mean_reversion"
    STATISTICAL_ARBITRAGE = "statistical_arbitrage"
    FUNDING_BASIS = "funding_basis"
    MARKET_MAKING = "market_making"


class MaterialRole(StrEnum):
    AUTHOR_CLAIM = "author_claim"
    REPORT = "report"
    TRADES = "trades"
    CODE = "code"
    DATA = "data"
    OOS = "oos"
    FORWARD = "forward"
    PAPER = "paper"
    LIVE = "live"


class EvidenceAvailability(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"
    E6 = "E6"


class MetricKind(StrEnum):
    TRADE_WIN_RATE = "trade_win_rate"
    PROFITABLE_WINDOW_RATE = "profitable_window_rate"
    PROFIT_FACTOR = "profit_factor"
    NET_PNL = "net_pnl"
    NET_EXPECTANCY = "net_expectancy"
    MAX_DRAWDOWN = "max_drawdown"
    AVERAGE_WIN = "average_win"
    AVERAGE_LOSS = "average_loss"


class TestType(StrEnum):
    BACKTEST = "backtest"
    OOS = "oos"
    FORWARD = "forward"
    PAPER = "paper"
    LIVE = "live"


class SampleUnit(StrEnum):
    TRADE = "trade"
    WALK_FORWARD_WINDOW = "walk_forward_window"
    EQUITY_OBSERVATION = "equity_observation"


class CostBasis(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class StrategyProfile(MetadataContract):
    strategy_id: Identifier
    name: ShortText
    families: tuple[StrategyFamily, ...] = Field(default=(), max_length=6)
    classification_missing_reason: Description | None = None

    @model_validator(mode="after")
    def validate_classification(self):
        if len(set(self.families)) != len(self.families):
            raise ValueError("strategy families must be unique")
        if bool(self.families) == (self.classification_missing_reason is not None):
            raise ValueError("unclassified strategies require only a missing reason")
        return self


class PassiveLogic(MetadataContract):
    summary: Description | None = None
    missing_reason: Description | None = None

    @model_validator(mode="after")
    def validate_missingness(self):
        if (self.summary is None) != (self.missing_reason is not None):
            raise ValueError(
                "missing logic requires a reason; present logic cannot be missing"
            )
        return self


class SourceAssertion(MetadataContract):
    source_id: Identifier
    material_role: MaterialRole
    original_url: HttpUrl | None = Field(default=None, max_length=2048)
    author: ShortText | None = None
    retrieved_at: datetime | None = None
    artifact_sha256: Sha256 | None = None
    artifact_byte_size: int | None = Field(default=None, ge=1, le=2**63 - 1)
    license_status: LicenseStatus | None = None
    missing_reason: Description | None = None

    @field_validator("original_url")
    @classmethod
    def validate_url(cls, value):
        if value is not None and (
            value.scheme != "https" or value.username or value.password
        ):
            raise ValueError("original URL must be credential-free HTTPS")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def validate_time(cls, value):
        return require_utc(value, "retrieved_at") if value is not None else None

    @property
    def metadata_complete(self) -> bool:
        return all(
            value is not None
            for value in (
                self.original_url,
                self.author,
                self.retrieved_at,
                self.artifact_sha256,
                self.artifact_byte_size,
                self.license_status,
            )
        )

    @model_validator(mode="after")
    def validate_missingness(self):
        if self.metadata_complete == (self.missing_reason is not None):
            raise ValueError(
                "incomplete source assertions require only a missing reason"
            )
        return self


class MetricWindow(MetadataContract):
    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_time(cls, value, info):
        return require_utc(value, info.field_name)

    @model_validator(mode="after")
    def validate_order(self):
        if self.end_at <= self.start_at:
            raise ValueError("sample window end must follow its start")
        return self


class CostContext(MetadataContract):
    fees: CostBasis
    funding: CostBasis
    spread: CostBasis
    slippage: CostBasis
    assumptions: Description


class AuthorMetric(MetadataContract):
    metric_id: Identifier
    kind: MetricKind
    source_id: Identifier
    value: Decimal | None = Field(default=None, max_digits=40, decimal_places=20)
    missing_reason: Description | None = None
    definition: Description | None = None
    unit: ShortText | None = None
    test_type: TestType | None = None
    market: ShortText | None = None
    timeframe: ShortText | None = None
    sample_window: MetricWindow | None = None
    denominator: int | None = Field(default=None, ge=1, le=10**12)
    sample_unit: SampleUnit | None = None
    costs: CostContext | None = None
    attribution: Literal["external_author_claim"] = "external_author_claim"

    @model_validator(mode="after")
    def validate_claim(self):
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing metric values require only a missing reason")
        if self.value is not None:
            if not self.value.is_finite():
                raise ValueError("metric value must be finite")
            if any(
                value is None
                for value in (
                    self.definition,
                    self.unit,
                    self.test_type,
                    self.market,
                    self.timeframe,
                    self.sample_window,
                    self.denominator,
                    self.sample_unit,
                    self.costs,
                )
            ):
                raise ValueError(
                    "numeric author claims require complete metric context"
                )
        rate_units = {
            MetricKind.TRADE_WIN_RATE: SampleUnit.TRADE,
            MetricKind.PROFITABLE_WINDOW_RATE: SampleUnit.WALK_FORWARD_WINDOW,
        }
        if self.kind in rate_units:
            if (
                self.sample_unit is not None
                and self.sample_unit != rate_units[self.kind]
            ):
                raise ValueError(
                    "trade and profitable-window rates need distinct denominators"
                )
            if self.unit is not None and self.unit != "ratio":
                raise ValueError(
                    "rate claims use ratio units, not percentages or thresholds"
                )
            if self.value is not None and not Decimal(0) <= self.value <= Decimal(1):
                raise ValueError("rate claims must lie in [0, 1]")
        return self


class CtccValidation(MetadataContract):
    status: Literal["not_performed"] = "not_performed"


class StrategyEvidencePack(MetadataContract):
    schema_version: Literal["ctcc.strategy_evidence.metadata.v1"] = (
        "ctcc.strategy_evidence.metadata.v1"
    )
    pack_id: Identifier
    external_namespace: Literal["external_evidence"] = "external_evidence"
    recorded_at: datetime
    profile: StrategyProfile
    logic: PassiveLogic
    sources: tuple[SourceAssertion, ...] = Field(min_length=1, max_length=32)
    metrics: tuple[AuthorMetric, ...] = Field(default=(), max_length=64)
    availability: EvidenceAvailability = EvidenceAvailability.E0
    availability_semantics: Literal[
        "declared_material_availability_not_verification"
    ] = "declared_material_availability_not_verification"
    ctcc_validation: CtccValidation = Field(default_factory=CtccValidation)
    file_roles: tuple[str, ...] = Field(
        default=PACK_FILE_ROLES, min_length=5, max_length=5
    )
    predictive_eligible: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    @field_validator("recorded_at")
    @classmethod
    def validate_time(cls, value):
        return require_utc(value, "recorded_at")

    @model_validator(mode="after")
    def validate_links(self):
        if self.file_roles != PACK_FILE_ROLES:
            raise ValueError("file roles are fixed labels, not caller-supplied paths")
        sources = {source.source_id: source for source in self.sources}
        if len(sources) != len(self.sources) or len(
            {m.metric_id for m in self.metrics}
        ) != len(self.metrics):
            raise ValueError("source IDs and metric IDs must each be unique")
        for source in self.sources:
            if (
                source.retrieved_at is not None
                and source.retrieved_at > self.recorded_at
            ):
                raise ValueError("source retrieval cannot follow pack recording")
        for metric in self.metrics:
            if metric.source_id not in sources:
                raise ValueError("metric references an unknown source")
            source = sources[metric.source_id]
            if metric.value is not None and not source.metadata_complete:
                raise ValueError("numeric claims require a complete source assertion")
            if (
                metric.sample_window is not None
                and source.retrieved_at is not None
                and metric.sample_window.end_at > source.retrieved_at
            ):
                raise ValueError("metric sample window cannot follow source retrieval")
        roles = {s.material_role for s in self.sources if s.metadata_complete}
        requirements = {
            EvidenceAvailability.E1: {MaterialRole.REPORT},
            EvidenceAvailability.E2: {MaterialRole.TRADES},
            EvidenceAvailability.E3: {MaterialRole.CODE, MaterialRole.DATA},
            EvidenceAvailability.E4: {MaterialRole.OOS},
            EvidenceAvailability.E6: {MaterialRole.LIVE},
        }
        if not requirements.get(self.availability, set()).issubset(roles):
            raise ValueError(
                "availability tier lacks complete matching material assertions"
            )
        if self.availability == EvidenceAvailability.E5 and not roles.intersection(
            {MaterialRole.FORWARD, MaterialRole.PAPER}
        ):
            raise ValueError(
                "E5 requires a complete forward or paper material assertion"
            )
        return self


class StrategyEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenStrategyEvidence:
    payload: bytes
    sha256: str


def _reject_injected_fields(value) -> None:
    # model_copy(update=...) may put unknown keys in __dict__ which model_dump
    # silently omits. Inspect those keys before serialization can discard them.
    if isinstance(value, MetadataContract):
        if set(value.__dict__) - set(type(value).model_fields) or value.model_extra:
            raise StrategyEvidenceError("metadata contains injected model fields")
        for item in value.__dict__.values():
            _reject_injected_fields(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _reject_injected_fields(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_injected_fields(item)


def _canonical(pack: StrategyEvidencePack) -> bytes:
    if type(pack) is not StrategyEvidencePack:
        raise StrategyEvidenceError("an exact StrategyEvidencePack is required")
    _reject_injected_fields(pack)
    checked = StrategyEvidencePack.model_validate(
        pack.model_dump(mode="python", serialize_as_any=True)
    )
    payload = json.dumps(
        checked.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_METADATA_BYTES:
        raise StrategyEvidenceError("metadata exceeds 256 KiB")
    return payload


def freeze_strategy_evidence_pack(pack: StrategyEvidencePack) -> FrozenStrategyEvidence:
    """Hash validated declarations only; no underlying source is opened or verified."""
    try:
        payload = _canonical(pack)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StrategyEvidenceError("invalid strategy metadata") from exc
    return FrozenStrategyEvidence(payload, hashlib.sha256(payload).hexdigest())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StrategyEvidenceError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value):
    raise StrategyEvidenceError(f"non-finite JSON constant: {value}")


def verify_strategy_evidence_pack(
    payload: bytes, *, expected_sha256: str
) -> StrategyEvidencePack:
    """Check pinned canonical metadata, not underlying source authenticity."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_METADATA_BYTES:
        raise StrategyEvidenceError("payload must be bytes within 256 KiB")
    if (
        type(expected_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise StrategyEvidenceError("expected metadata SHA-256 must be supplied")
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise StrategyEvidenceError("metadata SHA-256 mismatch")
    try:
        json.loads(
            payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        pack = StrategyEvidencePack.model_validate_json(payload)
        if _canonical(pack) != payload:
            raise StrategyEvidenceError("metadata JSON is not canonical")
        return pack
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StrategyEvidenceError("invalid canonical strategy metadata") from exc
