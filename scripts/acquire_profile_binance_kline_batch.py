from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path
import sys

import httpx

from app.research.external_benchmarks import (
    AcquisitionLimits,
    ArchiveInspectionPolicy,
    BATCH_EVIDENCE_PATH,
    BATCH_PREPARATION_PATH,
    BinanceBatchEvidence,
    BinanceBatchPlan,
    BinanceBatchPreparation,
    BinanceBatchResultEntry,
    BinancePublicArtifactIdentity,
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
    acquire_external_artifact,
    batch_evidence_prefix,
    build_binance_batch_evidence,
    canonical_binance_batch_plan,
    profile_binance_kline_archive,
    summarize_binance_daily_archive,
)
from app.research.external_benchmarks.evidence_io import (
    read_contract_json,
    write_contract_json,
)


def _dataset_artifact_path(
    dataset_root: Path,
    relative_path: str,
) -> Path:
    root = dataset_root.resolve(strict=True)
    path = root.joinpath(*relative_path.split("/"))
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError("batch artifact must be a real non-symlink file")
    if not resolved.is_relative_to(root):
        raise ValueError("batch artifact escaped the dataset root")
    return resolved


def _verify_complete(
    evidence: BinanceBatchEvidence,
    *,
    plan: BinanceBatchPlan,
    preparation: BinanceBatchPreparation,
) -> None:
    expected_items = plan.coordinate_items()
    expected_ids = tuple(coordinates.request_id for _, coordinates in expected_items)
    expected_partitions = tuple(partition for partition, _ in expected_items)
    expected_summaries = tuple(
        (
            window.partition,
            symbol,
            window.start_day,
            window.end_day,
        )
        for window in plan.windows
        for symbol in plan.symbols
    )
    observed_summaries = tuple(
        (
            summary.partition,
            summary.symbol,
            summary.start_day,
            summary.end_day,
        )
        for summary in evidence.partition_summaries
    )
    if (
        evidence.plan_id != plan.plan_id
        or evidence.plan_sha256 != plan.canonical_sha256()
        or evidence.preparation_sha256 != preparation.canonical_sha256()
        or evidence.expected_artifact_count != plan.expected_artifact_count
        or evidence.completed_artifact_count != plan.expected_artifact_count
        or tuple(entry.request_id for entry in evidence.entries) != expected_ids
        or tuple(entry.partition for entry in evidence.entries) != expected_partitions
        or observed_summaries != expected_summaries
        or evidence.holdout_semantics != plan.holdout_semantics
        or evidence.generated_at < preparation.prepared_at
        or evidence.execution_authority is not False
        or evidence.runtime_consumers != 0
        or evidence.promotion_eligible is not False
    ):
        raise ValueError("existing batch evidence failed final verification")


def _verify_preparation(
    plan: BinanceBatchPlan,
    preparation: BinanceBatchPreparation,
) -> None:
    expected_items = plan.coordinate_items()
    if (
        preparation.plan_id != plan.plan_id
        or preparation.plan_sha256 != plan.canonical_sha256()
        or preparation.expected_artifact_count != plan.expected_artifact_count
        or tuple(entry.request_id for entry in preparation.entries)
        != tuple(coordinates.request_id for _, coordinates in expected_items)
        or tuple(entry.partition for entry in preparation.entries)
        != tuple(partition for partition, _ in expected_items)
    ):
        raise ValueError("batch preparation disagrees with frozen plan")


async def _run(args: argparse.Namespace) -> int:
    plan = canonical_binance_batch_plan()
    preparation = read_contract_json(
        args.dataset_root,
        BATCH_PREPARATION_PATH,
        BinanceBatchPreparation,
    )
    _verify_preparation(plan, preparation)
    final_path = args.dataset_root.joinpath(*BATCH_EVIDENCE_PATH.split("/"))
    if final_path.exists():
        evidence = read_contract_json(
            args.dataset_root,
            BATCH_EVIDENCE_PATH,
            BinanceBatchEvidence,
        )
        _verify_complete(
            evidence,
            plan=plan,
            preparation=preparation,
        )
        print(evidence.model_dump_json(indent=2))
        print("BINANCE_BATCH_EVIDENCE_OUTPUT_STATUS=already_present")
        print("BINANCE_BATCH_ACQUISITION_REUSED=1")
        print("BINANCE_BATCH_QUALITY_PASSED=1")
        print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
        return 0

    preparation_by_id = {entry.request_id: entry for entry in preparation.entries}
    if len(preparation_by_id) != plan.expected_artifact_count:
        raise ValueError("batch preparation contains duplicate request IDs")
    semaphore = asyncio.Semaphore(args.max_concurrency)
    timeout = httpx.Timeout(60, connect=10)
    limits = AcquisitionLimits(max_bytes=1024 * 1024, max_redirects=0)
    archive_policy = ArchiveInspectionPolicy(
        max_members=2,
        max_total_uncompressed_bytes=4 * 1024 * 1024,
        max_single_member_bytes=4 * 1024 * 1024,
        max_expansion_ratio=Decimal("20"),
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:

        async def process_one(item):
            partition, coordinates = item
            try:
                preparation_entry = preparation_by_id[coordinates.request_id]
            except KeyError as exc:
                raise ValueError(
                    "batch preparation is missing reviewed coordinates"
                ) from exc
            prefix = batch_evidence_prefix(coordinates)
            identity = read_contract_json(
                args.dataset_root,
                preparation_entry.identity_relative_path,
                BinancePublicArtifactIdentity,
            )
            request = read_contract_json(
                args.dataset_root,
                preparation_entry.request_relative_path,
                ExternalArtifactAcquisitionRequest,
            )
            if (
                identity.canonical_sha256() != preparation_entry.identity_sha256
                or request.canonical_sha256() != preparation_entry.request_sha256
                or request.request_id != coordinates.request_id
                or request.expected_sha256 != preparation_entry.artifact_sha256
                or request.expected_byte_size != preparation_entry.artifact_byte_size
            ):
                raise ValueError("batch preparation identity changed")

            receipt_relative_path = f"{prefix}-receipt.json"
            receipt_path = args.dataset_root.joinpath(*receipt_relative_path.split("/"))
            async with semaphore:
                if receipt_path.exists():
                    receipt = read_contract_json(
                        args.dataset_root,
                        receipt_relative_path,
                        ExternalArtifactAcquisitionReceipt,
                    )
                else:
                    receipt = await acquire_external_artifact(
                        request,
                        args.dataset_root,
                        client=client,
                        limits=limits,
                        archive_policy=archive_policy,
                    )
                    status = write_contract_json(
                        args.dataset_root,
                        receipt_relative_path,
                        receipt,
                    )
                    if status != "written":
                        raise ValueError(
                            "new batch receipt unexpectedly already existed"
                        )

                manifest, generic_quality, provider_quality, item_evidence = (
                    profile_binance_kline_archive(
                        coordinates,
                        identity,
                        request,
                        receipt,
                        args.dataset_root,
                        generated_at=receipt.retrieved_at,
                    )
                )
                outputs = (
                    (f"{prefix}-manifest.json", manifest),
                    (f"{prefix}-generic-quality.json", generic_quality),
                    (f"{prefix}-binance-quality.json", provider_quality),
                    (f"{prefix}-evidence.json", item_evidence),
                )
                for relative_path, model in outputs:
                    write_contract_json(
                        args.dataset_root,
                        relative_path,
                        model,
                    )
                if not item_evidence.passed or not provider_quality.passed:
                    raise ValueError("batch provider evidence did not pass quality")
                artifact_path = _dataset_artifact_path(
                    args.dataset_root,
                    request.relative_path,
                )
                daily_summary = summarize_binance_daily_archive(
                    coordinates,
                    partition,
                    artifact_path,
                    artifact_sha256=request.expected_sha256,
                )
                write_contract_json(
                    args.dataset_root,
                    f"{prefix}-daily-summary.json",
                    daily_summary,
                )
            return (
                BinanceBatchResultEntry(
                    partition=partition,
                    request_id=coordinates.request_id,
                    artifact_sha256=request.expected_sha256,
                    request_sha256=request.canonical_sha256(),
                    receipt_sha256=receipt.canonical_sha256(),
                    manifest_sha256=manifest.canonical_sha256(),
                    generic_quality_sha256=(generic_quality.canonical_sha256()),
                    provider_quality_sha256=(provider_quality.canonical_sha256()),
                    evidence_sha256=item_evidence.canonical_sha256(),
                    daily_summary_sha256=daily_summary.canonical_sha256(),
                ),
                daily_summary,
                receipt.retrieved_at,
            )

        completed = await asyncio.gather(
            *(process_one(item) for item in plan.coordinate_items())
        )

    evidence = build_binance_batch_evidence(
        plan,
        preparation,
        tuple(item[0] for item in completed),
        tuple(item[1] for item in completed),
        generated_at=max(item[2] for item in completed),
    )
    evidence_status = write_contract_json(
        args.dataset_root,
        BATCH_EVIDENCE_PATH,
        evidence,
    )
    _verify_complete(
        evidence,
        plan=plan,
        preparation=preparation,
    )
    print(evidence.model_dump_json(indent=2))
    print(f"BINANCE_BATCH_EVIDENCE_OUTPUT={BATCH_EVIDENCE_PATH}")
    print(f"BINANCE_BATCH_EVIDENCE_OUTPUT_STATUS={evidence_status}")
    print("BINANCE_BATCH_ACQUISITION_REUSED=0")
    print("BINANCE_BATCH_QUALITY_PASSED=1")
    print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Acquire and profile the operator-confirmed frozen Binance batch")
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        choices=range(1, 9),
        default=4,
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
