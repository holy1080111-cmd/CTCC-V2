"""Native companion file storage with a synthetic provider and raw journal."""

import pytest

from app.domain.source_primitives import decode, sha, utc_from_ns
from app.mie.validation.legacy_baseline_market_replay_v1 import (
    LegacyBaselineMarketReplayError,
    replay_legacy_baseline_market_from_companion_v1,
)
from app.public_market_source.public_receipt_storage import _root_identity
from app.trade_qualification import global_public_runtime_v1 as public
from app.trade_qualification import public_source_runtime as native
from app.trade_qualification.global_public_capture_companion_v1 import (
    GlobalPublicCompanionError,
    capture_global_public_with_companion_v1,
    read_global_public_companion_v1,
)
from tests.unit.mie.test_legacy_baseline_market_replay_v1 import (
    configure_funding_server,
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    market as market,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.research.test_owned_global_public_runtime_v1 import (
    owned_global as owned_global,  # noqa: PLC0414 -- register the existing fixture.
)
from tests.unit.test_qualification_public_market_collector import policy


@pytest.fixture
def source_pair(owned_global, monkeypatch, tmp_path):
    directory, harness = owned_global
    monkeypatch.setattr(native, "_native_recheck_root_identity", _root_identity)
    configure_funding_server(harness)
    raw, companion = tmp_path / "raw", tmp_path / "companion"
    raw.mkdir()
    companion.mkdir()
    return raw, companion, directory, harness


async def capture(raw, companion):
    return await capture_global_public_with_companion_v1(
        raw,
        companion,
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(counts=(240,) * 4),
    )


@pytest.mark.asyncio
async def test_native_companion_publication_and_restart_replay(source_pair, tmp_path):
    raw, companion, journal, harness = source_pair
    publication = await capture(raw, companion)
    path = companion / "post-readback.json"
    recorded = path.read_bytes()
    assert publication.companion_sha256 == sha(recorded)
    assert publication.journal_root_identity == _root_identity(raw)
    assert publication.companion_root_identity == _root_identity(companion)
    assert (
        publication.independent_availability_verified
        is publication.execution_authority
        is False
    )
    assert [item.name for item in companion.iterdir()] == ["post-readback.json"]
    assert "post-readback.json" not in journal.content
    raw_before = dict(journal.content)
    requests_before = len(harness.requests)
    decision = utc_from_ns(decode(recorded)["sample"]["utc_ns"])
    retained_pins = tmp_path / "retained-publication.json"
    retained_pins.write_bytes(publication.canonical_bytes())
    publication_sha256 = publication.canonical_sha256()
    replay = replay_legacy_baseline_market_from_companion_v1(
        raw,
        companion,
        publication_json=retained_pins.read_bytes(),
        expected_publication_sha256=publication_sha256,
        decision_at=decision,
    )
    assert replay.market.instrument_id == "BTC-USDT-SWAP"
    assert (
        decode(replay.receipt_json)["post_readback_receipt_sha256"]
        == publication.companion_sha256
    )
    assert replay.execution_authority is False
    assert len(harness.requests) == requests_before
    assert journal.content == raw_before
    with pytest.raises(
        GlobalPublicCompanionError, match="global_companion_scope_changed"
    ):
        read_global_public_companion_v1(
            raw,
            companion,
            publication=publication.model_copy(update={"journal_sha256": "0" * 64}),
        )
    with pytest.raises(
        LegacyBaselineMarketReplayError, match="legacy_market_companion_refused"
    ):
        replay_legacy_baseline_market_from_companion_v1(
            raw,
            companion,
            publication_json=retained_pins.read_bytes() + b"\n",
            expected_publication_sha256=publication_sha256,
            decision_at=decision,
        )
    moved = companion.with_name("prior")
    companion.rename(moved)
    companion.mkdir()
    path.write_bytes(recorded)
    with pytest.raises(
        GlobalPublicCompanionError, match="global_companion_root_changed"
    ):
        read_global_public_companion_v1(raw, companion, publication=publication)
    assert (moved / path.name).read_bytes() == recorded
    harness.assert_closed()


@pytest.mark.parametrize(
    ("which", "refusal"),
    [
        ("journal", "global_companion_journal_occupied"),
        ("companion", "global_companion_root_occupied"),
    ],
)
@pytest.mark.asyncio
async def test_occupied_root_refuses_before_any_capture(source_pair, which, refusal):
    raw, companion, journal, harness = source_pair
    existing = (raw if which == "journal" else companion) / "retained.json"
    existing.write_bytes(b"retained")
    with pytest.raises(GlobalPublicCompanionError, match=f"^{refusal}$"):
        await capture(raw, companion)
    assert existing.read_bytes() == b"retained"
    assert not harness.requests and not journal.content


@pytest.mark.asyncio
async def test_equal_and_nested_roots_refuse_before_any_capture(source_pair):
    raw, companion, journal, harness = source_pair
    for roots in ((raw, raw), (raw, raw / "nested"), (companion / "nested", companion)):
        with pytest.raises(
            GlobalPublicCompanionError, match="global_companion_roots_overlap"
        ):
            await capture(*roots)
    assert not harness.requests and not journal.content


@pytest.mark.asyncio
async def test_late_write_failure_retains_original_journal_and_partial_file(
    source_pair, monkeypatch
):
    raw, companion, journal, harness = source_pair
    with native._native_recheck_root(companion) as directory:
        native_directory = type(directory)
    original = native_directory.publish

    def fail_late(self, name, payload):
        assert self.path == companion
        original(self, name, payload[:10])
        raise OSError("synthetic failure after writing partial bytes")

    monkeypatch.setattr(native_directory, "publish", fail_late)
    with pytest.raises(
        GlobalPublicCompanionError, match="global_companion_publication_refused"
    ):
        await capture(raw, companion)
    partial = companion / "post-readback.json"
    retained = partial.read_bytes()
    assert len(retained) == 10
    assert decode(journal.content["summary.json"])["disposition"] == "captured"
    before, requests = dict(journal.content), len(harness.requests)
    with pytest.raises(
        GlobalPublicCompanionError, match="global_companion_root_occupied"
    ):
        await capture(raw, companion)
    assert partial.read_bytes() == retained
    assert journal.content == before
    assert len(harness.requests) == requests
    harness.assert_closed()


@pytest.mark.asyncio
async def test_raw_journal_changes_after_capture_block_companion_publication(
    source_pair, monkeypatch
):
    raw, companion, journal, harness = source_pair
    original = public.capture_initial_global_public_market_v1

    async def changed_after_readback(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert result.code == "initial_global_public_captured"
        journal.content["summary.json"] += b"\n"
        return result

    monkeypatch.setattr(
        public, "capture_initial_global_public_market_v1", changed_after_readback
    )
    with pytest.raises(
        GlobalPublicCompanionError, match="global_companion_publication_refused"
    ):
        await capture(raw, companion)
    assert not list(companion.iterdir())
    assert journal.content["summary.json"].endswith(b"\n")
    harness.assert_closed()
