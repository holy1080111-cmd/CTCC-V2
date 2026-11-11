"""Operator-mode dispatch, archive admission, and no-credential output."""

from __future__ import annotations

import io
import json
import ssl
import tarfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database.repositories.public_runtime_bundle_custody import _closure
from app.domain.source_primitives import sha
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
    verify_prospective_devval_campaign_v1,
)
from scripts import devval_campaign_source as source
from scripts import manifest
from scripts import run_devval_campaign as command
from tests.unit.exchange.test_live_account_config_custody_v1 import _private_root_path
from tests.unit.mie.test_prospective_devval_campaign_plan_v1 import plan_input

_PIN = "d" * 64
_CHECKPOINT = {
    "expected_source_commit": "a" * 40,
    "expected_source_tree": "b" * 40,
    "expected_source_manifest_sha256": "c" * 64,
}
_BASE = ["--source-archive", "exact.tar", "--source-archive-sha256", "e" * 64]
_DB = [
    "--ca-file",
    "trust.pem",
    "--database-oid",
    "101",
    "--login-oid",
    "103",
    "--publisher-oid",
    "102",
]
_CAMPAIGN = ["--campaign", "campaign.json", "--campaign-sha256", _PIN]


def _archive(root: Path, path: Path) -> str:
    with tarfile.open(
        path,
        "w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "a" * 40},
    ) as stream:
        for entry in sorted(root.iterdir()):
            raw = entry.read_bytes()
            item = tarfile.TarInfo(entry.name)
            item.size = len(raw)
            stream.addfile(item, io.BytesIO(raw))
    return sha(path.read_bytes())


def test_source_checkpoint_checks_exact_extraction_and_refuses_extra(tmp_path):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    (root / "pyproject.toml").write_bytes(b"[project]\nname='fixture'\n")
    manifest.write_manifest(root, root / "MANIFEST.sha256")
    archive = tmp_path.resolve() / "source.tar"
    pin = _archive(root, archive)
    checkpoint = source.verify_source_checkpoint(root, archive, pin)
    assert checkpoint["expected_source_commit"] == "a" * 40
    assert len(checkpoint["expected_source_tree"]) == 40
    assert checkpoint["expected_source_manifest_sha256"] == sha(
        (root / "MANIFEST.sha256").read_bytes()
    )
    with pytest.raises(source.DevvalSourceCheckpointError, match="checkpoint_invalid"):
        source.verify_source_checkpoint(root, archive, "0" * 64)
    (root / ".env").write_bytes(b"fixture-configuration")
    with pytest.raises(source.DevvalSourceCheckpointError, match="checkpoint_invalid"):
        source.verify_source_checkpoint(root, archive, pin)
    archive_with_configuration = archive.with_name("source-with-config.tar")
    other_pin = _archive(root, archive_with_configuration)
    with pytest.raises(source.DevvalSourceCheckpointError, match="checkpoint_invalid"):
        source.verify_source_checkpoint(root, archive_with_configuration, other_pin)
    (root / ".env").unlink()
    (root / "pyproject.toml").write_bytes(b"[project]\nname='tamperd'\n")
    with pytest.raises(source.DevvalSourceCheckpointError, match="checkpoint_invalid"):
        source.verify_source_checkpoint(root, archive, pin)


def test_freeze_writes_original_campaign_once_without_database(
    monkeypatch, tmp_path, capsys
):
    base = _private_root_path(tmp_path)
    plan = base / "plan.json"
    plan.write_text(
        ProspectiveDevvalCampaignPlanV1(**plan_input()).model_dump_json(),
        encoding="utf-8",
    )
    output = base / "campaign.json"
    monkeypatch.setattr(command, "verify_source_checkpoint", lambda *_: _CHECKPOINT)
    monkeypatch.setattr(command, "_factories", lambda *_: pytest.fail("database"))
    args = ["freeze", *_BASE, "--plan", str(plan), "--output", str(output)]
    assert command.main(args) == 0
    response = json.loads(capsys.readouterr().out)
    raw = output.read_bytes()
    assert response["campaign_sha256"] == sha(raw)
    assert response["planned_rows"] == 20_160 and not response["execution_authority"]
    assert (
        verify_prospective_devval_campaign_v1(
            raw, expected_campaign_sha256=sha(raw), **_CHECKPOINT
        ).plan.expected_rows
        == 20_160
    )
    assert command.main(args) == 1
    assert output.read_bytes() == raw
    assert not capsys.readouterr().out


def test_bad_operator_arguments_do_not_echo_values_or_load_source(monkeypatch, capsys):
    monkeypatch.setattr(
        command, "verify_source_checkpoint", lambda *_: pytest.fail("source loaded")
    )
    assert command.main(["freeze", *_BASE, "--bad", "fixture-private-value"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.strip() == "devval_campaign_command_invalid"


def test_candidate_options_require_complete_replay_and_study_options(
    monkeypatch, tmp_path
):
    root = tmp_path / "capture"
    study_output = tmp_path / "study"
    candidate_output = tmp_path / "candidate"
    replay = [
        "replay",
        *_BASE,
        *_DB,
        "--campaign-sha256",
        _PIN,
        "--completion-sha256",
        "f" * 64,
        "--root",
        str(root),
        "--observer-oid",
        "104",
    ]
    study = [
        "--study-plan",
        "study.json",
        "--study-plan-sha256",
        "8" * 64,
        "--study-output-root",
        str(study_output),
    ]
    candidate = [
        "--candidate-plan",
        "candidate.json",
        "--candidate-plan-sha256",
        "7" * 64,
        "--legacy-scores",
        "legacy.json",
        "--candidate-output-root",
        str(candidate_output),
    ]
    assert command._arguments([*replay, *study]).candidate_plan is None
    parsed = command._arguments([*replay, *study, *candidate])
    assert parsed.candidate_plan == Path("candidate.json")
    assert parsed.candidate_output_root == candidate_output
    for invalid in (
        [*replay, *candidate],
        [*replay, *study[:-2], *candidate],
        [*replay, *study, *candidate[:-2]],
        [*replay, *study, *candidate[:2], *candidate[4:]],
        ["freeze", *_BASE, "--plan", "plan.json", "--output", "out.json", *candidate],
    ):
        with pytest.raises(command.CampaignCommandError, match="command_invalid"):
            command._arguments(invalid)

    monkeypatch.setattr(command, "_read", lambda *_: pytest.fail("candidate file read"))
    parsed.candidate_output_root = candidate_output / ".." / study_output.name
    with pytest.raises(command.CampaignCommandError, match="candidate_output_conflict"):
        command._candidate_inputs(parsed)


@pytest.mark.asyncio
async def test_native_factory_pins_tls_and_port_and_ignores_implicit_secrets(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        command._CAMPAIGN_ENV, "postgresql+asyncpg://observer@fixture.invalid/evidence"
    )
    monkeypatch.setenv("PGPASSWORD", "fixture-implicit-password")
    monkeypatch.setenv("PGPORT", "6543")
    keylog = tmp_path / "keylog.txt"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setattr(
        ssl.SSLContext, "load_verify_locations", lambda *_a, **_kw: None
    )
    args = command._arguments(["observe", *_BASE, *_DB, *_CAMPAIGN])
    async with command._factories(args) as (repo, others):
        params = _closure(repo._engine.sync_engine.pool._creator)["cparams"]
        assert repo._role == "observer" and not others
        assert params["password"] == "" and params["port"] == 5432
        tls = params["ssl"]
        assert tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname
        assert tls.keylog_filename is None and not keylog.exists()


@pytest.mark.asyncio
async def test_bad_campaign_fails_before_loading_a_database(monkeypatch):
    monkeypatch.setattr(command, "_read", lambda *_: b"{}")
    monkeypatch.setattr(command, "_factories", lambda *_: pytest.fail("database"))
    args = command._arguments(["publish", *_BASE, *_DB, *_CAMPAIGN])
    with pytest.raises(ValueError):
        await command._run(args, _CHECKPOINT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "publish_batch"), (("collect", False), ("replay", False), ("replay", True))
)
async def test_collection_and_readonly_replay_use_different_entry_points(
    monkeypatch, tmp_path, mode, publish_batch
):
    root = tmp_path.resolve()
    repository = object()
    factories = (object(), object(), object())
    events = []
    artifact = SimpleNamespace(
        payload=b"offline computational batch fixture",
        sha256=sha(b"offline computational batch fixture"),
        contract=SimpleNamespace(
            campaign_sha256=_PIN,
            completion_sha256="f" * 64,
            row_inventory_sha256="0" * 64,
        ),
    )

    @asynccontextmanager
    async def runtime(args):
        assert args.mode == mode
        yield repository, factories

    async def collect(**kwargs):
        assert kwargs["campaign_payload"] == b"fixture"
        assert kwargs["custody_session_factory"] is factories[2]
        check(kwargs)
        events.append("collect")
        return SimpleNamespace(receipt_sha256="f" * 64, row_count=20_160)

    async def replay(**kwargs):
        assert kwargs["custody_repository"] is factories[2]
        assert kwargs["expected_completion_sha256"] == "f" * 64
        assert kwargs["include_batch"] is publish_batch
        check(kwargs)
        events.append("replay")
        return SimpleNamespace(
            completion_sha256="f" * 64,
            row_inventory_sha256="0" * 64,
            development_rows=10_080,
            validation_rows=10_080,
            batch=artifact if publish_batch else None,
        )

    def check(kwargs):
        assert kwargs["root"] == root and kwargs["publication_repository"] is repository
        assert kwargs["expected_campaign_sha256"] == _PIN
        assert kwargs["expected_source_tree"] == _CHECKPOINT["expected_source_tree"]
        assert kwargs["witness_repository"].session_factory is factories[0]
        assert kwargs["observation_repository"].session_factory is factories[1]

    monkeypatch.setattr(command, "_factories", runtime)
    monkeypatch.setattr(command, "_read", lambda *_: b"fixture")
    monkeypatch.setattr(
        command, "verify_prospective_devval_campaign_v1", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(
        command, "RuntimeBundleCustodyRepository", lambda factory: factory
    )
    monkeypatch.setattr(command, "run_prospective_devval_campaign_v1", collect)
    monkeypatch.setattr(command, "replay_prospective_devval_campaign_v1", replay)
    extra = (
        _CAMPAIGN
        if mode == "collect"
        else ["--campaign-sha256", _PIN, "--completion-sha256", "f" * 64]
    )
    if publish_batch:
        output_root = _private_root_path(tmp_path)
        extra += ["--batch-output-root", str(output_root)]
    args = command._arguments(
        [mode, *_BASE, *_DB, *extra, "--root", str(root), "--observer-oid", "104"]
    )
    result = await command._run(args, _CHECKPOINT)
    assert events == [mode] and result["completion_sha256"] == "f" * 64
    if publish_batch:
        assert result["batch_sha256"] == artifact.sha256
        assert (output_root / "batch-v1.json").read_bytes() == artifact.payload
        receipt = (output_root / "receipt-v1.json").read_bytes()
        assert result["batch_publication_sha256"] == sha(receipt)
        assert json.loads(receipt)["row_inventory_sha256"] == "0" * 64
        with pytest.raises(command.CampaignCommandError, match="output_not_empty"):
            command._publish_batch(output_root, artifact)
        assert (output_root / "batch-v1.json").read_bytes() == artifact.payload


@pytest.mark.asyncio
async def test_study_plan_pin_is_checked_before_custody_and_study_is_published(
    monkeypatch, tmp_path
):
    output = _private_root_path(tmp_path)
    args = command._arguments(
        [
            "replay",
            *_BASE,
            *_DB,
            "--campaign-sha256",
            _PIN,
            "--completion-sha256",
            "f" * 64,
            "--root",
            str(tmp_path),
            "--observer-oid",
            "104",
            "--study-plan",
            "study.json",
            "--study-plan-sha256",
            "8" * 64,
            "--study-output-root",
            str(output),
        ]
    )
    plan = SimpleNamespace(campaign_sha256=_PIN, canonical_sha256=lambda: "7" * 64)
    monkeypatch.setattr(command, "_read", lambda *_: b"{}")
    monkeypatch.setattr(
        command,
        "DevvalCampaignStudyPlanV1",
        SimpleNamespace(model_validate_json=lambda raw: plan),
    )
    monkeypatch.setattr(command, "_factories", lambda *_: pytest.fail("database"))
    with pytest.raises(command.CampaignCommandError, match="study_plan_changed"):
        await command._run(args, _CHECKPOINT)
    assert not list(output.iterdir())

    batch = object()
    events = []

    @asynccontextmanager
    async def runtime(_args):
        yield object(), (object(), object(), object())
        events.append("database_closed")

    async def replay(**kwargs):
        assert kwargs["include_batch"] is True
        events.append("source_replayed")
        return SimpleNamespace(
            batch=batch,
            completion_sha256="f" * 64,
            row_inventory_sha256="0" * 64,
            development_rows=10_080,
            validation_rows=10_080,
        )

    def prepare(original, *, plan, expected_plan_sha256):
        assert original is batch and plan.canonical_sha256() == expected_plan_sha256
        assert events == ["source_replayed", "database_closed"]
        payload = b"offline study wiring fixture"
        return SimpleNamespace(
            payload=payload,
            sha256=sha(payload),
            contract=SimpleNamespace(
                study_plan_sha256=expected_plan_sha256,
                batch_sha256="a" * 64,
                completion_sha256="f" * 64,
            ),
        )

    plan.canonical_sha256 = lambda: "8" * 64
    monkeypatch.setattr(command, "_factories", runtime)
    monkeypatch.setattr(
        command, "RuntimeBundleCustodyRepository", lambda factory: factory
    )
    monkeypatch.setattr(command, "replay_prospective_devval_campaign_v1", replay)
    monkeypatch.setattr(command, "_prepare_study", prepare)
    result = await command._run(args, _CHECKPOINT)
    assert result["study_sha256"] == sha((output / "study-v1.json").read_bytes())
    receipt = (output / "receipt-v1.json").read_bytes()
    assert result["study_publication_sha256"] == sha(receipt)
    assert json.loads(receipt)["study_plan_sha256"] == "8" * 64
