"""Mathematical Intelligence Engine contracts.

Gate 1 is shadow-only and deliberately exposes no execution adapter.
"""

from app.mie.adapters import adapt_legacy_mathematical_core
from app.mie.contracts import (
    CalibrationStatus,
    DecisionAction,
    DecisionCandidate,
    DecisionChecks,
    Evidence,
    EvidenceDirection,
    EvidenceUse,
    ForecastHorizon,
    MarketRegime,
    MieShadowTrace,
    ModelHealth,
    ModelHealthStatus,
    ProbabilityForecast,
    ProbabilityVector,
    RegimeProbabilityVector,
    RegimeSnapshot,
    ValidationLevel,
    ValidationMetric,
    ValidationReference,
)

__all__ = [
    "CalibrationStatus",
    "DecisionAction",
    "DecisionCandidate",
    "DecisionChecks",
    "Evidence",
    "EvidenceDirection",
    "EvidenceUse",
    "ForecastHorizon",
    "MarketRegime",
    "MieShadowTrace",
    "ModelHealth",
    "ModelHealthStatus",
    "ProbabilityForecast",
    "ProbabilityVector",
    "RegimeProbabilityVector",
    "RegimeSnapshot",
    "ValidationLevel",
    "ValidationMetric",
    "ValidationReference",
    "adapt_legacy_mathematical_core",
]
