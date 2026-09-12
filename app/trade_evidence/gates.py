"""One-shot G12 publication from replayed G1--G11, never execution authority.

The fixed renderer and publisher are trusted local dependencies, not caller
callbacks. A caller-created result or receipt is not an authorization token;
there is deliberately no old-receipt-to-PASS or retry API. The report retains
its pre-G12 snapshot, avoiding a circular report/G12 hash. Publication errors
may leave partial files; they are neither overwritten nor silently repaired.

Clocks are trusted runtime observations, checked for causal order, not clock
authentication. Expiry during rendering/publication fails G12 while preserving
an actual returned receipt. Even twelve passing gates cannot trade: fresh
post-publication recheck, authenticated state and atomic reservation are absent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.structural_protection import select_structural_protection
from app.trade_evidence.models import (
    EvidenceCandle,
    EvidenceLevel,
    EvidencePanel,
    EvidenceSnapshot,
    Purpose,
)
from app.trade_evidence.renderer import render_evidence
from app.trade_evidence.service import prepare_evidence, validate_snapshot
from app.trade_evidence.storage import (
    FILE_NAMES,
    PublicationReceipt,
    PublishedFile,
    actual_utc,
    publish_evidence,
)
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.engine import (
    PortfolioInputs,
    PreEvidencePolicy,
    PreEvidenceRun,
    _preflight,
    verify_pre_evidence,
)
from app.trade_qualification.event_models import Digest
from app.trade_qualification.location import ExecutableQuote
from app.trade_qualification.models import (
    EntryQualificationResult,
    GateAssessment,
    QualificationGate,
    QualificationModel,
    require_aware,
)
from app.trade_qualification.quote_collector import (
    CollectedQuote,
    validate_collected_quote,
)
from app.trade_qualification.service import QualificationIntent, _plain

_FAILURES = (
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    ArithmeticError,
    RecursionError,
    RuntimeError,
    OSError,
)


class EvidenceGateError(ValueError):
    """An input cannot identify a safely replayable G12 invocation."""


def _error_detail(error: Exception) -> str:
    """Keep bounded lower-level IO/clock causes without expanding Gate text."""
    parts, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(parts) < 8:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}"[:1024])
        current = current.__cause__
    return " <- ".join(parts)[:4096]


def _digest(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", round_trip=True)
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > 32 * 1024 * 1024:
        raise EvidenceGateError("evidence_gate_record_too_large")
    return hashlib.sha256(raw).hexdigest()


def _guard(value, depth=0, budget=None):
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise EvidenceGateError("evidence_gate_traversal_limit")
    if isinstance(value, BaseModel):
        if type(value) not in {
            EvidenceGateRun,
            EvidenceSnapshot,
            EvidencePanel,
            EvidenceCandle,
            EvidenceLevel,
            ExecutableQuote,
            PublicationReceipt,
            PublishedFile,
        }:
            _preflight(value)
            return
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise EvidenceGateError("evidence_gate_dirty_model")
        for item in value.__dict__.values():
            _guard(item, depth + 1, budget)
    elif type(value) in (dict, MappingProxyType):
        if len(value) > 64:
            raise EvidenceGateError("evidence_gate_mapping_limit")
        for key, item in value.items():
            _guard(key, depth + 1, budget)
            _guard(item, depth + 1, budget)
    elif type(value) in (tuple, list):
        if len(value) > 2048:
            raise EvidenceGateError("evidence_gate_sequence_limit")
        for item in value:
            _guard(item, depth + 1, budget)
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 256
            or abs(value.as_tuple().exponent) > 256
        ):
            raise EvidenceGateError("evidence_gate_decimal_limit")
    elif type(value) is str:
        if len(value) > 8 * 1024 * 1024:
            raise EvidenceGateError("evidence_gate_text_limit")
    elif type(value) is datetime:
        require_aware(value)
    elif isinstance(value, Enum):
        _guard(value.value, depth + 1, budget)
    elif (
        value is None
        or type(value) is bool
        or (type(value) is int and abs(value) <= 10**40)
    ):
        pass
    else:
        raise EvidenceGateError("evidence_gate_unknown_type")


def _copy(value, expected):
    if type(value) is not expected:
        raise EvidenceGateError("evidence_gate_exact_type_required")
    _guard(value)
    return expected.model_validate(_plain(value), strict=True)


class EvidenceGateRun(QualificationModel):
    """Computational audit of this invocation, not a reusable receipt permit."""

    pre_evidence: PreEvidenceRun
    pre_evidence_sha256: Digest
    result: EntryQualificationResult
    snapshot: EvidenceSnapshot | None = None
    snapshot_sha256: Digest | None = None
    rendered_files: tuple[PublishedFile, ...] = Field(default=(), max_length=6)
    preparation_started_at: datetime | None = None
    publication_started_at: datetime | None = None
    receipt: PublicationReceipt | None = None
    error_detail: str | None = Field(default=None, max_length=4096)
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    @field_validator("preparation_started_at", "publication_started_at")
    @classmethod
    def optional_utc(cls, value):
        return require_aware(value) if value is not None else None

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "atomic_risk_reserved",
        "execution_recheck_performed",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("evidence_gate_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent(self):
        _guard(self)
        pre = self.pre_evidence
        if self.pre_evidence_sha256 != pre.evaluation_sha256:
            raise ValueError("evidence_pre_run_pin_mismatch")
        if not pre.pre_evidence_complete:
            if (
                self.result != pre.result
                or any(
                    value is not None
                    for value in (
                        self.snapshot,
                        self.snapshot_sha256,
                        self.receipt,
                        self.preparation_started_at,
                        self.publication_started_at,
                    )
                )
                or self.rendered_files
            ):
                raise ValueError("evidence_continued_after_failed_prefix")
            return self
        if len(self.result.gates) != 12 or self.result.gates[:11] != pre.result.gates:
            raise ValueError("evidence_requires_unchanged_eleven_gate_prefix")
        before, after = _plain(pre.result), _plain(self.result)
        before.pop("gates")
        after.pop("gates")
        if before != after:
            raise ValueError("evidence_changed_original_candidate")
        if (self.snapshot is None) != (self.snapshot_sha256 is None):
            raise ValueError("evidence_snapshot_pin_missing")
        if (
            self.preparation_started_at is not None
            and self.preparation_started_at < pre.result.evaluated_at
        ):
            raise ValueError("evidence_preparation_before_evaluation")
        if self.snapshot is not None and (
            self.snapshot.qualification != pre.result
            or self.snapshot.source_sha256 != pre.prefix.data_result.source_sha256
            or self.snapshot.instrument_id != pre.prefix.intent.instrument_id
            or self.snapshot.prepared_at != self.preparation_started_at
            or _digest(self.snapshot) != self.snapshot_sha256
        ):
            raise ValueError("evidence_snapshot_mismatch")
        if self.rendered_files and (
            self.snapshot is None
            or tuple(f.name for f in self.rendered_files) != FILE_NAMES
        ):
            raise ValueError("evidence_render_manifest_invalid")
        if self.publication_started_at is not None and (
            self.preparation_started_at is None
            or self.publication_started_at < self.preparation_started_at
        ):
            raise ValueError("evidence_publication_before_preparation")
        if self.receipt is not None and (
            self.snapshot is None
            or self.publication_started_at is None
            or self.receipt.completed_at < self.publication_started_at
            or self.receipt.report_id != pre.result.report_id
            or self.receipt.files != self.rendered_files
        ):
            raise ValueError("evidence_receipt_mismatch")
        gate = self.result.gates[-1]
        if gate.passed and (
            self.receipt is None
            or self.receipt.status != "written"
            or self.error_detail is not None
            or self.receipt.completed_at >= _expiry(pre)
        ):
            raise ValueError("evidence_pass_requires_fresh_one_shot_publication")
        return self

    @property
    def evaluation_sha256(self) -> str:
        return _digest(_copy(self, EvidenceGateRun))


def _expiry(run: PreEvidenceRun) -> datetime:
    return min(
        run.prefix.intent.expires_at,
        run.prefix.detection.trigger.expires_at,
        run.result.entry_zone.expires_at,
        run.prefix.timing.latest_valid_entry_time,
    )


def publish_qualification_evidence(
    root: Path,
    market: MarketSnapshot,
    *,
    run: PreEvidenceRun,
    intent: QualificationIntent,
    quote: CollectedQuote,
    reference: WSReferenceObservation,
    policy: PreEvidencePolicy,
    risk_inputs: PortfolioInputs,
    consumed_event_keys: frozenset[str],
    evaluated_at: datetime,
    purpose: Purpose,
    candle_limit: int = 80,
    clock: Callable[[], datetime] = actual_utc,
) -> EvidenceGateRun:
    """Recompute, prepare, render, publish/read back, then append G12 once.

    Failed earlier gates never reach the clock, preparation or filesystem. A
    post-write expiry retains the actual receipt but fails G12. Existing files,
    including an identical packet, cannot pass this one-shot API. No G13 fetch,
    clock adjustment, source authentication or execution takes place.
    """
    if (
        type(purpose) is not str
        or purpose not in {"synthetic_test", "observed"}
        or type(candle_limit) is not int
        or not 80 <= candle_limit <= 200
        or not callable(clock)
    ):
        raise EvidenceGateError("evidence_preparation_policy_invalid")
    try:
        pre = verify_pre_evidence(
            run,
            market,
            intent=intent,
            quote=quote,
            reference=reference,
            policy=policy,
            risk_inputs=risk_inputs,
            consumed_event_keys=consumed_event_keys,
            evaluated_at=evaluated_at,
        )
    except Exception as exc:
        raise EvidenceGateError("pre_evidence_replay_failed") from exc
    pin = pre.evaluation_sha256
    snapshot = receipt = prepared_at = publication_started_at = None
    rendered_files = ()

    def finish(code=None, detail=None):
        result = pre.result
        if code is not None:
            values = _plain(result)
            values["gates"] = (
                *values["gates"],
                _plain(
                    GateAssessment(
                        report_id=result.report_id,
                        gate=QualificationGate.EVIDENCE,
                        passed=code == "passed",
                        code=code,
                        reason=(
                            "The source-bound packet was published and fully read back; no execution recheck occurred."
                            if code == "passed"
                            else "Evidence qualification failed; any recorded publication does not authorize entry."
                        ),
                        measured_values={
                            "pre_evidence_sha256": pin,
                            "snapshot_sha256": _digest(snapshot) if snapshot else None,
                            "report_sha256": receipt.report_sha256 if receipt else None,
                            "published_file_count": len(receipt.files)
                            if receipt
                            else None,
                            "publication_completed_at": receipt.completed_at.isoformat()
                            if receipt
                            else None,
                            "entry_expired": code == "evidence_entry_expired",
                            "execution_recheck_performed": False,
                        },
                    )
                ),
            )
            result = EntryQualificationResult.model_validate(values, strict=True)
        return EvidenceGateRun.model_validate(
            _plain(
                {
                    "pre_evidence": pre,
                    "pre_evidence_sha256": pin,
                    "result": result,
                    "snapshot": snapshot,
                    "snapshot_sha256": _digest(snapshot) if snapshot else None,
                    "rendered_files": rendered_files,
                    "preparation_started_at": prepared_at,
                    "publication_started_at": publication_started_at,
                    "receipt": receipt,
                    "error_detail": detail,
                }
            ),
            strict=True,
        )

    if not pre.pre_evidence_complete:
        return finish()
    previous_time = pre.result.evaluated_at
    publication_times = []

    def sample_clock():
        nonlocal previous_time
        try:
            value = clock()
            if type(value) is not datetime:
                raise ValueError("exact datetime required")
            value = require_aware(value)
            if value < previous_time:
                raise ValueError("clock moved backwards")
        except Exception as exc:
            raise EvidenceGateError("evidence_clock_invalid") from exc
        previous_time = value
        return value

    def publication_clock():
        nonlocal publication_started_at
        value = sample_clock()
        publication_times.append(value)
        if len(publication_times) == 1:
            publication_started_at = value
            # This callback runs before the publisher opens any directory.
            # Rendering may have consumed the remaining entry lifetime.
            if value >= _expiry(pre):
                raise EvidenceGateError("evidence_entry_expired")
        return value

    try:
        prepared_at = sample_clock()
        if prepared_at >= _expiry(pre):
            return finish("evidence_entry_expired")
        source = json.loads(pre.prefix.data_result.source_json)
        rebuilt = MarketSnapshot.model_validate_json(
            json.dumps(source["market"]), strict=True
        )
        analysis = MultiTimeframeAnalysis.model_validate_json(
            json.dumps(source["analysis"]), strict=True
        )
        collected = validate_collected_quote(quote)
        if collected.bundle_sha256 != pre.prefix.data_result.quote_bundle_sha256:
            raise EvidenceGateError("evidence_quote_changed")
        selection = select_structural_protection(
            pre.prefix.detection,
            rebuilt,
            analysis,
            observed_at=pre.result.evaluated_at,
            entry=pre.result.candidate_entry,
            tick_size=pre.policy.prefix.tick_size,
            **{
                key: value
                for key, value in _plain(pre.policy.protection).items()
                if key != "policy_id"
            },
        )
        if selection.to_audit_json() != pre.protection_audit_json:
            raise EvidenceGateError("evidence_structural_audit_mismatch")
        snapshot = prepare_evidence(
            rebuilt,
            analysis,
            qualification=pre.result,
            detection=pre.prefix.detection,
            quote=collected.quote,
            protection=selection,
            prepared_at=prepared_at,
            purpose=purpose,
            candle_limit=candle_limit,
            zone_tick_size=pre.policy.prefix.tick_size,
        )
        snapshot = validate_snapshot(snapshot)
    except _FAILURES as exc:
        code = (
            "evidence_clock_invalid"
            if str(exc) == "evidence_clock_invalid"
            else "evidence_preparation_failed"
        )
        return finish(code, _error_detail(exc))
    try:
        packet = dict(render_evidence(snapshot))
        if set(packet) != set(FILE_NAMES) or any(
            type(item) is not bytes or not 0 < len(item) <= 8 * 1024 * 1024
            for item in packet.values()
        ):
            raise EvidenceGateError("evidence_render_packet_invalid")
        report = json.loads(packet["report.json"])
        if report.get("snapshot") != snapshot.model_dump(mode="json", round_trip=True):
            raise EvidenceGateError("evidence_render_snapshot_mismatch")
        rendered_files = tuple(
            PublishedFile(
                name=name,
                sha256=hashlib.sha256(packet[name]).hexdigest(),
                size_bytes=len(packet[name]),
            )
            for name in FILE_NAMES
        )
    except _FAILURES as exc:
        return finish("evidence_render_failed", _error_detail(exc))
    publication_returned = False
    try:
        returned = publish_evidence(
            root, report_id=pre.result.report_id, files=packet, clock=publication_clock
        )
        publication_returned = True
        checked = _copy(returned, PublicationReceipt)
        if (
            len(publication_times) != 2
            or checked.completed_at != publication_times[-1]
            or checked.files != rendered_files
            or checked.report_id != pre.result.report_id
            or checked.report_directory != str(root / pre.result.report_id)
        ):
            raise EvidenceGateError("evidence_publication_invalid")
        receipt = checked
    except _FAILURES as exc:
        clock_code = (
            str(exc)
            if isinstance(exc, EvidenceGateError)
            and str(exc) in {"evidence_clock_invalid", "evidence_entry_expired"}
            else None
        )
        # The publisher wraps clock exceptions; preserve their causal identity.
        cause = exc.__cause__
        seen = {id(exc)}
        while cause is not None and clock_code is None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, EvidenceGateError) and str(cause) in {
                "evidence_clock_invalid",
                "evidence_entry_expired",
            }:
                clock_code = str(cause)
            cause = cause.__cause__
        code = (
            clock_code
            if clock_code is not None
            else "evidence_publication_invalid"
            if publication_returned or isinstance(exc, EvidenceGateError)
            else "evidence_publication_failed"
        )
        return finish(code, _error_detail(exc))
    if receipt.status != "written":
        return finish("evidence_already_published")
    if receipt.completed_at >= _expiry(pre):
        return finish("evidence_entry_expired")
    return finish("passed")
