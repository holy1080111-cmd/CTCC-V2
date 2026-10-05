"""Synthetic native-adapter boundary + memory storage only; no native acceptance."""

import copy
import pickle

import pytest

from app.domain import native_clock as domain_clock
from app.public_market_source import public_attempt_journal as attempts
from app.public_market_source import public_clock as clock
from app.public_market_source import public_market_capture as capture
from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    canonical,
    decode,
)
from tests.unit.research.public_receipt_fixtures import os_evidence
from tests.unit.research.test_public_attempt_journal import invoke, read_attempt
from tests.unit.research.test_public_clock_v2 import fixture as clock_fixture
from tests.unit.research.test_public_clock_v2 import host_change, stream
from tests.unit.research.test_public_journal_contracts import (
    MemoryDirectory,
    memory_publisher,
)


def negative(mode="stopped"):
    evidence = clock_fixture()
    host_change(evidence, "service_state", 1)
    diagnostic = evidence["diagnostic"]
    status = diagnostic["status"]
    if mode == "manual":
        host_change(evidence, "service_state", 4)
        host_change(evidence, "service_start_type", 3)
    elif mode == "stderr":
        status["stderr"] = stream(b"synthetic actual stderr")
    elif mode in {"timeout", "overflow", "clock_failure"}:
        status["outcome"] = mode
        status["exit_code"] = None
        if mode == "overflow":
            status["stdout"]["truncated"] = True
        if mode == "clock_failure":
            status["completed"] = None
    elif mode == "reversed":
        status["completed"]["monotonic_ns"] -= 1
        status["completed"]["utc_ns"] -= 1
    elif mode == "partial_inventory":
        diagnostic["status"] = diagnostic["host_after"] = None
    return {"diagnostic": diagnostic}


def rejected_runtime(monkeypatch, *, stage="before", mode="stopped", terminal=False):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.requests.clear()
    original_stamp = capture.native_stamp
    observation = negative(mode)
    calls, failed = [], False

    def native():
        nonlocal failed
        calls.append(True)
        if len(calls) == (1 if stage == "before" else 2):
            failed = True
            raise clock.NativeClockObservationError(
                "PRIVATE_EXCEPTION_VALUE", observation
            )
        return os_evidence(fixture.stamp())

    def stamp():
        if terminal and failed:
            raise PublicReceiptError("native_clock_sample_unbounded")
        return original_stamp()

    monkeypatch.setattr(domain_clock, "native_os_clock", native)
    monkeypatch.setattr(capture, "_observe_owned_clock", clock._observe_owned_clock)
    monkeypatch.setattr(capture, "native_stamp", stamp)
    return fixture, directory, journal, observation, calls


@pytest.mark.parametrize("stage", ["before", "after"])
@pytest.mark.parametrize(
    "mode",
    [
        "stopped",
        "manual",
        "stderr",
        "timeout",
        "overflow",
        "clock_failure",
        "reversed",
        "partial_inventory",
    ],
)
def test_failed_clock_actual_bytes_and_times_survive_without_admission(
    monkeypatch, stage, mode
):
    fixture, directory, journal, observation, calls = rejected_runtime(
        monkeypatch, stage=stage, mode=mode
    )
    with pytest.raises(PublicReceiptError, match="os_clock_rejected"):
        invoke(fixture, journal)
    summary, requests = read_attempt(directory, journal)
    node = directory.content["attempts"]["attempt-00000001"]
    retained = decode(node[f"clock-{stage}.json"])
    assert retained["observation"] == observation
    assert retained["outcome"] == "rejected"
    assert len(calls) == (1 if stage == "before" else 2)
    assert len(fixture.requests) == (0 if stage == "before" else 3)
    assert len(requests) == len(fixture.requests)
    if stage == "after":
        assert all(raw for _, raw in requests)
        assert decode(node["clock-before.json"])["outcome"] == "accepted"
    assert summary["disposition"] == "rejected"
    assert summary["intended_receipt_sha256"] is None
    assert (
        not summary["execution_authority"]
        and not summary["measured_availability_eligible"]
    )
    assert journal.checkpoint.sequence == 0 and journal.checkpoint.attempt_sequence == 1
    assert b"PRIVATE_EXCEPTION_VALUE" not in b"".join(
        value for value in node.values() if type(value) is bytes
    )
    assert not journal.read_all()[0]


def test_terminal_clock_failure_preserves_null_without_second_sample(monkeypatch):
    fixture, directory, journal, _, _ = rejected_runtime(monkeypatch, terminal=True)
    with pytest.raises(PublicReceiptError, match="os_clock_rejected"):
        invoke(fixture, journal)
    summary, _ = read_attempt(directory, journal)
    assert summary["completed"] is None and summary["terminal_clock"] == "unavailable"
    assert "clock-before.json" in directory.content["attempts"]["attempt-00000001"]
    assert journal.checkpoint.sequence == 0


def test_setup_without_raw_cannot_invent_a_diagnostic(monkeypatch):
    fixture, directory, journal, _, _ = rejected_runtime(monkeypatch)

    def unavailable():
        raise PublicReceiptError("PRIVATE_SETUP_VALUE")

    monkeypatch.setattr(domain_clock, "native_os_clock", unavailable)
    with pytest.raises(PublicReceiptError, match="os_clock_rejected"):
        invoke(fixture, journal)
    result = decode(
        directory.content["attempts"]["attempt-00000001"]["clock-before.json"]
    )
    assert result["outcome"] == "unobserved" and result["observation"] is None
    assert not fixture.requests


@pytest.mark.parametrize(
    "stage",
    ["clock-before.json", "clock-before-binding.json", "summary.json", "chain.json"],
)
def test_late_persistence_failure_never_deletes_prior_observation(monkeypatch, stage):
    fixture, directory, journal, observation, _ = rejected_runtime(monkeypatch)
    original = MemoryDirectory.publish

    def fail(self, name, payload):
        original(self, name, payload)
        if name == stage:
            raise OSError("PRIVATE_STORAGE_VALUE")

    monkeypatch.setattr(MemoryDirectory, "publish", fail)
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    node = directory.content["attempts"]["attempt-00000001"]
    assert decode(node["clock-before.json"])["observation"] == observation
    assert journal.checkpoint.sequence == 0 and journal.checkpoint.attempt_sequence == 0
    with pytest.raises(PublicReceiptError):
        journal.read_all()


@pytest.mark.parametrize("replacement", ["exception", "dict", "bytes", "foreign"])
def test_caller_error_or_dto_cannot_write_clock_payload(monkeypatch, replacement):
    fixture, directory, journal, _, _ = rejected_runtime(monkeypatch)

    class Foreign:
        def __getattribute__(self, name):
            pytest.fail("foreign callback executed")

    values = {
        "exception": clock.NativeClockObservationError("PRIVATE_VALUE", negative()),
        "dict": negative(),
        "bytes": canonical(negative()),
        "foreign": Foreign(),
    }
    with (
        journal._begin_attempt(fixture.plan) as attempt,
        pytest.raises(PublicReceiptError, match="owned_clock_observation_required"),
    ):
        attempt.record_clock("before", values[replacement])
    node = directory.content["attempts"]["attempt-00000001"]
    assert set(node) == {"plan.json", "started.json"}
    assert not fixture.requests


@pytest.mark.parametrize("transfer", [copy.copy, copy.deepcopy, pickle.dumps])
def test_owned_carrier_cannot_be_copied_or_serialized(monkeypatch, transfer):
    fixture, directory, journal, _, _ = rejected_runtime(monkeypatch)
    with journal._begin_attempt(fixture.plan) as attempt:
        owned = clock._observe_owned_clock(attempt, "before")
        assert not hasattr(owned, "payload")
        with pytest.raises(PublicReceiptError, match="not_transferable"):
            transfer(owned)
    assert set(directory.content["attempts"]["attempt-00000001"]) == {
        "plan.json",
        "started.json",
    }


def test_exact_carrier_type_without_native_registration_cannot_persist(monkeypatch):
    fixture, directory, journal, _, calls = rejected_runtime(monkeypatch)
    # Even knowing the private constructor/issuer cannot register caller bytes.
    unregistered = clock._OwnedClockObservation(clock._CLOCK_ISSUER)
    with (
        journal._begin_attempt(fixture.plan) as attempt,
        pytest.raises(PublicReceiptError, match="owned_clock_observation_required"),
    ):
        attempt.record_clock("before", unregistered)
    assert not calls
    assert set(directory.content["attempts"]["attempt-00000001"]) == {
        "plan.json",
        "started.json",
    }


def test_carrier_scope_cannot_move_between_attempts_and_is_consumed(monkeypatch):
    fixture, directory, journal, _, _ = rejected_runtime(monkeypatch)
    other_directory = MemoryDirectory({})
    with (
        journal._begin_attempt(fixture.plan) as first,
        attempts.owned_attempt(
            other_directory, fixture.plan, stamp=fixture.stamp(), version=2
        ) as other,
    ):
        owned = clock._observe_owned_clock(first, "before")
        with pytest.raises(
            PublicReceiptError, match="owned_clock_observation_required"
        ):
            other.record_clock("before", owned)
        with pytest.raises(
            PublicReceiptError, match="owned_clock_observation_required"
        ):
            first.record_clock("before", owned)
    assert set(other_directory.content) == {"plan.json", "started.json"}
    assert "clock-before.json" not in directory.content["attempts"]["attempt-00000001"]


def test_native_scope_cannot_be_started_again_even_before_persistence(monkeypatch):
    fixture, _, journal, _, calls = rejected_runtime(monkeypatch)
    with journal._begin_attempt(fixture.plan) as attempt:
        owned = clock._observe_owned_clock(attempt, "before")
        with pytest.raises(PublicReceiptError, match="attempt_clock_already_started"):
            clock._observe_owned_clock(attempt, "before")
        assert len(calls) == 1
        assert attempt.record_clock("before", owned)["outcome"] == "rejected"


def test_consumed_carrier_cannot_retry_after_payload_write_fails(monkeypatch):
    fixture, directory, journal, _, calls = rejected_runtime(monkeypatch)
    original = MemoryDirectory.publish

    def fail(self, name, payload):
        original(self, name, payload)
        if name == "clock-before.json":
            raise OSError("PRIVATE_STORAGE_VALUE")

    monkeypatch.setattr(MemoryDirectory, "publish", fail)
    with journal._begin_attempt(fixture.plan) as attempt:
        owned = clock._observe_owned_clock(attempt, "before")
        with pytest.raises(OSError):
            attempt.record_clock("before", owned)
        with pytest.raises(
            PublicReceiptError, match="owned_clock_observation_required"
        ):
            attempt.record_clock("before", owned)
        with pytest.raises(PublicReceiptError, match="attempt_clock_already_started"):
            clock._observe_owned_clock(attempt, "before")
    node = directory.content["attempts"]["attempt-00000001"]
    assert "clock-before.json" in node and "clock-before-binding.json" not in node
    assert len(calls) == 1 and journal.checkpoint.attempt_sequence == 0


def test_carrier_wrong_stage_consumption_burns_original(monkeypatch):
    fixture, _, journal, _, _ = rejected_runtime(monkeypatch)
    with journal._begin_attempt(fixture.plan) as attempt:
        owned = clock._observe_owned_clock(attempt, "before")
        with pytest.raises(
            PublicReceiptError, match="owned_clock_observation_required"
        ):
            clock._owned_clock_payload(owned, attempt, "after")
        with pytest.raises(
            PublicReceiptError, match="owned_clock_observation_required"
        ):
            attempt.record_clock("before", owned)


@pytest.mark.parametrize(
    "mutation", ["raw", "binding", "extra", "missing", "swap_role", "relabel_success"]
)
def test_mutation_or_relabelling_cannot_replay_negative_attempt(monkeypatch, mutation):
    fixture, directory, journal, _, _ = rejected_runtime(monkeypatch)
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    node = directory.content["attempts"]["attempt-00000001"]
    if mutation == "raw":
        node["clock-before.json"] += b" "
    elif mutation == "binding":
        item = decode(node["clock-before-binding.json"])
        item["attempt_id"] = "f" * 32
        node["clock-before-binding.json"] = canonical(item)
    elif mutation == "extra":
        node["unowned.raw"] = b"unexpected"
    elif mutation == "missing":
        del node["clock-before.json"]
    elif mutation == "swap_role":
        node["clock-after.json"] = node.pop("clock-before.json")
    else:
        item = decode(node["summary.json"])
        item["disposition"], item["error_code"] = "completed_collection", "none"
        node["summary.json"] = canonical(item)
    with pytest.raises((PublicReceiptError, KeyError)):
        attempts.replay_attempt(attempts._WithoutChain(MemoryDirectory(node)))


def test_stream_integrity_is_checked_without_requiring_healthy_time():
    result = {
        "schema_version": clock.CLOCK_RESULT_VERSION,
        "outcome": "rejected",
        "observation": negative("reversed"),
    }
    assert clock.replay_clock_observation(canonical(result)) == result
    bad = copy.deepcopy(result)
    bad["observation"]["diagnostic"]["status"]["stdout"]["sha256"] = "f" * 64
    with pytest.raises(PublicReceiptError):
        clock.replay_clock_observation(canonical(bad))


def test_changed_readback_preserves_written_bytes_and_denies_checkpoint(monkeypatch):
    fixture, directory, journal, observation, _ = rejected_runtime(monkeypatch)
    original = MemoryDirectory.read

    def corrupt(self, name, maximum):
        raw = original(self, name, maximum)
        return raw + b" " if name == "clock-before.json" else raw

    monkeypatch.setattr(MemoryDirectory, "read", corrupt)
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    node = directory.content["attempts"]["attempt-00000001"]
    assert decode(node["clock-before.json"])["observation"] == observation
    assert journal.checkpoint.attempt_sequence == journal.checkpoint.sequence == 0


def test_modelled_process_interruption_keeps_unanchored_payload(monkeypatch):
    fixture, directory, journal, observation, _ = rejected_runtime(monkeypatch)
    original = MemoryDirectory.publish

    def interrupted(self, name, payload):
        original(self, name, payload)
        if name == "clock-before.json":
            raise SystemExit("synthetic process interruption")

    monkeypatch.setattr(MemoryDirectory, "publish", interrupted)
    with pytest.raises(SystemExit):
        invoke(fixture, journal)
    node = directory.content["attempts"]["attempt-00000001"]
    assert decode(node["clock-before.json"])["observation"] == observation
    assert "summary.json" not in node and "chain.json" not in node
    assert journal.checkpoint.attempt_sequence == journal.checkpoint.sequence == 0
    with pytest.raises(PublicReceiptError):
        journal.read_all()
