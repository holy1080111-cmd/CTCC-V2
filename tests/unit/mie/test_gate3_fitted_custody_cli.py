"""Pure CLI admission controls; no database or provider is contacted."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.mie.validation import gate3_fitted_candidate_custody_v1 as custody
from app.mie.validation.artifact import FrozenGate3Artifact
from scripts import run_gate3_fitted_custody as command
from scripts.run_gate3_fitted_custody import (
    FittedCustodyCommandError,
    _arguments,
    _database_url,
)
from tests.unit.mie.test_gate3_prospective import valid_prospective_preregistration


def _argv(root, mode="publish"):
    values = [mode]
    for name in (
        "source-archive",
        "binding",
        "batch",
        "study",
        "trials",
        "legacy",
        "preregistration",
        "ca-file",
        "output",
    ):
        values.extend((f"--{name}", str(root / name)))
    for name in (
        "source-archive-sha256",
        "binding-sha256",
        "batch-sha256",
        "study-sha256",
        "trials-sha256",
        "legacy-sha256",
        "preregistration-sha256",
        "ca-sha256",
    ):
        values.extend((f"--{name}", "a" * 64))
    for name, value in (
        ("database-oid", 101),
        ("login-oid", {"publish": 103, "acknowledge": 104}.get(mode, 102)),
        ("publisher-oid", 103),
        ("observer-oid", 104),
        ("evaluator-oid", 105),
        ("database-host", "custody.example"),
        ("database-port", 5432),
        ("database-name", "evidence"),
        ("login-name", "custodian"),
    ):
        values.extend((f"--{name}", str(value)))
    if mode == "observe":
        values.extend(("--ack-sha256", "b" * 64))
    return values


def test_only_explicit_custody_modes_are_admitted(tmp_path):
    for mode in ("publish", "acknowledge", "observe", "recover-ack"):
        assert _arguments(_argv(tmp_path, mode)).mode == mode
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(_argv(tmp_path, "reserve"))


def test_observe_requires_independent_ack_pin(tmp_path):
    values = _argv(tmp_path, "observe")
    del values[-2:]
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(values)
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(_argv(tmp_path) + ["--ack-sha256", "b" * 64])


def test_recovery_requires_separate_reader_and_excludes_ack_pin(tmp_path):
    values = _argv(tmp_path, "recover-ack")
    with pytest.raises(FittedCustodyCommandError, match="command_invalid"):
        _arguments(values + ["--ack-sha256", "b" * 64])
    values[values.index("--login-oid") + 1] = "104"
    with pytest.raises(FittedCustodyCommandError, match="command_role_invalid"):
        _arguments(values)


@pytest.fixture
def lost_ack(monkeypatch):
    seal = valid_prospective_preregistration()
    seal_sha256 = seal.canonical_sha256()
    checked = SimpleNamespace(preregistration_sha256=seal_sha256)
    binding = FrozenGate3Artifact(b"synthetic-binding", "b" * 64, checked)
    inputs = custody.FittedCandidateInputsV1(
        binding=binding,
        expected_binding_sha256=binding.sha256,
        batch=None,
        study=None,
        trial_result=None,
        legacy_scores=None,
        preregistration=seal,
        expected_preregistration_sha256=seal_sha256,
        source_archive_sha256="c" * 64,
    )

    def verify(candidate_inputs):
        assert candidate_inputs is inputs
        return checked

    monkeypatch.setattr(custody, "_verified", verify)
    reader = object.__new__(custody.Gate3FittedCandidateCustodyV1)
    reader._role = "reader"
    reader._expected = ("host", 5432, "db", "reader", 101, 102, 103, 104, 105)
    recorded = seal.created_at + timedelta(seconds=1)
    acknowledged = recorded + timedelta(seconds=1)
    read_at = acknowledged + timedelta(seconds=1)
    binding_row = {
        "binding_json": binding.payload.decode("ascii"),
        "seal_sha256": seal_sha256,
        "binding_sha256": binding.sha256,
        "publisher_oid": 103,
        "publication_xid": "11",
        "recorded_at": recorded,
        "database_read_at": read_at,
        "fitted_reader_only": True,
    }
    ack_row = {
        "seal_sha256": seal_sha256,
        "binding_sha256": binding.sha256,
        "observer_oid": 104,
        "acknowledgement_xid": "12",
        "acknowledged_at": acknowledged,
        "database_read_at": read_at,
        "fitted_reader_only": True,
    }
    return reader, inputs, binding_row, ack_row


@pytest.mark.asyncio
async def test_lost_ack_recovered_with_only_read_transactions(monkeypatch, lost_ack):
    reader, inputs, binding_row, ack_row = lost_ack
    calls = []

    async def transaction(statement, parameters, *, read):
        assert read is True
        assert parameters == {"seal": inputs.expected_preregistration_sha256}
        calls.append(statement)
        if statement in (custody._RECOVERY_BINDING, custody._READ_BINDING):
            return binding_row, "13"
        assert statement in (custody._RECOVERY_ACK, custody._READ_ACK)
        return ack_row, "14"

    monkeypatch.setattr(reader, "_transaction", transaction)
    recovered = await reader.recover_acknowledgement(inputs)
    pinned = await reader.read_acknowledged(
        inputs, expected_ack_sha256=recovered.acknowledgement.ack_sha256
    )
    assert recovered.acknowledgement == pinned
    assert not recovered.independent_ack_pin_verified
    assert not recovered.predictive_oos_eligible and not recovered.execution_authority
    assert calls == [
        custody._RECOVERY_BINDING,
        custody._RECOVERY_ACK,
        custody._READ_BINDING,
        custody._READ_ACK,
    ]
    for query in calls[:2]:
        assert query.text.count("pg_catalog.has_function_privilege(SESSION_USER") == 4
        assert " AS fitted_reader_only " in query.text


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("missing", "FITTED_CUSTODY_ACK_MISSING"),
        ("wrong_binding", "FITTED_CUSTODY_ACK_MISMATCH"),
        ("write_grant", "FITTED_CUSTODY_RECOVERY_ROLE_REFUSED"),
        ("observer_login", "FITTED_CUSTODY_ROLE_REFUSED"),
    ],
)
@pytest.mark.asyncio
async def test_recovery_refuses_unknown_or_wrong_ack_without_writing(
    monkeypatch, lost_ack, failure, error
):
    reader, inputs, binding_row, ack_row = lost_ack
    if failure == "observer_login":
        reader._role = "observer"
    if failure == "wrong_binding":
        ack_row["binding_sha256"] = "f" * 64
    if failure == "write_grant":
        ack_row["fitted_reader_only"] = False
    calls = []

    async def transaction(statement, _parameters, *, read):
        assert read is True
        calls.append(statement)
        if statement is custody._RECOVERY_BINDING:
            return binding_row, "13"
        assert statement is custody._RECOVERY_ACK
        return (None if failure == "missing" else ack_row), "14"

    monkeypatch.setattr(reader, "_transaction", transaction)
    with pytest.raises(custody.Gate3FittedCustodyError, match=error):
        await reader.recover_acknowledgement(inputs)
    assert len(calls) == (0 if failure == "observer_login" else 2)


@pytest.mark.asyncio
async def test_command_dispatches_recovery_to_reader_only(
    monkeypatch, tmp_path, lost_ack
):
    _, inputs, binding_row, ack_row = lost_ack
    args = _arguments(_argv(tmp_path, "recover-ack"))
    events = []

    async def dispose():
        events.append("disposed")

    async def recover(candidate_inputs):
        assert candidate_inputs is inputs
        events.append("readback")
        return SimpleNamespace(
            acknowledgement=SimpleNamespace(
                **(binding_row | ack_row | {"ack_sha256": "b" * 64})
            )
        )

    def repository(_factory, **kwargs):
        assert kwargs["role"] == "reader"
        assert kwargs["expected_login_oid"] == 102
        return SimpleNamespace(recover_acknowledgement=recover)

    monkeypatch.setattr(
        command,
        "_database_url",
        lambda _args: SimpleNamespace(port=5432, password=None),
    )
    monkeypatch.setattr(command, "_ca_context", lambda *_args: None)
    monkeypatch.setattr(
        command,
        "create_async_engine",
        lambda *_args, **_kwargs: SimpleNamespace(dispose=dispose),
    )
    monkeypatch.setattr(
        command, "async_sessionmaker", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(command, "Gate3FittedCandidateCustodyV1", repository)
    result = await command._execute(args, inputs)
    assert events == ["readback", "disposed"]
    assert result["observed_ack_sha256"] == "b" * 64
    assert result["independent_ack_pin_verified"] is False
    assert result["custody_writes"] == 0
    assert "ack_sha256" not in result


def test_recovery_receipt_keeps_observed_digest_separate_from_an_ack_pin(
    monkeypatch, tmp_path
):
    args = _arguments(_argv(tmp_path, "recover-ack"))
    monkeypatch.setattr(command, "require_private_evidence_base_v2", lambda _path: None)
    monkeypatch.setattr(command, "_read_root", lambda _path: nullcontext(object()))

    def retain(_directory, name, raw, _limit):
        assert name == args.output.name
        receipt = json.loads(raw)
        assert receipt["schema"] == "ctcc.mie.gate3_fitted_ack_recovery.v1"
        assert receipt["observed_ack_sha256"] == "b" * 64
        assert "ack_sha256" not in receipt
        assert receipt["independent_ack_pin_verified"] is False
        assert receipt["independent_custody_verified"] is False
        assert receipt["predictive_oos_eligible"] is False
        assert receipt["execution_authority"] is False
        return "c" * 64

    monkeypatch.setattr(command, "_retain", retain)
    assert (
        command._retain_result(
            args,
            {
                "observed_ack_sha256": "b" * 64,
                "independent_ack_pin_verified": False,
                "custody_writes": 0,
            },
        )
        == "c" * 64
    )


def test_bad_digest_and_role_oid_are_refused_before_any_io(tmp_path):
    values = _argv(tmp_path)
    values[values.index("--binding-sha256") + 1] = "not-a-digest"
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(values)
    values = _argv(tmp_path)
    values[values.index("--observer-oid") + 1] = "0"
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_invalid$"
    ):
        _arguments(values)


def test_output_may_not_alias_any_input(tmp_path):
    values = _argv(tmp_path)
    values[values.index("--output") + 1] = str(tmp_path / "binding")
    with pytest.raises(
        FittedCustodyCommandError, match="^gate3_fitted_command_path_conflict$"
    ):
        _arguments(values)


def test_private_url_must_match_the_independently_pinned_endpoint(
    tmp_path, monkeypatch
):
    args = _arguments(_argv(tmp_path))
    environment = "CTCC_GATE3_FITTED_CUSTODY_DATABASE_URL"
    monkeypatch.setenv(
        environment, "postgresql+asyncpg://custodian@wrong.example/evidence"
    )
    with pytest.raises(FittedCustodyCommandError, match="database_settings_invalid"):
        _database_url(args)
    monkeypatch.setenv(
        environment, "postgresql+asyncpg://custodian@custody.example/evidence"
    )
    assert _database_url(args).host == args.database_host
