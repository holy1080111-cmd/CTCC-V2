from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.analysis import (
    MathematicalCoreComponent,
    MathematicalCoreSnapshot,
)
from app.mie.adapters import adapt_legacy_mathematical_core
from app.mie.contracts import (
    EvidenceUse,
    ForecastHorizon,
    ValidationLevel,
)

D = Decimal
NOW = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(seconds=1)
HORIZON = ForecastHorizon(label="15m", seconds=900)


def legacy_core() -> MathematicalCoreSnapshot:
    return MathematicalCoreSnapshot(
        status="long",
        directional_score=D("0.5"),
        confidence=D("0.5"),
        coverage=D("0.8"),
        consensus=D("0.8"),
        instability=D("0.1"),
        auxiliary_directional_score=D("0.2"),
        auxiliary_confidence=D("0.2"),
        components=[
            MathematicalCoreComponent(
                code="derivative",
                signal=D("0.6"),
                reliability=D("0.7"),
                validation_level="analytical",
                detail="derivative_multi_timeframe_causal_aggregate",
            ),
            MathematicalCoreComponent(
                code="conformal",
                signal=D("0.3"),
                reliability=D("0.5"),
                validation_level="prequential",
                validation_sample_size=60,
                validation_metric=D("0.9"),
                detail="conformal_multi_timeframe_causal_aggregate",
            ),
            MathematicalCoreComponent(
                code="structure",
                signal=D("-0.2"),
                reliability=D("0.4"),
                validation_level="auxiliary",
                detail="structure_multi_timeframe_causal_aggregate",
            ),
        ],
    )

def test_legacy_adapter_is_deterministic_and_dependency_aware() -> None:
    kwargs = dict(
        instrument_id="BTC-USDT-SWAP",
        horizon=HORIZON,
        observed_at=NOW,
        data_cutoff=CUTOFF,
        provenance_sha256="c" * 64,
    )
    first = adapt_legacy_mathematical_core(legacy_core(), **kwargs)
    second = adapt_legacy_mathematical_core(legacy_core(), **kwargs)

    assert [item.evidence_id for item in first] == [
        item.evidence_id for item in second
    ]
    assert {item.dependency_group for item in first} == {
        "legacy.price_path.shared"
    }
    assert all(item.execution_authority is False for item in first)


def test_legacy_adapter_preserves_validation_without_overstating_it() -> None:
    items = adapt_legacy_mathematical_core(
        legacy_core(),
        instrument_id="BTC-USDT-SWAP",
        horizon=HORIZON,
        observed_at=NOW,
        data_cutoff=CUTOFF,
        provenance_sha256="c" * 64,
    )
    by_code = {
        item.source.rsplit(".", 1)[-1]: item
        for item in items
    }

    assert by_code["derivative"].validation_level == ValidationLevel.CAUSAL
    assert (
        by_code["derivative"].permitted_use
        == EvidenceUse.RISK_DOWNGRADE_ONLY
    )
    assert by_code["conformal"].validation_level == ValidationLevel.PREQUENTIAL
    assert by_code["conformal"].validation_sample_size == 60
    assert by_code["conformal"].calibrated_probability is None
    assert by_code["structure"].validation_level == ValidationLevel.AUXILIARY
    assert (
        by_code["structure"].permitted_use
        == EvidenceUse.AUXILIARY_TIE_BREAK
    )
