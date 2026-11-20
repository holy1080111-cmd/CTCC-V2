"""Pure read orchestration/byte-change contracts with an INERT semantic seam.

These tests count the canonical-validator invocation and original-byte views.
They do not establish valid market semantics, native storage, TLS or authority;
the unchanged real Global/Live source suites remain required separately.
"""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import global_public_runtime_v1 as subject


@pytest.fixture
def raw_readback(monkeypatch, tmp_path):
    now = datetime(2026, 11, 21, tzinfo=UTC)
    plan = canonical({"expires_at": (now + timedelta(seconds=30)).isoformat()})
    payload = b"declared-synthetic-packet"
    summary = {
        "disposition": "captured",
        "packet_sha256": sha(payload),
        "completed": {"utc_ns": 100, "monotonic_ns": 100},
    }

    class PacketData:
        bundle_sha256 = sha(payload)

    class RawDirectory:
        def __init__(self):
            self.content = {
                "plan.json": plan,
                "summary.json": canonical(summary),
                "event-0000.json": b"{}",
                "event-0000.raw": payload,
            }
            self.reads = []
            self.identity = (11, 22)

        def names(self):
            return tuple(self.content)

        def read(self, name, maximum):
            self.reads.append(name)
            return self.content[name]

    directory, calls = RawDirectory(), []

    @contextmanager
    def reopen(path):
        assert path == tmp_path
        yield directory

    def canonical_semantic_seam(view, *, expected_plan_sha256):
        calls.append(view)
        assert type(view) is subject._FrozenGlobalRawV2
        assert expected_plan_sha256 == sha(plan)
        assert view.read("plan.json", subject.journal.MAX_RAW) == plan
        assert view.read("summary.json", subject.journal.MAX_RAW) == canonical(summary)
        assert view.read("event-0000.raw", subject.journal.MAX_RAW) == payload
        return summary, ({"kind": "packet"},), PacketData()

    monkeypatch.setattr(subject.runtime, "_reopen_runtime_journal", reopen)
    monkeypatch.setattr(
        subject.runtime,
        "_native_recheck_root_identity",
        lambda _root: directory.identity,
    )
    monkeypatch.setattr(
        subject.runtime, "replay_public_runtime", canonical_semantic_seam
    )
    monkeypatch.setattr(subject, "GlobalPublicComponentPacketV1", PacketData)
    args = {
        "expected_plan_sha256": sha(plan),
        "expected_journal_sha256": sha(canonical(summary)),
        "expected_root_identity": directory.identity,
    }
    return tmp_path, directory, calls, args, canonical_semantic_seam


def test_one_full_validator_for_both_data_views_with_independent_all_byte_readback(
    raw_readback,
):
    root, directory, calls, args, _semantic = raw_readback
    result = subject._read_global_public_capture_audit_v2(root, **args)
    assert len(calls) == 1
    assert result.capture.code == "initial_global_public_replayed"
    assert result.capture.admission == "DENY"
    assert (
        result.capture.execution_authority
        is result.capture.qualification_performed
        is False
    )
    assert result.capture.order_writes == result.capture.account_requests == 0
    assert result.raw_files == tuple(sorted(directory.content.items()))
    assert all(directory.reads.count(name) == 2 for name in directory.content)
    public = subject.read_global_public_capture_v1(root, **args)
    assert type(public) is subject.GlobalPublicCaptureV1
    assert public.code == result.capture.code and len(calls) == 2
    assert calls[0] is not calls[1]  # A separate call always opens a NEW bank.


@pytest.mark.parametrize(
    "change", ("body", "plan", "summary", "missing", "extra", "identity")
)
def test_any_late_byte_inventory_or_root_change_fails_closed(
    raw_readback, monkeypatch, change
):
    root, directory, calls, args, semantic = raw_readback

    def changed(view, **kwargs):
        result = semantic(view, **kwargs)
        if change == "identity":
            directory.identity = (12, 23)
        elif change == "missing":
            del directory.content["event-0000.raw"]
        elif change == "extra":
            directory.content["event-0001.raw"] = b"extra"
        else:
            name = {
                "body": "event-0000.raw",
                "plan": "plan.json",
                "summary": "summary.json",
            }[change]
            directory.content[name] += b" "
        # The one validated buffer itself cannot be retagged by disk changes.
        assert (
            view.read("event-0000.raw", subject.journal.MAX_RAW)
            == b"declared-synthetic-packet"
        )
        return result

    monkeypatch.setattr(subject.runtime, "replay_public_runtime", changed)
    with pytest.raises(
        subject.GlobalPublicCaptureError, match="^global_public_readback_unavailable$"
    ):
        subject._read_global_public_capture_audit_v2(root, **args)
    assert len(calls) == 1


def test_semantic_failure_is_not_a_second_parse_retry(raw_readback, monkeypatch):
    root, _directory, calls, args, semantic = raw_readback

    def rejected(view, **kwargs):
        semantic(view, **kwargs)
        raise ValueError("declared semantic rejection")

    monkeypatch.setattr(subject.runtime, "replay_public_runtime", rejected)
    with pytest.raises(subject.GlobalPublicCaptureError):
        subject.read_global_public_capture_v1(root, **args)
    assert len(calls) == 1


def test_invalid_pin_fails_before_canonical_validator(raw_readback):
    root, _directory, calls, args, _semantic = raw_readback
    args["expected_journal_sha256"] = "a" * 64
    with pytest.raises(subject.GlobalPublicCaptureError):
        subject.read_global_public_capture_v1(root, **args)
    assert calls == []


@pytest.mark.parametrize(
    "name", ("../outside.raw", "EVENT-0000.raw", "unlisted", "event-0001.raw")
)
def test_unknown_case_path_or_unjoined_file_never_disappears_from_inventory(
    raw_readback, monkeypatch, name
):
    root, directory, calls, args, semantic = raw_readback
    directory.content[name] = b"extra"

    def strict_seam(view, **kwargs):
        result = semantic(view, **kwargs)
        assert set(view.names()) == {
            "plan.json",
            "summary.json",
            "event-0000.json",
            "event-0000.raw",
        }
        return result

    monkeypatch.setattr(subject.runtime, "replay_public_runtime", strict_seam)
    with pytest.raises(subject.GlobalPublicCaptureError):
        subject.read_global_public_capture_v1(root, **args)
    assert len(calls) <= 1


def test_frozen_byte_view_has_no_mutation_fallback_or_readback_authority(raw_readback):
    root, _directory, _calls, args, _semantic = raw_readback
    result = subject._read_global_public_capture_audit_v2(root, **args)
    view = subject._FrozenGlobalRawV2(result.raw_files)
    with pytest.raises(FrozenInstanceError):
        view._names = ()
    with pytest.raises(TypeError):
        view._files["plan.json"] = b"replacement"
    with pytest.raises(subject.GlobalPublicCaptureError):
        view.read("missing.raw", subject.journal.MAX_RAW)
    for saved in (result, result.capture, view):
        assert type(saved) is not type(Path())
        with pytest.raises(subject.GlobalPublicCaptureError):
            subject.read_global_public_capture_v1(saved, **args)


def test_capture_capacity_is_bounded_before_semantic_work(raw_readback, monkeypatch):
    root, _directory, calls, args, _semantic = raw_readback
    monkeypatch.setattr(subject.journal, "MAX_TOTAL", 1)
    with pytest.raises(subject.GlobalPublicCaptureError):
        subject.read_global_public_capture_v1(root, **args)
    assert calls == []
