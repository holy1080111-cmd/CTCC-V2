"""Fictional numeric-UID inputs BEFORE genuine G1--G12 computations.

Only publication is a synthetic storage contract; no actual account, order or
runtime authority. Report renames rebuild all evaluators, never mutate a PASS.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.trade_evidence import gates
from app.trade_qualification.engine import PortfolioInputs
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.reservations import (
    STAMP_FIELDS,
    AccountLedgerClaims,
    LedgerScope,
    ReservationRequest,
)
from app.trade_qualification.service import _plain
from tests.unit import qualification_recheck_fixtures as fixtures
from tests.unit.qualification_engine_fixtures import engine_inputs, engine_source
from tests.unit.test_qualification_evidence_gate import Clock, synthetic_publisher

SYNTHETIC_UID = "123456789"


@dataclass(frozen=True)
class LedgerFixture:
    request: ReservationRequest
    claims: AccountLedgerClaims
    source: object

    @property
    def now(self):
        return self.source.latest_source.evaluated_at


def ledger_fixture(
    direction="long",
    *,
    report_id=None,
    account_id=SYNTHETIC_UID,
    quote_max_age_seconds=None,
):
    def numeric_inputs(source):
        inputs = engine_inputs(source)
        raw = _plain(inputs["risk_inputs"])
        raw["account"]["account_id"] = account_id
        for name in STAMP_FIELDS:
            raw["account"][name]["account_id"] = account_id
        raw["authority"]["stamp"]["account_id"] = account_id
        inputs["risk_inputs"] = PortfolioInputs.model_validate(raw, strict=True)
        if quote_max_age_seconds is not None:
            inputs["policy"] = inputs["policy"].model_copy(
                update={
                    "economics": inputs["policy"].economics.model_copy(
                        update={
                            "maximum_quote_age_seconds": quote_max_age_seconds,
                        }
                    ),
                }
            )
        return inputs

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixtures, "engine_inputs", numeric_inputs)
        source = asyncio.run(
            fixtures.capture_recheck_source(
                engine_source(direction, report_id=report_id),
            )
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gates, "publish_evidence", synthetic_publisher())
        evidence = gates.publish_qualification_evidence(
            Path.cwd(),
            source.original_source.market,
            run=source.original_run,
            **source.original_inputs,
            purpose="synthetic_test",
            clock=Clock(source.original_source.evaluated_at),
        )
    origin = freeze_recheck_origin(evidence)
    scope = LedgerScope(account_id=account_id, settlement_currency="USDT")
    claims = AccountLedgerClaims(
        scope=scope,
        account=source.current_risk_inputs.account,
        authority=source.current_risk_inputs.authority,
        reconciliation_id="a" * 64,
    )
    request = ReservationRequest(
        scope=scope,
        origin=origin,
        quote=source.latest_source.quote.quote,
        risk_inputs=source.current_risk_inputs,
        expected_account_revision=1,
        expected_ledger_revision=1,
    )
    return LedgerFixture(request, claims, source)
