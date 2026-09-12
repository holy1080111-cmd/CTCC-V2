"""Pin recorded original G1--G12 facts, never a resumable execution permit.

These functions do not publish, read files, sample clocks or authenticate a
receipt. Even a replayed origin is only computational evidence. A future trusted
one-shot runtime must itself call G12 before collecting new observations; loading
this record cannot establish that the current invocation crossed that barrier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import field_validator, model_validator

from app.domain.market import MarketSnapshot
from app.trade_evidence.gates import (
    EvidenceGateRun,
    _digest,
)
from app.trade_evidence.gates import (
    _copy as _copy_evidence,
)
from app.trade_evidence.gates import (
    _guard as _evidence_guard,
)
from app.trade_evidence.service import validate_snapshot
from app.trade_qualification.engine import verify_pre_evidence
from app.trade_qualification.event_models import Digest
from app.trade_qualification.models import QualificationModel, require_aware
from app.trade_qualification.service import _plain
from app.trade_qualification.timing import event_identity


def _original_pins(evidence: EvidenceGateRun) -> dict:
    pre = evidence.pre_evidence
    if (
        not pre.pre_evidence_complete
        or len(evidence.result.gates) != 12
        or not all(gate.passed for gate in evidence.result.gates)
        or evidence.receipt is None
        or evidence.receipt.status != "written"
        or evidence.snapshot is None
    ):
        raise ValueError("recheck_origin_requires_complete_recorded_g12")
    deadline = min(
        pre.prefix.intent.expires_at,
        pre.prefix.detection.trigger.expires_at,
        pre.result.entry_zone.expires_at,
        pre.prefix.timing.latest_valid_entry_time,
    )
    key = event_identity(pre.prefix.detection)
    if key is None or evidence.receipt.completed_at >= deadline:
        raise ValueError("recheck_origin_has_no_live_original_event")
    return {
        "evidence_sha256": evidence.evaluation_sha256,
        "pre_evidence_sha256": pre.evaluation_sha256,
        "original_source_sha256": pre.prefix.data_result.source_sha256,
        "original_policy_sha256": pre.policy_sha256,
        "original_event_key": key,
        "publication_completed_at": evidence.receipt.completed_at,
        "deadline": deadline,
    }


def _origin_guard(value):
    if type(value) is not RecheckOrigin:
        raise ValueError("exact_recheck_origin_required")
    if (
        set(value.__dict__) != set(RecheckOrigin.model_fields)
        or value.__pydantic_extra__
    ):
        raise ValueError("dirty_recheck_origin")
    for item in value.__dict__.values():
        _evidence_guard(item)


class RecheckOrigin(QualificationModel):
    """Immutable original facts; no old-record-to-runtime admission API."""

    evidence: EvidenceGateRun
    evidence_sha256: Digest
    pre_evidence_sha256: Digest
    original_source_sha256: Digest
    original_policy_sha256: Digest
    original_event_key: Digest
    publication_completed_at: datetime
    deadline: datetime
    record_kind: Literal["recorded_origin_not_runtime_permission"] = (
        "recorded_origin_not_runtime_permission"
    )
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    publication_observed_here: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    @field_validator("publication_completed_at", "deadline")
    @classmethod
    def exact_aware_time(cls, value):
        # Typed strict parsing retains JSON's datetime conversion without
        # accepting strings at a Python input boundary.
        if type(value) is not datetime:
            raise ValueError("exact_origin_datetime_required")
        return require_aware(value)

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "publication_observed_here",
        "atomic_risk_reserved",
        "execution_recheck_performed",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("recorded_origin_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent(self):
        _origin_guard(self)
        expected = _original_pins(self.evidence)
        if any(getattr(self, name) != value for name, value in expected.items()):
            raise ValueError("recheck_origin_changed_original_facts")
        return self

    @property
    def evaluation_sha256(self) -> str:
        return _digest(copy_recheck_origin(self))

    @property
    def candidate(self):
        """Original result; contains exactly twelve gates and stays unqualified."""
        return self.evidence.result


def copy_recheck_origin(origin: RecheckOrigin) -> RecheckOrigin:
    """Reject nested subclasses/hidden fields before their serializers run."""
    _origin_guard(origin)
    return RecheckOrigin.model_validate(_plain(origin), strict=True)


def freeze_recheck_origin(evidence: EvidenceGateRun) -> RecheckOrigin:
    """Pin a consistent recorded packet, without claiming current publication.

    No disk/readback/source authentication or G13 gate is performed here. A
    stored record can be pinned for offline diagnostics, but cannot resume an
    execution workflow. ``replay_recheck_origin`` additionally recomputes the
    original evaluators from original raw inputs.
    """
    checked = _copy_evidence(evidence, EvidenceGateRun)
    return RecheckOrigin.model_validate(
        _plain({"evidence": checked, **_original_pins(checked)}), strict=True
    )


def replay_recheck_origin(
    origin: RecheckOrigin, original_market: MarketSnapshot, **original_inputs
) -> RecheckOrigin:
    """Replay original G1--G11 and snapshot, not the historical disk operation."""
    checked = copy_recheck_origin(origin)
    verified = verify_pre_evidence(
        checked.evidence.pre_evidence, original_market, **original_inputs
    )
    if verified.evaluation_sha256 != checked.pre_evidence_sha256:
        raise ValueError("recheck_original_evaluation_mismatch")
    snapshot = validate_snapshot(checked.evidence.snapshot)
    if snapshot.qualification != verified.result:
        raise ValueError("recheck_original_snapshot_mismatch")
    return checked
