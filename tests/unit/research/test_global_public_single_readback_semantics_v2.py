"""ORIGINAL canonical validator on SHA-pinned SYNTHETIC anonymous raw bytes.

Storage/identity are declared in-memory seams. This proves semantic compatibility
and read orchestration, never native TLS, account state, venue evidence or Arm.
"""

import base64
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.trade_qualification import global_public_runtime_v1 as subject
from app.trade_qualification import native_live_public_source_v1 as live

_FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "global_public_initial_synthetic_v2.json"
)
_FIXTURE_SHA256 = "1774898b1b9c78a92ae2b0e234cf480932eed0475258866c21dd01ce63c06ff0"


class _MemoryRaw:
    def __init__(self, files):
        self.files = dict(files)

    def names(self):
        return tuple(self.files)

    def read(self, name, maximum):
        raw = self.files[name]
        assert type(raw) is bytes and len(raw) <= maximum
        return raw


@pytest.fixture
def canonical_bank(monkeypatch, tmp_path):
    raw = _FIXTURE.read_bytes()
    assert sha(raw) == _FIXTURE_SHA256
    doc = decode(raw, maximum=16 * 1024 * 1024)
    assert doc["provenance"] == "synthetic_anonymous_transport_engineering_fixture"
    assert doc["actual_venue_evidence"] is doc["execution_authority"] is False
    files = []
    for row in doc["raw_files"]:
        value = base64.b64decode(row["base64"], validate=True)
        assert len(value) == row["bytes"] and sha(value) == row["sha256"]
        files.append((row["name"], value))
    directory, calls = _MemoryRaw(files), []

    @contextmanager
    def storage(root):
        assert root == tmp_path
        yield directory

    original = subject.runtime.replay_public_runtime

    def counted(view, **kwargs):
        calls.append(view)
        return original(view, **kwargs)

    monkeypatch.setattr(subject.runtime, "_reopen_runtime_journal", storage)
    monkeypatch.setattr(
        subject.runtime, "_native_recheck_root_identity", lambda _root: (11, 22)
    )
    monkeypatch.setattr(subject.runtime, "replay_public_runtime", counted)
    return (
        tmp_path,
        directory,
        calls,
        {
            "expected_plan_sha256": sha(directory.files["plan.json"]),
            "expected_journal_sha256": sha(directory.files["summary.json"]),
            "expected_root_identity": (11, 22),
        },
    )


def _rehash_events(files):
    summary = decode(files["summary.json"])
    previous = sha(files["plan.json"])
    for index in range(summary["event_count"]):
        name = f"event-{index:04d}.json"
        event = decode(files[name])
        event["previous_sha256"] = previous
        payload = files.get(f"event-{index:04d}.raw")
        if payload is not None:
            event["raw_sha256"], event["raw_bytes"] = sha(payload), len(payload)
        if event["kind"] == "packet":
            event["metadata"]["bundle_sha256"] = sha(payload)
            summary["packet_sha256"] = sha(payload)
        files[name] = canonical(event)
        previous = sha(files[name])
    summary["head_sha256"] = previous
    files["summary.json"] = canonical(summary)


def test_original_global_public_data_api_and_full_semantics_are_preserved(
    canonical_bank,
):
    root, directory, calls, args = canonical_bank
    result = subject.read_global_public_capture_v1(root, **args)
    assert (
        len(calls) == 1 and type(result.packet) is subject.GlobalPublicComponentPacketV1
    )
    assert (
        result.code == "initial_global_public_replayed" and result.admission == "DENY"
    )
    assert result.execution_authority is result.qualification_performed is False
    assert result.account_requests == result.order_writes == 0
    assert result.packet.environment == "anonymous_global_public"
    assert result.packet.packet_json == next(
        directory.files[f"event-{event['index']:04d}.raw"]
        for name in directory.names()
        if name.endswith(".json") and name.startswith("event-")
        for event in (decode(directory.files[name]),)
        if event["kind"] == "packet"
    )


def test_live_audit_metadata_uses_same_one_full_validator_and_exact_old_inventory(
    canonical_bank,
):
    root, directory, calls, args = canonical_bank
    plan = decode(directory.files["plan.json"])
    started = plan["invocation_started"]
    barrier = ClockStamp(
        utc_ns=started["utc_ns"] - 1, monotonic_ns=started["monotonic_ns"] - 1
    )
    params = live.LivePublicCaptureParametersV1(
        root,
        "synthetic-live-reader-test",
        plan["instrument_id"],
        object(),
        "initial",
        subject.journal._time(plan["expires_at"]),
        started["monotonic_ns"] + 30_000_000_000,
        None,
        None,
        None,
        live.POLICY_SHA256,
    )
    result = live._audit(
        root,
        root_identity=(11, 22),
        plan_sha256=args["expected_plan_sha256"],
        journal_sha256=args["expected_journal_sha256"],
        params=params,
        not_before=barrier.model_dump(),
    )
    assert len(calls) == 1
    assert result[4:7] == live._inventory(directory)
    assert result[3] is result[0].packet
    assert result[0].execution_authority is False


@pytest.mark.parametrize("changed", ("authority", "raw_body"))
def test_rehashed_invalid_complete_source_is_still_semantically_refused(
    canonical_bank, changed
):
    root, directory, calls, args = canonical_bank
    for name in tuple(directory.names()):
        if not name.startswith("event-") or not name.endswith(".json"):
            continue
        event = decode(directory.files[name])
        if changed == "authority" and event["kind"] == "packet":
            raw_name = f"event-{event['index']:04d}.raw"
            packet = decode(directory.files[raw_name])
            packet["execution_authority"] = True
            directory.files[raw_name] = canonical(packet)
            break
        if changed == "raw_body" and event["kind"] == "chunk":
            directory.files[f"event-{event['index']:04d}.raw"] = b"{}"
            break
    else:
        pytest.fail("declared fixture lacks requested original source event")
    _rehash_events(directory.files)
    args["expected_journal_sha256"] = sha(directory.files["summary.json"])
    with pytest.raises(subject.GlobalPublicCaptureError):
        subject.read_global_public_capture_v1(root, **args)
    assert len(calls) == 1


def test_a_new_call_never_reuses_the_first_call_packet_or_receipt(canonical_bank):
    root, directory, calls, args = canonical_bank
    first = subject.read_global_public_capture_v1(root, **args)
    name = next(name for name in directory.names() if name.endswith(".raw"))
    directory.files[name] += b" "
    with pytest.raises(subject.GlobalPublicCaptureError):
        subject.read_global_public_capture_v1(root, **args)
    assert len(calls) == 2 and calls[0] is not calls[1]
    assert first.execution_authority is False
