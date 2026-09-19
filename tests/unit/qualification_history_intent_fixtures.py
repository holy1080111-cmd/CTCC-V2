"""Native synthetic G12 through versioned replay; no authentic account or order."""

import asyncio
from types import SimpleNamespace

from app.trade_evidence.gates import publish_qualification_evidence
from app.trade_qualification import history_engine
from app.trade_qualification.engine import PortfolioInputs
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.reservations import (
    STAMP_FIELDS,
    AccountLedgerClaims,
    LedgerScope,
    ReservationRequest,
)
from app.trade_qualification.service import _plain
from tests.unit.history_qualification_fixtures import history_source
from tests.unit.qualification_ledger_fixtures import SYNTHETIC_UID, LedgerFixture
from tests.unit.test_history_expansion_v2 import current_inputs, v2_inputs
from tests.unit.test_history_reversal_v3 import v3_inputs
from tests.unit.test_qualification_evidence_gate import Clock


def history_ledger_fixture(root, *, version, direction, account_id=SYNTHETIC_UID):
    if version == 2:
        source = history_source(
            "volatility_expansion", direction, bracket=True, expansion_entry=True
        )
        inputs = v2_inputs(source)
        evaluate = history_engine.evaluate_history_pre_evidence_v2
    else:
        assert version == 3
        source = history_source(
            "structure_reversal", direction, bracket=True, reversal_bracket=True
        )
        inputs = v3_inputs(source)
        evaluate = history_engine.evaluate_history_pre_evidence_v3
    risk = _plain(inputs["risk_inputs"])
    risk["account"]["account_id"] = account_id
    for name in STAMP_FIELDS:
        risk["account"][name]["account_id"] = account_id
    risk["authority"]["stamp"]["account_id"] = account_id
    inputs["risk_inputs"] = PortfolioInputs.model_validate(risk, strict=True)
    run = evaluate(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    evidence = publish_qualification_evidence(
        root,
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result.gates[-1].passed, evidence.error_detail
    origin = freeze_recheck_origin(evidence)
    market, current = asyncio.run(current_inputs(source, inputs, origin))
    scope = LedgerScope(account_id=account_id, settlement_currency="USDT")
    current_risk = current["current_risk_inputs"]
    claims = AccountLedgerClaims(
        scope=scope,
        account=current_risk.account,
        authority=current_risk.authority,
        reconciliation_id="a" * 64,
    )
    request = ReservationRequest(
        scope=scope,
        origin=origin,
        quote=current["quote"].quote,
        risk_inputs=current_risk,
        expected_account_revision=1,
        expected_ledger_revision=1,
    )
    chain = SimpleNamespace(
        original_source=source,
        original_inputs=inputs,
        latest_source=SimpleNamespace(
            market=market,
            quote=current["quote"],
            reference=current["reference"],
            evaluated_at=current["observed_at"],
        ),
    )
    return LedgerFixture(request, claims, chain)
