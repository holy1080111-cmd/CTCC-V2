from __future__ import annotations

from pathlib import Path

import pytest

from app.research.external_benchmarks.binance import BinanceKlineCoordinates
from app.research.external_benchmarks.evidence_io import (
    ExternalEvidenceIOError,
    read_contract_json,
    write_contract_json,
)


def test_evidence_json_is_no_clobber_and_idempotent(tmp_path: Path) -> None:
    model = BinanceKlineCoordinates(
        symbol="BTCUSDT",
        interval="1m",
        day="2024-01-01",
    )
    assert write_contract_json(tmp_path, "evidence/spec.json", model) == "written"
    assert (
        write_contract_json(tmp_path, "evidence/spec.json", model)
        == "already_present"
    )
    loaded = read_contract_json(
        tmp_path,
        "evidence/spec.json",
        BinanceKlineCoordinates,
    )
    assert loaded == model

    different = model.model_copy(update={"symbol": "ETHUSDT"})
    with pytest.raises(ExternalEvidenceIOError, match="differs"):
        write_contract_json(tmp_path, "evidence/spec.json", different)


def test_evidence_paths_reject_traversal_and_symlinks(tmp_path: Path) -> None:
    model = BinanceKlineCoordinates(
        symbol="BTCUSDT",
        interval="1m",
        day="2024-01-01",
    )
    with pytest.raises(ExternalEvidenceIOError, match="safe relative"):
        write_contract_json(tmp_path, "../escape.json", model)

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ExternalEvidenceIOError, match="symlink"):
        write_contract_json(tmp_path, "link/escape.json", model)


def test_read_does_not_create_missing_evidence_directories(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExternalEvidenceIOError, match="parent does not exist"):
        read_contract_json(
            tmp_path,
            "missing/spec.json",
            BinanceKlineCoordinates,
        )

    assert not (tmp_path / "missing").exists()
