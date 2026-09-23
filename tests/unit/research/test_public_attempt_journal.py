"""Synthetic transport + memory journal tests, not native TLS/filesystem proof."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.public_market_source import public_market_capture as capture
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_attempt_journal import (
    MAX_CHUNKS,
    replay_attempt_chain,
)
from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    canonical,
    sha,
)
from tests.unit.research.public_receipt_fixtures import storage_fixture_capture
from tests.unit.research.test_public_journal_contracts import (
    MemoryDirectory,
    memory_publisher,
)


def invoke(fixture, journal):
    return asyncio.run(
        capture.collect_and_publish_public_minutes(
            plan=fixture.plan,
            expected_plan_sha256=fixture.plan.canonical_sha256(),
            journal=journal,
        )
    )


def read_attempt(directory, journal):
    checkpoint = journal.checkpoint
    records, _ = replay_attempt_chain(
        MemoryDirectory(directory.content["attempts"]),
        sequence=checkpoint.attempt_sequence,
        expected_head=checkpoint.attempt_head_sha256,
        genesis_sha256=checkpoint.genesis_sha256,
    )
    assert len(records) == 1
    return next(iter(records.values()))


@pytest.mark.parametrize(
    "kind",
    [
        "http",
        "redirect",
        "content_type",
        "encoding",
        "oversized_header",
        "malformed_json",
    ],
)
def test_rejected_status_headers_or_parse_keep_actual_body(monkeypatch, kind):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.requests.clear()
    raw = b'{"server":"synthetic rejected response"}'

    def rejected(request, _):
        status, headers = 200, {"content-type": "application/json"}
        if kind == "http":
            status = 503
        elif kind == "redirect":
            status, headers["location"] = 302, "https://us.okx.com"
        elif kind == "content_type":
            headers["content-type"] = "text/plain"
        elif kind == "encoding":
            headers["content-encoding"] = "gzip"
        elif kind == "oversized_header":
            headers["date"] = "x" * 1100
        return httpx.Response(
            status,
            stream=httpx.ByteStream(raw if kind != "malformed_json" else b"not-json"),
            headers=headers,
        )

    fixture.mutate_response = rejected
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    assert len(fixture.requests) == 1
    summary, requests = read_attempt(directory, journal)
    assert summary["disposition"] == "rejected"
    assert summary["measured_availability_eligible"] is False
    assert summary["intended_receipt_sha256"] is None
    result, stored = requests[0]
    assert stored == (raw if kind != "malformed_json" else b"not-json")
    assert result["body_complete"] is not None
    assert result["result"] == "rejected"
    assert result["truncated"] is False
    assert journal.checkpoint.sequence == 0
    if kind == "oversized_header":
        assert result["headers"]["headers_truncated"] is True
    assert all(
        "location" not in dict(item["headers"]["safe_headers"]) for item, _ in requests
    )


def test_missing_tls_proof_keeps_received_headers_and_body_before_rejection(
    monkeypatch,
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.requests.clear()
    raw = b'{"synthetic":"missing SSL object"}'
    fixture.mutate_response = lambda request, body: httpx.Response(
        200, stream=httpx.ByteStream(raw), headers={"content-type": "application/json"}
    )
    # Exercise the native proof rejection branch with explicit synthetic
    # transport. This does not establish native TLS or a production receipt.
    monkeypatch.setattr(capture, "_client_guard", lambda client, first=False: True)
    with pytest.raises(PublicReceiptError, match="public_tls_peer_missing"):
        invoke(fixture, journal)
    summary, requests = read_attempt(directory, journal)
    result, retained = requests[0]
    assert len(fixture.requests) == 1
    assert result["headers"]["http_status"] == 200
    assert result["headers"]["tls"]["classification"] == "unverified"
    assert result["headers"]["headers_received"] is not None
    assert retained == raw
    assert result["body_complete"] is not None
    assert result["result"] == summary["disposition"] == "rejected"
    assert summary["intended_receipt_sha256"] is None
    assert journal.checkpoint.sequence == 0
    assert all(client.is_closed for client in fixture.clients)


@pytest.mark.parametrize("kind", ["timeout", "cancel"])
def test_partial_stream_records_only_received_bytes_without_body_completion(
    monkeypatch, kind
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.requests.clear()
    raw = b'{"code":"0","data":'

    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield raw
            if kind == "timeout":
                raise httpx.ReadTimeout("not logged")
            raise asyncio.CancelledError

        async def aclose(self):
            pass

    fixture.mutate_response = lambda request, body: httpx.Response(
        200, stream=Partial(), headers={"content-type": "application/json"}
    )
    with pytest.raises((PublicReceiptError, asyncio.CancelledError)):
        invoke(fixture, journal)
    assert len(fixture.requests) == 1
    summary, requests = read_attempt(directory, journal)
    result, stored = requests[0]
    assert summary["disposition"] == result["result"] == "incomplete"
    assert result["body_complete"] is None
    assert stored == raw
    assert result["error_code"] == ("cancelled" if kind == "cancel" else kind)
    assert (
        "body-complete.json"
        not in directory.content["attempts"]["attempt-00000001"]["request-000"]
    )
    assert all(client.is_closed for client in fixture.clients)


def test_oversized_received_chunk_keeps_exact_prefix_and_explicit_truncation(
    monkeypatch,
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.requests.clear()
    fixture.plan = type(fixture.plan).model_validate(
        {**fixture.plan.model_dump(), "max_response_bytes": 1024}
    )
    read = []
    raw = bytes(range(256)) * 8

    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            read.append(1)
            yield raw
            read.append(2)
            yield b"never-read"

        async def aclose(self):
            pass

    fixture.mutate_response = lambda request, body: httpx.Response(
        500, stream=Oversized(), headers={"content-type": "text/plain"}
    )
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    summary, requests = read_attempt(directory, journal)
    result, retained = requests[0]
    assert read == [1]
    assert retained == raw[:1024]
    assert result["observed_bytes"] == 2048
    assert result["stored_bytes"] == 1024
    assert result["truncated"] is True
    assert result["body_complete"] is None
    assert result["chunks"][0]["observed_sha256"] == sha(raw)
    assert summary["disposition"] == "incomplete"


def test_chunk_count_limit_retains_prior_bytes_and_limit_observation(monkeypatch):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    read = []

    class Many(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(MAX_CHUNKS + 2):
                read.append(index)
                yield b"x"

        async def aclose(self):
            pass

    fixture.mutate_response = lambda request, body: httpx.Response(
        200, stream=Many(), headers={"content-type": "application/json"}
    )
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    _, requests = read_attempt(directory, journal)
    result, retained = requests[0]
    assert retained == b"x" * MAX_CHUNKS
    assert result["observed_bytes"] == MAX_CHUNKS + 1
    assert result["truncated"] is True
    assert len(read) == MAX_CHUNKS + 1


def test_late_chunk_metadata_failure_keeps_durable_raw_and_blocks_resume(monkeypatch):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    original = MemoryDirectory.publish

    def fail(directory, name, payload):
        if name == "chunk-000.json":
            raise OSError("synthetic failed metadata write")
        original(directory, name, payload)

    monkeypatch.setattr(MemoryDirectory, "publish", fail)
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    pending = directory.content["attempts"]["attempt-00000001"]
    assert pending["request-000"]["chunk-000.raw"]
    assert "chain.json" not in pending
    assert journal.checkpoint.attempt_sequence == 0
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        storage.resume_public_receipt_journal(
            root=Path.cwd(), trusted_checkpoint=journal.checkpoint
        )


def test_interrupted_unsealed_attempt_cannot_be_recovered_with_current_time(
    monkeypatch,
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    with pytest.raises(SystemExit), journal._begin_attempt(fixture.plan) as attempt:
        attempt.begin_request(
            endpoint="/api/v5/public/time", query=(), stamp=fixture.stamp()
        )
        attempt.chunk(b"partial", fixture.stamp())
        raise SystemExit("simulated abrupt termination")
    pending = directory.content["attempts"]["attempt-00000001"]
    assert pending["request-000"]["chunk-000.raw"] == b"partial"
    assert "summary.json" not in pending
    assert "chain.json" not in pending
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        journal.read_all()


def test_attempt_source_bytes_tamper_fails_even_when_main_head_is_unchanged(
    monkeypatch,
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    with pytest.raises(PublicReceiptError, match="native_public_capture_required"):
        invoke(fixture, journal)
    original_main_head = journal.checkpoint.head_sha256
    node = directory.content["attempts"]["attempt-00000001"]["request-000"]
    node["chunk-000.raw"] += b" "
    assert journal.checkpoint.head_sha256 == original_main_head
    with pytest.raises(PublicReceiptError, match="chunk_identity_mismatch"):
        journal.read_all()


def test_stale_external_attempt_pin_rejects_an_otherwise_complete_capture_chain(
    monkeypatch,
):
    fixture, _, journal, _ = memory_publisher(monkeypatch)
    old = journal.checkpoint
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    with pytest.raises(PublicReceiptError, match="inventory_mismatch"):
        storage.resume_public_receipt_journal(root=Path.cwd(), trusted_checkpoint=old)


def test_failed_response_cannot_be_relabelled_complete_by_resigning_summaries(
    monkeypatch,
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    fixture.mutate_response = lambda request, body: httpx.Response(
        500,
        stream=httpx.ByteStream(b"rejected"),
        headers={"content-type": "text/plain"},
    )
    with pytest.raises(PublicReceiptError):
        invoke(fixture, journal)
    node = directory.content["attempts"]["attempt-00000001"]
    summary = storage.decode(node["summary.json"])
    summary["disposition"], summary["error_code"] = "completed_collection", "none"
    node["summary.json"] = canonical(summary)
    chain = storage.decode(node["chain.json"])
    chain["summary_sha256"] = sha(node["summary.json"])
    node["chain.json"] = canonical(chain)
    counterfeit = journal.checkpoint.model_copy(
        update={"attempt_head_sha256": sha(node["chain.json"])}
    )
    with pytest.raises(PublicReceiptError, match="attempt_incomplete"):
        storage.resume_public_receipt_journal(
            root=Path.cwd(), trusted_checkpoint=counterfeit
        )


@pytest.mark.parametrize("forge_matching_native_fixture", [False, True])
def test_measured_binding_requires_every_attempt_tls_body_query_and_time(
    monkeypatch, forge_matching_native_fixture
):
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    with journal._begin_attempt(fixture.plan) as attempt:
        packet, files = asyncio.run(
            capture._collect_packet(
                fixture.plan, fixture.plan.canonical_sha256(), _attempt=attempt
            )
        )
        # Private forged fixture for binding semantics only. The public entry
        # above rejects this exact MockTransport rather than doing this rewrite.
        packet = storage_fixture_capture(packet, files).receipt
        if forge_matching_native_fixture:
            for index, (result, page) in enumerate(
                zip(
                    attempt.requests,
                    (packet.time_before, *packet.pages, packet.time_after),
                    strict=True,
                )
            ):
                result["headers"]["tls"] = {
                    "classification": "owned_native_tls",
                    "peer_sha256": page["tls_peer_sha256"],
                    "hostname": page["tls_hostname"],
                    "version": page["tls_version"],
                }
                node = directory.content["attempts"]["attempt-00000001"][
                    f"request-{index:03d}"
                ]
                node["headers.json"] = canonical(result["headers"])
                node["result.json"] = canonical(result)
        digest = attempt.seal(
            disposition="completed_collection",
            code="none",
            stamp=fixture.stamp(),
            receipt_sha256=packet.canonical_sha256(),
        )
    bound = type(packet).model_validate(
        {**packet.model_dump(), "attempt_sha256": digest}
    )
    owned = capture._OwnedPublicCapture(capture._ISSUER, bound, files)
    if not forge_matching_native_fixture:
        with pytest.raises(PublicReceiptError, match="source_mismatch"):
            journal._publish_owned(owned)
        assert "capture-00000001" not in directory.content
        assert journal.checkpoint.sequence == 0
        assert journal.checkpoint.attempt_sequence == 1
    else:
        published = journal._publish_owned(owned)
        assert published.checkpoint.sequence == 1
        assert published.checkpoint.attempt_sequence == 1
        restarted = storage.resume_public_receipt_journal(
            root=Path.cwd(), trusted_checkpoint=published.checkpoint
        )
        assert (
            restarted.read_all()[0][0][1].canonical_sha256() == bound.canonical_sha256()
        )
