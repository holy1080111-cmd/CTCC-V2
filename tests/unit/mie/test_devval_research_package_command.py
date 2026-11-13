"""Exercise the offline package command on a real private evidence directory."""

import json
from pathlib import Path

import pytest

from app.domain.source_primitives import sha
from app.mie.validation.prospective_devval_research_package_v1 import (
    verify_devval_research_package_v1,
)
from scripts import compile_devval_research_package as command
from tests.unit.exchange.test_live_account_config_custody_v1 import _private_root_path
from tests.unit.mie.test_devval_research_package_v1 import selections  # noqa: F401


@pytest.fixture
def command_files(tmp_path, monkeypatch, selections):  # noqa: F811
    inputs, _, _ = selections
    output = _private_root_path(tmp_path)
    values = {
        "blueprint": inputs.blueprint_bytes,
        "scorer-config": inputs.scorer_config_bytes,
        "calibration-policy": inputs.calibration_policy_bytes,
        "market-policy": inputs.market_policy.model_dump_json().encode("utf-8"),
    }
    for name, content in values.items():
        (tmp_path / f"{name}.json").write_bytes(content)
    args = ["--source-archive", str(tmp_path / "retained-source.tar")]
    for name in values:
        args.extend((f"--{name}", str(tmp_path / f"{name}.json")))
    for name in ("source-archive", "blueprint", "calibration-policy", "market-policy"):
        args.extend(
            (
                f"--{name}-sha256",
                getattr(inputs, f"expected_{name.replace('-', '_')}_sha256"),
            )
        )
    args.extend(("--output-directory", str(output), "--decision-delay-seconds", "60"))

    def checkpoint(root, archive, expected_sha256):
        assert root == Path(command.__file__).resolve().parents[1]
        assert archive == tmp_path / "retained-source.tar"
        assert expected_sha256 == inputs.expected_source_archive_sha256
        source = inputs.expected_source_checkpoint
        return {
            "expected_source_commit": source.commit,
            "expected_source_tree": source.tree,
            "expected_source_manifest_sha256": source.manifest_sha256,
        }

    monkeypatch.setattr(command, "verify_source_checkpoint", checkpoint)
    return args, output, tmp_path, inputs


def test_private_no_clobber_canonical_research_package(command_files, capsys):
    args, output, _, inputs = command_files
    assert command.main(args) == 0
    identity = json.loads(capsys.readouterr().out)
    originals = {file.name: file.read_bytes() for file in output.iterdir()}
    assert set(originals) == {
        "campaign.json",
        "companion_schedule.json",
        "package.json",
    }
    for artifact, payload in originals.items():
        assert sha(payload) == identity[artifact.removesuffix(".json") + "_sha256"]
    for flag in (
        "pre_window_publication_verified",
        "independent_availability_verified",
        "predictive_oos_eligible",
        "execution_authority",
    ):
        assert identity[flag] is False
    rebuilt = verify_devval_research_package_v1(
        originals["package.json"],
        expected_package_sha256=identity["package_sha256"],
        inputs=inputs,
    )
    assert rebuilt.campaign.payload == originals["campaign.json"]
    assert rebuilt.companion_schedule.payload == originals["companion_schedule.json"]

    assert command.main(args) == 1
    assert capsys.readouterr().out == ""
    assert {file.name: file.read_bytes() for file in output.iterdir()} == originals


@pytest.mark.parametrize("mutation", ("wrong_pin", "duplicate_field"))
def test_bad_typed_market_policy_fails_before_any_publish(
    command_files, capsys, mutation
):
    args, output, parent, inputs = command_files
    path = parent / "market-policy.json"
    if mutation == "duplicate_field":
        original = path.read_text(encoding="utf-8")
        changed = original.replace(
            '"total_timeout_seconds":30',
            '"total_timeout_seconds":1,"total_timeout_seconds":30',
        )
        assert changed != original
    else:
        changed = inputs.market_policy.model_copy(
            update={"total_timeout_seconds": 31}
        ).model_dump_json()
    path.write_text(changed, encoding="utf-8")
    assert command.main(args) == 1
    captured = capsys.readouterr()
    assert captured.err == "devval_research_package_command_refused\n"
    assert captured.out == ""
    assert list(output.iterdir()) == []


def test_partial_output_remains_on_late_failure_and_retry_is_refused(
    command_files, monkeypatch, capsys
):
    args, output, _, _ = command_files
    retain = command._retain

    def filesystem_failure(directory, name, raw, maximum):
        if name == "companion_schedule.json":
            raise OSError("internal path must be redacted")
        return retain(directory, name, raw, maximum)

    with monkeypatch.context() as patch:
        patch.setattr(command, "_retain", filesystem_failure)
        assert command.main(args) == 1
    captured = capsys.readouterr()
    assert captured.err == "devval_research_package_command_refused\n"
    assert captured.out == ""
    original_campaign = (output / "campaign.json").read_bytes()
    assert original_campaign
    assert command.main(args) == 1
    assert {file.name: file.read_bytes() for file in output.iterdir()} == {
        "campaign.json": original_campaign
    }
