from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

import httpx

from app.research.external_benchmarks import (
    BATCH_PLAN_PATH,
    BATCH_PREPARATION_PATH,
    BinanceBatchKlineCoordinates,
    BinanceBatchPartition,
    BinanceBatchPreparation,
    BinanceBatchPreparationEntry,
    BinancePublicArtifactIdentity,
    ExternalArtifactAcquisitionRequest,
    batch_evidence_prefix,
    canonical_binance_batch_plan,
    prepare_binance_kline_request,
    sha256_file,
)
from app.research.external_benchmarks.evidence_io import (
    read_contract_json,
    write_contract_json,
)


def _verify_existing_preparation(
    dataset_root: Path,
    preparation: BinanceBatchPreparation,
    *,
    terms_sha256: str,
) -> None:
    plan = canonical_binance_batch_plan()
    if (
        preparation.plan_id != plan.plan_id
        or preparation.plan_sha256 != plan.canonical_sha256()
        or preparation.expected_artifact_count != plan.expected_artifact_count
    ):
        raise ValueError("existing batch preparation disagrees with plan")
    expected_items = plan.coordinate_items()
    expected_ids = tuple(coordinates.request_id for _, coordinates in expected_items)
    if tuple(entry.request_id for entry in preparation.entries) != expected_ids:
        raise ValueError("existing batch preparation coordinates changed")
    for (partition, coordinates), entry in zip(
        expected_items,
        preparation.entries,
        strict=True,
    ):
        if (
            entry.partition != partition
            or entry.symbol != coordinates.symbol
            or entry.interval != coordinates.interval
            or entry.day != coordinates.day
        ):
            raise ValueError("existing batch preparation coordinates changed")
        identity = read_contract_json(
            dataset_root,
            entry.identity_relative_path,
            BinancePublicArtifactIdentity,
        )
        request = read_contract_json(
            dataset_root,
            entry.request_relative_path,
            ExternalArtifactAcquisitionRequest,
        )
        if not _prepared_item_matches(
            coordinates,
            entry,
            identity,
            request,
            terms_sha256=terms_sha256,
        ):
            raise ValueError("existing batch preparation evidence changed")


def _preparation_entry(
    partition: BinanceBatchPartition,
    coordinates: BinanceBatchKlineCoordinates,
    identity: BinancePublicArtifactIdentity,
    request: ExternalArtifactAcquisitionRequest,
) -> BinanceBatchPreparationEntry:
    prefix = batch_evidence_prefix(coordinates)
    return BinanceBatchPreparationEntry(
        partition=partition,
        symbol=coordinates.symbol,
        interval=coordinates.interval,
        day=coordinates.day,
        request_id=coordinates.request_id,
        identity_relative_path=f"{prefix}-identity.json",
        request_relative_path=f"{prefix}-request.json",
        identity_sha256=identity.canonical_sha256(),
        request_sha256=request.canonical_sha256(),
        artifact_sha256=identity.artifact_sha256,
        artifact_byte_size=identity.artifact_byte_size,
        provider_last_modified_at=identity.provider_last_modified_at,
    )


def _prepared_item_matches(
    coordinates: BinanceBatchKlineCoordinates,
    entry: BinanceBatchPreparationEntry,
    identity: BinancePublicArtifactIdentity,
    request: ExternalArtifactAcquisitionRequest,
    *,
    terms_sha256: str,
) -> bool:
    return bool(
        identity.coordinates_sha256 == coordinates.canonical_sha256()
        and str(identity.artifact_url) == coordinates.download_url
        and str(identity.checksum_url) == coordinates.checksum_url
        and identity.canonical_sha256() == entry.identity_sha256
        and request.canonical_sha256() == entry.request_sha256
        and request.request_id == coordinates.request_id
        and str(request.download_url) == coordinates.download_url
        and request.relative_path == coordinates.relative_path
        and request.expected_sha256 == entry.artifact_sha256
        and request.expected_byte_size == entry.artifact_byte_size
        and identity.observed_at == request.terms_reviewed_at
        and identity.terms_review_sha256 == request.terms_review_sha256
        and identity.terms_review_sha256 == terms_sha256
    )


def _reviewed_terms_sha256(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("terms review cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("terms review does not exist") from exc
    if not resolved.is_file():
        raise ValueError("terms review must be a real file")
    return sha256_file(resolved)


async def _run(args: argparse.Namespace) -> int:
    plan = canonical_binance_batch_plan()
    terms_sha256 = _reviewed_terms_sha256(args.terms_review)
    plan_status = write_contract_json(
        args.dataset_root,
        BATCH_PLAN_PATH,
        plan,
    )
    preparation_path = args.dataset_root.joinpath(*BATCH_PREPARATION_PATH.split("/"))
    if preparation_path.exists():
        preparation = read_contract_json(
            args.dataset_root,
            BATCH_PREPARATION_PATH,
            BinanceBatchPreparation,
        )
        _verify_existing_preparation(
            args.dataset_root,
            preparation,
            terms_sha256=terms_sha256,
        )
        print(preparation.model_dump_json(indent=2))
        print(f"BINANCE_BATCH_PLAN_OUTPUT={BATCH_PLAN_PATH}")
        print(f"BINANCE_BATCH_PLAN_OUTPUT_STATUS={plan_status}")
        print(f"BINANCE_BATCH_PREPARATION_OUTPUT={BATCH_PREPARATION_PATH}")
        print("BINANCE_BATCH_PREPARATION_OUTPUT_STATUS=already_present")
        print("BINANCE_BATCH_PREPARATION_REUSED=1")
        print("BINANCE_BATCH_ARTIFACT_GET_PERFORMED=0")
        print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
        return 0

    semaphore = asyncio.Semaphore(args.max_concurrency)
    timeout = httpx.Timeout(30, connect=10)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:

        async def prepare_one(item):
            partition, coordinates = item
            prefix = batch_evidence_prefix(coordinates)
            identity_relative_path = f"{prefix}-identity.json"
            request_relative_path = f"{prefix}-request.json"
            identity_path = args.dataset_root.joinpath(
                *identity_relative_path.split("/")
            )
            request_path = args.dataset_root.joinpath(*request_relative_path.split("/"))
            if identity_path.exists() != request_path.exists():
                raise ValueError("partial batch identity pair cannot be reused")
            if identity_path.exists():
                identity = read_contract_json(
                    args.dataset_root,
                    identity_relative_path,
                    BinancePublicArtifactIdentity,
                )
                request = read_contract_json(
                    args.dataset_root,
                    request_relative_path,
                    ExternalArtifactAcquisitionRequest,
                )
                entry = _preparation_entry(
                    partition,
                    coordinates,
                    identity,
                    request,
                )
                if not _prepared_item_matches(
                    coordinates,
                    entry,
                    identity,
                    request,
                    terms_sha256=terms_sha256,
                ):
                    raise ValueError("partial batch identity changed")
                return entry, identity, request
            async with semaphore:
                identity, request = await prepare_binance_kline_request(
                    coordinates,
                    args.terms_review,
                    client=client,
                )
            return (
                _preparation_entry(
                    partition,
                    coordinates,
                    identity,
                    request,
                ),
                identity,
                request,
            )

        prepared = await asyncio.gather(
            *(prepare_one(item) for item in plan.coordinate_items())
        )

    entries = tuple(item[0] for item in prepared)
    for entry, identity, request in prepared:
        identity_status = write_contract_json(
            args.dataset_root,
            entry.identity_relative_path,
            identity,
        )
        request_status = write_contract_json(
            args.dataset_root,
            entry.request_relative_path,
            request,
        )
        if identity_status not in {"written", "already_present"}:
            raise ValueError("batch identity evidence was not persisted")
        if request_status not in {"written", "already_present"}:
            raise ValueError("batch request evidence was not persisted")

    preparation = BinanceBatchPreparation(
        plan_id=plan.plan_id,
        plan_sha256=plan.canonical_sha256(),
        prepared_at=max(identity.observed_at for _, identity, _ in prepared),
        expected_artifact_count=plan.expected_artifact_count,
        total_expected_bytes=sum(entry.artifact_byte_size for entry in entries),
        entries=entries,
    )
    preparation_status = write_contract_json(
        args.dataset_root,
        BATCH_PREPARATION_PATH,
        preparation,
    )
    print(preparation.model_dump_json(indent=2))
    print(f"BINANCE_BATCH_PLAN_OUTPUT={BATCH_PLAN_PATH}")
    print(f"BINANCE_BATCH_PLAN_OUTPUT_STATUS={plan_status}")
    print(f"BINANCE_BATCH_PREPARATION_OUTPUT={BATCH_PREPARATION_PATH}")
    print(f"BINANCE_BATCH_PREPARATION_OUTPUT_STATUS={preparation_status}")
    print("BINANCE_BATCH_PREPARATION_REUSED=0")
    print("BINANCE_BATCH_ARTIFACT_GET_PERFORMED=0")
    print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the frozen Binance batch metadata without downloading "
            "any ZIP artifact"
        )
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--terms-review", type=Path, required=True)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        choices=range(1, 9),
        default=4,
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
