"""Synthetic raw/public fixed-clock math, not an owned Live submit proof."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.source_primitives import decode
from app.okx_live import final_recheck_v1 as subject
from app.okx_live import finite_portfolio_v1 as finite
from app.okx_live import qualification_math_v1 as math
from app.trade_qualification import native_live_public_source_v1 as public
from tests.unit.research import test_native_live_public_source_v1 as public_fixture
from tests.unit.test_live_qualification_math_finite_v1 import _declared_finite_options
from tests.unit.test_live_qualification_math_v1 import initial
from tests.unit.test_live_qualification_math_v1 import (
    live_source as live_source,  # noqa: PLC0414
)
from tests.unit.test_live_qualification_math_v1 import market as market  # noqa: PLC0414


def _later_financial(inputs, at):
    claims = inputs.claims
    financial = finite.LiveFinancialOperandsV1(
        **(
            claims.financial.model_dump(mode="python")
            | {
                "evaluated_at": at,
                "daily_window_started_at": at.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
                "weekly_window_started_at": at - timedelta(days=7),
            }
        )
    )
    return finite.LiveFinitePortfolioInputsV1(
        **(
            inputs.model_dump(mode="python")
            | {
                "claims": finite.LiveFinitePortfolioClaimsV1(
                    **(claims.model_dump(mode="python") | {"financial": financial})
                )
            }
        )
    )


def _budgets(calculation):
    operands = decode(calculation.risk_operands_json)
    samples = tuple(
        math._sample_risk(
            calculation.risk_inputs,
            intent=calculation.intent,
            policy=calculation.policy,
            entry=Decimal(operands[key]),
            stop=calculation.result.stop_loss,
            cost=Decimal(operands["round_trip_cost_per_base"]),
            now=calculation.source.evaluated_at,
        )
        for key in ("candidate_entry", "executable_reference")
    )
    assert all(s.passed for s in samples)
    return {
        "committed_risk": max(s.max_loss_amount for s in samples),
        "committed_margin": max(s.required_margin for s in samples),
        "committed_notional": max(s.notional for s in samples),
    }


@pytest.mark.asyncio
async def test_new_final_clock_rechecks_same_source_without_overwriting_committed_evidence(
    live_source, market, monkeypatch, tmp_path
):
    first_owner, first_source, options, _ = await initial(live_source, market)
    options = _declared_finite_options(first_source, options)
    original = math.evaluate_live_qualification_math_v1(first_source, **options)
    assert original.pre_evidence_complete
    before = original.receipt_json
    count = live_source[0].count
    first_owner.close()
    with (
        monkeypatch.context() as patch,
        contextmanager(public_fixture.live_source.__wrapped__)(
            patch, market, tmp_path / "new-post-g12-bank"
        ) as fresh,
    ):
        fresh[0].count = count + 10
        fresh[0].stamp()
        _, _, _, _, key, request = fresh
        opaque, _ = request(
            "post_g12", invocation=original.source.invocation, candidate_sha256="a" * 64
        )
        current_owner = await public.capture_owned_live_public_v1(opaque, key=key)
        current_source = public._replay_owned_live_public_v1(current_owner, key=key)
        current_inputs = _later_financial(
            options["risk_inputs"], current_source.evaluated_at
        )
        current = math.recheck_live_qualification_math_v1(
            current_source,
            original=original,
            consumed_event_keys=frozenset(),
            publication_candidate_sha256="a" * 64,
            risk_inputs=current_inputs,
        )
        assert current.pre_evidence_complete, [
            (g.gate.value, g.code) for g in current.result.gates
        ]
        committed = current.receipt_json
        later = public._replay_owned_live_public_v1(current_owner, key=key)
        assert (
            later.evaluated_at > current_source.evaluated_at and later != current_source
        )
        assert subject.same_live_public_bank_v1(current_source, later)
        inputs = _later_financial(current_inputs, later.evaluated_at)
        args = dict(
            initial=original,
            committed=current,
            current_source=later,
            risk_inputs=inputs,
            consumed_event_keys=frozenset(),
            publication_candidate_sha256="a" * 64,
            deadline=original.intent.expires_at,
            **_budgets(current),
        )
        checked = subject.final_current_recheck_data_v1(**args)
        assert (
            checked.pre_evidence_complete
            and checked.candidate_geometry_sha256 == original.candidate_geometry_sha256
        )
        assert original.receipt_json == before and current.receipt_json == committed
        assert (
            checked.receipt_json != committed and checked.execution_authority is False
        )
        with pytest.raises(ValueError):
            subject.final_current_recheck_data_v1(
                **(args | {"committed_risk": args["committed_risk"] / 2})
            )
        with pytest.raises(ValueError):
            subject.final_current_recheck_data_v1(
                **(
                    args
                    | {
                        "current_source": replace(
                            later, evaluated_at=original.intent.expires_at
                        )
                    }
                )
            )
