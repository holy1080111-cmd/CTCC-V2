"""Crash probe setup and readiness; synthetic fixture, no DB or exchange calls."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import qualification_durability_probe as probe
from scripts import verify_final_hermetic as harness


def marker_body():
    return {
        "schema": "ctcc.synthetic.qualification.crash-probe.v1",
        "execution_authority": False,
        "order_writes": 0,
    }


@pytest.mark.asyncio
async def test_real_synthetic_fixture_can_be_prepared_inside_probe_event_loop():
    fixture = await probe.synthetic_ledger_fixture()
    assert fixture.request.scope.account_id.startswith("987654321")
    assert fixture.request.scope == fixture.claims.scope
    assert fixture.request.scope.environment == "demo"


def test_ready_marker_is_invisible_until_complete_fsync(tmp_path, monkeypatch):
    marker = tmp_path / "durable.json"
    original = probe.os.fsync
    calls = []

    def fsync(fd):
        assert not marker.exists()
        temporary = tuple(tmp_path.glob("*.tmp"))
        assert len(temporary) == 1
        assert json.loads(temporary[0].read_bytes())["body"] == marker_body()
        original(fd)
        calls.append("fsync")

    monkeypatch.setattr(probe.os, "fsync", fsync)
    probe.write_marker(marker, marker_body())
    envelope = json.loads(marker.read_bytes())
    assert (
        envelope["sha256"]
        == hashlib.sha256(probe.canonical(envelope["body"])).hexdigest()
    )
    assert calls == ["fsync"]
    assert tuple(tmp_path.iterdir()) == (marker,)


def test_ready_marker_never_overwrites_prior_evidence(tmp_path):
    marker = tmp_path / "durable.json"
    marker.write_bytes(b"prior evidence")
    with pytest.raises(FileExistsError):
        probe.write_marker(marker, marker_body())
    assert marker.read_bytes() == b"prior evidence"
    assert tuple(tmp_path.iterdir()) == (marker,)


@pytest.mark.parametrize("stage", ("fsync", "link"))
def test_incomplete_marker_is_not_published(tmp_path, monkeypatch, stage):
    marker = tmp_path / "durable.json"

    def fail(*_):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(probe.os, stage, fail)
    with pytest.raises(OSError):
        probe.write_marker(marker, marker_body())
    assert not marker.exists()
    assert not tuple(tmp_path.iterdir())


def fake_running(monkeypatch, running):
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(stdout=json.dumps({"Running": running})),
    )


def test_exited_seed_fails_immediately_with_diagnostics(tmp_path, monkeypatch):
    fake_running(monkeypatch, False)
    calls = []
    run = SimpleNamespace(command=lambda name, _: calls.append(name))
    monkeypatch.setattr(
        harness.time, "sleep", lambda _: pytest.fail("must not wait for exited seed")
    )
    with pytest.raises(RuntimeError, match="durable_seed_process_exited"):
        harness.wait_for_durable_seed(run, "synthetic", tmp_path / "missing.json")
    assert calls == ["crash-seed-exited-log", "crash-seed-exited-state"]


def test_live_seed_complete_marker_allows_host_crash_stage(tmp_path, monkeypatch):
    fake_running(monkeypatch, True)
    marker = tmp_path / "durable.json"
    probe.write_marker(marker, marker_body())
    harness.wait_for_durable_seed(SimpleNamespace(), "synthetic", marker)


@pytest.mark.parametrize("damage", ("hash", "authority", "order_writes"))
def test_live_process_cannot_make_bad_marker_ready(tmp_path, monkeypatch, damage):
    fake_running(monkeypatch, True)
    marker = tmp_path / "durable.json"
    body = marker_body()
    if damage == "authority":
        body["execution_authority"] = True
    elif damage == "order_writes":
        body["order_writes"] = 1
    probe.write_marker(marker, body)
    if damage == "hash":
        envelope = json.loads(marker.read_bytes())
        envelope["sha256"] = "0" * 64
        marker.write_bytes(probe.canonical(envelope))
    with pytest.raises(RuntimeError, match="durable_seed_marker_invalid"):
        harness.wait_for_durable_seed(SimpleNamespace(), "synthetic", marker)
