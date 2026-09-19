"""Offline real-archive rehearsal; never opens holdout archives or fits a candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from app.mie.validation.archive_batch import (
    ArchiveBatchPlan,
    ArchiveBatchSource,
    load_archive_batch_rehearsal,
)
from app.mie.validation.availability import ArchiveObservationReceipt
from app.mie.validation.batch_replay import (
    aggregate_point_in_time_minutes,
    archive_batch_minutes,
    minute_source_sha256,
)
from app.mie.validation.contracts import (
    DatasetPartition,
    PartitionWindow,
    PurgedWalkForwardSplit,
)
from app.mie.validation.replay import ReplayValidationError, replay_features_at
from app.research.external_benchmarks import (
    BATCH_PLAN_PATH,
    BATCH_PREPARATION_PATH,
    BinanceBatchPlan,
    BinanceBatchPreparation,
    canonical_binance_batch_plan,
)
from app.research.external_benchmarks.binance_batch_contracts import (
    BinanceBatchKlineCoordinates,
)
from app.research.external_benchmarks.contracts import (
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
)
from app.research.external_benchmarks.evidence_io import (
    evidence_path,
    read_contract_json,
)
from scripts.verify_mie_gate3_batch_qualification import (
    DEFAULT_QUALIFICATION_PATH,
    verify_qualification_file,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish(root: Path, name: str, data: bytes) -> str:
    target = evidence_path(root, name)
    with target.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if target.read_bytes() != data:
        raise ValueError("rehearsal artifact readback mismatch")
    return digest(data)


def new_plan(public_plan: BinanceBatchPlan) -> ArchiveBatchPlan:
    windows = tuple(
        PartitionWindow(
            partition=DatasetPartition(window.partition.value),
            start_at=datetime.combine(window.start_day, time(), tzinfo=UTC),
            end_at=datetime.combine(
                window.end_day + timedelta(days=1), time(), tzinfo=UTC
            ),
        )
        for window in public_plan.windows
    )
    count = sum((item.end_at - item.start_at).days for item in windows[:2]) * len(
        public_plan.symbols
    )
    dependency = 256 * 14400
    return ArchiveBatchPlan(
        plan_id="gate3:real:development:validation:aggregation:v1",
        # This is a new computational replay plan, never a historical preregistration.
        created_at=datetime.now(UTC),
        split=PurgedWalkForwardSplit(
            development=windows[0],
            validation=windows[1],
            holdout=windows[2],
            purge_seconds=dependency,
            embargo_seconds=dependency,
            max_feature_dependency_seconds=dependency,
            label_dependency_seconds=14400,
        ),
        symbols=public_plan.symbols,
        expected_artifact_count=count,
        expected_rows=count * 1440,
    )


def run(
    root: Path, output: Path, plan_path: Path | None, expected_plan: str | None
) -> dict:
    if root.is_symlink():
        raise ValueError("source dataset root cannot be a symlink")
    root = root.resolve(strict=True)
    if output.exists() or output.resolve().is_relative_to(root):
        raise ValueError("rehearsal output must be new and outside the source dataset")
    qualification = verify_qualification_file(DEFAULT_QUALIFICATION_PATH)
    public_plan = read_contract_json(root, BATCH_PLAN_PATH, BinanceBatchPlan)
    preparation = read_contract_json(
        root, BATCH_PREPARATION_PATH, BinanceBatchPreparation
    )
    if (
        public_plan.canonical_sha256()
        != canonical_binance_batch_plan().canonical_sha256()
        or public_plan.canonical_sha256() != qualification.plan_contract_sha256
        or preparation.canonical_sha256() != qualification.preparation_contract_sha256
        or preparation.plan_sha256 != public_plan.canonical_sha256()
    ):
        raise ValueError("original public batch identity changed")
    if plan_path is not None and (
        plan_path.is_symlink() or plan_path.stat().st_size > 64 * 1024
    ):
        raise ValueError("retained replay plan must be a bounded regular source")
    plan = (
        new_plan(public_plan)
        if plan_path is None
        else ArchiveBatchPlan.model_validate_json(plan_path.read_bytes())
    )
    if plan_path is not None and plan.canonical_sha256() != expected_plan:
        raise ValueError("retained replay plan pin changed")
    reference_plan = new_plan(public_plan)
    if plan.model_dump(exclude={"created_at"}) != reference_plan.model_dump(
        exclude={"created_at"}
    ):
        raise ValueError(
            "replay plan differs from the declared development/validation scope"
        )

    prepared = {entry.request_id: entry for entry in preparation.entries}
    sources = []
    receipt_links = []
    for window in (plan.split.development, plan.split.validation):
        day = window.start_at
        while day < window.end_at:
            for symbol in plan.symbols:
                coordinate = BinanceBatchKlineCoordinates(symbol=symbol, day=day.date())
                entry = prepared[coordinate.request_id]
                if entry.partition.value != window.partition.value:
                    raise ValueError("source partition differs from the retained plan")
                prefix = f"evidence/{symbol.lower()}-1m-{day.date().isoformat()}"
                request = read_contract_json(
                    root, f"{prefix}-request.json", ExternalArtifactAcquisitionRequest
                )
                acquired = read_contract_json(
                    root, f"{prefix}-receipt.json", ExternalArtifactAcquisitionReceipt
                )
                if (
                    request.canonical_sha256() != entry.request_sha256
                    or acquired.request_sha256 != entry.request_sha256
                    or acquired.request_id != coordinate.request_id
                    or acquired.relative_path != coordinate.relative_path
                    or acquired.sha256 != entry.artifact_sha256
                    or acquired.byte_size != entry.artifact_byte_size
                    or not acquired.verified
                ):
                    raise ValueError(
                        "acquisition receipt does not bind the frozen source"
                    )
                artifact = evidence_path(
                    root, coordinate.relative_path, create_parents=False
                )
                if artifact.stat().st_size != entry.artifact_byte_size:
                    raise ValueError("archive size changed")
                raw = artifact.read_bytes()
                if digest(raw) != entry.artifact_sha256:
                    raise ValueError("archive bytes changed")
                receipt = ArchiveObservationReceipt(
                    archive_sha256=entry.artifact_sha256,
                    archive_byte_size=len(raw),
                    symbol=symbol,
                    instrument_id=coordinate.instrument_id,
                    partition=window.partition,
                    day_start_at=day,
                    member_name=f"{symbol}-1m-{day.date().isoformat()}.csv",
                    # Receipt body completion is the measured archive observation.
                    # The provider's historical Last-Modified is never row availability.
                    observed_at=acquired.retrieved_at,
                    retrieved_at=acquired.retrieved_at,
                )
                sources.append(
                    ArchiveBatchSource(raw, receipt, receipt.canonical_sha256())
                )
                receipt_links.append(
                    {
                        "request_id": coordinate.request_id,
                        "original_receipt_sha256": digest(
                            evidence_path(
                                root, f"{prefix}-receipt.json", create_parents=False
                            ).read_bytes()
                        ),
                        "derived_archive_receipt_sha256": receipt.canonical_sha256(),
                    }
                )
            day += timedelta(days=1)
    plan_pin = plan.canonical_sha256()
    manifest = load_archive_batch_rehearsal(
        tuple(sources), plan=plan, expected_plan_sha256=plan_pin
    )
    output.mkdir(parents=True, exist_ok=False)
    files = {
        "archive-plan.json": publish(
            output, "archive-plan.json", plan.canonical_json_bytes()
        ),
        "archive-manifest.json": publish(
            output, "archive-manifest.json", manifest.canonical_json_bytes()
        ),
    }
    results = []
    for partition in (DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION):
        for symbol in plan.symbols:
            instrument = f"{symbol.removesuffix('USDT')}-USDT-SWAP"
            minute_plan, minutes = archive_batch_minutes(
                manifest,
                archive_bytes=tuple(item.archive_bytes for item in sources),
                expected_plan_sha256=plan_pin,
                partition=partition,
                instrument_id=instrument,
            )
            pins = {
                "expected_plan_sha256": minute_plan.canonical_sha256(),
                "expected_source_sha256": minute_source_sha256(minutes),
            }
            first = aggregate_point_in_time_minutes(minutes, plan=minute_plan, **pins)
            second = aggregate_point_in_time_minutes(minutes, plan=minute_plan, **pins)
            if first.canonical_json_bytes() != second.canonical_json_bytes():
                raise ValueError("deterministic repeated aggregation changed")
            historical_rejections = []
            for series in first.timeframes:
                try:
                    replay_features_at(
                        tuple(item.row for item in series.bars),
                        as_of=minute_plan.window.end_at,
                        bar_horizon=series.horizon,
                    )
                except ReplayValidationError as exc:
                    if str(exc) != "a due replay bar was not available at the cutoff":
                        raise
                    historical_rejections.append(series.horizon.label)
                else:
                    raise ValueError(
                        "historical archive unexpectedly gained causal availability"
                    )
            name = f"{partition.value}-{symbol}-aggregation.json"
            files[name] = publish(output, name, first.canonical_json_bytes())
            results.append(
                {
                    "partition": partition.value,
                    "symbol": symbol,
                    "minute_count": len(minutes),
                    "aggregate_counts": [len(item.bars) for item in first.timeframes],
                    "aggregation_sha256": first.canonical_sha256(),
                    "historical_cutoff_rejections": historical_rejections,
                    "deterministic_repeat": True,
                }
            )
            print(f"REHEARSAL_GROUP_COMPLETE={partition.value}:{symbol}", flush=True)
    summary = {
        "schema": "ctcc.gate3.development_validation_rehearsal.v1",
        "plan_sha256": plan_pin,
        "manifest_sha256": manifest.canonical_sha256(),
        "source_artifacts": len(sources),
        "source_rows": manifest.total_rows,
        "holdout_archives_opened": 0,
        "candidate_fitted": False,
        "predictive_oos_eligible": False,
        "point_in_time_provenance_qualified": False,
        "execution_authority": False,
        "current_claim": "computational",
        "receipt_links": receipt_links,
        "results": results,
        "files": files,
    }
    publish(
        output,
        "summary.json",
        (json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if (args.plan is None) != (args.expected_plan_sha256 is None):
        parser.error("a retained plan and its independent SHA256 are required together")
    result = run(args.dataset_root, args.output, args.plan, args.expected_plan_sha256)
    print(f"MIE_DEVELOPMENT_VALIDATION_REHEARSAL_ROWS={result['source_rows']}")
    print("MIE_PREDICTIVE_OOS_ELIGIBLE=0")


if __name__ == "__main__":
    main()
