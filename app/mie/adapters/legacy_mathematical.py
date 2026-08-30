from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from app.domain.analysis import MathematicalCoreSnapshot
from app.mie.contracts import (
    Evidence,
    EvidenceDirection,
    EvidenceUse,
    ForecastHorizon,
    ValidationLevel,
)


_VALIDATION_MAP = {
    "analytical": ValidationLevel.CAUSAL,
    "prequential": ValidationLevel.PREQUENTIAL,
    "auxiliary": ValidationLevel.AUXILIARY,
}


def _direction(signal: Decimal) -> EvidenceDirection:
    if signal > 0:
        return EvidenceDirection.LONG
    if signal < 0:
        return EvidenceDirection.SHORT
    return EvidenceDirection.NEUTRAL


def adapt_legacy_mathematical_core(
    core: MathematicalCoreSnapshot,
    *,
    instrument_id: str,
    horizon: ForecastHorizon,
    observed_at: datetime,
    data_cutoff: datetime,
    provenance_sha256: str,
    feature_version: str = "v1.6.8-mathematical-core",
    model_version: str = "v1.6.8",
) -> tuple[Evidence, ...]:
    """Map frozen Gate 8 components into correlated, downward-only evidence."""

    evidence: list[Evidence] = []
    for component in core.components:
        source = f"legacy.mathematical_core.{component.code}"
        validation_level = _VALIDATION_MAP[component.validation_level]
        permitted_use = (
            EvidenceUse.AUXILIARY_TIE_BREAK
            if validation_level == ValidationLevel.AUXILIARY
            else EvidenceUse.RISK_DOWNGRADE_ONLY
        )
        evidence_id = uuid5(
            NAMESPACE_URL,
            (
                f"ctcc-v2:mie:{provenance_sha256}:{instrument_id}:"
                f"{horizon.label}:{observed_at.isoformat()}:{source}"
            ),
        )
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                source=source,
                instrument_id=instrument_id,
                horizon=horizon,
                observed_at=observed_at,
                data_cutoff=data_cutoff,
                generated_at=observed_at,
                direction=_direction(component.signal),
                strength=abs(component.signal),
                reliability=component.reliability,
                uncertainty=Decimal("1") - component.reliability,
                data_quality=core.coverage,
                validation_level=validation_level,
                permitted_use=permitted_use,
                validation_sample_size=component.validation_sample_size,
                validation_metric=component.validation_metric,
                dependency_group="legacy.price_path.shared",
                feature_version=feature_version,
                model_version=model_version,
                provenance_sha256=provenance_sha256,
                detail_codes=(
                    component.detail,
                    f"legacy_validation:{component.validation_level}",
                ),
            )
        )
    return tuple(evidence)
