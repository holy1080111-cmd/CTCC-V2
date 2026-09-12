"""Synthetic G1–G11 sources; account/authority fields are fictional typed claims.

No market, account, history, risk reservation, or Demo authorization is collected
from a real service. Complete/armed/write flags below describe ONLY the synthetic
test world. Hashes label those fixture claims, not actual exchange receipts.
Real OHLC evaluators rebuild every analysis/event/stop/target and gate decision.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from decimal import Context, Decimal, localcontext
from types import MappingProxyType

from app.strategies.structural_protection import (
    StructuralProtectionSelection,
    select_structural_protection,
)
from app.trade_qualification.economics import EconomicsResult, evaluate_economics
from app.trade_qualification.portfolio import PortfolioRiskResult, evaluate_portfolio
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
)
from tests.unit.qualification_prefix_fixtures import (
    MINIMUM_SCORE,
    SyntheticPrefixTrace,
    prefix_source,
    run_prefix_source,
)
from tests.unit.test_qualification_economics import policy as cost_policy
from tests.unit.test_qualification_portfolio import (
    STAMP_FIELDS,
    account,
    authority,
    instrument,
    stamp,
)
from tests.unit.test_qualification_portfolio import policy as risk_policy

D = Decimal
PROTECTION_PARAMETERS = MappingProxyType(
    {
        "expected_slippage_bps": D(1),
        "cost_bps": D(10),
        "min_net_rr": D(2),
        "min_stop_distance_atr": D(1),
        "atr_buffer_multiplier": D("0.25"),
        "minimum_buffer_bps": D(5),
    }
)
REQUESTED_CONTRACTS = D(10)
REQUESTED_LEVERAGE = 10


@dataclass(frozen=True)
class SyntheticEngineTrace:
    prefix: SyntheticPrefixTrace
    protection: StructuralProtectionSelection
    economics: EconomicsResult
    portfolio: PortfolioRiskResult


def engine_source(direction="long", *, report_id=None):
    """Enrich only historical raw wicks; never move entry, return or trigger bars."""
    source = prefix_source(direction, report_id=report_id)
    with localcontext(Context(prec=100)):
        for timeframe in ("15m", "1H", "4H"):
            rows = source.market.candles[timeframe]
            for offset, price, low in (
                (-30, D("110.17"), False),
                (-25, D("98.50"), True),
                (-20, D("97.20"), True),
                (-15, D("107.23"), False),
            ):
                field = "low" if low else "high"
                if direction == "short":
                    field = "high" if low else "low"
                    price = D(200) - price
                rows[offset] = rows[offset].model_copy(update={field: price})
    return source


def _claim_hash(role, observed_at):
    label = f"synthetic-only-not-an-exchange-receipt:{role}:{observed_at.isoformat()}"
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def portfolio_claims(source):
    """Explicit fictional Demo account/instrument/guard evidence, not real state."""
    now = source.evaluated_at
    stamps = {
        role: stamp(
            environment="demo",
            observed_at=now,
            received_at=now,
            source_sha256=_claim_hash(role, now),
        )
        for role in STAMP_FIELDS
    }
    return {
        "requested_contracts": REQUESTED_CONTRACTS,
        "requested_leverage": REQUESTED_LEVERAGE,
        "instrument": instrument(
            instrument_id=source.market.instrument_id,
            observed_at=now,
            received_at=now,
            source_sha256=_claim_hash("synthetic-contract-metadata", now),
        ),
        "account": account(
            **stamps,
            peak_observed_at=now - timedelta(days=7),
            peak_window_started_at=now - timedelta(days=30),
            history_start=now - timedelta(days=7),
            history_end=now,
        ),
        "authority": authority(
            stamp=stamp(
                environment="demo",
                observed_at=now,
                received_at=now,
                source_sha256=_claim_hash("synthetic-demo-guard", now),
            )
        ),
    }


def portfolio_policy(source):
    return risk_policy(
        drawdown_window_started_at=source.evaluated_at - timedelta(days=30)
    )


def prefix_arguments(source):
    return {
        "intent": QualificationIntent(
            report_id=source.report_id,
            instrument_id=source.market.instrument_id,
            strategy=source.strategy,
            direction=source.direction,
            candidate_entry=source.market.ticker.last,
            created_at=source.evaluated_at,
            expires_at=source.evaluated_at + timedelta(minutes=5),
        ),
        "policy": QualificationPrefixPolicy(
            policy_id="synthetic-engine-prefix-policy",
            data=source.policy,
            minimum_score=MINIMUM_SCORE,
            tick_size=source.tick_size,
            max_allowed_drift_bps=source.maximum_drift_bps,
        ),
        "quote": source.quote,
        "reference": source.reference,
        "consumed_event_keys": frozenset(),
        "evaluated_at": source.evaluated_at,
    }


def engine_inputs(source):
    """Exact composition API inputs, with no precomputed approvals or geometry."""
    from app.trade_qualification.engine import (
        PortfolioInputs,
        PreEvidencePolicy,
        ProtectionPolicy,
    )

    arguments = prefix_arguments(source)
    arguments["policy"] = PreEvidencePolicy(
        policy_id="synthetic-engine-policy",
        prefix=arguments["policy"],
        protection=ProtectionPolicy(
            policy_id="synthetic-structural-policy", **PROTECTION_PARAMETERS
        ),
        economics=cost_policy(),
        portfolio=portfolio_policy(source),
    )
    arguments["risk_inputs"] = PortfolioInputs(**portfolio_claims(source))
    return arguments


def run_engine_source(source):
    """Independent evaluator chain to compare with the actual G1–G11 engine."""
    prefix = run_prefix_source(source)
    event = prefix.detection
    protection = select_structural_protection(
        event,
        prefix.market,
        prefix.analysis,
        observed_at=source.evaluated_at,
        entry=event.trigger.trigger_price,
        tick_size=source.tick_size,
        **PROTECTION_PARAMETERS,
    )
    assert protection.protection_valid, protection.to_audit_json()
    economics = evaluate_economics(
        report_id=source.report_id,
        instrument_id=source.market.instrument_id,
        direction=source.direction,
        candidate_entry=event.trigger.trigger_price,
        stop_loss=protection.selected.stop.final_stop,
        take_profit=protection.selected.target.final_target,
        quote=source.quote.quote,
        policy=cost_policy(),
        current_time=source.evaluated_at,
    )
    assert economics.passed, economics
    portfolio = evaluate_portfolio(
        report_id=source.report_id,
        instrument_id=source.market.instrument_id,
        direction=source.direction,
        candidate_entry=economics.candidate_entry,
        stop_loss=economics.stop_loss,
        round_trip_cost_per_base=economics.cost_per_base,
        policy=portfolio_policy(source),
        current_time=source.evaluated_at,
        **portfolio_claims(source),
    )
    assert portfolio.passed, portfolio
    return SyntheticEngineTrace(prefix, protection, economics, portfolio)
