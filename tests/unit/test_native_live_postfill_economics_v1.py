"""Pure declared synthetic arithmetic; never an account/source/execution proof."""

from dataclasses import replace
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import postfill_economics_v1 as subject
from app.okx_live.reconciliation_facts_v1 import assess_live_postsubmit_facts_v1
from tests import native_live_postsubmit_fixtures_v1 as fixture


def _case():
    value, inputs = fixture.scenario()
    facts = assess_live_postsubmit_facts_v1(value, **inputs)
    facts = replace(
        facts, filled_contracts=Decimal("0.01"), entry_fees_usdt=Decimal("-0.00001")
    )
    policy = canonical({"economics": {"minimum_net_rr": "2"}})
    original = decode(value.original_candidate_json.encode())
    original["policy_sha256"] = sha(policy)
    original = canonical(original)
    value = value.model_copy(
        update={
            "contracts": Decimal("0.01"),
            "policy_sha256": sha(policy),
            "original_candidate_json": original.decode(),
            "candidate_sha256": sha(original),
        }
    )
    math = canonical(
        {
            "policy": decode(policy),
            "result": {"gates": [{"passed": True} for _ in range(11)]},
            "risk_operands": {"round_trip_cost_per_base": "0.01"},
        }
    )
    capture = SimpleNamespace(contract_value=Decimal(1), contract_multiplier=Decimal(1))
    return value, facts, capture, policy, math


def test_actual_fill_keeps_fixed_geometry_and_conservative_original_cost_budget():
    value, facts, capture, policy, math = _case()
    result = subject.assess_fixed_live_fill_economics_v1(
        value, facts, capture, policy_json=policy, recheck_math_json=math
    )
    assert result["passed"] is True and result["execution_authority"] is False
    assert Decimal(result["risk_amount"]) <= max(value.risk_samples)
    assert result["actual_entry_fee_usdt"] == "0.00001"
    assert result["original_remaining_cost_per_base"] == "0.01"


@pytest.mark.parametrize(
    "mutation",
    (
        "fee",
        "reservation",
        "prior_gate",
        "foreign_policy",
        "wrong_fill_size",
        "outside_geometry",
    ),
)
def test_actual_fill_cannot_repair_failure_or_exceed_original_reservation(mutation):
    value, facts, capture, policy, math = _case()
    if mutation == "fee":
        facts = replace(facts, entry_fees_usdt=Decimal(-1))
    elif mutation == "reservation":
        value = value.model_copy(update={"risk_samples": (Decimal("0.0001"),)})
    elif mutation == "prior_gate":
        data = decode(math)
        data["result"]["gates"][0]["passed"] = False
        math = canonical(data)
    elif mutation == "foreign_policy":
        policy = canonical({"economics": {"minimum_net_rr": "0.1"}})
    elif mutation == "wrong_fill_size":
        facts = replace(facts, filled_contracts=Decimal(1))
    else:
        facts = replace(facts, average_price=Decimal(104))
    result = subject.assess_fixed_live_fill_economics_v1(
        value, facts, capture, policy_json=policy, recheck_math_json=math
    )
    assert result["passed"] is False and result["execution_authority"] is False


def test_actual_fill_math_is_independent_of_callers_decimal_context():
    value, facts, capture, policy, math = _case()
    expected = subject.assess_fixed_live_fill_economics_v1(
        value, facts, capture, policy_json=policy, recheck_math_json=math
    )
    with localcontext() as context:
        context.prec = 2
        assert (
            subject.assess_fixed_live_fill_economics_v1(
                value, facts, capture, policy_json=policy, recheck_math_json=math
            )
            == expected
        )
