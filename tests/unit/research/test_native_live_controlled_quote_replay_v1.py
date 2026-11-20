"""Declared synthetic source replay; no Live order or native TLS acceptance."""

import asyncio

import pytest

from app.domain.source_primitives import sha
from app.trade_qualification import native_live_public_source_v1 as subject
from tests.unit.research import test_native_live_public_source_v1 as fixtures

live_source = fixtures.live_source
market = fixtures.market


@pytest.mark.asyncio
async def test_exit_only_quote_reparse_matches_full_audit_without_reusing_models_or_clock(
    live_source,
):
    _, _, _, harness, key, request = live_source
    opaque, _ = request("controlled_exit")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    full = subject._replay_owned_live_public_v1(owner, key=key)
    before = (len(harness.requests), len(harness.connections))
    one = subject._replay_owned_live_controlled_quote_v1(owner, key=key)
    two = subject._replay_owned_live_controlled_quote_v1(owner, key=key)
    assert one.quote == two.quote == full.quote
    assert one.quote is not two.quote and one.quote.ticker is not two.quote.ticker
    assert (
        one.reference == two.reference == full.reference
        and one.reference is not two.reference
    )
    assert one.evaluated_at < two.evaluated_at
    assert one.raw_inventory_sha256 == full.raw_inventory_sha256
    assert one.original_packet_sha256 == full.packet.bundle_sha256
    assert one.original_publication_sha256 == full.publication_receipt_sha256
    assert one.execution_authority is one.qualification_performed is False
    assert not hasattr(one, "market") and not hasattr(one, "packet")
    assert (len(harness.requests), len(harness.connections)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initial", "post_g12"])
async def test_exit_replay_cannot_substitute_for_entry_or_post_g12_full_replay(
    live_source, stage
):
    _, _, _, _, key, request = live_source
    opaque, _ = request(stage)
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_controlled_quote_v1(owner, key=key)
    assert owner not in subject._OWNERS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["raw", "packet", "missing", "envelope", "extra", "identity", "stale", "expired"],
)
async def test_exit_quote_guard_rechecks_entire_original_inventory_root_and_current_age(
    live_source, monkeypatch, mutation
):
    clock, parent, raw, harness, key, request = live_source
    opaque, _ = request("controlled_exit")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    before = (len(harness.requests), len(harness.connections))
    if mutation in {"raw", "missing"}:
        name = next(name for name in raw.content if name.endswith(".raw"))
        if mutation == "raw":
            raw.content[name] += b" "
        else:
            raw.content.pop(name)
    elif mutation == "packet":
        name = max(name for name in raw.content if name.endswith(".raw"))
        raw.content[name] += b" "
    elif mutation == "envelope":
        parent.content[subject._FILE] += b" "
    elif mutation == "extra":
        raw.content["extra.json"] = b"{}"
    elif mutation == "identity":
        monkeypatch.setattr(
            subject.transport, "_native_recheck_root_identity", lambda _: (999, 999)
        )
    else:
        clock.count += 6000 if mutation == "stale" else 40000
    with pytest.raises(subject.LivePublicSourceError):
        subject._replay_owned_live_controlled_quote_v1(owner, key=key)
    assert owner not in subject._OWNERS
    assert (len(harness.requests), len(harness.connections)) == before


@pytest.mark.asyncio
async def test_exit_quote_replay_saved_hashes_or_cross_task_owner_never_authorize(
    live_source,
):
    _, _, _, harness, key, request = live_source
    for value in (
        {"packet_sha256": sha(b"synthetic")},
        object.__new__(subject.OwnedLivePublicSourceV1),
    ):
        with pytest.raises(subject.LivePublicSourceError):
            subject._replay_owned_live_controlled_quote_v1(value, key=key)
    opaque, _ = request("controlled_exit")
    owner = await subject.capture_owned_live_public_v1(opaque, key=key)
    before = len(harness.requests)

    async def different():
        with pytest.raises(subject.LivePublicSourceError):
            subject._replay_owned_live_controlled_quote_v1(owner, key=key)

    await asyncio.create_task(different())
    assert owner not in subject._OWNERS and len(harness.requests) == before
