"""Synthetic V6 G12 cannot enter a legacy risk hold or submission intent."""

import pytest

from app.trade_qualification import reservations, submission_intent
from tests.unit import qualification_sweep_v6_stage_c_fixtures as fixtures

stage_c_case = fixtures.stage_c_case


def test_v6_g12_cannot_inherit_legacy_reservation_admission(stage_c_case, monkeypatch):
    case = stage_c_case
    assert case.evidence.result.evidence_complete
    request = reservations.ReservationRequest(
        scope=reservations.LedgerScope(
            account_id="123456789", settlement_currency="USDT"
        ),
        origin=case.origin,
        quote=case.source.quote.quote,
        risk_inputs=case.inputs["risk_inputs"],
        expected_account_revision=1,
        expected_ledger_revision=1,
    )
    monkeypatch.setattr(
        reservations,
        "validate_claims",
        lambda *_args, **_kwargs: pytest.fail(
            "account claims reached before V6 denial"
        ),
    )
    with pytest.raises(
        reservations.QualificationLedgerError,
        match="^ledger_history_version_unsupported$",
    ):
        reservations.prepare_reservation(
            request, object(), (), observed_at=case.origin.publication_completed_at
        )

    # The intent guard already excludes V6 from its exact model allowlist.
    with pytest.raises(
        reservations.QualificationLedgerError,
        match="^submit_intent_input_invalid$",
    ):
        submission_intent.build_submission_intent(request, object())
