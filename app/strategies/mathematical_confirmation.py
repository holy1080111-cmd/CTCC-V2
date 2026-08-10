from __future__ import annotations

from decimal import Decimal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.strategy import MathematicalConfirmation

D = Decimal


def mathematical_score_cap(
    confirmation: MathematicalConfirmation,
    *,
    medium_minimum: int,
    high_minimum: int,
) -> int:
    """Return a downward-only raw-score ceiling for one risk grade."""

    if not 0 < medium_minimum < high_minimum <= 100:
        raise ValueError("invalid mathematical score thresholds")
    if confirmation.risk_grade == "high":
        return 100
    if confirmation.risk_grade == "medium":
        return high_minimum - 1
    if confirmation.risk_grade == "low":
        return medium_minimum - 1
    return 0


def mathematical_confirmation(
    analysis: MultiTimeframeAnalysis, direction: str
) -> MathematicalConfirmation:
    """Interpret the common core for one direction without adding score."""

    core = analysis.mathematical_core
    if direction not in {"long", "short"} or core is None:
        return MathematicalConfirmation(
            status="insufficient",
            risk_grade="low",
            confidence=D("0"),
            directional_support=D("0"),
            reliability=D("0"),
            coverage=D("0"),
            consensus=D("0"),
            instability=D("0"),
            auxiliary_bonus=0,
            auxiliary_directional_support=D("0"),
        )

    direction_sign = D("1") if direction == "long" else D("-1")
    directional_support = direction_sign * core.directional_score
    reliability = (
        core.coverage * core.consensus * (D("1") - core.instability)
    )
    auxiliary_directional_support = (
        direction_sign * core.auxiliary_directional_score
    )
    validated_components = [
        component.code
        for component in core.components
        if component.validation_level != "auxiliary"
        and component.reliability > 0
    ]
    auxiliary_components = [
        component.code
        for component in core.components
        if component.validation_level == "auxiliary"
        and component.reliability > 0
    ]
    auxiliary_bonus = (
        min(5, int(D("5") * core.auxiliary_confidence))
        if auxiliary_directional_support > 0 and auxiliary_components
        else 0
    )
    if core.status == "unstable":
        status = "unstable"
        risk_grade = "blocked"
    elif directional_support <= D("-0.25") and core.confidence >= D("0.20"):
        status = "opposed"
        risk_grade = "blocked"
    elif directional_support >= D("0.25") and core.confidence >= D("0.20"):
        status = "confirmed"
        if (
            core.confidence >= D("0.65")
            and reliability >= D("0.65")
            and core.instability <= D("0.20")
        ):
            risk_grade = "high"
        elif core.confidence >= D("0.35") and reliability >= D("0.45"):
            risk_grade = "medium"
        else:
            risk_grade = "low"
    elif core.status == "insufficient":
        status = "insufficient"
        risk_grade = "low"
    else:
        status = "mixed"
        risk_grade = "low"

    if risk_grade == "blocked":
        auxiliary_bonus = 0

    return MathematicalConfirmation(
        status=status,
        risk_grade=risk_grade,
        confidence=core.confidence,
        directional_support=max(D("-1"), min(D("1"), directional_support)),
        reliability=max(D("0"), min(D("1"), reliability)),
        coverage=core.coverage,
        consensus=core.consensus,
        instability=core.instability,
        component_codes=validated_components,
        auxiliary_bonus=auxiliary_bonus,
        auxiliary_directional_support=max(
            D("-1"), min(D("1"), auxiliary_directional_support)
        ),
        auxiliary_component_codes=auxiliary_components,
    )
