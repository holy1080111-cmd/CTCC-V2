"""Redaction and permission boundaries for the standalone owner diagnostic."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.source_primitives import canonical, sha
from scripts import native_owner_target_plan_0056_v1 as subject
from tests.unit.test_native_owner_grant_0056_draft_v1 import _pins


def _inputs():
    pins = _pins()
    return subject.OwnerTargetPlanInputsV1(
        subject.OwnerUpgradePinsV1(
            pins.source_commit, pins.source_tree, pins.source_archive_sha256, pins.route
        ),
        pins.writer,
        pins.custody,
        pins.frame_reader,
    )


def _inputs_document():
    data = asdict(_inputs())
    return {"schema": subject._INPUT, **data.pop("source"), **data}


def test_loads_exact_private_reviewed_inputs_and_refuses_duplicate_or_wrong_hash(
    monkeypatch, tmp_path
):
    guard = Mock()
    monkeypatch.setattr(subject, "_require_private_file", guard)
    file = tmp_path / "operator-route.json"
    payload = canonical(_inputs_document())
    file.write_bytes(payload)
    assert subject._load_inputs(file, sha(payload)) == _inputs()
    guard.assert_called_once_with(file)
    with pytest.raises(subject.OwnerTargetPlanRefusal):
        subject._load_inputs(file, "0" * 64)
    duplicate = b'{"writer":"replacement",' + payload[1:]
    file.write_bytes(duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        subject._load_inputs(file, sha(duplicate))


def test_inspection_output_contains_no_route_or_role_names_or_oids():
    pins = _pins()
    receipt = subject._receipt(pins, "0" * 64)
    serialized = json.dumps(receipt)
    for private in (
        pins.route.host,
        pins.route.owner,
        *_inputs().role_names().values(),
    ):
        # The owner may appear in field *names*, never as an emitted value.
        assert json.dumps(private) not in serialized
    assert receipt["target_grant_pins_sha256"] == sha(canonical(asdict(pins)))
    assert (
        receipt["target_grant_pins_sha256"]
        != subject._receipt(replace(pins, writer_oid=12345), "0" * 64)[
            "target_grant_pins_sha256"
        ]
    )
    assert receipt["grants_present"] is receipt["startup_admission"] is False
    assert (
        receipt["external_approval_authenticated"]
        is receipt["execution_authority"]
        is False
    )
    assert receipt["admission"] == "DENY"


def test_private_plan_no_clobber_and_exact_readback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subject, "require_private_evidence_base_v2", lambda path: tmp_path
    )
    monkeypatch.setattr(subject, "_require_private_file", Mock())
    digest = subject._retain_private_plan(tmp_path, _pins())
    path = tmp_path / subject._PRIVATE_PLAN_FILE
    payload = path.read_bytes()
    assert sha(payload) == digest
    assert json.loads(payload) == {
        "schema": subject._PRIVATE_PLAN_SCHEMA,
        "pins": asdict(_pins()),
    }
    with pytest.raises(OSError):
        subject._retain_private_plan(tmp_path, _pins())
    assert path.read_bytes() == payload


@pytest.mark.asyncio
async def test_bad_archive_stops_before_owner_dsn_or_database(monkeypatch):
    invalid = Mock(side_effect=ValueError("private source path"))
    monkeypatch.setattr(
        subject, "require_exact_installed_owner_source_0056_v1", invalid
    )
    prepare = Mock()
    open_engine = Mock()
    monkeypatch.setattr(subject, "prepare_route", prepare)
    monkeypatch.setattr(subject, "create_async_engine", open_engine)
    with pytest.raises(subject.OwnerTargetPlanRefusal, match="target_plan_refused"):
        await subject.observe_reviewed_owner_target_plan_0056_v1(
            _inputs(),
            owner_dsn_file=Path("dsn"),
            ca_file=Path("ca"),
            source_archive_file=Path("archive"),
        )
    prepare.assert_not_called()
    open_engine.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [False, True])
async def test_reopens_owner_route_after_catalog_and_never_calls_grant(
    monkeypatch, change
):
    inputs = _inputs()
    before = SimpleNamespace(
        url="private route",
        tls=object(),
        manifest_sha256=inputs.source.route.source_manifest_sha256,
        migration_sha256="a" * 64,
        route_sha256="b" * 64,
    )
    after = SimpleNamespace(**vars(before))
    if change:
        after.migration_sha256 = "0" * 64
    verify_source = Mock()
    monkeypatch.setattr(
        subject, "require_exact_installed_owner_source_0056_v1", verify_source
    )
    monkeypatch.setattr(subject, "_require_private_file", Mock())
    prepare = Mock(side_effect=(before, after))
    monkeypatch.setattr(subject, "prepare_route", prepare)
    monkeypatch.setattr(
        subject,
        "preflight_owner_route_0056_v1",
        AsyncMock(
            return_value={
                "status": "PASS",
                "route_sha256": before.route_sha256,
                "startup_admission": False,
                "execution_authority": False,
            }
        ),
    )
    destroy = AsyncMock()
    engine = SimpleNamespace(dispose=destroy)
    monkeypatch.setattr(subject, "create_async_engine", Mock(return_value=engine))
    factory = object()
    monkeypatch.setattr(subject, "async_sessionmaker", Mock(return_value=factory))
    target = AsyncMock(return_value=(_pins(), "f" * 64))
    monkeypatch.setattr(subject, "_read_target", target)
    write = AsyncMock(side_effect=AssertionError("owner mutation attempted"))
    monkeypatch.setattr(subject.draft, "_locked_grant", write)
    monkeypatch.setattr(subject.draft, "_commit_once", write)
    options = {
        "owner_dsn_file": Path("dsn"),
        "ca_file": Path("ca"),
        "source_archive_file": Path("archive"),
    }
    if change:
        with pytest.raises(subject.OwnerTargetPlanRefusal):
            await subject.observe_reviewed_owner_target_plan_0056_v1(inputs, **options)
    else:
        receipt = await subject.observe_reviewed_owner_target_plan_0056_v1(
            inputs, **options
        )
        assert receipt["portable_reference_sha256"] == "f" * 64
        assert receipt["portable_reference_matched"] is True
        assert receipt["status"] == "OBSERVED_DENY"
    assert prepare.call_count == 2
    assert verify_source.call_count == (1 if change else 2)
    target.assert_awaited_once_with(factory, inputs)
    destroy.assert_awaited_once()
    write.assert_not_awaited()


def test_cli_exit_codes_and_output_never_echo_inputs(monkeypatch, capsys):
    options = [
        "--inspect-reviewed-owner-target",
        "--reviewed-inputs-file",
        "/private-pin-contents",
        "--reviewed-inputs-sha256",
        "3" * 64,
        "--owner-dsn-file",
        "/private-dsn-contents",
        "--ca-file",
        "/private-ca-contents",
        "--source-archive-file",
        "/private-archive-contents",
    ]
    observer = AsyncMock(return_value=subject._receipt(_pins(), "f" * 64))
    monkeypatch.setattr(subject, "_load_inputs", Mock(return_value=_inputs()))
    monkeypatch.setattr(subject, "observe_reviewed_owner_target_plan_0056_v1", observer)
    assert subject.main(options) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "OBSERVED_DENY"
    assert subject.main([*options, "--role", "private-obsolete-role"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["admission"] == "DENY"
    assert "private" not in captured.out + captured.err
    observer.assert_awaited_once()
