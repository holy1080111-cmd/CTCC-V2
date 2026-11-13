"""Offline export contract using the existing synthetic owned Global journal."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.routers.analysis import _analyze_imported_snapshot
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import sha
from app.trade_qualification import global_public_runtime_v1 as public
from scripts import export_public_analysis_snapshot as export
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    market as market,  # noqa: PLC0414 -- register the existing synthetic fixture.
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    owned_global as owned_global,  # noqa: PLC0414 -- register the existing synthetic fixture.
)
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_qualification_public_market_collector import policy


async def _captured(owned_global, tmp_path, monkeypatch):
    directory, harness = owned_global
    original = harness.public

    def server(request):
        rows = original(request)
        if request.url.path.endswith("/ticker"):
            rows[0].update(
                vol24h="987654321.010",
                volCcy24h="0.0012300",
                volCcyQuote="123456789",
            )
        if request.url.path.endswith("/funding-rate"):
            row = rows[0]
            upcoming = int(row["nextFundingTime"])
            row.update(
                fundingTime=str(upcoming),
                nextFundingTime=str(upcoming + 8 * 60 * 60 * 1000),
                method="current_period",
                formulaType="withRate",
                minFundingRate="-0.00375",
                maxFundingRate="0.00375",
                settState="processing",
                settFundingRate="0",
                nextFundingRate="",
            )
        return rows

    harness.public = server
    monkeypatch.setattr(
        public.ws, "PUBLIC_WS_URL", "wss://ws.okx.com:8443/ws/v5/public"
    )
    result = await public.capture_initial_global_public_market_v1(
        tmp_path / "journal",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(counts=(240,) * 4),
    )
    assert result.code == "initial_global_public_captured"
    assert result.plan_sha256 and result.journal_sha256
    return directory, harness, result


def _replay_args(tmp_path, result):
    return SimpleNamespace(
        instrument="BTC-USDT-SWAP",
        journal=tmp_path / "journal",
        output=tmp_path / "export",
        mode="replay",
        plan_sha256=result.plan_sha256,
        journal_sha256=result.journal_sha256,
    )


@pytest.mark.asyncio
async def test_replay_exports_importable_snapshot_without_new_public_requests(
    owned_global, tmp_path, monkeypatch
):
    journal, harness, captured = await _captured(owned_global, tmp_path, monkeypatch)
    output = MemoryDirectory({})
    original_context = export.public_runtime_journal._root_context
    checked_roots = []
    args = _replay_args(tmp_path, captured)

    @contextmanager
    def routed(path):
        if path == args.output.absolute():
            yield output
        else:
            with original_context(path) as directory:
                yield directory

    monkeypatch.setattr(export.public_runtime_journal, "_root_context", routed)

    def checked(path):
        checked_roots.append(path)
        return path.absolute()

    monkeypatch.setattr(export, "_checked_root", checked)
    requests_before = len(harness.requests)
    receipt = export._export(args)
    assert checked_roots == [args.journal, args.output]
    assert len(harness.requests) == requests_before
    assert journal.content["plan.json"] and journal.content["summary.json"]
    assert set(output.content) == {"MarketSnapshot.json", "snapshot-receipt.json"}
    raw = output.content["MarketSnapshot.json"]
    market = MarketSnapshot.model_validate_json(raw, strict=True, extra="forbid")
    imported = _analyze_imported_snapshot(raw, "public-snapshot-export")
    assert receipt["snapshot_sha256"] == imported.input_sha256 == sha(raw)
    assert market.instrument_id == receipt["instrument_id"] == "BTC-USDT-SWAP"
    assert set(market.candles) == set(market.quality) == {"4H", "1H", "15m", "5m"}
    assert imported.analysis.trade_ready is False
    assert "imported_snapshot_unverified" in imported.analysis.blockers
    assert receipt["source_authenticity_verified"] is False
    assert receipt["execution_authority"] is False
    harness.assert_closed()


@pytest.mark.asyncio
async def test_existing_output_refuses_before_replay_or_write(
    owned_global, tmp_path, monkeypatch
):
    _, harness, captured = await _captured(owned_global, tmp_path, monkeypatch)
    output = MemoryDirectory({"existing.json": b"original"})
    args = _replay_args(tmp_path, captured)
    monkeypatch.setattr(export, "_checked_root", lambda path: path.absolute())

    @contextmanager
    def output_only(path):
        assert path == args.output.absolute()
        yield output

    monkeypatch.setattr(export.public_runtime_journal, "_root_context", output_only)
    with pytest.raises(
        export.SnapshotExportRefused, match="public_snapshot_output_unavailable"
    ):
        export._export(args)
    assert output.content == {"existing.json": b"original"}
    harness.assert_closed()


def test_root_gate_and_separate_directory_refusal(tmp_path, monkeypatch):
    seen = []

    def trusted(path):
        seen.append(path)
        return Path(path)

    monkeypatch.setattr(export, "require_private_evidence_base_v2", trusted)
    assert export._checked_root(tmp_path) == tmp_path
    assert seen == [str(tmp_path.absolute())]
    args = SimpleNamespace(journal=tmp_path, output=tmp_path / "child", mode="replay")
    monkeypatch.setattr(export, "_checked_root", lambda path: path.absolute())
    with pytest.raises(
        export.SnapshotExportRefused,
        match="public_snapshot_separate_directories_required",
    ):
        export._export(args)


def test_output_readback_failure_never_reports_export():
    class ChangedDirectory(MemoryDirectory):
        def read(self, name, maximum):
            return (
                b"changed"
                if name == "MarketSnapshot.json"
                else super().read(name, maximum)
            )

    output = ChangedDirectory({})
    receipt = {"schema": "ctcc.local_analysis.public_snapshot_export.v1"}
    with pytest.raises(
        export.SnapshotExportRefused, match="public_snapshot_output_readback"
    ):
        export._write_export(output, b"{}", receipt)
    assert "snapshot-receipt.json" not in output.content


def test_wrong_sha_arguments_are_rejected_before_any_root():
    with pytest.raises(SystemExit):
        export._arguments(
            [
                "--instrument",
                "BTC-USDT-SWAP",
                "--journal",
                "/tmp/journal",
                "--output",
                "/tmp/output",
                "replay",
                "--plan-sha256",
                "A" * 64,
                "--journal-sha256",
                "0" * 64,
            ]
        )
