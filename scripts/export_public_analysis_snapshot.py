"""Capture or reopen an anonymous public journal and export a local-analysis snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from app.api.routers.analysis import _analyze_imported_snapshot
from app.domain.source_primitives import MAX_RAW, canonical, sha
from app.public_market_source import public_runtime_journal
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_qualification import (
    candle_collector,
    market_aux_collector,
    quote_collector,
    ws_collector,
)
from app.trade_qualification import global_public_runtime_v1 as public
from app.trade_qualification.global_public_runtime_v2 import (
    matches_global_public_market_identity_v2,
    project_global_public_market_snapshot_v2,
)
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy

_TIME_FRAMES = frozenset(candle_collector.TIMEFRAMES)
_MAX_AGE_SECONDS = 5
# Anonymous V1 uses a shared age setting for its three REST quote components.
# Keep ticker/mark at five seconds in the export check and cap funding data
# return at V1's sixty-second maximum. The trading policies are separate.
_FUNDING_DATA_RETURN_AGE_SECONDS = 60


class SnapshotExportRefused(ValueError):
    pass


def _checked_root(path: Path) -> Path:
    return require_private_evidence_base_v2(str(path.absolute()))


def _capture_policy() -> PublicMarketCollectionPolicy:
    return PublicMarketCollectionPolicy(
        quote=quote_collector.QuoteCollectionPolicy(
            max_age_seconds=_FUNDING_DATA_RETURN_AGE_SECONDS
        ),
        candles=candle_collector.CandleCollectionPolicy(
            requests=tuple(
                candle_collector.FrameRequest(
                    timeframe=frame, requested_confirmed_bars=240
                )
                for frame in candle_collector.TIMEFRAMES
            ),
        ),
        market_aux=market_aux_collector.MarketAuxCollectionPolicy(
            max_age_seconds=_MAX_AGE_SECONDS
        ),
        ws=ws_collector.WSCollectionPolicy(max_age_seconds=_MAX_AGE_SECONDS),
    )


def _snapshot(reopened, instrument: str) -> tuple[bytes, dict]:
    packet = reopened.packet
    if (
        reopened.code != "initial_global_public_replayed"
        or type(packet) is not public.GlobalPublicComponentPacketV1
        or packet.instrument_id != instrument
        or reopened.admission != "DENY"
        or reopened.execution_authority is not False
    ):
        raise SnapshotExportRefused("public_snapshot_journal_incomplete")
    for row in packet.quote.provenance:
        maximum_age = (
            _FUNDING_DATA_RETURN_AGE_SECONDS
            if row.role == "funding"
            else _MAX_AGE_SECONDS
        )
        if not (
            timedelta(0)
            <= packet.completed_at - row.source_time
            <= timedelta(seconds=maximum_age)
        ) or not (
            timedelta(0)
            <= packet.completed_at - row.received_at
            <= timedelta(seconds=_MAX_AGE_SECONDS)
        ):
            raise SnapshotExportRefused("public_snapshot_quote_stale")
    market, _ = project_global_public_market_snapshot_v2(
        packet, evaluated_at=packet.completed_at
    )
    if not (
        matches_global_public_market_identity_v2(market, instrument)
        and _TIME_FRAMES == set(market.candles) == set(market.quality)
        and packet.started_at <= market.received_at <= packet.completed_at
    ):
        raise SnapshotExportRefused("public_snapshot_projection_mismatch")
    # Validate the exact Pydantic wire bytes with the real local importer.
    raw = market.model_dump_json().encode("utf-8")
    checked = _analyze_imported_snapshot(raw, "public-snapshot-export")
    if checked.input_sha256 != sha(raw) or checked.execution_authority:
        raise SnapshotExportRefused("public_snapshot_import_mismatch")
    return raw, {
        "schema": "ctcc.local_analysis.public_snapshot_export.v1",
        "instrument_id": instrument,
        "environment": packet.environment,
        "as_of": market.received_at.isoformat(),
        "plan_sha256": reopened.plan_sha256,
        "journal_sha256": reopened.journal_sha256,
        "packet_sha256": packet.bundle_sha256,
        "snapshot_sha256": sha(raw),
        "source_authenticity_verified": False,
        "historical_availability_verified": False,
        "execution_authority": False,
    }


def _write_export(directory, snapshot: bytes, receipt: dict) -> None:
    receipt_raw = canonical(receipt)
    if directory.names() or not 0 < len(snapshot) <= MAX_RAW:
        raise SnapshotExportRefused("public_snapshot_output_unavailable")
    for name, payload in (
        ("MarketSnapshot.json", snapshot),
        ("snapshot-receipt.json", receipt_raw),
    ):
        directory.publish(name, payload)
        if directory.read(name, MAX_RAW) != payload:
            raise SnapshotExportRefused("public_snapshot_output_readback")
    if sorted(directory.names()) != ["MarketSnapshot.json", "snapshot-receipt.json"]:
        raise SnapshotExportRefused("public_snapshot_output_readback")


def _sha256(value: str) -> str:
    if re.fullmatch("[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("expected lowercase SHA-256")
    return value


def _arguments(arguments):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--instrument",
        choices=tuple(sorted(public_runtime_journal.GLOBAL_INSTRUMENTS)),
        required=True,
    )
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_subparsers(dest="mode", required=True)
    mode.add_parser("capture", help="Make one anonymous capture; no automatic retry.")
    replay = mode.add_parser(
        "replay", help="Export an already captured journal without network."
    )
    replay.add_argument("--plan-sha256", type=_sha256, required=True)
    replay.add_argument("--journal-sha256", type=_sha256, required=True)
    return parser.parse_args(arguments)


def _export(args) -> dict:
    journal_path = _checked_root(args.journal)
    output_path = _checked_root(args.output)
    if (
        output_path == journal_path
        or output_path in journal_path.parents
        or journal_path in output_path.parents
    ):
        raise SnapshotExportRefused("public_snapshot_separate_directories_required")
    with public_runtime_journal._root_context(output_path) as output:
        if output.names():
            raise SnapshotExportRefused("public_snapshot_output_unavailable")
        if args.mode == "capture":
            captured = asyncio.run(
                public.capture_initial_global_public_market_v1(
                    journal_path,
                    instrument_id=args.instrument,
                    market_policy=_capture_policy(),
                )
            )
            if captured.code != "initial_global_public_captured":
                raise SnapshotExportRefused("public_snapshot_capture_incomplete")
            plan_sha256, journal_sha256 = captured.plan_sha256, captured.journal_sha256
        else:
            plan_sha256, journal_sha256 = args.plan_sha256, args.journal_sha256
        reopened = public.read_global_public_capture_v1(
            journal_path,
            expected_plan_sha256=plan_sha256,
            expected_journal_sha256=journal_sha256,
        )
        snapshot, receipt = _snapshot(reopened, args.instrument)
        _write_export(output, snapshot, receipt)
        return receipt


def main(arguments=None) -> int:
    args = _arguments(arguments)
    try:
        receipt = _export(args)
    except SnapshotExportRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- Keep raw and private diagnostics local.
        print("public_snapshot_export_refused", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
