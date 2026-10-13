"""Synthetic output-integrity tests; no real archives, candidate or OOS read."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.mie.contracts import ForecastHorizon
from app.mie.features import FeatureBar
from app.mie.validation.archive_batch import (
    ArchiveBatchManifest,
    ArchiveBatchMember,
    ArchiveBatchPlan,
)
from app.mie.validation.availability import ArchiveObservationReceipt, AvailabilityBasis
from app.mie.validation.batch_replay import (
    TARGETS,
    AggregateBar,
    AggregatedTimeframe,
    DeterministicMinuteBatch,
    MinuteAggregationPlan,
)
from app.mie.validation.contracts import (
    DatasetPartition,
    PartitionWindow,
    PurgedWalkForwardSplit,
)
from app.mie.validation.replay import PointInTimeBar
from app.research.external_benchmarks.contracts import (
    ExternalArtifactAcquisitionReceipt,
)
from scripts.replay_gate3_development_validation import (
    publish,
    read_acquisition_receipt_bytes,
    run,
)
from scripts.verify_gate3_development_validation_groups import verify_four_groups

START = datetime(2024, 1, 1, tzinfo=UTC)
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def sha(value: str | bytes) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def fixture_plan_manifest() -> tuple[ArchiveBatchPlan, ArchiveBatchManifest]:
    plan = ArchiveBatchPlan(
        plan_id="gate3:four:synthetic:groups:v1",
        created_at=START + timedelta(days=10),
        split=PurgedWalkForwardSplit(
            development=PartitionWindow(
                partition=DatasetPartition.DEVELOPMENT,
                start_at=START,
                end_at=START + timedelta(days=1),
            ),
            validation=PartitionWindow(
                partition=DatasetPartition.VALIDATION,
                start_at=START + timedelta(days=2),
                end_at=START + timedelta(days=3),
            ),
            holdout=PartitionWindow(
                partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
                start_at=START + timedelta(days=4),
                end_at=START + timedelta(days=5),
            ),
            purge_seconds=60,
            embargo_seconds=60,
            max_feature_dependency_seconds=60,
            label_dependency_seconds=60,
        ),
        symbols=SYMBOLS,
        expected_artifact_count=4,
        expected_rows=4 * 1440,
    )
    members = []
    for partition, day in (
        (DatasetPartition.DEVELOPMENT, START),
        (DatasetPartition.VALIDATION, START + timedelta(days=2)),
    ):
        for symbol in SYMBOLS:
            receipt = ArchiveObservationReceipt(
                archive_sha256=sha(f"archive:{partition}:{symbol}"),
                archive_byte_size=8,
                symbol=symbol,
                instrument_id=f"{symbol.removesuffix('USDT')}-USDT-SWAP",
                partition=partition,
                day_start_at=day,
                member_name=f"{symbol}-1m-{day.date().isoformat()}.csv",
                observed_at=START + timedelta(days=9),
                retrieved_at=START + timedelta(days=9, seconds=1),
            )
            members.append(
                ArchiveBatchMember(
                    receipt=receipt,
                    receipt_sha256=receipt.canonical_sha256(),
                    dataset_sha256=sha(f"dataset:{partition}:{symbol}"),
                )
            )
    manifest = ArchiveBatchManifest(
        plan=plan,
        plan_sha256=plan.canonical_sha256(),
        members=tuple(members),
        total_rows=plan.expected_rows,
        total_archive_bytes=4 * 8,
    )
    return plan, manifest


def synthetic_aggregate(
    plan: ArchiveBatchPlan,
    manifest: ArchiveBatchManifest,
    partition: DatasetPartition,
    symbol: str,
    *,
    omit_last_15m: bool = False,
) -> DeterministicMinuteBatch:
    window = (
        plan.split.development
        if partition == DatasetPartition.DEVELOPMENT
        else plan.split.validation
    )
    instrument = f"{symbol.removesuffix('USDT')}-USDT-SWAP"
    minute_plan = MinuteAggregationPlan(
        source_manifest_sha256=manifest.canonical_sha256(),
        window=window,
        instrument_id=instrument,
        expected_minute_rows=1440,
        volume_unit="source_unspecified",
    )
    source_member = next(
        member
        for member in manifest.members
        if member.receipt.partition == partition and member.receipt.symbol == symbol
    )
    timeframes = []
    for label, seconds in TARGETS:
        bars = []
        count = 86400 // seconds - (1 if label == "15m" and omit_last_15m else 0)
        width = seconds // 60
        for ordinal in range(1, count + 1):
            content_sha = sha(f"{partition}:{symbol}:{label}:{ordinal}")
            bar = FeatureBar(
                closed_at=window.start_at + timedelta(seconds=ordinal * seconds),
                open=Decimal(100),
                high=Decimal(101),
                low=Decimal(99),
                close=Decimal(100),
                volume=Decimal(1),
            )
            bars.append(
                AggregateBar(
                    row=PointInTimeBar(
                        source_row_id=f"synthetic:{partition}:{symbol}:{label}:{ordinal}",
                        source_row_sha256=content_sha,
                        instrument_id=instrument,
                        available_at=source_member.receipt.retrieved_at,
                        bar=bar,
                    ),
                    constituent_sha256=content_sha,
                    constituent_count=width,
                    availability_bases=(AvailabilityBasis.ARCHIVE_OBSERVATION,) * width,
                    receipt_sha256s=(source_member.receipt_sha256,) * width,
                )
            )
        timeframes.append(
            AggregatedTimeframe(
                horizon=ForecastHorizon(label=label, seconds=seconds),
                bars=tuple(bars),
            )
        )
    return DeterministicMinuteBatch(
        plan=minute_plan,
        plan_sha256=minute_plan.canonical_sha256(),
        source_minutes_sha256=sha(f"minutes:{partition}:{symbol}"),
        timeframes=tuple(timeframes),
    )


def write_groups(
    root: Path,
    *,
    omit_last_15m: bool = False,
    substitute_holdout: bool = False,
) -> tuple[Path, str, Path, str, tuple[Path, ...], tuple[str, ...]]:
    plan, manifest = fixture_plan_manifest()
    plan_path = root / "retained-plan.json"
    manifest_path = root / "retained-manifest.json"
    plan_path.write_bytes(plan.canonical_json_bytes())
    manifest_path.write_bytes(manifest.canonical_json_bytes())
    paths = []
    summary_hashes = []
    for partition in (DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION):
        for symbol in SYMBOLS:
            directory = root / f"{partition.value}-{symbol}"
            directory.mkdir()
            aggregate = synthetic_aggregate(
                plan,
                manifest,
                partition,
                symbol,
                omit_last_15m=(
                    omit_last_15m
                    and partition == DatasetPartition.DEVELOPMENT
                    and symbol == "BTCUSDT"
                ),
            )
            aggregate_name = f"{partition.value}-{symbol}-aggregation.json"
            files = {
                "archive-plan.json": plan.canonical_json_bytes(),
                "archive-manifest.json": manifest.canonical_json_bytes(),
                aggregate_name: aggregate.canonical_json_bytes(),
            }
            for name, payload in files.items():
                (directory / name).write_bytes(payload)
            result = {
                "partition": (
                    "retrospective_holdout"
                    if substitute_holdout
                    and partition == DatasetPartition.DEVELOPMENT
                    and symbol == "BTCUSDT"
                    else partition.value
                ),
                "symbol": symbol,
                "minute_count": 1440,
                "aggregate_counts": [len(item.bars) for item in aggregate.timeframes],
                "aggregation_sha256": aggregate.canonical_sha256(),
                "repeat_aggregation_sha256": aggregate.canonical_sha256(),
                "historical_cutoff_rejections": ["15m", "1H", "4H"],
                "deterministic_repeat": True,
            }
            summary = {
                "schema": "ctcc.gate3.development_validation_group_rehearsal.v1",
                "plan_sha256": plan.canonical_sha256(),
                "manifest_sha256": manifest.canonical_sha256(),
                "source_artifacts": len(manifest.members),
                "source_rows": manifest.total_rows,
                "holdout_archives_opened": 0,
                "candidate_fitted": False,
                "predictive_oos_eligible": False,
                "point_in_time_provenance_qualified": False,
                "execution_authority": False,
                "current_claim": "computational",
                "receipt_links": [
                    {
                        "request_id": f"synthetic:{index}",
                        "original_receipt_sha256": sha(f"original:{index}"),
                        "derived_archive_receipt_sha256": member.receipt_sha256,
                    }
                    for index, member in enumerate(manifest.members)
                ],
                "results": [result],
                "files": {name: sha(payload) for name, payload in files.items()},
            }
            summary_bytes = (
                json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            (directory / "summary.json").write_bytes(summary_bytes)
            paths.append(directory)
            summary_hashes.append(sha(summary_bytes))
    return (
        plan_path,
        plan.canonical_sha256(),
        manifest_path,
        manifest.canonical_sha256(),
        tuple(paths),
        tuple(summary_hashes),
    )


def verify_inputs(inputs: tuple) -> dict:
    plan_path, plan_sha, manifest_path, manifest_sha, paths, summary_hashes = inputs
    return verify_four_groups(
        plan_path=plan_path,
        expected_plan_sha256=plan_sha,
        manifest_path=manifest_path,
        expected_manifest_sha256=manifest_sha,
        group_outputs=paths,
        expected_summary_sha256s=summary_hashes,
    )


def test_four_exact_groups_are_computational_only(tmp_path: Path) -> None:
    result = verify_inputs(write_groups(tmp_path))
    assert [item["group"] for item in result["groups"]] == [
        "development:BTCUSDT",
        "development:ETHUSDT",
        "validation:BTCUSDT",
        "validation:ETHUSDT",
    ]
    assert result["source_rows"] == 5760
    assert result["holdout_archives_opened"] == 0
    assert result["candidate_fitted"] is False
    assert result["point_in_time_provenance_qualified"] is False
    assert result["predictive_oos_eligible"] is False
    assert result["execution_authority"] is False


def test_group_set_rejects_missing_duplicate_or_changed_external_pin(
    tmp_path: Path,
) -> None:
    inputs = write_groups(tmp_path)
    plan_path, plan_sha, manifest_path, manifest_sha, paths, hashes = inputs
    for bad_paths, bad_hashes in (
        (paths[:-1], hashes[:-1]),
        ((paths[0], paths[0], *paths[2:]), hashes),
        (paths, ("0" * 64, *hashes[1:])),
    ):
        with pytest.raises(ValueError):
            verify_four_groups(
                plan_path=plan_path,
                expected_plan_sha256=plan_sha,
                manifest_path=manifest_path,
                expected_manifest_sha256=manifest_sha,
                group_outputs=bad_paths,
                expected_summary_sha256s=bad_hashes,
            )


@pytest.mark.parametrize("damage", ["missing_15m", "holdout_alias"])
def test_self_consistently_resigned_incomplete_or_holdout_group_still_denies(
    tmp_path: Path, damage: str
) -> None:
    inputs = write_groups(
        tmp_path,
        omit_last_15m=damage == "missing_15m",
        substitute_holdout=damage == "holdout_alias",
    )
    with pytest.raises(ValueError):
        verify_inputs(inputs)


def test_changed_artifact_or_manifest_pin_denies(tmp_path: Path) -> None:
    inputs = write_groups(tmp_path)
    plan_path, plan_sha, manifest_path, _manifest_sha, paths, hashes = inputs
    with pytest.raises(ValueError, match="pin"):
        verify_four_groups(
            plan_path=plan_path,
            expected_plan_sha256=plan_sha,
            manifest_path=manifest_path,
            expected_manifest_sha256="0" * 64,
            group_outputs=paths,
            expected_summary_sha256s=hashes,
        )
    with (paths[0] / "development-BTCUSDT-aggregation.json").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="digest"):
        verify_inputs(inputs)


@pytest.mark.parametrize("damage", ["receipt_substitution", "availability_shift"])
def test_rehashed_group_cannot_replace_manifest_source_availability(
    tmp_path: Path, damage: str
) -> None:
    inputs = write_groups(tmp_path)
    plan_path, plan_sha, manifest_path, manifest_sha, paths, hashes = inputs
    directory = paths[0]
    aggregate_path = directory / "development-BTCUSDT-aggregation.json"
    aggregate_payload = json.loads(aggregate_path.read_bytes())
    first_bar = aggregate_payload["timeframes"][0]["bars"][0]
    if damage == "receipt_substitution":
        first_bar["receipt_sha256s"][0] = sha("substituted-receipt")
    else:
        first_bar["row"]["available_at"] = (
            (START + timedelta(days=10)).isoformat().replace("+00:00", "Z")
        )
    aggregate = DeterministicMinuteBatch.model_validate_json(
        json.dumps(aggregate_payload)
    )
    aggregate_bytes = aggregate.canonical_json_bytes()
    aggregate_path.write_bytes(aggregate_bytes)

    summary_path = directory / "summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["results"][0]["aggregation_sha256"] = aggregate.canonical_sha256()
    summary["results"][0]["repeat_aggregation_sha256"] = aggregate.canonical_sha256()
    summary["files"][aggregate_path.name] = sha(aggregate_bytes)
    summary_bytes = (
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    summary_path.write_bytes(summary_bytes)
    resigned_hashes = (sha(summary_bytes), *hashes[1:])

    with pytest.raises(ValueError, match="source availability"):
        verify_four_groups(
            plan_path=plan_path,
            expected_plan_sha256=plan_sha,
            manifest_path=manifest_path,
            expected_manifest_sha256=manifest_sha,
            group_outputs=paths,
            expected_summary_sha256s=resigned_hashes,
        )


def test_one_group_requires_prior_plan_and_manifest_pin_before_source_io(
    tmp_path: Path,
) -> None:
    output = tmp_path / "unused"
    with pytest.raises(ValueError, match="exact retained plan and manifest pins"):
        run(
            tmp_path,
            output,
            None,
            None,
            selected_group=(DatasetPartition.DEVELOPMENT, "BTCUSDT"),
            expected_manifest_sha256="a" * 64,
        )
    assert not output.exists()


def test_no_clobber_group_file_keeps_original_bytes(tmp_path: Path) -> None:
    root = tmp_path / "group"
    root.mkdir()
    assert publish(root, "receipt.json", b"first") == sha(b"first")
    with pytest.raises(FileExistsError):
        publish(root, "receipt.json", b"replacement")
    assert (root / "receipt.json").read_bytes() == b"first"


def test_original_receipt_hash_uses_the_same_single_parse_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = ExternalArtifactAcquisitionReceipt(
        request_id="binance.btcusdt.1m.2024-01-01",
        request_sha256=sha("request"),
        source_id="binance.public_data",
        final_url="https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01-01.zip",
        relative_path="binance/BTCUSDT-1m-2024-01-01.zip",
        sha256=sha("archive"),
        byte_size=8,
        media_type="application/zip",
        retrieved_at=START + timedelta(days=9),
        redirect_count=0,
        status="downloaded",
        archive_report_sha256=sha("archive-report"),
    )
    target = tmp_path / "receipt.json"
    original_bytes = (receipt.model_dump_json(indent=2) + "\n").encode()
    target.write_bytes(original_bytes)
    original_open = Path.open
    reads = 0

    def one_open(path: Path, *args, **kwargs):
        nonlocal reads
        if path == target:
            reads += 1
            if reads > 1:
                raise AssertionError("receipt was opened again after parsing")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", one_open)
    parsed, retained_bytes = read_acquisition_receipt_bytes(tmp_path, "receipt.json")
    assert reads == 1
    assert parsed == receipt
    assert retained_bytes == original_bytes
    assert sha(retained_bytes) == sha(original_bytes)
    assert sha(retained_bytes) != sha((receipt.model_dump_json() + "\n").encode())


@pytest.mark.parametrize(
    "payload",
    [
        b'{"request_id":"first","request_id":"second"}',
        b'{"nested":{"status":"first","status":"second"}}',
    ],
)
def test_original_receipt_rejects_duplicate_json_keys(
    tmp_path: Path, payload: bytes
) -> None:
    (tmp_path / "receipt.json").write_bytes(payload)
    with pytest.raises(ValueError, match="repeats a JSON key"):
        read_acquisition_receipt_bytes(tmp_path, "receipt.json")


def test_original_receipt_rejects_non_json_number(tmp_path: Path) -> None:
    (tmp_path / "receipt.json").write_bytes(b'{"request_id":NaN}')
    with pytest.raises(ValueError, match="non-JSON number"):
        read_acquisition_receipt_bytes(tmp_path, "receipt.json")
