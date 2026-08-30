from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.analysis import (
    MathematicalCoreComponent,
    MathematicalCoreSnapshot,
    MultiTimeframeAnalysis,
)
from app.domain.strategy import MathematicalConfirmation
from app.strategies.mathematical_confirmation import (
    mathematical_confirmation,
    mathematical_score_cap,
)

D = Decimal


def _analysis(core: MathematicalCoreSnapshot | None) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=D("100"),
        regime="bull_trend",
        overall_bias="long",
        alignment_score=100,
        trade_ready=True,
        timeframe_analyses={},
        mathematical_core=core,
        generated_at=datetime.now(timezone.utc),
    )


def _core(
    *,
    status: str = "long",
    directional_score: str = "0.9",
    confidence: str = "0.8",
    coverage: str = "0.9",
    consensus: str = "0.9",
    instability: str = "0.05",
) -> MathematicalCoreSnapshot:
    return MathematicalCoreSnapshot(
        status=status,
        directional_score=D(directional_score),
        confidence=D(confidence),
        coverage=D(coverage),
        consensus=D(consensus),
        instability=D(instability),
        components=[
            MathematicalCoreComponent(
                code="state",
                signal=D(directional_score),
                reliability=D("0.9"),
                validation_level="analytical",
                detail="state",
            )
        ],
    )


def test_reliable_aligned_core_unlocks_high_demo_grade() -> None:
    result = mathematical_confirmation(_analysis(_core()), "long")

    assert result.status == "confirmed"
    assert result.risk_grade == "high"
    assert result.directional_support == D("0.9")


def test_moderate_core_is_limited_to_medium_grade() -> None:
    result = mathematical_confirmation(
        _analysis(
            _core(
                directional_score="0.6",
                confidence="0.5",
                coverage="0.8",
                consensus="0.8",
                instability="0.1",
            )
        ),
        "long",
    )

    assert result.status == "confirmed"
    assert result.risk_grade == "medium"


def test_opposed_direction_is_blocked() -> None:
    result = mathematical_confirmation(_analysis(_core()), "short")

    assert result.status == "opposed"
    assert result.risk_grade == "blocked"
    assert result.directional_support == D("-0.9")


def test_unstable_core_is_blocked_even_when_direction_matches() -> None:
    result = mathematical_confirmation(
        _analysis(
            _core(
                status="unstable",
                confidence="0.05",
                instability="0.9",
            )
        ),
        "long",
    )

    assert result.status == "unstable"
    assert result.risk_grade == "blocked"
    assert result.auxiliary_bonus == 0


def test_missing_core_falls_back_to_low_insufficient_grade() -> None:
    result = mathematical_confirmation(_analysis(None), "long")

    assert result.status == "insufficient"
    assert result.risk_grade == "low"
    assert result.confidence == D("0")


def test_auxiliary_alignment_adds_only_a_separate_bounded_bonus() -> None:
    core = _core()
    core = core.model_copy(
        update={
            "auxiliary_directional_score": D("0.9"),
            "auxiliary_confidence": D("0.81"),
            "components": [
                *core.components,
                MathematicalCoreComponent(
                    code="structure",
                    signal=D("0.9"),
                    reliability=D("0.8"),
                    validation_level="auxiliary",
                    detail="structure",
                ),
            ],
        }
    )

    result = mathematical_confirmation(_analysis(core), "long")

    assert result.auxiliary_bonus == 4
    assert result.auxiliary_directional_support == D("0.9")
    assert result.component_codes == ["state"]
    assert result.auxiliary_component_codes == ["structure"]
    assert result.risk_grade == "high"


def test_opposed_auxiliary_evidence_never_adds_bonus() -> None:
    core = _core()
    core = core.model_copy(
        update={
            "auxiliary_directional_score": D("-0.9"),
            "auxiliary_confidence": D("0.9"),
            "components": [
                *core.components,
                MathematicalCoreComponent(
                    code="momentum",
                    signal=D("-0.9"),
                    reliability=D("0.8"),
                    validation_level="auxiliary",
                    detail="momentum",
                ),
            ],
        }
    )

    result = mathematical_confirmation(_analysis(core), "long")

    assert result.auxiliary_bonus == 0


def test_domain_rejects_forged_high_grade_with_low_confidence() -> None:
    from app.domain.strategy import MathematicalConfirmation

    with pytest.raises(ValidationError):
        MathematicalConfirmation(
            status="confirmed",
            risk_grade="high",
            confidence=D("0.2"),
            directional_support=D("0.9"),
            reliability=D("0.9"),
            coverage=D("0.9"),
            consensus=D("0.9"),
            instability=D("0.1"),
            component_codes=["state"],
        )


def test_domain_rejects_unidentified_auxiliary_bonus() -> None:
    from app.domain.strategy import MathematicalConfirmation

    with pytest.raises(ValidationError):
        MathematicalConfirmation(
            status="confirmed",
            risk_grade="high",
            confidence=D("0.8"),
            directional_support=D("0.9"),
            reliability=D("0.8"),
            coverage=D("0.9"),
            consensus=D("0.9"),
            instability=D("0.1"),
            auxiliary_bonus=3,
            auxiliary_directional_support=D("0.8"),
        )


def test_domain_rejects_high_grade_without_validated_components() -> None:
    from app.domain.strategy import MathematicalConfirmation

    with pytest.raises(ValidationError, match="validated mathematical components"):
        MathematicalConfirmation(
            status="confirmed",
            risk_grade="high",
            confidence=D("0.8"),
            directional_support=D("0.9"),
            reliability=D("0.8"),
            coverage=D("0.9"),
            consensus=D("0.9"),
            instability=D("0.1"),
        )


@pytest.mark.parametrize(
    ("grade", "expected_cap"),
    [("high", 100), ("medium", 89), ("low", 79), ("blocked", 0)],
)
def test_mathematical_score_cap_is_downward_only(
    grade: str, expected_cap: int
) -> None:
    status = "opposed" if grade == "blocked" else "confirmed"
    confirmation = MathematicalConfirmation(
        status=status,
        risk_grade=grade,
        confidence=D("0.8"),
        directional_support=D("-0.8") if grade == "blocked" else D("0.8"),
        reliability=D("0.8"),
        coverage=D("0.9"),
        consensus=D("0.9"),
        instability=D("0.1"),
        component_codes=(
            ["state"] if grade in {"high", "medium"} else []
        ),
    )

    assert mathematical_score_cap(
        confirmation, medium_minimum=80, high_minimum=90
    ) == expected_cap
