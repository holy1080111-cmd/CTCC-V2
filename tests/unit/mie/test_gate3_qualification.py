from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.mie.validation import (
    Gate3Claim,
    Gate3DatasetQualification,
    HoldoutAccessState,
)
from scripts.verify_mie_gate3_batch_qualification import (
    verify_qualification_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
QUALIFICATION_PATH = (
    PROJECT_ROOT
    / "docs"
    / "evidence"
    / "mie_gate3_binance_batch_qualification_v1.json"
)


def _payload() -> dict[str, object]:
    payload = QUALIFICATION_PATH.read_text(encoding="utf-8")
    return Gate3DatasetQualification.model_validate_json(payload).model_dump()


def test_committed_batch_qualification_is_fail_closed() -> None:
    qualification = verify_qualification_file(QUALIFICATION_PATH)

    assert qualification.completed_artifact_count == 180
    assert qualification.total_artifact_bytes == 11_146_413
    assert qualification.total_minute_rows == 259_200
    assert qualification.partition_summary_count == 6
    assert qualification.partition_overlap_count == 0
    assert (
        qualification.holdout_access_state
        == HoldoutAccessState.DESCRIPTIVE_SUMMARY_EXPOSED
    )
    assert qualification.candidate_design_predated_holdout_access is False
    assert qualification.predictive_oos_eligible is False
    assert qualification.current_claim == Gate3Claim.COMPUTATIONAL
    assert qualification.strategy_evaluated is False
    assert qualification.costs_evaluated is False
    assert qualification.runtime_consumers == 0
    assert qualification.execution_authority is False
    assert qualification.real_order_tested is False


def test_exposed_holdout_cannot_be_marked_predictive_eligible() -> None:
    payload = _payload()
    payload["predictive_oos_eligible"] = True
    payload["candidate_design_predated_holdout_access"] = True

    with pytest.raises(ValidationError, match="requires an unread holdout"):
        Gate3DatasetQualification.model_validate(payload)


def test_predictive_eligibility_requires_candidate_design_to_predate_access() -> None:
    payload = _payload()
    payload["holdout_access_state"] = HoldoutAccessState.UNREAD
    payload["predictive_oos_eligible"] = True

    with pytest.raises(ValidationError, match="pre-existing candidate design"):
        Gate3DatasetQualification.model_validate(payload)


def test_claim_cannot_exceed_dataset_eligibility() -> None:
    payload = _payload()
    payload["current_claim"] = Gate3Claim.PREDICTIVE_OOS

    with pytest.raises(ValidationError, match="exceeds dataset eligibility"):
        Gate3DatasetQualification.model_validate(payload)


def test_qualification_cannot_predate_batch_evidence() -> None:
    qualification = Gate3DatasetQualification.model_validate(_payload())
    payload = _payload()
    payload["recorded_at"] = qualification.evidence_generated_at - timedelta(
        microseconds=1
    )

    with pytest.raises(ValidationError, match="cannot predate"):
        Gate3DatasetQualification.model_validate(payload)


def test_qualification_has_no_order_or_sizing_geometry() -> None:
    forbidden = {
        "account_id",
        "order_id",
        "quantity",
        "contracts",
        "leverage",
        "margin",
        "exchange_payload",
        "write_authority",
    }
    assert forbidden.isdisjoint(Gate3DatasetQualification.model_fields)
