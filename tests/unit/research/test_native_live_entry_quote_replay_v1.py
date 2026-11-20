"""ENTRY byte-reader contracts; the admission seam is explicitly synthetic."""

import asyncio

import pytest

from app.trade_qualification import native_live_public_source_v1 as subject
from tests.unit.research import test_native_live_public_source_v1 as fixtures

live_source = fixtures.live_source
market = fixtures.market


def _declared_admission(monkeypatch, owner):
    qualification = object()

    def require(candidate, actual):
        if candidate is not qualification or actual is not owner:
            raise subject.LivePublicSourceError("synthetic_entry_proof_refused")

    # This only tests the byte/parser purpose. Genuine proof admission has
    # separate original-frame/constructor/source-closure tests.
    monkeypatch.setattr(subject, "_require_entry_bank_v1", require)
    return qualification


@pytest.mark.asyncio
async def test_post_g12_entry_quote_is_freshly_reparsed_and_retains_original_inventory(
    live_source, monkeypatch
):
    _, _, _, harness, key, request = live_source
    opaque, _ = request("post_g12")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    original = subject._replay_owned_live_public_v1(owner, key=key)
    qualification = _declared_admission(monkeypatch, owner)
    before = len(harness.requests), len(harness.connections)
    one = subject._replay_owned_live_entry_quote_v1(owner, qualification, key=key)
    two = subject._replay_owned_live_entry_quote_v1(owner, qualification, key=key)
    assert type(one) is subject.LiveEntryQuoteReplayV1
    assert one.quote == two.quote == original.quote
    assert one.quote is not two.quote and one.quote.ticker is not two.quote.ticker
    assert one.reference == original.reference
    assert one.raw_inventory_sha256 == original.raw_inventory_sha256
    assert one.original_packet_sha256 == original.packet.bundle_sha256
    assert one.original_publication_sha256 == original.publication_receipt_sha256
    assert original.evaluated_at < one.evaluated_at < two.evaluated_at
    assert not hasattr(one, "market") and not hasattr(one, "packet")
    assert one.execution_authority is one.qualification_performed is False
    assert before == (len(harness.requests), len(harness.connections))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ("initial", "controlled_exit"))
async def test_only_original_post_g12_purpose_can_use_entry_quote_reader(
    live_source, monkeypatch, stage
):
    _, _, _, _, key, request = live_source
    opaque, _ = request(stage)
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    qualification = _declared_admission(monkeypatch, owner)
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_entry_quote_v1(owner, qualification, key=key)
    assert owner not in subject._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ("raw", "missing", "extra", "envelope", "identity", "stale", "cancel")
)
async def test_entry_reader_rereads_complete_inventory_and_current_clock_after_admission(
    live_source, monkeypatch, mutation
):
    clock, parent, raw, harness, key, request = live_source
    opaque, _ = request("post_g12")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    qualification = _declared_admission(monkeypatch, owner)
    before = len(harness.requests)
    if mutation in ("raw", "missing"):
        name = next(n for n in raw.content if n.endswith(".raw"))
        if mutation == "raw":
            raw.content[name] += b" "
        else:
            raw.content.pop(name)
    elif mutation == "extra":
        raw.content["unreviewed.json"] = b"{}"
    elif mutation == "envelope":
        parent.content[subject._FILE] += b" "
    elif mutation == "identity":
        monkeypatch.setattr(
            subject.transport, "_native_recheck_root_identity", lambda _: (9, 9)
        )
    elif mutation == "stale":
        clock.count += 6000
    else:

        async def different_task():
            with pytest.raises(subject.LivePublicSourceError):
                subject._replay_owned_live_entry_quote_v1(owner, qualification, key=key)

        await asyncio.create_task(different_task())
        assert owner not in subject._OWNERS and len(harness.requests) == before
        return
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_entry_quote_v1(owner, qualification, key=key)
    assert owner not in subject._OWNERS and len(harness.requests) == before


@pytest.mark.asyncio
async def test_initial_bank_is_reopened_as_old_evidence_without_retiming_its_quote(
    live_source, monkeypatch
):
    clock, _, raw, _, key, request = live_source
    opaque, _ = request("initial")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    original = subject._replay_owned_live_public_v1(owner, key=key)
    qualification = _declared_admission(monkeypatch, owner)
    clock.count += 6000
    pins = subject._recheck_owned_initial_entry_bytes_v1(owner, qualification, key=key)
    assert pins[:2] == (original.raw_inventory_sha256, original.packet.bundle_sha256)
    name = next(n for n in raw.content if n.endswith(".raw"))
    raw.content[name] += b" "
    with pytest.raises(subject.LivePublicSourceError):
        subject._recheck_owned_initial_entry_bytes_v1(owner, qualification, key=key)
    assert owner not in subject._OWNERS
