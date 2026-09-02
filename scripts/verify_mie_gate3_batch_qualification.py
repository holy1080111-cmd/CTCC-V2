from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from pydantic import BaseModel

from app.mie.validation import (
    Gate3Claim,
    Gate3DatasetQualification,
    HoldoutAccessState,
)
from app.research.external_benchmarks import (
    BATCH_EVIDENCE_PATH,
    BATCH_PLAN_PATH,
    BATCH_PREPARATION_PATH,
    BinanceBatchEvidence,
    BinanceBatchPlan,
    BinanceBatchPreparation,
    canonical_binance_batch_plan,
)
from app.research.external_benchmarks.evidence_io import read_contract_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUALIFICATION_PATH = (
    PROJECT_ROOT
    / "docs"
    / "evidence"
    / "mie_gate3_binance_batch_qualification_v1.json"
)
MAX_QUALIFICATION_BYTES = 64 * 1024


class Gate3BatchQualificationError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _real_file(path: Path, *, root: Path | None = None) -> Path:
    if path.is_symlink():
        raise Gate3BatchQualificationError("qualification input cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise Gate3BatchQualificationError(
            "qualification input does not exist"
        ) from exc
    if not resolved.is_file():
        raise Gate3BatchQualificationError(
            "qualification input must be a real file"
        )
    if root is not None and not resolved.is_relative_to(root):
        raise Gate3BatchQualificationError("dataset input escaped its root")
    return resolved


def _load_json[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
) -> ModelT:
    resolved = _real_file(path)
    if resolved.stat().st_size > MAX_QUALIFICATION_BYTES:
        raise Gate3BatchQualificationError("qualification input is too large")
    try:
        payload = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Gate3BatchQualificationError(
            "qualification input could not be read"
        ) from exc
    return model_type.model_validate_json(payload)


def verify_qualification_file(path: Path) -> Gate3DatasetQualification:
    qualification = _load_json(path, Gate3DatasetQualification)
    if (
        qualification.holdout_access_state
        != HoldoutAccessState.DESCRIPTIVE_SUMMARY_EXPOSED
        or qualification.candidate_design_predated_holdout_access
        or qualification.predictive_oos_eligible
        or qualification.current_claim != Gate3Claim.COMPUTATIONAL
        or qualification.strategy_evaluated
        or qualification.costs_evaluated
        or not qualification.reference_only
        or qualification.promotion_eligible
        or qualification.runtime_consumers != 0
        or qualification.execution_authority
        or qualification.real_order_tested
    ):
        raise Gate3BatchQualificationError(
            "committed qualification exceeds the accepted batch evidence"
        )
    return qualification


def _verify_external_dataset(
    qualification: Gate3DatasetQualification,
    dataset_root: Path,
) -> None:
    if dataset_root.is_symlink():
        raise Gate3BatchQualificationError("dataset root cannot be a symlink")
    try:
        root = dataset_root.resolve(strict=True)
    except OSError as exc:
        raise Gate3BatchQualificationError("dataset root does not exist") from exc
    if not root.is_dir():
        raise Gate3BatchQualificationError("dataset root must be a directory")

    plan_path = _real_file(root.joinpath(*BATCH_PLAN_PATH.split("/")), root=root)
    preparation_path = _real_file(
        root.joinpath(*BATCH_PREPARATION_PATH.split("/")),
        root=root,
    )
    evidence_path = _real_file(
        root.joinpath(*BATCH_EVIDENCE_PATH.split("/")),
        root=root,
    )
    plan = read_contract_json(root, BATCH_PLAN_PATH, BinanceBatchPlan)
    preparation = read_contract_json(
        root,
        BATCH_PREPARATION_PATH,
        BinanceBatchPreparation,
    )
    evidence = read_contract_json(root, BATCH_EVIDENCE_PATH, BinanceBatchEvidence)
    expected_plan = canonical_binance_batch_plan()

    checks = (
        qualification.dataset_plan_id == expected_plan.plan_id == plan.plan_id,
        qualification.plan_contract_sha256
        == expected_plan.canonical_sha256()
        == plan.canonical_sha256(),
        preparation.plan_sha256 == expected_plan.canonical_sha256(),
        evidence.plan_sha256 == expected_plan.canonical_sha256(),
        qualification.plan_file_sha256 == _sha256_file(plan_path),
        qualification.preparation_contract_sha256
        == preparation.canonical_sha256()
        == evidence.preparation_sha256,
        qualification.preparation_file_sha256 == _sha256_file(preparation_path),
        qualification.evidence_file_sha256 == _sha256_file(evidence_path),
        qualification.evidence_generated_at == evidence.generated_at,
        qualification.completed_artifact_count
        == evidence.completed_artifact_count
        == evidence.expected_artifact_count
        == expected_plan.expected_artifact_count,
        qualification.total_artifact_bytes == preparation.total_expected_bytes,
        qualification.total_minute_rows == evidence.total_minute_rows,
        qualification.partition_summary_count == len(evidence.partition_summaries),
        qualification.partition_overlap_count == evidence.partition_overlap_count,
        qualification.holdout_semantics == evidence.holdout_semantics,
        evidence.passed,
        not evidence.strategy_evaluated,
        not evidence.costs_evaluated,
        evidence.reference_only,
        not evidence.promotion_eligible,
        evidence.runtime_consumers == 0,
        not evidence.execution_authority,
    )
    if not all(checks):
        raise Gate3BatchQualificationError(
            "external batch evidence disagrees with the committed qualification"
        )

    preparation_by_id = {entry.request_id: entry for entry in preparation.entries}
    evidence_by_id = {entry.request_id: entry for entry in evidence.entries}
    if (
        len(preparation_by_id) != expected_plan.expected_artifact_count
        or len(evidence_by_id) != expected_plan.expected_artifact_count
    ):
        raise Gate3BatchQualificationError("external batch entries are incomplete")

    total_bytes = 0
    for _, coordinates in expected_plan.coordinate_items():
        try:
            prepared = preparation_by_id[coordinates.request_id]
            completed = evidence_by_id[coordinates.request_id]
        except KeyError as exc:
            raise Gate3BatchQualificationError(
                "external batch is missing a frozen coordinate"
            ) from exc
        artifact = _real_file(
            root.joinpath(*coordinates.relative_path.split("/")),
            root=root,
        )
        if (
            artifact.stat().st_size != prepared.artifact_byte_size
            or _sha256_file(artifact) != prepared.artifact_sha256
            or completed.artifact_sha256 != prepared.artifact_sha256
        ):
            raise Gate3BatchQualificationError(
                "external batch artifact identity changed"
            )
        total_bytes += artifact.stat().st_size
    if total_bytes != qualification.total_artifact_bytes:
        raise Gate3BatchQualificationError("external batch byte total changed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the fail-closed Gate 3 batch qualification receipt"
    )
    parser.add_argument(
        "--qualification",
        type=Path,
        default=DEFAULT_QUALIFICATION_PATH,
    )
    parser.add_argument("--dataset-root", type=Path)
    args = parser.parse_args()

    qualification = verify_qualification_file(args.qualification)
    if args.dataset_root is not None:
        _verify_external_dataset(qualification, args.dataset_root)
        print("MIE_GATE3_BATCH_DATASET_BINDING_VERIFIED=1")
    print(
        "MIE_GATE3_BATCH_QUALIFICATION_SHA256="
        f"{qualification.canonical_sha256()}"
    )
    print("MIE_GATE3_BATCH_QUALIFICATION_VERIFIED=1")
    print("MIE_GATE3_BATCH_CURRENT_CLAIM=computational")
    print("MIE_GATE3_BATCH_HOLDOUT_ACCESS=descriptive_summary_exposed")
    print("MIE_GATE3_BATCH_PREDICTIVE_OOS_ELIGIBLE=0")
    print("MIE_GATE3_BATCH_RUNTIME_CONSUMERS=0")
    print("MIE_GATE3_BATCH_EXECUTION_AUTHORITY=0")
    print("MIE_GATE3_BATCH_REAL_ORDER_TESTED=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
