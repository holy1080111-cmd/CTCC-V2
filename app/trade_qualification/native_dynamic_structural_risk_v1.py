"""Native DATA adapter for the existing structural ladder, never order authority.

The G11 and R7 actors replay their genuine owners before calling this module.
Its pure result cannot acquire an account, set leverage, resize an intent, move
protection, renew an event, grant G12 publication or bypass a ledger hard cap.
"""

import sys as _native_sys
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from app.demo_automation import risk_profile, structural_risk
from app.domain import strategy as candidate_models
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.source_primitives import canonical, decode, sha
from app.strategies import derivative_confirmation as derivatives
from app.strategies import mathematical_confirmation as mathematics
from app.strategies import structural_protection as protection
from app.trade_qualification import account_ledger_claims_v2 as claims_codec
from app.trade_qualification import account_owned_financial_v1 as financial
from app.trade_qualification import captured_execution_mode_v1 as execution
from app.trade_qualification import demo_control as control
from app.trade_qualification import hard_risk_caps as hard
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import owned_demo_risk_policy as risk_config

VERSION = "ctcc.native_dynamic_structural_risk.v1"


class NativeDynamicRiskError(ValueError):
    """Fixed source-safe refusal; no source document or private values escape."""


def _deny(code="native_dynamic_structural_risk_unavailable"):
    raise NativeDynamicRiskError(code) from None


@dataclass(frozen=True, slots=True)
class NativeDynamicRiskDataV1:
    receipt_json: bytes
    selected_leverage: int
    tier_risk_pct: Decimal

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)

    @property
    def execution_authority(self):
        return False


def _geometry(selection, result):
    if (
        not isinstance(selection, protection.StructuralProtectionSelection)
        or not selection.protection_valid
        or selection.selected is None
        or selection.reference_entry != result.candidate_entry
    ):
        _deny("native_dynamic_original_structural_geometry_required")
    chosen = selection.selected
    if (
        chosen.stop.final_stop != result.stop_loss
        or chosen.target.final_target != result.take_profit
        or chosen.stop.buffer is None
        or chosen.gross_rr is None
    ):
        _deny("native_dynamic_fixed_original_prices_changed")
    return candidate_models.StructuralProtectionGeometry(
        timeframe=chosen.stop.anchor.timeframe,
        source_closed_at=max(
            chosen.stop.anchor.source_closed_at,
            chosen.target.anchor.source_closed_at,
        ),
        reference_entry=result.candidate_entry,
        stop_anchor=chosen.stop.anchor.anchor_price,
        # The selected target is already rounded inward to the exchange tick.
        # Its original raw anchor remains in the pinned selection audit.
        target_anchor=chosen.target.final_target,
        volatility_buffer=abs(chosen.stop.anchor.anchor_price - result.stop_loss),
        stop_loss=result.stop_loss,
        take_profit=result.take_profit,
        gross_risk_reward=chosen.gross_rr,
    )


def evaluate_native_dynamic_risk_data_v1(
    *,
    settings,
    analysis,
    selection,
    result,
    claims,
    instrument,
    contracts,
    entry,
    cost_per_base,
    raw_score,
    effective_score,
    leverage_ceiling,
    configured_leverage,
    expires_at,
):
    """Pure calculation only. Exact typed data does not authenticate its source.

    Reuse the existing score interpretation and structural solver. Stairs are
    upper limits. A lower actually configured step remains legal if the fixed
    quantity passes its tier risk and every independent G11/ledger hard cap.
    """
    identity = risk_config.freeze_demo_risk_config(settings)
    if (
        type(analysis) is not MultiTimeframeAnalysis
        or type(claims) is not claims_codec.FiniteAccountLedgerClaimsV1
        or type(contracts) is not Decimal
        or not contracts.is_finite()
        or not Decimal(0) < contracts < Decimal("1e20")
        or len(contracts.as_tuple().digits) > 40
        or contracts.as_tuple().exponent < -20
        or type(entry) is not Decimal
        or not entry.is_finite()
        or entry <= 0
        or type(cost_per_base) is not Decimal
        or not cost_per_base.is_finite()
        or cost_per_base < 0
        or type(raw_score) is not int
        or not 0 <= raw_score <= 100
        or type(effective_score) is not int
        or not 0 <= effective_score <= raw_score
        or type(leverage_ceiling) is not int
        or not 1 <= leverage_ceiling <= 20
        or type(configured_leverage) is not int
        or configured_leverage not in structural_risk.LEVERAGE_LADDER
        or configured_leverage > leverage_ceiling
        or expires_at > result.entry_zone.expires_at
    ):
        _deny("native_dynamic_exact_data_or_ceiling_invalid")
    # Decimal operations share a fixed high precision, independent of ambient
    # context. Financial cap comparisons below use exact rational amounts.
    with localcontext(Context(prec=100)):
        actual_cost = max(
            cost_per_base, entry * structural_risk.structural_cost_rate(settings)
        )
        price_risk = abs(entry - result.stop_loss)
        reward = abs(result.take_profit - entry)
        if price_risk <= 0 or reward <= actual_cost:
            _deny("native_dynamic_fixed_economics_invalid")
        net_rr = (reward - actual_cost) / (price_risk + actual_cost)
        if net_rr < settings.okx_demo_structural_min_net_risk_reward:
            _deny("native_dynamic_fixed_net_rr_rejected")
        candidate = candidate_models.TradeCandidate(
            strategy=result.strategy,
            direction=result.direction,
            score=raw_score,
            risk_score=effective_score,
            entry=entry,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit,
            risk_reward=net_rr,
            invalidation="fixed original structural stop",
            expires_at=expires_at,
            mathematical_confirmation=mathematics.mathematical_confirmation(
                analysis, result.direction
            ),
            derivative_confirmation=derivatives.derivative_confirmation(
                analysis, result.direction
            ),
            protection_model="structure",
            structural_protection=_geometry(selection, result),
            estimated_round_trip_cost_pct=actual_cost / entry,
            gross_risk_reward=reward / price_risk,
            net_risk_reward=net_rr,
        )
        adjusted_score, rejection = risk_profile.mathematical_adjusted_score(
            candidate, settings
        )
        if rejection is not None or adjusted_score < settings.strategy_min_score:
            _deny("native_dynamic_mathematical_risk_score_rejected")
        candidate = candidate_models.TradeCandidate.model_validate(
            {**candidate.model_dump(), "risk_score": adjusted_score}
        )
        tier = risk_profile.score_risk_tier(adjusted_score, settings)
        permitted = [
            step
            for step in structural_risk.LEVERAGE_LADDER
            if step <= min(tier.leverage, leverage_ceiling)
        ]
        if not permitted:
            _deny("native_dynamic_no_permitted_leverage_step")
        tier_risk = min(
            tier.risk_pct, claims.risk_policy.risk_per_trade_pct, Decimal("0.005")
        )
        solver_tier = type(tier).model_validate(
            {
                **tier.model_dump(),
                "leverage": permitted[-1],
                "risk_pct": tier_risk,
            }
        )
        state = claims.account
        exposures = (*state.positions, *state.pending_reservations)
        pending_margin = sum(
            (item.margin for item in state.pending_reservations), Decimal(0)
        )
        existing_margin = sum((item.margin for item in exposures), Decimal(0))
        instrument_margin = sum(
            (
                item.margin
                for item in exposures
                if item.instrument_id == instrument.instrument_id
            ),
            Decimal(0),
        )
        bucket = min(
            Decimal(300),
            settings.okx_demo_position_margin_bucket_usdt,
            claims.financial.position_margin_bucket_usdt,
        )
        margin_cap = min(
            state.equity,
            bucket - instrument_margin,
            state.available_margin - pending_margin,
            state.equity
            * min(
                settings.okx_demo_portfolio_max_margin_pct,
                claims.risk_policy.max_portfolio_margin_pct,
                Decimal("0.60"),
            )
            - existing_margin,
        )
        if margin_cap <= 0:
            _deny("native_dynamic_actual_margin_capacity_exhausted")
        solved = structural_risk.select_structural_leverage(
            candidate,
            solver_tier,
            settings,
            account_equity=state.equity,
            position_margin_cap=margin_cap,
        )
        if configured_leverage > min(
            solved.leverage_cap,
            instrument.max_leverage,
            claims.risk_policy.max_leverage,
        ):
            _deny("native_dynamic_configured_leverage_above_candidate_cap")
        risk = hard.rounded_up_amount(
            Fraction(contracts)
            * Fraction(instrument.contract_value)
            * (
                abs(Fraction(entry) - Fraction(result.stop_loss))
                + Fraction(actual_cost)
            )
        )
        if risk > Fraction(state.equity) * Fraction(tier_risk):
            _deny("native_dynamic_score_tier_risk_exceeded")
        document = {
            "schema_version": VERSION,
            "settings_sha256": identity.sha256,
            "raw_score": raw_score,
            "original_effective_score": effective_score,
            "risk_score": adjusted_score,
            "mathematical_confirmation": candidate.mathematical_confirmation.model_dump(
                mode="json"
            ),
            "derivative_confirmation": candidate.derivative_confirmation.model_dump(
                mode="json"
            ),
            "tier": solver_tier.model_dump(mode="json"),
            "caller_leverage_ceiling": leverage_ceiling,
            "selected_leverage": configured_leverage,
            "solver_preferred_leverage": solved.selected_leverage,
            "required_leverage_for_full_risk_budget": solved.required_leverage,
            "candidate_leverage_cap": solved.leverage_cap,
            "twenty_x_eligible": solved.twenty_x_eligible,
            "cap_reasons": list(solved.cap_reasons),
            "account_equity": str(state.equity),
            "position_margin_cap": str(margin_cap),
            "aggregate_margin_denominator": "settlement_account_equity",
            "contracts": str(contracts),
            "contract_value": str(instrument.contract_value),
            "entry": str(entry),
            "stop_loss": str(result.stop_loss),
            "take_profit": str(result.take_profit),
            "cost_per_base": str(actual_cost),
            "net_rr": str(net_rr),
            "expires_at": expires_at.isoformat(),
            "admission": "DENY",
            "execution_authority": False,
        }
    raw = canonical(document)
    if not 0 < len(raw) <= 32768:
        _deny("native_dynamic_receipt_capacity")
    return NativeDynamicRiskDataV1(raw, configured_leverage, tier_risk)


def validate_native_settings_projection_v1(settings, claims, native_policy_sha256):
    """Validate native finite policy DATA without relabeling Control identity.

    The Source actor separately pins/replays the entire actual Settings object.
    Native Control config is a scope/genesis/plan/inventory identity, and its
    policy pin is this financial projection. Neither is the dynamic subset SHA.
    """
    dynamic = risk_config.freeze_demo_risk_config(settings)
    if type(claims) is not claims_codec.FiniteAccountLedgerClaimsV1:
        _deny("native_dynamic_exact_native_finite_claims_required")
    projected = financial._configured_policy_from_settings_v1(
        settings,
        drawdown_window_started_at=claims.risk_policy.drawdown_window_started_at,
    )
    if (
        projected.risk_policy != claims.risk_policy
        or projected.position_margin_bucket_usdt
        != claims.financial.position_margin_bucket_usdt
        or projected.selected_settings_sha256 != native_policy_sha256
    ):
        _deny("native_dynamic_native_financial_projection_changed")
    return dynamic, projected


def evaluate_actor_native_dynamic_risk_v1(
    original,
    issued,
    *,
    result,
    analysis,
    entry,
    cost_per_base,
    raw_score,
    effective_score,
    contracts,
    leverage_ceiling,
    expires_at,
    key,
):
    """Fixed actor seam. Caller must replay its actual held sources first."""
    from app.trade_qualification import native_account_risk_assembly_v1 as account
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import owned_native_candidate_v1 as g11

    if key is not g11._DYNAMIC_RISK_KEY_V1 and key is not r7._DYNAMIC_RISK_KEY_V2:
        _deny("native_dynamic_fixed_actor_required")
    if (
        type(issued) is not account.IssuedAccountForG11V1
        or issued.scope != original["scope"]
        or original["selection"].to_audit_json() != original["selection_raw"]
        or expires_at
        > min(
            original["deadline"],
            original["computed"].intent.expires_at,
            original["computed"].detection.trigger.expires_at,
        )
    ):
        _deny("native_dynamic_actual_issued_source_required")
    captured = decode(original["control"])
    state = control.decode(control.canonical(captured["state"]))
    if (
        state.pins != issued.control_expectation.pins
        or state.pins is None
        or issued.policy_sha256 != state.pins.policy_sha256
    ):
        _deny("native_dynamic_original_native_control_pins_changed")
    identity, native_policy = validate_native_settings_projection_v1(
        issued.private_client.settings,
        issued.claims,
        issued.policy_sha256,
    )
    mode = issued.execution_mode
    side = "net" if mode.position_mode == "net_mode" else result.direction
    observations = tuple(
        row
        for row in mode.leverage_observations
        if row.instrument_id == original["computed"].intent.instrument_id
        and row.margin_mode == "isolated"
        and row.position_side == side
    )
    if (
        len(observations) != 1
        or type(observations[0].leverage) is not Decimal
        or observations[0].leverage not in structural_risk.LEVERAGE_LADDER
    ):
        _deny("native_dynamic_actual_configured_ladder_step_required")
    chosen = int(observations[0].leverage)
    selected = execution.select_captured_isolated_execution_v1(
        mode,
        instrument_id=original["computed"].intent.instrument_id,
        direction=result.direction,
        requested_leverage=chosen,
    )
    specs = tuple(
        row
        for row in issued.instrument_specs
        if row.instrument_id == original["computed"].intent.instrument_id
    )
    if len(specs) != 1:
        _deny("native_dynamic_actual_instrument_spec_required")
    value = evaluate_native_dynamic_risk_data_v1(
        settings=issued.private_client.settings,
        analysis=analysis,
        selection=original["selection"],
        result=result,
        claims=issued.claims,
        instrument=specs[0],
        contracts=contracts,
        entry=entry,
        cost_per_base=cost_per_base,
        raw_score=raw_score,
        effective_score=effective_score,
        leverage_ceiling=leverage_ceiling,
        configured_leverage=chosen,
        expires_at=expires_at,
    )
    document = decode(value.receipt_json)
    document.update(
        scope=issued.scope.model_dump(mode="json", round_trip=True),
        credential_session_sha256=issued.control_expectation.pins.credential_session_sha256,
        control_event_sha256=captured["event_sha256"],
        control_state_sha256=sha(canonical(captured["state"])),
        native_control_config_sha256=state.pins.config_sha256,
        native_financial_policy_sha256=native_policy.selected_settings_sha256,
        dynamic_settings_sha256=identity.sha256,
        original_event_key=g11.local.event_identity(original["computed"].detection),
        original_source_sha256=original["computed"].g1.source_sha256,
        analysis_sha256=sha(canonical(analysis.model_dump(mode="json"))),
        original_selection_sha256=sha(original["selection_raw"].encode("ascii")),
        original_policy_sha256=issued.control_expectation.pins.policy_sha256,
        issued_row_sha256=issued.issued_row_sha256,
        issuer_sha256=issued.issuer_sha256,
        account_revision=issued.account_revision,
        ledger_revision=issued.ledger_revision,
        execution_selection_sha256=selected.receipt_sha256,
    )
    raw = canonical(document)
    if not 0 < len(raw) <= 32768:
        _deny("native_dynamic_receipt_capacity")
    return NativeDynamicRiskDataV1(raw, value.selected_leverage, value.tier_risk_pct)


_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    ("evaluate_actor_native_dynamic_risk_v1",),
    dependencies=(
        risk_profile,
        structural_risk,
        candidate_models,
        derivatives,
        mathematics,
        protection,
        claims_codec,
        execution,
        control,
        hard,
        risk_config,
        financial,
        financial.configured,
        financial.portfolio,
    ),
)
