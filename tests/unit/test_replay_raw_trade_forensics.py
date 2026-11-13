"""Offline receipt CLI with real retained synthetic sources and file operations."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_evidence import raw_forensics
from scripts import replay_raw_trade_forensics as cli
from tests.unit.test_trade_evidence_raw_forensics import fixtures


@pytest.fixture(scope="module")
def synthetic_sources():
    return fixtures()


@pytest.fixture
def workspace(tmp_path, monkeypatch, synthetic_sources):
    # Destination-specific ACLs differ on Windows and POSIX. Exercise the real
    # native I/O here and isolate only host ACL validation.
    monkeypatch.setattr(cli, "require_private_evidence_base_v2", Path)
    monkeypatch.setattr(cli, "_windows_private", lambda _: None)
    submission, operands = synthetic_sources
    files = {
        "submission.json": submission,
        "account.json": operands["account_bytes"],
        "attribution.json": operands["attribution_bytes"],
        "holding-path.json": operands["holding_path_bytes"],
    }
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    pins = {
        "schema": cli._SCHEMA,
        "submission_report_sha256": operands["expected_submission_sha256"],
        "account_packet_sha256": operands["expected_account_sha256"],
        "account_plan_sha256": operands["expected_account_plan_sha256"],
        "attribution_sha256": operands["expected_attribution_sha256"],
        "holding_path_sha256": sha(operands["holding_path_bytes"]),
    }
    raw = canonical(pins)
    (tmp_path / "source-pins.json").write_bytes(raw)
    return tmp_path, raw


def _arguments(workspace, *mode):
    root, pins = workspace
    return ["--source-directory", str(root), "--source-pins-sha256", sha(pins), *mode]


def test_create_reopen_verify_and_refuse_overwrite(
    workspace, capsys, synthetic_sources
):
    root, _ = workspace
    body, operands = synthetic_sources
    expected = raw_forensics.reconstruct_trade_forensics(body, **operands)
    assert cli.main(_arguments(workspace, "--reconstruct")) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["receipt_sha256"] == expected.receipt_sha256
    assert response["admission"] == "DENY"
    assert (
        response["execution_authority"]
        is response["real_trade_sample_verified"]
        is False
    )
    assert "account_id" not in response and "analysis" not in response
    receipt = root / "forensics-receipt.json"
    original = receipt.read_bytes()
    assert original == expected.canonical_receipt
    assert (
        cli.main(_arguments(workspace, "--verify-receipt-sha256", sha(original))) == 0
    )
    assert json.loads(capsys.readouterr().out) == response
    assert cli.main(_arguments(workspace, "--reconstruct")) == 1
    refusal = capsys.readouterr()
    assert not refusal.out
    assert refusal.err == '{"admission":"DENY","status":"REFUSED"}\n'
    assert receipt.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    ["digest", "canonical", "duplicate", "account", "holding", "wrong-plan", "alias"],
)
def test_pinning_and_original_bytes_refuse_without_publication(
    workspace, capsys, change
):
    root, pin_raw = workspace
    pins = json.loads(pin_raw)
    if change == "digest":
        (root / "source-pins.json").write_bytes(pin_raw + b"\n")
    if change == "canonical":
        pin_raw += b"\n"
    if change == "duplicate":
        pin_raw = b'{"schema":"fake",' + pin_raw[1:]
    if change == "account":
        (root / "account.json").write_bytes(b"changed private account")
    if change == "holding":
        (root / "holding-path.json").unlink()
    if change == "wrong-plan":
        pins["account_plan_sha256"] = "b" * 64
        pin_raw = canonical(pins)
    if change == "alias":
        os.link(root / "submission.json", root / "second-link.json")
    if change != "digest":
        (root / "source-pins.json").write_bytes(pin_raw)
    assert cli.main(_arguments((root, pin_raw), "--reconstruct")) == 1
    refusal = capsys.readouterr()
    assert not refusal.out
    assert refusal.err == '{"admission":"DENY","status":"REFUSED"}\n'
    assert not (root / "forensics-receipt.json").exists()


def test_forged_receipt_with_new_digest_cannot_substitute(workspace, capsys):
    root, pins = workspace
    digest = cli.replay_raw_trade_forensics(root, source_pins_sha256=sha(pins))
    receipt = root / "forensics-receipt.json"
    assert sha(receipt.read_bytes()) == digest
    forged = json.loads(receipt.read_bytes())
    forged["real_trade_sample_verified"] = True
    raw = canonical(forged)
    receipt.write_bytes(raw)
    assert cli.main(_arguments(workspace, "--verify-receipt-sha256", sha(raw))) == 1
    assert not capsys.readouterr().out
    assert receipt.read_bytes() == raw


def test_late_source_change_before_readback_leaves_receipt_but_refuses(
    workspace, monkeypatch
):
    root, pins = workspace
    original = cli._sources
    reads = []

    def mutate_on_reopen(directory, pin):
        reads.append(directory.path)
        if len(reads) == 2:
            (root / "attribution.json").write_bytes(b"changed after publication")
        return original(directory, pin)

    monkeypatch.setattr(cli, "_sources", mutate_on_reopen)
    with pytest.raises(
        cli.ForensicReplayRefused, match="^raw_trade_forensics_offline_refused$"
    ):
        cli.replay_raw_trade_forensics(root, source_pins_sha256=sha(pins))
    assert reads == [root, root]
    assert (root / "forensics-receipt.json").exists()


def test_invalid_cli_option_never_echoes_supplied_text():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "scripts.replay_raw_trade_forensics",
            "--private_account_value",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert result.stderr == '{"admission":"DENY","status":"REFUSED"}\n'
