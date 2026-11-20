"""Fixed Live fill geometry and conservative remaining-cost DATA arithmetic."""

from decimal import Context, Decimal, localcontext

from app.domain.source_primitives import canonical, decode, sha


def _amount(value, *, positive=False):
    if type(value) is not str or not 0 < len(value) <= 80:
        raise ValueError("native_live_fill_economics_unknown")
    result = Decimal(value)
    if (
        not result.is_finite()
        or abs(result.as_tuple().exponent) > 80
        or (positive and result <= 0)
    ):
        raise ValueError("native_live_fill_economics_unknown")
    return result


def assess_fixed_live_fill_economics_v1(
    intent, facts, capture, *, policy_json, recheck_math_json
):
    """Keep entry/SL/TP/policy fixed; never turn earlier failure into a permit.

    All originally reserved projected costs remain charged. Actual entry fees
    are charged additionally, so an unknown entry/exit split cannot understate
    risk. Rebates do not increase the budget. These are projected remaining
    costs and actual entry fees, never a realized final PnL claim.
    """
    try:
        policy = decode(policy_json, maximum=65536)
        math = decode(recheck_math_json, maximum=65536)
        if (
            canonical(policy) != policy_json
            or canonical(math) != recheck_math_json
            or canonical(math["policy"]) != policy_json
            or sha(policy_json) != intent.policy_sha256
        ):
            raise ValueError
        if (
            facts.outcome != "FULL_FILL_PROTECTED"
            or facts.average_price is None
            or facts.entry_fees_usdt is None
            or facts.filled_contracts != intent.contracts
        ):
            raise ValueError
        if not (
            intent.stop_loss < facts.average_price < intent.take_profit
            if intent.direction == "long"
            else intent.take_profit < facts.average_price < intent.stop_loss
        ):
            raise ValueError
        result = math["result"]
        if len(result["gates"]) != 11 or any(
            gate["passed"] is not True for gate in result["gates"]
        ):
            raise ValueError
        with localcontext(Context(prec=200)):
            original_cost = _amount(math["risk_operands"]["round_trip_cost_per_base"])
            minimum_rr = _amount(policy["economics"]["minimum_net_rr"], positive=True)
            if original_cost < 0:
                raise ValueError
            units = (
                intent.contracts * capture.contract_value * capture.contract_multiplier
            )
            if units <= 0:
                raise ValueError
            entry_fee = max(Decimal(0), -facts.entry_fees_usdt)
            cost = original_cost + entry_fee / units
            risk_distance = abs(facts.average_price - intent.stop_loss)
            reward_distance = abs(intent.take_profit - facts.average_price)
            net_rr = (reward_distance - cost) / (risk_distance + cost)
            risk = (risk_distance + cost) * units
            notional = facts.average_price * units
            margin = notional / intent.leverage
            passed = (
                net_rr >= minimum_rr
                and risk <= max(intent.risk_samples)
                and notional <= intent.notional_amount
                and margin <= intent.margin_amount
                and risk <= Decimal("0.25")
                and notional <= Decimal(25)
            )
            return {
                "schema": "ctcc.native_live.actual_fill_economics_data.v1",
                "passed": passed,
                "code": "passed"
                if passed
                else "actual_fill_economics_or_reserved_risk_failed",
                "net_rr": str(net_rr),
                "minimum_net_rr": str(minimum_rr),
                "risk_amount": str(risk),
                "notional_amount": str(notional),
                "margin_amount": str(margin),
                "original_remaining_cost_per_base": str(original_cost),
                "actual_entry_fee_usdt": str(entry_fee),
                "execution_authority": False,
            }
    except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
        return {
            "schema": "ctcc.native_live.actual_fill_economics_data.v1",
            "passed": False,
            "code": "actual_fill_economics_unknown",
            "net_rr": None,
            "minimum_net_rr": None,
            "risk_amount": None,
            "notional_amount": None,
            "margin_amount": None,
            "original_remaining_cost_per_base": None,
            "actual_entry_fee_usdt": None,
            "execution_authority": False,
        }
