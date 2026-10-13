"""Verify four separately replayed Dev/Val groups; computational only.

This checks retained external hashes and complete local output identity. It does
not turn late historical archive observations into point-in-time evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import timedelta
from pathlib import Path

from app.mie.validation.archive_batch import ArchiveBatchManifest, ArchiveBatchPlan
from app.mie.validation.availability import AvailabilityBasis
from app.mie.validation.batch_replay import TARGETS, DeterministicMinuteBatch
from app.mie.validation.contracts import DatasetPartition, Gate3Contract
from app.mie.validation.replay import ReplayValidationError, replay_features_at

SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_CONTRACT_BYTES = 128 * 1024 * 1024
MAX_SUMMARY_BYTES = 256 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_bounded(path: Path, *, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("group artifact is missing, linked, empty, or oversized")
    size = path.stat().st_size
    if not 0 < size <= limit:
        raise ValueError("group artifact is missing, linked, empty, or oversized")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) != size or path.stat().st_size != size:
        raise ValueError("group artifact changed during bounded read")
    return payload


def _read_contract[T: Gate3Contract](
    path: Path, contract_type: type[T], expected_sha256: str
) -> T:
    if type(expected_sha256) is not str or SHA256_RE.fullmatch(expected_sha256) is None:
        raise ValueError("external contract pin is invalid")
    payload = _read_bounded(path, limit=MAX_CONTRACT_BYTES)
    if _sha256(payload) != expected_sha256:
        raise ValueError("external contract pin differs")
    checked = contract_type.model_validate_json(payload)
    if checked.canonical_json_bytes() != payload:
        raise ValueError("contract bytes are noncanonical")
    return checked


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("group summary repeats a JSON key")
        value[key] = item
    return value


def _read_summary(path: Path, expected_sha256: str) -> dict:
    if type(expected_sha256) is not str or SHA256_RE.fullmatch(expected_sha256) is None:
        raise ValueError("external group summary pin is invalid")
    payload = _read_bounded(path, limit=MAX_SUMMARY_BYTES)
    if _sha256(payload) != expected_sha256:
        raise ValueError("external group summary pin differs")
    summary = json.loads(payload, object_pairs_hook=_unique_object)
    if (
        type(summary) is not dict
        or (
            json.dumps(summary, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
        != payload
    ):
        raise ValueError("group summary bytes are noncanonical")
    return summary


def _check_group(
    directory: Path,
    *,
    expected_summary_sha256: str,
    plan: ArchiveBatchPlan,
    manifest: ArchiveBatchManifest,
    expected_plan_sha256: str,
    expected_manifest_sha256: str,
) -> dict:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("group output directory is unavailable")
    summary = _read_summary(directory / "summary.json", expected_summary_sha256)
    if (
        set(summary)
        != {
            "schema",
            "plan_sha256",
            "manifest_sha256",
            "source_artifacts",
            "source_rows",
            "holdout_archives_opened",
            "candidate_fitted",
            "predictive_oos_eligible",
            "point_in_time_provenance_qualified",
            "execution_authority",
            "current_claim",
            "receipt_links",
            "results",
            "files",
        }
        or summary["schema"] != "ctcc.gate3.development_validation_group_rehearsal.v1"
    ):
        raise ValueError("group summary schema differs")
    if (
        summary["plan_sha256"] != expected_plan_sha256
        or summary["manifest_sha256"] != expected_manifest_sha256
        or type(summary["source_artifacts"]) is not int
        or summary["source_artifacts"] != len(manifest.members)
        or type(summary["source_rows"]) is not int
        or summary["source_rows"] != manifest.total_rows
        or type(summary["holdout_archives_opened"]) is not int
        or summary["holdout_archives_opened"] != 0
        or summary["candidate_fitted"] is not False
        or summary["predictive_oos_eligible"] is not False
        or summary["point_in_time_provenance_qualified"] is not False
        or summary["execution_authority"] is not False
        or summary["current_claim"] != "computational"
    ):
        raise ValueError("group summary identity or authority differs")
    links = summary["receipt_links"]
    if type(links) is not list or len(links) != len(manifest.members):
        raise ValueError("group original receipt inventory differs")
    request_ids: set[str] = set()
    for link, member in zip(links, manifest.members, strict=True):
        if (
            type(link) is not dict
            or set(link)
            != {
                "request_id",
                "original_receipt_sha256",
                "derived_archive_receipt_sha256",
            }
            or type(link["request_id"]) is not str
            or not link["request_id"]
            or link["request_id"] in request_ids
            or type(link["original_receipt_sha256"]) is not str
            or SHA256_RE.fullmatch(link["original_receipt_sha256"]) is None
            or link["derived_archive_receipt_sha256"] != member.receipt_sha256
        ):
            raise ValueError("group original receipt link differs")
        request_ids.add(link["request_id"])
    results = summary["results"]
    if type(results) is not list or len(results) != 1 or type(results[0]) is not dict:
        raise ValueError("group has missing or extra result")
    result = results[0]
    if set(result) != {
        "partition",
        "symbol",
        "minute_count",
        "aggregate_counts",
        "aggregation_sha256",
        "repeat_aggregation_sha256",
        "historical_cutoff_rejections",
        "deterministic_repeat",
    }:
        raise ValueError("group result schema differs")
    partition = result["partition"]
    symbol = result["symbol"]
    if partition not in ("development", "validation") or symbol not in plan.symbols:
        raise ValueError("group is outside Development/Validation plan")
    group_key = f"{partition}:{symbol}"
    aggregate_name = f"{partition}-{symbol}-aggregation.json"
    expected_files = {"archive-plan.json", "archive-manifest.json", aggregate_name}
    files = summary["files"]
    if (
        type(files) is not dict
        or set(files) != expected_files
        or {item.name for item in directory.iterdir()}
        != expected_files | {"summary.json"}
    ):
        raise ValueError("group output files are missing, extra, or ambiguous")
    for name in expected_files:
        if type(files[name]) is not str or SHA256_RE.fullmatch(files[name]) is None:
            raise ValueError("group file digest is invalid")
        if (
            _sha256(_read_bounded(directory / name, limit=MAX_CONTRACT_BYTES))
            != files[name]
        ):
            raise ValueError("group file digest differs")
    if (
        files["archive-plan.json"] != expected_plan_sha256
        or files["archive-manifest.json"] != expected_manifest_sha256
    ):
        raise ValueError("group plan or manifest file changed")
    aggregate = _read_contract(
        directory / aggregate_name,
        DeterministicMinuteBatch,
        files[aggregate_name],
    )
    window = (
        plan.split.development
        if partition == DatasetPartition.DEVELOPMENT.value
        else plan.split.validation
    )
    expected_minutes = (window.end_at - window.start_at) // timedelta(minutes=1)
    if (
        aggregate.plan.window != window
        or aggregate.plan.instrument_id != f"{symbol.removesuffix('USDT')}-USDT-SWAP"
        or aggregate.plan.source_manifest_sha256 != expected_manifest_sha256
        or aggregate.plan.volume_unit != "source_unspecified"
        or aggregate.plan.expected_minute_rows != expected_minutes
        or aggregate.plan.canonical_sha256() != aggregate.plan_sha256
        or aggregate.canonical_sha256() != result["aggregation_sha256"]
        or result["repeat_aggregation_sha256"] != result["aggregation_sha256"]
        or result["deterministic_repeat"] is not True
        or type(result["minute_count"]) is not int
        or result["minute_count"] != expected_minutes
        or result["historical_cutoff_rejections"] != ["15m", "1H", "4H"]
        or len(aggregate.timeframes) != len(TARGETS)
    ):
        raise ValueError("group aggregate lineage or deterministic result differs")
    counts = []
    for series, (label, seconds) in zip(aggregate.timeframes, TARGETS, strict=True):
        expected_count = (window.end_at - window.start_at) // timedelta(seconds=seconds)
        width = seconds // 60
        if (
            series.horizon.label != label
            or series.horizon.seconds != seconds
            or len(series.bars) != expected_count
        ):
            raise ValueError("group aggregate coverage differs")
        for ordinal, item in enumerate(series.bars, start=1):
            if (
                item.row.instrument_id != aggregate.plan.instrument_id
                or item.row.bar.closed_at
                != window.start_at + timedelta(seconds=ordinal * seconds)
                or item.constituent_count != width
                or len(item.receipt_sha256s) != width
                or item.availability_bases
                != (AvailabilityBasis.ARCHIVE_OBSERVATION,) * width
                or item.row.source_row_sha256 != item.constituent_sha256
            ):
                raise ValueError("group aggregate bar or availability basis differs")
        try:
            replay_features_at(
                tuple(item.row for item in series.bars),
                as_of=window.end_at,
                bar_horizon=series.horizon,
            )
        except ReplayValidationError as exc:
            if str(exc) != "a due replay bar was not available at the cutoff":
                raise ValueError(
                    "group replay failed for an unexpected reason"
                ) from exc
        else:
            raise ValueError("group gained historical causal availability")
        counts.append(len(series.bars))
    if result["aggregate_counts"] != counts:
        raise ValueError("group aggregate counts differ")
    return {
        "group": group_key,
        "summary_sha256": expected_summary_sha256,
        "aggregation_sha256": aggregate.canonical_sha256(),
        "minute_count": expected_minutes,
        "aggregate_counts": counts,
    }


def verify_four_groups(
    *,
    plan_path: Path,
    expected_plan_sha256: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
    group_outputs: tuple[Path, ...],
    expected_summary_sha256s: tuple[str, ...],
) -> dict:
    """Require all four exact groups and separately retained summary hash pins."""
    if (
        type(group_outputs) is not tuple
        or type(expected_summary_sha256s) is not tuple
        or len(group_outputs) != 4
        or len(expected_summary_sha256s) != 4
        or len({path.resolve(strict=True) for path in group_outputs}) != 4
        or len(set(expected_summary_sha256s)) != 4
    ):
        raise ValueError("four distinct group outputs and summary pins are required")
    plan = _read_contract(plan_path, ArchiveBatchPlan, expected_plan_sha256)
    manifest = _read_contract(
        manifest_path, ArchiveBatchManifest, expected_manifest_sha256
    )
    if (
        plan.symbols != ("BTCUSDT", "ETHUSDT")
        or manifest.plan.canonical_json_bytes() != plan.canonical_json_bytes()
        or manifest.plan_sha256 != expected_plan_sha256
    ):
        raise ValueError("four-group plan and manifest identity differ")
    verified = [
        _check_group(
            directory,
            expected_summary_sha256=summary_sha,
            plan=plan,
            manifest=manifest,
            expected_plan_sha256=expected_plan_sha256,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        for directory, summary_sha in zip(
            group_outputs, expected_summary_sha256s, strict=True
        )
    ]
    expected_keys = {
        f"{partition}:{symbol}"
        for partition in ("development", "validation")
        for symbol in plan.symbols
    }
    if {item["group"] for item in verified} != expected_keys:
        raise ValueError("four-group set is duplicate, incomplete, or substituted")
    verified.sort(key=lambda item: item["group"])
    return {
        "schema": "ctcc.gate3.development_validation_four_group_check.v1",
        "plan_sha256": expected_plan_sha256,
        "manifest_sha256": expected_manifest_sha256,
        "source_artifacts": len(manifest.members),
        "source_rows": manifest.total_rows,
        "groups": verified,
        "holdout_archives_opened": 0,
        "candidate_fitted": False,
        "point_in_time_provenance_qualified": False,
        "predictive_oos_eligible": False,
        "execution_authority": False,
        "current_claim": "computational",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--group-output", type=Path, action="append", required=True)
    parser.add_argument("--expected-summary-sha256", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify_four_groups(
        plan_path=args.plan,
        expected_plan_sha256=args.expected_plan_sha256,
        manifest_path=args.manifest,
        expected_manifest_sha256=args.expected_manifest_sha256,
        group_outputs=tuple(args.group_output),
        expected_summary_sha256s=tuple(args.expected_summary_sha256),
    )
    payload = (
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with args.output.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if args.output.read_bytes() != payload:
        raise ValueError("four-group report readback differs")
    print("MIE_DEVELOPMENT_VALIDATION_GROUPS_VERIFIED=4")
    print("MIE_PREDICTIVE_OOS_ELIGIBLE=0")


if __name__ == "__main__":
    main()
