"""New G12 publication with a separate, non-authorizing computational proof.

The proof pins the *complete* in-memory EvidenceGateRun from this invocation.
Independent readback needs the original source inputs to replay G1--G11; a
digest or the six rendered files alone cannot reconstruct that calculation.
Neither this caller-origin route nor its proof authenticates source custody or
grants execution permission. The publish entrypoint rejects old G12 runs;
standalone readback proves computation and file consistency only, because a
caller-controlled sidecar cannot attest when it was first published.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PosixPath, WindowsPath
from typing import Literal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.trade_evidence import gates, storage
from app.trade_evidence.service import prepare_evidence, validate_snapshot
from app.trade_qualification.one_shot import _original
from app.trade_qualification.quote_collector import validate_collected_quote
from app.trade_qualification.service import _plain

_SCHEMA = "ctcc.g12_computational_proof.v1"
_MAX_PROOF_BYTES = 64 * 1024
_PROOF_NAME = "proof.json"
_REASON = (
    "The source-bound packet was published and fully read back; "
    "no execution recheck occurred."
)


class G12ComputationalProofError(ValueError):
    """A fixed denial; never expose a path, source row or account field."""


@dataclass(frozen=True, slots=True)
class G12ComputationalProofDiagnosticV1:
    proof_sha256: str
    report_id: str
    evidence: gates.EvidenceGateRun = field(repr=False)
    code: Literal["g12_computation_replayed_not_source_authority"] = (
        "g12_computation_replayed_not_source_authority"
    )
    admission: Literal["DENY"] = "DENY"
    source_authenticity_verified: Literal[False] = False
    publication_provenance_verified: Literal[False] = False
    execution_authority: Literal[False] = False


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: dict) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if not 0 < len(raw) <= _MAX_PROOF_BYTES:
        raise G12ComputationalProofError("g12_proof_invalid")
    return raw


def _decode(raw: bytes) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    if type(value) is not dict or _canonical(value) != raw:
        raise ValueError
    return value


def _root(root: Path) -> Path:
    if type(root) not in (PosixPath, WindowsPath):
        raise G12ComputationalProofError("g12_proof_root_invalid")
    return storage._root_path(root)


def _context(root: Path):
    return storage._windows_root(root) if os.name == "nt" else storage._posix_root(root)


def _proof_document(evidence: gates.EvidenceGateRun, render_profile: str) -> dict:
    receipt = evidence.receipt
    if (
        receipt is None
        or receipt.status != "written"
        or not evidence.pre_evidence.pre_evidence_complete
        or evidence.result.gates[-1].code != "passed"
        or not evidence.result.gates[-1].passed
        or evidence.error_detail is not None
    ):
        raise G12ComputationalProofError("g12_new_pass_required")
    return {
        "schema_version": _SCHEMA,
        "report_id": evidence.result.report_id,
        "pre_evidence_sha256": evidence.pre_evidence_sha256,
        "evaluation_sha256": evidence.evaluation_sha256,
        "source_sha256": evidence.pre_evidence.prefix.data_result.source_sha256,
        "snapshot_sha256": evidence.snapshot_sha256,
        "report_sha256": receipt.report_sha256,
        "file_sha256": {item.name: item.sha256 for item in receipt.files},
        "publication_started_at": evidence.publication_started_at.isoformat(),
        "publication_completed_at": receipt.completed_at.isoformat(),
        "render_profile": render_profile,
        "admission": "DENY",
        "source_authenticity_verified": False,
        "publication_provenance_verified": False,
        "execution_authority": False,
    }


def _replay_expected_packet(evidence, market, inputs, render_profile):
    market, pre, inputs = _original(market, evidence.pre_evidence, inputs)
    if pre != evidence.pre_evidence or not pre.pre_evidence_complete:
        raise ValueError
    source = json.loads(pre.prefix.data_result.source_json)
    rebuilt = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    quote = validate_collected_quote(inputs["quote"])
    if quote.bundle_sha256 != pre.prefix.data_result.quote_bundle_sha256:
        raise ValueError
    selector = (
        gates.select_sweep_structural_protection
        if type(pre) is gates.HistoryPreEvidenceRunV6
        else gates.select_range_structural_protection
        if type(pre) is gates.HistoryPreEvidenceRunV5
        else gates.select_reversal_structural_protection
        if type(pre) is gates.HistoryPreEvidenceRunV3
        else gates.select_structural_protection
    )
    selection = selector(
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
        raise ValueError
    if evidence.snapshot is None or evidence.preparation_started_at is None:
        raise ValueError
    snapshot = validate_snapshot(
        prepare_evidence(
            rebuilt,
            analysis,
            qualification=pre.result,
            detection=pre.prefix.detection,
            quote=quote.quote,
            protection=selection,
            prepared_at=evidence.preparation_started_at,
            purpose=evidence.snapshot.purpose,
            candle_limit=evidence.snapshot.candle_limit,
            zone_tick_size=pre.policy.prefix.tick_size,
        )
    )
    if (
        snapshot != evidence.snapshot
        or gates._digest(snapshot) != evidence.snapshot_sha256
    ):
        raise ValueError
    return dict(
        gates.render_evidence_liquidity_v2(snapshot)
        if render_profile == "liquidity_v2"
        else gates.render_evidence(snapshot)
    )


def _replay_g12_result(evidence):
    receipt = evidence.receipt
    if receipt is None or evidence.snapshot is None:
        raise ValueError
    expected = gates.GateAssessment(
        report_id=evidence.result.report_id,
        gate=gates.QualificationGate.EVIDENCE,
        passed=True,
        code="passed",
        reason=_REASON,
        measured_values={
            "pre_evidence_sha256": evidence.pre_evidence_sha256,
            "snapshot_sha256": gates._digest(evidence.snapshot),
            "report_sha256": receipt.report_sha256,
            "published_file_count": len(receipt.files),
            "publication_completed_at": receipt.completed_at.isoformat(),
            "entry_expired": False,
            "execution_recheck_performed": False,
        },
    )
    if evidence.result.gates[-1] != expected:
        raise ValueError


def read_g12_computational_proof_v1(
    proof_root: Path,
    evidence_root: Path,
    *,
    expected_proof_sha256: str,
    evidence: gates.EvidenceGateRun,
    original_market: MarketSnapshot,
    original_inputs: dict,
) -> G12ComputationalProofDiagnosticV1:
    """Replay computation and bytes; caller-owned sidecar cannot prove freshness."""
    try:
        proof_root, evidence_root = _root(proof_root), _root(evidence_root)
        if (
            proof_root == evidence_root
            or os.path.samefile(proof_root, evidence_root)
            or type(expected_proof_sha256) is not str
        ):
            raise ValueError
        evidence = gates._copy(evidence, type(evidence))
        if type(original_inputs) is not dict or set(original_inputs) != {
            "intent",
            "quote",
            "reference",
            "policy",
            "risk_inputs",
            "consumed_event_keys",
            "evaluated_at",
        }:
            raise ValueError
        with _context(proof_root) as directory:
            if set(directory.names()) != {_PROOF_NAME}:
                raise ValueError
            raw = directory.read(_PROOF_NAME, _MAX_PROOF_BYTES)
        observed = _decode(raw)
        if (
            _sha(raw) != expected_proof_sha256
            or observed.get("render_profile") not in {"v1", "liquidity_v2"}
            or observed != _proof_document(evidence, observed["render_profile"])
        ):
            raise ValueError
        replayed = _replay_expected_packet(
            evidence, original_market, original_inputs, observed["render_profile"]
        )
        _replay_g12_result(evidence)
        receipt = evidence.receipt
        if (
            receipt.report_directory != str(evidence_root / evidence.result.report_id)
            or tuple(item.name for item in receipt.files) != storage.FILE_NAMES
            or evidence.rendered_files != receipt.files
            or set(replayed) != set(storage.FILE_NAMES)
        ):
            raise ValueError
        with (
            _context(evidence_root) as directory,
            directory.child(evidence.result.report_id) as packet_directory,
        ):
            if set(packet_directory.names()) != set(storage.FILE_NAMES):
                raise ValueError
            packet = {
                name: packet_directory.read(
                    name,
                    storage.MAX_REPORT_BYTES
                    if name == "report.json"
                    else storage.MAX_PNG_BYTES,
                )
                for name in storage.FILE_NAMES
            }
        storage._packet(evidence.result.report_id, packet)
        if any(packet[name] != replayed[name] for name in storage.FILE_NAMES):
            raise ValueError
        if any(_sha(packet[item.name]) != item.sha256 for item in receipt.files):
            raise ValueError
        with _context(proof_root) as directory:
            if (
                set(directory.names()) != {_PROOF_NAME}
                or directory.read(_PROOF_NAME, _MAX_PROOF_BYTES) != raw
            ):
                raise ValueError
        return G12ComputationalProofDiagnosticV1(
            _sha(raw), evidence.result.report_id, evidence
        )
    except Exception:  # noqa: BLE001 -- deny without leaking source/path details
        raise G12ComputationalProofError("g12_proof_readback_denied") from None


def publish_g12_computational_proof_v1(
    evidence_root: Path,
    proof_root: Path,
    original_market: MarketSnapshot,
    *,
    run,
    original_inputs: dict,
    purpose: Literal["synthetic_test", "observed"],
    clock: Callable[[], datetime] = storage.actual_utc,
    render_profile: Literal["v1", "liquidity_v2"] = "v1",
) -> G12ComputationalProofDiagnosticV1:
    """Publish only for synthetic tests; observed lacks DB0039 custody."""
    if type(purpose) is not str or purpose != "synthetic_test":
        code = (
            "g12_proof_observed_custody_unavailable"
            if type(purpose) is str and purpose == "observed"
            else "g12_proof_purpose_denied"
        )
        raise G12ComputationalProofError(code)
    try:
        evidence_root, proof_root = _root(evidence_root), _root(proof_root)
        if (
            evidence_root == proof_root
            or os.path.samefile(evidence_root, proof_root)
            or render_profile not in {"v1", "liquidity_v2"}
        ):
            raise ValueError
        with _context(proof_root) as directory:
            if directory.names():
                raise ValueError
        market, pre, inputs = _original(original_market, run, original_inputs)
        evidence = gates.publish_qualification_evidence(
            evidence_root,
            market,
            run=pre,
            purpose=purpose,
            clock=clock,
            render_profile=render_profile,
            **inputs,
        )
        raw = _canonical(_proof_document(evidence, render_profile))
        with _context(proof_root) as directory:
            if directory.names():
                raise ValueError
            directory.publish(_PROOF_NAME, raw)
            if directory.read(_PROOF_NAME, _MAX_PROOF_BYTES) != raw:
                raise ValueError
        return read_g12_computational_proof_v1(
            proof_root,
            evidence_root,
            expected_proof_sha256=_sha(raw),
            evidence=evidence,
            original_market=market,
            original_inputs=inputs,
        )
    except G12ComputationalProofError:
        raise
    except Exception:  # noqa: BLE001 -- any G12 or late proof failure denies
        raise G12ComputationalProofError("g12_proof_publish_denied") from None
