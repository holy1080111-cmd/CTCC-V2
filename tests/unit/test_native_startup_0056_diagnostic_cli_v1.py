"""No database or provider I/O for the explicit DB0056 DENY diagnostic."""

from __future__ import annotations

import json

import pytest

from scripts import native_startup_0056_diagnostic_cli_v1 as subject


def _pins_bytes() -> bytes:
    return json.dumps(
        {
            "schema": subject._PIN_SCHEMA,
            **{name: "a" * 64 for name in subject._PIN_FIELDS},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def test_exact_caller_pins_parse_without_authority():
    parsed = subject._parse_pins(_pins_bytes())
    assert type(parsed) is subject.NativeStartupCandidate0056PinsV1
    assert parsed.source_manifest_sha256 == "a" * 64
    assert subject._output("OBSERVED_DENY") == {
        "schema": subject._SCHEMA,
        "status": "OBSERVED_DENY",
        "caller_pins_matched": True,
        "independent_reference_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'{"schema":"x"}',
        _pins_bytes().replace(b"a" * 64, b"z" * 64, 1),
        _pins_bytes().replace(b'"schema":', b'"extra":1,"schema":', 1),
        _pins_bytes().replace(b'"schema":', b'"schema":1,"schema":', 1),
        b"x" * 4097,
    ],
)
def test_malformed_or_conflicting_pins_refused(raw):
    with pytest.raises(ValueError):
        subject._parse_pins(raw)


def test_main_has_fixed_denial_without_reading_database(monkeypatch, capsys):
    called = False

    async def should_not_observe(_pins):
        nonlocal called
        called = True

    monkeypatch.setattr(subject, "_observe_configured", should_not_observe)
    assert subject.main(["--unexpected"]) == 1
    assert called is False
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "REFUSED"
    assert output["admission"] == "DENY"
    assert "source_manifest_sha256" not in output


def test_main_observation_still_emits_deny(monkeypatch, capsys):
    pins = subject._parse_pins(_pins_bytes())

    async def observed(value):
        assert value is pins

    monkeypatch.setattr(subject, "_read_private_pins", lambda _path, _sha: pins)
    monkeypatch.setattr(subject, "_observe_configured", observed)
    assert (
        subject.main(
            [
                "--inspect-configured",
                "--pins-file",
                "/private/reference.json",
                "--pins-sha256",
                "b" * 64,
            ]
        )
        == 2
    )
    output = json.loads(capsys.readouterr().out)
    assert output == subject._output("OBSERVED_DENY")


def test_main_redacts_database_failure(monkeypatch, capsys):
    pins = subject._parse_pins(_pins_bytes())

    async def failed(_pins):
        raise RuntimeError("PRIVATE_DIAGNOSTIC_MARKER_SHOULD_NOT_APPEAR")

    monkeypatch.setattr(subject, "_read_private_pins", lambda _path, _sha: pins)
    monkeypatch.setattr(subject, "_observe_configured", failed)
    assert (
        subject.main(
            [
                "--inspect-configured",
                "--pins-file",
                "/private/reference.json",
                "--pins-sha256",
                "b" * 64,
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "PRIVATE_DIAGNOSTIC_MARKER_SHOULD_NOT_APPEAR" not in output
    assert json.loads(output) == subject._output("REFUSED")
